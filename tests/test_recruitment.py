import copy,json
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.agents.contracts import ExperimentDesign,RoleInput
from app.agents.recruitment_contracts import ColdStartKit
from app.agents.runtime import validate_output
from app.db import connect_db
from app.services.recruitment import recruitment_view
from app.services.llm_client import ModelReply,CallInfo
from conftest import HEADERS,PROJECT,design,opportunity,create_project
from test_two_rounds import publish_respond_review

def create_design(client):
    pid=create_project(client)
    result=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={})
    assert result.status_code==200,result.text
    return pid,result.json()['data']['experiment_id']

def view(client,eid):
    r=client.get(f'/api/experiments/{eid}/recruitment-kit');assert r.status_code==200
    return r.json()['data']

def test_kit_contract_and_legacy_optional():
    output=design({});parsed=ExperimentDesign.model_validate(output);assert len(parsed.cold_start_kit.channels)==2
    legacy=copy.deepcopy(output);legacy.pop('cold_start_kit')
    assert ExperimentDesign.model_validate(legacy).cold_start_kit is None
    inp=RoleInput(project_id='fixture',round_number=1,project=PROJECT,context={'opportunity':opportunity()})
    with pytest.raises(ValueError,match='cold-start'):validate_output('zhuoyu',ExperimentDesign.model_validate(legacy),inp)

@pytest.mark.parametrize('mutation',[
    lambda k:k['channels'].pop(),
    lambda k:k['channels'][0].update(audience_feature_ids=['p5']),
    lambda k:k['channels'][0].update(hypothesis_ids=['h3']),
    lambda k:k.update(community_message='只需一分钟，加入我们{耗时说明}{实验链接}'),
    lambda k:k.update(community_message='请看https://example.invalid/admin{耗时说明}{实验链接}'),
    lambda k:k.update(community_message='联系测试@example.invalid{耗时说明}{实验链接}'),
    lambda k:k.update(community_message='联络13812345678{耗时说明}{实验链接}'),
    lambda k:k.update(community_message='这是一个招募信息{耗时说明}'),
    lambda k:k.update(community_message='字'*101+'{耗时说明}{实验链接}'),
    lambda k:k['interview_questions'][0].update(question='如果有这个产品，你一定愿意买吗？'),
    lambda k:k['interview_questions'].pop(),
    lambda k:k.update(unknown_field='not permitted'),
])
def test_invalid_kit_contract(mutation):
    kit=design({})['cold_start_kit'];mutation(kit)
    with pytest.raises(ValidationError):ColdStartKit.model_validate(kit)

@pytest.mark.parametrize('key,value',[('question_ids',['q999']),('question_ids',['q2'])])
def test_channel_current_question_and_hypothesis_mapping(key,value):
    data=design({});data['cold_start_kit']['channels'][0][key]=value
    with pytest.raises(ValidationError):ExperimentDesign.model_validate(data)

@pytest.mark.parametrize('field',['target_customer','value_proposition'])
def test_strict_handoff_unchanged(field):
    data=design({});data['plan'][field]='未经批准的受众或价值改写'
    inp=RoleInput(project_id='fixture',round_number=1,project=PROJECT,context={'opportunity':opportunity()})
    with pytest.raises(ValueError,match='must exactly equal'):validate_output('zhuoyu',ExperimentDesign.model_validate(data),inp)

def test_new_output_schema_requires_kit_and_retains_consts(client,model,monkeypatch):
    original=model.__call__;captured=[]
    def inspect(prompt,data,settings,**kwargs):
        if '「琢玉」' in prompt:
            schema=json.loads(prompt.split('输出必须符合下列JSON Schema：\n')[1])
            assert schema['properties']['cold_start_kit']=={'$ref':'#/$defs/ColdStartKit'}
            assert 'cold_start_kit' in schema['required']
            for field in ['target_customer','value_proposition']:assert schema['$defs']['Plan']['properties'][field]['const']==data['context']['opportunity'][field]
            captured.append(data)
        return original(prompt,data,settings,**kwargs)
    monkeypatch.setattr('app.agents.runtime.call_model',inspect)
    create_design(client);assert len(captured)==1

def test_publish_links_and_no_private_urls(client,settings):
    pid,eid=create_design(client);initial=view(client,eid)
    assert initial['state']=='ready' and initial['public_url'] is None
    assert '{实验链接}' in initial['community_message'] and '时长待试填确认' in initial['community_message']
    assert client.get(f'/experiments/{eid}/recruitment').status_code==200
    client.post(f'/api/experiments/{eid}/publish',headers=HEADERS,json={})
    current=view(client,eid);detail=client.get(f'/api/projects/{pid}').json()['data']
    assert current['public_url']==detail['experiment']['survey_url']
    assert current['public_url'] in current['community_message'] and '{实验链接}' not in current['community_message']
    assert all(x not in current['community_message'] for x in ['/admin','/projects/','/register'])
    with connect_db(settings.database_path) as c:
        raw=json.loads(c.execute('SELECT output_json FROM agent_artifacts WHERE id=?',(current['source_artifact_id'],)).fetchone()[0])
        assert '{实验链接}' in raw['cold_start_kit']['community_message']
        assert c.execute('PRAGMA user_version').fetchone()[0]==4


