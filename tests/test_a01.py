import sqlite3
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_admin
from app.config import PROJECT_ROOT, Settings, load_settings
from app.db import connect_db, init_db
from app.main import create_app

NOW = "2026-10-10T00:00:00+00:00"
PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
TABLES = {"projects", "experiments", "responses", "agent_runs", "tool_calls", "reports"}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        app_env="test", database_path=tmp_path / "nested" / "test.sqlite3",
        llm_base_url="https://model.example.invalid/v1", llm_api_key=SecretStr("unit-test-key"),
        llm_model="test-model", admin_user="test-admin",
        admin_password=SecretStr("unit-test-password"), public_base_url="http://127.0.0.1:8000",
    )


@pytest.fixture
def client(settings):
    application = create_app(settings)
    # 测试路由只检验 A01 访问保护，不加入生产应用或实现 A02 项目接口。
    router = APIRouter(dependencies=[Depends(require_admin)])

    @router.api_route("/api/a01-test", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def protected():
        return {"data": {"accepted": True}}

    application.include_router(router)
    with TestClient(application) as test_client:
        yield test_client


def insert_project(connection, **overrides):
    row = {
        "id": PROJECT_ID, "name": "A01 test", "idea": "isolated test",
        "target_users": "test", "skills": "test", "budget_cny": 0,
        "validation_goal": "test", "data_kind": "test", "created_at": NOW,
    }
    row.update(overrides)
    connection.execute(
        """INSERT INTO projects (id, name, idea, target_users, skills, budget_cny,
           validation_goal, data_kind, created_at)
           VALUES (:id, :name, :idea, :target_users, :skills, :budget_cny,
           :validation_goal, :data_kind, :created_at)""", row,
    )


def test_health_and_authenticated_home(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok"}}
    response = client.get("/", auth=("test-admin", "unit-test-password"))
    assert response.status_code == 200
    assert "A01 临时管理首页" in response.text


@pytest.mark.parametrize("authorization", [None, "Bearer token", "Basic !!!", "Basic Zm9v", "Basic d3Jvbmc6d3Jvbmc="])
@pytest.mark.parametrize("path", ["/", "/api/a01-test"])
def test_unauthorized_management(client, authorization, path):
    headers = {} if authorization is None else {"Authorization": authorization}
    response = client.get(path, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert response.json()["error"]["fields"] == {}
    assert "unit-test-password" not in response.text


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_same_origin_writes(client, method):
    response = client.request(
        method, "/api/a01-test", auth=("test-admin", "unit-test-password"),
        headers={"Origin": "http://127.0.0.1:8000", "X-Requested-With": "FirstOrderLab"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "http://127.0.0.1:8000"}, {"X-Requested-With": "FirstOrderLab"},
    {"Origin": "null", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://evil.example", "Host": "evil.example", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "https://127.0.0.1:8000", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://127.0.0.1:9000", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://127.0.0.1:8000/path", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://user@127.0.0.1:8000", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://127.0.0.1:invalid", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://127.0.0.1:8000\t", "X-Requested-With": "FirstOrderLab"},
    {"Origin": "http://127.0.0.1:8000", "X-Requested-With": "wrong"},
])
def test_forbidden_writes(client, headers):
    response = client.post("/api/a01-test", auth=("test-admin", "unit-test-password"), headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_origin_default_port(settings):
    application = create_app(replace(settings, public_base_url="http://127.0.0.1"))

    @application.post("/api/a01-test", dependencies=[Depends(require_admin)])
    def protected():
        return {"data": {}}

    with TestClient(application) as client:
        for origin, status in [("http://127.0.0.1:80", 200), ("http://127.0.0.1:0", 403)]:
            assert client.post(
                "/api/a01-test", auth=("test-admin", "unit-test-password"),
                headers={"Origin": origin, "X-Requested-With": "FirstOrderLab"},
            ).status_code == status


def test_config_isolated_file_and_environment(tmp_path, monkeypatch):
    values = {
        "APP_ENV": "test", "DATABASE_PATH": "data/a01-test.sqlite3",
        "LLM_BASE_URL": "https://model.example.invalid/v1", "LLM_API_KEY": "unit-test-key",
        "LLM_MODEL": "test-model", "ADMIN_USER": "test-admin",
        "ADMIN_PASSWORD": "unit-test-password", "PUBLIC_BASE_URL": "http://127.0.0.1:8000",
    }
    for name in values:
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{name}={value}" for name, value in values.items()), encoding="utf-8")
    result = load_settings(env_file)
    assert result.database_path == PROJECT_ROOT / "data/a01-test.sqlite3"
    assert result.llm_api_key.get_secret_value() == "unit-test-key"
    assert "unit-test-key" not in repr(result)
    assert "unit-test-password" not in repr(result)
    assert "unit-test-key" not in str(result.llm_api_key)
    monkeypatch.setenv("ADMIN_USER", "override-admin")
    assert load_settings(env_file).admin_user == "override-admin"
    for name, value in [("ADMIN_PASSWORD", ""), ("LLM_API_KEY", "REPLACE_ME"),
                        ("LLM_BASE_URL", "https://unit-test-key@model.example.invalid/v1"),
                        ("DATABASE_PATH", ":memory:")]:
        with monkeypatch.context() as temporary:
            temporary.setenv(name, value)
            with pytest.raises(RuntimeError) as error:
                load_settings(env_file)
            assert name in str(error.value)
            assert "unit-test-key" not in str(error.value)
            assert "unit-test-password" not in str(error.value)


def test_restart_preserves_all_tables_and_clears_only_running(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        with connect_db(settings.database_path) as connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            assert tables == TABLES
            insert_project(connection)
            connection.execute(
                "INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("experiment", PROJECT_ID, "a" * 32, "{}", "[]", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at) VALUES (?, ?, ?, ?, ?)",
                ("experiment", str(uuid4()), "{}", 1, NOW),
            )
            for run_id, status in [("old-success", "succeeded"), ("interrupted", "running")]:
                connection.execute(
                    "INSERT INTO agent_runs (id, project_id, action, status, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, PROJECT_ID, "review", status, NOW, NOW if status == "succeeded" else None),
                )
                connection.execute(
                    "INSERT INTO tool_calls (id, run_id, tool_name, status, started_at, finished_at, input_summary_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, run_id, "analyze_experiment", status, NOW, NOW if status == "succeeded" else None, "{}"),
                )
            connection.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("report", "experiment", "old-success", 1, 1, "{}", "{}", NOW),
            )
            original = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                        for table in ("projects", "experiments", "responses", "reports")}
    with TestClient(create_app(settings)):
        with connect_db(settings.database_path) as connection:
            for table, rows in original.items():
                assert [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] == rows
            for table in ("agent_runs", "tool_calls"):
                row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", ("interrupted",)).fetchone()
                assert row["status"] == "failed" and row["error_code"] == "INTERRUPTED"
                assert datetime.fromisoformat(row["finished_at"]).utcoffset().total_seconds() == 0
                old = connection.execute(f"SELECT * FROM {table} WHERE id = ?", ("old-success",)).fetchone()
                assert old["status"] == "succeeded" and old["finished_at"] == NOW
            connection.execute(
                "INSERT INTO agent_runs (id, project_id, action, status, started_at) VALUES (?, ?, ?, ?, ?)",
                ("retry", PROJECT_ID, "plan", "running", NOW),
            )


@pytest.mark.parametrize("overrides", [
    {"id": "not-a-uuid"}, {"id": "z" * 36}, {"budget_cny": -1}, {"budget_cny": 100001},
    {"budget_cny": "bad"}, {"data_kind": "fake"}, {"name": " "}, {"name": "x" * 81},
])
def test_project_checks(settings, overrides):
    init_db(settings.database_path)
    with connect_db(settings.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            insert_project(connection, **overrides)


def test_foreign_keys_unique_constraints_and_running_lock(settings):
    init_db(settings.database_path)
    with connect_db(settings.database_path) as connection:
        insert_project(connection)
        other_id = str(uuid4())
        insert_project(connection, id=other_id)
        connection.execute("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)",
                           ("experiment", PROJECT_ID, "a" * 32, "{}", "[]", None, NOW))
        rejected = [
            ("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)", ("second", PROJECT_ID, "b" * 32, "{}", "[]", None, NOW)),
            ("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)", ("second", other_id, "a" * 32, "{}", "[]", None, NOW)),
            ("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)", ("orphan", str(uuid4()), "c" * 32, "{}", "[]", None, NOW)),
            ("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)", ("short", other_id, "short", "{}", "[]", None, NOW)),
            ("INSERT INTO agent_runs (id, project_id, action, status, started_at) VALUES (?, ?, ?, ?, ?)", ("bad-action", PROJECT_ID, "other", "running", NOW)),
            ("INSERT INTO agent_runs (id, project_id, action, status, started_at) VALUES (?, ?, ?, ?, ?)", ("bad-status", PROJECT_ID, "plan", "other", NOW)),
        ]
        for sql, parameters in rejected:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql, parameters)
        run_sql = "INSERT INTO agent_runs (id, project_id, action, status, started_at) VALUES (?, ?, ?, ?, ?)"
        connection.execute(run_sql, ("running", PROJECT_ID, "plan", "running", NOW))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(run_sql, ("conflict", PROJECT_ID, "review", "running", NOW))
        connection.execute(run_sql, ("other-project", other_id, "plan", "running", NOW))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tool_calls (id, run_id, tool_name, status, started_at, input_summary_json) VALUES (?, ?, ?, ?, ?, ?)",
                ("bad-tool", "running", "unknown_tool", "running", NOW, "{}"),
            )
        response_sql = "INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at) VALUES (?, ?, ?, ?, ?)"
        connection.execute(response_sql, ("experiment", "submission", "{}", 1, NOW))
        for parameters in [("experiment", "submission", "{}", 1, NOW), ("experiment", "other", "{}", 0, NOW),
                           ("missing", "other", "{}", 1, NOW), ("experiment", "invalid-json", "invalid", 1, NOW)]:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(response_sql, parameters)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_connection_rolls_back_and_closes(settings):
    init_db(settings.database_path)
    with pytest.raises(RuntimeError):
        with connect_db(settings.database_path) as connection:
            insert_project(connection)
            raise RuntimeError("test rollback")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    with connect_db(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_report_constraints(settings):
    init_db(settings.database_path)
    with connect_db(settings.database_path) as connection:
        insert_project(connection)
        connection.execute("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)",
                           ("experiment", PROJECT_ID, "a" * 32, "{}", "[]", NOW, NOW))
        connection.execute(
            "INSERT INTO agent_runs (id, project_id, action, status, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("review", PROJECT_ID, "review", "succeeded", NOW, NOW),
        )
        sql = "INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        for parameters in [
            ("invalid", "missing", "review", 0, 0, "{}", "{}", NOW),
            ("invalid", "experiment", "missing", 0, 0, "{}", "{}", NOW),
            ("invalid", "experiment", "review", -1, 0, "{}", "{}", NOW),
            ("invalid", "experiment", "review", 0, 1, "{}", "{}", NOW),
            ("invalid", "experiment", "review", 1, 0, "{}", "{}", NOW),
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(sql, parameters)
        connection.execute(sql, ("report", "experiment", "review", 0, 0, "{}", "{}", NOW))
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(sql, ("duplicate", "experiment", "review", 0, 0, "{}", "{}", NOW))
