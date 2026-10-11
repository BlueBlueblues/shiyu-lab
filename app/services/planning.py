import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.schemas import Plan, ProjectCreate
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
    except (ValueError, TypeError, RecursionError):
        raise LLMError("LLM_OUTPUT_INVALID") from None


def generate_plan(project: ProjectCreate, settings: Settings) -> PlanResult:
    prompt = PLAN_PROMPT.read_text(encoding="utf-8")
    reply = call_model(prompt, project.model_dump(mode="json"), settings)
    try:
        plan = parse_plan(reply.text)
    except LLMError as error:
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
