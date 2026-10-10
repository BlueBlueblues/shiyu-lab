"""核心接口、权限与幂等用例。

对应成员C指南 C02 第 4 条与总纲 §10 的验收编号：
  T01 创建项目、刷新、重启      T04 草稿访问与发布
  T05 三类题真实提交            T06 同 UUID 重放和冲突
  T12 管理权限与公开隔离        （发布锁定、幂等、校验错误）

断言只针对总纲 §4/§6 写明的结构、状态码与错误码，不逐字断言模型文案。
"""

from __future__ import annotations

import uuid

import pytest

from conftest import (
    PUBLIC_ORIGIN,
    SAMPLE_RESPONSES,
    SITE_HEADER,
    TEST_PROJECT_PAYLOAD,
    build_env,
    data_of,
    error_code,
    read_public_token,
    token_from_url,
)

MANAGEMENT_GETS = (
    "/api/projects",
    "/api/experiments/does-not-exist",
)


# --------------------------------------------------------------------------
# T01 / 健康检查 / 权限边界
# --------------------------------------------------------------------------


def test_health_check_is_public_and_unwrapped(app_env):
    """总纲 §6.2：健康检查不要求管理员身份，也不暴露配置。"""
    response = app_env.anon.get("/health")
    assert response.status_code == 200, response.text[:400]
    assert response.json() == {"status": "ok"}, response.text[:400]


def test_management_reads_require_admin(app_env):
    """T12：未登录读管理数据必须 401 且带 AUTH_REQUIRED。"""
    for url in MANAGEMENT_GETS:
        response = app_env.anon.get(url)
        assert response.status_code == 401, f"{url} -> HTTP {response.status_code}"
        assert error_code(response) == "AUTH_REQUIRED", f"{url} 错误码不符"


def test_project_creation_persists_and_is_listed(app_env):
    """T01：创建 → 列表 → 详情一致，且派生状态为 draft。"""
    project_id = app_env.create_project()

    listing = app_env.admin.get("/api/projects")
    assert listing.status_code == 200, listing.text[:400]
    items = data_of(listing)
    assert isinstance(items, list)
    match = [item for item in items if item["id"] == project_id]
    assert len(match) == 1, f"列表里找不到刚创建的项目 {project_id}：{items}"
    assert match[0]["data_kind"] == "test", "data_kind 必须原样返回 test"
    assert match[0]["status"] == "draft", "尚无实验草稿时状态应为 draft（总纲 §5）"
    for key in ("id", "name", "status", "data_kind", "created_at"):
        assert key in match[0], f"项目摘要缺少 {key}：{match[0]}"

    detail_response = app_env.admin.get(f"/api/projects/{project_id}")
    assert detail_response.status_code == 200, detail_response.text[:400]
    detail = data_of(detail_response)
    for key in ("project", "plan", "experiment", "latest_report_meta", "running_run_id"):
        assert key in detail, f"详情缺少 {key}：{list(detail)}"
    assert detail["plan"] is None, "尚未生成计划时 plan 应为 null"
    assert detail["experiment"] is None, "尚未生成草稿时 experiment 应为 null"
    assert detail["latest_report_meta"] is None, "无报告时 latest_report_meta 应为 null"
    assert detail["running_run_id"] in (None, ""), "没有运行时 running_run_id 应为 null"
    assert detail["project"]["name"] == TEST_PROJECT_PAYLOAD["name"]
    assert detail["project"]["data_kind"] == "test"
    assert "plan_json" not in detail["project"], "详情不应重复返回 plan_json（总纲 §6.2）"


def test_project_survives_process_restart(app_env, monkeypatch):
    """T01：重启后同一项目仍可读取（重新导入应用 + 复用同一个库）。"""
    project_id = app_env.create_project()

    restarted = build_env(app_env.db_path, app_env.fake_llm, monkeypatch)
    try:
        response = restarted.admin.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, (
            f"重启后读不到项目：HTTP {response.status_code} {response.text[:400]}"
        )
        assert data_of(response)["project"]["name"] == TEST_PROJECT_PAYLOAD["name"]
    finally:
        restarted.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("name", "长" * 81),
        ("idea", "长" * 2001),
        ("target_users", "长" * 501),
        ("budget_cny", -1),
        ("budget_cny", 100001),
        ("data_kind", "production"),
    ],
)
def test_project_validation_rejects_bad_input(app_env, field, value):
    """总纲 §7.1：必填去空格非空、长度上限、预算范围、data_kind 枚举。"""
    payload = dict(TEST_PROJECT_PAYLOAD)
    payload[field] = value
    response = app_env.admin.post("/api/projects", json=payload)
    assert response.status_code == 422, (
        f"{field}={value!r} 应被拒绝，实际 HTTP {response.status_code} {response.text[:300]}"
    )
    assert error_code(response) == "VALIDATION_ERROR"
    fields = response.json()["error"].get("fields") or {}
    assert isinstance(fields, dict) and fields, "422 必须带 fields 以定位输入项（总纲 §6）"
    assert any(field in key for key in fields), (
        f"fields 未指向出错的字段 {field}：{fields}"
    )


