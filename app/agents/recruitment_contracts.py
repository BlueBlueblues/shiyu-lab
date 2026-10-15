"""Versioned recruitment suggestions; no contacts, transport, or activity metrics."""
import re
from typing import Annotated, Literal
from pydantic import Field, StringConstraints, model_validator
from app.schemas import ContractModel, HypothesisId

KitText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
FeatureId = Literal['p1', 'p2', 'p3', 'p4', 'p5']
ChannelType = Literal['class_or_course', 'student_club', 'offline_event', 'topic_community', 'professional_network', 'personal_network']
LINK_PLACEHOLDER = '{实验链接}'
TIME_PLACEHOLDER = '{耗时说明}'

class AudienceFeature(ContractModel):
    id: FeatureId
    description: KitText
    hypothesis_ids: list[HypothesisId] = Field(min_length=1, max_length=3)

class RecruitmentChannel(ContractModel):
    channel_type: ChannelType
    where_to_look: KitText
    audience_feature_ids: list[FeatureId] = Field(min_length=1, max_length=5)
    hypothesis_ids: list[HypothesisId] = Field(min_length=1, max_length=3)
    question_ids: list[KitText] = Field(min_length=1, max_length=6)
    why_matched: KitText
    approach: KitText
    precautions: list[KitText] = Field(min_length=1, max_length=3)

class InterviewQuestion(ContractModel):
    question: KitText
    follow_ups: list[KitText] = Field(min_length=1, max_length=2)
    hypothesis_ids: list[HypothesisId] = Field(min_length=1, max_length=3)

class ColdStartKit(ContractModel):
    version: Literal[1] = 1
    audience_features: list[AudienceFeature] = Field(min_length=2, max_length=5)
    channels: list[RecruitmentChannel] = Field(min_length=2, max_length=3)
    community_message: KitText
    private_invitation: KitText
    interview_questions: list[InterviewQuestion] = Field(min_length=3, max_length=5)
    interview_notes: list[KitText] = Field(min_length=2, max_length=4)

    @model_validator(mode='after')
    def safe_suggestions(self):
        features = {f.id:f for f in self.audience_features}
        if len(features) != len(self.audience_features):
            raise ValueError('Recruitment feature IDs must be unique')
        if len({c.channel_type for c in self.channels}) != len(self.channels):
            raise ValueError('Recruitment channels must be distinct')
        for c in self.channels:
            if not set(c.audience_feature_ids) <= features.keys():
                raise ValueError('Recruitment channel refers to unknown audience feature')
            related = {h for f in c.audience_feature_ids for h in features[f].hypothesis_ids}
            if not set(c.hypothesis_ids) & related:
                raise ValueError('Recruitment channel must relate to audience hypotheses')
        if self.community_message.count(LINK_PLACEHOLDER) != 1 or self.community_message.count(TIME_PLACEHOLDER) != 1:
            raise ValueError('Recruitment message requires one public-link and one duration placeholder')
        core = self.community_message.replace(LINK_PLACEHOLDER, '').replace(TIME_PLACEHOLDER, '')
        if len(core) > 100:
            raise ValueError('Recruitment message core exceeds 100 characters')
        if self.private_invitation.count(TIME_PLACEHOLDER) != 1:
            raise ValueError('Private invitation requires one duration placeholder')
        for text in [self.community_message, self.private_invitation]:
            if re.search(r'(?:\d+|[一二三四五六七八九十]+)\s*(?:分钟|秒钟?|小时)', text):
                raise ValueError('Recruitment duration must come from an author trial, not the model')
        # All links are inserted from the actual published experiment by the server.
        def texts(value):
            if isinstance(value,str):yield value
            elif isinstance(value,dict):
                for item in value.values():yield from texts(item)
            elif isinstance(value,list):
                for item in value:yield from texts(item)
        for text in texts(self.model_dump()):
            if re.search(r'https?://|www\.|javascript:|mailto:|data:text|/(?:admin|projects|register)\b|[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)\d[\d -]{6,}\d(?!\d)', text, re.I):
                raise ValueError('Recruitment kit must not contain supplied URLs or contact details')
        for item in self.interview_questions:
            for text in [item.question, *item.follow_ups]:
                if re.search(r'如果.{0,40}(?:愿意|会不会|购买|买|使用)|一定.{0,20}(?:愿意|买)', text):
                    raise ValueError('Interview must ask about past experience, not leading purchase hypotheticals')
        return self

    def validate_plan_links(self, plan):
        hypotheses = {h.id:set(h.question_ids) for h in plan.hypotheses}
        questions = {q.id for q in plan.questions}
        for channel in self.channels:
            if not set(channel.hypothesis_ids) <= hypotheses.keys() or not set(channel.question_ids) <= questions:
                raise ValueError('Recruitment channel must reference the current experiment')
            linked = {q for h in channel.hypothesis_ids for q in hypotheses[h]}
            if not set(channel.question_ids) <= linked:
                raise ValueError('Recruitment questions must match channel hypotheses')
