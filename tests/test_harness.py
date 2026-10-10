"""C 自有测试脚手架的自我检查（不依赖 A 的后端）。

A 的 ``app/`` 还没交付时，``test_api.py`` / ``test_statistics.py`` / ``test_workflow.py``
只能以明确原因 skip。为了证明这套脚手架本身是**可执行且正确**的，而不是一份写完就没人跑过的
空壳，这里针对 C 自己写的部分做检查：

* ``FakeLLM`` 的计划/复盘自动识别、显式脚本、故障注入、参数容忍；
* ``_patch_llm`` 对 ``from ... import <seam>`` 这种直接绑定的重绑（这是最容易写错的一处）；
* ``Client`` 请求头合并与裁剪；
* ``token_from_url`` / ``error_code`` / ``data_of`` 的解析；
* ``read_public_token`` / ``insert_running_run`` 对总纲 §4 表结构的读写。

这些用例不碰 A 的代码，也不需要模型或网络，任何环境都应全绿。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import types

import httpx
import pytest

from conftest import (
    LLM_SEAM_CANDIDATES,
    PLAN_FIXTURE,
    REVIEW_FIXTURE,
    Client,
    FakeLLM,
    _patch_llm,
    data_of,
    error_code,
    insert_running_run,
    read_public_token,
    token_from_url,
)


# --------------------------------------------------------------------------
# FakeLLM
# --------------------------------------------------------------------------


def test_fake_llm_auto_selects_plan_or_review_by_prompt():
    llm = FakeLLM()
    plan_text = llm("你是需求验证助手，请输出 hypotheses、experiment_steps 等字段。", {"idea": "x"})
    review_text = llm("请输出 hypothesis_assessments、limitations、next_actions。", {"stats": {}})

    assert json.loads(plan_text)["hypotheses"] == PLAN_FIXTURE["hypotheses"]
    assert json.loads(review_text)["hypothesis_assessments"] == REVIEW_FIXTURE["hypothesis_assessments"]
    assert llm.call_count == 2
    assert [call["index"] for call in llm.calls] == [0, 1], "调用应被编号，便于断言调用次数"


def test_fake_llm_honours_explicit_queue_and_injected_failure():
    llm = FakeLLM()
    failure = httpx.TimeoutException("mock timeout")
    llm.queue = ["这不是 JSON", failure]

    assert llm("计划提示词", {}) == "这不是 JSON", "显式脚本必须优先于自动识别"
    with pytest.raises(httpx.TimeoutException):
        llm("计划提示词", {})
    assert llm("计划提示词", {}) == llm.plan_text, "脚本用尽后回到自动识别"
    assert llm.call_count == 3


def test_fake_llm_tolerates_positional_and_keyword_call_shapes():
    llm = FakeLLM()
    llm(system_prompt="计划", data={"a": 1})
    llm("计划", {"a": 2})
    llm(prompt="计划", payload={"a": 3})

    payloads = [call["payload"] for call in llm.calls]
    assert payloads == [{"a": 1}, {"a": 2}, {"a": 3}], payloads
    assert all(call["system_prompt"] for call in llm.calls)


# --------------------------------------------------------------------------
# _patch_llm
# --------------------------------------------------------------------------


@pytest.fixture
def fake_app_package():
    """临时安装一个假的 ``app.services`` 包，测完恢复原状。

    只用于验证覆盖逻辑本身：如果 A 的真实包已经在 ``sys.modules`` 里，
    这里会先保存、结束后还原，不会污染其他用例。
    """
    saved = {
        name: sys.modules.get(name)
        for name in ("app", "app.services", "app.services.llm_client", "app.services.workflow")
    }

    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = []  # 标记为包，避免 importlib 去磁盘找子模块
    services_pkg = types.ModuleType("app.services")
    services_pkg.__path__ = []
    llm_module = types.ModuleType("app.services.llm_client")
    workflow_module = types.ModuleType("app.services.workflow")

    def call_model(system_prompt, data=None):
        return "真实实现尚未交付"

    llm_module.call_model = call_model
    # 模拟 ``from app.services.llm_client import call_model`` 造成的直接绑定
    workflow_module.call_model = call_model

    app_pkg.services = services_pkg
    services_pkg.llm_client = llm_module
    services_pkg.workflow = workflow_module
    sys.modules.update(
        {
            "app": app_pkg,
            "app.services": services_pkg,
            "app.services.llm_client": llm_module,
            "app.services.workflow": workflow_module,
        }
    )
    try:
        yield llm_module, workflow_module
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_patch_llm_rebinds_direct_imports_too(monkeypatch, fake_app_package):
    """``from ... import call_model`` 会让模块级绑定脱离原模块，必须一并替换。"""
    llm_module, workflow_module = fake_app_package
    fake = FakeLLM()

    seam = _patch_llm(monkeypatch, fake)

    assert seam == "call_model", f"应命中候选表里的第一个可用名字：{LLM_SEAM_CANDIDATES}"
    assert llm_module.call_model is fake, "模块属性必须被替换"
    assert workflow_module.call_model is fake, "直接导入的绑定也必须被替换"
    assert json.loads(workflow_module.call_model("hypotheses 计划", {}))["target_customer"] == (
        PLAN_FIXTURE["target_customer"]
    ), "被重绑后的调用应当真的走到假实现"


def test_patch_llm_reports_missing_seam(monkeypatch, fake_app_package):
    """A 改了模型函数名时，应给出可读的失败信息而不是静默跳过。"""
    llm_module, _ = fake_app_package
    del llm_module.call_model

    with pytest.raises(BaseException) as excinfo:
        _patch_llm(monkeypatch, FakeLLM())
    assert "llm_client" in str(excinfo.value)


# --------------------------------------------------------------------------
# Client 请求头包装
# --------------------------------------------------------------------------


class _RecordingTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.seen: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(dict(request.headers))
        return httpx.Response(200, json={"data": {}})


def test_client_wrapper_merges_defaults_and_drops_selected_headers():
    transport = _RecordingTransport()
    raw = httpx.Client(transport=transport, base_url="http://testserver")
    client = Client(raw, {"Origin": "http://testserver", "X-Requested-With": "FirstOrderLab"})

    client.get("/api/projects")
    client.variant(drop=("X-Requested-With",)).post("/api/projects", json={})
    client.post("/api/projects", json={}, headers={"X-Requested-With": "Other"})

    first, second, third = transport.seen
    assert first["origin"] == "http://testserver"
    assert first["x-requested-with"] == "FirstOrderLab"
    assert "x-requested-with" not in second, "variant 必须真的去掉指定请求头"
    assert third["x-requested-with"] == "Other", "单次请求传入的头应覆盖默认值"


# --------------------------------------------------------------------------
# 解析辅助
# --------------------------------------------------------------------------


def test_token_from_url_extracts_public_token():
    assert token_from_url("http://testserver/e/abc123") == "abc123"
    assert token_from_url("http://192.168.1.23:8000/e/abc123/survey") == "abc123"
    assert token_from_url("https://example.com/e/abc123?from=qr") == "abc123"
    with pytest.raises(AssertionError):
        token_from_url("/projects/xyz")


class _FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def test_error_code_and_data_of_read_contract_wrappers():
    assert error_code(_FakeResponse(409, {"error": {"code": "RUN_IN_PROGRESS", "message": "x"}})) == (
        "RUN_IN_PROGRESS"
    )
    assert data_of(_FakeResponse(200, {"data": {"project_id": "p1"}})) == {"project_id": "p1"}
    with pytest.raises(AssertionError):
        error_code(_FakeResponse(500, {"detail": "boom"}))
    with pytest.raises(AssertionError):
        data_of(_FakeResponse(200, {"project_id": "p1"}))


# --------------------------------------------------------------------------
# 直接读写契约表的辅助函数
# --------------------------------------------------------------------------


def _create_contract_tables(db_path) -> None:
    connection = sqlite3.connect(str(db_path))
    try:
        connection.executescript(
            """
            CREATE TABLE experiments (
                id TEXT PRIMARY KEY,
                public_token TEXT NOT NULL UNIQUE
            );
            CREATE TABLE agent_runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error_code TEXT,
                error_message TEXT
            );
            CREATE TABLE tool_calls (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                input_summary_json TEXT,
                output_ref_json TEXT,
                error_code TEXT
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_read_public_token_requires_128_bit_token(tmp_path):
    db_path = tmp_path / "contract.db"
    _create_contract_tables(db_path)
    token = "a" * 32  # 32 个字符 ≈ 128 位随机量

    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            "INSERT INTO experiments (id, public_token) VALUES (?, ?)", ("exp-1", token)
        )
        connection.commit()
    finally:
        connection.close()

    assert read_public_token(db_path, "exp-1") == token

    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute(
            "INSERT INTO experiments (id, public_token) VALUES (?, ?)", ("exp-2", "short")
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(AssertionError):
        read_public_token(db_path, "exp-2")


def test_insert_running_run_writes_run_and_tool_rows(tmp_path):
    db_path = tmp_path / "contract.db"
    _create_contract_tables(db_path)

    insert_running_run(db_path, "proj-1", "run-1")

    connection = sqlite3.connect(str(db_path))
    try:
        run = connection.execute(
            "SELECT project_id, action, status, error_code FROM agent_runs WHERE id='run-1'"
        ).fetchone()
        tool = connection.execute(
            "SELECT run_id, tool_name, status FROM tool_calls WHERE run_id='run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert run == ("proj-1", "plan", "running", None)
    assert tool == ("run-1", "make_validation_plan", "running")
