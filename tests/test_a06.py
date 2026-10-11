import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import repositories
from app.db import connect_db
from app.main import create_app
from app.schemas import Question, Statistics
from app.services.statistics import calculate_statistics
from smoke_a05 import seed_draft
from test_a05 import setup, admin, publish, body


@pytest.fixture
def client(setup):
    with TestClient(create_app(setup[0])) as client:
        yield client


def read(client, setup, suffix):
    return client.get(f"/api/experiments/{setup[2]}/{suffix}", **admin(setup))


def submit(client, setup, answers):
    payload = {"client_submission_id": str(uuid4()), "consent": True, "answers": answers}
    response = client.post(f"/api/public/e/{setup[3]}/responses", json=payload)
    assert response.status_code == 201
    return response.json()["data"]


def optional_multiple(client, setup):
    data = client.get(f"/api/experiments/{setup[2]}", **admin(setup)).json()["data"]
    data["questions"][3]["required"] = False
    response = client.patch(f"/api/experiments/{setup[2]}",
                            json={key: data[key] for key in ("landing", "questions")}, **admin(setup))
    assert response.status_code == 200


@pytest.mark.parametrize("suffix", ["responses", "statistics"])
def test_admin_only(client, setup, suffix):
    endpoint = f"/api/experiments/{setup[2]}/{suffix}"
    for auth in (None, ("wrong", "wrong")):
        response = client.get(endpoint, auth=auth)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("suffix", ["responses", "statistics"])
def test_missing(client, setup, suffix):
    response = client.get(f"/api/experiments/missing/{suffix}", **admin(setup))
    assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"


def test_draft_no_statistics(client, setup):
    assert read(client, setup, "responses").json() == {"data": []}
    response = read(client, setup, "statistics")
    assert response.status_code == 400 and response.json()["error"]["code"] == "INVALID_STATE"


def test_zero_feedback_preserves_all_questions_options(client, setup):
    publish(client, setup)
    response = read(client, setup, "statistics")
    assert response.status_code == 200
    result = Statistics.model_validate(response.json()["data"])
    assert result.response_count == 0 and result.cutoff_response_id == 0
    original = client.get(f"/api/experiments/{setup[2]}", **admin(setup)).json()["data"]["questions"]
    assert len(result.questions) == len(original)
    for question, source in zip(result.questions, original):
        assert question.answered_count == 0
        if source["type"] == "text":
            assert question.texts == []
        else:
            assert [option.id for option in question.options] == [option["id"] for option in source["options"]]
            assert all(option.count == 0 and option.percentage is None for option in question.options)


def test_T07_denominator_and_raw_contract(client, setup, monkeypatch):
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: pytest.fail("统计不得调用模型"))
    optional_multiple(client, setup)
    publish(client, setup)
    samples = [
        {"q1": "o1", "q2": "o1", "q3": "  第一份反馈  ", "q4": ["o1", "o2"]},
        {"q1": "o1", "q2": "o1", "q4": ["o1", "o2"]},
        {"q1": "o2", "q2": "o1", "q3": "第二份反馈", "q4": ["o2"]},
        {"q1": "o2", "q2": "o1", "q3": "   ", "q4": []},
    ]
    ids = [submit(client, setup, sample)["response_id"] for sample in samples]
    raw_response = read(client, setup, "responses")
    assert raw_response.status_code == 200
    raw = raw_response.json()["data"]
    assert len(raw) == 4 and [item["id"] for item in raw] == ids
    assert all(set(item) == {"id", "answers", "created_at"} for item in raw)
    assert raw[0]["answers"]["q3"] == "第一份反馈"
    assert "q3" not in raw[3]["answers"] and "q4" not in raw[3]["answers"]
    data = read(client, setup, "statistics").json()["data"]
    assert set(data) == {"experiment_id", "response_count", "cutoff_response_id", "questions"}
    assert data["response_count"] == 4 and data["cutoff_response_id"] == ids[-1]
    questions = {q["id"]: q for q in data["questions"]}
    assert questions["q1"]["options"][0]["percentage"] == 50.0
    multiple = questions["q4"]
    assert multiple["answered_count"] == 3
    assert multiple["options"][0]["count"] == 2 and multiple["options"][0]["percentage"] == 66.67
    assert multiple["options"][1]["percentage"] == 100.0
    assert multiple["options"][2]["count"] == 0 and multiple["options"][2]["percentage"] == 0.0
    assert sum(option["percentage"] for option in multiple["options"]) > 100
    assert questions["q3"]["texts"] == [{"response_id": ids[0], "text": "第一份反馈"},
                                            {"response_id": ids[2], "text": "第二份反馈"}]
    assert questions["q3"]["answered_count"] == 2


