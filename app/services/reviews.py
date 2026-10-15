import json
from pathlib import Path

from app.schemas import Review
from app.services.llm_client import LLMError, call_model, extract_json_object

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "review.txt"


def generate_review(project, plan, statistics, settings):
    reply = call_model(PROMPT.read_text(encoding="utf-8"), {
        "project": project.model_dump(mode="json"), "hypotheses": [h.model_dump() for h in plan.hypotheses],
        "questions": [q.model_dump() for q in plan.questions], "statistics": statistics.model_dump(mode="json"),
    }, settings)
    try:
        review = Review.model_validate(json.loads(extract_json_object(reply.text)))
        questions = {q.id: q for q in statistics.questions}
        for assessment in review.hypothesis_assessments:
            for ref in assessment.evidence_refs:
                question = questions.get(ref.question_id)
                if question is None:
                    raise ValueError("invalid question")
                if hasattr(ref, "option_id"):
                    if question.type == "text" or not any(o.id == ref.option_id for o in question.options):
                        raise ValueError("invalid option evidence")
                elif question.type != "text" or ref.response_id not in {text.response_id for text in question.texts}:
                    raise ValueError("invalid response evidence")
        if statistics.response_count == 0:
            review.summary = "尚无反馈，证据不足；不能判断需求、付费意愿或商业成功。"
            review.key_feedback = []
            for assessment in review.hypothesis_assessments:
                assessment.status = "insufficient_evidence"
                assessment.reason = "本次快照为零回答，没有可支持或否定该假设的反馈证据。"
                assessment.evidence_refs = []
            review.limitations = ["零回答，证据不足；不能代表目标用户总体。", *review.limitations[:9]]
    except (ValueError, TypeError, RecursionError):
        raise LLMError("LLM_OUTPUT_INVALID", reply.call_info) from None
    return review, reply.call_info
