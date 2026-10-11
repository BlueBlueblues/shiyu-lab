"""Real Edge E2E against a temporary database and localhost Mock model only.

Run: FO_LAB_E2E_MOCKED=1 python -m pytest tests/c_integration/e2e -q
Requires Playwright and installed Microsoft Edge; no external service or credentials.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

if os.environ.get("FO_LAB_E2E_MOCKED") != "1":
    pytest.skip("Set FO_LAB_E2E_MOCKED=1 for isolated Mock Edge E2E", allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[3]
PASSWORD = "isolated-browser-test-password"


@pytest.fixture
def site(tmp_path):
    plan = json.loads((ROOT / "tests/c_integration/fixtures/plan.json").read_text(encoding="utf-8"))
    review = json.loads((ROOT / "tests/c_integration/fixtures/review.json").read_text(encoding="utf-8"))
    calls = []
    gate, entered = threading.Event(), threading.Event()
    gate.set()

    class MockModel(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            data = json.loads(payload["messages"][-1]["content"])
            reviewing = "statistics" in data
            calls.append("review" if reviewing else "plan")
            entered.set()
            if not gate.wait(20):
                self.send_error(504, "Controlled model gate timed out")
                return
            body = json.dumps({"choices": [{"message": {"content": json.dumps(
                review if reviewing else plan, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
    worker = threading.Thread(target=mock.serve_forever, daemon=True)
    worker.start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    database = tmp_path / "browser.sqlite3"
    env = dict(os.environ, APP_ENV="test", DATABASE_PATH=str(database), PUBLIC_BASE_URL=base,
               ADMIN_USER="e2e-admin", ADMIN_PASSWORD=PASSWORD,
               LLM_BASE_URL=f"http://127.0.0.1:{mock.server_port}/v1",
               LLM_API_KEY="mock-not-a-real-key", LLM_MODEL="mock-e2e")
    artifact = ROOT / "evidence/e2e" / tmp_path.name
    artifact.mkdir(parents=True, exist_ok=True)
    try:
        with (artifact / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host",
                "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=log,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    assert process.poll() is None, (artifact / "server.log").read_text(encoding="utf-8")
                    try:
                        with urlopen(base + "/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.1)
                else:
                    pytest.fail("Isolated server did not start")
                with sync_api.sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge", headless=True)
                    try:
                        yield SimpleNamespace(base=base, browser=browser, calls=calls, gate=gate,
                            entered=entered, plan=plan, database=database, artifact=artifact)
                    finally:
                        browser.close()
            finally:
                gate.set()
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                (artifact / "model-calls.json").write_text(json.dumps(calls), encoding="utf-8")
    finally:
        mock.shutdown()
        mock.server_close()
        worker.join(timeout=5)


def register_and_login(page, username):
    page.goto("/register")
    page.locator("[name=username]").fill(username)
    page.locator("[name=password]").fill(PASSWORD)
    page.locator("[name=password_confirmation]").fill(PASSWORD)
    # Email remains optional; no invitation or verification code dependency.
    assert page.locator("[name=invitation], [name=verification_code]").count() == 0
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url("**/login?registered=1")
    page.locator("[name=username]").fill(username)
    page.locator("[name=password]").fill(PASSWORD)
    page.get_by_test_id("auth-submit").click()
    page.wait_for_url("**/projects")
    expect(page.get_by_test_id("user-nav")).to_contain_text(username)


def create_project(page, name="Isolated UX E2E"):
    page.goto("/projects/new")
    for key, value in dict(name=name, idea="Validate campus sign-up needs", target_users="Students",
                           skills="Web development", validation_goal="Test demand").items():
        page.locator(f"[name={key}]").fill(value)
    page.locator("[name=data_kind]").select_option("test")
    page.get_by_test_id("project-create").click()
    expect(page.get_by_test_id("ws-stage-guide")).to_be_visible()
    return page.url.split("/projects/")[1].split("#")[0]


def detail(page, project):
    response = page.request.get(f"/api/projects/{project}")
    assert response.status == 200
    return response.json()["data"]


def click_stage(page, anchor):
    page.locator(f".ws-step[href='{anchor}']").click()
    expect(page).to_have_url(re.compile(re.escape(anchor) + "$"))
    assert page.locator(anchor).evaluate("el => {const r=el.getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight;}")


def assert_no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.url


def submit_feedback(page, url, questions):
    page.goto(url)
    page.get_by_test_id("survey-consent").check()
    for question in questions:
        group = page.get_by_test_id(f"question-{question['id']}")
        if question["type"] == "text":
            group.locator("textarea").fill("Controlled test feedback only")
        else:
            options = question["options"][:2] if question["type"] == "multiple" else question["options"][:1]
            for option in options:
                group.get_by_test_id(f"option-{question['id']}-{option['id']}").check()
    page.get_by_test_id("survey-submit").click()
    expect(page.get_by_test_id("submission-success")).to_be_visible()


def test_full_browser_flow(site):
    owner = site.browser.new_context(base_url=site.base, accept_downloads=True)
    other = site.browser.new_context(base_url=site.base)
    public = site.browser.new_context(base_url=site.base)
    owner.tracing.start(screenshots=True, snapshots=True)
    errors, writes, baseline_console = [], [], []
    def console_message(message):
        if message.type != "error":
            return
        url = message.location.get("url", "")
        # These two diagnostics already exist in the untouched 32e3017 templates.
        known = (url == site.base + "/favicon.ico" and message.text ==
                 "Failed to load resource: the server responded with a status of 404 (Not Found)") or (
                 message.text.startswith("Pattern attribute value [A-Za-z][A-Za-z0-9_-]* is not a valid regular expression:")
                 and url.startswith(site.base + "/register"))
        (baseline_console if known else errors).append({"message": message.text, "url": url})
    try:
        page, stranger, visitor = owner.new_page(), other.new_page(), public.new_page()
        for tab in (page, stranger, visitor):
            tab.on("pageerror", lambda error: errors.append(str(error)))
            tab.on("console", console_message)
        page.on("request", lambda request: writes.append(request.url) if request.method != "GET" else None)
        page.on("dialog", lambda dialog: dialog.accept())
        register_and_login(page, "ux_owner")
        cookies = owner.cookies()
        assert len(cookies) == 1 and cookies[0]["httpOnly"] and cookies[0]["sameSite"] == "Lax"
        project = create_project(page)
        workspace = f"/projects/{project}"
        expect(page.get_by_test_id("ws-stage-guide")).to_contain_text("第 2 阶段")
        before = len(writes)
        for anchor in ("#project-overview", "#validation-plan", "#feedback", "#review"):
            click_stage(page, anchor)
        assert len(writes) == before and site.calls == []
        page.goto(workspace + "/report")
        expect(page.get_by_test_id("report-empty")).to_be_visible()
        expect(page.get_by_test_id("report-download")).to_be_disabled()
        page.get_by_test_id("report-back-cta").click()
        expect(page).to_have_url(site.base + workspace + "#project-actions")

        page.get_by_test_id("plan-generate").click()
        expect(page.get_by_test_id("experiment-publish")).to_be_enabled(timeout=20000)
        experiment = detail(page, project)["experiment"]
        exp = experiment["id"]
        page.goto(f"/experiments/{exp}/edit")
        expect(page.get_by_test_id("draft-step-hint")).to_contain_text("第 2 步")
        page.locator("[name=title]").fill("Edited isolated draft")
        page.get_by_test_id("experiment-save").click()
        expect(page.get_by_test_id("experiment-status")).to_contain_text("保存")
        expect(page.get_by_test_id("experiment-save")).to_be_enabled()
        assert detail(page, project)["experiment"]["landing"]["title"] == "Edited isolated draft"
        for suffix in ("service", "survey"):
            page.goto(f"/experiments/{exp}/preview/{suffix}")
            assert page.locator("body").inner_text()
            assert_no_overflow(page)
        page.goto(f"/experiments/{exp}/edit")
        page.get_by_test_id("draft-back").click()
        expect(page).to_have_url(site.base + workspace + "#experiment-draft")
        page.get_by_test_id("experiment-publish").click()
        expect(page.get_by_test_id("survey-url")).to_be_visible()
        expect(page.get_by_test_id("ws-stage-guide")).to_contain_text("第 3 阶段")
        survey = page.get_by_test_id("survey-url").input_value()
        service = page.get_by_test_id("service-url").input_value()
        assert survey == detail(page, project)["experiment"]["survey_url"]
        for kind, url in (("service", service), ("survey", survey)):
            with page.expect_popup() as popup:
                page.get_by_test_id(f"open-{kind}-url").click()
            opened = popup.value
            opened.wait_for_load_state()
            assert opened.url == url
            assert opened.evaluate("window.opener === null")
            opened.close()
            page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text => {window.copied=text;}}})")
            page.get_by_test_id(f"copy-{kind}-url").click()
            expect(page.get_by_test_id(f"copy-{kind}-status")).to_contain_text("已复制")
            assert page.evaluate("window.copied") == url
            for clipboard in ("undefined", "{writeText:()=>Promise.reject(new Error('denied'))}"):
                page.evaluate(f"Object.defineProperty(navigator,'clipboard',{{configurable:true,value:{clipboard}}})")
                page.get_by_test_id(f"copy-{kind}-url").click()
                expect(page.get_by_test_id(f"copy-{kind}-status")).to_contain_text("Ctrl+C")
                assert page.get_by_test_id(f"{kind}-url").evaluate("el => document.activeElement===el && el.selectionStart===0 && el.selectionEnd===el.value.length")
            page.get_by_test_id(f"{kind}-url").evaluate("el => {el.originalSelect=el.select; el.select=()=>{throw new Error('selection blocked');};}")
            page.get_by_test_id(f"copy-{kind}-url").click()
            expect(page.get_by_test_id(f"copy-{kind}-status")).to_contain_text("无法自动选中")
            page.get_by_test_id(f"{kind}-url").evaluate("el => {el.select=el.originalSelect;}")
        for width in (320, 375, 430):
            page.set_viewport_size({"width": width, "height": 800})
            assert_no_overflow(page)
        page.screenshot(path=str(site.artifact / "workspace-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"/experiments/{exp}/edit")
        assert page.locator("[name=title]").get_attribute("readonly") is not None
        assert page.get_by_test_id("experiment-save").count() == 0
        page.get_by_test_id("draft-back").click()
        submit_feedback(visitor, survey, site.plan["questions"])
        visitor.reload()
        expect(visitor.get_by_test_id("submission-success")).to_be_visible()
        expect(visitor.get_by_test_id("survey-submit")).to_be_disabled()
        page.goto(workspace)
        expect(page.get_by_test_id("response-count")).to_have_text("有效回答份数：1 份")
        stats = page.request.get(f"/api/experiments/{exp}/statistics").json()["data"]
        multiple = next(question for question in stats["questions"] if question["type"] == "multiple")
        assert multiple["answered_count"] == 1
        assert [option["count"] for option in multiple["options"]] == [1, 1, 0, 0]
        assert [option["percentage"] for option in multiple["options"]] == [100, 100, 0, 0]
        page.get_by_test_id("responses-load").click()
        expect(page.get_by_test_id("responses-body")).to_contain_text("Controlled test feedback")
        page.get_by_test_id("review-generate").click()
        expect(page.get_by_test_id("report-content")).to_be_visible(timeout=20000)
        expect(page.get_by_test_id("ws-stage-guide")).to_contain_text("第 4 阶段")
        page.locator(".ws-step[href$='/report']").click()
        expect(page.get_by_test_id("report-response-count")).to_have_text("1")
        expect(page.get_by_test_id("report-data-kind")).to_contain_text("测试")
        with page.expect_download() as download_info:
            page.get_by_test_id("report-download").click()
        download_info.value.save_as(site.artifact / "report.md")
        markdown = (site.artifact / "report.md").read_text(encoding="utf-8")
        assert "1" in markdown and ("test" in markdown or "测试" in markdown)
        for testid, anchor in (("report-back-feedback", "#statistics"), ("report-back-review", "#review")):
            page.goto(workspace + "/report")
            page.get_by_test_id(testid).click()
            expect(page).to_have_url(site.base + workspace + anchor)
        second_public = site.browser.new_context(base_url=site.base)
        try:
            second_visitor = second_public.new_page()
            second_visitor.on("pageerror", lambda error: errors.append(str(error)))
            second_visitor.on("console", console_message)
            submit_feedback(second_visitor, survey, site.plan["questions"])
        finally:
            second_public.close()
        page.goto(workspace)
        expect(page.get_by_test_id("response-count")).to_have_text("有效回答份数：2 份")
        expect(page.get_by_test_id("ws-stage-guide")).to_contain_text("已有新反馈")
        page.get_by_test_id("ws-step-report-stale").click()
        expect(page.get_by_test_id("report-stale")).to_be_visible()
        expect(page.get_by_test_id("report-response-count")).to_have_text("1")
        assert page.request.get(f"/api/projects/{project}/report.md").text() == markdown
        for width in (320, 375, 430):
            page.set_viewport_size({"width": width, "height": 800})
            assert_no_overflow(page)
        page.screenshot(path=str(site.artifact / "report-mobile.png"), full_page=True)
        page.get_by_test_id("report-back-review").click()
        page.get_by_test_id("review-generate").click()
        expect(page.get_by_test_id("ws-stage-guide")).to_contain_text("第 4 阶段", timeout=20000)
        page.get_by_test_id("report-content").click()
        expect(page.get_by_test_id("report-response-count")).to_have_text("2")
        expect(page.get_by_test_id("report-fresh")).to_be_visible()

        register_and_login(stranger, "ux_other")
        assert project not in stranger.content()
        # All management resource kinds remain private; public survey remains accessible.
        runs = page.request.get(f"/api/projects/{project}/runs").json()["data"]
        for path in (workspace, workspace + "/report", f"/experiments/{exp}/edit",
                     f"/experiments/{exp}/preview/service", f"/api/projects/{project}",
                     f"/api/projects/{project}/report", f"/api/projects/{project}/report.md",
                     f"/api/projects/{project}/runs", f"/api/experiments/{exp}",
                     f"/api/experiments/{exp}/statistics", f"/api/experiments/{exp}/responses",
                     f"/api/runs/{runs[0]['id']}"):
            assert stranger.request.get(path).status == 404, path
        before = len(site.calls)
        for path in (f"/api/projects/{project}/plan", f"/api/projects/{project}/review", f"/api/experiments/{exp}/publish"):
            response = stranger.request.post(path, data={}, headers={"Origin": site.base, "X-Requested-With": "FirstOrderLab"})
            assert response.status == 404, path
        assert len(site.calls) == before
        page.goto(workspace)
        page.get_by_test_id("auth-logout").click()
        expect(page).to_have_url(site.base + "/")
        assert owner.cookies() == []
        page.goto(workspace)
        page.wait_for_url("**/login")
        assert page.request.get(f"/api/projects/{project}").status == 401
        with sqlite3.connect(site.database) as db:
            assert db.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM projects WHERE data_kind='test'").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert site.calls == ["plan", "review", "review"]
        assert not any("/auth/register/code" in url for url in writes)
        # Expected permission probes use APIRequestContext, not the page console.
        assert not errors, errors
    finally:
        (site.artifact / "console-baseline.json").write_text(json.dumps(baseline_console, ensure_ascii=False, indent=2), encoding="utf-8")
        (site.artifact / "console-unexpected.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
        owner.tracing.stop(path=str(site.artifact / "trace.zip"))
        for context in (owner, other, public):
            context.close()


def test_running_lock_and_zero_feedback_report(site):
    context = site.browser.new_context(base_url=site.base)
    try:
        page, waiting = context.new_page(), context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        register_and_login(page, "ux_running")
        project = create_project(page, "Running lock test")
        workspace = f"/projects/{project}"
        site.gate.clear()
        page.get_by_test_id("plan-generate").click()
        assert site.entered.wait(5), "Mock model was not reached"
        waiting.goto(workspace)
        expect(waiting.get_by_test_id("ws-stage-status")).to_contain_text("等待")
        expect(waiting.get_by_test_id("plan-generate")).to_be_disabled()
        expect(waiting.get_by_test_id("review-generate")).to_be_disabled()
        for anchor in ("#project-overview", "#validation-plan", "#feedback", "#review"):
            click_stage(waiting, anchor)
        assert site.calls == ["plan"]
        site.gate.set()
        expect(page.get_by_test_id("experiment-publish")).to_be_enabled(timeout=20000)
        page.get_by_test_id("experiment-publish").click()
        expect(page.get_by_test_id("survey-url")).to_be_visible()
        site.entered.clear()
        site.gate.clear()
        page.get_by_test_id("review-generate").click()
        assert site.entered.wait(5)
        exp = detail(waiting, project)["experiment"]["id"]
        waiting.goto(f"/experiments/{exp}/edit")
        assert waiting.locator("[name=title]").get_attribute("readonly") is not None
        waiting.goto(workspace + "/report")
        expect(waiting.get_by_test_id("report-running")).to_contain_text("尚无已保存")
        expect(waiting.get_by_test_id("report-download")).to_be_disabled()
        site.gate.set()
        expect(page.get_by_test_id("report-content")).to_be_visible(timeout=20000)
        page.get_by_test_id("report-content").click()
        expect(page.get_by_test_id("report-response-count")).to_have_text("0")
        expect(page.get_by_test_id("review-summary")).to_contain_text("证据不足")
        assert site.calls == ["plan", "review"]
    finally:
        site.gate.set()
        context.close()