def test_budget_zero_is_accepted(app_env):
    """B02：预算为 0 可以提交。"""
    response = app_env.admin.post(
        "/api/projects", json={**TEST_PROJECT_PAYLOAD, "budget_cny": 0}
    )
    assert response.status_code == 201, response.text[:400]


def test_management_write_requires_origin_and_site_header(app_env):
    """T12：管理写入缺固定请求头或来源非法都必须 403。"""
    payload = dict(TEST_PROJECT_PAYLOAD)

    missing_header = app_env.admin.variant(drop=(SITE_HEADER,)).post(
        "/api/projects", json=payload
    )
    assert missing_header.status_code == 403, (
        f"缺少 {SITE_HEADER} 应 403，实际 {missing_header.status_code}"
    )
    assert error_code(missing_header) == "FORBIDDEN"

    cross_origin = app_env.admin.post(
        "/api/projects", json=payload, headers={"Origin": "http://evil.example"}
    )
    assert cross_origin.status_code == 403, (
        f"跨源写入应 403，实际 {cross_origin.status_code}"
    )
    assert error_code(cross_origin) == "FORBIDDEN"

    ok = app_env.admin.post("/api/projects", json=payload)
    assert ok.status_code == 201, "合法来源 + 固定请求头的写入不应被拒绝"


# --------------------------------------------------------------------------
# T02 计划与草稿
# --------------------------------------------------------------------------


def test_generate_plan_creates_validated_draft(app_env):
    """T02：一次计划请求产生 3 条假设、4～6 题、合法引用与两条工具日志。"""
    project_id = app_env.create_project()
    response = app_env.generate_plan(project_id)
    assert response.status_code == 200, response.text[:400]
    result = data_of(response)
    for key in ("project_id", "experiment_id", "run_id", "status"):
        assert key in result, f"计划返回缺少 {key}：{result}"
    assert result["project_id"] == project_id
    assert result["status"] == "planned", "计划成功后状态应为 planned（总纲 §5）"

    experiment_response = app_env.admin.get(f"/api/experiments/{result['experiment_id']}")
    assert experiment_response.status_code == 200, experiment_response.text[:400]
    experiment = data_of(experiment_response)
    assert experiment["published_at"] is None, "草稿不应有发布时间"
    assert experiment["service_url"] is None and experiment["survey_url"] is None, (
        "未发布实验的公开链接必须为 null（总纲 §6.2）"
    )

    questions = experiment["questions"]
    assert 4 <= len(questions) <= 6, f"题目数量应在 4～6 之间，实际 {len(questions)}"
    assert [q["type"] for q in questions] == ["single", "single", "text", "multiple"], (
        "三类题必须齐全且题型与夹具一致"
    )
    question_ids = {q["id"] for q in questions}
    for question in questions:
        assert question["id"] and question["title"], f"题目缺少 id/title：{question}"
        if question["type"] == "text":
            assert question["options"] == [], "简答题 options 必须为空"
        else:
            assert 2 <= len(question["options"]) <= 8, (
                f"选择题选项应为 2～8 个：{question}"
            )
            option_ids = [o["id"] for o in question["options"]]
            assert len(option_ids) == len(set(option_ids)), "选项 ID 在题内必须唯一"

    detail = data_of(app_env.admin.get(f"/api/projects/{project_id}"))
    plan = detail["plan"]
    assert plan is not None, "计划成功后详情必须带 plan"
    assert len(plan["hypotheses"]) == 3, "计划必须有 3 条假设（总纲 §7.2）"
    for hypothesis in plan["hypotheses"]:
        assert hypothesis["question_ids"], f"假设缺少题号引用：{hypothesis}"
        for question_id in hypothesis["question_ids"]:
            assert question_id in question_ids, (
                f"假设 {hypothesis['id']} 引用了不存在的题号 {question_id}"
            )

    runs_response = app_env.admin.get(f"/api/projects/{project_id}/runs")
    assert runs_response.status_code == 200, runs_response.text[:400]
    runs = data_of(runs_response)
    assert len(runs) == 1, f"一次计划请求应产生一条 run，实际 {len(runs)}"
    run_id = result["run_id"]
    run_detail = data_of(app_env.admin.get(f"/api/runs/{run_id}"))
    tool_names = [call["tool_name"] for call in run_detail["tool_calls"]]
    assert tool_names == ["make_validation_plan", "create_experiment"], (
        f"工具名与顺序不符合总纲 §3.3：{tool_names}"
    )
    for call in run_detail["tool_calls"]:
        assert call["status"] == "succeeded", f"工具未成功：{call}"
        assert call["started_at"] and call["finished_at"], f"工具缺少时间线：{call}"
        assert call.get("output_ref_json") is not None, f"工具缺少结果引用：{call}"
    assert run_detail["status"] == "succeeded"

    assert app_env.fake_llm.call_count == 1, (
        "一次计划请求只应调用模型一次（create_experiment 由程序完成，"
        f"实际调用 {app_env.fake_llm.call_count} 次）"
    )


