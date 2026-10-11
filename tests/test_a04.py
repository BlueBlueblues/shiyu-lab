import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import repositories
from app.config import PROJECT_ROOT, Settings
from app.db import connect_db, init_db
from app.main import create_app
from app.schemas import Plan, ProjectCreate
from app.services import workflow
import smoke_a04

AUTH = ("a04-test-admin", "a04-test-password")
HEADERS = {"Origin": "http://127.0.0.1:8000", "X-Requested-With": "FirstOrderLab"}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        app_env="test", database_path=tmp_path / "a04.sqlite3",
        llm_base_url="https://model.example.invalid/v1", llm_api_key=SecretStr("a04-test-key"),
        llm_model="a04-mock-model", admin_user=AUTH[0], admin_password=SecretStr(AUTH[1]),
        public_base_url=HEADERS["Origin"],
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers=HEADERS) as client:
        client.auth = AUTH
        yield client


@pytest.fixture
def payload():
    return {"name": "A04 隔离流程测试", "idea": "仅用于测试工具事务的服务想法",
            "target_users": "测试对象", "skills": "Python", "budget_cny": 100,
            "validation_goal": "测试计划草稿流程", "data_kind": "test"}


@pytest.fixture
def project_id(client, payload):
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    return response.json()["data"]["project_id"]


@pytest.fixture
def plan_data():
    # 契约示例仅是 Mock 模型响应；成功日志由被测工作流真实写入临时数据库。
    contract = (PROJECT_ROOT / "docs/tasks/01_项目总任务大纲.md").read_text(encoding="utf-8")
    section = contract.split("### 7.2 ", 1)[1]
    return json.loads(re.search(r"```json\s*(.*?)\s*```", section, re.S).group(1))


