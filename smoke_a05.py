"""实际 TCP HTTP 验收；测试草稿来自契约样例，不调用或冒充真实模型。"""
import json
import re
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4

import httpx
import uvicorn
from pydantic import SecretStr

from app import repositories
from app.config import PROJECT_ROOT, Settings
from app.db import connect_db, init_db
from app.main import create_app
from app.schemas import Plan, ProjectCreate
from app.services.workflow import utc_now


def test_settings(path, base_url="http://127.0.0.1:8000"):
    return Settings("test", path, "https://model.example.invalid/v1", SecretStr("a05-test-key"),
                    "not-called", "a05-test-admin", SecretStr("a05-test-password"), base_url)


def seed_draft(settings):
    init_db(settings.database_path)
    document = (PROJECT_ROOT / "docs/tasks/01_项目总任务大纲.md").read_text(encoding="utf-8")
    plan = Plan.model_validate(json.loads(re.search(r"```json\s*(.*?)\s*```",
                                                  document.split("### 7.2 ", 1)[1], re.S).group(1)))
    with connect_db(settings.database_path) as connection:
        project_id = repositories.create_project(connection, ProjectCreate(
            name="A05 隔离 HTTP 测试", idea="明确标记为 test 的契约草稿样例，不是模型验收结果",
            target_users="测试对象", skills="Python", budget_cny=100,
            validation_goal="验证发布和问卷持久化", data_kind="test"))
        experiment_id, token = str(uuid4()), token_urlsafe(16)
        repositories.save_experiment(connection, project_id, plan, experiment_id, token, utc_now())
    return project_id, experiment_id, token


@contextmanager
def listening(settings, bound_socket):
    server = uvicorn.Server(uvicorn.Config(create_app(settings), log_level="critical", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets": [bound_socket]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("隔离 HTTP 服务启动失败")
            time.sleep(.02)
        yield
    finally:
        server.should_exit = True
        thread.join(10)
        bound_socket.close()
        if thread.is_alive():
            raise RuntimeError("隔离 HTTP 服务未正常停止")


def bind_socket():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


def main():
    with TemporaryDirectory(prefix="firstorder-a05-") as directory:
        sock = bind_socket()
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        settings = test_settings(Path(directory) / "a05.sqlite3", base)
        project_id, experiment_id, token = seed_draft(settings)
        auth = (settings.admin_user, settings.admin_password.get_secret_value())
        headers = {"Origin": base, "X-Requested-With": "FirstOrderLab"}
        path = f"/api/experiments/{experiment_id}"
        submission = {"client_submission_id": str(uuid4()), "consent": True,
                      "answers": {"q1": "o2", "q2": "o1", "q3": "HTTP 测试反馈", "q4": ["o2", "o1"]}}
        checks = {}
        with listening(settings, sock), httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
            checks["unauthorized_401"] = client.get(path).status_code == 401
            checks["unpublished_404"] = client.get(f"/e/{token}").status_code == 404
            draft = client.get(path, auth=auth).json()["data"]
            edit = {"landing": draft["landing"], "questions": draft["questions"]}
            edit["landing"]["title"] = "A05 实际 HTTP 编辑测试"
            checks["edit_200"] = client.patch(path, json=edit, auth=auth, headers=headers).status_code == 200
            checks["forbidden_403"] = client.post(path + "/publish", json={}, auth=auth).status_code == 403
            published = client.post(path + "/publish", json={}, auth=auth, headers=headers)
            assert published.status_code == 200
            publish_data = published.json()["data"]
            checks["publish_idempotent"] = client.post(path + "/publish", json={}, auth=auth, headers=headers).json()["data"] == publish_data
            checks["published_locked"] = client.patch(path, json=edit, auth=auth, headers=headers).status_code == 409
            endpoint = f"/api/public/e/{token}/responses"
            first = client.post(endpoint, json=submission)
            assert first.status_code == 201
            submission["answers"]["q4"].reverse()
            replay = client.post(endpoint, json=submission)
            checks["replay_200"] = replay.status_code == 200 and replay.json()["data"]["duplicate"]
            checks["same_response_id"] = first.json()["data"]["response_id"] == replay.json()["data"]["response_id"]
            submission["answers"]["q1"] = "o1"
            checks["conflict_409"] = client.post(endpoint, json=submission).status_code == 409
            public_page = client.get(f"/e/{token}")
            page_status = public_page.status_code
            checks["missing_B_template_reported"] = page_status == 500 and "成员 B" in public_page.json()["error"]["message"]
        with connect_db(settings.database_path) as connection:
            row = connection.execute("SELECT * FROM responses").fetchone()
            checks["one_persistent_response"] = connection.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 1
            checks["three_answer_types_saved"] = json.loads(row["answers_json"])["q4"] == ["o1", "o2"] and "q3" in json.loads(row["answers_json"])
            checks["no_fabricated_agent_logs"] = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0
        restarted_socket = bind_socket()
        restarted_base = f"http://127.0.0.1:{restarted_socket.getsockname()[1]}"
        with listening(settings, restarted_socket), httpx.Client(base_url=restarted_base, timeout=10, trust_env=False) as client:
            restored = client.get(path, auth=auth).json()["data"]
            checks["restart_published_preserved"] = restored["published_at"] == publish_data["published_at"] and restored["service_url"] == publish_data["service_url"]
            submission["answers"]["q1"] = "o2"
            checks["restart_answers_preserved"] = client.post(endpoint, json=submission).json()["data"] == replay.json()["data"]
        print(json.dumps({"data_kind": "test", "transport": "real TCP HTTP / Uvicorn",
                          "draft_source": "contract fixture; no model call or fabricated successful run",
                          "checks": checks, "first_submit_status": first.status_code,
                          "first_submit": first.json(), "public_page_status": page_status,
                          "B_pages_complete": False}, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
