import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.schemas import Plan, ProjectCreate, Hypothesis, Landing, Question, Option
from app.services.llm_client import CallInfo, LLMError, call_model, extract_json_object

logger = logging.getLogger(__name__)
PLAN_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "plan.txt"


@dataclass
class PlanResult:
    plan: Plan = field(repr=False)
    call_info: CallInfo


def parse_plan(text: str) -> Plan:
    try:
        return Plan.model_validate(json.loads(extract_json_object(text)))
    except ValidationError as error:
        # Unknown extra-field names and validator messages can contain model text.
        fields = {name for model in (Plan, Hypothesis, Landing, Question, Option) for name in model.model_fields}
        details = [{"path": [part if isinstance(part, int) or part in fields else "<unknown_field>"
                             for part in item["loc"]], "type": item["type"]}
                   for item in error.errors(include_input=False, include_context=False, include_url=False)[:20]]
        invalid = LLMError("LLM_OUTPUT_INVALID")
        invalid.validation_errors = details
        invalid.failure_kind = "plan_contract" if any(item["type"] == "value_error" for item in details) else "plan_schema"
        raise invalid from None
    except json.JSONDecodeError:
        invalid = LLMError("LLM_OUTPUT_INVALID")
        invalid.validation_errors = [{"path": [], "type": "json_decode"}]
        invalid.failure_kind = "plan_json"
        raise invalid from None
    except (ValueError, TypeError, RecursionError):
        invalid = LLMError("LLM_OUTPUT_INVALID")
        invalid.validation_errors = [{"path": [], "type": "json_object_boundary"}]
        invalid.failure_kind = "plan_json"
        raise invalid from None


def generate_plan(project: ProjectCreate, settings: Settings) -> PlanResult:
    prompt = PLAN_PROMPT.read_text(encoding="utf-8")
    reply = call_model(prompt, project.model_dump(mode="json"), settings)
    try:
        plan = parse_plan(reply.text)
    except LLMError as error:
        reply.call_info.stage = "plan_validation"
        reply.call_info.failure_kind = error.failure_kind
        reply.call_info.validation_errors = error.validation_errors
        logger.warning("llm_plan_validation_details %s", json.dumps({
            "model": reply.call_info.model, "started_at": reply.call_info.started_at,
            "http_status": reply.call_info.http_status, "duration_seconds": reply.call_info.duration_seconds,
            "failure_kind": error.failure_kind, "validation_errors": error.validation_errors,
        }, ensure_ascii=False))
        logger.info("llm_plan_validation %s", json.dumps({
            "model": reply.call_info.model, "started_at": reply.call_info.started_at,
            "success": False, "error_code": error.code,
        }, ensure_ascii=False))
        raise LLMError(error.code, reply.call_info) from None
    logger.info("llm_plan_validation %s", json.dumps({
        "model": reply.call_info.model, "started_at": reply.call_info.started_at,
        "success": True, "error_code": None,
    }, ensure_ascii=False))
    return PlanResult(plan=plan, call_info=reply.call_info)
