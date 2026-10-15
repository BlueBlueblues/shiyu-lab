from typing import Literal
from pydantic import Field, StrictBool, model_validator, field_validator
from app.schemas import (ContractModel, Text500, Text1000, Plan, HypothesisId,
                         OptionEvidenceRef, ResponseEvidenceRef, Review, ProjectCreate)

Evidence = OptionEvidenceRef | ResponseEvidenceRef

class VentureHypothesis(ContractModel):
    id: HypothesisId
    statement: Text500
    importance: Literal['high', 'medium', 'low']
    uncertainty: Literal['high', 'medium', 'low']
    observable_metric: Text500
    success_criterion: Text500
    failure_criterion: Text500

class Opportunity(ContractModel):
    target_customer: Text500
    applicability_limits: list[Text500] = Field(min_length=1, max_length=8)
    pain_points: list[Text500] = Field(min_length=1, max_length=10)
    value_proposition: Text500
    key_risks: list[Text500] = Field(min_length=1, max_length=8)
    hypotheses: list[VentureHypothesis] = Field(min_length=3, max_length=3)
    missing_information: list[Text500] = Field(min_length=1, max_length=10)
    unverified_notice: Text500

    @model_validator(mode='after')
    def ids(self):
        if {h.id for h in self.hypotheses} != {'h1','h2','h3'}:
            raise ValueError('Three unique hypothesis IDs required')
        return self

class DesignCheck(ContractModel):
    question_id: Text500
    hypothesis_ids: list[HypothesisId] = Field(min_length=1, max_length=3)
    rationale: Text500
    bias_check: Text500

class ExperimentDesign(ContractModel):
    method: Text500
    method_reason: Text500
    sample_limits: list[Text500] = Field(min_length=1, max_length=8)
    success_metrics: list[Text500] = Field(min_length=1, max_length=8)
    question_checks: list[DesignCheck] = Field(min_length=4, max_length=6)
    plan: Plan

    @model_validator(mode='after')
    def mapping(self):
        checks = {c.question_id:c for c in self.question_checks}
        if len(checks) != len(self.question_checks) or set(checks) != {q.id for q in self.plan.questions}:
            raise ValueError('Every question needs one quality check')
        for h in self.plan.hypotheses:
            for q in h.question_ids:
                if h.id not in checks[q].hypothesis_ids:
                    raise ValueError('Question checks must match hypothesis mapping')
        for check in self.question_checks:
            if set(check.hypothesis_ids) != {h.id for h in self.plan.hypotheses if check.question_id in h.question_ids}:
                raise ValueError('Quality check cannot invent hypothesis links')
        return self

class HypothesisDecision(ContractModel):
    hypothesis_id: HypothesisId
    decision: Literal['retain', 'modify', 'abandon']
    reason: Text500
    evidence_refs: list[Evidence] = Field(max_length=30)

class SuggestedChange(ContractModel):
    field: Literal['target_customer','pain_points','value_proposition','hypotheses','experiment_steps','next_tasks','landing','questions']
    before: Text1000
    after: Text1000
    reason: Text500
    hypothesis_ids: list[HypothesisId] = Field(min_length=1, max_length=3)
    evidence_refs: list[Evidence] = Field(max_length=30)

class IterationStrategy(ContractModel):
    diagnosis: Text1000
    hypothesis_decisions: list[HypothesisDecision] = Field(min_length=3, max_length=3)
    next_goals: list[Text500] = Field(min_length=1, max_length=8)
    constraints: list[Text500] = Field(min_length=1, max_length=8)
    suggested_changes: list[SuggestedChange] = Field(min_length=1, max_length=8)
    unverified: list[Text500] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def ids(self):
        if {d.hypothesis_id for d in self.hypothesis_decisions} != {'h1','h2','h3'}:
            raise ValueError('Three hypothesis decisions required')
        return self

class RoleInput(ContractModel):
    contract_version: Literal[4] = 4
    project_id: str
    round_number: int = Field(ge=1)
    project: ProjectCreate
    sources: list[str] = Field(default_factory=list, max_length=20)
    context: dict

class Approval(ContractModel):
    confirmed: StrictBool

    @field_validator('confirmed')
    @classmethod
    def explicit_approval(cls, value):
        if value is not True:raise ValueError('Explicit approval required')
        return value
    revised_goals: list[Text500] | None = Field(default=None, min_length=1, max_length=8)
    revised_constraints: list[Text500] | None = Field(default=None, min_length=1, max_length=8)

class Rejection(ContractModel):
    reason: Text500

ROLE_OUTPUTS = {'shiyu':Opportunity, 'zhuoyu':ExperimentDesign, 'jianyu':Review, 'gongyu':IterationStrategy}
ROLE_NAMES = {'shiyu':'识玉', 'zhuoyu':'琢玉', 'jianyu':'鉴玉', 'gongyu':'攻玉'}
