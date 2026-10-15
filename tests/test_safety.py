import copy,json,sqlite3,threading,time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.db import connect_db
from app.agents.contracts import RoleInput,Opportunity,ExperimentDesign,IterationStrategy
from app.agents.runtime import validate_output
from app.services.llm_client import LLMError,ModelReply,CallInfo
from conftest import HEADERS,PROJECT,create_project,opportunity,design,review,strategy
from test_two_rounds import publish_respond_review

def test_contracts_and_fabricated_evidence():
    with pytest.raises(ValidationError):Opportunity.model_validate({**opportunity(),'fake_market_size':100})
    broken=design({});broken['question_checks'][0]['hypothesis_ids']=['h3']
    with pytest.raises(ValidationError):ExperimentDesign.model_validate(broken)
    from app.schemas import Review
    stats={'response_count':0,'questions':[]}
    inp=RoleInput(project_id='test',round_number=1,project=PROJECT,context={'statistics':stats,'hypotheses':[{'id':f'h{i}','question_ids':[f'q{i}']} for i in range(1,4)]})
    raw=review({'statistics':stats});raw['summary']='99%愿意购买'
    with pytest.raises(ValueError):validate_output('jianyu',Review.model_validate(raw),inp)
    raw=review({'statistics':stats});raw['hypothesis_assessments'][0]['status']='preliminary_support'
    with pytest.raises(ValueError):validate_output('jianyu',Review.model_validate(raw),inp)

def test_bad_json_bounded_and_retry(client,settings,model,monkeypatch):
    pid=create_project(client);calls=[]
    def invalid(*args,**kwargs):
        calls.append(1);return ModelReply('{bad json',CallInfo('mock', 'test',success=True,http_status=200))
    monkeypatch.setattr('app.agents.runtime.call_model',invalid)
    r=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={})
    assert r.status_code==502 and len(calls)==2
    with connect_db(settings.database_path) as c:
        assert c.execute('SELECT status,error_code FROM agent_tasks').fetchone()['status']=='failed'
        assert c.execute('SELECT count(*) FROM experiments').fetchone()[0]==0
    monkeypatch.setattr('app.agents.runtime.call_model',model)
    assert client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).status_code==200
    with connect_db(settings.database_path) as c:
        assert c.execute("SELECT count(*) FROM agent_tasks WHERE role='shiyu'").fetchone()[0]==2

def test_timeout_no_late_writes(client,settings,model):
    pid=create_project(client);model.failure=LLMError('LLM_TIMEOUT')
    r=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={})
    assert r.status_code==504
    assert len(model.calls)==1
    with connect_db(settings.database_path) as c:
        assert c.execute('SELECT count(*) FROM agent_artifacts').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM agent_runs WHERE status='running'").fetchone()[0]==0

def test_concurrent_plan_and_cached_duplicate(client,settings,model,monkeypatch):
    pid=create_project(client);entered=threading.Event();release=threading.Event()
    def slow(*args,**kwargs):entered.set();assert release.wait(10);return model(*args,**kwargs)
    monkeypatch.setattr('app.agents.runtime.call_model',slow)
    with ThreadPoolExecutor(2) as pool:
        future=pool.submit(client.post,f'/api/projects/{pid}/plan',headers=HEADERS,json={})
        assert entered.wait(5)
        duplicate=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={})
        assert duplicate.status_code==409
        release.set();assert future.result().status_code==200
    assert client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).status_code==200
    assert len(model.calls)==2
    with connect_db(settings.database_path) as c:assert c.execute('SELECT count(*) FROM experiments').fetchone()[0]==1

def test_separate_execution_and_role_pages(client,model):
    pid=create_project(client)
    assert client.post(f'/api/projects/{pid}/agents/zhuoyu/run',headers=HEADERS,json={}).status_code==400
    assert client.post(f'/api/projects/{pid}/agents/shiyu/run',headers=HEADERS,json={}).status_code==200
    assert [x[0] for x in model.calls]==['shiyu']
    assert client.post(f'/api/projects/{pid}/agents/zhuoyu/run',headers=HEADERS,json={}).status_code==200
    for role in ['shiyu','zhuoyu','jianyu','gongyu']:
        r=client.get(f'/projects/{pid}/agents/{role}');assert r.status_code==200
    assert client.get(f'/projects/{pid}').status_code==200
    assert client.get(f'/api/projects/{pid}/agent-tasks').status_code==200