def test_regenerate_replaces_draft_but_keeps_ids(app_env):
    """A04 第 4 条：未发布重生成整体替换草稿，experiment_id 与 token 不变。"""
    project_id = app_env.create_project()
    first = data_of(app_env.generate_plan(project_id))
    token_before = read_public_token(app_env.db_path, first["experiment_id"])

    second = data_of(app_env.generate_plan(project_id))
    assert second["experiment_id"] == first["experiment_id"], (
        "重生成不应新建实验，应替换同一草稿"
    )
    token_after = read_public_token(app_env.db_path, second["experiment_id"])
    assert token_after == token_before, "重生成不应更换 public_token"
    assert second["run_id"] != first["run_id"], "重生成应产生一条新的 run"


# --------------------------------------------------------------------------
# T04 发布与锁定
# --------------------------------------------------------------------------


def test_draft_public_url_is_404_before_publish(app_env):
    """T04：未发布实验的公开地址必须 404。"""
    project_id = app_env.create_project()
    experiment_id = data_of(app_env.generate_plan(project_id))["experiment_id"]
    token = read_public_token(app_env.db_path, experiment_id)

    for url in (f"/e/{token}", f"/e/{token}/survey"):
        response = app_env.anon.get(url)
        assert response.status_code == 404, (
            f"草稿公开地址 {url} 应为 404，实际 {response.status_code}"
        )


def test_publish_exposes_public_pages_and_locks_edits(app_env):
    """T04：发布后公开可访问、重复发布幂等、改题与重生成 409 锁定。"""
    project_id = app_env.create_project()
    experiment_id = data_of(app_env.generate_plan(project_id))["experiment_id"]

    first = app_env.publish(experiment_id)
    assert first.status_code == 200, first.text[:400]
    published = data_of(first)
    for key in ("experiment_id", "published_at", "service_url", "survey_url"):
        assert key in published, f"发布返回缺少 {key}：{published}"
    assert published["published_at"], "发布后必须有发布时间"
    service_url = published["service_url"]
    token = token_from_url(service_url)
    assert token_from_url(published["survey_url"]) == token, "服务页与问卷必须同一个 token"
    assert service_url.startswith(PUBLIC_ORIGIN + "/e/"), (
        f"公开链接应使用受控的 PUBLIC_BASE_URL（{PUBLIC_ORIGIN}），实际 {service_url}"
    )

    service_page = app_env.anon.get(service_url)
    assert service_page.status_code == 200, service_page.text[:400]
    survey_page = app_env.anon.get(published["survey_url"])
    assert survey_page.status_code == 200, survey_page.text[:400]

    repeat = app_env.publish(experiment_id)
    assert repeat.status_code == 200, f"重复发布应返回同一结果：{repeat.text[:300]}"
    repeated = data_of(repeat)
    assert repeated["published_at"] == published["published_at"], "重复发布不应改变发布时间"
    assert repeated["service_url"] == published["service_url"]

    experiment = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}"))
    locked_patch = app_env.admin.patch(
        f"/api/experiments/{experiment_id}",
        json={"landing": experiment["landing"], "questions": experiment["questions"]},
    )
    assert locked_patch.status_code == 409, (
        f"发布后编辑应 409，实际 {locked_patch.status_code}"
    )
    assert error_code(locked_patch) == "EXPERIMENT_LOCKED"

    locked_plan = app_env.generate_plan(project_id)
    assert locked_plan.status_code == 409, (
        f"发布后重生成应 409，实际 {locked_plan.status_code}"
    )
    assert error_code(locked_plan) == "EXPERIMENT_LOCKED"


