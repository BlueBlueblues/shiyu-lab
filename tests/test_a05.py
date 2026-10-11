import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from app.db import connect_db
from app.main import create_app
from smoke_a05 import seed_draft, test_settings as make_settings


@pytest.fixture
def setup(tmp_path):
    settings = make_settings(tmp_path / "a05.sqlite3")
    project_id, experiment_id, token = seed_draft(settings)
    return settings, project_id, experiment_id, token


@pytest.fixture
def client(setup, tmp_path):
    app = create_app(setup[0])
    templates = tmp_path / "templates"
    templates.mkdir()
    # 测试专用模板只验证 A 的上下文和自动转义，不替代成员 B 页面。
    for name in ("service.html", "survey.html"):
        (templates / name).write_text('{{ landing.title }} {{ questions[0].title }} {{ is_preview }} '
                                     '{% if not is_preview %}{{ submit_url }}{% endif %}', encoding="utf-8")
    (templates / "experiment_edit.html").write_text('{{ experiment.id }} {{ is_locked }}', encoding="utf-8")
    app.state.templates = Jinja2Templates(directory=str(templates))
    with TestClient(app) as client:
        yield client


def admin(setup):
    settings = setup[0]
    return {"auth": (settings.admin_user, settings.admin_password.get_secret_value()),
            "headers": {"Origin": settings.public_base_url, "X-Requested-With": "FirstOrderLab"}}


def path(setup):
    return f"/api/experiments/{setup[2]}"


def draft(client, setup):
    return client.get(path(setup), **admin(setup)).json()["data"]


def publish(client, setup):
    response = client.post(path(setup) + "/publish", json={}, **admin(setup))
    assert response.status_code == 200
    return response.json()["data"]


def body():
    return {"client_submission_id": str(uuid4()), "consent": True,
            "answers": {"q1": "o2", "q2": "o1", "q3": "测试文本", "q4": ["o2", "o1"]}}


def submit(client, setup, data):
    return client.post(f"/api/public/e/{setup[3]}/responses", json=data)


def count(setup):
    with connect_db(setup[0].database_path) as connection:
        return connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0]


def test_edit_persistent_and_plan_consistent(client, setup):
    original = draft(client, setup)
    assert original["service_url"] is None and original["survey_url"] is None
    edit = {key: original[key] for key in ("landing", "questions")}
    edit["landing"]["title"] = "修改后的标题"
    edit["questions"][0]["title"] = "修改题目"
    edit["questions"][0]["options"][0]["label"] = "修改标签"
    edit["questions"][2]["required"] = True
    response = client.patch(path(setup), json=edit, **admin(setup))
    assert response.status_code == 200
    assert draft(client, setup)["landing"] == edit["landing"]
    with connect_db(setup[0].database_path) as connection:
        row = connection.execute("SELECT * FROM experiments").fetchone()
        plan = json.loads(connection.execute("SELECT plan_json FROM projects").fetchone()[0])
        assert json.loads(row["questions_json"]) == edit["questions"] == plan["questions"]
        assert plan["landing"] == edit["landing"]


@pytest.mark.parametrize("change", ["qid", "type", "oid", "add_q", "delete_q", "add_o", "delete_o", "reorder_q", "reorder_o"])
def test_structure_locked(client, setup, change):
    data = draft(client, setup)
    edit = copy.deepcopy({key: data[key] for key in ("landing", "questions")})
    q = edit["questions"][0]
    if change == "qid": q["id"] = "other"
    elif change == "type": q["type"] = "multiple"
    elif change == "oid": q["options"][0]["id"] = "other"
    elif change == "add_q": edit["questions"].append({**copy.deepcopy(q), "id": "q5"})
    elif change == "delete_q": edit["questions"].pop()
    elif change == "add_o": q["options"].append({"id": "o9", "label": "新增"})
    elif change == "delete_o": q["options"].pop()
    elif change == "reorder_q": edit["questions"].reverse()
    else: q["options"].reverse()
    assert client.patch(path(setup), json=edit, **admin(setup)).status_code == 422
    assert draft(client, setup) == data


def test_running_lock(client, setup):
    data = draft(client, setup)
    with connect_db(setup[0].database_path) as connection:
        connection.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'plan','running',?)",
                           (str(uuid4()), setup[1], "2026-10-10T00:00:00+00:00"))
    for method, suffix, payload in [("patch", "", {k: data[k] for k in ("landing", "questions")}), ("post", "/publish", {})]:
        response = getattr(client, method)(path(setup) + suffix, json=payload, **admin(setup))
        assert response.status_code == 409 and response.json()["error"]["code"] == "RUN_IN_PROGRESS"


