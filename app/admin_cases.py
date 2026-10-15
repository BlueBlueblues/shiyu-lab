"""Temporary, session-authenticated administrator case reader. No write or AI actions."""
import json
import re
import sqlite3
from contextlib import contextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from app.passwords import LEGACY_ADMIN_ID
from app.users import SESSION_COOKIE, session_user

router = APIRouter()
ROLES = {'shiyu': '识玉', 'zhuoyu': '琢玉', 'jianyu': '鉴玉', 'gongyu': '攻玉'}

@contextmanager
def readonly(path):
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        yield connection
    finally:
        connection.rollback()
        connection.close()

def administrator(request: Request):
    # Intentionally does not reuse the owner-scoped router or accept Basic fallback.
    with readonly(request.app.state.settings.database_path) as connection:
        user = session_user(connection, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(303, headers={'Location': '/login'})
    if user['id'] != LEGACY_ADMIN_ID:
        raise HTTPException(403, '只有已登录管理员可以查看验证案例。')
    request.state.current_user = user

def obj(value):
    try:
        parsed = json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}

def redact(value, private_texts=()):
    """Conservative display projection, not an export of stored JSON or responses."""
    if isinstance(value, list):
        return [redact(item, private_texts) for item in value]
    if isinstance(value, dict):
        return {k: redact(v, private_texts) for k, v in value.items() if k not in {'email','username','user_id','actor_user_id','response_id','session','token','token_hash','invitation','answers','texts','key_feedback'}}
    if not isinstance(value, str):
        return value
    for text in sorted(set(private_texts), key=len, reverse=True):
        if text.strip():
            value = value.replace(text, '〔开放回答已隐藏〕')
    # Quoted respondent excerpts are not necessary for the administrator summary.
    value = re.sub(r'“[^”]*”|「[^」]*」|‘[^’]*’', '〔引文已隐藏〕', value)
    value = re.sub(r'https?://\S+|www\.\S+', '〔链接已隐藏〕', value, flags=re.I)
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '〔邮箱已隐藏〕', value)
    value = re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '〔标识已隐藏〕', value, flags=re.I)
    value = re.sub(r'(?<!\d)(?:\d[\d ()+-]{6,}\d[\dXx]?)(?!\d)', '〔号码已隐藏〕', value)
    value = re.sub(r'(?:姓名|联系人|学号|身份证号|手机号|微信号|QQ号)\s*[:：=]\s*[^\s，。；;]{1,40}', '〔身份信息已隐藏〕', value, flags=re.I)
    return value

def fields(data, names):
    return {name: data[name] for name in names.split() if name in data}

def evidence(items):
    # Text evidence is summarized at question level; never expose response IDs.
    return [fields(item, 'question_id option_id') for item in items if isinstance(item, dict)]

def review(data):
    result = fields(data, 'summary limitations next_actions')
    result['hypothesis_assessments'] = []
    for assessment in data.get('hypothesis_assessments', []):
        item = fields(assessment, 'hypothesis_id status reason')
        refs = assessment.get('evidence_refs', [])
        if any('response_id' in r for r in refs):
            item['reason'] = '涉及开放回答的分析叙述已隐藏；仅展示题目级证据与汇总。'
        item['evidence_refs'] = evidence(refs)
        result['hypothesis_assessments'].append(item)
    return result

def strategy(data):
    result = fields(data, 'diagnosis next_goals constraints unverified')
    result['suggested_changes'] = []
    for change in data.get('suggested_changes', data.get('diff', [])):
        item = fields(change, 'field before after reason hypothesis_ids')
        item['evidence_refs'] = evidence(change.get('evidence_refs', []))
        result['suggested_changes'].append(item)
    result['hypothesis_decisions'] = []
    for decision in data.get('hypothesis_decisions', []):
        item = fields(decision, 'hypothesis_id decision reason')
        item['evidence_refs'] = evidence(decision.get('evidence_refs', []))
        result['hypothesis_decisions'].append(item)
    return result