def test_auth_invitation_otp_isolation(client,settings):
    from fastapi.testclient import TestClient
    pid=create_project(client)
    exp=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    report=publish_respond_review(client,pid,exp)
    invite=client.post('/api/admin/invitations',headers=HEADERS,json={'expires_hours':24}).json()['data']
    from urllib.parse import urlsplit,parse_qs
    token=parse_qs(urlsplit(invite['link']).query)['invite'][0]
    payload={'email':'controlled@example.invalid','invitation':token}
    fake=client.app.state.email_sender
    assert client.post('/api/auth/register/code',headers=HEADERS,json=payload).status_code==202
    assert len(fake.messages)==1
    assert client.post('/api/auth/register/code',headers=HEADERS,json=payload).status_code==429
    code=fake.messages[0][1]
    reg={**payload,'username':'ordinary-user','password':'isolated-user-fixture-123!','verification_code':code}
    assert client.post('/api/auth/register',headers=HEADERS,json=reg).status_code==201
    assert client.post('/api/auth/register',headers=HEADERS,json=reg).status_code==400
    client.post('/api/auth/logout',headers=HEADERS,json={})
    assert client.post('/api/auth/login',headers=HEADERS,json={'username':reg['username'],'password':reg['password']}).status_code==200
    assert client.get('/api/admin/invitations',headers=HEADERS).status_code==403
    assert client.post('/api/admin/invitations',headers=HEADERS,json={}).status_code==403
    for path in [f'/api/projects/{pid}',f'/api/projects/{pid}/agent-tasks',f'/projects/{pid}/agents/shiyu',f'/api/experiments/{exp}/responses',f'/api/projects/{pid}/report']:
        assert client.get(path,headers=HEADERS).status_code==404,path
    assert client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).status_code==404
    assert client.post('/api/auth/login',headers={**HEADERS,'Origin':'https://evil.invalid'},json={'username':reg['username'],'password':reg['password']}).status_code==403
    own=create_project(client)
    assert client.get(f'/api/projects/{own}').status_code==200

def test_otp_failures_consume_attempts_not_invite(client,settings):
    from urllib.parse import urlsplit,parse_qs
    invite=client.post('/api/admin/invitations',headers=HEADERS,json={}).json()['data']
    token=parse_qs(urlsplit(invite['link']).query)['invite'][0]
    payload={'email':'attempt@example.invalid','invitation':token}
    assert client.post('/api/auth/register/code',headers=HEADERS,json=payload).status_code==202
    code=client.app.state.email_sender.messages[-1][1]
    wrong='000000' if code!='000000' else '111111'
    reg={**payload,'username':'attempt-user','password':'isolated-fixture-123!','verification_code':wrong}
    for _ in range(5):assert client.post('/api/auth/register',headers=HEADERS,json=reg).status_code==400
    assert client.post('/api/auth/register',headers=HEADERS,json={**reg,'verification_code':code}).status_code==400
    with connect_db(settings.database_path) as c:
        assert c.execute('SELECT used_count FROM invitations WHERE id=?',(invite['id'],)).fetchone()[0]==0
        row=c.execute('SELECT attempts,consumed_at FROM registration_challenges').fetchone();assert row['attempts']==5 and row['consumed_at']

def test_stale_approval_and_published_immutability(client,settings,model):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    publish_respond_review(client,pid,eid)
    proposal=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={}).json()['data']['proposal_id']
    with connect_db(settings.database_path) as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute("UPDATE experiments SET landing_json='{}' WHERE id=?",(eid,))
        # Add a valid synthetic feedback row directly to model an intervening submission.
        c.execute('INSERT INTO responses(experiment_id,client_submission_id,answers_json,consent,created_at) VALUES(?,?,?,?,?)',(eid,str(uuid4()),json.dumps({'q1':'a','q2':'b','q3':['a'],'q4':'合成测试'}),1,'test'))
    count=len(model.calls)
    assert client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True}).status_code==409
    assert len(model.calls)==count

def test_second_round_exports(client):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    publish_respond_review(client,pid,eid)
    proposal=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={}).json()['data']['proposal_id']
    eid2=client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True}).json()['data']['experiment_id']
    publish_respond_review(client,pid,eid2)
    for path in [f'/api/projects/{pid}/report.md',f'/projects/{pid}/report',f'/projects/{pid}/iterations/{proposal}']:
        assert client.get(path).status_code==200,path

