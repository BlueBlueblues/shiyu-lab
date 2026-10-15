# Test bootstrap: always isolate configuration from local/production environments.
import os, tempfile
_test_bootstrap = tempfile.TemporaryDirectory(prefix="shiyu-test-bootstrap-")
os.environ.update(APP_ENV="test", DATABASE_PATH=_test_bootstrap.name+"/bootstrap.sqlite3",
    LLM_BASE_URL="https://model.invalid", LLM_API_KEY="offline-test-placeholder",
    LLM_MODEL="mock-demo-offline", ADMIN_USER="isolated-reviewer",
    ADMIN_PASSWORD="isolated-test-only-password", PUBLIC_BASE_URL="http://127.0.0.1:8000",
    AGENT_EXECUTION_ENABLED="false", INVITE_REGISTRATION_ENABLED="false", MAIL_PROVIDER="disabled")
import copy
import json
import secrets
from pathlib import Path
import pytest
from pydantic import SecretStr
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.agents.runtime import now
from app.services.llm_client import ModelReply, CallInfo

HEADERS={'Origin':'https://test.shiyu.invalid','X-Requested-With':'FirstOrderLab','Accept':'application/json'}

def opportunity():
    return {'target_customer':'大学生创业实践参与者','applicability_limits':['仅合成测试，未验证真实市场'],
      'pain_points':['待验证：验证成本高'],'value_proposition':'低成本验证创业想法','key_risks':['样本偏差'],
      'hypotheses':[{'id':f'h{i}','statement':f'待验证假设{i}','importance':'high','uncertainty':'high',
        'observable_metric':'问卷自报意愿份数，不是付款','success_criterion':'待确认建议：7天收集5份反馈，人工确认',
        'failure_criterion':'待确认建议：7天仍无反馈，复核招募'} for i in range(1,4)],
      'missing_information':['实际需求尚未验证'],'unverified_notice':'合成测试，未验证市场事实'}

def recruitment_kit(plan):
    return {'version':1,
      'audience_features':[{'id':'p1','description':'最近亲自尝试过项目所述任务、能回忆具体过程的人','hypothesis_ids':['h1']},
                           {'id':'p2','description':'遇到同类困难并实际尝试过替代办法的人','hypothesis_ids':['h2']}],
      'channels':[{'channel_type':'class_or_course','where_to_look':'与目标任务相关的课程讨论空间，先核对受众经历',
        'audience_feature_ids':['p1'],'hypothesis_ids':['h1'],'question_ids':plan['hypotheses'][0]['question_ids'],
        'why_matched':'目标学生可能在相关课程实际接触这项任务，先询问是否有经历','approach':'先征求管理者许可，再自愿招募',
        'precautions':['不声称已经联系到课程群，不重复刷屏']},
        {'channel_type':'topic_community','where_to_look':'讨论项目所述困难和替代方案的主题社区',
        'audience_feature_ids':['p2'],'hypothesis_ids':['h2'],'question_ids':plan['hypotheses'][1]['question_ids'],
        'why_matched':'关注替代方案的人更可能有相关实际困难','approach':'阅读规则后发中立招募帖',
        'precautions':['先核实受众匹配，不收集联系方式']}],
      'community_message':'想邀请有相关经历的同学参与需求验证，了解实际困难和应对方法，是否参与完全自愿。{耗时说明}。{实验链接}',
      'private_invitation':'你好，想了解你最近处理相关任务的真实经历，用于需求验证。{耗时说明}，你愿意聊聊吗？不方便也没关系。',
      'interview_questions':[{'question':'最近一次完成这类任务是什么时候？','follow_ups':['当时具体做了哪些步骤？'],'hypothesis_ids':['h1']},
        {'question':'那次最困难的环节是什么？','follow_ups':['你当时如何应对？'],'hypothesis_ids':['h2']},
        {'question':'你已经用过哪些替代办法？','follow_ups':['实际结果怎样？'],'hypothesis_ids':['h3']}],
      'interview_notes':['说明访谈目的，征得同意后再记录，对方可随时拒绝','不收集无关敏感信息，区分原话与自己的解释']}