def test_noncontiguous_ids_and_experiment_isolation(client, setup):
    publish(client, setup)
    first = submit(client, setup, body()["answers"])["response_id"]
    _, other_id, other_token = seed_draft(setup[0])
    assert client.post(f"/api/experiments/{other_id}/publish", json={}, **admin(setup)).status_code == 200
    other = client.post(f"/api/public/e/{other_token}/responses", json=body())
    assert other.status_code == 201
    last = submit(client, setup, body()["answers"])["response_id"]
    assert last > first + 1
    result = read(client, setup, "statistics").json()["data"]
    assert result["response_count"] == 2 and result["cutoff_response_id"] == last
    assert [r["id"] for r in read(client, setup, "responses").json()["data"]] == [first, last]


def test_optional_empty_nonzero_response_count(client, setup):
    optional_multiple(client, setup)
    publish(client, setup)
    submit(client, setup, {"q1": "o1", "q2": "o1", "q3": " ", "q4": []})
    result = read(client, setup, "statistics").json()["data"]
    assert result["response_count"] == 1
    assert result["questions"][2]["answered_count"] == 0
    assert result["questions"][3]["answered_count"] == 0
    assert all(o["percentage"] is None for o in result["questions"][3]["options"])


def test_reads_allowed_during_agent_run(client, setup):
    publish(client, setup)
    with connect_db(setup[0].database_path) as connection:
        connection.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'review','running',?)",
                           (str(uuid4()), setup[1], "2026-10-11T00:00:00+00:00"))
    for suffix in ("responses", "statistics"):
        assert read(client, setup, suffix).status_code == 200


def test_restart_consistency(setup):
    with TestClient(create_app(setup[0])) as client:
        publish(client, setup)
        submit(client, setup, body()["answers"])
        expected = {suffix: read(client, setup, suffix).json() for suffix in ("responses", "statistics")}
    with TestClient(create_app(setup[0])) as client:
        for suffix, data in expected.items():
            assert read(client, setup, suffix).json() == data


def test_snapshot_excludes_concurrent_insert(client, setup):
    publish(client, setup)
    submit(client, setup, body()["answers"])
    with connect_db(setup[0].database_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    with connect_db(setup[0].database_path) as connection:
        class ExperimentCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                row = self.cursor.fetchone()
                with connect_db(setup[0].database_path) as writer:
                    repositories.insert_response(writer, setup[2], str(uuid4()), body()["answers"], "2026-10-11T00:00:00+00:00")
                return row

        class InterleavedConnection:
            def execute(self, sql, parameters=()):
                cursor = connection.execute(sql, parameters)
                return ExperimentCursor(cursor) if sql.startswith("SELECT id, questions_json") else cursor

        _, responses = repositories.response_snapshot(InterleavedConnection(), setup[2])
        assert len(responses) == 1
    assert read(client, setup, "statistics").json()["data"]["response_count"] == 2


def test_pure_statistics_empty_optional_fields(client, setup):
    original = client.get(f"/api/experiments/{setup[2]}", **admin(setup)).json()["data"]["questions"]
    result = calculate_statistics(setup[2], [Question.model_validate(q) for q in original],
                                  [{"id": 12, "answers": {"q3": "  ", "q4": []}}])
    assert result.response_count == 1 and result.cutoff_response_id == 12
    assert all(q.answered_count == 0 for q in result.questions)