def model_response(plan_data):
    return httpx.Response(200, request=httpx.Request("POST", "https://model.example.invalid/v1/chat/completions"),
                          json={"choices": [{"message": {"content": json.dumps(plan_data)}, "finish_reason": "stop"}],
                                "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}})


@pytest.fixture
def model(monkeypatch, plan_data):
    model = Mock(return_value=model_response(plan_data))
    monkeypatch.setattr(httpx, "post", model)
    return model


def generate(client, project_id):
    return client.post(f"/api/projects/{project_id}/plan", json={})


def snapshot(settings, project_id):
    with connect_db(settings.database_path) as connection:
        project = dict(connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())
        row = connection.execute("SELECT * FROM experiments WHERE project_id = ?", (project_id,)).fetchone()
        return project, dict(row) if row else None


def runs(client, project_id):
    response = client.get(f"/api/projects/{project_id}/runs")
    assert response.status_code == 200
    return response.json()["data"]


def detail(client, run_id):
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    return response.json()["data"]


def test_success_creates_consistent_plan_draft_and_real_logs(client, settings, project_id, payload, plan_data, model):
    response = generate(client, project_id)
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert set(result) == {"project_id", "experiment_id", "run_id", "status"}
    assert result["status"] == "planned" and result["project_id"] == project_id
    UUID(result["experiment_id"])
    UUID(result["run_id"])
    project, experiment = snapshot(settings, project_id)
    assert json.loads(project["plan_json"]) == Plan.model_validate(plan_data).model_dump()
    assert json.loads(experiment["landing_json"]) == plan_data["landing"]
    assert json.loads(experiment["questions_json"]) == plan_data["questions"]
    assert experiment["id"] == result["experiment_id"] and experiment["published_at"] is None
    assert len(experiment["public_token"]) >= 43
    run = detail(client, result["run_id"])
    assert runs(client, project_id)[0]["id"] == run["id"]
    assert run["status"] == "succeeded" and run["error_code"] is None
    assert [tool["tool_name"] for tool in run["tool_calls"]] == ["make_validation_plan", "create_experiment"]
    for record in [run, *run["tool_calls"]]:
        assert record["status"] == "succeeded" and record["finished_at"] >= record["started_at"]
        assert datetime.fromisoformat(record["started_at"]).utcoffset().total_seconds() == 0
    first, second = run["tool_calls"]
    assert first["finished_at"] <= second["started_at"]
    assert first["output_ref"]["model_call"]["token_usage"]["total_tokens"] == 300
    assert first["input_summary"]["data_kind"] == "test"
    assert first["input_summary"]["model"] == settings.llm_model
    assert second["output_ref"] == {"experiment_id": result["experiment_id"]}
    for sensitive in ("a04-test-key", AUTH[1], payload["idea"], experiment["public_token"]):
        assert sensitive not in json.dumps(run)
    model.assert_called_once()
    messages = model.call_args.kwargs["json"]["messages"]
    assert json.loads(messages[1]["content"]) == payload
    assert model.call_args.kwargs["timeout"] == 60.0
    project_detail = client.get(f"/api/projects/{project_id}").json()["data"]
    assert project_detail["project"]["status"] == "planned" and project_detail["running_run_id"] is None
    assert project_detail["experiment"]["service_url"] is None


def test_regenerate_preserves_ids_token_creation_time_and_replaces_content(client, settings, project_id, plan_data, model):
    first = generate(client, project_id).json()["data"]
    _, before = snapshot(settings, project_id)
    plan_data["landing"]["title"] = "第二份 Mock 草稿"
    model.return_value = model_response(plan_data)
    second = generate(client, project_id).json()["data"]
    _, after = snapshot(settings, project_id)
    assert first["experiment_id"] == second["experiment_id"] and first["run_id"] != second["run_id"]
    assert before["public_token"] == after["public_token"] and before["created_at"] == after["created_at"]
    assert json.loads(after["landing_json"])["title"] == "第二份 Mock 草稿"
    assert [run["id"] for run in runs(client, project_id)] == [second["run_id"], first["run_id"]]
    assert model.call_count == 2


@pytest.mark.parametrize("failure,code,status", [
    (httpx.ReadTimeout("sensitive a04-test-key"), "LLM_TIMEOUT", 504),
    (httpx.ConnectError("sensitive a04-test-key"), "LLM_UPSTREAM_ERROR", 502),
    (httpx.Response(503, request=httpx.Request("POST", "https://model.example.invalid"),
                    json={"error": "sensitive a04-test-key"}), "LLM_UPSTREAM_ERROR", 502),
    (model_response({}), "LLM_OUTPUT_INVALID", 502),
])
@pytest.mark.parametrize("has_draft", [False, True])
def test_model_failure_preserves_draft_and_failed_logs(client, settings, project_id, model, failure, code, status, has_draft):
    if has_draft:
        assert generate(client, project_id).status_code == 200
    before = snapshot(settings, project_id)
    model.reset_mock()
    if isinstance(failure, Exception):
        model.side_effect = failure
    else:
        model.return_value = failure
    response = generate(client, project_id)
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code and error["fields"] == {} and error["run_id"]
    assert snapshot(settings, project_id) == before
    run = detail(client, error["run_id"])
    assert run["status"] == "failed" and run["error_code"] == code and run["finished_at"]
    assert len(run["tool_calls"]) == 1
    assert run["tool_calls"][0]["status"] == "failed" and run["tool_calls"][0]["error_code"] == code
    assert "sensitive" not in json.dumps(run) + response.text and "a04-test-key" not in json.dumps(run)
    assert client.get(f"/api/projects/{project_id}").json()["data"]["running_run_id"] is None
    model.assert_called_once()


@pytest.mark.parametrize("has_draft", [False, True])
@pytest.mark.parametrize("fault", ["experiment", "run_success"])
def test_database_failure_rolls_back_plan_experiment_and_success_logs(client, settings, project_id, model, has_draft, fault):
    if has_draft:
        assert generate(client, project_id).status_code == 200
    before = snapshot(settings, project_id)
    with connect_db(settings.database_path) as connection:
        if fault == "experiment":
            verb = "UPDATE" if has_draft else "INSERT"
            connection.execute(f"""CREATE TRIGGER simulate_save_failure BEFORE {verb} ON experiments
                                BEGIN SELECT RAISE(ABORT, 'private database details'); END""")
        else:
            connection.execute("""CREATE TRIGGER simulate_finish_failure BEFORE UPDATE ON agent_runs
                                WHEN NEW.status = 'succeeded'
                                BEGIN SELECT RAISE(ABORT, 'private database details'); END""")
    response = generate(client, project_id)
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private" not in response.text
    assert snapshot(settings, project_id) == before
    run = detail(client, response.json()["error"]["run_id"])
    assert run["status"] == "failed"
    assert [tool["status"] for tool in run["tool_calls"]] == ["succeeded", "failed"]
    assert run["tool_calls"][1]["output_ref"] is None


def test_published_rejects_without_model_request(client, settings, project_id, model):
    assert generate(client, project_id).status_code == 200
    with connect_db(settings.database_path) as connection:
        connection.execute("UPDATE experiments SET published_at = ? WHERE project_id = ?", (workflow.utc_now(), project_id))
    before = snapshot(settings, project_id)
    response = generate(client, project_id)
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_LOCKED"
    assert len(runs(client, project_id)) == 1 and model.call_count == 1
    assert snapshot(settings, project_id) == before


def test_publication_is_rechecked_inside_save_transaction(client, settings, project_id, model, plan_data):
    assert generate(client, project_id).status_code == 200
    before = snapshot(settings, project_id)

    def published_during_request(*args, **kwargs):
        with connect_db(settings.database_path) as connection:
            connection.execute("UPDATE experiments SET published_at = ? WHERE project_id = ?", (workflow.utc_now(), project_id))
        return model_response(plan_data)

    model.side_effect = published_during_request
    response = generate(client, project_id)
    assert response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_LOCKED"
    project, experiment = snapshot(settings, project_id)
    assert project == before[0] and experiment["landing_json"] == before[1]["landing_json"]
    run = detail(client, response.json()["error"]["run_id"])
    assert [tool["status"] for tool in run["tool_calls"]] == ["succeeded", "failed"]


def test_concurrent_request_logs_readable_and_other_writer_unblocked(client, settings, project_id, payload, model, plan_data):
    entered, release = Event(), Event()

    def waiting_response(*args, **kwargs):
        entered.set()
        assert release.wait(4)
        return model_response(plan_data)

    model.side_effect = waiting_response
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(generate, client, project_id)
        try:
            assert entered.wait(2)
            active = runs(client, project_id)[0]
            assert active["status"] == "running"
            run = detail(client, active["id"])
            assert run["tool_calls"][0]["status"] == "running"
            assert client.get(f"/api/projects/{project_id}").json()["data"]["running_run_id"] == active["id"]
            with connect_db(settings.database_path) as connection:
                connection.execute("PRAGMA busy_timeout = 100")
                connection.execute("BEGIN IMMEDIATE")
                other_id = repositories.create_project(connection, ProjectCreate(**payload))
            assert client.get(f"/api/projects/{other_id}").status_code == 200
            second = generate(client, project_id)
            assert second.status_code == 409 and second.json()["error"]["code"] == "RUN_IN_PROGRESS"
            assert len(runs(client, project_id)) == 1
            # 数据库部分唯一索引独立兜底，不仅依靠预检查。
            with connect_db(settings.database_path) as connection, pytest.raises(sqlite3.IntegrityError):
                connection.execute("""INSERT INTO agent_runs (id, project_id, action, status, started_at)
                                   VALUES (?, ?, 'plan', 'running', ?)""", (str(uuid4()), project_id, workflow.utc_now()))
        finally:
            release.set()
        assert first.result(timeout=3).status_code == 200
    model.assert_called_once()


@pytest.mark.parametrize("limit", ["MODEL_SECONDS", "WORKFLOW_SECONDS"])
def test_wait_timeout_discards_late_model_result(client, settings, project_id, model, plan_data, monkeypatch, limit):
    release, finished = Event(), Event()
    monkeypatch.setattr(workflow, limit, 0.2)

    def waiting_response(*args, **kwargs):
        assert release.wait(3)
        finished.set()
        return model_response(plan_data)

    model.side_effect = waiting_response
    try:
        response = generate(client, project_id)
        assert response.status_code == 504 and response.json()["error"]["code"] == "LLM_TIMEOUT"
        assert detail(client, response.json()["error"]["run_id"])["status"] == "failed"
        assert snapshot(settings, project_id)[1] is None
    finally:
        release.set()
    assert finished.wait(1)
    assert snapshot(settings, project_id)[1] is None
    model.assert_called_once()


def test_budget_exhausted_before_commit_rolls_back(client, settings, project_id, model, monkeypatch):
    monkeypatch.setattr(workflow, "WORKFLOW_SECONDS", 0.4)
    original = repositories.save_experiment

    def slow_save(*args, **kwargs):
        result = original(*args, **kwargs)
        time.sleep(0.22)
        return result

    monkeypatch.setattr(repositories, "save_experiment", slow_save)
    response = generate(client, project_id)
    assert response.status_code == 504
    project, experiment = snapshot(settings, project_id)
    assert project["plan_json"] is None and experiment is None
    assert detail(client, response.json()["error"]["run_id"])["tool_calls"][1]["status"] == "failed"


@pytest.mark.parametrize("method,path", [("POST", "/api/projects/{id}/plan"),
                                        ("GET", "/api/projects/{id}/runs"), ("GET", "/api/runs/{id}")])
@pytest.mark.parametrize("auth", [None, ("wrong", "wrong")])
def test_unauthorized_new_endpoints(client, project_id, model, method, path, auth):
    response = client.request(method, path.format(id=project_id), auth=auth,
                              **({"json": {}} if method == "POST" else {}))
    assert response.status_code == 401 and response.json()["error"]["code"] == "AUTH_REQUIRED"
    assert "Basic" in response.headers["www-authenticate"]
    model.assert_not_called()


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": ""},
                                     {"X-Requested-With": ""}, {"X-Requested-With": "wrong"}])
