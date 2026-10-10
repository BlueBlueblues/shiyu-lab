"""工具工作流、故障分支、运行锁与复盘快照用例。

成员C指南 C02 第 6 条、C04 第 7 条；总纲 §5（失败行为）、§10 的
T03（模型失败后重试）、T08（零反馈与真实反馈复盘）、T09（复盘后新增反馈）、
T10（工具日志与并发/中断）。

故障注入方式：只覆盖 ``app.services.llm_client`` 的统一模型入口，
让 A 的 workflow / statistics / repositories 仍然是被测对象。
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading

import httpx
import pytest

from conftest import (
    REVIEW_FIXTURE,
    SAMPLE_RESPONSES,
    build_env,
    data_of,
    error_code,
    insert_running_run,
)

REVIEW_STATUSES = {"preliminary_support", "not_supported", "insufficient_evidence"}


def latest_run(app_env, project_id: str) -> dict:
    runs = data_of(app_env.admin.get(f"/api/projects/{project_id}/runs"))
    assert runs, "项目应至少有一条运行记录"
    return runs[0]


# --------------------------------------------------------------------------
# T03 模型异常后的可读错误、无半成品、锁释放、可重试
# --------------------------------------------------------------------------


def test_invalid_model_output_fails_cleanly_and_allows_retry(app_env):
    """T03：非法 JSON 不得被静默替换成示例计划，也不得留下半份实验。"""
    project_id = app_env.create_project()
    app_env.fake_llm.queue = ["这不是 JSON，也不是计划。"]

    failed = app_env.generate_plan(project_id)
    assert failed.status_code == 502, (
        f"非法模型输出应 502，实际 {failed.status_code} {failed.text[:300]}"
    )
    assert error_code(failed) == "LLM_OUTPUT_INVALID"

    detail = data_of(app_env.admin.get(f"/api/projects/{project_id}"))
    assert detail["plan"] is None, "失败后不得写入计划"
    assert detail["experiment"] is None, "失败后不得留下半份实验草稿"

    run = latest_run(app_env, project_id)
    assert run["status"] == "failed", f"失败运行状态应为 failed：{run}"
    assert run["error_code"] == "LLM_OUTPUT_INVALID", f"运行错误码不符：{run}"
    assert run["finished_at"], "失败运行也要写结束时间"

    assert detail["running_run_id"] in (None, ""), "失败后运行锁必须释放"
    retry = app_env.generate_plan(project_id)
    assert retry.status_code == 200, (
        f"失败后重试应当成功，实际 {retry.status_code} {retry.text[:300]}"
    )
    assert data_of(retry)["status"] == "planned"
    assert app_env.fake_llm.call_count == 2, "一次失败 + 一次成功应各调用模型一次"


def test_llm_timeout_reports_504_and_releases_lock(app_env):
    """总纲 §5/§6：模型超时返回 504 LLM_TIMEOUT，且不自动重试、不锁死项目。"""
    project_id = app_env.create_project()
    app_env.fake_llm.queue = [httpx.TimeoutException("mock timeout after 40s")]

    failed = app_env.generate_plan(project_id)
    assert failed.status_code == 504, (
        f"模型超时应 504，实际 {failed.status_code} {failed.text[:300]}"
    )
    assert error_code(failed) == "LLM_TIMEOUT"
    assert app_env.fake_llm.call_count == 1, "契约规定不自动重试，模型只应被调用一次"

    run = latest_run(app_env, project_id)
    assert run["status"] == "failed" and run["error_code"] == "LLM_TIMEOUT"
    assert data_of(app_env.admin.get(f"/api/projects/{project_id}"))["running_run_id"] in (None, "")
    assert app_env.generate_plan(project_id).status_code == 200


def test_llm_upstream_error_reports_502(app_env):
    """上游不可用时返回 502 LLM_UPSTREAM_ERROR，且错误信息脱敏。"""
    project_id = app_env.create_project()
    app_env.fake_llm.queue = [httpx.ConnectError("mock upstream refused connection")]

    failed = app_env.generate_plan(project_id)
    assert failed.status_code == 502, failed.text[:300]
    assert error_code(failed) == "LLM_UPSTREAM_ERROR"

    message = failed.json()["error"]["message"]
    assert "c-test-key-not-used" not in message, "错误信息不得泄露 API Key"
    assert "Traceback" not in message, "错误信息不得回传堆栈"


def test_failed_regeneration_keeps_previous_draft(app_env):
    """A04 第 5 条：重生成失败不得覆盖之前成功的草稿。"""
    project_id = app_env.create_project()
    first = data_of(app_env.generate_plan(project_id))
    experiment_id = first["experiment_id"]
    before = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}"))

    app_env.fake_llm.queue = ["{ 这不是合法 JSON"]
    failed = app_env.generate_plan(project_id)
    assert failed.status_code == 502, failed.text[:300]

    after = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}"))
    assert after["landing"] == before["landing"], "失败的重生成不应改动草稿内容"
    assert after["questions"] == before["questions"]
    assert after["published_at"] is None and before["published_at"] is None

    detail = data_of(app_env.admin.get(f"/api/projects/{project_id}"))
    assert detail["experiment"]["id"] == experiment_id
    assert detail["status"] == "planned", "仍保留可用草稿，状态应为 planned"


# --------------------------------------------------------------------------
# T10 并发与中断
# --------------------------------------------------------------------------


def test_concurrent_plan_request_is_rejected_with_409(app_env):
    """T10：同项目运行中再次生成返回 409，但读取与日志仍可继续。"""
    project_id = app_env.create_project()
    app_env.fake_llm.entered = threading.Event()
    app_env.fake_llm.hold = threading.Event()

    outcome: dict = {}

    def first_request() -> None:
        try:
            outcome["response"] = app_env.admin.post(
                f"/api/projects/{project_id}/plan", json={}
            )
        except Exception as exc:  # noqa: BLE001 - 线程内异常要带回主线程报错
            outcome["exception"] = exc

    thread = threading.Thread(target=first_request, daemon=True)
    thread.start()
    try:
        assert app_env.fake_llm.entered.wait(20), "模型调用没有开始，无法验证并发保护"

        concurrent = app_env.admin_second.post(f"/api/projects/{project_id}/plan", json={})
        assert concurrent.status_code == 409, (
            f"运行中再次生成应 409，实际 {concurrent.status_code} {concurrent.text[:300]}"
        )
        assert error_code(concurrent) == "RUN_IN_PROGRESS"

        assert app_env.admin.get("/api/projects").status_code == 200, "运行中列表仍应可读"
        runs_during = app_env.admin.get(f"/api/projects/{project_id}/runs")
        assert runs_during.status_code == 200, "运行中必须能查询运行日志"
        assert latest_run(app_env, project_id)["status"] == "running", "运行日志应显示 running"
    finally:
        app_env.fake_llm.hold.set()
        thread.join(timeout=30)

    assert "exception" not in outcome, f"首次请求抛异常：{outcome.get('exception')!r}"
    assert outcome["response"].status_code == 200, outcome["response"].text[:400]
    assert latest_run(app_env, project_id)["status"] == "succeeded"


def test_interrupted_running_run_is_failed_on_startup(app_env, monkeypatch):
    """总纲 §5：启动时把上次中断遗留的 running 记录标记为 failed/INTERRUPTED。"""
    project_id = app_env.create_project()
    run_id = f"c-interrupted-{project_id[:8]}"
    insert_running_run(app_env.db_path, project_id, run_id)

    restarted = build_env(app_env.db_path, app_env.fake_llm, monkeypatch)
    try:
        runs = data_of(restarted.admin.get(f"/api/projects/{project_id}/runs"))
        target = [run for run in runs if run["id"] == run_id]
        assert len(target) == 1, f"重启后应能查到遗留运行：{runs}"
        assert target[0]["status"] == "failed", f"遗留 running 应被标记 failed：{target[0]}"
        assert target[0]["error_code"] == "INTERRUPTED", target[0]

        run_detail = data_of(restarted.admin.get(f"/api/runs/{run_id}"))
        for call in run_detail["tool_calls"]:
            assert call["status"] == "failed", f"遗留工具日志也应失败：{call}"
            assert call["error_code"] == "INTERRUPTED", call

        assert data_of(restarted.admin.get(f"/api/projects/{project_id}"))["running_run_id"] in (
            None,
            "",
        ), "重启后不得再有运行中的项目"
        retry = restarted.admin.post(f"/api/projects/{project_id}/plan", json={})
        assert retry.status_code == 200, (
            f"中断后应能重新运行，实际 {retry.status_code} {retry.text[:300]}"
        )
    finally:
        restarted.close()


# --------------------------------------------------------------------------
# T08 复盘
# --------------------------------------------------------------------------


def test_review_requires_published_experiment(app_env):
    """总纲 §6.2：仅发布后可调用复盘。"""
    empty_project = app_env.create_project()
    response = app_env.review(empty_project)
    assert response.status_code == 400, (
        f"尚无实验时复盘应 400，实际 {response.status_code}"
    )
    assert error_code(response) == "INVALID_STATE"

    drafted_project = app_env.create_project()
    app_env.generate_plan(drafted_project)
    response = app_env.review(drafted_project)
    assert response.status_code == 400, (
        f"草稿未发布时复盘应 400，实际 {response.status_code}"
    )
    assert error_code(response) == "INVALID_STATE"


def test_review_saves_snapshot_and_uses_model_once(app_env, published_with_sample):
    """T08：复盘保存当时统计快照，只调用一次模型，并留下 analyze_experiment 工具日志。"""
    project_id = published_with_sample["project_id"]
    calls_before = app_env.fake_llm.call_count

    response = app_env.review(project_id)
    assert response.status_code == 200, response.text[:400]
    result = data_of(response)
    for key in ("report_id", "run_id", "response_count", "cutoff_response_id"):
        assert key in result, f"复盘返回缺少 {key}：{result}"
    assert result["response_count"] == 4, f"应复盘 4 份回答：{result['response_count']}"
    assert result["cutoff_response_id"] == max(published_with_sample["response_ids"])

    assert app_env.fake_llm.call_count == calls_before + 1, (
        "一次复盘只应调用模型一次（统计必须由程序完成）"
    )

    run_detail = data_of(app_env.admin.get(f"/api/runs/{result['run_id']}"))
    tool_names = [call["tool_name"] for call in run_detail["tool_calls"]]
    assert tool_names == ["analyze_experiment"], f"复盘工具名不符：{tool_names}"
    assert run_detail["status"] == "succeeded"

    report = app_env.report(project_id)
    assert report["id"] == result["report_id"]
    assert report["data_kind"] == "test", "报告必须标出数据来源是 test"
    assert report["response_count"] == 4
    assert report["stale"] is False, "刚生成的报告不应是过期快照"

    snapshot = report["statistics"]
    assert snapshot["response_count"] == 4
    q4 = [q for q in snapshot["questions"] if q["id"] == "q4"][0]
    o1 = [o for o in q4["options"] if o["id"] == "o1"][0]
    assert (o1["count"], o1["percentage"]) == (2, 66.67), (
        "报告里的统计必须是保存的快照，且分母正确"
    )

    review = report["review"]
    for key in ("summary", "hypothesis_assessments", "key_feedback", "limitations", "next_actions"):
        assert key in review, f"复盘输出缺少 {key}：{list(review)}"
    assessed = {item["hypothesis_id"] for item in review["hypothesis_assessments"]}
    assert assessed == {"h1", "h2", "h3"}, f"必须评估 3 条假设：{assessed}"
    for item in review["hypothesis_assessments"]:
        assert item["status"] in REVIEW_STATUSES, item
        assert item["reason"], item
    assert len(review["next_actions"]) <= 3, "next_actions 最多 3 项（总纲 §7.5）"
    for action in review["next_actions"]:
        for key in ("action", "owner_role", "completion_criterion"):
            assert action.get(key), f"行动项缺少 {key}：{action}"


def test_zero_response_review_forces_insufficient_evidence(app_env, published):
    """T08：零回答时，即使模型声称有支持，程序也必须强制为 insufficient_evidence。"""
    optimistic = copy.deepcopy(REVIEW_FIXTURE)
    for item in optimistic["hypothesis_assessments"]:
        item["status"] = "preliminary_support"
    app_env.fake_llm.queue = [json.dumps(optimistic, ensure_ascii=False)]

    response = app_env.review(published["project_id"])
    assert response.status_code == 200, response.text[:400]
    result = data_of(response)
    assert result["response_count"] == 0
    assert result["cutoff_response_id"] == 0

    report = app_env.report(published["project_id"])
    for item in report["review"]["hypothesis_assessments"]:
        assert item["status"] == "insufficient_evidence", (
            f"零回答必须强制证据不足，实际 {item}"
        )


def test_review_rejects_evidence_outside_snapshot(app_env, published_with_sample):
    """A07 第 5 条：引用不存在或不属于本快照的回答必须被拒绝，且不保存报告。"""
    project_id = published_with_sample["project_id"]

    stale = copy.deepcopy(REVIEW_FIXTURE)
    stale["hypothesis_assessments"][2]["evidence_refs"] = [
        {"question_id": "q3", "response_id": 987654321}
    ]
    app_env.fake_llm.queue = [json.dumps(stale, ensure_ascii=False)]

    rejected = app_env.review(project_id)
    assert rejected.status_code == 502, (
        f"越界引用应判为模型输出非法，实际 {rejected.status_code} {rejected.text[:300]}"
    )
    assert error_code(rejected) == "LLM_OUTPUT_INVALID"

    missing = app_env.admin.get(f"/api/projects/{project_id}/report")
    assert missing.status_code == 404, (
        f"失败的复盘不得留下报告，实际 {missing.status_code}"
    )

    valid = copy.deepcopy(REVIEW_FIXTURE)
    real_response_id = published_with_sample["response_ids"][2]
    valid["hypothesis_assessments"][2]["evidence_refs"] = [
        {"question_id": "q3", "response_id": real_response_id}
    ]
    app_env.fake_llm.queue = [json.dumps(valid, ensure_ascii=False)]
    accepted = app_env.review(project_id)
    assert accepted.status_code == 200, (
        f"引用本快照内真实回答应被接受，实际 {accepted.status_code} {accepted.text[:300]}"
    )
    assert data_of(accepted)["response_count"] == 4


# --------------------------------------------------------------------------
# T09 快照与过期
# --------------------------------------------------------------------------


def test_new_response_makes_report_stale_and_old_report_is_kept(app_env, published_with_sample):
    """T09：新增反馈后旧报告不改、stale=true，重新复盘纳入新回答。"""
    project_id = published_with_sample["project_id"]
    experiment_id = published_with_sample["experiment_id"]

    first = data_of(app_env.review(project_id))
    first_report = app_env.report(project_id)
    assert first_report["response_count"] == 4 and first_report["stale"] is False

    extra = app_env.submit(published_with_sample["token"], SAMPLE_RESPONSES[0])
    assert extra.status_code == 201, extra.text[:300]

    stale_report = app_env.report(project_id)
    assert stale_report["id"] == first["report_id"], "新增回答不应自动生成新报告"
    assert stale_report["response_count"] == 4, "旧报告使用的回答份数不得被改写"
    assert stale_report["stale"] is True, "落后于当前反馈时 stale 必须为 true"

    detail = data_of(app_env.admin.get(f"/api/projects/{project_id}"))
    assert detail["status"] == "collecting", (
        f"有未纳入报告的新反馈时状态应回到 collecting，实际 {detail['status']}"
    )
    assert detail["latest_report_meta"]["stale"] is True

    connection = sqlite3.connect(str(app_env.db_path))
    try:
        rows = connection.execute(
            "SELECT id, response_count, cutoff_response_id FROM reports "
            "WHERE experiment_id = ? ORDER BY rowid",
            (experiment_id,),
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, f"重新复盘前应只有 1 份报告：{rows}"
    assert rows[0][1] == 4

    second = data_of(app_env.review(project_id))
    assert second["report_id"] != first["report_id"], "重新复盘应生成新报告，不覆盖旧的"
    assert second["response_count"] == 5, "新报告必须纳入新增回答"

    fresh = app_env.report(project_id)
    assert fresh["id"] == second["report_id"]
    assert fresh["response_count"] == 5 and fresh["stale"] is False

    connection = sqlite3.connect(str(app_env.db_path))
    try:
        rows = connection.execute(
            "SELECT response_count FROM reports WHERE experiment_id = ? ORDER BY rowid",
            (experiment_id,),
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in rows] == [4, 5], (
        f"旧报告必须保留且不被覆盖，实际 reports.response_count = {rows}"
    )


def test_runs_endpoint_orders_by_start_time_desc(app_env, published):
    """总纲 §6.2：运行摘要按开始时间倒序。"""
    project_id = published["project_id"]
    app_env.review(project_id)

    runs = data_of(app_env.admin.get(f"/api/projects/{project_id}/runs"))
    assert len(runs) == 2, f"应有计划与复盘两条运行记录：{runs}"
    started = [run["started_at"] for run in runs]
    assert started == sorted(started, reverse=True), f"运行摘要未按开始时间倒序：{started}"
    assert set(run["action"] for run in runs) == {"plan", "review"}


# --------------------------------------------------------------------------
# 报告下载
# --------------------------------------------------------------------------


def test_markdown_report_download(app_env, published_with_sample):
    """A07 第 8/9 条：Markdown 下载来自保存的快照，无报告时 404。"""
    project_id = published_with_sample["project_id"]

    before = app_env.admin.get(f"/api/projects/{project_id}/report.md")
    assert before.status_code == 404, f"无报告时应 404，实际 {before.status_code}"

    app_env.review(project_id)
    response = app_env.admin.get(f"/api/projects/{project_id}/report.md")
    assert response.status_code == 200, response.text[:300]

    content_type = response.headers.get("content-type", "")
    assert "application/json" not in content_type, (
        f"报告下载必须是 Markdown 文件而不是 JSON：{content_type}"
    )
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition.lower(), f"应作为附件下载：{disposition}"

    body = response.text
    assert "4" in body, "报告应包含回答份数"
    assert "66.67" in body, "报告应引用保存的统计快照（多选分母 2/3）"
    assert "test" in body.lower() or "测试" in body, "报告必须标明数据来源 real/test"
    assert "insufficient" in body or "证据" in body, "报告应包含结论或局限说明"

    download_again = app_env.admin.get(f"/api/projects/{project_id}/report.md")
    assert download_again.text == body, "同一报告重复下载内容必须一致"
