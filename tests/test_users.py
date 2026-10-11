import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import SCHEMA, connect_db, init_db
from app.main import create_app
from app.passwords import LEGACY_ADMIN_ID, hash_password, verify_password
from app.users import Register, User, SESSION_COOKIE
from pydantic import ValidationError
from smoke_a05 import test_settings as make_settings
from test_a01 import insert_project, PROJECT_ID
from smoke_a05 import seed_draft
from test_a04 import plan_data
from test_a07 import review_data, model_response


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path / "users.sqlite3", "http://testserver")


def test_password_hash_has_independent_salt():
    password = "仅用于测试的长密码123"
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password("incorrect", first)
    assert not verify_password(password, "malformed")


def test_legacy_database_migration_preserves_records_and_backup(settings):
    with sqlite3.connect(settings.database_path) as old:
        old.executescript(SCHEMA)
        insert_project(old)
        old.execute("INSERT INTO experiments VALUES (?,?,?,?,?,?,?)", (
            "exp", PROJECT_ID, "t" * 32, "{}", "[]", "2026-10-10", "2026-10-10"))
        old.execute("INSERT INTO responses VALUES (1,?,?,?,?,?)", ("exp", "submission", "{}", 1, "2026-10-10"))
        old.execute("INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?)", (
            "run", PROJECT_ID, "review", "succeeded", "2026-10-10", "2026-10-10", None, None))
        old.execute("INSERT INTO tool_calls VALUES (?,?,?,?,?,?,?,?,?)", (
            "tool", "run", "analyze_experiment", "succeeded", "2026-10-10", "2026-10-10", "{}", "{}", None))
        old.execute("INSERT INTO reports VALUES (?,?,?,?,?,?,?,?)", (
            "report", "exp", "run", 1, 1, "{}", "{}", "2026-10-10"))
        tables = ("projects", "experiments", "responses", "agent_runs", "tool_calls", "reports")
        snapshots = {table: old.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    init_db(settings.database_path, settings.admin_user, settings.admin_password.get_secret_value())
    init_db(settings.database_path, settings.admin_user, settings.admin_password.get_secret_value())
    with connect_db(settings.database_path) as db:
        for table in tables:
            rows = [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
            assert ([row[:-1] for row in rows] if table == "projects" else rows) == snapshots[table]
        assert db.execute("SELECT user_id FROM projects").fetchone()[0] == LEGACY_ADMIN_ID
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        user = db.execute("SELECT * FROM users").fetchone()
        assert user["username"] == settings.admin_user
        assert verify_password(settings.admin_password.get_secret_value(), user["password_hash"])
    backups = list(settings.database_path.parent.glob("*.pre-users-*.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT * FROM projects").fetchall() == snapshots["projects"]
        assert "users" not in {row[0] for row in backup.execute("SELECT name FROM sqlite_master")}


def test_migration_failure_rolls_back(settings, monkeypatch):
    with sqlite3.connect(settings.database_path) as db:
        db.executescript(SCHEMA)
        insert_project(db)
    import app.db as database_module
    monkeypatch.setattr(database_module, "hash_password", lambda _: (_ for _ in ()).throw(RuntimeError("test failure")))
    with pytest.raises(RuntimeError):
        init_db(settings.database_path, settings.admin_user, "test-password")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert "user_id" not in {row[1] for row in db.execute("PRAGMA table_info(projects)")}
        assert "users" not in {row[0] for row in db.execute("SELECT name FROM sqlite_master")}


def test_project_owner_foreign_key_and_legacy_seed(settings):
    init_db(settings.database_path)
    with connect_db(settings.database_path) as db:
        insert_project(db)
        assert db.execute("SELECT user_id FROM projects").fetchone()[0] == LEGACY_ADMIN_ID
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE projects SET user_id='missing'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE projects SET user_id=NULL")


def test_user_model_validation():
    body = Register(username=" Alice ", password="test-password-123")
    assert body.username == "Alice" and body.email is None
    assert "test-password-123" not in repr(body)
    for override in ({"username": "bad name"}, {"password": "short"}, {"email": "bad-address"}):
        with pytest.raises(ValidationError):
            Register.model_validate({"username": "Alice", "password": "test-password-123", **override})
    assert "password_hash" not in User.model_fields


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        client.headers.update({"Origin": settings.public_base_url, "X-Requested-With": "FirstOrderLab",
                               "Accept": "application/json"})
        yield client


def register(client, username="Alice"):
    response = client.post("/api/auth/register", json={"username": username, "password": "test-password-123"})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def login(client, username="Alice"):
    response = client.post("/api/auth/login", json={"username": username, "password": "test-password-123"})
    assert response.status_code == 200, response.text
    return response


def test_register_duplicate_and_password_storage(client, settings):
    user = register(client)
    assert set(user) == {"id", "username", "email", "created_at"}
    duplicate = client.post("/api/auth/register", json={"username": "alice", "password": "test-password-123"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"
    with connect_db(settings.database_path) as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        assert row["password_hash"] != "test-password-123"
        assert verify_password("test-password-123", row["password_hash"])
    assert SESSION_COOKIE not in client.cookies


def test_login_bad_password_session_and_logout(client, settings):
    register(client)
    for username, password in (("Alice", "bad-password"), ("Nobody", "test-password-123")):
        result = client.post("/api/auth/login", json={"username": username, "password": password})
        assert result.status_code == 401
        assert result.json()["error"]["message"] == "用户名或密码错误。"
        assert SESSION_COOKIE not in client.cookies
    result = login(client, "alice")
    cookie = result.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=43200" in cookie
    token = client.cookies[SESSION_COOKIE]
    assert token not in result.text
    assert "password_hash" not in result.text
    assert client.get("/api/projects").status_code == 200
    with connect_db(settings.database_path) as db:
        assert token not in str(tuple(db.execute("SELECT * FROM sessions").fetchone()))
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert client.get("/api/projects").status_code == 401
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/projects").status_code == 401


@pytest.mark.parametrize("path", ["register", "login", "logout"])
def test_auth_writes_require_same_origin(client, path):
    payload = {} if path == "logout" else {"username": "Alice", "password": "test-password-123"}
    result = client.post("/api/auth/" + path, json=payload, headers={"Origin": "https://evil.example"})
    assert result.status_code == 403
    client.headers.pop("X-Requested-With")
    assert client.post("/api/auth/" + path, json=payload).status_code == 403


def test_session_rotation_expiration_and_rate_limit(client, settings):
    register(client)
    login(client)
    old = client.cookies[SESSION_COOKIE]
    login(client)
    current = client.cookies[SESSION_COOKIE]
    assert current != old
    with connect_db(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        db.execute("UPDATE sessions SET created_at=1, expires_at=2")
    assert client.get("/api/projects").status_code == 401
    for _ in range(7):
        client.post("/api/auth/login", json={"username": "Alice", "password": "wrong"})
    result = client.post("/api/auth/login", json={"username": "Alice", "password": "wrong"})
    assert result.status_code == 429 and result.headers["retry-after"] == "60"


def test_users_only_see_own_projects_and_cannot_choose_owner(client, settings):
    alice = register(client)
    login(client)
    payload = {"name": "Alice private project", "idea": "测试想法", "target_users": "test",
               "skills": "test", "budget_cny": 0, "validation_goal": "test", "data_kind": "test"}
    created = client.post("/api/projects", json=payload)
    assert created.status_code == 201
    pid = created.json()["data"]["project_id"]
    with connect_db(settings.database_path) as db:
        assert db.execute("SELECT user_id FROM projects WHERE id=?", (pid,)).fetchone()[0] == alice["id"]
    assert [row["id"] for row in client.get("/api/projects").json()["data"]] == [pid]
    assert client.post("/api/projects", json={**payload, "user_id": LEGACY_ADMIN_ID}).status_code == 422
    other = TestClient(client.app)
    try:
        other.headers.update(dict(client.headers))
        register(other, "Bob")
        login(other, "Bob")
        assert other.get("/api/projects").json()["data"] == []
        assert "Alice private project" not in other.get("/projects").text
        assert other.get(f"/api/projects/{pid}").status_code == 404
        assert other.get(f"/projects/{pid}").status_code == 404
        response = other.post("/api/projects", json={**payload, "name": "Bob private project"})
        assert response.status_code == 201
        bob_pid = response.json()["data"]["project_id"]
        assert client.get(f"/api/projects/{bob_pid}").status_code == 404
        assert "Bob private project" not in client.get("/projects").text
        admin_client = TestClient(client.app)
        try:
            assert admin_client.get(f"/api/projects/{pid}", auth=(settings.admin_user,
                settings.admin_password.get_secret_value())).status_code == 404
        finally:
            admin_client.close()
    finally:
        other.close()


def test_all_resource_routes_reject_other_owner_before_model_calls(client, settings, monkeypatch):
    pid, eid, token = seed_draft(settings)
    with connect_db(settings.database_path) as db:
        db.execute("INSERT INTO agent_runs (id,project_id,action,status,started_at,finished_at) VALUES (?,?,?,?,?,?)",
                   ("private-run", pid, "review", "succeeded", "2026-10-10", "2026-10-10"))
    register(client)
    login(client)
    import app.main as main_module
    def forbidden(*args):
        pytest.fail("越权请求不得调用模型工作流")
    monkeypatch.setattr(main_module, "run_plan", forbidden)
    monkeypatch.setattr(main_module, "run_review", forbidden)
    routes = [
        ("GET", f"/api/projects/{pid}"), ("GET", f"/projects/{pid}"),
        ("POST", f"/api/projects/{pid}/plan"), ("POST", f"/api/projects/{pid}/review"),
        ("GET", f"/api/projects/{pid}/report"), ("GET", f"/api/projects/{pid}/report.md"),
        ("GET", f"/projects/{pid}/report"), ("GET", f"/api/projects/{pid}/runs"),
        ("GET", "/api/runs/private-run"), ("GET", f"/api/experiments/{eid}"),
        ("PATCH", f"/api/experiments/{eid}"), ("POST", f"/api/experiments/{eid}/publish"),
        ("GET", f"/api/experiments/{eid}/responses"), ("GET", f"/api/experiments/{eid}/statistics"),
        ("GET", f"/experiments/{eid}/edit"), ("GET", f"/experiments/{eid}/preview/service"),
        ("GET", f"/experiments/{eid}/preview/survey"),
    ]
    for method, path in routes:
        response = client.request(method, path, **({"json": {}} if method != "GET" else {}))
        assert response.status_code == 404, (method, path, response.text)
        assert response.json()["error"]["code"] == "NOT_FOUND"
        assert pid not in response.text
    # 默认管理员也不能看新用户项目；公开问卷仍可匿名访问和提交。
    with connect_db(settings.database_path) as db:
        db.execute("UPDATE experiments SET published_at='2026-10-10' WHERE id=?", (eid,))
    anonymous = TestClient(client.app)
    try:
        assert anonymous.get(f"/e/{token}").status_code == 200
        assert anonymous.get(f"/e/{token}/survey").status_code == 200
        from test_a05 import body
        assert anonymous.post(f"/api/public/e/{token}/responses", json=body()).status_code == 201
    finally:
        anonymous.close()


def test_administrator_session_sees_legacy_data(client, settings):
    pid, _, _ = seed_draft(settings)
    response = client.post("/api/auth/login", json={"username": settings.admin_user,
                          "password": settings.admin_password.get_secret_value()})
    assert response.status_code == 200
    assert [row["id"] for row in client.get("/api/projects").json()["data"]] == [pid]
    assert client.get(f"/projects/{pid}").status_code == 200


def test_secure_cookie_on_https_and_browser_redirect(settings):
    from dataclasses import replace
    secure_settings = replace(settings, public_base_url="https://testserver")
    with TestClient(create_app(secure_settings), base_url="https://testserver", follow_redirects=False) as client:
        page = client.get("/projects", headers={"Accept": "text/html"})
        assert page.status_code == 303 and page.headers["location"] == "/login"
        client.headers.update({"Origin": "https://testserver", "X-Requested-With": "FirstOrderLab"})
        register(client)
        response = login(client)
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/projects").status_code == 200


def test_user_pages_and_personalized_home(client):
    home = client.get("/")
    assert 'href="/login"' in home.text and 'href="/register"' in home.text
    assert "user-nav" not in home.text
    for path in ("/login", "/register"):
        page = client.get(path)
        assert page.status_code == 200
        assert 'type="password"' in page.text and 'method="post"' in page.text
        assert SESSION_COOKIE not in page.text
    assert client.get("/profile").status_code == 401
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 401
    register(client)
    login(client)
    for path in ("/", "/profile", "/projects", "/projects/new"):
        page = client.get(path)
        assert page.status_code == 200
        assert "你好，Alice" in page.text
        assert 'data-testid="auth-logout"' in page.text
        assert page.headers["cache-control"] == "no-store"
        assert SESSION_COOKIE not in page.text
        assert "scrypt$" not in page.text
    assert 'href="/projects"' in client.get("/").text
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["APIKeyCookie"]["in"] == "cookie"


def test_registered_user_full_business_chain_and_report_permissions(client, settings, monkeypatch, plan_data, review_data):
    from unittest.mock import Mock
    import httpx
    from test_a05 import body
    model = Mock(side_effect=[model_response(plan_data), model_response(review_data)])
    monkeypatch.setattr(httpx, "post", model)
    register(client)
    login(client)
    project = {"name": "用户空间 Mock 链路", "idea": "隔离测试", "target_users": "test", "skills": "test",
               "budget_cny": 0, "validation_goal": "test", "data_kind": "test"}
    result = client.post("/api/projects", json=project)
    assert result.status_code == 201
    pid = result.json()["data"]["project_id"]
    plan = client.post(f"/api/projects/{pid}/plan", json={})
    assert plan.status_code == 200, plan.text
    detail = client.get(f"/api/projects/{pid}").json()["data"]
    eid = detail["experiment"]["id"]
    preview = client.get(f"/experiments/{eid}/preview/service")
    assert preview.status_code == 200 and "user-nav" not in preview.text and "你好，Alice" not in preview.text
    assert client.patch(f"/api/experiments/{eid}", json={"landing": plan_data["landing"],
                        "questions": plan_data["questions"]}).status_code == 200
    published = client.post(f"/api/experiments/{eid}/publish", json={})
    assert published.status_code == 200
    survey_url = published.json()["data"]["survey_url"]
    from urllib.parse import urlsplit
    survey_path = urlsplit(survey_url).path
    token = survey_path.split("/")[2]
    anonymous = TestClient(client.app)
    try:
        assert anonymous.get(survey_path).status_code == 200
        assert anonymous.post(f"/api/public/e/{token}/responses", json=body()).status_code == 201
        assert client.get(f"/api/experiments/{eid}/statistics").json()["data"]["response_count"] == 1
        reviewed = client.post(f"/api/projects/{pid}/review", json={})
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["data"]["response_count"] == 1
        assert model.call_count == 2
        assert client.get(f"/api/projects/{pid}/report").json()["data"]["response_count"] == 1
        assert 'data-testid="report-download"' in client.get(f"/projects/{pid}/report").text
        markdown = client.get(f"/api/projects/{pid}/report.md")
        assert markdown.status_code == 200 and markdown.headers["content-type"].startswith("text/markdown")
        assert "attachment" in markdown.headers["content-disposition"]
        for path in (f"/api/projects/{pid}/report", f"/api/projects/{pid}/report.md", f"/projects/{pid}/report"):
            assert anonymous.get(path).status_code == 401
        anonymous.headers.update(dict(client.headers))
        register(anonymous, "Bob")
        login(anonymous, "Bob")
        for path in (f"/api/projects/{pid}/report", f"/api/projects/{pid}/report.md", f"/projects/{pid}/report",
                     f"/api/runs/{reviewed.json()['data']['run_id']}"):
            assert anonymous.get(path).status_code == 404
        assert model.call_count == 2
    finally:
        anonymous.close()