def project_summary(connection, row):
    pid = row['id']
    experiments = connection.execute('''SELECT id,round_number,published_at FROM experiments
        WHERE project_id=? ORDER BY round_number DESC''', (pid,)).fetchall()
    role_set = {t[0] for t in connection.execute('''SELECT DISTINCT t.role FROM agent_tasks t
        JOIN agent_artifacts a ON a.task_id=t.id WHERE t.project_id=? AND t.status='succeeded' ''', (pid,))}
    count = connection.execute('''SELECT COUNT(*) FROM responses r JOIN experiments e ON e.id=r.experiment_id
        WHERE e.project_id=?''', (pid,)).fetchone()[0]
    approved = connection.execute('SELECT COUNT(*) FROM iteration_proposals WHERE project_id=? AND confirmed_at IS NOT NULL', (pid,)).fetchone()[0]
    stage = '已保存想法' if not role_set else '已有识玉成果，待实验设计'
    if experiments:
        latest = experiments[0]
        stage = '实验草稿，待创建者审核' if not latest['published_at'] else '已发布，收集反馈'
        if connection.execute('SELECT 1 FROM reports WHERE experiment_id=?', (latest['id'],)).fetchone():
            stage = '已有鉴玉报告，请核对快照与新增反馈'
        if connection.execute('''SELECT 1 FROM iteration_proposals WHERE source_experiment_id=?
            AND confirmed_at IS NULL AND rejected_at IS NULL''', (latest['id'],)).fetchone():
            stage = '已有攻玉提案，等待创建者决定'
    owner_admin = row['user_id'] == LEGACY_ADMIN_ID
    return {'id': pid, 'name': redact(row['name']), 'created_at': row['created_at'],
        'owner_label': '管理员项目' if owner_admin else '普通用户项目', 'test': row['data_kind'] == 'test',
        'round_number': experiments[0]['round_number'] if experiments else 1, 'stage': stage,
        'roles': {role: role in role_set for role in ROLES}, 'published': sum(bool(e['published_at']) for e in experiments),
        'responses': count, 'approved': approved}

