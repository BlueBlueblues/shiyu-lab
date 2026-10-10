import argparse
import json
from dataclasses import asdict

from app.config import load_settings
from app.schemas import ProjectCreate
from app.services.llm_client import LLMError
from app.services.planning import generate_plan


def scenarios():
    return {"webpage": (ProjectCreate(
        name="A03 模型计划测试：校园活动报名网页服务",
        idea="为校园社团提供低成本活动报名网页定制服务，希望先验证需求，不宣称已经交付或成交。",
        target_users="有活动组织经验的校园社团负责人",
        skills="基础网页开发、Python、AI 辅助编程", budget_cny=100,
        validation_goal="验证是否有试用需求、组织者当前的痛点和最关心的功能", data_kind="test",
    ), ("社团", "活动", "组织"), ("报名", "网页", "活动")), "ppt": (ProjectCreate(
        name="校园 PPT 美化服务",
        idea="为大学生提供低成本 PPT 排版与视觉美化服务",
        target_users="需要课堂展示、答辩或社团汇报的学生",
        skills="Python、AI 辅助设计、PPT 制作", budget_cny=50,
        validation_goal="了解同学是否存在需求、可接受价格及最看重的服务内容",
        data_kind="test",
    ), ("学生", "大学", "同学"), ("ppt", "幻灯", "演示文稿")), "photography": (ProjectCreate(
        name="大学生校园摄影预约服务",
        idea="面向毕业生提供低成本校园写真拍摄服务",
        target_users="希望拍摄校园毕业写真的大学生毕业生",
        skills="校园人像摄影、照片后期处理", budget_cny=200,
        validation_goal="验证用户需求、可接受价格及预约意愿", data_kind="test",
    ), ("毕业", "学生", "大学"), ("摄影", "拍摄", "写真"))}


def check_topic(plan, customer_terms: tuple[str, ...], topic_terms: tuple[str, ...]) -> dict:
    checks = {
        "target_customer": any(term in plan.target_customer.lower() for term in customer_terms),
        "value_proposition": any(term in plan.value_proposition.lower() for term in topic_terms),
        "question_titles": any(term in question.title.lower()
                               for question in plan.questions for term in topic_terms),
    }
    # shortcut: 关键词仅筛查明显跑题，语义相关性仍需人工核对输出摘要。
    return {"method": "keyword_screening_requires_manual_review", "checks": checks,
            "passed": all(checks.values())}


def main(selected: str | None = None) -> int:
    settings = load_settings()
    outputs = []
    cases = scenarios()
    if selected is not None:
        cases = {selected: cases[selected]}
    for project, customer_terms, topic_terms in cases.values():
        output = {"project_name": project.name, "data_kind": "test", "plan_validated": False,
                  "hypothesis_count": None, "question_count": None, "topic_check": None}
        try:
            result = generate_plan(project, settings)
        except LLMError as error:
            output.update(error={"code": error.code, "message": error.message, "fields": {}},
                          call_info=asdict(error.call_info) if error.call_info else None)
        else:
            output.update(
                plan_validated=True, call_info=asdict(result.call_info),
                hypothesis_count=len(result.plan.hypotheses), question_count=len(result.plan.questions),
                question_types=sorted({question.type for question in result.plan.questions}),
                target_customer=result.plan.target_customer,
                value_proposition=result.plan.value_proposition,
                question_titles=[question.title for question in result.plan.questions],
                questions=[question.model_dump() for question in result.plan.questions],
                topic_check=check_topic(result.plan, customer_terms, topic_terms),
            )
        outputs.append(output)
        print(json.dumps({"data": output}, ensure_ascii=False, indent=2), flush=True)
    return 0 if all(item["plan_validated"] and item["topic_check"]["passed"] for item in outputs) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A03 真实模型冒烟（test 输入，不访问数据库）")
    parser.add_argument("--scenario", choices=scenarios(), help="只测试指定场景；默认测试全部场景")
    raise SystemExit(main(parser.parse_args().scenario))
