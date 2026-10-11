from app.schemas import Question, Statistics


def calculate_statistics(experiment_id: str, questions: list[Question], responses: list[dict]) -> Statistics:
    """输入已发布题目及经提交接口校验保存的回答；不调用模型。"""
    result = []
    for question in questions:
        answered = [(response["id"], response["answers"][question.id]) for response in responses
                    if question.id in response["answers"] and response["answers"][question.id]]
        item = {"id": question.id, "type": question.type}
        if question.type == "text":
            texts = [{"response_id": response_id, "text": value.strip()} for response_id, value in answered
                     if value.strip()]
            item.update(answered_count=len(texts), texts=texts)
        else:
            denominator = len(answered)
            options = []
            for option in question.options:
                count = sum(option.id == value if question.type == "single" else option.id in value
                            for _, value in answered)
                options.append({"id": option.id, "label": option.label, "count": count,
                                "percentage": round(count * 100 / denominator, 2) if denominator else None})
            item.update(answered_count=denominator, options=options)
        result.append(item)
    return Statistics(experiment_id=experiment_id, response_count=len(responses),
                      cutoff_response_id=max((response["id"] for response in responses), default=0),
                      questions=result)
