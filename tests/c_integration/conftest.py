"""FirstOrder Lab 核心测试的公共夹具（成员 C 所有）。

契约来源：``docs/tasks/01_项目总任务大纲.md``
  §3.2 目录与文件所有权、§4 六张表、§5 状态与失败行为、
  §6 HTTP 接口、§7.3 真实回答、§7.4 统计对象、§7.5 复盘输出。

设计约束（成员C指南 C02）
  1. 每个测试分配独立的临时 SQLite，绝不触碰真实数据库；
  2. 覆盖模型调用函数，使用总纲格式的固定 plan / review 夹具；
  3. fixture 项目一律 ``data_kind=test``，含 single/multiple/text 三类题；
  4. 管理端请求带合法 Origin 与 ``X-Requested-With``，公开提交不带管理员认证；
  5. 不逐字断言模型的市场结论，只断言契约要求的结构、状态和数字。

后端交付前（A01/A02 未落地时）本套用例会以明确原因 skip，不会伪装成通过；
设置 ``FO_LAB_REQUIRE_BACKEND=1`` 可把“后端缺失”变成硬失败。
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import json
import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# --------------------------------------------------------------------------
# 固定值：与总纲 §6.2 的错误包装、§3.1 的配置名保持一致
# --------------------------------------------------------------------------

ADMIN_USER = "c-test-admin"
ADMIN_PASSWORD = "c-test-password"

# TestClient 的默认 base_url；同源检查与公开链接都以它为基准。
PUBLIC_ORIGIN = "http://testserver"
SITE_HEADER = "X-Requested-With"
SITE_HEADER_VALUE = "FirstOrderLab"

REQUIRE_BACKEND = os.environ.get("FO_LAB_REQUIRE_BACKEND") == "1"

TEST_PROJECT_PAYLOAD = {
    "name": "校园活动报名网页服务（C 自动测试）",
    "idea": "为校园社团制作低成本活动报名网页，先验证是否存在需求。",
    "target_users": "社团负责人、活动组织者",
    "skills": "基础网页开发、AI 辅助编程",
    "budget_cny": 100,
    "validation_goal": "验证是否有试用需求及用户最关心的功能",
    "data_kind": "test",
}

# 成员C指南 C03 的 T07 手算样本（全部为 test 数据）。
# 期望值全部由本文件手算得出，不从被测实现反推。
SAMPLE_RESPONSES = [
    {
        "q1": "o1",
        "q2": "o1",
        "q3": "第一条文字：报名信息散在聊天记录里",
        "q4": ["o1", "o2"],
    },
    {"q1": "o2", "q2": "o1", "q4": ["o1"]},
    {"q1": "o1", "q2": "o2", "q3": "第二条文字：统计人数要手工数", "q4": ["o2"]},
    {"q1": "o2", "q2": "o2"},
]


def load_fixture(name: str) -> dict:
    """读取 ``tests/fixtures`` 下的 JSON 夹具。"""
    with (FIXTURE_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


PLAN_FIXTURE = load_fixture("plan.json")
REVIEW_FIXTURE = load_fixture("review.json")


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def error_code(response) -> str:
    """按总纲 §6 取出 ``error.code``，形状不符时直接失败并打印响应体。"""
    try:
        payload = response.json()
    except Exception:  # pragma: no cover - 响应不是 JSON 时给出可读信息
        pytest.fail(f"响应不是 JSON（HTTP {response.status_code}）：{response.text[:500]}")
    assert isinstance(payload, dict) and "error" in payload, (
        f"错误响应缺少 error 包装（HTTP {response.status_code}）：{payload}"
    )
    assert "code" in payload["error"], f"error 缺少 code：{payload}"
    return payload["error"]["code"]


def data_of(response) -> object:
    """按总纲 §6 取出成功响应的 ``data``。"""
    payload = response.json()
    assert isinstance(payload, dict) and "data" in payload, (
        f"成功响应缺少 data 包装：{payload}"
    )
    return payload["data"]


def token_from_url(url: str) -> str:
    """从 ``/e/{public_token}`` 形式的公开链接里取出 token。"""
    assert isinstance(url, str) and "/e/" in url, f"公开链接形状不符合契约：{url!r}"
    return url.split("/e/", 1)[1].split("/")[0].split("?")[0]


def _purge_app_modules() -> None:
    """把 ``app`` 包从模块缓存里移除，让下一个测试重新读取环境变量。

    总纲 §3.2 把 ``DATABASE_PATH`` 定义为进程配置，因此“每个测试一个临时库”
    要求重新导入应用包，而不是只改环境变量。
    """
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]


def _backend_missing(db_path: Path, exc: BaseException):
    message = (
        "BLOCKED: 找不到可用的后端应用 app.main，本用例无法执行。\n"
        f"  导入错误     : {type(exc).__name__}: {exc}\n"
        "  需要的交付物 : 仓库根目录下的 app/main.py，且暴露名为 app 的 ASGI 对象\n"
        "                 （总纲 §3.2、§6；成员A指南 A01～A02）\n"
        f"  测试库路径   : {db_path}\n"
        "  影响         : T01～T12 的核心用例全部无法执行\n"
        "  记录         : docs/test-results.md\n"
        "  提示         : 设 FO_LAB_REQUIRE_BACKEND=1 可让本情况变成硬失败而不是跳过"
    )
    if REQUIRE_BACKEND:
        pytest.fail(message)
    pytest.skip(message)


# --------------------------------------------------------------------------
# mock 模型
# --------------------------------------------------------------------------


def _contract_payload(value):
    """递归剥离 ``_`` 开头的说明性键。

    夹具文件里用 ``_comment`` 写给人看的说明，但 A 的 ``ContractModel`` 使用
    ``extra="forbid"``（``app/schemas.py:21``），把 ``_comment`` 一起发给被测代码
    会被判为 ``LLM_OUTPUT_INVALID``。2026-10-10 对齐基线 3f79df9 时实测暴露。
    """
    if isinstance(value, dict):
        return {key: _contract_payload(item) for key, item in value.items()
                if not (isinstance(key, str) and key.startswith("_"))}
    if isinstance(value, list):
        return [_contract_payload(item) for item in value]
    return value


class FakeLLM:
    """覆盖 ``app.services.llm_client`` 中唯一模型入口的假实现。

    只替代“模型”这一层：返回夹具文本或抛出故障，其余逻辑仍走 A 的真实代码，
    所以统计、事务、日志、校验都还是被测对象（成员A指南：mock 通过不算真实模型成功）。
    """

    def __init__(self) -> None:
        self.plan_text = json.dumps(_contract_payload(PLAN_FIXTURE), ensure_ascii=False)
        self.review_text = json.dumps(_contract_payload(REVIEW_FIXTURE), ensure_ascii=False)
        self.calls: list[dict] = []
        #: 显式脚本；元素为字符串（当作模型文本）或异常实例（抛给被测代码）。
        self.queue: list[object] = []
        self.lock = threading.Lock()
        #: 置位后模型调用会阻塞，直到 ``hold`` 被 set，用于并发/锁用例。
        self.entered: threading.Event | None = None
        self.hold: threading.Event | None = None
        self.hold_timeout = 30.0

    # -- 调用接口 ---------------------------------------------------------
    def __call__(self, *args, **kwargs):
        """返回**模型文本**（字符串），供脚手架自检与人工阅读。

        被测代码需要 ``ModelReply`` 形状，那层适配在 ``as_model_fn()``；
        两者分开，避免把「假模型产出什么文本」和「如何满足调用方签名」混在一起。
        """
        return self.invoke(*args, **kwargs)

    def as_model_fn(self, seam_name: str | None = None):
        """把 FakeLLM 适配成 A 的统一模型入口签名。

        2026-10-10 对齐基线 3f79df9：A 的 ``call_model(system_prompt, data, settings)``
        返回 ``ModelReply(text=..., call_info=CallInfo(...))``，调用方读取
        ``reply.text`` / ``reply.call_info``。这里用鸭子类型构造同名属性，
        不 import A 的实现，避免与后端耦合。
        """
        def model_fn(*args, **kwargs):
            import httpx
            llm = importlib.import_module("app.services.llm_client")
            try:
                return self._reply(self.invoke(*args, **kwargs))
            except httpx.TimeoutException:
                raise llm.LLMError("LLM_TIMEOUT") from None
            except (httpx.RequestError, httpx.HTTPStatusError):
                raise llm.LLMError("LLM_UPSTREAM_ERROR") from None
        model_fn.__name__ = seam_name or "fake_call_model"
        return model_fn

    # -- 内部 -------------------------------------------------------------
    def invoke(self, *args, **kwargs):
        """真实记录调用并返回模型文本；故障注入时抛出对应异常。"""
        system_prompt, payload = self._split(args, kwargs)
        with self.lock:
            self.calls.append(
                {
                    "index": len(self.calls),
                    "system_prompt": system_prompt,
                    "payload": payload,
                }
            )
            queued = self.queue.pop(0) if self.queue else None

        if self.entered is not None:
            self.entered.set()
        if self.hold is not None:
            self.hold.wait(self.hold_timeout)

        if isinstance(queued, BaseException):
            raise queued
        if isinstance(queued, str):
            return queued
        return self._auto_text(system_prompt)

    @staticmethod
    def _reply(text: str):
        """构造与 A 的统一入口返回类型一致的 ``ModelReply``。

        2026-10-10 对齐基线 3f79df9：
        - A 的 ``call_model`` 返回 ``ModelReply(text, call_info)``，
          且 ``workflow.make_validation_plan`` 会对 ``call_info`` 执行
          ``dataclasses.asdict(...)``，因此 ``call_info`` 必须是真实 dataclass，
          用 ``SimpleNamespace`` 会抛 ``TypeError``。
        - 这里直接复用 ``app.services.llm_client`` 公开的 ``CallInfo`` / ``ModelReply``，
          形状永远与后端一致，A 改字段时测试会立刻暴露而不是静默漂移。
        """
        llm = importlib.import_module("app.services.llm_client")
        call_info = llm.CallInfo(
            model="c-test-mock-model",
            started_at="2026-10-10T00:00:00+00:00",
            duration_seconds=0.001,
            success=True,
            http_status=200,
            token_usage=None,
            error_code=None,
        )
        return llm.ModelReply(text=text, call_info=call_info)

    def _auto_text(self, system_prompt: str) -> str:
        blob = (system_prompt or "").lower()
        review_markers = (
            "hypothesis_assessments",
            "evidence_refs",
            "next_actions",
            "复盘",
            "复盘输出",
        )
        if any(marker.lower() in blob for marker in review_markers):
            return self.review_text
        return self.plan_text

    @staticmethod
    def _split(args, kwargs) -> tuple[str, object]:
        """容忍 A 的统一入口使用位置参数或关键字参数。"""
        system_prompt = ""
        payload: object = {}
        for value in args:
            if isinstance(value, str) and not system_prompt:
                system_prompt = value
            elif isinstance(value, dict) and not payload:
                payload = value
        system_prompt = (
            kwargs.get("system_prompt")
            or kwargs.get("prompt")
            or kwargs.get("system")
            or system_prompt
        )
        payload = (
            kwargs.get("data")
            or kwargs.get("payload")
            or kwargs.get("user_payload")
            or kwargs.get("user")
            or payload
        )
        return system_prompt or "", payload

    # -- 断言辅助 ---------------------------------------------------------
    @property
    def call_count(self) -> int:
        return len(self.calls)


#: A 在 ``app/services/llm_client.py`` 里暴露统一模型函数时的候选名字。
#: 契约没有固定函数名（只规定“只暴露一个统一函数”），因此这里按顺序探测，
#: 并把命中的名字回传给测试；A 改名后只需在此列表补一项。
LLM_SEAM_CANDIDATES = (
    "call_model",
    "call_llm",
    "complete",
    "chat_completion",
    "invoke_model",
    "generate",
    "request_model",
)


def _patch_llm(monkeypatch, fake: FakeLLM) -> str:
    llm = importlib.import_module("app.services.llm_client")
    # 先把调用链上的模块全部导入，保证它们都已进入 sys.modules 且完成了模块级绑定，
    # 否则「后导入的模块」会在 patch 之后才绑定原函数，导致 mock 失效（2026-10-10 实测）。
    for module_name in (
        "app.services.planning",
        "app.services.workflow",
        "app.services.reviews",
        "app.services.reports",
        "app.main",
    ):
        try:
            importlib.import_module(module_name)
        except ImportError:
            continue

    seam = None
    for name in LLM_SEAM_CANDIDATES:
        if callable(getattr(llm, name, None)):
            seam = name
            break
    if seam is None:
        candidates = ", ".join(LLM_SEAM_CANDIDATES)
        pytest.fail(
            "app.services.llm_client 里找不到可覆盖的模型入口函数。\n"
            f"  已尝试的名字 : {candidates}\n"
            "  契约         : 总纲 §3.1/§3.3 —— llm_client 只暴露一个统一函数，"
            "输入系统提示词与数据，输出模型文本。\n"
            "  处理         : 请 A 确认函数名；C 在 conftest.py 的 "
            "LLM_SEAM_CANDIDATES 补上即可。"
        )

    original = getattr(llm, seam)
    # 用适配器替换真实入口：FakeLLM 负责「产出什么文本/抛什么故障」，
    # 适配器负责「满足 A 的入参形态与返回类型（ModelReply）」。
    fake_fn = fake.as_model_fn(seam)
    monkeypatch.setattr(llm, seam, fake_fn)

    # 若 A 在其它模块里用了 ``from app.services.llm_client import <seam>``，
    # 模块级绑定不会随 llm_client 一起被替换，这里一并重绑。
    #
    # 2026-10-10 对齐基线 3f79df9：A 的真实调用链是
    #   app/main.py -> app.services.workflow.run_plan -> app.services.planning.generate_plan
    # 而 generate_plan 在 planning 模块内直接调用 call_model，
    # 因此 app.services.planning 必须在重绑列表里；漏掉它会让测试真的去请求模型。
    for module_name in (
        "app.services.planning",
        "app.services.workflow",
        "app.services.reviews",
        "app.services.reports",
        "app.main",
    ):
        module = sys.modules.get(module_name)
        if module is not None and getattr(module, seam, None) is original:
            monkeypatch.setattr(module, seam, fake_fn)
    return seam


# --------------------------------------------------------------------------
# 测试客户端包装：统一附加管理凭据、Origin 与固定请求头
# --------------------------------------------------------------------------


class Client:
    """在同一个 httpx 会话上加一层默认请求头，便于按用例裁剪。"""

    def __init__(self, client, headers: dict) -> None:
        self._client = client
        self._headers = dict(headers)

    def _kwargs(self, kwargs: dict) -> dict:
        kwargs = dict(kwargs)
        headers = dict(self._headers)
        headers.update(kwargs.pop("headers", None) or {})
        kwargs["headers"] = headers
        return kwargs

    def request(self, method: str, url: str, **kwargs):
        return self._client.request(method, url, **self._kwargs(kwargs))

    def get(self, url: str, **kwargs):
        return self._client.get(url, **self._kwargs(kwargs))

    def post(self, url: str, **kwargs):
        return self._client.post(url, **self._kwargs(kwargs))

    def patch(self, url: str, **kwargs):
        return self._client.patch(url, **self._kwargs(kwargs))

    def variant(self, drop=(), **headers):
        """返回一个去掉/覆盖部分默认请求头的同会话视图。"""
        merged = {k: v for k, v in self._headers.items() if k not in drop}
        merged.update(headers)
        return Client(self._client, merged)


class AppEnv:
    """一次应用实例：一个临时库 + 一份被覆盖的模型 + 三个客户端视图。"""

    def __init__(self, db_path: Path, app, fake_llm: FakeLLM, seam: str, stack) -> None:
        self.db_path = db_path
        self.app = app
        self.fake_llm = fake_llm
        self.llm_seam = seam
        self._stack = stack

        admin_headers = {
            "Authorization": "Basic "
            + base64.b64encode(f"{ADMIN_USER}:{ADMIN_PASSWORD}".encode()).decode(),
            "Origin": PUBLIC_ORIGIN,
            SITE_HEADER: SITE_HEADER_VALUE,
        }
        site_headers = {"Origin": PUBLIC_ORIGIN, SITE_HEADER: SITE_HEADER_VALUE}

        from fastapi.testclient import TestClient

        primary = stack.enter_context(TestClient(app))
        second = stack.enter_context(TestClient(app))
        self.admin = Client(primary, admin_headers)
        # 并发用例需要一个独立会话，避免同一个 httpx 客户端被两个线程共用。
        self.admin_second = Client(second, admin_headers)
        self.anon = Client(primary, {})
        self.site = Client(primary, site_headers)

    # -- 业务辅助 ---------------------------------------------------------
    def create_project(self, **overrides) -> str:
        payload = dict(TEST_PROJECT_PAYLOAD)
        payload.update(overrides)
        response = self.admin.post("/api/projects", json=payload)
        assert response.status_code == 201, (
            f"创建项目失败：HTTP {response.status_code} {response.text[:400]}"
        )
        return data_of(response)["project_id"]

    def generate_plan(self, project_id: str):
        return self.admin.post(f"/api/projects/{project_id}/plan", json={})

    def publish(self, experiment_id: str):
        return self.admin.post(f"/api/experiments/{experiment_id}/publish", json={})

    def setup_published(self, **overrides) -> dict:
        """创建项目 → 生成草稿 → 发布，返回公开链接与各 ID。"""
        project_id = self.create_project(**overrides)
        plan_response = self.generate_plan(project_id)
        assert plan_response.status_code == 200, (
            f"生成计划失败：HTTP {plan_response.status_code} {plan_response.text[:400]}"
        )
        experiment_id = data_of(plan_response)["experiment_id"]

        publish_response = self.publish(experiment_id)
        assert publish_response.status_code == 200, (
            f"发布失败：HTTP {publish_response.status_code} {publish_response.text[:400]}"
        )
        published = data_of(publish_response)
        token = token_from_url(published["service_url"])
        return {
            "project_id": project_id,
            "experiment_id": experiment_id,
            "token": token,
            "service_url": published["service_url"],
            "survey_url": published["survey_url"],
            "published_at": published["published_at"],
        }

    def submit(self, token: str, answers: dict, *, submission_id=None, consent=True):
        """按总纲 §7.3 提交一份公开回答（不带管理员认证）。"""
        import uuid as _uuid

        return self.site.post(
            f"/api/public/e/{token}/responses",
            json={
                "client_submission_id": submission_id or str(_uuid.uuid4()),
                "consent": consent,
                "answers": answers,
            },
        )

    def submit_sample(self, token: str) -> list[int]:
        """提交 C03 的固定 4 份样本，返回按提交顺序的 response_id 列表。"""
        ids = []
        for answers in SAMPLE_RESPONSES:
            response = self.submit(token, answers)
            assert response.status_code == 201, (
                f"样本提交失败：HTTP {response.status_code} {response.text[:400]}"
            )
            ids.append(data_of(response)["response_id"])
        return ids

    def statistics(self, experiment_id: str) -> dict:
        response = self.admin.get(f"/api/experiments/{experiment_id}/statistics")
        assert response.status_code == 200, (
            f"读取统计失败：HTTP {response.status_code} {response.text[:400]}"
        )
        return data_of(response)

    def report(self, project_id: str) -> dict:
        response = self.admin.get(f"/api/projects/{project_id}/report")
        assert response.status_code == 200, (
            f"读取报告失败：HTTP {response.status_code} {response.text[:400]}"
        )
        return data_of(response)

    def review(self, project_id: str):
        return self.admin.post(f"/api/projects/{project_id}/review", json={})

    def close(self) -> None:
        self._stack.close()


def build_env(db_path: Path, fake_llm: FakeLLM, monkeypatch) -> AppEnv:
    """按总纲配置名准备环境并导入应用；用于首次构建与“重启”复现。"""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC_ORIGIN)
    monkeypatch.setenv("ADMIN_USER", ADMIN_USER)
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "c-test-key-not-used")
    monkeypatch.setenv("LLM_MODEL", "c-test-mock-model")

    try:
        main = importlib.import_module("app.main")
    except Exception as exc:  # noqa: BLE001 - 需要把任何导入错误变成可读阻塞信息
        _backend_missing(db_path, exc)
    app = main.create_app(importlib.import_module("app.config").load_settings())
    if app is None:
        _backend_missing(db_path, AttributeError("app.main 未暴露名为 app 的 ASGI 对象"))

    # Any missed mock seam fails locally, never connects to a supplier.
    def deny_model_http(*args, **kwargs):
        raise AssertionError("C tests must not send model HTTP requests")
    monkeypatch.setattr(importlib.import_module("httpx"), "post", deny_model_http)
    seam = _patch_llm(monkeypatch, fake_llm)
    return AppEnv(db_path, app, fake_llm, seam, contextlib.ExitStack())


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def app_env(tmp_path, monkeypatch, fake_llm) -> AppEnv:
    """默认环境：独立临时库 + data_kind=test 项目可创建。"""
    env = build_env(tmp_path / "firstorder-test.db", fake_llm, monkeypatch)
    try:
        yield env
    finally:
        env.close()


@pytest.fixture
def published(app_env) -> dict:
    """已发布实验：项目 + 实验 + 公开 token。"""
    return app_env.setup_published()


@pytest.fixture
def published_with_sample(app_env, published) -> dict:
    """已发布实验 + C03 的 4 份固定样本回答。"""
    published = dict(published)
    published["response_ids"] = app_env.submit_sample(published["token"])
    return published


def read_public_token(db_path: Path, experiment_id: str) -> str:
    """从临时库直接读草稿实验的 ``public_token``。

    公开接口在未发布时不会返回 token（总纲 §6.2），但 T04 要求验证“未发布公开 404”，
    所以这里按契约表结构取一次，只用于拼出应当 404 的地址。
    """
    connection = sqlite3.connect(str(db_path))
    try:
        exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='experiments'"
        ).fetchone()
        if not exists:
            row = None
        else:
            row = connection.execute(
                "SELECT public_token FROM experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
    finally:
        connection.close()
    if row is None and not exists:
        pytest.skip("experiments 表尚不存在（A01 未交付），无法读取草稿 token")
    assert row is not None, f"experiments 表里没有实验 {experiment_id}"
    token = row[0]
    assert isinstance(token, str) and len(token) >= 32, (
        f"public_token 至少应有 128 位随机量（总纲 §4），实际长度 {len(str(token))}"
    )
    return token


def insert_running_run(db_path: Path, project_id: str, run_id: str, action: str = "plan") -> None:
    """直接写入一条 ``running`` 运行记录，用于模拟进程被中断后重启。

    表与字段取自总纲 §4；只写入该表确实存在的列，避免因 A 增列而误报。
    """
    connection = sqlite3.connect(str(db_path))
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        run_columns = {
            info[1] for info in connection.execute("PRAGMA table_info(agent_runs)")
        }
        if not run_columns:
            pytest.skip("agent_runs 表尚不存在（A01 未交付），无法模拟中断运行")
        run_row = {
            "id": run_id,
            "project_id": project_id,
            "action": action,
            "status": "running",
            "started_at": "2026-10-10T00:00:00Z",
            "finished_at": None,
            "error_code": None,
            "error_message": None,
        }
        columns = [c for c in run_row if c in run_columns]
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO agent_runs ({', '.join(columns)}) VALUES ({placeholders})",
            [run_row[c] for c in columns],
        )

        tool_columns = {
            info[1] for info in connection.execute("PRAGMA table_info(tool_calls)")
        }
        if tool_columns:
            tool_row = {
                "id": f"{run_id}-tool-1",
                "run_id": run_id,
                "tool_name": "make_validation_plan",
                "status": "running",
                "started_at": "2026-10-10T00:00:01Z",
                "finished_at": None,
                "input_summary_json": "{}",
                "output_ref_json": "{}",
                "error_code": None,
            }
            tool_columns_to_write = [c for c in tool_row if c in tool_columns]
            tool_placeholders = ", ".join("?" for _ in tool_columns_to_write)
            connection.execute(
                f"INSERT INTO tool_calls ({', '.join(tool_columns_to_write)}) "
                f"VALUES ({tool_placeholders})",
                [tool_row[c] for c in tool_columns_to_write],
            )
        connection.commit()
    finally:
        connection.close()
