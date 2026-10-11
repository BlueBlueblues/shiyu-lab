"""B 页面验证：使用隔离数据库和现有正式路由，不注册或替换路由，不调用模型。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DB = Path(tempfile.gettempdir()) / f"fo-pages-{uuid.uuid4().hex[:8]}.sqlite3"
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_PATH": str(DB),
    "LLM_BASE_URL": "https://llm.example.invalid/v1",
    "LLM_API_KEY": "test-key-not-real",
    "LLM_MODEL": "test-model",
    "ADMIN_USER": "admin",
    "ADMIN_PASSWORD": "pages-password",
    "PUBLIC_BASE_URL": "http://testserver",
})

from unittest.mock import patch  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


from app.db import connect_db, init_db  # noqa: E402
from app.main import admin_router, create_app  # noqa: E402

ADMIN = ("admin", "pages-password")
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
EXPERIMENT_ID = "exp-pages-0001"


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
     "options": [{"id": "o1", "label": "手机方便"}, {"id": "o2", "label": "可导出"}]},
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
    "landing": LANDING,
    "questions": QUESTIONS,
}

results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((ok, name))
    print(("PASS  " if ok else "FAIL  ") + name + (("  — " + detail) if detail else ""))


def seed() -> None:
    init_db(DB)
    with connect_db(DB) as connection:
        connection.execute(
            "INSERT INTO projects (id,name,idea,target_users,skills,budget_cny,"
            "validation_goal,data_kind,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (PROJECT_ID, "校园活动报名网站服务", "为校园社团制作低成本活动报名网页。",
             "社团负责人", "基础网页开发", 100.0, "验证是否有试用需求", "test",
             json.dumps(PLAN, ensure_ascii=False), "2026-10-10T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO experiments (id,project_id,public_token,landing_json,"
            "questions_json,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
            (EXPERIMENT_ID, PROJECT_ID, "p" * 44,
             json.dumps(LANDING, ensure_ascii=False), json.dumps(QUESTIONS, ensure_ascii=False),
             None, "2026-10-10T00:00:00+00:00"),
        )


def run_checks() -> int:
    seed()
    app = create_app()

    with patch("httpx.post", side_effect=AssertionError("禁止调用真实模型")), \
            TestClient(app, follow_redirects=False) as client, \
            TestClient(app, follow_redirects=False) as anonymous:
        client.auth = ADMIN

        print("=" * 92)
        print("1) 路由与鉴权")
        check("未登录 /projects/new 401", anonymous.get("/projects/new").status_code == 401,
              "状态 %s" % anonymous.get("/projects/new").status_code)
        response = client.get("/projects/new")
        check("已登录 /projects/new 200", response.status_code == 200,
              "状态 %s %s" % (response.status_code, response.text[:160]))
        if response.status_code == 200:
            html = response.text
            check("  含 project-form / project-create",
                  'data-testid="project-form"' in html and 'data-testid="project-create"' in html)
            check("  含总纲 §7.1 全部字段",
                  all(('name="%s"' % f) in html for f in
                      ("name", "idea", "target_users", "skills", "budget_cny",
                       "validation_goal", "data_kind")))
            check("  引用静态资源 app.js / app.css",
                  "/static/app.js" in html and "/static/app.css" in html)

        response = client.get("/projects/%s" % PROJECT_ID)
        check("已登录 /projects/{id} 200", response.status_code == 200,
              "状态 %s %s" % (response.status_code, response.text[:160]))
        if response.status_code == 200:
            html = response.text
            for marker in ('data-testid="plan-generate"', 'data-testid="run-log"',
                           'data-testid="experiment-publish"', 'data-testid="links-empty"', 'data-testid="project-status"',
                           'data-testid="project-meta"'):
                check("  含 %s" % marker, marker in html)
            # 此处实验尚未发布：B 接通 A07 后，复盘按钮按"仅发布后可复盘"的规则禁用；
            # 还没有报告时查看/下载也保持禁用占位。属性之间可能换行，先压空白再比较。
            flat = " ".join(html.split())
            check("  未发布时复盘入口禁用、无报告时查看/下载为禁用占位",
                  all(('data-testid="%s" disabled' % t) in flat
                      for t in ("review-generate", "report-content", "report-download")))

        check("不存在的项目 404", client.get("/projects/no-such-id").status_code == 404)

        print("=" * 92)
        print("2) 页面数据面：正式 context 与前端 API 契约")
        detail = client.get("/api/projects/%s" % PROJECT_ID).json()["data"]
        workspace = client.get("/projects/%s" % PROJECT_ID)
        check("正式工作台模板与 context 匹配详情 API", workspace.template.name == "workspace.html"
              and all(workspace.context[key] == value for key, value in detail.items()))
        check("GET /api/projects/{id} 返回 project/plan/experiment/running_run_id",
              set(("project", "plan", "experiment", "latest_report_meta", "running_run_id"))
              <= set(detail.keys()))
        check("  plan 含 3 条假设", len(detail["plan"]["hypotheses"]) == 3)
        check("  experiment 含 4 题", len(detail["experiment"]["questions"]) == 4)
        check("  未发布时 service_url / survey_url 均为 null",
              detail["experiment"]["service_url"] is None and detail["experiment"]["survey_url"] is None)
        runs = client.get("/api/projects/%s/runs" % PROJECT_ID).json()["data"]
        check("GET /runs 返回数组（当前为空）", runs == [])

        headers = {"Origin": "http://testserver", "X-Requested-With": "FirstOrderLab"}
        project = {key: detail["project"][key] for key in
                   ("name", "idea", "target_users", "skills", "budget_cny", "validation_goal", "data_kind")}
        created = client.post("/api/projects", json=project, headers=headers)
        check("创建接口返回 project_id / draft 契约", created.status_code == 201
              and set(created.json().get("data", {})) == {"project_id", "status"}
              and created.json()["data"]["status"] == "draft")
        published = client.post(f"/api/experiments/{EXPERIMENT_ID}/publish", json={}, headers=headers)
        check("人工发布返回实际分享链接", published.status_code == 200
              and published.json()["data"]["survey_url"] == "http://testserver/e/" + "p" * 44 + "/survey")
        check("发布后正式公开问卷 200", anonymous.get("/e/" + "p" * 44 + "/survey").status_code == 200)
        # B 接通 A07 后：已发布且无运行中任务 → 复盘按钮可用（还没有报告，故为「生成复盘」）
        published_flat = " ".join(client.get(f"/projects/%s" % PROJECT_ID).text.split())
        check("发布后复盘按钮可用（未发布禁用的规则同时被验证）",
              'data-testid="review-generate"' in published_flat
              and 'data-testid="review-generate" disabled' not in published_flat
              and "生成复盘" in published_flat)
        check("报告页路由可用且显示尚无报告",
              client.get(f"/projects/{PROJECT_ID}/report").status_code == 200
              and 'data-testid="report-empty-card"' in client.get(f"/projects/{PROJECT_ID}/report").text)
        check("无报告时报告 API 404（不返回空白成功结果）",
              client.get(f"/api/projects/{PROJECT_ID}/report").status_code == 404)
        statistics = client.get(f"/api/experiments/{EXPERIMENT_ID}/statistics")
        check("A06 已存在且隔离实验真实零反馈", statistics.status_code == 200
              and statistics.json()["data"]["response_count"] == 0)
        check("A06 原始反馈接口已存在", client.get(f"/api/experiments/{EXPERIMENT_ID}/responses").json() == {"data": []})

        print("=" * 92)
        print("3) 公开页面仍不受影响")
        check("发布后公开服务页 200", anonymous.get("/e/%s" % ("p" * 44)).status_code == 200)
        check("/health 200", client.get("/health").status_code == 200)
        check("正式公开首页无需认证 200", anonymous.get("/").status_code == 200)
        response = client.get("/projects")
        check("正式项目列表 projects.html 200", response.status_code == 200
              and response.template.name == "projects.html"
              and 'data-testid="project-row-%s"' % PROJECT_ID in response.text)
        for path in ("/projects", "/projects/new", "/projects/{project_id}"):
            check("正式路由只注册一次 " + path,
                  sum(route.path == path and "GET" in getattr(route, "methods", set())
                      for route in admin_router.routes) == 1)
            check("正式路由未登录 401 " + path,
                  anonymous.get(path.replace("{project_id}", PROJECT_ID)).status_code == 401)

    failed = [name for ok, name in results if not ok]
    print("=" * 92)
    print("共 %d 项，通过 %d 项，失败 %d 项" % (len(results), len(results) - len(failed), len(failed)))
    for name in failed:
        print("  失败：", name)
    return 1 if failed else 0


def main() -> int:
    try:
        return run_checks()
    finally:
        DB.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