def design(context):
    source=context.get('opportunity',opportunity())
    plan={'target_customer':source['target_customer'],'pain_points':source['pain_points'],'value_proposition':source['value_proposition'],
      'hypotheses':[{'id':f'h{i}','statement':source['hypotheses'][i-1]['statement'],'question_ids':[f'q{i}']+(['q4'] if i==3 else [])} for i in range(1,4)],
      'experiment_steps':['待确认建议：7天收集5份合成测试回答，人工确认'], 'next_tasks':['人工审核发布；待确认建议7天完成一次复盘'],
      'landing':{'title':'合成测试落地页','summary':'合成测试，验证需求','target_users':source['target_customer'],'value_proposition':source['value_proposition'],'features':['待验证服务'],'disclaimer':'需求验证草稿，未产生商业成交'},
      'questions':[{'id':f'q{i}','title':f'合成测试问题{i}','type':('text' if i==4 else 'multiple' if i==3 else 'single'),'required':True,
        'options':[] if i==4 else [{'id':'a','label':'愿意'},{'id':'b','label':'不愿意'},{'id':'c','label':'不确定'}]} for i in range(1,5)]}
    if context.get('approved_strategy'):
        plan=copy.deepcopy(context['source_plan']);plan['landing']['title']='第二轮合成测试：验证新目标'
        plan['questions'][0]['title']='第二轮合成测试问题1'
    return {'method':'小样本探索性问卷','method_reason':'检验自报意向而非实际付费','sample_limits':['合成测试，不代表真实用户'],
      'success_metrics':['待确认建议：7天5份回答，人工审核阈值'],
      'question_checks':[{'question_id':q['id'],'hypothesis_ids':[h['id'] for h in plan['hypotheses'] if q['id'] in h['question_ids']],
                         'rationale':'对应待验证假设','bias_check':'包含拒绝与不确定，不宣称成交'} for q in plan['questions']], 'plan':plan,'cold_start_kit':recruitment_kit(plan)}

def review(context):
    count=context['statistics']['response_count']
    return {'summary':'合成测试，证据不足以判断市场','hypothesis_assessments':[
      {'hypothesis_id':f'h{i}','status':'insufficient_evidence','reason':'合成测试样本，不能代表真实市场',
       'evidence_refs':[{'question_id':f'q{i}','option_id':'a'}] if count else []} for i in range(1,4)],
      'key_feedback':[],'limitations':['合成测试，小样本且问卷意向不是付款'],
      'next_actions':[{'action':'继续验证','owner_role':'学生','completion_criterion':'待确认建议：7天收集5份反馈'}]}

def strategy(context):
    refs=context['report']['review']['hypothesis_assessments'][0]['evidence_refs']
    return {'diagnosis':'合成测试样本不足，需改进实验问题','hypothesis_decisions':[
       {'hypothesis_id':f'h{i}','decision':'modify' if i==1 else 'retain','reason':'证据不足，继续检验','evidence_refs':refs if i==1 else []} for i in range(1,4)],
       'next_goals':['待确认建议：7天验证修改后的问题，合成测试'],'constraints':['不宣称真实成交，保留拒绝选项'],
       'suggested_changes':[{'field':'questions','before':'当前自报意向问题','after':'更清楚的意向问题','reason':'合成测试证据不足',
                            'hypothesis_ids':['h1'],'evidence_refs':refs}], 'unverified':['新实验效果尚未验证']}

class FakeModel:
    def __init__(self):self.calls=[];self.failure=None
    def __call__(self,prompt,data,settings,**kwargs):
        role='shiyu' if '「识玉」' in prompt else 'zhuoyu' if '「琢玉」' in prompt else 'gongyu' if '「攻玉」' in prompt else 'jianyu'
        self.calls.append((role,copy.deepcopy(data)))
        if self.failure:raise self.failure
        result={'shiyu':lambda c:opportunity(),'zhuoyu':design,'jianyu':review,'gongyu':strategy}[role](data['context'])
        return ModelReply(json.dumps(result,ensure_ascii=False),CallInfo('mock-demo-offline',now(),0.01,True,200,{'total_tokens':10}))

class FakeMail:
    def __init__(self):self.messages=[]
    def send_code(self,email,code):self.messages.append((email,code))

@pytest.fixture
def settings(tmp_path):
    return Settings(app_env='test',database_path=tmp_path/'isolated.sqlite3',llm_base_url='https://model.invalid',llm_api_key=SecretStr('isolated'),llm_model='mock-demo-offline',admin_user='test-admin',admin_password=SecretStr('isolated-admin-fixture-123!'),public_base_url='https://test.shiyu.invalid',invite_registration_enabled=True,registration_otp_secret=SecretStr(secrets.token_urlsafe(32)),mail_provider='disabled')

@pytest.fixture
def model(monkeypatch):
    fake=FakeModel();monkeypatch.setattr('app.agents.runtime.call_model',fake);return fake

@pytest.fixture
def client(settings,model):
    with TestClient(create_app(settings),base_url=settings.public_base_url) as c:
        c.app.state.email_sender=FakeMail()
        assert c.post('/api/auth/login',headers=HEADERS,json={'username':settings.admin_user,'password':settings.admin_password.get_secret_value()}).status_code==200
        yield c

PROJECT={'name':'合成测试创业项目','idea':'为大学生提供创业验证练习','target_users':'大学生','skills':'教学设计','budget_cny':100.0,'validation_goal':'检验试用与购买意愿，不能混同','data_kind':'test'}

def create_project(client):
    r=client.post('/api/projects',headers=HEADERS,json=PROJECT);assert r.status_code==201,r.text
    return r.json()['data']['project_id']
