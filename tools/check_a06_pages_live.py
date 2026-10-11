"""A06 真后端集成验证（B 侧）。

验证三件事：
  1) 三个管理页面路由（A 已接线）能渲染 B 的模板，context 与 A 的交接文档一致
  2) A06 统计接口的固定样本口径（T07）：3 份作答、某选项被 2 份选择 → 66.67%，零分母为 null
  3) 原始回答接口不泄露提交编号；草稿统计返回 400 INVALID_STATE

用法：python tools/check_pages_live.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DB = Path(tempfile.gettempdir()) / f"fo-a06-{uuid.uuid4().hex[:8]}.sqlite3"
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_PATH": str(DB),
    "LLM_BASE_URL": "https://llm.example.invalid/v1",
    "LLM_API_KEY": "test-key-not-real",
    "LLM_MODEL": "test-model",
    "ADMIN_USER": "admin",
    "ADMIN_PASSWORD": "a06-password",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect_db, init_db  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN = ("admin", "a06-password")
PROJECT_ID = "33333333-3333-4333-8333-333333333333"
DRAFT_PROJECT_ID = "44444444-4444-4444-8444-444444444444"
EXPERIMENT_ID = "exp-a06-0001"
DRAFT_EXPERIMENT_ID = "exp-a06-draft"
TOKEN = "t" * 44

LANDING = {
    "title": "校园活动报名网页服务",
    "summary": "面向社团组织者的服务需求验证。",
    "target_users": "社团负责人、活动组织者",
    "value_proposition": "简化报名信息收集与整理",
    "features": ["定制活动介绍页", "报名信息统一收集"],
    "disclaimer": "目前为需求验证，不代表已有商业成交或完整服务上线。",
}
QUESTIONS = [
    {"id": "q1", "type": "single", "title": "是否遇到报名整理困难？", "required": True,
     "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}]},
    {"id": "q2", "type": "single", "title": "是否愿意试用？", "required": True,
     "options": [{"id": "o1", "label": "愿意"}, {"id": "o2", "label": "不愿意"}]},
    {"id": "q3", "type": "text", "title": "流程还有什么问题？", "required": False, "options": []},
    {"id": "q4", "type": "multiple", "title": "最关心哪些功能？", "required": True,
     "options": [{"id": "o1", "label": "手机使用方便"}, {"id": "o2", "label": "信息导出"},
                 {"id": "o3", "label": "操作简单"}, {"id": "o4", "label": "暂时无需求"}]},
]
PLAN = {
    "target_customer": "有活动组织经验的社团负责人",
    "pain_points": ["报名信息汇总耗时"],
    "value_proposition": "提供简单、可维护的活动报名网页",
    "hypotheses": [
        {"id": "h1", "statement": "部分组织者存在报名整理困难", "question_ids": ["q1"]},
        {"id": "h2", "statement": "部分组织者愿意试用定制网页", "question_ids": ["q2"]},
        {"id": "h3", "statement": "用户能指出现有流程的改进点", "question_ids": ["q3"]},
    ],
    "experiment_steps": ["邀请有活动组织经验的同学填写问卷"],
    "next_tasks": ["人工检查问卷是否诱导回答"],
    "landing": LANDING, "questions": QUESTIONS,
}
# T07 固定样本：共 4 份回答；q4 只有 3 份有效作答（r4 的空数组不算作答）
RESPONSES = [
    {"q1": "o1", "q2": "o1", "q3": "第一份反馈", "q4": ["o1", "o2"]},
    {"q1": "o1", "q2": "o2", "q3": "", "q4": ["o1", "o2"]},
    {"q1": "o2", "q2": "o1", "q3": "   ", "q4": ["o2"]},
    {"q1": "o2", "q2": "o2", "q3": "第四份反馈", "q4": []},
]

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + detail) if detail else ""))


def seed() -> None:
    init_db(DB)

    def insert_project(pid, name, kind, status_published, eid, token):
        with connect_db(DB) as connection:
            connection.execute(
                "INSERT INTO projects (id,name,idea,target_users,skills,budget_cny,"
                "validation_goal,data_kind,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (pid, name, "为校园社团制作低成本活动报名网页。", "社团负责人", "基础网页开发",
                 100.0, "验证是否有试用需求", kind, json.dumps(PLAN, ensure_ascii=False),
                 "2026-10-10T00:00:00+00:00"))
            connection.execute(
                "INSERT INTO experiments (id,project_id,public_token,landing_json,"
                "questions_json,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
                (eid, pid, token, json.dumps(LANDING, ensure_ascii=False),
                 json.dumps(QUESTIONS, ensure_ascii=False), status_published,
                 "2026-10-10T00:00:00+00:00"))

    insert_project(PROJECT_ID, "校园活动报名网站服务", "test",
                   "2026-10-11T01:00:00+00:00", EXPERIMENT_ID, TOKEN)
    insert_project(DRAFT_PROJECT_ID, "尚未发布的草稿项目", "test", None, DRAFT_EXPERIMENT_ID, "d" * 44)

    with connect_db(DB) as connection:
        for index, answers in enumerate(RESPONSES, start=1):
            connection.execute(
                "INSERT INTO responses (experiment_id, client_submission_id, answers_json, consent, created_at)"
                " VALUES (?,?,?,1,?)",
                (EXPERIMENT_ID, str(uuid.uuid4()), json.dumps(answers, ensure_ascii=False, sort_keys=True),
                 f"2026-10-11T02:0{index}:00+00:00"))


def main() -> int:
    seed()
    app = create_app()

    with TestClient(app, follow_redirects=False) as client, \
            TestClient(app, follow_redirects=False) as anonymous:
        client.auth = ADMIN

        print("=" * 92)
        print("1) 三个管理页面路由（A 已接线）与 context")
        check("未登录 GET / 401", anonymous.get("/").status_code == 401)
        check("未登录 GET /projects/new 401", anonymous.get("/projects/new").status_code == 401)
        check("未登录 GET /projects/{id} 401",
              anonymous.get(f"/projects/{PROJECT_ID}").status_code == 401)

        response = client.get("/")
        check("GET / 200", response.status_code == 200, f"状态 {response.status_code}")
        if response.status_code == 200:
            html = response.text
            check("  渲染 B 的 projects.html", 'data-testid="project-list-body"' in html)
            check("  服务端已渲染出项目行（无需 JS 也能读）",
                  "project-row-" in html and "校园活动报名网站服务" in html)
            check("  两个项目都在列表里", html.count("project-row-") == 2)

        response = client.get("/projects/new")
        check("GET /projects/new 200", response.status_code == 200)
        if response.status_code == 200:
            check("  渲染 B 的 project_new.html 且字段齐全",
                  'data-testid="project-form"' in response.text
                  and all(('name="%s"' % f) in response.text for f in
                          ("name", "idea", "target_users", "skills", "budget_cny",
                           "validation_goal", "data_kind")))

        response = client.get(f"/projects/{PROJECT_ID}")
        check("GET /projects/{id} 200", response.status_code == 200)
        if response.status_code == 200:
            html = response.text
            check("  渲染 B 的 workspace.html", 'data-testid="plan-generate"' in html)
            check("  项目信息服务端渲染", "为校园社团制作低成本活动报名网页" in html)
            check("  方案 3 条假设都在", all(("%s　" % h) in html for h in ("h1", "h2", "h3")))
            check("  题目表 4 题", html.count("question-q") == 4)
            check("  已发布态显示分享链接", 'data-testid="survey-url"' in html)
            check("  已发布态无发布按钮", 'data-testid="experiment-publish"' not in html)
            check("  统计区进入取数分支", "正在读取统计" in html)
            check("  内联配置带真实 experimentId 与 published: true",
                  EXPERIMENT_ID in html and "published: true" in html)
            check("  页面不含任何假统计数字", "%" not in html.split("statistics-body")[0][-2000:])

        check("不存在项目 404", client.get("/projects/missing").status_code == 404)

        print("=" * 92)
        print("2) A06 统计接口口径（T07 固定样本）")
        stats = client.get(f"/api/experiments/{EXPERIMENT_ID}/statistics")
        check("GET statistics 200", stats.status_code == 200)
        data = stats.json()["data"] if stats.status_code == 200 else {}
        check("  response_count = 4", data.get("response_count") == 4, str(data.get("response_count")))
        check("  cutoff_response_id = 4", data.get("cutoff_response_id") == 4,
              str(data.get("cutoff_response_id")))
        by_id = {q["id"]: q for q in data.get("questions", [])}
        check("  返回全部 4 题", set(by_id) == {"q1", "q2", "q3", "q4"})
        q4 = by_id.get("q4", {})
        check("  q4 有效作答 3 份（r4 空数组不计）", q4.get("answered_count") == 3,
              str(q4.get("answered_count")))
        options = {o["id"]: o for o in q4.get("options", [])}
        check("  q4/o1 计数 2 → 66.67%", options.get("o1", {}).get("percentage") == 66.67,
              str(options.get("o1")))
        check("  q4/o2 计数 3 → 100.0%", options.get("o2", {}).get("percentage") == 100.0,
              str(options.get("o2")))
        check("  q4/o3 计数 0 → 0.0%（有分母，不是 null）",
              options.get("o3", {}).get("percentage") == 0.0, str(options.get("o3")))
        check("  q4 保留全部 4 个选项", len(q4.get("options", [])) == 4)
        q1 = by_id.get("q1", {})
        check("  q1 有效作答 4 份（单选分母是全部回答）", q1.get("answered_count") == 4)
        q3 = by_id.get("q3", {})
        check("  q3 简答有效 2 条（空文本与纯空格不计）", q3.get("answered_count") == 2,
              str(q3.get("answered_count")))
        check("  q3 文本按 response_id 返回",
              all(set(item) == {"response_id", "text"} for item in q3.get("texts", [])))

        zero = client.get(f"/api/experiments/{DRAFT_EXPERIMENT_ID}/statistics")
        check("  草稿统计 400 INVALID_STATE",
              zero.status_code == 400 and zero.json()["error"]["code"] == "INVALID_STATE",
              f"状态 {zero.status_code}")
        check("  不存在的实验统计 404",
              client.get("/api/experiments/nope/statistics").status_code == 404)

        print("=" * 92)
        print("3) 原始回答接口")
        raw = client.get(f"/api/experiments/{EXPERIMENT_ID}/responses")
        check("GET responses 200", raw.status_code == 200)
        rows = raw.json()["data"] if raw.status_code == 200 else []
        check("  返回 4 条，按 id 升序", [r["id"] for r in rows] == [1, 2, 3, 4],
              str([r["id"] for r in rows]))
        check("  字段恰好是 id/answers/created_at",
              all(set(r) == {"id", "answers", "created_at"} for r in rows))
        check("  不泄露 client_submission_id",
              "client_submission_id" not in json.dumps(rows))
        check("  草稿实验无回答时返回空数组",
              client.get(f"/api/experiments/{DRAFT_EXPERIMENT_ID}/responses").json()["data"] == [])

        print("=" * 92)
        print("4) C 的页面与公开页未受影响")
        draft_html = client.get(f"/experiments/{DRAFT_EXPERIMENT_ID}/edit").text
        check("草稿编辑页（未发布）仍含 experiment-save", 'data-testid="experiment-save"' in draft_html)
        locked_html = client.get(f"/experiments/{EXPERIMENT_ID}/edit").text
        check("已发布实验的编辑页为只读（无保存按钮、有锁定提示）",
              'data-testid="experiment-save"' not in locked_html
              and 'data-testid="experiment-locked"' in locked_html)
        check("公开问卷 200 且含 data-questions",
              "data-questions=" in client.get(f"/e/{TOKEN}/survey").text)
        check("/health 200", client.get("/health").status_code == 200)

    failed = [name for ok, name in results if not ok]
    print("=" * 92)
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
    for name in failed:
        print("  失败：", name)
    try:
        DB.unlink(missing_ok=True)
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
