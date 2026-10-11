"""三个新页面的真后端集成验证（B 侧）。

目的：证明"模板 + app.js + app.css"在**真实 FastAPI 应用**上可用，而不是只在裸 Jinja 里能渲染。

做法：import app.main 的 admin_router，在**运行时**把三个页面路由挂上去（等价于 A 要写的那几行），
      再用 TestClient 访问。不修改仓库里的任何后端文件；跑完即退出。

同时给出 A 需要补的最小路由代码（见本文件 register_reference_routes 的注释）。

用法：python tools/check_pages_live.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

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

from fastapi import Depends, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import repositories  # noqa: E402
from app.db import connect_db, get_db, init_db  # noqa: E402
from app.main import admin_router, create_app  # noqa: E402

ADMIN = ("admin", "pages-password")
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
EXPERIMENT_ID = "exp-pages-0001"

# 必须放在模块级：本文件顶部有 from __future__ import annotations，
# 若把 Annotated 别名定义在函数内部，FastAPI 解析不到它，会把 connection 当成查询参数（422）。
Database = Annotated[sqlite3.Connection, Depends(get_db)]

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


def register_reference_routes() -> None:
    """=== 这就是请 A 在 app/main.py 里补的三行路由（最小实现）===

        @admin_router.get("/", response_class=HTMLResponse)
        def projects_page(request: Request):
            with connect_db(request.app.state.settings.database_path) as connection:
                rows = repositories.list_projects(connection)
            return request.app.state.templates.TemplateResponse(
                request=request, name="projects.html", context={"projects": rows})

        @admin_router.get("/projects/new")
        def project_new_page(request: Request):
            return request.app.state.templates.TemplateResponse(
                request=request, name="project_new.html", context={})

        @admin_router.get("/projects/{project_id}")
        def workspace_page(project_id: str, request: Request, connection: Database):
            detail = repositories.get_project(connection, project_id,
                                              request.app.state.settings.public_base_url)
            if detail is None:
                raise HTTPException(status_code=404, detail="项目不存在。")
            return request.app.state.templates.TemplateResponse(
                request=request, name="workspace.html", context={})

    注意：`/` 目前被 A 的临时纯文本路由占用，必须**替换**而不是新增。
    本脚本只挂 /projects/new 与 /projects/{id}（`/` 无法在不改 A 代码的情况下测试）。
    """
    @admin_router.get("/projects/new")
    def _project_new_page(request: Request):
        return request.app.state.templates.TemplateResponse(
            request=request, name="project_new.html", context={})

    @admin_router.get("/projects/{project_id}")
    def _workspace_page(project_id: str, request: Request, connection: Database):
        detail = repositories.get_project(connection, project_id,
                                         request.app.state.settings.public_base_url)
        if detail is None:
            raise HTTPException(status_code=404, detail="项目不存在。")
        return request.app.state.templates.TemplateResponse(
            request=request, name="workspace.html", context={})


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


def main() -> int:
    seed()
    register_reference_routes()
    app = create_app()

    with TestClient(app, follow_redirects=False) as client, \
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
                           'data-testid="experiment-publish"', 'data-testid="service-url"',
                           'data-testid="survey-url"', 'data-testid="project-status"',
                           'data-testid="project-meta"'):
                check("  含 %s" % marker, marker in html)
            check("  统计/复盘按钮明确禁用",
                  all(('data-testid="%s" disabled' % t) in html
                      for t in ("response-count", "review-generate", "report-content", "report-download")))

        check("不存在的项目 404", client.get("/projects/no-such-id").status_code == 404)

        print("=" * 92)
        print("2) 页面数据面：页面本身不带管理数据，由前端调 API 取")
        detail = client.get("/api/projects/%s" % PROJECT_ID).json()["data"]
        check("GET /api/projects/{id} 返回 project/plan/experiment/running_run_id",
              set(("project", "plan", "experiment", "latest_report_meta", "running_run_id"))
              <= set(detail.keys()))
        check("  plan 含 3 条假设", len(detail["plan"]["hypotheses"]) == 3)
        check("  experiment 含 4 题", len(detail["experiment"]["questions"]) == 4)
        check("  未发布时 service_url / survey_url 均为 null",
              detail["experiment"]["service_url"] is None and detail["experiment"]["survey_url"] is None)
        runs = client.get("/api/projects/%s/runs" % PROJECT_ID).json()["data"]
        check("GET /runs 返回数组（当前为空）", runs == [])

        print("=" * 92)
        print("3) 公开页面仍不受影响")
        check("/e/{token} 未发布 404", client.get("/e/%s" % ("p" * 44)).status_code == 404)
        check("/health 200", client.get("/health").status_code == 200)
        check("临时 / 仍是纯文本（待 A 替换成 projects.html）",
              client.get("/", headers={"Accept": "text/html"}).headers.get("content-type", "")
              .startswith("text/plain"),
              "content-type=" + str(client.get("/").headers.get("content-type")))

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
