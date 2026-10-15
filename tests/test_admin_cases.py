import json,sqlite3,secrets,hashlib
from uuid import uuid4
import pytest
from app.db import connect_db
from app.users import create_user,Register,create_session
from app.passwords import LEGACY_ADMIN_ID
from app.admin_cases import readonly,redact
from conftest import HEADERS,PROJECT,create_project
from test_two_rounds import publish_respond_review


def ordinary_project(client,settings):
    pid=create_project(client)
    with connect_db(settings.database_path) as c:
        user=create_user(c,Register(username='case-owner',password='isolated-case-password-123!'))
        c.execute('UPDATE projects SET user_id=?,data_kind=? WHERE id=?',(user.id,'real',pid))
        token=create_session(c,user.id)
    return pid,token


def test_admin_cross_user_reader_and_existing_owner_isolation(client,settings):
    pid,_=ordinary_project(client,settings)
    assert client.get('/projects/'+pid).status_code==404
    for path in ['/admin/cases','/admin/cases/'+pid]:
        r=client.get(path);assert r.status_code==200
        assert 'no-store' in r.headers['cache-control'] and 'noindex' in r.headers['x-robots-tag']
        assert r.headers['referrer-policy']=='no-referrer'
    assert '普通用户项目' in client.get('/admin/cases').text
    assert '尚未完成' in client.get('/admin/cases/'+pid).text
    assert client.get('/admin/cases/'+str(uuid4())).status_code==404


def test_session_only_ordinary_and_anonymous_denied(client,settings):
    pid,token=ordinary_project(client,settings)
    client.cookies.clear()
    # Existing Basic compatibility must NOT grant access to this new Session-only reader.
    client.auth=(settings.admin_user,settings.admin_password.get_secret_value())
    for path in ['/admin/cases','/admin/cases/'+pid]:
        r=client.get(path,follow_redirects=False);assert r.status_code==303 and r.headers['location']=='/login'
        assert 'no-store' in r.headers['cache-control'] and 'noindex' in r.headers['x-robots-tag']
    client.auth=None;client.cookies.set('firstorder_session',token)
    for path in ['/admin/cases','/admin/cases/'+pid]:assert client.get(path).status_code==403
    assert 'nav-cases' not in client.get('/projects').text
    assert client.get('/projects/'+pid).status_code==200
    other=create_project(client)
    with connect_db(settings.database_path) as c:c.execute('UPDATE projects SET user_id=? WHERE id=?',(LEGACY_ADMIN_ID,other))
    assert client.get('/projects/'+other).status_code==404


def test_readonly_connection_enforced(settings,client):
    with readonly(settings.database_path) as c:
        assert c.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):c.execute("DELETE FROM projects")


def test_stale_snapshot_privacy_and_no_side_effects(client,settings,model):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id']
    publish_respond_review(client,pid,eid)
    with connect_db(settings.database_path) as c:
        row=c.execute('SELECT statistics_json,review_json FROM reports WHERE experiment_id=?',(eid,)).fetchone()
        stats=json.loads(row[0]);review=json.loads(row[1]);secret_text='陈小乙的独有开放回答，联系方式不能公开'
        stats['questions'][-1]['texts'][0]['text']=secret_text
        review['summary']='分析引用：'+secret_text+'；邮箱 visitor@example.invalid，电话13812345678'
        review['key_feedback']=['不能出现在管理员HTML的原始引文']
        review['hypothesis_assessments'][0]['evidence_refs']=[{'question_id':'q4','response_id':998877}]
        c.execute('UPDATE reports SET statistics_json=?,review_json=? WHERE experiment_id=?',(json.dumps(stats),json.dumps(review),eid))
        c.execute('INSERT INTO responses(experiment_id,client_submission_id,answers_json,consent,created_at) VALUES(?,?,?,?,?)',(eid,str(uuid4()),'{"q4":"never disclose raw responses"}',1,'2026-10-15T00:00:00+00:00'))
        c.execute('UPDATE projects SET name=? WHERE id=?',('<img src=x onerror=alert(1)>',pid))
    before=sqlite3.connect(settings.database_path)
    digest=hashlib.sha256('\n'.join(before.iterdump()).encode()).hexdigest();before.close();calls=len(model.calls)
    r=client.get('/admin/cases/'+pid);assert r.status_code==200
    assert '当前收到 4 份反馈，此报告仅分析其中 3 份' in r.text
    for forbidden in [secret_text,'visitor@example.invalid','13812345678','998877','never disclose raw responses','不能出现在管理员HTML的原始引文','<img src=x']:
        assert forbidden not in r.text
    assert '&lt;img' in r.text
    assert '实测 · 算学统计' in r.text and '推演 · 策论归因' in r.text
    assert '冷启动招募工具包' in r.text
    assert 'data-testid="iteration-confirm"' not in r.text and '<form' not in r.text
    with sqlite3.connect(settings.database_path) as c:assert hashlib.sha256('\n'.join(c.iterdump()).encode()).hexdigest()==digest
    assert len(model.calls)==calls


def test_history_approval_and_next_round(client,settings):
    pid=create_project(client);eid=client.post(f'/api/projects/{pid}/plan',headers=HEADERS,json={}).json()['data']['experiment_id'];publish_respond_review(client,pid,eid)
    proposal=client.post(f'/api/projects/{pid}/iterations',headers=HEADERS,json={}).json()['data']['proposal_id']
    client.post(f'/api/projects/{pid}/iterations/{proposal}/confirm',headers=HEADERS,json={'confirmed':True,'revised_goals':['人工保留的测试目标']})
    r=client.get('/admin/cases/'+pid);assert r.status_code==200
    for text in ['第 2 轮','原案 · 待辨','新议 · 待试','核讫 · 准复琢','人工保留的测试目标']:assert text in r.text
    assert 'actor_user_id' not in r.text and 'input_json' not in r.text


@pytest.mark.parametrize('method',['post','put','patch','delete'])
def test_no_write_routes(client,method):
    for path in ['/admin/cases','/admin/cases/'+str(uuid4())]:
        response=client.request(method,path,json={});assert response.status_code==405
        assert 'no-store' in response.headers['cache-control'] and 'noindex' in response.headers['x-robots-tag']


def test_redaction_projection():
    assert redact({'email':'private','response_id':42,'summary':'联系姓名：张三 电话13812345678 user@example.invalid'})=={'summary':'联系〔身份信息已隐藏〕 电话〔号码已隐藏〕 〔邮箱已隐藏〕'}
