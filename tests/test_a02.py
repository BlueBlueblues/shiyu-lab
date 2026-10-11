import json
import sqlite3
from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app import repositories
from app.config import Settings
from app.db import connect_db, get_db
from app.passwords import LEGACY_ADMIN_ID
from app.main import create_app
from app.schemas import Answers, Plan, ProjectCreate, Review, Statistics

AUTH = ("a02-test-admin", "a02-test-password")
HEADERS = {"Origin": "http://127.0.0.1:8000", "X-Requested-With": "FirstOrderLab"}
NOW = "2026-10-10T00:00:00+00:00"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        app_env="test", database_path=tmp_path / "a02.sqlite3",
        llm_base_url="https://model.example.invalid/v1", llm_api_key=SecretStr("a02-test-key"),
        llm_model="test-model", admin_user=AUTH[0], admin_password=SecretStr(AUTH[1]),
        public_base_url="http://127.0.0.1:8000",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers=HEADERS) as test_client:
        test_client.auth = AUTH
        yield test_client


@pytest.fixture
def payload():
    return {
        "name": "A02 接口测试项目", "idea": "测试校园社团服务项目的持久化。",
        "target_users": "测试用社团组织者", "skills": "Python 开发",
        "budget_cny": 100, "validation_goal": "验证项目 API 字段与持久化",
        "data_kind": "test",
    }


@pytest.fixture
def plan_data():
    options = [{"id": "o1", "label": "测试选项一"}, {"id": "o2", "label": "测试选项二"}]
    return {
        "target_customer": "结构测试对象", "pain_points": ["结构测试"],
        "value_proposition": "结构测试", "experiment_steps": ["结构测试"],
        "next_tasks": ["结构测试"],
        "hypotheses": [{"id": f"h{i}", "statement": "结构测试", "question_ids": [f"q{i}"]}
                       for i in range(1, 4)],
        "landing": {
            "title": "结构测试", "summary": "结构测试", "target_users": "结构测试",
            "value_proposition": "结构测试", "features": ["结构测试"],
            "disclaimer": "仅测试模型结构，不代表真实调研或服务上线。",
        },
        "questions": [
            {"id": f"q{i}", "type": kind, "title": "结构测试", "required": i != 3,
             "options": deepcopy(options) if kind != "text" else []}
            for i, kind in enumerate(("single", "single", "text", "multiple"), start=1)
        ],
    }


@pytest.fixture
def review_data():
    return {
        "summary": "仅结构测试，证据不足。",
        "hypothesis_assessments": [
            {"hypothesis_id": f"h{i}", "status": "insufficient_evidence", "reason": "没有真实反馈",
             "evidence_refs": []} for i in range(1, 4)
        ],
        "key_feedback": [], "limitations": ["仅结构测试"], "next_actions": [],
    }


def create_project(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]["project_id"]


