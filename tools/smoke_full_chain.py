"""全链路冒烟（B 侧，A06）：项目 → 计划 → 草稿 → 工作台页面 → 发布 → 公开问卷 → 提交 → 统计。

本脚本验证从建项目开始，经工作流生成草稿、管理页面呈现、发布、公开提交和统计的链路。
本脚本用一个本地假模型服务（OpenAI Chat Completions 兼容）替掉真实模型，
让 A 的 workflow 真正执行一次，从而验证：

  - POST /api/projects/{id}/plan 真的走完 make_validation_plan + create_experiment 两个工具
  - agent_runs / tool_calls 落库，状态与结果引用可读
  - 三个管理页面在 draft / planned / collecting 三种状态下都能渲染
  - 发布后公开页 200、发布前 404、发布后编辑 409
  - 三类题真实提交入库、幂等重放、冲突 409、统计分母正确

**不调用真实模型，也不冒充真实结果**：脚本开头明确打印这是假模型，输出只用于功能验证；
旧配置的真实成功证据已保留；当前配置的真实验收另用 smoke_a07.py，不能以本脚本代替。

用法：python tools/smoke_full_chain.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DB = Path(tempfile.gettempdir()) / f"fo-chain-{uuid.uuid4().hex[:8]}.sqlite3"

# ---------------------------------------------------------------- 假模型返回的计划
LANDING = {
    "title": "校园活动报名网页服务",
    "summary": "面向社团组织者的服务需求验证，帮助收集并整理报名信息。",
    "target_users": "社团负责人、活动组织者",
    "value_proposition": "简化报名信息收集与整理",
    "features": ["定制活动介绍页", "报名信息统一收集", "结果导出"],
    "disclaimer": "目前为需求验证，不代表已有商业成交或完整服务上线。",
}
QUESTIONS = [
    {"id": "q1", "type": "single", "title": "你是否遇到报名信息整理困难？", "required": True,
     "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}, {"id": "o3", "label": "没有"}]},
    {"id": "q2", "type": "single", "title": "是否愿意了解或试用定制报名网页？", "required": True,
     "options": [{"id": "o1", "label": "愿意"}, {"id": "o2", "label": "不确定"}, {"id": "o3", "label": "不愿意"}]},
    {"id": "q3", "type": "text", "title": "你目前的报名流程有什么问题？", "required": False, "options": []},
    {"id": "q4", "type": "multiple", "title": "你最关心哪些功能？", "required": True,
     "options": [{"id": "o1", "label": "手机使用方便"}, {"id": "o2", "label": "信息导出"},
                 {"id": "o3", "label": "操作简单"}, {"id": "o4", "label": "暂时无需求"}]},
]
PLAN = {
    "target_customer": "有活动组织经验的社团负责人",
    "pain_points": ["报名信息汇总耗时", "活动通知发出后没人确认是否看到"],
    "value_proposition": "提供简单、可维护的活动报名网页",
    "hypotheses": [
        {"id": "h1", "statement": "部分组织者存在报名整理困难", "question_ids": ["q1"]},
        {"id": "h2", "statement": "部分组织者愿意试用定制网页", "question_ids": ["q2"]},
        {"id": "h3", "statement": "用户能指出现有流程的改进点", "question_ids": ["q3"]},
    ],
    "experiment_steps": ["邀请有活动组织经验的同学填写问卷", "收集 5 份以上回答后按真实数据复盘"],
    "next_tasks": ["人工检查问卷是否诱导回答", "确认报名页说明不承诺已上线服务"],
    "landing": LANDING,
    "questions": QUESTIONS,
}

CALLS: list[dict] = []


class MockModel(BaseHTTPRequestHandler):
    """OpenAI Chat Completions 兼容的最小实现；只回固定计划，不接任何真实供应商。"""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw.decode("utf-8"))
        except ValueError:
            request = {}
        CALLS.append({"path": self.path, "model": request.get("model"),
                      "messages": len(request.get("messages", [])),
                      "has_authorization": bool(self.headers.get("authorization"))})
        content = json.dumps(PLAN, ensure_ascii=False)
        body = json.dumps({
            "id": "mock-1", "object": "chat.completion", "model": request.get("model", "mock-model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 240, "total_tokens": 360},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        return


def start_mock_model() -> str:
    server = HTTPServer(("127.0.0.1", 0), MockModel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d/v1" % server.server_address[1]


MOCK_BASE = start_mock_model()
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_PATH": str(DB),
    "LLM_BASE_URL": MOCK_BASE,
    "LLM_API_KEY": "mock-key-not-a-real-secret",
    "LLM_MODEL": "mock-plan-model",
    "ADMIN_USER": "admin",
    "ADMIN_PASSWORD": "chain-password",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect_db  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN = ("admin", "chain-password")
# 管理端写请求要求 Origin 与 PUBLIC_BASE_URL 严格同源
WRITE = {"Origin": "http://testserver", "X-Requested-With": "FirstOrderLab"}

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + detail) if detail else ""))


def main() -> int:
    print("=" * 94)
    print("假模型服务：%s（OpenAI Chat Completions 兼容，只回固定计划）" % MOCK_BASE)
    print("数据库：%s" % DB)
    print("注意：本脚本不调用真实模型，输出只证明链路可用，不作为真实模型证据。")
    print("=" * 94)

    app = create_app()
    with TestClient(app, follow_redirects=False) as client:
        client.auth = ADMIN

        # ---------- 1. 建项目 ----------
        payload = {"name": "校园活动报名网站服务", "idea": "为校园社团制作低成本活动报名网页。",
                   "target_users": "社团负责人", "skills": "基础网页开发", "budget_cny": 100,
                   "validation_goal": "验证是否有试用需求", "data_kind": "test"}
        response = client.post("/api/projects", json=payload, headers=WRITE)
        check("POST /api/projects 201", response.status_code == 201,
              f"状态 {response.status_code} {response.text[:120]}")
        project_id = response.json()["data"]["project_id"]
        print("      project_id = %s" % project_id)

        html = client.get("/").text
        check("项目列表页出现新项目（服务端渲染）",
              "校园活动报名网站服务" in html and ("project-row-%s" % project_id) in html)

        html = client.get(f"/projects/{project_id}").text
        check("工作台 draft 态：无方案无草稿的空状态齐全",
              'data-testid="plan-empty"' in html and 'data-testid="draft-empty"' in html)
        check("工作台 draft 态：统计区提示没有实验",
              "还没有实验，无法统计" in html)

        # ---------- 2. 生成计划（真实工作流 + 假模型） ----------
        response = client.post(f"/api/projects/{project_id}/plan", json={}, headers=WRITE)
        check("POST /plan 200", response.status_code == 200,
              f"状态 {response.status_code} {response.text[:160]}")
        data = response.json()["data"] if response.status_code == 200 else {}
        experiment_id = data.get("experiment_id")
        run_id = data.get("run_id")
        check("  返回 experiment_id 与 run_id", bool(experiment_id) and bool(run_id),
              f"experiment={experiment_id} run={run_id}")
        check("  假模型收到了一次 chat/completions 调用",
              any(c["path"].endswith("/chat/completions") for c in CALLS),
              json.dumps(CALLS, ensure_ascii=False))
        check("  调用带了 Authorization 头（密钥不回显）",
              all(c["has_authorization"] for c in CALLS))

        # ---------- 3. 工具日志 ----------
        runs = client.get(f"/api/projects/{project_id}/runs").json()["data"]
        check("运行记录 1 条且 succeeded",
              len(runs) == 1 and runs[0]["status"] == "succeeded",
              json.dumps([{k: r[k] for k in ("action", "status", "error_code")} for r in runs],
                         ensure_ascii=False))
        detail = client.get(f"/api/runs/{run_id}").json()["data"]
        names = [tool["tool_name"] for tool in detail["tool_calls"]]
        check("两个工具都被真实记录：make_validation_plan + create_experiment",
              names == ["make_validation_plan", "create_experiment"], str(names))
        check("两个工具状态均为 succeeded",
              all(tool["status"] == "succeeded" for tool in detail["tool_calls"]))
        check("工具带结果引用（不是空壳日志）",
              all(tool["output_ref"] for tool in detail["tool_calls"]),
              json.dumps([t["output_ref"] for t in detail["tool_calls"]], ensure_ascii=False)[:200])

        # ---------- 4. 工作台 planned 态 ----------
        html = client.get(f"/projects/{project_id}").text
        check("工作台 planned 态：方案来自真实工作流",
              "有活动组织经验的社团负责人" in html and "报名信息汇总耗时" in html)
        check("  3 条待验证假设齐全", all(("%s　" % h) in html for h in ("h1", "h2", "h3")))
        check("  草稿区 4 题、服务页文案齐全",
              html.count("question-q") == 4 and "校园活动报名网页服务" in html)
        check("  出现发布按钮与发布提示",
              'data-testid="experiment-publish"' in html and 'data-testid="publish-hint"' in html)
        check("  未发布时统计区仍提示未发布", "INVALID_STATE" in html)
        check("  未发布时分享链接为空状态", 'data-testid="links-empty"' in html)

        # ---------- 5. 人工发布 ----------
        # 注意：Experiment schema 不含 public_token，token 只能从库里读或从发布返回的 URL 里取
        with connect_db(DB) as connection:
            row = connection.execute("SELECT public_token FROM experiments WHERE id = ?",
                                     (experiment_id,)).fetchone()
        token = row["public_token"] if row else ""
        # token_urlsafe(32) → 43 个 base64url 字符 = 32 字节 = 256 位随机量（总纲要求 ≥128 位）
        check("实验已生成足够强的公开 token（≥128 位随机量）",
              len(token) >= 22, "长度 %d 字符（≈%d 位）" % (len(token), len(token) * 6))
        check("发布前公开服务页 404", client.get("/e/%s" % token).status_code == 404)
        response = client.post(f"/api/experiments/{experiment_id}/publish", json={}, headers=WRITE)
        check("POST /publish 200", response.status_code == 200,
              f"状态 {response.status_code} {response.text[:120]}")
        published = response.json()["data"]
        check("  返回 service_url 与 survey_url",
              bool(published.get("service_url")) and bool(published.get("survey_url")),
              published.get("survey_url", ""))
        check("  公开 URL 由受控配置拼出且与库里 token 一致",
              token in published["survey_url"], published["survey_url"])
        check("发布后公开服务页 200", client.get("/e/%s" % token).status_code == 200)
        survey = client.get("/e/%s/survey" % token)
        check("发布后公开问卷 200", survey.status_code == 200)
        check("  公开问卷带 data-questions（上一轮修复点）", "data-questions=" in survey.text)
        check("  公开问卷渲染三类题（checkbox 含知情同意那一个，共 5 个）",
              survey.text.count('type="radio"') == 6
              and survey.text.count('type="checkbox"') == 5
              and survey.text.count("<textarea") == 1,
              "radio=%d checkbox=%d textarea=%d" % (survey.text.count('type="radio"'),
                                                    survey.text.count('type="checkbox"'),
                                                    survey.text.count("<textarea")))

        response = client.patch(f"/api/experiments/{experiment_id}",
                                json={"landing": LANDING, "questions": QUESTIONS}, headers=WRITE)
        check("发布后编辑 409 EXPERIMENT_LOCKED",
              response.status_code == 409 and response.json()["error"]["code"] == "EXPERIMENT_LOCKED",
              f"状态 {response.status_code}")

        # ---------- 6. 公开提交 ----------
        submission_id = str(uuid.uuid4())
        answers = {"q1": "o1", "q2": "o1", "q3": "报名信息散在聊天记录里", "q4": ["o1", "o2"]}
        body = {"client_submission_id": submission_id, "consent": True, "answers": answers}
        response = client.post(f"/api/public/e/{token}/responses", json=body)
        check("三类题提交 201", response.status_code == 201,
              f"状态 {response.status_code} {response.text[:160]}")
        check("  返回 response_id",
              bool(response.json().get("data", {}).get("response_id")))

        with connect_db(DB) as connection:
            stored = connection.execute(
                "SELECT answers_json FROM responses WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
        check("  数据库里存的是**带答案**的记录（上一轮缺陷的验收点）",
              stored is not None and json.loads(stored["answers_json"]) == answers,
              stored["answers_json"] if stored else "无记录")

        response = client.post(f"/api/public/e/{token}/responses", json=body)
        check("同 UUID 同内容重放 200 + duplicate",
              response.status_code == 200 and response.json()["data"]["duplicate"] is True,
              f"状态 {response.status_code}")

        conflict = dict(body, answers={"q1": "o2", "q2": "o1", "q4": ["o1"]})
        response = client.post(f"/api/public/e/{token}/responses", json=conflict)
        check("同 UUID 异内容 409 IDEMPOTENCY_CONFLICT",
              response.status_code == 409
              and response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT")

        with connect_db(DB) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS n FROM responses WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()["n"]
        check("  重放与冲突都没有多写记录", count == 1, f"实际 {count} 条")

        # ---------- 7. 统计与 collecting 态 ----------
        stats = client.get(f"/api/experiments/{experiment_id}/statistics").json()["data"]
        check("统计 response_count = 1", stats["response_count"] == 1, str(stats["response_count"]))
        by_id = {q["id"]: q for q in stats["questions"]}
        check("  q1 单选分母 1 份 → 100.00%",
              by_id["q1"]["answered_count"] == 1
              and by_id["q1"]["options"][0]["percentage"] == 100.0)
        check("  q4 多选两项各 100%",
              by_id["q4"]["answered_count"] == 1
              and [o["percentage"] for o in by_id["q4"]["options"]] == [100.0, 100.0, 0.0, 0.0])
        check("  q3 简答 1 条文本",
              by_id["q3"]["answered_count"] == 1 and len(by_id["q3"]["texts"]) == 1)

        html = client.get(f"/projects/{project_id}").text
        check("工作台 collecting 态：显示分享链接与复制按钮",
              token in html and 'data-testid="copy-survey-url"' in html)
        check("  不再显示发布按钮", 'data-testid="experiment-publish"' not in html)
        check("  统计区已进入取数分支（发布后）", "正在读取统计" in html)
        check("  草稿区标明已发布并锁定", "已锁定" in html)

        response = client.get("/")
        check("项目列表状态派生为 collecting",
              "收反馈中" in response.text, f"状态 {response.status_code}")

    failed = [ok for ok, _ in results]
    print("=" * 94)
    bad = [name for ok, name in results if not ok]
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(bad), len(bad)))
    for name in bad:
        print("  失败：", name)
    print("假模型调用次数：%d（真实模型调用 0 次）" % len(CALLS))
    try:
        DB.unlink(missing_ok=True)
    except OSError:
        pass
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
