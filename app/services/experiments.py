import json
from datetime import datetime, timezone
from urllib.parse import quote

from pydantic import ValidationError

from app import repositories
from app.schemas import ExperimentEdit, Plan


class ExperimentError(Exception):
    def __init__(self, code, fields=None):
        self.code = code
        self.fields = fields or {}
        self.status_code, self.message = {
            "NOT_FOUND": (404, "实验不存在或尚未发布。"),
            "RUN_IN_PROGRESS": (409, "该项目已有任务运行中，请稍后再试。"),
            "EXPERIMENT_LOCKED": (409, "实验已发布，不能修改。"),
            "IDEMPOTENCY_CONFLICT": (409, "该提交编号已用于不同的回答，请勿更改重试内容。"),
            "VALIDATION_ERROR": (422, "输入或实验内容校验失败，请检查字段。"),
        }[code]
        super().__init__(self.message)


def require_record(row):
    if row is None:
        raise ExperimentError("NOT_FOUND")
    return row


def experiment_data(row, base_url):
    url = f"{base_url.rstrip('/')}/e/{quote(row['public_token'], safe='')}"
    return {"id": row["id"], "project_id": row["project_id"],
            "landing": json.loads(row["landing_json"]), "questions": json.loads(row["questions_json"]),
            "published_at": row["published_at"],
            "service_url": url if row["published_at"] else None,
            "survey_url": url + "/survey" if row["published_at"] else None}


def checked_plan(row, edit=None):
    try:
        data = edit.model_dump() if edit else {
            "landing": json.loads(row["landing_json"]), "questions": json.loads(row["questions_json"])}
        content = ExperimentEdit.model_validate(data)
        plan = json.loads(row["plan_json"])
        plan.update(content.model_dump())
        return Plan.model_validate(plan)
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise ExperimentError("VALIDATION_ERROR", {"questions": "实验内容或假设引用不符合既有契约。"}) from None


def question_structure(questions):
    return [(q["id"], q["type"], [option["id"] for option in q["options"]]) for q in questions]


def edit_experiment(connection, experiment_id, edit, base_url):
    connection.execute("BEGIN IMMEDIATE")
    row = require_record(repositories.experiment_record(connection, experiment_id))
    check_write_lock(row)
    if row["published_at"] is not None:
        raise ExperimentError("EXPERIMENT_LOCKED")
    old = checked_plan(row)
    if question_structure([q.model_dump() for q in old.questions]) != question_structure(edit.model_dump()["questions"]):
        raise ExperimentError("VALIDATION_ERROR", {"questions": "题号、题型、选项编号、数量和顺序必须保持不变。"})
    repositories.update_draft(connection, row, checked_plan(row, edit))
    return experiment_data(repositories.experiment_record(connection, experiment_id), base_url)


def check_write_lock(row):
    if row["running_run_id"] is not None:
        raise ExperimentError("RUN_IN_PROGRESS")


def publish(connection, experiment_id, base_url):
    connection.execute("BEGIN IMMEDIATE")
    row = require_record(repositories.experiment_record(connection, experiment_id))
    check_write_lock(row)
    checked_plan(row)
    if row["published_at"] is None:
        repositories.publish_experiment(connection, experiment_id, datetime.now(timezone.utc).isoformat())
    data = experiment_data(repositories.experiment_record(connection, experiment_id), base_url)
    return {"experiment_id": data["id"], **{key: data[key] for key in ("published_at", "service_url", "survey_url")}}


def normalize_answers(questions, answers):
    fields = {}
    normalized = {}
    known = {q.id for q in questions}
    for key in answers.keys() - known:
        fields[f"answers.{key}"] = "题号不存在。"
    for question in questions:
        value = answers.get(question.id)
        field = f"answers.{question.id}"
        if value is None:
            if question.required:
                fields[field] = "此题为必答题。"
            continue
        valid_options = {option.id for option in question.options}
        if question.type == "multiple":
            if not isinstance(value, list) or len(set(value)) != len(value) or not set(value) <= valid_options:
                fields[field] = "多选答案必须是该题不重复的选项编号数组。"
                continue
            value = sorted(value)
        elif not isinstance(value, str):
            fields[field] = "此题答案必须是字符串。"
            continue
        elif question.type == "single" and value not in valid_options:
            fields[field] = "单选选项不存在。"
            continue
        elif question.type == "text":
            if len(value) > 1000:
                fields[field] = "简答最多 1000 字符。"
                continue
            value = value.strip()
        if value == "" or value == []:
            if question.required:
                fields[field] = "此题为必答题。"
        else:
            normalized[question.id] = value
    if fields:
        raise ExperimentError("VALIDATION_ERROR", fields)
    return normalized


def submit(connection, token, body):
    # 与查询、插入共用一个写事务；唯一约束仍作为数据库兜底。
    connection.execute("BEGIN IMMEDIATE")
    row = require_record(repositories.published_experiment(connection, token))
    content = ExperimentEdit.model_validate({"landing": json.loads(row["landing_json"]),
                                           "questions": json.loads(row["questions_json"])})
    answers = normalize_answers(content.questions, body.answers)
    existing = repositories.find_response(connection, row["id"], str(body.client_submission_id))
    if existing:
        if json.loads(existing["answers_json"]) != answers or existing["consent"] != int(body.consent):
            raise ExperimentError("IDEMPOTENCY_CONFLICT")
        return {"response_id": existing["id"], "submitted_at": existing["created_at"], "duplicate": True}
    submitted_at = datetime.now(timezone.utc).isoformat()
    response_id = repositories.insert_response(connection, row["id"], str(body.client_submission_id), answers, submitted_at)
    return {"response_id": response_id, "submitted_at": submitted_at, "duplicate": False}
