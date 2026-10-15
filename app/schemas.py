from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StringConstraints,
    field_validator, model_validator,
)

Text80 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text500 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Text1000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Text2000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Count = Annotated[int, Field(strict=True, ge=0)]
ResponseId = Annotated[int, Field(strict=True, ge=1)]
ProjectStatus = Literal["draft", "planned", "collecting", "reviewed"]
HypothesisId = Literal["h1", "h2", "h3"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(ContractModel):
    name: Text80
    idea: Text2000
    target_users: Text500
    skills: Text500
    budget_cny: Annotated[float, Field(strict=True, ge=0, le=100000, allow_inf_nan=False)]
    validation_goal: Text500
    data_kind: Literal["real", "test"]


class Landing(ContractModel):
    title: Text200
    summary: Text1000
    target_users: Text500
    value_proposition: Text500
    features: list[Text500] = Field(max_length=6)
    disclaimer: Text500


class Option(ContractModel):
    id: Text500
    label: Text80


class Question(ContractModel):
    id: Text500
    type: Literal["single", "multiple", "text"]
    title: Text200
    required: StrictBool
    options: list[Option] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_options(self):
        if self.type == "text" and self.options:
            raise ValueError("简答题的 options 必须为空")
        if self.type != "text" and not 2 <= len(self.options) <= 8:
            raise ValueError("选择题必须有 2～8 个选项")
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("同一题内的选项 ID 必须唯一")
        return self


class ExperimentEdit(ContractModel):
    landing: Landing
    questions: list[Question] = Field(min_length=4, max_length=6)


class Hypothesis(ContractModel):
    id: HypothesisId
    statement: Text500
    question_ids: list[Text500] = Field(min_length=1, max_length=6)


class Plan(ContractModel):
    target_customer: Text500
    pain_points: list[Text500] = Field(min_length=1, max_length=10)
    value_proposition: Text500
    hypotheses: list[Hypothesis] = Field(min_length=3, max_length=3)
    experiment_steps: list[Text500] = Field(min_length=1, max_length=10)
    next_tasks: list[Text500] = Field(min_length=1, max_length=10)
    landing: Landing
    questions: list[Question] = Field(min_length=4, max_length=6)

    @model_validator(mode="after")
    def validate_references(self):
        question_ids = {question.id for question in self.questions}
        if len(question_ids) != len(self.questions):
            raise ValueError("题号必须唯一")
        if {hypothesis.id for hypothesis in self.hypotheses} != {"h1", "h2", "h3"}:
            raise ValueError("必须包含互不重复的 h1、h2、h3")
        for hypothesis in self.hypotheses:
            if len(set(hypothesis.question_ids)) != len(hypothesis.question_ids):
                raise ValueError("假设引用的题号不得重复")
            if not set(hypothesis.question_ids) <= question_ids:
                raise ValueError("假设只能引用计划中存在的题号")
        return self


AnswerValue = (
    Annotated[str, StringConstraints(max_length=1000)]
    | Annotated[list[Text500], Field(max_length=8)]
)


class Answers(ContractModel):
    """只校验结构；A05 还须按已发布题目校验题号、选项、必答和规范化。"""

    client_submission_id: UUID
    consent: StrictBool
    answers: dict[Text500, AnswerValue] = Field(max_length=6)

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value):
        if not value:
            raise ValueError("必须同意参与后才能提交")
        return value

    @field_validator("answers")
    @classmethod
    def reject_duplicate_choices(cls, value):
        if any(isinstance(answer, list) and len(set(answer)) != len(answer) for answer in value.values()):
            raise ValueError("多选答案不得包含重复值")
        return value


class OptionStatistics(Option):
    count: Count
    percentage: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)] | None


class ChoiceStatistics(ContractModel):
    id: Text500
    type: Literal["single", "multiple"]
    answered_count: Count
    options: list[OptionStatistics] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_denominator(self):
        for option in self.options:
            if option.count > self.answered_count:
                raise ValueError("选项计数不能超过该题有效作答份数")
            if (option.percentage is None) != (self.answered_count == 0):
                raise ValueError("零分母必须返回 null 百分比，有效分母必须返回数值")
        return self


class TextFeedback(ContractModel):
    response_id: ResponseId
    text: Text1000


