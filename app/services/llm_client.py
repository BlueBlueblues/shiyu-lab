import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)
ERRORS = {
    "LLM_TIMEOUT": (504, "模型请求超时，请手动重试。"),
    "LLM_UPSTREAM_ERROR": (502, "模型服务暂时不可用，请检查配置或稍后手动重试。"),
    "LLM_OUTPUT_INVALID": (502, "模型返回内容不符合验证计划要求，请手动重试。"),
}


@dataclass
class CallInfo:
    model: str
    started_at: str
    duration_seconds: float = 0
    success: bool = False
    http_status: int | None = None
    token_usage: dict | None = None
    error_code: str | None = None


class LLMError(Exception):
    def __init__(self, code: str, call_info: CallInfo | None = None):
        self.code = code
        self.status_code, self.message = ERRORS[code]
        self.call_info = call_info
        super().__init__(self.message)


@dataclass
class ModelReply:
    text: str = field(repr=False)
    call_info: CallInfo


def _token_usage(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    usage = {}
    for key in (
        "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens",
    ):
        count = value.get(key)
        if type(count) is int and count >= 0:
            usage[key] = count
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        details = value.get(key)
        if isinstance(details, dict):
            counts = {
                name: details[name] for name in (
                    "cached_tokens", "reasoning_tokens", "audio_tokens",
                    "accepted_prediction_tokens", "rejected_prediction_tokens",
                ) if type(details.get(name)) is int and details[name] >= 0
            }
            if counts:
                usage[key] = counts
    return usage or None


def call_model(system_prompt: str, data: dict, settings: Settings) -> ModelReply:
    """同步 HTTP 适配；success 仅表示 HTTP 和 Chat 响应格式成功，不代表计划校验成功。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False, allow_nan=False)},
    ]
    info = CallInfo(model=settings.llm_model, started_at=datetime.now(timezone.utc).isoformat())
    started = time.perf_counter()
    try:
        response = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
            json={"model": settings.llm_model, "messages": messages, "stream": False},
            timeout=40.0, follow_redirects=False,
        )
        info.http_status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            info.token_usage = _token_usage(body.get("usage"))
        response.raise_for_status()
        if isinstance(body, dict) and body.get("error"):
            raise LLMError("LLM_UPSTREAM_ERROR", info)
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError):
            raise LLMError("LLM_OUTPUT_INVALID", info) from None
        info.success = True
        return ModelReply(text=content, call_info=info)
    except httpx.TimeoutException:
        info.error_code = "LLM_TIMEOUT"
        raise LLMError("LLM_TIMEOUT", info) from None
    except (httpx.RequestError, httpx.HTTPStatusError):
        info.error_code = "LLM_UPSTREAM_ERROR"
        raise LLMError("LLM_UPSTREAM_ERROR", info) from None
    except LLMError as error:
        info.error_code = error.code
        raise
    finally:
        info.duration_seconds = round(time.perf_counter() - started, 3)
        # 日志仅包含受控元数据，不记录 URL、请求内容、模型原文或上游错误正文。
        logger.info("llm_http_call %s", json.dumps(asdict(info), ensure_ascii=False))