def test_evidence_and_design_constraints_are_not_cosmetic():
    from app.agents.contracts import IterationStrategy
    stats={'response_count':1,'questions':[{'id':f'q{i}','type':'single','answered_count':1,'options':[{'id':'a','label':'意向','count':1,'percentage':100.0}]} for i in range(1,4)]}
    context={'statistics':stats};analysed=review(context)
    report={'statistics':stats,'review':analysed}
    context={'report':report,'permitted_evidence_refs':[{'question_id':f'q{i}','option_id':'a'} for i in range(1,4)]}
    raw=strategy(context);raw['suggested_changes'][0]['evidence_refs']=[{'question_id':'invented','option_id':'a'}]
    inputs=RoleInput(project_id='fixture',round_number=1,project=PROJECT,context=context)
    with pytest.raises(ValueError):validate_output('gongyu',IterationStrategy.model_validate(raw),inputs)
    good=design({});source=copy.deepcopy(good['plan'])
    inp=RoleInput(project_id='fixture',round_number=2,project=PROJECT,context={'source_plan':source,'approved_strategy':{'goals':['fixture']}})
    with pytest.raises(ValueError):validate_output('zhuoyu',ExperimentDesign.model_validate(good),inp)

def test_mail_failure_preserves_accounts(client,settings,monkeypatch):
    from app.mail import MailDeliveryError
    from urllib.parse import urlsplit,parse_qs
    invite=client.post('/api/admin/invitations',headers=HEADERS,json={}).json()['data']
    token=parse_qs(urlsplit(invite['link']).query)['invite'][0]
    class FailedMail:
        def send_code(self,*args):raise MailDeliveryError(uncertain=True)
    client.app.state.email_sender=FailedMail()
    r=client.post('/api/auth/register/code',headers=HEADERS,json={'email':'failure@example.invalid','invitation':token})
    assert r.status_code==503
    with connect_db(settings.database_path) as c:
        assert c.execute('SELECT used_count FROM invitations WHERE id=?',(invite['id'],)).fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM users').fetchone()[0]==1

def test_startup_recovers_interrupted_tasks(settings):
    from app.db import init_db
    from app.agents.runtime import execute_role
    init_db(settings.database_path,settings.admin_user,settings.admin_password.get_secret_value())
    with connect_db(settings.database_path) as c:
        from app import repositories
        from app.schemas import ProjectCreate
        pid=repositories.create_project(c,ProjectCreate.model_validate(PROJECT))
        c.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'plan','running','test')",('interrupted',pid))
        c.execute("INSERT INTO tool_calls(id,run_id,tool_name,status,started_at,input_summary_json) VALUES('tool','interrupted','make_validation_plan','running','test','{}')")
        c.execute("INSERT INTO agent_tasks(id,project_id,run_id,tool_id,round_number,role,version,status,started_at,input_json,input_digest) VALUES(?,?,'interrupted','tool',1,'shiyu',1,'running','test','{}','fixture')",('task',pid))
    init_db(settings.database_path,settings.admin_user,settings.admin_password.get_secret_value())
    with connect_db(settings.database_path) as c:
        assert c.execute("SELECT error_code FROM agent_tasks WHERE id='task'").fetchone()[0]=='INTERRUPTED'
        assert c.execute("SELECT status FROM agent_runs WHERE id='interrupted'").fetchone()[0]=='failed'
        assert c.execute('SELECT count(*) FROM agent_artifacts').fetchone()[0]==0

def test_ai_pause_preserves_auth_registration_and_reading(settings,model):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(replace(settings,agent_execution_enabled=False)),base_url=settings.public_base_url) as c:
        assert c.post('/api/auth/login',headers=HEADERS,json={'username':settings.admin_user,'password':settings.admin_password.get_secret_value()}).status_code==200
        assert c.get('/register').status_code==200
        assert c.get('/admin/invitations').status_code==200
        pid=create_project(c)
        assert c.get(f'/projects/{pid}').status_code==200
        assert c.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).status_code==503
        assert c.post(f'/api/projects/{pid}/agents/shiyu/run',headers=HEADERS,json={}).status_code==503
        assert not model.calls


def test_repair_receives_candidate_without_executing_it(client,model,monkeypatch):
    from conftest import opportunity
    pid=create_project(client);seen=[]
    def repair(prompt,data,settings,**kwargs):
        if '「识玉」' in prompt:
            seen.append(data)
            if len(seen)==1:
                bad=opportunity();bad['unexpected']='not executable'
                return ModelReply(json.dumps(bad),CallInfo('mock','test',success=True,http_status=200))
            assert data['candidate_output']['unexpected']=='not executable'
            assert data['validation_errors']
        return model(prompt,data,settings,**kwargs)
    monkeypatch.setattr('app.agents.runtime.call_model',repair)
    assert client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).status_code==200
    assert len(seen)==2