def case_detail(connection, project_id, base_url):
    row = connection.execute('SELECT id,name,idea,target_users,validation_goal,created_at,user_id,data_kind FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '案例不存在。')
    case = project_summary(connection, row)
    case.update(idea=redact(row['idea']), target_users=redact(row['target_users']), validation_goal=redact(row['validation_goal']))
    raw_reports = connection.execute('''SELECT r.experiment_id,r.created_at,r.response_count,r.cutoff_response_id,r.statistics_json,r.review_json
        FROM reports r JOIN experiments e ON e.id=r.experiment_id WHERE e.project_id=? ORDER BY r.created_at''', (project_id,)).fetchall()
    private_texts = [text['text'] for r in raw_reports for q in obj(r['statistics_json']).get('questions', [])
                     for text in q.get('texts', []) if isinstance(text, dict) and isinstance(text.get('text'), str)]
    artifacts = []
    for task in connection.execute('''SELECT t.role,t.round_number,t.version,t.finished_at,a.output_json
        FROM agent_tasks t JOIN agent_artifacts a ON a.task_id=t.id
        WHERE t.project_id=? AND t.status='succeeded' ORDER BY t.round_number,t.started_at,t.version''', (project_id,)):
        data = obj(task['output_json'])
        if task['role'] == 'shiyu':
            data = fields(data, 'target_customer value_proposition hypotheses pain_points applicability_limits unverified_notice')
        elif task['role'] == 'zhuoyu':
            data = fields(data, 'method method_reason success_metrics sample_limits plan cold_start_kit')
        elif task['role'] == 'jianyu':
            data = review(data)
        elif task['role'] == 'gongyu':
            data = strategy(data)
        else:
            continue
        artifacts.append({'role': task['role'], 'round_number': task['round_number'], 'version': task['version'],
                          'finished_at': task['finished_at'], 'output': redact(data, private_texts)})
    case['rounds'] = []
    for experiment in connection.execute('''SELECT id,round_number,published_at,created_at,plan_json,landing_json,questions_json,public_token,parent_experiment_id
        FROM experiments WHERE project_id=? ORDER BY round_number''', (project_id,)):
        eid = experiment['id']
        current = connection.execute('SELECT COUNT(*),MAX(id) FROM responses WHERE experiment_id=?', (eid,)).fetchone()
        saved = []
        for report in raw_reports:
            if report['experiment_id'] != eid:
                continue
            statistics = obj(report['statistics_json'])
            safe_questions = []
            for q in statistics.get('questions', []):
                item = fields(q, 'id type answered_count options')
                safe_questions.append(item)
            saved.append({'created_at': report['created_at'], 'response_count': report['response_count'],
                'current_count': current[0], 'stale': report['response_count'] != current[0] or (report['cutoff_response_id'] or 0) != (current[1] or 0),
                'statistics': redact(safe_questions, private_texts), 'review': redact(review(obj(report['review_json'])), private_texts)})
        plan = obj(experiment['plan_json'])
        # Published/editable content is the stored experiment, not an AI artifact substituted for it.
        plan['landing'] = obj(experiment['landing_json'])
        try: plan['questions'] = json.loads(experiment['questions_json'])
        except (ValueError, TypeError): plan['questions'] = []
        proposals = []
        for proposal in connection.execute('''SELECT proposal_json,created_at,confirmed_at,rejected_at,decision_json,created_experiment_id
            FROM iteration_proposals WHERE project_id=? AND source_experiment_id=? ORDER BY created_at''', (project_id,eid)):
            destination = connection.execute('SELECT round_number FROM experiments WHERE id=? AND project_id=?', (proposal['created_experiment_id'],project_id)).fetchone()
            decision = fields(obj(proposal['decision_json']), 'goals constraints')
            raw = obj(proposal['proposal_json'])
            saved_strategy = raw.get('strategy', raw)
            if 'next_plan' in raw:
                saved_strategy = {'diagnosis': raw.get('diagnosis'), 'unverified': ['旧版建议，不代表本轮琢玉成果'],
                    'suggested_changes': [{**change, 'before': plan.get(change.get('field')), 'after': raw['next_plan'].get(change.get('field')), 'evidence_refs': []} for change in raw.get('changes', [])]}
            proposals.append({'created_at': proposal['created_at'], 'confirmed_at': proposal['confirmed_at'],
                'rejected_at': proposal['rejected_at'], 'next_round': destination[0] if destination else None,
                'strategy': redact(strategy(saved_strategy), private_texts), 'decision': redact(decision, private_texts)})
        # Only actual public routes; never a private workspace/invitation URL.
        base = urlsplit(base_url)
        url = None
        if experiment['published_at'] and base.scheme in {'http','https'} and base.hostname and not base.username and not base.password and not base.query and not base.fragment and base.path in {'','/'} and re.fullmatch(r'[A-Za-z0-9_-]{22,}',experiment['public_token']):
            url = base_url.rstrip('/')+'/e/'+experiment['public_token']+'/survey'
        case['rounds'].append({'number':experiment['round_number'],'created_at':experiment['created_at'],
            'published_at':experiment['published_at'], 'responses':current[0], 'plan':redact(plan, private_texts),
            'public_url':url, 'reports':saved, 'proposals':proposals,
            'artifacts':[a for a in artifacts if a['round_number']==experiment['round_number']]})
    known = {r['number'] for r in case['rounds']}
    for number in sorted({a['round_number'] for a in artifacts} - known):
        case['rounds'].append({'number':number,'artifacts':[a for a in artifacts if a['round_number']==number],
            'reports':[],'proposals':[],'plan':{},'public_url':None,'responses':0,'published_at':None,'created_at':None})
    case['rounds'].sort(key=lambda r:r['number'])
    return case

@router.get('/admin/cases', dependencies=[Depends(administrator)], include_in_schema=False)
def cases(request: Request):
    with readonly(request.app.state.settings.database_path) as c:
        rows = c.execute('SELECT id,name,created_at,user_id,data_kind FROM projects ORDER BY created_at DESC').fetchall()
        projects = [project_summary(c,row) for row in rows]
    return request.app.state.templates.TemplateResponse(request=request, name='admin_cases.html', context={'cases':projects,'roles':ROLES})

@router.get('/admin/cases/{project_id}', dependencies=[Depends(administrator)], include_in_schema=False)
def detail(project_id: str, request: Request):
    with readonly(request.app.state.settings.database_path) as c:
        case = case_detail(c, project_id, request.app.state.settings.public_base_url)
    return request.app.state.templates.TemplateResponse(request=request, name='admin_case.html', context={'case':case,'roles':ROLES})
