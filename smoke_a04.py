import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import load_settings
from app.db import connect_db
from app.main import create_app
from app.schemas import Plan


def main() -> int:
    configured = load_settings()
    # 真实模型凭据保留在内存；数据库和管理凭据均使用独立测试配置。
    with TemporaryDirectory(prefix="firstorder-a04-") as directory:
        settings = replace(
            configured, app_env="test", database_path=Path(directory) / "a04-smoke.sqlite3",
            admin_user="a04-smoke-admin", admin_password=SecretStr("a04-smoke-test-password"),
            public_base_url="http://127.0.0.1:8000",
        )
        headers = {"Origin": settings.public_base_url, "X-Requested-With": "FirstOrderLab"}
        with TestClient(create_app(settings), headers=headers) as client:
            client.auth = (settings.admin_user, settings.admin_password.get_secret_value())
            created = client.post("/api/projects", json={
                "name": "A04 真实接口测试：校园活动报名网页服务",
                "idea": "为校园社团提供低成本活动报名网页定制服务，希望先验证需求，不宣称已有交付或订单。",
                "target_users": "有活动组织经验的校园社团负责人",
                "skills": "基础网页开发、Python、AI 辅助编程", "budget_cny": 100,
                "validation_goal": "验证试用意愿、最关心功能和可接受价格", "data_kind": "test",
            })
            if created.status_code != 201:
                print(json.dumps({"error": created.json(), "stage": "create_test_project"}, ensure_ascii=False))
                return 1
            project_id = created.json()["data"]["project_id"]
            response = client.post(f"/api/projects/{project_id}/plan", json={})
            stored_runs = client.get(f"/api/projects/{project_id}/runs").json()["data"]
            run_id = stored_runs[0]["id"] if stored_runs else None
            run = client.get(f"/api/runs/{run_id}").json()["data"] if run_id else None
            with connect_db(settings.database_path) as connection:
                project = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
                experiment = connection.execute("SELECT * FROM experiments WHERE project_id = ?", (project_id,)).fetchone()
                plan = Plan.model_validate_json(project["plan_json"]) if project["plan_json"] else None
                consistent = bool(plan and experiment and
                                  plan.landing.model_dump() == json.loads(experiment["landing_json"]) and
                                  [question.model_dump() for question in plan.questions] == json.loads(experiment["questions_json"]))
                counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in ("projects", "experiments", "agent_runs", "tool_calls", "responses", "reports")}
            checks = {"plan_stored_and_validated": plan is not None, "draft_consistent": consistent,
                      "unpublished": bool(experiment and experiment["published_at"] is None),
                      "two_successful_tools": bool(run and [tool["tool_name"] for tool in run["tool_calls"]]
                                                   == ["make_validation_plan", "create_experiment"] and
                                                   all(tool["status"] == "succeeded" for tool in run["tool_calls"])),
                      "run_succeeded": bool(run and run["status"] == "succeeded")}
        # 新应用生命周期及数据库连接重建后，实际成功产物仍存在。
        with TestClient(create_app(settings), headers=headers) as restarted:
            restarted.auth = (settings.admin_user, settings.admin_password.get_secret_value())
            restored = restarted.get(f"/api/projects/{project_id}").json()["data"]
            checks["restart_preserved"] = bool(plan and restored["plan"] == plan.model_dump() and
                                               restored["experiment"]["id"] == experiment["id"] and
                                               restarted.get(f"/api/runs/{run_id}").json()["data"]["status"] == "succeeded")
        output = {"data_kind": "test", "isolated_temporary_database": True,
                  "plan_http_status": response.status_code, "response": response.json(),
                  "project_id": project_id, "run_id": run_id, "checks": checks, "database_counts": counts,
                  "hypothesis_count": len(plan.hypotheses) if plan else None,
                  "question_count": len(plan.questions) if plan else None, "run": run}
        print(json.dumps({"data": output}, ensure_ascii=False, indent=2))
        return 0 if response.status_code == 200 and all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