def test_publish_idempotent_and_all_locks(client, setup):
    data = publish(client, setup)
    assert publish(client, setup) == data
    assert data["service_url"].startswith(setup[0].public_base_url)
    assert client.get(f"/e/{setup[3]}").status_code == 200
    assert client.get(f"/e/{setup[3]}/survey").status_code == 200
    current = draft(client, setup)
    response = client.patch(path(setup), json={k: current[k] for k in ("landing", "questions")}, **admin(setup))
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_LOCKED"
    response = client.post(f"/api/projects/{setup[1]}/plan", json={}, **admin(setup))
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_LOCKED"


@pytest.mark.parametrize("suffix", ["", "/survey"])
def test_public_unpublished_invalid(client, setup, suffix):
    for token in (setup[3], "invalid"):
        assert client.get(f"/e/{token}{suffix}").status_code == 404
    assert submit(client, setup, body()).status_code == 404


@pytest.mark.parametrize("kind", ["missing", "unknown_q", "unknown_o", "single_list", "multiple_string", "text_list", "empty_required", "duplicate", "consent", "long_text", "number", "uuid"])
def test_invalid_answer_no_insert(client, setup, kind):
    publish(client, setup)
    data = body()
    if kind == "missing": del data["answers"]["q1"]
    elif kind == "unknown_q": data["answers"]["q9"] = "o1"
    elif kind == "unknown_o": data["answers"]["q1"] = "o9"
    elif kind == "single_list": data["answers"]["q1"] = ["o1"]
    elif kind == "multiple_string": data["answers"]["q4"] = "o1"
    elif kind == "text_list": data["answers"]["q3"] = ["o1"]
    elif kind == "empty_required": data["answers"]["q4"] = []
    elif kind == "duplicate": data["answers"]["q4"] = ["o1", "o1"]
    elif kind == "consent": data["consent"] = False
    elif kind == "long_text": data["answers"]["q3"] = "x" * 1001
    elif kind == "number": data["answers"]["q1"] = 1
    else: data["client_submission_id"] = "invalid"
    response = submit(client, setup, data)
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert count(setup) == 0


def test_answers_persist_replay_conflict(client, setup):
    publish(client, setup)
    data = body()
    first = submit(client, setup, data)
    assert first.status_code == 201 and first.json()["data"]["duplicate"] is False
    data["answers"]["q4"].reverse()
    replay = submit(client, setup, data)
    assert replay.status_code == 200 and replay.json()["data"]["duplicate"] is True
    assert replay.json()["data"]["response_id"] == first.json()["data"]["response_id"]
    data["answers"]["q1"] = "o1"
    assert submit(client, setup, data).status_code == 409
    assert count(setup) == 1
    with connect_db(setup[0].database_path) as connection:
        row = connection.execute("SELECT * FROM responses").fetchone()
        assert json.loads(row["answers_json"]) == {"q1": "o2", "q2": "o1", "q3": "测试文本", "q4": ["o1", "o2"]}


def test_optional_empty_normalized(client, setup):
    current = draft(client, setup)
    current["questions"][3]["required"] = False
    assert client.patch(path(setup), json={k: current[k] for k in ("landing", "questions")}, **admin(setup)).status_code == 200
    publish(client, setup)
    data = body()
    data["answers"].update(q3="   ", q4=[])
    assert submit(client, setup, data).status_code == 201
    del data["answers"]["q3"]
    del data["answers"]["q4"]
    assert submit(client, setup, data).status_code == 200


def test_concurrent_same_uuid(client, setup):
    publish(client, setup)
    data = body()
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: submit(client, setup, data), range(8)))
    assert sorted(r.status_code for r in responses) == [200] * 7 + [201]
    assert len({r.json()["data"]["response_id"] for r in responses}) == 1
    assert count(setup) == 1


@pytest.mark.parametrize("suffix", ["edit", "preview/service", "preview/survey"])
def test_admin_pages_and_preview(client, setup, suffix):
    endpoint = f"/experiments/{setup[2]}/{suffix}"
    assert client.get(endpoint).status_code == 401
    response = client.get(endpoint, **admin(setup))
    assert response.status_code == 200
    if suffix.startswith("preview"):
        assert response.context["is_preview"] is True and response.context["submit_url"] is None
        assert setup[3] not in response.text


