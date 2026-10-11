"""一条完整的浏览器链路（成员C指南 C04，计划 10/12 上午执行）。

链路：创建项目 → 生成方案与草稿 → 编辑保存 → 人工发布 → 复制公开问卷链接
      → 在**独立的未登录浏览器上下文**中填写并提交 → 管理端看统计 → 复盘 → 下载 Markdown。

运行前提（缺任一项本文件整体 skip，不会让核心用例变红）：
  1. ``pip install playwright`` 且 ``playwright install chromium``；
  2. 已用**独立 test 数据库**启动服务：
     ``$env:DATABASE_PATH="data/e2e.db"; python -m uvicorn app.main:app --port 8000``
  3. 环境变量与启动配置一致：
     ``FO_LAB_E2E_BASE_URL``（如 http://127.0.0.1:8000）、``ADMIN_USER``、``ADMIN_PASSWORD``。

选择器只使用成员B指南第 5 节承诺的 ``data-testid``，不依赖颜色、中文文案位置或
CSS 类名；模型生成的文案不逐字断言。管理上下文用 Playwright 的
``http_credentials`` 完成 HTTP Basic，公开上下文不携带任何管理凭据。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest

BASE_URL = os.environ.get("FO_LAB_E2E_BASE_URL", "").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ARTIFACT_DIR = Path(os.environ.get("FO_LAB_E2E_ARTIFACTS", "evidence/e2e"))

if os.environ.get("FO_LAB_E2E_MOCKED") != "1":
    pytest.skip("完整 UI 骨架只允许隔离 test 服务且模型已 Mock；报告页面仍待 B 交付", allow_module_level=True)

#: B 指南第 5 节的 data-testid 没有覆盖草稿编辑表单的具体输入控件。
#: B04 落地后由 C04 按真实表单属性补齐这一行，不要靠猜中文 label。
LANDING_TITLE_INPUT = "input[name='title']"

sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="未安装 Playwright；按成员C指南 C04 安装后再跑 tests/e2e",
)
expect = sync_api.expect

pytestmark = pytest.mark.e2e

if not BASE_URL:
    pytest.skip(
        "未设置 FO_LAB_E2E_BASE_URL；本文件需要已启动的服务（见文件头部说明）",
        allow_module_level=True,
    )
if not (ADMIN_USER and ADMIN_PASSWORD):
    pytest.skip(
        "未设置 ADMIN_USER / ADMIN_PASSWORD；本文件需要与启动配置一致的管理凭据",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def run_artifacts() -> Path:
    directory = ARTIFACT_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture
def admin_context(browser):
    """管理端上下文：由 Playwright 处理 HTTP Basic 挑战。"""
    context = browser.new_context(
        base_url=BASE_URL,
        http_credentials={"username": ADMIN_USER, "password": ADMIN_PASSWORD},
    )
    context.set_default_timeout(15_000)
    try:
        yield context
    finally:
        context.close()


@pytest.fixture
def public_context(browser):
    """公开上下文：不带任何管理凭据，模拟真实试用者。"""
    context = browser.new_context(base_url=BASE_URL)
    context.set_default_timeout(15_000)
    try:
        yield context
    finally:
        context.close()


def _project_id_from(page) -> str:
    assert "/projects/" in page.url, f"创建后未跳转到工作台：{page.url}"
    return page.url.split("/projects/", 1)[1].split("/")[0].split("?")[0]


def test_full_browser_flow(admin_context, public_context, run_artifacts):
    """T11：从新建项目到下载报告，在真实浏览器里完整跑通一次。"""
    page = admin_context.new_page()
    unique = uuid.uuid4().hex[:8]

    # 1. 创建项目（data_kind 必须选 test，避免录屏数据混入真实需求样本）
    page.goto("/projects/new")
    page.get_by_test_id("project-form").locator("input[name='name']").fill(
        f"C04 E2E 校园报名服务 {unique}"
    )
    page.get_by_test_id("project-idea").fill("为校园社团制作低成本活动报名网页，先验证需求。")
    page.get_by_test_id("project-form").locator("select[name='data_kind']").select_option("test")
    page.get_by_test_id("project-create").click()
    page.wait_for_url("**/projects/*")
    project_id = _project_id_from(page)

    # 2. 生成验证方案与草稿：等运行日志出现两个真实工具名，不使用固定 sleep
    page.get_by_test_id("plan-generate").click()
    run_log = page.get_by_test_id("run-log")
    expect(run_log).to_contain_text("制定验证计划", timeout=90_000)
    expect(run_log).to_contain_text("创建实验草稿", timeout=90_000)

    # 3. 从详情 API 取 experiment_id，进入草稿编辑并保存一处文字修改
    detail = page.request.get(f"{BASE_URL}/api/projects/{project_id}").json()["data"]
    assert detail["experiment"] is not None, "生成后详情里必须有实验草稿"
    experiment_id = detail["experiment"]["id"]

    page.goto(f"/experiments/{experiment_id}/edit")
    title_input = page.locator(LANDING_TITLE_INPUT).first
    title_input.fill(f"校园活动报名网页服务 {unique}")
    page.get_by_test_id("experiment-save").click()
    expect(page.get_by_test_id("experiment-save")).to_be_enabled(timeout=15_000)

    # 4. 人工发布，并取到真实公开问卷链接
    page.goto(f"/projects/{project_id}")
    page.get_by_test_id("experiment-publish").click()
    survey_url = page.get_by_test_id("survey-url").inner_text().strip()
    assert "/e/" in survey_url, f"发布后未显示公开链接：{survey_url!r}"
    assert survey_url.startswith(BASE_URL), (
        f"公开链接应使用受控的 PUBLIC_BASE_URL：{survey_url!r}"
    )

    # 5. 未登录上下文填写三类题并提交。
    #    题号与选项号由后端数据决定（真实模型不保证叫 q1..q4），因此从 API 读，
    #    不写死在用例里 —— B 的 data-testid 形如 question-{question_id}。
    experiment_json = page.request.get(f"{BASE_URL}/api/experiments/{experiment_id}").json()["data"]
    questions = experiment_json["questions"]
    required_singles = [q for q in questions if q["type"] == "single" and q["required"]]
    text_question = next(q for q in questions if q["type"] == "text")
    multiple_question = next(q for q in questions if q["type"] == "multiple")
    assert len(required_singles) >= 2, (
        "夹具要求至少两个必答单选；真实模型题号不同不影响本用例，但题型构成不符即失败"
    )

    visitor = public_context.new_page()
    visitor.goto(survey_url)
    visitor.get_by_test_id("survey-consent").check()
    for question in required_singles[:2]:
        first_option = question["options"][0]
        visitor.get_by_test_id(f"question-{question['id']}").get_by_test_id(
            f"option-{question['id']}-{first_option['id']}"
        ).check()
    visitor.get_by_test_id(f"question-{text_question['id']}").locator("textarea").fill(
        "报名信息散在聊天记录里"
    )
    multiple_option = multiple_question["options"][0]
    visitor.get_by_test_id(f"question-{multiple_question['id']}").get_by_test_id(
        f"option-{multiple_question['id']}-{multiple_option['id']}"
    ).check()
    visitor.get_by_test_id("survey-submit").click()
    expect(visitor.get_by_test_id("submission-success")).to_be_visible(timeout=30_000)
    visitor.screenshot(path=str(run_artifacts / "visitor-submitted.png"), full_page=True)

    # 6. 管理端统计出现这 1 份回答
    page.goto(f"/projects/{project_id}")
    expect(page.get_by_test_id("response-count")).to_contain_text("1", timeout=30_000)

    # 7. 复盘
    page.get_by_test_id("review-generate").click()
    expect(page.get_by_test_id("report-content")).to_be_visible(timeout=90_000)

    # 8. 下载 Markdown，确认文件非空且与页面报告同源
    with page.expect_download() as download_info:
        page.get_by_test_id("report-download").click()
    download = download_info.value
    target = run_artifacts / (download.suggested_filename or "report.md")
    download.save_as(target)
    assert target.exists() and target.stat().st_size > 0, "下载的 Markdown 为空"
    markdown = target.read_text(encoding="utf-8")
    assert "1" in markdown, "报告应写明本次快照的回答份数"
    assert "test" in markdown.lower() or "测试" in markdown, (
        "报告必须标明数据来源是 test，避免录屏数据被当作真实试点"
    )

    page.screenshot(path=str(run_artifacts / "admin-report.png"), full_page=True)
