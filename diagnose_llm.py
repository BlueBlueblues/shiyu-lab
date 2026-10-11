"""独立 Chat Completions 诊断；每次总等待 120 秒，无重试、无数据库访问。"""
import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.config import PROJECT_ROOT, load_settings
from app.schemas import ProjectCreate
from app.services.llm_client import LLMError, _token_usage
from app.services.planning import PLAN_PROMPT, parse_plan

DIAGNOSTIC_SECONDS = 120.0


async def request_once(settings, evidence, payload=None):
    started = time.monotonic()
    evidence.update(http_status=None, success=False, request_count=1)
    try:
        # HTTPX 的阶段超时之外，再限制整次连接、发送和读取的总时间。
        async with asyncio.timeout(DIAGNOSTIC_SECONDS):
            async with httpx.AsyncClient(timeout=DIAGNOSTIC_SECONDS, follow_redirects=False) as client:
                async with client.stream(
                    "POST", evidence["request_url"],
                    headers={"Authorization": "Bearer " + settings.llm_api_key.get_secret_value()},
                    json=payload if payload is not None else {
                        "model": settings.llm_model,
                        "messages": [{"role": "user", "content": "只回复 OK，不要输出其他内容。"}],
                        "stream": False},
                ) as response:
                    evidence["http_status"] = response.status_code
                    evidence["response_headers_after_seconds"] = round(time.monotonic() - started, 3)
                    await response.aread()
        if not response.is_success:
            evidence["error"] = {"type": "HTTPError", "message": "供应商返回非成功状态；不保存原始错误正文。"}
            return
        body = response.json()
        if not isinstance(body, dict) or body.get("error"):
            raise ValueError
        choice = body["choices"][0]
        content = choice["message"]["content"]
        valid = isinstance(content, str) and bool(content.strip())
        finish = choice.get("finish_reason")
        evidence["checks"] = {"content_nonempty_string": valid,
                              "not_truncated_or_filtered": finish not in {"length", "content_filter"}}
        evidence["token_usage"] = _token_usage(body.get("usage"))
        if valid:
            evidence["content_chars"] = len(content)
            evidence["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
            # 只保存预期短回复，避免意外响应回显请求头或其他敏感内容。
            evidence["content"] = "OK" if content.strip() == "OK" else None
        if evidence.get("kind") == "plan":
            if not valid:
                raise ValueError
            plan = parse_plan(content)
            evidence["checks"]["plan_contract"] = True
            evidence["hypothesis_count"] = len(plan.hypotheses)
            evidence["question_count"] = len(plan.questions)
        else:
            evidence["checks"]["reply_is_OK"] = valid and content.strip() == "OK"
        evidence["finish_reason"] = finish if finish in {None, "stop", "length", "content_filter", "tool_calls"} else "other"
        evidence["success"] = all(evidence["checks"].values())
        if not evidence["success"]:
            evidence["error"] = {"type": "OutputMismatch", "message": "响应内容或终止原因不符合最小 OK 请求预期。"}
    except TimeoutError:
        evidence["error"] = {"type": "DiagnosticDeadline", "message": "整次诊断达到 120 秒总等待上限。"}
    except httpx.TimeoutException as error:
        evidence["error"] = {"type": type(error).__name__, "message": "连接、发送或读取响应超时。"}
    except httpx.RequestError as error:
        evidence["error"] = {"type": type(error).__name__, "message": "供应商连接或传输失败；不保存异常原文。"}
    except LLMError as error:
        evidence["error"] = {"type": error.code, "message": "生成内容不满足项目 Plan 输出契约；不保存原文。"}
    except (ValueError, KeyError, IndexError, TypeError):
        evidence["error"] = {"type": "InvalidResponse", "message": "响应不符合 choices[0].message.content 契约。"}
    finally:
        evidence["duration_seconds"] = round(time.monotonic() - started, 3)
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-input", type=str, help="已有 test 冒烟 result.json；只重放方案生成请求")
    args = parser.parse_args()
    settings = load_settings()
    payload = None
    kind = "minimal"
    metadata = {}
    if args.plan_input:
        source_path = PROJECT_ROOT / args.plan_input
        project = ProjectCreate.model_validate(json.loads(source_path.read_text(encoding="utf-8"))["project_input"])
        assert project.data_kind == "test"
        prompt = PLAN_PROMPT.read_text(encoding="utf-8")
        payload = {"model": settings.llm_model, "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(project.model_dump(mode="json"), ensure_ascii=False, allow_nan=False)},
        ], "stream": False}
        kind = "plan"
        metadata = {"source_evidence": str(source_path), "data_kind": "test",
                    "system_prompt_chars": len(prompt),
                    "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                    "payload_sha256": hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()}
    directory = PROJECT_ROOT / "docs/model-verification" / (
        "diagnostic-" + kind + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    evidence = {"started_at": datetime.now(timezone.utc).isoformat(),
                "request_url": settings.llm_base_url.rstrip("/") + "/chat/completions",
                "model": settings.llm_model, "stream": False,
                "total_timeout_seconds": DIAGNOSTIC_SECONDS, "automatic_retries": 0,
                "trust_env": True, "database_access": False, "kind": kind, **metadata}
    asyncio.run(request_once(settings, evidence, payload))
    encoded = json.dumps(evidence, ensure_ascii=False, indent=2)
    assert settings.llm_api_key.get_secret_value() not in encoded
    (directory / "result.json").write_text(encoded, encoding="utf-8")
    print(encoded)
    print("Evidence: " + str(directory / "result.json"))
    return 0 if evidence["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