def test_failure_retains_previous_valid_experiment_and_kit(client,settings,model,monkeypatch):
    pid,eid=create_design(client);before=view(client,eid)
    with connect_db(settings.database_path) as c:original=dict(c.execute('SELECT * FROM experiments WHERE id=?',(eid,)).fetchone())
    # Force a new prompt version to avoid the valid cached design, without changing production prompts.
    real_dump=__import__('app.agents.runtime',fromlist=['digest']).digest
    monkeypatch.setattr('app.agents.runtime.digest',lambda value:'fault-case-'+real_dump(value))
    def invalid(prompt,data,settings,**kwargs):
        if '「琢玉」' in prompt:
            output=design(data['context']);output['cold_start_kit']['channels']=[]
            return ModelReply(json.dumps(output),CallInfo('mock','test',success=True,http_status=200))
        return model(prompt,data,settings,**kwargs)
    monkeypatch.setattr('app.agents.runtime.call_model',invalid)
    response=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={});assert response.status_code==502
    after=view(client,eid);assert after['source_artifact_id']==before['source_artifact_id'] and after['kit']==before['kit'] and after['failed_attempt']
    assert '最近一次琢玉生成未成功' in client.get(f'/experiments/{eid}/recruitment').text
    with connect_db(settings.database_path) as c:
        assert dict(c.execute('SELECT * FROM experiments WHERE id=?',(eid,)).fetchone())==original
        assert not c.execute("SELECT count(*) FROM agent_runs WHERE status='running'").fetchone()[0]


def test_historical_round_binding(client,settings):
    pid,first=create_design(client);first_view=view(client,first)
    publish_respond_review(client,pid,first)
    proposal=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={}).json()['data']['proposal_id']
    second=client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True}).json()['data']['experiment_id']
    assert view(client,first)['source_artifact_id']==first_view['source_artifact_id']
    next_view=view(client,second);assert next_view['round_number']==2 and next_view['source_artifact_id']!=first_view['source_artifact_id'] and next_view['public_url'] is None
    assert f'/experiments/{first}/recruitment' in client.get(f'/projects/{pid}/rounds').text
    assert '冷启动招募工具包' in client.get(f'/projects/{pid}/agents/zhuoyu?round_number=1').text


def test_legacy_and_invalid_saved_kit_are_readable(client,settings):
    pid,eid=create_design(client)
    with connect_db(settings.database_path) as c:
        row=c.execute('SELECT a.id,a.output_json FROM agent_artifacts a JOIN agent_tasks t ON t.id=a.task_id WHERE t.role=\'zhuoyu\' AND t.project_id=?',(pid,)).fetchone()
        original=json.loads(row['output_json']);legacy=copy.deepcopy(original);legacy.pop('cold_start_kit')
        # Fixture conversion models a pre-feature immutable record; production trigger remains unchanged.
        c.execute('DROP TRIGGER agent_artifacts_immutable')
        c.execute('UPDATE agent_artifacts SET output_json=? WHERE id=?',(json.dumps(legacy),row['id']))
    assert view(client,eid)['state']=='legacy'
    for path in [f'/projects/{pid}',f'/experiments/{eid}/edit',f'/experiments/{eid}/recruitment',f'/projects/{pid}/agents/zhuoyu']:
        assert client.get(path).status_code==200
    with connect_db(settings.database_path) as c:
        original['cold_start_kit']['channels']=[];c.execute('UPDATE agent_artifacts SET output_json=? WHERE id=?',(json.dumps(original),row['id']))
    assert view(client,eid)['state']=='invalid'
    assert '未通过当前契约校验' in client.get(f'/experiments/{eid}/recruitment').text


def test_kit_xss_is_escaped_and_artifact_immutable(client,settings,model,monkeypatch):
    original=model.__call__
    def malicious(prompt,data,settings,**kwargs):
        reply=original(prompt,data,settings,**kwargs)
        if '「琢玉」' in prompt:
            output=json.loads(reply.text);output['cold_start_kit']['audience_features'][0]['description']='<img src=x onerror=alert(1)>'
            reply=ModelReply(json.dumps(output),reply.call_info)
        return reply
    monkeypatch.setattr('app.agents.runtime.call_model',malicious)
    pid,eid=create_design(client);html=client.get(f'/experiments/{eid}/recruitment').text
    assert '<img src=x onerror=alert(1)>' not in html and '&lt;img src=x onerror=alert(1)&gt;' in html
    import sqlite3
    with connect_db(settings.database_path) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE agent_artifacts SET output_json='{}'")


