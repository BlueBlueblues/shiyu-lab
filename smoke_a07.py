"""真实模型 + 实际 HTTP 监听验收；临时库、test 项目、无自动重试。"""
import hashlib
import json
import subprocess
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import SecretStr

from app import repositories
from app.config import PROJECT_ROOT, load_settings
from app.db import connect_db
from app.schemas import Plan, Report, Statistics
from app.services.reports import markdown_report
from app.services.workflow import MODEL_SECONDS, WORKFLOW_SECONDS
from smoke_a05 import bind_socket, listening


def main():
    configured = load_settings()
    evidence_dir = PROJECT_ROOT / "docs/model-verification" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    evidence_dir.mkdir(parents=True, exist_ok=False)
    evidence = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip(),
                "started_at": datetime.now(timezone.utc).isoformat(), "provider": urlsplit(configured.llm_base_url).hostname,
                "base_url": configured.llm_base_url, "evidence_dir": str(evidence_dir),
                "isolated_temporary_database": True,
                "provider_host": urlsplit(configured.llm_base_url).hostname, "model": configured.llm_model,
                "data_kind": "test", "real_user_trial": False, "transport": "real TCP HTTP",
                "automatic_retries": 0,
                "budgets_seconds": {"http_each_phase": 60, "model_wait": MODEL_SECONDS,
                                    "workflow_including_cleanup": WORKFLOW_SECONDS, "smoke_client_each_phase": 85},
                "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT)),
                "source_sha256": {name: hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest()
                                  for name in ("app/services/llm_client.py", "app/services/workflow.py", "app/services/reviews.py", "smoke_a07.py")},
                "stages": [], "checks": {}, "model_calls": [], "success": False}
    started = time.monotonic()
    with TemporaryDirectory(prefix="firstorder-a07-real-") as directory:
        sock = bind_socket()
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        settings = replace(configured, app_env="test", database_path=Path(directory) / "a07.sqlite3",
                           public_base_url=base, admin_user="a07-smoke-admin",
                           admin_password=SecretStr("a07-smoke-test-password"))
        assert settings.database_path.parent == Path(directory)
        assert settings.database_path.resolve() != configured.database_path.resolve()
        auth = (settings.admin_user, settings.admin_password.get_secret_value())
        headers = {"Origin": base, "X-Requested-With": "FirstOrderLab"}
        with listening(settings, sock), httpx.Client(base_url=base, timeout=85, trust_env=False) as client:
            def request(stage, method, path, expected, payload=None, admin=True):
                tick = time.monotonic()
                kwargs = {"auth": auth, "headers": headers} if admin else {}
                if payload is not None:
                    kwargs["json"] = payload
                response = client.request(method, path, **kwargs)
                entry = {"stage": stage, "http_status": response.status_code,
                         "duration_seconds": round(time.monotonic() - tick, 3)}
                if "application/json" in response.headers.get("content-type", ""):
                    entry["response"] = response.json()
                evidence["stages"].append(entry)
                if response.status_code != expected:
                    raise RuntimeError(f"{stage}: HTTP {response.status_code}")
                return response

            try:
                project = {"name": "TEST 真实模型验收：校园 PPT 美化服务",
                           "idea": "为大学生提供低成本 PPT 排版与视觉美化服务；本项目用于程序验收，不代表已有订单或真实试用。",
                           "target_users": "需要课堂展示、答辩或社团汇报的学生", "skills": "Python、AI 辅助设计、PPT 制作",
                           "budget_cny": 50, "validation_goal": "验证需求、可接受价格和试用意愿", "data_kind": "test"}
                evidence["project_input"] = project
                assert project["data_kind"] == "test"
                pid = request("create", "POST", "/api/projects", 201, project).json()["data"]["project_id"]
                evidence["project_id"] = pid
                generated = request("real_plan", "POST", f"/api/projects/{pid}/plan", 200, {}).json()["data"]
                evidence["plan_run_id"] = generated["run_id"]
                eid = generated["experiment_id"]
                detail = request("detail", "GET", f"/api/projects/{pid}", 200).json()["data"]
                plan = Plan.model_validate(detail["plan"])
                evidence["checks"]["plan_contract"] = True
                evidence["checks"]["test_project"] = detail["project"]["data_kind"] == "test"
                evidence["plan"] = plan.model_dump()
                (evidence_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
                request("publish", "POST", f"/api/experiments/{eid}/publish", 200, {})
                with connect_db(settings.database_path) as connection:
                    token = connection.execute("SELECT public_token FROM experiments WHERE id=?", (eid,)).fetchone()[0]
                submit_path = f"/api/public/e/{token}/responses"
                def answers(index):
                    return {q.id: ("受控 test 回答：希望先了解交付周期和费用，不代表真实用户意见。" if q.type == "text"
                                  else [q.options[index % len(q.options)].id] if q.type == "multiple"
                                  else q.options[index % len(q.options)].id) for q in plan.questions}
                for index in range(3):
                    data = {"client_submission_id": str(uuid4()), "consent": True, "answers": answers(index)}
                    # token 不保留在脱敏证据中；保存受控回答以便核对反馈依据。
                    evidence.setdefault("controlled_answers", []).append(data["answers"])
                    request(f"test_response_{index+1}", "POST", submit_path, 201, data, admin=False)
                stats = request("statistics", "GET", f"/api/experiments/{eid}/statistics", 200).json()["data"]
                Statistics.model_validate(stats)
                evidence["checks"]["statistics_contract"] = True
                evidence["checks"]["three_test_responses"] = stats["response_count"] == 3
                result = request("real_review", "POST", f"/api/projects/{pid}/review", 200, {}).json()["data"]
                evidence["review_run_id"] = result["run_id"]
                saved = request("report", "GET", f"/api/projects/{pid}/report", 200).json()["data"]
                Report.model_validate(saved)
                evidence["checks"]["report_json_contract"] = True
                evidence["checks"]["report_test_snapshot"] = saved["data_kind"] == "test" and saved["response_count"] == 3 and not saved["stale"]
                evidence["report"] = saved
                (evidence_dir / "review.json").write_text(json.dumps(saved["review"], ensure_ascii=False, indent=2), encoding="utf-8")
                markdown_response = request("markdown", "GET", f"/api/projects/{pid}/report.md", 200)
                md = markdown_response.text
                evidence["checks"]["report_matches_prior_statistics"] = saved["statistics"] == stats
                with connect_db(settings.database_path) as connection:
                    row = connection.execute("SELECT * FROM reports WHERE id=?", (saved["id"],)).fetchone()
                    evidence["checks"]["report_matches_persistent_snapshot"] = json.loads(row["statistics_json"]) == stats and json.loads(row["review_json"]) == saved["review"]
                    evidence["checks"]["markdown_matches_persistent_snapshot"] = md == markdown_report(repositories.latest_report(connection, pid))
                evidence["checks"]["markdown_download_contract"] = (
                    markdown_response.headers.get("content-type", "").startswith("text/markdown")
                    and markdown_response.headers.get("content-disposition") == f'attachment; filename="firstorder-report-{saved["id"]}.md"'
                    and "数据标识：test" in md)
                evidence["markdown_sha256"] = hashlib.sha256(md.encode()).hexdigest()
                (evidence_dir / "report.md").write_text(md, encoding="utf-8")
                request("new_test_response", "POST", submit_path, 201,
                        {"client_submission_id": str(uuid4()), "consent": True, "answers": answers(3)}, admin=False)
                stale = request("stale_report", "GET", f"/api/projects/{pid}/report", 200).json()["data"]
                old_md = request("old_markdown", "GET", f"/api/projects/{pid}/report.md", 200).text
                evidence["checks"]["stale_true_old_snapshot"] = stale["stale"] and stale["statistics"] == stats and stale["response_count"] == 3
                evidence["checks"]["old_markdown_unchanged"] = old_md == md
                final_detail = request("final_detail", "GET", f"/api/projects/{pid}", 200).json()["data"]
                evidence["checks"]["collecting_after_new_feedback"] = final_detail["project"]["status"] == "collecting"
                evidence["success"] = all(evidence["checks"].values())
            except Exception as error:
                # 只保留阶段和异常类型；不输出网络异常中潜在的敏感 URL 或请求。
                evidence["failure"] = {"type": type(error).__name__, "stage": evidence["stages"][-1]["stage"] if evidence["stages"] else "startup"}
            finally:
                if "project_id" in evidence:
                    runs = client.get(f"/api/projects/{evidence['project_id']}/runs", auth=auth).json()["data"]
                    evidence["runs"] = []
                    for run in runs:
                        record = client.get(f"/api/runs/{run['id']}", auth=auth).json()["data"]
                        evidence["runs"].append(record)
                        for tool in record["tool_calls"]:
                            call = (tool["output_ref"] or {}).get("model_call")
                            if call:
                                evidence["model_calls"].append(call)
                    evidence["failed_runs"] = sum(r["status"] == "failed" for r in runs)
                    model_stages = [stage for stage in evidence["stages"] if stage["stage"] in {"real_plan", "real_review"}]
                    evidence["model_stage_attempt_count"] = len(model_stages)
                    evidence["model_stage_failure_count"] = sum(stage["http_status"] != 200 for stage in model_stages)
                    # 工作流等待超时可能没有 CallInfo，不能将缺失元数据误记为零失败。
                    evidence["model_call_failure_count"] = (
                        sum(not call["success"] for call in evidence["model_calls"])
                        if len(evidence["model_calls"]) == len(model_stages) else None)
        evidence["duration_seconds"] = round(time.monotonic() - started, 3)
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    evidence["check_pass_count"] = sum(bool(value) for value in evidence["checks"].values())
    evidence["check_fail_count"] = sum(not value for value in evidence["checks"].values())
    (evidence_dir / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: evidence.get(key) for key in ("commit", "base_url", "model", "evidence_dir", "started_at", "finished_at",
        "duration_seconds", "success", "failure", "checks", "plan_run_id", "review_run_id", "model_calls", "failed_runs",
        "model_stage_attempt_count", "model_stage_failure_count", "model_call_failure_count")}, ensure_ascii=False, indent=2))
    return 0 if evidence["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