class TextStatistics(ContractModel):
    id: Text500
    type: Literal["text"]
    answered_count: Count
    texts: list[TextFeedback]

    @model_validator(mode="after")
    def validate_count(self):
        if self.answered_count != len(self.texts):
            raise ValueError("简答计数必须与有效文本条数一致")
        return self


QuestionStatistics = Annotated[ChoiceStatistics | TextStatistics, Field(discriminator="type")]


class Statistics(ContractModel):
    experiment_id: str
    response_count: Count
    cutoff_response_id: Count
    questions: list[QuestionStatistics] = Field(min_length=4, max_length=6)

    @model_validator(mode="after")
    def validate_snapshot(self):
        if (self.response_count == 0) != (self.cutoff_response_id == 0):
            raise ValueError("只有零回答快照的截止回答 ID 为 0")
        if len({question.id for question in self.questions}) != len(self.questions):
            raise ValueError("统计题号必须唯一")
        if any(question.answered_count > self.response_count for question in self.questions):
            raise ValueError("各题作答份数不能超过实验回答份数")
        return self


class RawResponse(ContractModel):
    id: ResponseId
    answers: dict[Text500, AnswerValue]
    created_at: str


class OptionEvidenceRef(ContractModel):
    question_id: Text500
    option_id: Text500


class ResponseEvidenceRef(ContractModel):
    question_id: Text500
    response_id: ResponseId


class HypothesisAssessment(ContractModel):
    hypothesis_id: HypothesisId
    status: Literal["preliminary_support", "not_supported", "insufficient_evidence"]
    reason: Text500
    evidence_refs: list[OptionEvidenceRef | ResponseEvidenceRef] = Field(max_length=50)


class NextAction(ContractModel):
    action: Text500
    owner_role: Text500
    completion_criterion: Text500


class Review(ContractModel):
    summary: Text500
    hypothesis_assessments: list[HypothesisAssessment] = Field(min_length=3, max_length=3)
    key_feedback: list[Text500] = Field(max_length=10)
    limitations: list[Text500] = Field(min_length=1, max_length=10)
    next_actions: list[NextAction] = Field(max_length=3)

    @model_validator(mode="after")
    def validate_hypotheses(self):
        if {item.hypothesis_id for item in self.hypothesis_assessments} != {"h1", "h2", "h3"}:
            raise ValueError("复盘必须包含互不重复的 h1、h2、h3")
        return self


class IterationChange(ContractModel):
    field: Literal["target_customer", "pain_points", "value_proposition", "hypotheses",
                   "experiment_steps", "next_tasks", "landing", "questions"]
    reason: Text500
    hypothesis_ids: list[HypothesisId] = Field(min_length=1, max_length=3)


class IterationReasons(ContractModel):
    changes: list[IterationChange] = Field(min_length=1, max_length=8)


class IterationPlan(IterationReasons):
    diagnosis: Text1000
    next_plan: Plan


class IterationConfirmation(ContractModel):
    confirmed: StrictBool

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if not value:
            raise ValueError("必须明确确认迭代方案")
        return value


class ProjectCreated(ContractModel):
    project_id: str
    status: Literal["draft"]


class ProjectSummary(ContractModel):
    id: str
    name: Text80
    status: ProjectStatus
    data_kind: Literal["real", "test"]
    created_at: str


class Project(ProjectCreate):
    id: str
    status: ProjectStatus
    created_at: str


class Experiment(ContractModel):
    id: str
    project_id: str
    landing: Landing
    questions: list[Question] = Field(min_length=4, max_length=6)
    published_at: str | None
    service_url: str | None
    survey_url: str | None


class ReportMeta(ContractModel):
    id: str
    response_count: Count
    cutoff_response_id: Count
    created_at: str
    stale: bool


class Report(ContractModel):
    id: str
    created_at: str
    response_count: Count
    cutoff_response_id: Count
    statistics: Statistics
    review: Review
    stale: bool
    data_kind: Literal["real", "test"]


class ProjectDetail(ContractModel):
    project: Project
    plan: Plan | None
    experiment: Experiment | None
    latest_report_meta: ReportMeta | None
    running_run_id: str | None


Data = TypeVar("Data")


class DataResponse(ContractModel, Generic[Data]):
    data: Data


class ErrorDetails(ContractModel):
    code: str
    message: str
    fields: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    error: ErrorDetails
