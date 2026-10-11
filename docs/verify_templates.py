"""模板联调验证脚本（一次性，验证通过后可删）

用途：在不依赖真实模型的情况下，验证新交付的 4 个 Jinja2 模板能否被真实路由渲染，
      以及问卷三类题能否通过公开提交接口入库、幂等、拒绝非法输入。

运行方式（本机临时环境）：
    python docs/verify_templates.py
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

DB = Path(tempfile.gettempdir()) / f"fo-verify-{uuid.uuid4().hex[:8]}.sqlite3"
os.environ.update({
    "APP_ENV": "test",
    "DATABASE_PATH": str(DB),
    "LLM_BASE_URL": "https://llm.example.invalid/v1",
    "LLM_API_KEY": "test-key-not-real",
    "LLM_MODEL": "test-model",
    "ADMIN_USER": "admin",
    "ADMIN_PASSWORD": "verify-password",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect_db, init_db  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN = ("admin", "verify-password")
WRITE_HEADERS = {"Origin": "http://testserver", "X-Requested-With": "FirstOrderLab"}

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
    "landing": LANDING,
    "questions": QUESTIONS,
}

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EXPERIMENT_ID = "exp-verify-0001"
TOKEN = "v" * 44

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


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
            (EXPERIMENT_ID, PROJECT_ID, TOKEN,
             json.dumps(LANDING, ensure_ascii=False), json.dumps(QUESTIONS, ensure_ascii=False),
             None, "2026-10-10T00:00:00+00:00"),
        )


def run_checks(client: TestClient, anonymous: TestClient) -> None:
    client.auth = ADMIN

    # ---------- 1. 草稿预览可访问 ----------
    for path, label, marker in (
        (f"/experiments/{EXPERIMENT_ID}/edit", "草稿编辑页", "experiment-save"),
        (f"/experiments/{EXPERIMENT_ID}/preview/service", "服务页预览", "preview-banner"),
        (f"/experiments/{EXPERIMENT_ID}/preview/survey", "问卷预览", "preview-banner"),
    ):
        response = client.get(path)
        check(f"{label} 200", response.status_code == 200,
              f"{path} → {response.status_code}" + ("" if response.status_code == 200 else f" {response.text[:120]}"))
        if response.status_code == 200:
            check(f"{label} 含 {marker}", marker in response.text)

    # 预览问卷必须禁用提交
    preview = client.get(f"/experiments/{EXPERIMENT_ID}/preview/survey")
    if preview.status_code == 200:
        check("预览问卷禁用提交按钮",
              'data-testid="survey-submit" disabled' in preview.text)

    # 公开页不得泄露管理数据
    public_leak_check = client.get(f"/experiments/{EXPERIMENT_ID}/preview/service")
    if public_leak_check.status_code == 200:
        html = public_leak_check.text
        check("预览页无管理数据泄露",
              "run-log" not in html and "response-count" not in html and "admin" not in html.lower())

    # ---------- 2. 未发布时公开页 404 ----------
    check("未发布公开服务页 404", client.get(f"/e/{TOKEN}").status_code == 404)
    check("未发布公开问卷 404", client.get(f"/e/{TOKEN}/survey").status_code == 404)

    # ---------- 3. 草稿编辑保存 ----------
    edited_landing = dict(LANDING)
    edited_landing["title"] = "校园活动报名网页服务（已编辑）"
    edited = {"landing": edited_landing, "questions": QUESTIONS}
    response = client.patch(f"/api/experiments/{EXPERIMENT_ID}", json=edited, headers=WRITE_HEADERS)
    check("PATCH 草稿保存 200", response.status_code == 200,
          f"状态 {response.status_code} {response.text[:160]}")

    # 结构变更必须被拒绝
    broken_questions = [dict(QUESTIONS[0], id="qX")] + QUESTIONS[1:]
    response = client.patch(f"/api/experiments/{EXPERIMENT_ID}",
                            json={"landing": edited_landing, "questions": broken_questions},
                            headers=WRITE_HEADERS)
    check("PATCH 改题号被拒 422", response.status_code == 422, f"状态 {response.status_code}")

    # ---------- 4. 人工发布 ----------
    response = client.post(f"/api/experiments/{EXPERIMENT_ID}/publish", json={}, headers=WRITE_HEADERS)
    check("发布 200", response.status_code == 200,
          f"状态 {response.status_code} {response.text[:160]}")
    published = response.json().get("data", {}) if response.status_code == 200 else {}
    check("发布返回 survey_url", bool(published.get("survey_url")), str(published.get("survey_url")))

    # ---------- 5. 发布后公开页 200 ----------
    response = client.get(f"/e/{TOKEN}")
    check("发布后公开服务页 200", response.status_code == 200, f"状态 {response.status_code}")
    if response.status_code == 200:
        check("服务页含 CTA 与验证声明",
              "survey-cta" in response.text and "需求验证" in response.text)
        check("公开服务页无管理数据泄露",
              "run-log" not in response.text and "admin" not in response.text.lower())

    response = client.get(f"/e/{TOKEN}/survey")
    check("发布后公开问卷 200", response.status_code == 200, f"状态 {response.status_code}")
    if response.status_code == 200:
        html = response.text
        check("问卷含三类题与 testid",
              all(f'data-testid="question-{q["id"]}"' in html for q in QUESTIONS)
              and 'data-testid="survey-consent"' in html
              and 'data-testid="submission-success"' in html)
        check("问卷渲染公开提交地址",
              f"/api/public/e/{TOKEN}/responses" in html)

    # 发布后编辑必须锁定
    response = client.patch(f"/api/experiments/{EXPERIMENT_ID}", json=edited, headers=WRITE_HEADERS)
    check("发布后 PATCH 409 EXPERIMENT_LOCKED",
          response.status_code == 409 and "EXPERIMENT_LOCKED" in response.text,
          f"状态 {response.status_code}")

    response = client.get(f"/experiments/{EXPERIMENT_ID}/edit")
    check("发布后编辑页只读", response.status_code == 200 and "experiment-locked" in response.text)

    # ---------- 6. 三类题真实提交 ----------
    submission_id = str(uuid.uuid4())
    payload = {"client_submission_id": submission_id, "consent": True,
               "answers": {"q1": "o1", "q2": "o2", "q3": "报名信息散在聊天记录里", "q4": ["o1", "o2"]}}
    response = client.post(f"/api/public/e/{TOKEN}/responses", json=payload)
    check("三类题提交 201", response.status_code == 201,
          f"状态 {response.status_code} {response.text[:200]}")
    body = response.json().get("data", {}) if response.status_code in (200, 201) else {}
    check("提交返回 response_id", bool(body.get("response_id")), str(body))

    # 幂等重放
    response = client.post(f"/api/public/e/{TOKEN}/responses", json=payload)
    duplicate = response.json().get("data", {}).get("duplicate") if response.status_code == 200 else None
    check("同 UUID 同内容重放 200 + duplicate",
          response.status_code == 200 and duplicate is True, f"状态 {response.status_code}")

    # 同 UUID 不同内容冲突
    conflict = dict(payload, answers={"q1": "o2", "q2": "o2", "q4": ["o1"]})
    response = client.post(f"/api/public/e/{TOKEN}/responses", json=conflict)
    check("同 UUID 不同内容 409", response.status_code == 409, f"状态 {response.status_code}")

    # 非法选项
    invalid = {"client_submission_id": str(uuid.uuid4()), "consent": True,
               "answers": {"q1": "o9", "q2": "o1", "q4": ["o1"]}}
    response = client.post(f"/api/public/e/{TOKEN}/responses", json=invalid)
    check("非法选项 422", response.status_code == 422, f"状态 {response.status_code}")

    # 必答缺失
    missing = {"client_submission_id": str(uuid.uuid4()), "consent": True, "answers": {"q3": "只有简答"}}
    response = client.post(f"/api/public/e/{TOKEN}/responses", json=missing)
    check("必答缺失 422", response.status_code == 422, f"状态 {response.status_code}")

    # consent=false
    no_consent = {"client_submission_id": str(uuid.uuid4()), "consent": False, "answers": {"q1": "o1"}}
    response = client.post(f"/api/public/e/{TOKEN}/responses", json=no_consent)
    check("consent=false 被拒 422", response.status_code == 422, f"状态 {response.status_code}")

    # 入库条数应为 1（重放与冲突都没新增）
    with connect_db(DB) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM responses WHERE experiment_id = ?", (EXPERIMENT_ID,)).fetchone()[0]
    check("数据库最终只入库 1 条回答", count == 1, f"实际 {count} 条")

    # ---------- 7. 权限与公开隔离 ----------
    check("公开首页无需认证 200", anonymous.get("/").status_code == 200)
    check("未登录管理页 401", anonymous.get("/projects").status_code == 401)
    check("未登录读取实验 401", anonymous.get(f"/api/experiments/{EXPERIMENT_ID}").status_code == 401)
    response = client.patch(f"/api/experiments/{EXPERIMENT_ID}", json=edited)
    check("缺 X-Requested-With 写入 403", response.status_code == 403, f"状态 {response.status_code}")

    # ---------- 8. /health ----------
    health = anonymous.get("/health")
    payload_health = health.json() if health.status_code == 200 else None
    check('GET /health 返回 {"data":{"status":"ok"}}',
          health.status_code == 200 and payload_health == {"data": {"status": "ok"}},
          f"实际返回 {json.dumps(payload_health, ensure_ascii=False)}")


def main() -> int:
    seed()
    with TestClient(create_app(), follow_redirects=False) as client, \
            TestClient(create_app(), follow_redirects=False) as anonymous:
        run_checks(client, anonymous)

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 72)
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    for name in failed:
        print("  失败：", name)
    print("=" * 72)
    try:
        DB.unlink(missing_ok=True)
    except OSError:
        pass
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
