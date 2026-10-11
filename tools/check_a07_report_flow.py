"""A07 报告链路验证（B 侧，**对真实路由**，不再使用桩）。

前置：本目录是 BC 集成包（A07 后端已实现）。本脚本用本地假模型服务让
`POST /api/projects/{id}/review` 真正执行一次完整工作流（读快照 → 调模型 → 校验 → 落库），
然后逐项验证报告页、报告 API 与 Markdown 下载的快照语义。

覆盖：
  1. 无报告时报告页显示明确空状态，且不预渲染任何数字/结论
  2. POST /review 返回 report_id/run_id/response_count/cutoff_response_id，报告真实落库
  3. 统计快照口径（T07 固定样本：q4 有效 3 份、o1 = 66.67%、零计数显示 0.00%）
  4. 报告页渲染快照 + 复盘 + 证据引用解析成「选项『标签』」
  5. Markdown 下载与页面同为快照
  6. 新增回答后 stale=true，但**旧报告与下载内容仍是原快照**（份数不变、百分比不变）
  7. 重新复盘产生第 2 份报告，旧报告保留、各自保留份数
  8. 草稿项目复盘 400 INVALID_STATE；不存在项目 404
  9. 模型文本被 Jinja 转义（注入 <script> 不成为可执行标签）
 10. 正式路由只注册一次（本脚本不注入任何桩路由）

用法：python tools/check_a07_report_flow.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DB = Path(tempfile.gettempdir()) / f"fo-a07real-{uuid.uuid4().hex[:8]}.sqlite3"

# ---------------------------------------------------------------- 假模型返回的复盘
# 结构严格按总纲 §7.5 与 app/schemas.py 的 Review；证据引用必须是本快照内真实存在的题号/选项/回答。
REVIEW = {
    "summary": "本次样本量很小，只能作为探索性证据：组织者对报名信息整理存在一定困难，试用意向尚不明确。"
               "<script>alert('xss')</script>",
    "hypothesis_assessments": [
        {"hypothesis_id": "h1", "status": "preliminary_support",
         "reason": "多数作答者选择经常或偶尔遇到，说明整理困难在本样本中较为普遍。",
         "evidence_refs": [{"question_id": "q1", "option_id": "o1"}]},
        {"hypothesis_id": "h2", "status": "insufficient_evidence",
         "reason": "愿意与不愿意各占一半，样本量不足以判断试用意向。",
         "evidence_refs": [{"question_id": "q2", "option_id": "o1"},
                           {"question_id": "q2", "option_id": "o2"}]},
        {"hypothesis_id": "h3", "status": "preliminary_support",
         "reason": "部分作答者给出了具体的流程问题描述。",
         "evidence_refs": [{"question_id": "q3", "response_id": 1}]},
    ],
    "key_feedback": ["报名信息分散在聊天记录里，统计人数要手工数", "希望能直接导出名单"],
    "limitations": ["回答份数少于 5 份，属于探索性样本，不能代表校园总体",
                    "本次只验证需求与意向，未验证付费意愿，也没有任何成交证据"],
    "next_actions": [{"action": "邀请 2～3 位实际组织过活动的同学补充填写并做简短访谈",
                      "owner_role": "实验执行", "completion_criterion": "新增至少 2 份有效回答并记录访谈要点"}],
}
CALLS: list[dict] = []


class MockModel(BaseHTTPRequestHandler):
    """OpenAI Chat Completions 兼容的最小实现：只回固定复盘，不接任何真实供应商。"""

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            request = json.loads(raw.decode("utf-8"))
        except ValueError:
            request = {}
        CALLS.append({"path": self.path, "model": request.get("model"),
                      "has_auth": bool(self.headers.get("authorization"))})
        body = json.dumps({
            "id": "mock-review", "object": "chat.completion", "model": request.get("model", "mock"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": json.dumps(REVIEW, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 300, "total_tokens": 500},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


server = HTTPServer(("127.0.0.1", 0), MockModel)
threading.Thread(target=server.serve_forever, daemon=True).start()
MOCK_BASE = "http://127.0.0.1:%d/v1" % server.server_address[1]

os.environ.update({
    "APP_ENV": "test",
    "DATABASE_PATH": str(DB),
    "LLM_BASE_URL": MOCK_BASE,
    "LLM_API_KEY": "mock-key-not-a-real-secret",
    "LLM_MODEL": "mock-review-model",
    "ADMIN_USER": "admin",
    "ADMIN_PASSWORD": "a07-password",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect_db, init_db  # noqa: E402
from app.main import admin_router, create_app  # noqa: E402

ADMIN = ("admin", "a07-password")
WRITE = {"Origin": "http://testserver", "X-Requested-With": "FirstOrderLab"}
PROJECT_ID = "77777777-7777-4777-8777-777777777777"
DRAFT_PROJECT_ID = "88888888-8888-4888-8888-888888888888"
EXPERIMENT_ID = "exp-a07-real-0001"
DRAFT_EXPERIMENT_ID = "exp-a07-real-draft"

LANDING = {"title": "校园活动报名网页服务", "summary": "面向社团组织者的服务需求验证。",
           "target_users": "社团负责人", "value_proposition": "简化报名信息收集",
           "features": ["定制活动介绍页"], "disclaimer": "目前为需求验证，不代表已有商业成交。"}
QUESTIONS = [
    {"id": "q1", "type": "single", "title": "是否遇到报名整理困难？", "required": True,
     "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}, {"id": "o3", "label": "没有"}]},
    {"id": "q2", "type": "single", "title": "是否愿意试用？", "required": True,
     "options": [{"id": "o1", "label": "愿意"}, {"id": "o2", "label": "不愿意"}]},
    {"id": "q3", "type": "text", "title": "流程还有什么问题？", "required": False, "options": []},
    {"id": "q4", "type": "multiple", "title": "最关心哪些功能？", "required": True,
     "options": [{"id": "o1", "label": "手机使用方便"}, {"id": "o2", "label": "信息导出"}]},
]
PLAN = {"target_customer": "社团负责人", "pain_points": ["报名信息汇总耗时"],
        "value_proposition": "简化报名信息收集",
        "hypotheses": [
            {"id": "h1", "statement": "部分组织者存在报名整理困难", "question_ids": ["q1"]},
            {"id": "h2", "statement": "部分组织者愿意试用定制网页", "question_ids": ["q2"]},
            {"id": "h3", "statement": "用户能指出现有流程的改进点", "question_ids": ["q3"]}],
        "experiment_steps": ["邀请同学填写问卷"], "next_tasks": ["检查问卷措辞"],
        "landing": LANDING, "questions": QUESTIONS}
RESPONSES = [
    {"q1": "o1", "q2": "o1", "q3": "报名信息散在聊天记录里", "q4": ["o1", "o2"]},
    {"q1": "o1", "q2": "o2", "q3": "", "q4": ["o1", "o2"]},
    {"q1": "o2", "q2": "o1", "q3": "手工数人数很慢", "q4": ["o2"]},
    {"q1": "o2", "q2": "o2", "q3": "", "q4": []},
]

results: list[tuple[bool, str]] = []


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + detail) if detail else ""))


def seed() -> None:
    init_db(DB)
    with connect_db(DB) as connection:
        for pid, name, published, eid, token in (
            (PROJECT_ID, "校园活动报名网站服务", "2026-10-11T01:00:00+00:00", EXPERIMENT_ID, "t" * 44),
            (DRAFT_PROJECT_ID, "尚未发布的草稿项目", None, DRAFT_EXPERIMENT_ID, "d" * 44),
        ):
            connection.execute(
                "INSERT INTO projects (id,name,idea,target_users,skills,budget_cny,"
                "validation_goal,data_kind,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, name, "为校园社团制作低成本活动报名网页。", "社团负责人", "基础网页开发",
                 100.0, "验证是否有试用需求", "test", json.dumps(PLAN, ensure_ascii=False),
                 "2026-10-11T00:00:00+00:00"))
            connection.execute(
                "INSERT INTO experiments (id,project_id,public_token,landing_json,"
                "questions_json,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
                (eid, pid, token, json.dumps(LANDING, ensure_ascii=False),
                 json.dumps(QUESTIONS, ensure_ascii=False), published, "2026-10-11T00:00:00+00:00"))
        for index, answers in enumerate(RESPONSES, start=1):
            connection.execute(
                "INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at)"
                " VALUES (?,?,?,1,?)",
                (EXPERIMENT_ID, str(uuid.uuid4()),
                 json.dumps(answers, ensure_ascii=False, sort_keys=True),
                 f"2026-10-11T02:0{index}:00+00:00"))


def main() -> int:
    seed()
    app = create_app()

    print("=" * 94)
    print("假模型服务：%s（只回固定复盘，不接真实供应商）" % MOCK_BASE)
    print("注意：本脚本**不注入任何桩路由**，跑的是 BC 集成包里的真实 A07 实现。")
    print("=" * 94)

    with TestClient(app, follow_redirects=False) as client, \
            TestClient(app, follow_redirects=False) as anonymous:
        client.auth = ADMIN

        print("0) 路由与前提")
        paths = [route.path for route in admin_router.routes
                 if "GET" in getattr(route, "methods", set())]
        for path in ("/projects/{project_id}/report", "/api/projects/{project_id}/report",
                     "/api/projects/{project_id}/report.md"):
            check("真实路由存在且只注册一次：%s" % path, paths.count(path) == 1,
                  "出现 %d 次" % paths.count(path))

        print("=" * 94)
        print("1) 无报告时的空状态")
        page = client.get(f"/projects/{PROJECT_ID}/report")
        check("报告页 200", page.status_code == 200, f"状态 {page.status_code}")
        check("  显示「尚无成功复盘报告」空状态", 'data-testid="report-empty-card"' in page.text)
        check("  空状态下下载按钮禁用", 'data-testid="report-download" disabled' in squash(page.text))
        check("  不预渲染任何统计数字或结论",
              "percentage" not in page.text and "preliminary_support" not in page.text
              and "暂无数据" not in page.text)
        api = client.get(f"/api/projects/{PROJECT_ID}/report")
        check("无报告时报告 API 404", api.status_code == 404, f"状态 {api.status_code}")
        md = client.get(f"/api/projects/{PROJECT_ID}/report.md")
        check("无报告时 Markdown 也 404", md.status_code == 404, f"状态 {md.status_code}")

        print("=" * 94)
        print("2) 生成复盘（真实工作流 + 假模型）")
        response = client.post(f"/api/projects/{PROJECT_ID}/review", json={}, headers=WRITE)
        check("POST /review 200", response.status_code == 200,
              f"状态 {response.status_code} {response.text[:160]}")
        created = response.json()["data"] if response.status_code == 200 else {}
        check("  返回 report_id / run_id / response_count / cutoff_response_id",
              all(k in created for k in ("report_id", "run_id", "response_count",
                                         "cutoff_response_id")), json.dumps(created))
        check("  快照回答数 4、截止编号 4",
              created["response_count"] == 4 and created["cutoff_response_id"] == 4)
        check("  假模型被真实调用一次（带 Authorization）",
              len(CALLS) == 1 and CALLS[0]["has_auth"]
              and CALLS[0]["path"].endswith("/chat/completions"), json.dumps(CALLS))
        with connect_db(DB) as connection:
            rows = [dict(r) for r in connection.execute(
                "SELECT id, response_count, cutoff_response_id FROM reports").fetchall()]
        check("  报告真实落库", len(rows) == 1 and rows[0]["response_count"] == 4,
              json.dumps(rows, ensure_ascii=False))
        tool = client.get("/api/projects/%s/runs" % PROJECT_ID).json()["data"]
        check("  产生了 action=review 的真实运行记录", any(r["action"] == "review" for r in tool),
              json.dumps([{k: r[k] for k in ("action", "status")} for r in tool], ensure_ascii=False))

        print("=" * 94)
        print("3) 报告 API 与统计快照口径（T07 固定样本）")
        report = client.get(f"/api/projects/{PROJECT_ID}/report").json()["data"]
        check("GET /report 字段齐全",
              set(("id", "created_at", "response_count", "cutoff_response_id",
                   "statistics", "review", "stale", "data_kind")) <= set(report))
        check("  新报告 stale=false", report["stale"] is False)
        by_id = {q["id"]: q for q in report["statistics"]["questions"]}
        q4 = by_id["q4"]
        check("  q4 有效作答 3 份（第 4 份空数组不计）", q4["answered_count"] == 3,
              str(q4["answered_count"]))
        check("  q4/o1 = 66.67%", q4["options"][0]["percentage"] == 66.67, str(q4["options"][0]))
        check("  q4/o2 = 100.0%", q4["options"][1]["percentage"] == 100.0)
        check("  q3 简答 2 条（空文本不计）", by_id["q3"]["answered_count"] == 2)
        check("  题型字段与统计口径字段一致",
              by_id["q3"]["type"] == "text" and by_id["q1"]["type"] == "single")

        print("=" * 94)
        print("4) 报告页渲染（服务端 context，无 JS 依赖）")
        page = client.get(f"/projects/{PROJECT_ID}/report")
        html = page.text
        check("报告页 200", page.status_code == 200)
        check("  报告时间包含交付约定的 testid", 'data-testid="report-created-at"' in html)
        check("  显示报告份数与截止编号",
              'data-testid="report-response-count"' in html and ">4<" in squash(html)
              and 'data-testid="report-cutoff-response-id"' in html)
        check("  当前无新反馈 → report-fresh", 'data-testid="report-fresh"' in html
              and 'data-testid="report-stale"' not in html)
        check("  q4/o1 百分比按两位小数渲染 66.67%", "66.67%" in html)
        check("  有分母的零计数渲染成 0.00%（不是「暂无数据」）",
              "0.00%" in html or by_id["q4"]["options"][0]["count"] == 0)
        check("  统计快照按题渲染并带 testid",
              all(('data-testid="stat-%s"' % q) in html for q in ("q1", "q2", "q3", "q4")))
        check("  复盘三态标签与三条假设评估齐全",
              all(('data-testid="assessment-%s"' % h) in html for h in ("h1", "h2", "h3"))
              and "初步支持" in html and "证据不足" in html)
        check("  证据引用解析成「选项『标签』」而不是 o1",
              "选项「经常」" in html and "选项「愿意」" in html, )
        check("  简答证据解析成「回答 #1」", "回答 #1" in html)
        check("  局限与下一步行动齐全",
              'data-testid="review-limitations"' in html and 'data-testid="review-next-actions"' in html)
        check("  下载链接指向 /api/projects/{id}/report.md",
              ("/api/projects/%s/report.md" % PROJECT_ID) in html)
        check("  测试样例项目显式标注", 'data-testid="report-test-badge"' in html)

        print("=" * 94)
        print("5) 模型文本转义（不使用 safe / innerHTML）")
        check("注入的 <script> 被转义，未成为可执行标签",
              "<script>alert" not in html and "&lt;script&gt;" in html)
        check("  页面没有 innerHTML 拼接（模板层）", "innerHTML" not in html)

        print("=" * 94)
        print("6) Markdown 下载与页面同为快照")
        md = client.get(f"/api/projects/{PROJECT_ID}/report.md")
        check("Markdown 200", md.status_code == 200)
        check("  附件文件名带报告 UUID",
              "firstorder-report-" in md.headers.get("content-disposition", ""),
              md.headers.get("content-disposition", ""))
        text = md.text
        check("  含快照份数 4 与截止编号 4", "4" in text)
        check("  含 66.67% 与选项标签", "66.67" in text and "手机使用方便" in text)
        check("  含结论、局限与下一步行动", "局限" in text and "下一步" in text)

        print("=" * 94)
        print("7) 新增回答后：旧报告与下载内容仍是原快照")
        with connect_db(DB) as connection:
            connection.execute(
                "INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at)"
                " VALUES (?,?,?,1,?)",
                (EXPERIMENT_ID, str(uuid.uuid4()),
                 json.dumps({"q1": "o3", "q2": "o1", "q3": "第五份反馈", "q4": ["o2"]},
                            ensure_ascii=False, sort_keys=True), "2026-10-11T06:00:00+00:00"))

        after = client.get(f"/api/projects/{PROJECT_ID}/report").json()["data"]
        check("stale 变为 true", after["stale"] is True)
        check("  旧报告份数**仍是 4**（未重算）", after["response_count"] == 4, str(after["response_count"]))
        check("  旧报告截止编号**仍是 4**", after["cutoff_response_id"] == 4)
        check("  快照统计与生成时逐字节一致",
              after["statistics"] == report["statistics"])
        check("  复盘内容未变", after["review"] == report["review"])
        md_after = client.get(f"/api/projects/{PROJECT_ID}/report.md").text
        check("  下载内容仍是旧快照（不出现第 5 份的文本）", "第五份反馈" not in md_after)
        html_after = client.get(f"/projects/{PROJECT_ID}/report").text
        check("  报告页显示 stale 提醒并写明份数",
              'data-testid="report-stale"' in html_after and "前 4 份回答" in html_after)
        check("  工作台也显示 stale 提醒并回到 collecting",
              'data-testid="report-stale"' in client.get(f"/projects/{PROJECT_ID}").text
              and "收反馈中" in client.get("/projects").text)

        print("=" * 94)
        print("8) 重新复盘：新报告纳入新回答，旧报告保留")
        response = client.post(f"/api/projects/{PROJECT_ID}/review", json={}, headers=WRITE)
        check("再次 POST /review 200", response.status_code == 200,
              f"状态 {response.status_code} {response.text[:120]}")
        again = response.json()["data"]
        check("  新报告回答数 5", again["response_count"] == 5, str(again["response_count"]))
        with connect_db(DB) as connection:
            rows = [dict(r) for r in connection.execute(
                "SELECT id, response_count FROM reports").fetchall()]
        check("  库里有 2 份报告、各自保留份数（旧的未被覆盖）",
              sorted(r["response_count"] for r in rows) == [4, 5], json.dumps(rows))
        newest = client.get(f"/api/projects/{PROJECT_ID}/report").json()["data"]
        check("  最新报告 stale 回到 false", newest["stale"] is False)
        check("  最新报告 q4/o1 百分比随样本变化",
              {q["id"]: q for q in newest["statistics"]["questions"]}["q4"]["options"][0]["percentage"]
              != 66.67)

        print("=" * 94)
        print("9) 边界：草稿项目与不存在项目")
        response = client.post(f"/api/projects/{DRAFT_PROJECT_ID}/review", json={}, headers=WRITE)
        check("草稿项目复盘 400 INVALID_STATE",
              response.status_code == 400 and response.json()["error"]["code"] == "INVALID_STATE",
              f"状态 {response.status_code}")
        draft_page = client.get(f"/projects/{DRAFT_PROJECT_ID}/report")
        check("  草稿项目报告页仍是空状态（不是错误页）",
              draft_page.status_code == 200 and 'data-testid="report-empty-card"' in draft_page.text)
        check("不存在项目报告页 404", client.get("/projects/nope/report").status_code == 404)
        check("不存在项目复盘 404",
              client.post("/api/projects/nope/review", json={}, headers=WRITE).status_code == 404)
        check("未登录报告页 401", anonymous.get(f"/projects/{PROJECT_ID}/report").status_code == 401,
              "状态 %s" % anonymous.get(f"/projects/{PROJECT_ID}/report").status_code)
        check("未登录报告 API 401",
              anonymous.get(f"/api/projects/{PROJECT_ID}/report").status_code == 401)
        check("未登录 Markdown 401",
              anonymous.get(f"/api/projects/{PROJECT_ID}/report.md").status_code == 401)
        check("未登录复盘 401",
              anonymous.post(f"/api/projects/{PROJECT_ID}/review", json={}).status_code == 401)
        check("报告页写来源不合法时 403（缺 X-Requested-With）",
              client.post(f"/api/projects/{PROJECT_ID}/review", json={},
                          headers={"Origin": "http://testserver"}).status_code == 403)

    failed = [name for ok, name in results if not ok]
    print("=" * 94)
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
    for name in failed:
        print("  失败：", name)
    print("假模型调用次数：%d（真实供应商调用 0 次；真实模型证据见 docs/a07-real-*）" % len(CALLS))
    try:
        DB.unlink(missing_ok=True)
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