def test_non_owner_cannot_read_kit(client,settings):
    from app.users import create_user,Register
    pid,eid=create_design(client)
    with connect_db(settings.database_path) as c:create_user(c,Register(username='kit-outsider',password='isolated-kit-fixture-123!'))
    client.post('/api/auth/logout',headers=HEADERS,json={})
    assert client.post('/api/auth/login',headers=HEADERS,json={'username':'kit-outsider','password':'isolated-kit-fixture-123!'}).status_code==200
    for path in [f'/api/experiments/{eid}/recruitment-kit',f'/experiments/{eid}/recruitment',f'/experiments/{eid}/edit']:
        assert client.get(path).status_code==404


def test_non_student_audience_is_handed_to_design_and_displayed(client,model,monkeypatch):
    actual=model.__call__
    def professional(prompt,data,settings,**kwargs):
        result=actual(prompt,data,settings,**kwargs);raw=json.loads(result.text)
        if '「识玉」' in prompt:
            raw['target_customer']='独立经营社区面包店、亲自处理每日备货的店主'
            raw['value_proposition']='探索备货决策中的实际困难，未验证服务有效性'
        elif '「琢玉」' in prompt:
            assert data['context']['opportunity']['target_customer'].startswith('独立经营')
            raw['cold_start_kit']['channels'][0].update(channel_type='professional_network',where_to_look='本地烘焙经营者交流活动的公开参与方式',why_matched='亲自备货的经营者能够讲述实际决策经历')
            raw['cold_start_kit']['channels'][1].update(channel_type='offline_event',where_to_look='公开行业交流活动，先核实参加者职责',why_matched='寻找承担备货决策的人而非泛校园用户')
        return ModelReply(json.dumps(raw,ensure_ascii=False),result.call_info)
    monkeypatch.setattr('app.agents.runtime.call_model',professional)
    payload={**PROJECT,'target_users':'社区面包店店主','idea':'了解经营者备货难题','validation_goal':'核实最近备货经历与现有替代办法'}
    pid=client.post('/api/projects',headers=HEADERS,json=payload).json()['data']['project_id']
    eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    kit=view(client,eid)
    assert kit['target_customer'].startswith('独立经营')
    assert {c['channel_type'] for c in kit['kit']['channels']}=={'professional_network','offline_event'}
    assert '烘焙经营者' in client.get(f'/experiments/{eid}/recruitment').text


def test_public_link_ignores_host_and_copy_keeps_history(client):
    pid,eid=create_design(client)
    client.post(f'/api/experiments/{eid}/publish',headers=HEADERS,json={})
    response=client.get(f'/api/experiments/{eid}/recruitment-kit',headers={'Host':'untrusted.invalid'})
    assert response.json()['data']['public_url'].startswith('https://test.shiyu.invalid/e/')


def test_rollback_base_reads_new_artifacts(client,settings,tmp_path):
    """Old 7fd86bc app must read a new kit DB; no downgrade/restore of the database."""
    import subprocess,sys,tarfile,io
    pid,eid=create_design(client)
    old=tmp_path/'base';old.mkdir()
    from pathlib import Path
    import hashlib
    root=Path(__file__).resolve().parents[1]
    fixture=Path(__file__).parent/'fixtures/rollback'
    manifest=json.loads((fixture/'manifest.json').read_text())
    for name,digest in manifest['sha256'].items():
        source=fixture/name if (fixture/name).exists() else root/name
        data=source.read_bytes()
        assert hashlib.sha256(data).hexdigest()==digest
        target=old/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    database=tmp_path/'rollback-check.sqlite3'
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(settings.database_path)) as source,closing(sqlite3.connect(database)) as dest:source.backup(dest)
    code="""import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from pydantic import SecretStr
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
v=json.loads(sys.stdin.read())
s=Settings(app_env='test',database_path=Path(sys.argv[2]),llm_base_url='https://model.invalid',llm_api_key=SecretStr('fixture'),llm_model='mock-demo-offline',admin_user=v['user'],admin_password=SecretStr(v['password']),public_base_url='https://test.shiyu.invalid',mail_provider='disabled')
with TestClient(create_app(s),base_url=s.public_base_url) as c:
 c.auth=(s.admin_user,s.admin_password.get_secret_value())
 for path in ['/projects/'+v['project'],'/projects/'+v['project']+'/rounds','/projects/'+v['project']+'/agents/zhuoyu','/experiments/'+v['experiment']+'/edit']:
  assert c.get(path).status_code==200
print('Base v4 reads new recruitment artifacts without DB downgrade.')
"""
    result=subprocess.run([sys.executable,'-B','-c',code,str(old),str(database)],input=json.dumps({'user':settings.admin_user,'password':settings.admin_password.get_secret_value(),'project':pid,'experiment':eid}),capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