def test_unknown_token_is_404(app_env, published):
    """T04：无效 token 一律 404。"""
    assert app_env.anon.get("/e/this-token-does-not-exist").status_code == 404
    assert app_env.anon.get("/e/this-token-does-not-exist/survey").status_code == 404


def test_draft_patch_only_allows_text_and_required(app_env):
    """A05 第 2 条：只允许改文字、选项 label 与 required，结构保持不变。"""
    project_id = app_env.create_project()
    experiment_id = data_of(app_env.generate_plan(project_id))["experiment_id"]
    experiment = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}"))

    landing = dict(experiment["landing"])
    landing["title"] = "校园活动报名网页服务（已人工检查措辞）"
    questions = [dict(q) for q in experiment["questions"]]
    questions[0] = dict(questions[0])
    questions[0]["title"] = "人工改写后的题干"
    questions[0]["options"] = [dict(o) for o in questions[0]["options"]]
    questions[0]["options"][0]["label"] = "人工改写后的选项"
    questions[2] = dict(questions[2])
    questions[2]["required"] = False

    response = app_env.admin.patch(
        f"/api/experiments/{experiment_id}",
        json={"landing": landing, "questions": questions},
    )
    assert response.status_code == 200, response.text[:400]
    saved = data_of(response)
    assert saved["landing"]["title"] == landing["title"]
    assert saved["questions"][0]["title"] == "人工改写后的题干"
    assert saved["questions"][0]["options"][0]["label"] == "人工改写后的选项"
    assert [q["id"] for q in saved["questions"]] == [q["id"] for q in experiment["questions"]]
    assert [q["type"] for q in saved["questions"]] == [
        q["type"] for q in experiment["questions"]
    ]
    assert saved["questions"][0]["options"][0]["id"] == experiment["questions"][0]["options"][0]["id"]

    reopened = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}"))
    assert reopened["landing"]["title"] == landing["title"], "刷新后修改应仍在"

    # 改 ID / 改题型属于结构变更，必须拒绝并保持假设引用有效。
    broken = [dict(q) for q in reopened["questions"]]
    broken[0] = {**broken[0], "id": "q-renamed"}
    bad_id = app_env.admin.patch(
        f"/api/experiments/{experiment_id}",
        json={"landing": reopened["landing"], "questions": broken},
    )
    assert bad_id.status_code == 422, (
        f"修改题号应被拒绝，实际 {bad_id.status_code} {bad_id.text[:300]}"
    )
    assert error_code(bad_id) == "VALIDATION_ERROR"

    broken_type = [dict(q) for q in reopened["questions"]]
    broken_type[0] = {**broken_type[0], "type": "text", "options": []}
    bad_type = app_env.admin.patch(
        f"/api/experiments/{experiment_id}",
        json={"landing": reopened["landing"], "questions": broken_type},
    )
    assert bad_type.status_code == 422, (
        f"修改题型应被拒绝，实际 {bad_type.status_code} {bad_type.text[:300]}"
    )


# --------------------------------------------------------------------------
# T05 公开提交校验
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "answers"),
    [
        ("未知题号", {"q99": "o1", "q1": "o1", "q2": "o1"}),
        ("未知选项", {"q1": "o99", "q2": "o1"}),
        ("必答缺失", {"q2": "o1"}),
        ("必答为空串", {"q1": "", "q2": "o1"}),
        ("类型错误（单选传数组）", {"q1": ["o1"], "q2": "o1"}),
        ("多选重复取值", {"q1": "o1", "q2": "o1", "q4": ["o1", "o1"]}),
        ("多选传字符串", {"q1": "o1", "q2": "o1", "q4": "o1"}),
        ("简答超长", {"q1": "o1", "q2": "o1", "q3": "长" * 1001}),
    ],
)
def test_survey_validation_rejects_bad_answers(app_env, published, label, answers):
    """T05：非法答案 422 且不入库；服务器按已发布题目校验，不依赖前端。"""
    response = app_env.submit(published["token"], answers)
    assert response.status_code == 422, (
        f"{label} 应被拒绝，实际 HTTP {response.status_code} {response.text[:300]}"
    )
    assert error_code(response) == "VALIDATION_ERROR", f"{label} 错误码不符"

    listing = data_of(app_env.admin.get(f"/api/experiments/{published['experiment_id']}/responses"))
    assert listing == [], f"{label} 被拒绝后不应留下记录：{listing}"