def test_late_model_result_cannot_write_artifacts(settings,monkeypatch):
    from app.db import init_db
    from app.agents.runtime import execute_role
    from app import repositories
    from app.schemas import ProjectCreate
    from conftest import FakeModel
    init_db(settings.database_path,settings.admin_user,settings.admin_password.get_secret_value())
    with connect_db(settings.database_path) as c:
        pid=repositories.create_project(c,ProjectCreate.model_validate(PROJECT))
        repositories.start_plan_run(c,pid,'late-run','test')
    fake=FakeModel()
    def delayed(*args,**kwargs):time.sleep(.12);return fake(*args,**kwargs)
    monkeypatch.setattr('app.agents.runtime.call_model',delayed)
    with pytest.raises(LLMError) as error:
        execute_role('shiyu',{'project_id':pid,'round_number':1,'project':PROJECT,'context':{}},settings,'late-run',deadline=time.monotonic()+.03)
    assert error.value.code=='LLM_TIMEOUT'
    time.sleep(.2)
    with connect_db(settings.database_path) as c:
        assert c.execute('SELECT count(*) FROM agent_artifacts').fetchone()[0]==0
        assert c.execute('SELECT status FROM agent_tasks').fetchone()[0]=='failed'


def test_two_valid_question_types_are_allowed():
    # Role chooses suitable types; validation must not require unrelated multiple-choice questions.
    raw=design({});raw['plan']['questions'][2]['type']='single'
    output=ExperimentDesign.model_validate(raw)
    inputs=RoleInput(project_id='fixture',round_number=1,project=PROJECT,context={'opportunity':opportunity()})
    assert validate_output('zhuoyu',output,inputs) is output

def test_confirmation_concurrency_creates_only_one_successor(client,settings,model,monkeypatch):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    publish_respond_review(client,pid,eid)
    proposal=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={}).json()['data']['proposal_id']
    entered=threading.Event();release=threading.Event()
    def slow(*args,**kwargs):entered.set();assert release.wait(10);return model(*args,**kwargs)
    monkeypatch.setattr('app.agents.runtime.call_model',slow)
    url=f'/api/projects/{pid}/iterations/{proposal}/confirm'
    with ThreadPoolExecutor(2) as pool:
        future=pool.submit(client.post,url,headers=HEADERS,json={'confirmed':True})
        assert entered.wait(5)
        assert client.post(url,headers=HEADERS,json={'confirmed':True}).status_code==409
        release.set();assert future.result().status_code==200
    with connect_db(settings.database_path) as c:assert c.execute('SELECT count(*) FROM experiments').fetchone()[0]==2

def test_shiyu_can_reassess_history_without_mutating_published_experiment(client,settings,model):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    publish_respond_review(client,pid,eid)
    with connect_db(settings.database_path) as c:before=dict(c.execute('SELECT * FROM experiments WHERE id=?',(eid,)).fetchone())
    r=client.post(f'/api/projects/{pid}/agents/shiyu/run',headers=HEADERS,json={})
    assert r.status_code==200,r.text
    assert model.calls[-1][0]=='shiyu' and model.calls[-1][1]['context']['history'][0]['statistics']['response_count']==3
    with connect_db(settings.database_path) as c:
        assert before==dict(c.execute('SELECT * FROM experiments WHERE id=?',(eid,)).fetchone())

def test_legacy_proposal_read_and_confirmation_uses_new_designer(client,settings,model):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    report=publish_respond_review(client,pid,eid)
    plan=client.get(f'/api/projects/{pid}').json()['data']['plan']
    next_plan=copy.deepcopy(plan);next_plan['landing']['title']='旧版建议，不假装琢玉已完成'
    proposal=str(uuid4());run=str(uuid4())
    with connect_db(settings.database_path) as c:
        c.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'iterate','succeeded','legacy-fixture')",(run,pid))
        c.execute('INSERT INTO iteration_proposals(id,project_id,source_experiment_id,source_report_id,run_id,proposal_json,created_at) VALUES(?,?,?,?,?,?,?)',
            (proposal,pid,eid,report['id'],run,json.dumps({'diagnosis':'旧版合成测试提案','changes':[{'field':'landing','reason':'合成测试建议','hypothesis_ids':['h1']}],'next_plan':next_plan},ensure_ascii=False),'legacy-fixture'))
    assert client.get(f'/api/projects/{pid}/iterations/{proposal}').json()['data']['contract_version']==3
    assert client.get(f'/projects/{pid}/iterations/{proposal}').status_code==200
    before=len(model.calls)
    assert client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':1}).status_code==422
    r=client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True})
    assert r.status_code==200,r.text
    assert len(model.calls)==before+1 and model.calls[-1][0]=='zhuoyu'
