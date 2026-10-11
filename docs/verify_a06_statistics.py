"""C 独立核验 A06 统计口径（不依赖 A 的测试用例）。

与总纲 §10 T07 的固定样本一致：
  R1: q1=o1, q4=[o1,o2], q3="第一条文字"
  R2: q1=o2, q4=[o1],    q3=未答
  R3: q1=o1, q4=[o2],    q3="第二条文字"
  R4: q1=o2, q4=未答,    q3=未答

应得：response_count=4；q1 answered_count=4（各 50%）；
      q4 answered_count=3（o1=2 → 66.67%，o2=2 → 66.67%，合计 >100%）；
      q3 answered_count=2；零回答时 percentage 全为 null。

用法（仓库在工作区外，用 PYTHONPATH 指向仓库）：
  $env:PYTHONPATH="C:\...\firstorder-lab"
  <venv311>\Scripts\python.exe C:\...\.c-verify\verify_a06_statistics.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

REPO = Path(os.environ.get("FO_REPO", r"C:\Users\MATEBOOK\Documents\GitHub\firstorder-lab"))
sys.path.insert(0, str(REPO))

DB = Path(tempfile.gettempdir()) / f"fo-a06-verify-{uuid.uuid4().hex[:8]}.sqlite3"
os.environ.update({
    "APP_ENV": "test", "DATABASE_PATH": str(DB),
    "LLM_BASE_URL": "https://llm.example.invalid/v1",
    "LLM_API_KEY": "not-real", "LLM_MODEL": "test-model",
    "ADMIN_USER": "admin", "ADMIN_PASSWORD": "verify-password",
    "PUBLIC_BASE_URL": "http://testserver",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect_db, init_db  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN = ("admin", "verify-password")
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
DRAFT_PROJECT_ID = "33333333-3333-4333-8333-333333333333"
EXPERIMENT_ID = "exp-a06-verify"
DRAFT_ID = "exp-a06-draft"
TOKEN = "t" * 44

LANDING = {"title": "统计核验", "summary": "用于独立核验统计口径的服务简介。",
           "target_users": "社团负责人", "value_proposition": "简化收集",
           "features": ["导出"], "disclaimer": "当前为需求验证，不代表已成交或服务上线。"}
QUESTIONS = [
    {"id": "q1", "type": "single", "title": "是否遇到过整理困难？", "required": True,
     "options": [{"id": "o1", "label": "经常"}, {"id": "o2", "label": "偶尔"}]},
    {"id": "q2", "type": "single", "title": "是否愿意试用？", "required": True,
     "options": [{"id": "o1", "label": "愿意"}, {"id": "o2", "label": "不愿意"}]},
    {"id": "q3", "type": "text", "title": "现有流程有什么问题？", "required": False, "options": []},
    {"id": "q4", "type": "multiple", "title": "关心哪些功能？", "required": False,
     "options": [{"id": "o1", "label": "手机方便"}, {"id": "o2", "label": "信息导出"},
                 {"id": "o3", "label": "操作简单"}, {"id": "o4", "label": "暂无需求"}]},
]
PLAN = {
    "target_customer": "组织者", "pain_points": ["汇总耗时"],
    "value_proposition": "简单可维护",
    "hypotheses": [
        {"id": "h1", "statement": "存在整理困难", "question_ids": ["q1"]},
        {"id": "h2", "statement": "愿意试用", "question_ids": ["q2"]},
        {"id": "h3", "statement": "能指出改进点", "question_ids": ["q3"]},
    ],
    "experiment_steps": ["邀请同学填写"], "next_tasks": ["人工检查措辞"],
    "landing": LANDING, "questions": QUESTIONS,
}
SAMPLES = [
    {"q1": "o1", "q2": "o1", "q3": "第一条文字", "q4": ["o1", "o2"]},
    {"q1": "o2", "q2": "o1", "q4": ["o1"]},
    {"q1": "o1", "q2": "o2", "q3": "第二条文字", "q4": ["o2"]},
    {"q1": "o2", "q2": "o2"},
]

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def seed():
    init_db(DB)
    with connect_db(DB) as c:
        c.execute("INSERT INTO projects (id,name,idea,target_users,skills,budget_cny,"
                  "validation_goal,data_kind,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (PROJECT_ID, "统计核验项目", "低成本服务验证。", "社团负责人", "Python",
                   100.0, "验证需求", "test", json.dumps(PLAN, ensure_ascii=False),
                   "2026-10-11T00:00:00+00:00"))
        c.execute("INSERT INTO experiments (id,project_id,public_token,landing_json,"
                  "questions_json,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
                  (EXPERIMENT_ID, PROJECT_ID, TOKEN,
                   json.dumps(LANDING, ensure_ascii=False),
                   json.dumps(QUESTIONS, ensure_ascii=False),
                   "2026-10-11T00:00:00+00:00", "2026-10-11T00:00:00+00:00"))
        c.execute("INSERT INTO projects (id,name,idea,target_users,skills,budget_cny,"
                  "validation_goal,data_kind,plan_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (DRAFT_PROJECT_ID, "草稿项目", "验证。", "用户", "Python",
                   0.0, "验证", "test", json.dumps(PLAN, ensure_ascii=False), "2026-10-11T00:00:00+00:00"))
        c.execute("INSERT INTO experiments (id,project_id,public_token,landing_json,"
                  "questions_json,published_at,created_at) VALUES (?,?,?,?,?,?,?)",
                  (DRAFT_ID, DRAFT_PROJECT_ID, "d" * 44,
                   json.dumps(LANDING, ensure_ascii=False),
                   json.dumps(QUESTIONS, ensure_ascii=False), None, "2026-10-11T00:00:00+00:00"))


def main():
    seed()
    with TestClient(create_app(), follow_redirects=False) as client, \
            TestClient(create_app(), follow_redirects=False) as anon:
        client.auth = ADMIN

        # ---- 零回答 ----
        zero = client.get(f"/api/experiments/{EXPERIMENT_ID}/statistics")
        check("零回答统计 200", zero.status_code == 200, f"HTTP {zero.status_code}")
        zdata = zero.json()["data"]
        check("零回答 response_count=0", zdata["response_count"] == 0)
        check("零回答 cutoff_response_id=0", zdata["cutoff_response_id"] == 0)
        nulls = [o["percentage"] for q in zdata["questions"] if q["type"] != "text" for o in q["options"]]
        check("零回答所有 percentage=null（不是 0）", all(p is None for p in nulls), f"实际 {set(nulls)}")
        check("零回答仍输出全部选项",
              all(len(q.get("options", [])) >= 2 for q in zdata["questions"] if q["type"] != "text"))

        # ---- 提交 4 份样本 ----
        for index, answers in enumerate(SAMPLES, start=1):
            payload = {"client_submission_id": str(uuid.uuid4()), "consent": True, "answers": answers}
            response = client.post(f"/api/public/e/{TOKEN}/responses", json=payload)
            if response.status_code != 201:
                check(f"第 {index} 份样本提交", False, f"HTTP {response.status_code} {response.text[:150]}")

        stat = client.get(f"/api/experiments/{EXPERIMENT_ID}/statistics").json()["data"]
        check("response_count=4", stat["response_count"] == 4, f"实际 {stat['response_count']}")
        check("cutoff_response_id>0", stat["cutoff_response_id"] > 0, f"实际 {stat['cutoff_response_id']}")

        by_id = {q["id"]: q for q in stat["questions"]}
        q1, q3, q4 = by_id["q1"], by_id["q3"], by_id["q4"]

        check("q1 answered_count=4（必答单选分母=全部回答）", q1["answered_count"] == 4,
              f"实际 {q1['answered_count']}")
        q1pct = {o["id"]: o["percentage"] for o in q1["options"]}
        check("q1 两选项各 50%", q1pct == {"o1": 50.0, "o2": 50.0}, f"实际 {q1pct}")

        check("★ q4 answered_count=3（可选多选分母=作答份数）", q4["answered_count"] == 3,
              f"实际 {q4['answered_count']}")
        q4pct = {o["id"]: o["percentage"] for o in q4["options"]}
        check("★ q4 o1=66.67%", q4pct.get("o1") == 66.67, f"实际 {q4pct.get('o1')}")
        check("★ q4 o2=66.67%", q4pct.get("o2") == 66.67, f"实际 {q4pct.get('o2')}")
        check("q4 未选中选项为 0%（不是 null）",
              q4pct.get("o3") == 0.0 and q4pct.get("o4") == 0.0,
              f"实际 o3={q4pct.get('o3')} o4={q4pct.get('o4')}")
        check("q4 百分比合计 >100%（多选特性）", sum(v for v in q4pct.values() if v) > 100,
              f"实际 {sum(v for v in q4pct.values() if v)}")

        check("q3 answered_count=2（空白未答不计入）", q3["answered_count"] == 2,
              f"实际 {q3['answered_count']}")
        check("q3 返回 response_id 与 text",
              all(isinstance(t.get("response_id"), int) and t.get("text") for t in q3["texts"]),
              f"实际 {q3['texts']}")

        # ---- 原始反馈接口 ----
        raw = client.get(f"/api/experiments/{EXPERIMENT_ID}/responses")
        check("原始反馈 200", raw.status_code == 200, f"HTTP {raw.status_code}")
        rows = raw.json()["data"]
        check("原始反馈 4 条且按 id 升序",
              len(rows) == 4 and [r["id"] for r in rows] == sorted(r["id"] for r in rows))
        check("原始反馈不暴露 client_submission_id / token",
              all("client_submission_id" not in r and "public_token" not in r for r in rows))

        # ---- 权限 ----
        check("未登录读统计 401",
              anon.get(f"/api/experiments/{EXPERIMENT_ID}/statistics").status_code == 401)
        check("未登录读原始反馈 401",
              anon.get(f"/api/experiments/{EXPERIMENT_ID}/responses").status_code == 401)

        # ---- 草稿未发布 ----
        draft = client.get(f"/api/experiments/{DRAFT_ID}/statistics")
        check("未发布草稿统计 400 INVALID_STATE",
              draft.status_code == 400 and "INVALID_STATE" in draft.text,
              f"HTTP {draft.status_code} {draft.text[:120]}")

    failed = [n for n, ok, _ in results if not ok]
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