def test_invalid_write_headers_rejected(client, project_id, model, headers):
    response = client.post(f"/api/projects/{project_id}/plan", json={}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert runs(client, project_id) == []
    model.assert_not_called()


@pytest.mark.parametrize("body", [{"unexpected": True}, [], "text", None])
def test_nonempty_or_wrong_body_rejected(client, project_id, model, body):
    response = client.post(f"/api/projects/{project_id}/plan", content=json.dumps(body),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert runs(client, project_id) == []
    model.assert_not_called()


@pytest.mark.parametrize("method,path", [("POST", "/api/projects/{id}/plan"),
                                        ("GET", "/api/projects/{id}/runs"), ("GET", "/api/runs/{id}")])
def test_missing_resource_404(client, model, method, path):
    response = client.request(method, path.format(id=uuid4()), **({"json": {}} if method == "POST" else {}))
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
    model.assert_not_called()


def test_restart_preserves_success_and_cleans_interrupted_logs(client, settings, project_id, model):
    result = generate(client, project_id).json()["data"]
    before = snapshot(settings, project_id)
    interrupted = str(uuid4())
    # 单元测试模拟进程在持久开始记录后中断，不构造成功工具结果。
    with connect_db(settings.database_path) as connection:
        repositories.start_plan_run(connection, project_id, interrupted, workflow.utc_now())
        repositories.start_tool(connection, interrupted, str(uuid4()), "make_validation_plan", workflow.utc_now(),
                                {"project_id": project_id, "data_kind": "test"})
    with TestClient(create_app(settings), headers=HEADERS) as restarted:
        restarted.auth = AUTH
        assert snapshot(settings, project_id) == before
        assert detail(restarted, result["run_id"])["status"] == "succeeded"
        recovered = detail(restarted, interrupted)
        assert recovered["status"] == "failed" and recovered["error_code"] == "INTERRUPTED"
        assert recovered["tool_calls"][0]["error_code"] == "INTERRUPTED"
        assert generate(restarted, project_id).status_code == 200


def test_unexpected_error_redacted_and_run_released(client, project_id, model):
    model.side_effect = RuntimeError("private a04-test-key a04-test-password traceback")
    response = generate(client, project_id)
    assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
    run = detail(client, response.json()["error"]["run_id"])
    assert run["status"] == "failed" and "private" not in response.text + json.dumps(run)
    assert "a04-test-key" not in json.dumps(run)


@pytest.mark.parametrize("invalid", [False, True])
def test_smoke_script_uses_isolated_database_and_truthful_results(settings, model, monkeypatch, capsys, invalid):
    monkeypatch.setattr(smoke_a04, "load_settings", lambda: settings)
    if invalid:
        model.return_value = model_response({})
    assert smoke_a04.main() == (1 if invalid else 0)
    output = json.loads(capsys.readouterr().out)["data"]
    assert output["data_kind"] == "test" and output["isolated_temporary_database"] is True
    assert not settings.database_path.exists()
    assert output["database_counts"]["responses"] == 0 and output["database_counts"]["reports"] == 0
    assert output["run_id"] and output["run"]["status"] == ("failed" if invalid else "succeeded")
    assert output["checks"]["two_successful_tools"] is (not invalid)
    assert output["database_counts"]["experiments"] == (0 if invalid else 1)
    model.assert_called_once()


def test_interruption_during_model_wait_cannot_create_draft_or_overwrite_failed_logs(client, settings, project_id, model, plan_data):
    def interrupted_response(*args, **kwargs):
        # 以 A01 启动清理模拟中断后的状态，返回晚到的 Mock 模型结果。
        init_db(settings.database_path)
        return model_response(plan_data)

    model.side_effect = interrupted_response
    response = generate(client, project_id)
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_STATE"
    run = detail(client, response.json()["error"]["run_id"])
    assert run["status"] == "failed" and run["error_code"] == "INTERRUPTED"
    assert len(run["tool_calls"]) == 1 and run["tool_calls"][0]["error_code"] == "INTERRUPTED"
    assert snapshot(settings, project_id)[1] is None
