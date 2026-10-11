import asyncio
import json
import re
from types import SimpleNamespace

import httpx
from pydantic import SecretStr

import diagnose_llm


def run(monkeypatch, handler, budget=120, kind="minimal", payload=None):
    original_client = httpx.AsyncClient
    monkeypatch.setattr(diagnose_llm, "DIAGNOSTIC_SECONDS", budget)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    settings = SimpleNamespace(llm_model="test-model", llm_api_key=SecretStr("diagnostic-test-secret"))
    result = {"request_url": "https://test.example.invalid/v1/chat/completions", "kind": kind}
    asyncio.run(diagnose_llm.request_once(settings, result, payload))
    assert settings.llm_api_key.get_secret_value() not in json.dumps(result)
    return result


def test_single_nonstream_request(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == "test-model" and body["stream"] is False
        assert body["messages"] == [{"role": "user", "content": "只回复 OK，不要输出其他内容。"}]
        assert request.headers["Authorization"] == "Bearer diagnostic-test-secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]})

    result = run(monkeypatch, handler)
    assert result["success"] and result["content"] == "OK" and len(requests) == 1


def test_total_deadline_no_retry(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200)

    result = run(monkeypatch, handler, .02)
    assert result["error"]["type"] == "DiagnosticDeadline" and len(requests) == 1
    assert result["duration_seconds"] < .5


def test_upstream_error_body_not_logged(monkeypatch):
    result = run(monkeypatch, lambda request: httpx.Response(401, text="diagnostic-test-secret"))
    assert result["http_status"] == 401 and result["error"]["type"] == "HTTPError"


def test_missing_content_contract(monkeypatch):
    result = run(monkeypatch, lambda request: httpx.Response(200, json={"choices": []}))
    assert not result["success"] and result["error"]["type"] == "InvalidResponse"


def test_plan_null_content_is_recorded(monkeypatch):
    result = run(monkeypatch, lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": None}}]}), kind="plan")
    assert not result["success"] and result["error"]["type"] == "InvalidResponse"


def test_plan_payload_and_contract(monkeypatch):
    document = (diagnose_llm.PROJECT_ROOT / "docs/tasks/01_项目总任务大纲.md").read_text(encoding="utf-8")
    plan = json.loads(re.search(r"```json\s*(.*?)\s*```", document.split("### 7.2 ", 1)[1], re.S).group(1))
    payload = {"model": "test-model", "messages": [{"role": "system", "content": "test prompt"}], "stream": False}

    def handler(request):
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(plan)}, "finish_reason": "stop"}]})

    result = run(monkeypatch, handler, kind="plan", payload=payload)
    assert result["success"] and result["checks"]["plan_contract"]
    assert result["hypothesis_count"] == 3 and result["content"] is None