def test_create_list_detail_database_and_utc(client, settings, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    created = response.json()["data"]
    assert set(created) == {"project_id", "status"}
    assert UUID(created["project_id"]).version == 4
    assert created["status"] == "draft"
    project_id = created["project_id"]
    listed = client.get("/api/projects").json()["data"]
    assert len(listed) == 1
    assert set(listed[0]) == {"id", "name", "status", "data_kind", "created_at"}
    assert listed[0]["id"] == project_id and listed[0]["status"] == "draft"
    response = client.get(f"/api/projects/{project_id}")
    assert response.status_code == 200
    detail = response.json()["data"]
    assert set(detail) == {"project", "plan", "experiment", "latest_report_meta", "running_run_id"}
    assert all(detail[name] is None for name in ("plan", "experiment", "latest_report_meta", "running_run_id"))
    assert "plan_json" not in detail["project"]
    with connect_db(settings.database_path) as db:
        row = dict(db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        assert row.pop("plan_json") is None
        assert row.pop("user_id") == LEGACY_ADMIN_ID
        assert detail["project"] == {**row, "status": "draft"}
        for name, value in payload.items():
            assert row[name] == value
        assert datetime.fromisoformat(row["created_at"]).utcoffset().total_seconds() == 0
        assert "status" not in {column[1] for column in db.execute("PRAGMA table_info(projects)")}


def test_empty_list(client):
    assert client.get("/api/projects").json() == {"data": []}


@pytest.mark.parametrize("field,value", [
    ("budget_cny", -1), ("budget_cny", 100001), ("budget_cny", True), ("budget_cny", "100"),
    ("budget_cny", None), ("name", " \t\r\n\u3000"), ("name", "x" * 81),
    ("idea", "x" * 2001), ("idea", " "), ("target_users", "x" * 501),
    ("target_users", "\t"), ("skills", "x" * 501), ("skills", " "),
    ("validation_goal", "x" * 501), ("validation_goal", " "),
    ("data_kind", "fake"), ("data_kind", "TEST"),
    ("status", "reviewed"), ("plan_json", "{}"), ("ADMIN_PASSWORD", "a02-test-password"),
])
def test_invalid_project_uses_safe_422(client, settings, payload, field, value):
    payload[field] = value
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 422
    error = response.json()["error"]
    assert set(error) == {"code", "message", "fields"}
    assert error["code"] == "VALIDATION_ERROR" and field in error["fields"]
    assert "detail" not in response.json()
    assert AUTH[1] not in response.text and "a02-test-key" not in response.text
    with connect_db(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


@pytest.mark.parametrize("budget", [0, 100000, 12.5])
def test_limits_and_trim(client, payload, budget):
    payload.update(name=" " + "n" * 80 + "\t", idea=" " + "i" * 2000 + " ",
                   target_users="t" * 500, skills="s" * 500, validation_goal="v" * 500,
                   budget_cny=budget)
    project_id = create_project(client, payload)
    project = client.get(f"/api/projects/{project_id}").json()["data"]["project"]
    assert project["name"] == "n" * 80 and project["idea"] == "i" * 2000
    assert project["budget_cny"] == budget


def test_kind_cannot_be_updated(client, payload):
    assert ProjectCreate.model_validate({**payload, "data_kind": "real"}).data_kind == "real"
    project_id = create_project(client, payload)
    assert client.patch(f"/api/projects/{project_id}", json={"data_kind": "real"}).status_code == 405
    assert client.get(f"/api/projects/{project_id}").json()["data"]["project"]["data_kind"] == "test"


@pytest.mark.parametrize("body", ["{", "null", "[]", '{"budget_cny": NaN}', '{}'])
def test_malformed_or_incomplete_json(client, body):
    response = client.post("/api/projects", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["fields"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_budget_returns_json_error(client, payload, value):
    payload["budget_cny"] = value
    response = client.post("/api/projects", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "budget_cny" in response.json()["error"]["fields"]


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/projects"), ("POST", "/api/projects"),
    ("GET", "/api/projects/550e8400-e29b-41d4-a716-446655440000"),
])
def test_management_requires_auth(client, payload, method, path):
    response = client.request(method, path, auth=None, json=payload if method == "POST" else None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert response.headers["www-authenticate"].startswith("Basic")


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"}, {"Origin": "http://evil.example", "Host": "evil.example"},
    {"X-Requested-With": "wrong"}, {"Origin": ""}, {"X-Requested-With": ""},
])
def test_management_write_requires_origin_and_marker(client, settings, payload, headers):
    response = client.post("/api/projects", json=payload, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    with connect_db(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


@pytest.mark.parametrize("project_id", ["550e8400-e29b-41d4-a716-446655440000", "not-a-uuid", "' OR 1=1 --"])
def test_missing_project(client, project_id):
    response = client.get(f"/api/projects/{project_id}")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "项目不存在。", "fields": {}}}


def test_reconnect_and_restart_preserve_project(settings, payload):
    with TestClient(create_app(settings), headers=HEADERS) as client:
        client.auth = AUTH
        project_id = create_project(client, payload)
        original = client.get(f"/api/projects/{project_id}").json()
    with connect_db(settings.database_path) as db:
        assert repositories.get_project(db, project_id, settings.public_base_url) == original["data"]
    with TestClient(create_app(settings)) as restarted:
        restarted.auth = AUTH
        assert restarted.get(f"/api/projects/{project_id}").json() == original
        assert restarted.get("/api/projects").json()["data"][0]["id"] == project_id


@pytest.mark.parametrize("status", ["draft", "planned", "collecting", "reviewed"])
def test_derived_status_and_running_id(client, settings, payload, plan_data, status):
    project_id = create_project(client, payload)
    experiment_id = str(uuid4())
    report_id = str(uuid4())
    with connect_db(settings.database_path) as db:
        if status != "draft":
            db.execute("UPDATE projects SET plan_json = ? WHERE id = ?", (json.dumps(plan_data), project_id))
            db.execute("INSERT INTO experiments VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (experiment_id, project_id, "test-token-" + "a" * 32,
                        json.dumps(plan_data["landing"]), json.dumps(plan_data["questions"]),
                        None if status == "planned" else NOW, NOW))
        if status == "reviewed":
            db.execute("INSERT INTO agent_runs (id, project_id, action, status, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (report_id, project_id, "review", "succeeded", NOW, NOW))
            db.execute("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (report_id, experiment_id, report_id, 0, 0, "{}", "{}", NOW))
        db.execute("INSERT INTO agent_runs (id, project_id, action, status, started_at) VALUES (?, ?, ?, ?, ?)",
                   ("active-test-run", project_id, "plan" if status in {"draft", "planned"} else "review", "running", NOW))
    detail = client.get(f"/api/projects/{project_id}", headers={"Host": "evil.example"}).json()["data"]
    assert detail["project"]["status"] == status
    assert detail["running_run_id"] == "active-test-run"
    assert client.get("/api/projects").json()["data"][0]["status"] == status
    if status != "draft":
        assert detail["plan"] == plan_data
        experiment = detail["experiment"]
        assert set(experiment) == {"id", "project_id", "landing", "questions", "published_at", "service_url", "survey_url"}
        assert experiment["landing"] == plan_data["landing"] and experiment["questions"] == plan_data["questions"]
        if status == "planned":
            assert experiment["service_url"] is None and experiment["survey_url"] is None
        else:
            assert experiment["service_url"].startswith(settings.public_base_url + "/e/")
            assert experiment["survey_url"] == experiment["service_url"] + "/survey"
    if status == "reviewed":
        assert detail["latest_report_meta"] == {
            "id": report_id, "response_count": 0, "cutoff_response_id": 0, "created_at": NOW, "stale": False,
        }
        with connect_db(settings.database_path) as db:
            db.execute("INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at) VALUES (?, ?, ?, ?, ?)",
                       (experiment_id, str(uuid4()), "{}", 1, NOW))
        stale = client.get(f"/api/projects/{project_id}").json()["data"]
        assert stale["project"]["status"] == "collecting"
        assert stale["latest_report_meta"]["stale"] is True
        assert stale["latest_report_meta"]["response_count"] == 0
        assert client.get("/api/projects").json()["data"][0]["status"] == "collecting"
        with connect_db(settings.database_path) as db:
            current_cutoff = db.execute("SELECT MAX(id) FROM responses WHERE experiment_id = ?", (experiment_id,)).fetchone()[0]
            db.execute("UPDATE reports SET response_count = ?, cutoff_response_id = ? WHERE id = ?",
                       (1, current_cutoff + 1, report_id))
        assert client.get(f"/api/projects/{project_id}").json()["data"]["latest_report_meta"]["stale"] is True
        with connect_db(settings.database_path) as db:
            db.execute("UPDATE reports SET cutoff_response_id = ? WHERE id = ?", (current_cutoff, report_id))
        assert client.get(f"/api/projects/{project_id}").json()["data"]["project"]["status"] == "reviewed"
        later_report = str(uuid4())
        with connect_db(settings.database_path) as db:
            db.execute("INSERT INTO agent_runs (id, project_id, action, status, started_at, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (later_report, project_id, "review", "succeeded", NOW, NOW))
            db.execute("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (later_report, experiment_id, later_report, 0, 0, "{}", "{}", "2026-10-10T00:01:00+00:00"))
        latest = client.get(f"/api/projects/{project_id}").json()["data"]
        assert latest["latest_report_meta"]["id"] == later_report
        assert latest["project"]["status"] == "collecting"


def test_database_and_unexpected_errors_are_redacted(client, payload, monkeypatch):
    for exception in (sqlite3.OperationalError("private database details a02-test-key"),
                      RuntimeError("private traceback a02-test-password")):
        def fail(*args, **kwargs):
            raise exception

        with monkeypatch.context() as temporary:
            temporary.setattr(repositories, "list_projects", fail)
            with TestClient(client.app, raise_server_exceptions=False) as safe_client:
                safe_client.auth = AUTH
                response = safe_client.get("/api/projects")
                assert response.status_code == 500
                assert response.json()["error"]["code"] == "INTERNAL_ERROR"
                assert response.json()["error"]["fields"] == {}
                for secret in ("a02-test-key", AUTH[1], "private", "Traceback", "sqlite"):
                    assert secret not in response.text


def test_commit_failure_is_not_reported_as_success(client, settings, payload):
    def failed_commit():
        with connect_db(settings.database_path) as db:
            yield db
            raise sqlite3.OperationalError("simulated commit failure")

    client.app.dependency_overrides[get_db] = failed_commit
    try:
        response = client.post("/api/projects", json=payload)
        assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
    finally:
        client.app.dependency_overrides.clear()
    with connect_db(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_openapi_has_contracts_and_session_basic_security(client):
    schema = client.get("/openapi.json").json()
    for path, method in [("/api/projects", "post"), ("/api/projects", "get"), ("/api/projects/{project_id}", "get")]:
        operation = schema["paths"][path][method]
        assert operation["security"] == [{"HTTPBasic": []}, {"APIKeyCookie": []}]
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse",
        }
    properties = schema["components"]["schemas"]["ProjectCreate"]["properties"]
    assert properties["name"]["maxLength"] == 80
    assert properties["idea"]["maxLength"] == 2000
    assert properties["budget_cny"]["minimum"] == 0 and properties["budget_cny"]["maximum"] == 100000


@pytest.fixture
def statistics_data(plan_data):
    return {
        "experiment_id": str(uuid4()), "response_count": 0, "cutoff_response_id": 0,
        "questions": [
            {"id": question["id"], "type": question["type"], "answered_count": 0,
             **({"texts": []} if question["type"] == "text" else
                {"options": [{**option, "count": 0, "percentage": None} for option in question["options"]]})}
            for question in plan_data["questions"]
        ],
    }


def test_schema_valid_structures_and_round_trip(plan_data, review_data, statistics_data):
    assert Plan.model_validate(plan_data).model_dump() == plan_data
    assert Review.model_validate(review_data).model_dump() == review_data
    answers = {"client_submission_id": str(uuid4()), "consent": True,
               "answers": {"q1": "o1", "q3": "", "q4": ["o2", "o1"]}}
    assert Answers.model_validate(answers).model_dump(mode="json") == answers
    assert Statistics.model_validate(statistics_data).model_dump() == statistics_data


@pytest.mark.parametrize("path,value", [
    (("cutoff_response_id",), 1), (("questions", 1, "id"), "q1"),
    (("questions", 0, "options", 0, "percentage"), 0),
    (("questions", 0, "options", 0, "count"), 1),
    (("questions", 2, "answered_count"), 1),
])
def test_statistics_schema_invariants(statistics_data, path, value):
    target = statistics_data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        Statistics.model_validate(statistics_data)


def test_statistics_allows_multiple_percentages_over_100(statistics_data):
    statistics_data.update(response_count=2, cutoff_response_id=7)
    question = statistics_data["questions"][3]
    question["answered_count"] = 2
    for option in question["options"]:
        option.update(count=2, percentage=100)
    assert Statistics.model_validate(statistics_data).questions[3].answered_count == 2
    question["options"][0]["percentage"] = None
    with pytest.raises(ValidationError):
        Statistics.model_validate(statistics_data)


@pytest.mark.parametrize("path,value", [
    (("questions",), []),
    (("questions",), [{"id": "q", "type": "text", "title": "结构测试", "required": False, "options": []}] * 7),
    (("questions", 1, "id"), "q1"), (("hypotheses", 1, "id"), "h1"),
    (("hypotheses", 0, "question_ids"), ["missing"]),
    (("hypotheses", 0, "question_ids"), ["q1", "q1"]),
    (("experiment_steps",), ["结构测试"] * 11), (("landing", "summary"), "x" * 1001),
    (("landing", "features"), ["结构测试"] * 7), (("questions", 0, "options", 1, "id"), "o1"),
    (("questions", 0, "type"), "text"), (("questions", 0, "options"), []),
    (("questions", 0, "title"), "x" * 201), (("questions", 0, "options", 0, "label"), "x" * 81),
    (("questions", 0, "type"), "other"), (("questions", 0, "required"), "true"),
])
def test_plan_schema_rejects_invalid_structure(plan_data, path, value):
    target = plan_data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        Plan.model_validate(plan_data)


@pytest.mark.parametrize("overrides", [
    {"client_submission_id": "not-a-uuid"}, {"consent": False}, {"consent": 1},
    {"answers": {"q3": "x" * 1001}}, {"answers": {"q4": ["o1", "o1"]}},
    {"answers": {"q4": ["o1"] * 9}}, {"answers": {"q1": 1}},
])
def test_answers_schema(overrides):
    value = {"client_submission_id": str(uuid4()), "consent": True, "answers": {}}
    value.update(overrides)
    with pytest.raises(ValidationError):
        Answers.model_validate(value)


@pytest.mark.parametrize("reference", [
    {"question_id": "q1"}, {"question_id": "q1", "option_id": "o1", "response_id": 1},
    {"question_id": "q3", "response_id": 0},
])
def test_review_references_are_exclusive(review_data, reference):
    review_data["hypothesis_assessments"][0]["evidence_refs"] = [reference]
    with pytest.raises(ValidationError):
        Review.model_validate(review_data)


def test_review_limits_and_valid_refs(review_data):
    review_data["hypothesis_assessments"][0]["evidence_refs"] = [
        {"question_id": "q1", "option_id": "o1"}, {"question_id": "q3", "response_id": 1},
    ]
    assert Review.model_validate(review_data).model_dump() == review_data
    review_data["next_actions"] = [{"action": "结构测试", "owner_role": "A", "completion_criterion": "结构测试"}] * 4
    with pytest.raises(ValidationError):
        Review.model_validate(review_data)
    review_data["next_actions"] = []
    review_data["hypothesis_assessments"][1]["hypothesis_id"] = "h1"
    with pytest.raises(ValidationError):
        Review.model_validate(review_data)