def test_public_context_escape_no_internal_information(client, setup):
    data = draft(client, setup)
    data["landing"]["title"] = '<script>alert("x")</script>'
    assert client.patch(path(setup), json={k: data[k] for k in ("landing", "questions")}, **admin(setup)).status_code == 200
    publish(client, setup)
    response = client.get(f"/e/{setup[3]}")
    assert '<script>' not in response.text and '&lt;script&gt;' in response.text
    assert response.context["is_preview"] is False
    assert set(response.context) == {"request", "landing", "questions", "is_preview", "submit_url", "service_url", "survey_url"}
    assert setup[1] not in response.text and "/api/projects" not in response.text


def test_management_security(client, setup):
    assert client.get(path(setup)).status_code == 401
    data = draft(client, setup)
    for suffix, method, payload in [("", "patch", {k: data[k] for k in ("landing", "questions")}), ("/publish", "post", {})]:
        assert getattr(client, method)(path(setup) + suffix, json=payload).status_code == 401
        kwargs = admin(setup)
        kwargs["headers"]["Origin"] = "https://evil.example"
        assert getattr(client, method)(path(setup) + suffix, json=payload, **kwargs).status_code == 403


def test_restart_preserves(setup):
    with TestClient(create_app(setup[0])) as client:
        published = publish(client, setup)
        data = body()
        first = submit(client, setup, data).json()["data"]
    with TestClient(create_app(setup[0])) as client:
        assert draft(client, setup)["published_at"] == published["published_at"]
        replay = submit(client, setup, data)
        assert replay.status_code == 200 and replay.json()["data"]["response_id"] == first["response_id"]


def test_missing_B_template_explicit(setup, tmp_path, monkeypatch):
    app = create_app(setup[0])
    empty_templates = tmp_path / "empty_templates"
    empty_templates.mkdir()
    with TestClient(app) as client:
        publish(client, setup)
        assert client.get(f"/e/{setup[3]}").status_code == 200
        # 独立空目录触发真实 TemplateNotFound，不依赖仓库模板是否已交付。
        with monkeypatch.context() as patch:
            patch.setattr(app.state, "templates", Jinja2Templates(directory=str(empty_templates)))
            response = client.get(f"/e/{setup[3]}")
            assert response.status_code == 500
            assert response.json() == {"error": {
                "code": "INTERNAL_ERROR",
                "message": "页面模板尚未由成员 B 交付，页面集成未完成。",
                "fields": {},
            }}
        assert client.get(f"/e/{setup[3]}").status_code == 200


def test_publish_revalidates_corrupted_content(client, setup):
    with connect_db(setup[0].database_path) as connection:
        connection.execute("UPDATE experiments SET questions_json='[]' WHERE id=?", (setup[2],))
    response = client.post(path(setup) + "/publish", json={}, **admin(setup))
    assert response.status_code == 422
    with connect_db(setup[0].database_path) as connection:
        assert connection.execute("SELECT published_at FROM experiments").fetchone()[0] is None


def test_published_submission_allowed_during_run(client, setup):
    publish(client, setup)
    with connect_db(setup[0].database_path) as connection:
        connection.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'review','running',?)",
                           (str(uuid4()), setup[1], "2026-10-10T00:00:00+00:00"))
    assert submit(client, setup, body()).status_code == 201


def test_controlled_urls_ignore_host(client, setup):
    options = admin(setup)
    options["headers"]["Host"] = "evil.example"
    response = client.post(path(setup) + "/publish", json={}, **options)
    assert response.status_code == 200
    assert response.json()["data"]["service_url"].startswith(setup[0].public_base_url)


@pytest.mark.parametrize("endpoint,method,payload", [
    ("/api/experiments/missing", "get", None),
    ("/api/experiments/missing/publish", "post", {}),
    ("/api/public/e/missing/responses", "post", "answers"),
])
def test_missing_resources(client, setup, endpoint, method, payload):
    kwargs = admin(setup) if not endpoint.startswith("/api/public") else {}
    if payload is not None:
        kwargs["json"] = body() if payload == "answers" else payload
    response = getattr(client, method)(endpoint, **kwargs)
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_required_text_and_optional_type_validation(client, setup):
    current = draft(client, setup)
    current["questions"][2]["required"] = True
    assert client.patch(path(setup), json={k: current[k] for k in ("landing", "questions")}, **admin(setup)).status_code == 200
    publish(client, setup)
    data = body()
    data["answers"]["q3"] = "   "
    assert submit(client, setup, data).status_code == 422
    data["answers"]["q3"] = "x" * 1000
    assert submit(client, setup, data).status_code == 201


def test_concurrent_conflicting_contents(client, setup):
    publish(client, setup)
    first = body()
    second = copy.deepcopy(first)
    second["answers"]["q1"] = "o1"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda data: submit(client, setup, data), [first, second]))
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert count(setup) == 1
