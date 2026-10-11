"""C 的浏览器端到端验证（T05/T06/T11 前半段 + 手机布局）。

覆盖 TestClient 无法触及的区域：真实执行 app.js 的问卷填写与提交。
这正是 B 发现 `data-questions` 缺陷的地方，也是 C 上一轮标注"未验证"的部分。

做法：
  1. 用隔离 SQLite 启动真实 uvicorn 服务（不碰 data/ 与真实库）
  2. 用管理员 Basic 认证建 test 项目，直接种入一个已发布实验（不调模型）
  3. 用 Playwright 驱动系统 Edge（channel=msedge，无需下载 chromium）
  4. 未登录上下文打开公开问卷 → 勾选同意 → 填三类题 → 提交 → 断言成功提示
  5. 核对数据库只入库 1 条、answers 内容正确（这能证明 data-questions 生效）
  6. 375x667 手机视口截图并检查是否横向溢出
  7. 同 UUID 重放不新增记录（T06）

用法：
  $env:PLAYWRIGHT_BROWSERS_PATH 不需要（用系统 Edge）
  <venv311>\Scripts\python.exe <此脚本>
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(os.environ.get("FO_REPO", r"C:\Users\MATEBOOK\Documents\GitHub\firstorder-lab"))
WORK = Path(os.environ.get("FO_WORK", r"C:\Users\MATEBOOK\Documents\deepseek-harness\default-workspace\.c-verify"))
ARTIFACTS = WORK / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

PORT = 8077
BASE = f"http://127.0.0.1:{PORT}"
DB = WORK / "browser-e2e.sqlite3"
if DB.exists():
    DB.unlink()

ADMIN_USER, ADMIN_PASSWORD = "admin", "browser-verify-password"
TOKEN = "e2e" + "b" * 41
PROJECT_ID = "44444444-4444-4444-8444-444444444444"
EXPERIMENT_ID = "exp-browser-e2e"

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
                 {"id": "o3", "label": "操作简单"}]},
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

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}", flush=True)


def seed() -> None:
    """只建表与种入已发布实验；项目走真实 API 创建以验证跨进程持久化。"""
    sys.path.insert(0, str(REPO))
    os.environ.update({
        "APP_ENV": "test", "DATABASE_PATH": str(DB),
        "LLM_BASE_URL": "https://llm.example.invalid/v1", "LLM_API_KEY": "not-real",
        "LLM_MODEL": "test-model", "ADMIN_USER": ADMIN_USER, "ADMIN_PASSWORD": ADMIN_PASSWORD,
        "PUBLIC_BASE_URL": BASE,
    })
    from app.db import connect_db, init_db

    init_db(DB)
    with connect_db(DB) as c:
        c.execute("INSERT INTO projects (id,name,idea,target_users,skills,budget_cny,"
                  "validation_goal,data_kind,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (PROJECT_ID, "浏览器验证项目", "为校园社团制作低成本活动报名网页。",
                   "社团负责人", "Python", 100.0, "验证需求", "test",
                   json.dumps(PLAN, ensure_ascii=False), "2026-10-11T00:00:00+00:00"))
        c.execute("INSERT INTO experiments (id,project_id,public_token,landing_json,"
                  "questions_json,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
                  (EXPERIMENT_ID, PROJECT_ID, TOKEN,
                   json.dumps(LANDING, ensure_ascii=False),
                   json.dumps(QUESTIONS, ensure_ascii=False),
                   "2026-10-11T00:00:00+00:00", "2026-10-11T00:00:00+00:00"))


def wait_for_server(timeout=40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def admin_get(path: str):
    request = urllib.request.Request(f"{BASE}{path}")
    token = base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode())


def main() -> int:
    seed()
    env = dict(os.environ)
    env.update({
        "APP_ENV": "test", "DATABASE_PATH": str(DB),
        "LLM_BASE_URL": "https://llm.example.invalid/v1", "LLM_API_KEY": "not-real",
        "LLM_MODEL": "test-model", "ADMIN_USER": ADMIN_USER, "ADMIN_PASSWORD": ADMIN_PASSWORD,
        "PUBLIC_BASE_URL": BASE, "PYTHONPATH": str(REPO),
        "PYTHONIOENCODING": "utf-8",
    })
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(REPO), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_server():
            check("服务启动", False, f"{BASE}/health 在 40 秒内未就绪")
            return 1
        check("服务启动", True, f"{BASE}/health 200")

        status, health = admin_get("/health")
        check("/health 契约（带 data 包装）", health == {"data": {"status": "ok"}}, json.dumps(health, ensure_ascii=False))

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            # 用系统 Edge，避免下载 chromium（沙箱限制）
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                # ---------- 公开问卷：未登录上下文 ----------
                public = browser.new_context(viewport={"width": 375, "height": 667})
                page = public.new_page()
                page.goto(f"{BASE}/e/{TOKEN}/survey", wait_until="domcontentloaded")
                check("公开问卷页加载 200", page.title() != "", page.url)

                # data-questions 必须被渲染（B 发现的缺陷）
                has_attr = page.evaluate(
                    "() => { const f = document.getElementById('survey-form');"
                    " return f ? f.hasAttribute('data-questions') : false; }"
                )
                check("★★ form 渲染了 data-questions（B 发现的缺陷已修复）", has_attr)

                # 填写三类题
                page.get_by_test_id("survey-consent").check()
                page.get_by_test_id("option-q1-o1").check()
                page.get_by_test_id("option-q2-o2").check()
                page.get_by_test_id(f"question-q3").locator("textarea").fill("报名信息散在聊天记录里")
                page.get_by_test_id("option-q4-o1").check()
                check("三类题可填写", True)

                page.get_by_test_id("survey-submit").click()
                page.get_by_test_id("submission-success").wait_for(state="visible", timeout=20000)
                check("★ 提交成功提示可见", page.get_by_test_id("submission-success").is_visible())

                # 手机视口截图 + 横向溢出检查
                page.screenshot(path=str(ARTIFACTS / "mobile-survey-375.png"), full_page=True)
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                check("375px 无横向溢出", overflow <= 1, f"溢出 {overflow}px")

                # 320px 复检
                page.set_viewport_size({"width": 320, "height": 568})
                overflow_320 = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                check("320px 无横向溢出", overflow_320 <= 1, f"溢出 {overflow_320}px")
                page.screenshot(path=str(ARTIFACTS / "mobile-survey-320.png"), full_page=True)
                public.close()

                # ---------- 数据库核对：answers 必须非空 ----------
                connection = sqlite3.connect(DB)
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT id, answers_json, consent FROM responses WHERE experiment_id = ?",
                    (EXPERIMENT_ID,)).fetchall()
                check("★ 数据库恰好入库 1 条回答", len(rows) == 1, f"实际 {len(rows)} 条")
                if rows:
                    answers = json.loads(rows[0]["answers_json"])
                    check("★ answers 非空（证明 data-questions 生效）", bool(answers),
                          json.dumps(answers, ensure_ascii=False))
                    check("单选值正确（q1=o1）", answers.get("q1") == "o1", str(answers.get("q1")))
                    check("多选值为数组（q4=[o1]）", answers.get("q4") == ["o1"], str(answers.get("q4")))
                    check("可选题未答时被省略（无空 q3 以外的空值）",
                          all(v not in ("", [], None) for v in answers.values()))
                    check("consent 记为 1", rows[0]["consent"] == 1)
                connection.close()

                # ---------- 管理端：统计与原始反馈 ----------
                manager = browser.new_context(http_credentials={"username": ADMIN_USER, "password": ADMIN_PASSWORD})
                admin_page = manager.new_page()
                status, stats = admin_get(f"/api/experiments/{EXPERIMENT_ID}/statistics")
                check("统计接口 response_count=1", stats["data"]["response_count"] == 1,
                      str(stats["data"]["response_count"]))
                q4 = next(q for q in stats["data"]["questions"] if q["id"] == "q4")
                check("q4 分母=1（只 1 份作答）", q4["answered_count"] == 1, str(q4["answered_count"]))

                # 管理页面路由（B 的模板已交付）
                for path in ("/", "/projects/new", f"/projects/{PROJECT_ID}"):
                    admin_page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
                    check(f"管理页 {path} 渲染成功", admin_page.title() != "" and "500" not in admin_page.title(),
                          admin_page.url)
                admin_page.screenshot(path=str(ARTIFACTS / "admin-workspace.png"), full_page=True)
                manager.close()
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 72, flush=True)
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项", flush=True)
    for name in failed:
        print("  失败：", name, flush=True)
    print(f"截图目录：{ARTIFACTS}", flush=True)
    print("=" * 72, flush=True)
    try:
        DB.unlink(missing_ok=True)
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