def test_survey_rejects_missing_consent(app_env, published):
    """总纲 §8：不同意知情说明就不能提交，也不应入库。"""
    response = app_env.submit(published["token"], {"q1": "o1", "q2": "o1"}, consent=False)
    assert response.status_code in (400, 422), (
        f"consent=false 应被拒绝，实际 {response.status_code}"
    )
    assert error_code(response) in ("INVALID_STATE", "VALIDATION_ERROR")
    listing = data_of(app_env.admin.get(f"/api/experiments/{published['experiment_id']}/responses"))
    assert listing == []


def test_valid_three_type_submission_is_stored(app_env, published):
    """T05：single / multiple / text 三类题都可提交，并可按 ID 查回。"""
    response = app_env.submit(published["token"], SAMPLE_RESPONSES[0])
    assert response.status_code == 201, response.text[:400]
    created = data_of(response)
    assert created["duplicate"] is False
    assert isinstance(created["response_id"], int), "回答 ID 必须是整数（总纲 §6）"
    assert created["submitted_at"], "必须返回提交时间"

    listing = data_of(app_env.admin.get(f"/api/experiments/{published['experiment_id']}/responses"))
    assert len(listing) == 1, f"应恰好保存一条回答：{listing}"
    stored = listing[0]
    assert stored["id"] == created["response_id"]
    assert stored["answers"]["q1"] == "o1"
    assert stored["answers"]["q2"] == "o1"
    assert stored["answers"]["q4"] == ["o1", "o2"], "多选必须按契约保存为数组"
    assert stored["answers"]["q3"] == SAMPLE_RESPONSES[0]["q3"]


# --------------------------------------------------------------------------
# T06 幂等
# --------------------------------------------------------------------------


def test_submission_idempotency_and_conflict(app_env, published):
    """T06：同 UUID 同内容 200 不增数；同 UUID 不同内容 409。"""
    submission_id = str(uuid.uuid4())
    answers = SAMPLE_RESPONSES[0]
    experiment_id = published["experiment_id"]

    first = app_env.submit(published["token"], answers, submission_id=submission_id)
    assert first.status_code == 201, first.text[:400]
    first_id = data_of(first)["response_id"]

    replay = app_env.submit(published["token"], answers, submission_id=submission_id)
    assert replay.status_code == 200, (
        f"同 UUID 同内容的重放应 200，实际 {replay.status_code} {replay.text[:300]}"
    )
    replayed = data_of(replay)
    assert replayed["duplicate"] is True, "重放必须标 duplicate=true"
    assert replayed["response_id"] == first_id, "重放必须返回原记录 ID"

    listing = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}/responses"))
    assert len(listing) == 1, f"重放不得新增回答：{len(listing)}"

    conflict = app_env.submit(
        published["token"], {"q1": "o2", "q2": "o2"}, submission_id=submission_id
    )
    assert conflict.status_code == 409, (
        f"同 UUID 不同内容应 409，实际 {conflict.status_code}"
    )
    assert error_code(conflict) == "IDEMPOTENCY_CONFLICT"

    listing = data_of(app_env.admin.get(f"/api/experiments/{experiment_id}/responses"))
    assert len(listing) == 1, "冲突请求不得写库"


# --------------------------------------------------------------------------
# T12 公开隔离
# --------------------------------------------------------------------------


def test_public_pages_do_not_leak_management_data(app_env, published_with_sample):
    """T12：公开页不含其他人的原始回答、日志、密钥或管理链接。"""
    experiment_id = published_with_sample["experiment_id"]
    secret_text = SAMPLE_RESPONSES[0]["q3"]

    for url in (published_with_sample["service_url"], published_with_sample["survey_url"]):
        page = app_env.anon.get(url)
        assert page.status_code == 200, page.text[:300]
        body = page.text
        assert secret_text not in body, f"公开页泄露了原始文字回答：{url}"
        assert "make_validation_plan" not in body, f"公开页泄露了工具日志：{url}"
        assert "ADMIN_PASSWORD" not in body and "LLM_API_KEY" not in body

    for url in (
        f"/api/experiments/{experiment_id}/responses",
        f"/api/experiments/{experiment_id}/statistics",
        f"/api/projects/{published_with_sample['project_id']}/report",
        f"/api/projects/{published_with_sample['project_id']}/runs",
    ):
        response = app_env.anon.get(url)
        assert response.status_code == 401, f"{url} 未登录应 401，实际 {response.status_code}"
