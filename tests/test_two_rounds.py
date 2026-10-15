import json
from uuid import uuid4
from app.db import connect_db
from conftest import HEADERS,create_project

def publish_respond_review(client,pid,eid):
    r=client.post(f'/api/experiments/{eid}/publish',headers=HEADERS,json={});assert r.status_code==200,r.text
    detail=client.get(f'/api/projects/{pid}').json()['data']
    from urllib.parse import urlsplit
    token=urlsplit(detail['experiment']['survey_url']).path.split('/')[2]
    for i in range(3):
        payload={'client_submission_id':str(uuid4()),'consent':True,'answers':{'q1':'a','q2':'b','q3':['a','c'],'q4':'合成测试文字反馈，非真实用户证据'}}
        r=client.post(f'/api/public/e/{token}/responses',json=payload);assert r.status_code==201,r.text
        assert client.post(f'/api/public/e/{token}/responses',json=payload).status_code==200
    r=client.post(f'/api/projects/{pid}/review',headers=HEADERS,json={});assert r.status_code==200,r.text
    report=client.get(f'/api/projects/{pid}/report').json()['data']
    assert report['statistics']['response_count']==3
    assert report['statistics']['questions'][0]['options'][0]['count']==3
    return report

def test_two_round_workflow(client,settings,model):
    pid=create_project(client)
    r=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={});assert r.status_code==200,r.text
    first=r.json()['data']['experiment_id']
    first_report=publish_respond_review(client,pid,first)
    with connect_db(settings.database_path) as c:
        original=dict(c.execute('SELECT * FROM experiments WHERE id=?',(first,)).fetchone())
    r=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={});assert r.status_code==200,r.text
    proposal=r.json()['data']['proposal_id']
    assert [x[0] for x in model.calls]==['shiyu','zhuoyu','jianyu','gongyu']
    # A duplicate strategy request reuses the pending proposal without spending a model call.
    r=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={});assert r.json()['data']['proposal_id']==proposal
    assert len(model.calls)==4
    assert client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':False}).status_code==422
    r=client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True,'revised_goals':['人工修改：验证更清楚的试用意向']})
    assert r.status_code==200,r.text
    second=r.json()['data']['experiment_id'];assert first!=second
    assert model.calls[-1][0]=='zhuoyu'
    assert model.calls[-1][1]['context']['approved_strategy']['goals']==['人工修改：验证更清楚的试用意向']
    assert client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True}).json()['data']['already_confirmed'] is True
    assert len(model.calls)==5
    second_report=publish_respond_review(client,pid,second)
    with connect_db(settings.database_path) as c:
        assert original==dict(c.execute('SELECT * FROM experiments WHERE id=?',(first,)).fetchone())
        assert c.execute('SELECT count(*) FROM reports').fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM responses').fetchone()[0]==6
        assert c.execute('SELECT count(*) FROM agent_tasks').fetchone()[0]==6
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert client.get(f'/projects/{pid}/rounds').status_code==200
    assert first_report['id']!=second_report['id']

def test_reject_preserves_evidence(client,settings,model):
    pid=create_project(client)
    first=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    publish_respond_review(client,pid,first)
    proposal=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={}).json()['data']['proposal_id']
    assert client.post(f'/api/projects/{pid}/iterations/{proposal}/reject',headers=HEADERS,json={'reason':'人工决定继续招募，不修改实验'}).status_code==200
    assert client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True}).status_code==400
    assert len(model.calls)==4
    with connect_db(settings.database_path) as c:assert c.execute('SELECT count(*) FROM experiments').fetchone()[0]==1
