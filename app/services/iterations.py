"""攻玉 proposes strategy; human approval authorizes a NEW 琢玉 inference."""
import json
import time
from secrets import token_urlsafe
from uuid import uuid4
from app import repositories
from app.db import connect_db
from app.schemas import Plan, IterationPlan
from app.agents.contracts import IterationStrategy, Approval
from app.agents.runtime import execute_role, latest_artifact
from app.services.workflow import WORKFLOW_SECONDS, WorkflowError, utc_now, write_transaction


def iteration_source(connection, project_id, experiment_id=None):
    row=repositories.project_row(connection,project_id,experiment_id)
    if row is None:raise repositories.RepositoryError('NOT_FOUND')
    latest=repositories.project_row(connection,project_id)
    if row['experiment_id']!=latest['experiment_id'] or row['published_at'] is None or row['report_id'] is None:
        raise repositories.RepositoryError('INVALID_STATE')
    if repositories.report_is_stale(row):raise repositories.RepositoryError('STALE_REPORT')
    report=repositories.latest_report(connection,project_id,row['experiment_id'])
    raw=connection.execute('SELECT plan_snapshot_json FROM reports WHERE id=?',(report['id'],)).fetchone()[0]
    source=Plan.model_validate(json.loads(raw or row['round_plan_json']))
    return row,source,report


def role_context(c,project_id,row,source,report):
    opportunity=latest_artifact(c,project_id,'shiyu')
    design=latest_artifact(c,project_id,'zhuoyu',row['round_number'])
    analysis=latest_artifact(c,project_id,'jianyu',row['round_number'])
    history=[dict(r) for r in c.execute('''SELECT e.round_number,e.id,e.parent_experiment_id,
       p.source_report_id,p.confirmed_at,p.decision_json FROM experiments e LEFT JOIN iteration_proposals p
       ON p.created_experiment_id=e.id WHERE e.project_id=? ORDER BY e.round_number''',(project_id,))]
    return {'opportunity':opportunity['output'] if opportunity else None,
        'design':design['output'] if design else None,'source_plan':source.model_dump(mode='json'),
        'report':{k:report[k] for k in ['id','statistics','review','data_kind']},'history':history}, [a['id'] for a in [opportunity,design,analysis] if a]


def finish_failure(settings,run_id,error):
    code=error.code if isinstance(error,(WorkflowError,repositories.RepositoryError)) else getattr(error,'code','INTERNAL_ERROR')
    with connect_db(settings.database_path) as c:
        repositories.fail_run(c,run_id,utc_now(),code,'任务未完成，已有证据保留，可手动重试。')
    return WorkflowError(code,run_id)


def run_iteration(project_id,settings):
    deadline=time.monotonic()+WORKFLOW_SECONDS-5
    run_id,proposal_id=str(uuid4()),str(uuid4())
    try:
        with write_transaction(settings,deadline) as c:
            project=repositories.load_project_input(c,project_id)
            if c.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'",(project_id,)).fetchone():raise repositories.RepositoryError('RUN_IN_PROGRESS')
            row,source,report=iteration_source(c,project_id)
            existing=c.execute('''SELECT id,run_id FROM iteration_proposals WHERE project_id=? AND source_report_id=?
                AND rejected_at IS NULL AND created_experiment_id IS NULL ORDER BY created_at DESC LIMIT 1''',(project_id,report['id'])).fetchone()
            if existing:return {'proposal_id':existing['id'],'run_id':existing['run_id'],'status':'awaiting_confirmation','already_proposed':True}
            context,sources=role_context(c,project_id,row,source,report)
            round_number=row['round_number']
            c.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'iterate','running',?)",(run_id,project_id,utc_now()))
    except repositories.RepositoryError as e:raise WorkflowError(e.code) from None
    try:
        strategy,artifact_id=execute_role('gongyu',{'project_id':project_id,'round_number':round_number,
           'project':project.model_dump(mode='json'),'sources':sources,'context':context},settings,run_id,deadline=deadline)
        with write_transaction(settings,deadline) as c:
            _,_,current=iteration_source(c,project_id,row['experiment_id'])
            if current['id']!=report['id']:raise repositories.RepositoryError('STALE_REPORT')
            c.execute('''INSERT INTO iteration_proposals(id,project_id,source_experiment_id,source_report_id,run_id,proposal_json,created_at)
              VALUES(?,?,?,?,?,?,?)''',(proposal_id,project_id,row['experiment_id'],report['id'],run_id,
              json.dumps({'contract_version':4,'strategy':strategy.model_dump(mode='json'),'artifact_id':artifact_id},ensure_ascii=False),utc_now()))
            repositories.complete_plan_run(c,run_id,utc_now())
        return {'proposal_id':proposal_id,'run_id':run_id,'status':'awaiting_confirmation'}
    except BaseException as error:
        failure=finish_failure(settings,run_id,error)
        if not isinstance(error,Exception):raise
        raise failure from None


def get_proposal(c,project_id,proposal_id):
    row=c.execute('SELECT * FROM iteration_proposals WHERE id=? AND project_id=?',(proposal_id,project_id)).fetchone()
    if row is None:raise WorkflowError('NOT_FOUND')
    raw=json.loads(row['proposal_json'])
    result={k:row[k] for k in row.keys() if k!='proposal_json'}
    if raw.get('contract_version')==4:
        strategy=IterationStrategy.model_validate(raw['strategy']).model_dump(mode='json')
        return {**result,**strategy,'contract_version':4,'artifact_id':raw['artifact_id'],'diff':strategy['suggested_changes']}
    # Read legacy outputs truthfully. Legacy next_plan is a suggestion, NOT a new 琢玉 result.
    old=IterationPlan.model_validate(raw)
    source=json.loads(c.execute('SELECT plan_json FROM experiments WHERE id=?',(row['source_experiment_id'],)).fetchone()[0])
    diff=[{'field':item.field,'before':source[item.field],'after':old.next_plan.model_dump()[item.field],
        'reason':item.reason,'hypothesis_ids':item.hypothesis_ids,'evidence_refs':[]} for item in old.changes]
    return {**result,'contract_version':3,'diagnosis':old.diagnosis,'next_goals':[old.diagnosis],
      'constraints':[item.reason for item in old.changes],'diff':diff,'unverified':['旧版提案；确认后将由琢玉独立设计'],
      'hypothesis_decisions':[],'suggested_changes':diff}


def reject_iteration(project_id,proposal_id,reason,settings):
    try:
        with write_transaction(settings,time.monotonic()+10) as c:
            proposal=get_proposal(c,project_id,proposal_id)
            if proposal['created_experiment_id']:raise repositories.RepositoryError('INVALID_STATE')
            if c.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'",(project_id,)).fetchone():raise repositories.RepositoryError('RUN_IN_PROGRESS')
            c.execute('UPDATE iteration_proposals SET rejected_at=COALESCE(rejected_at,?),decision_json=? WHERE id=?',
                      (utc_now(),json.dumps({'decision':'rejected','reason':reason,'actor_user_id':c.execute('SELECT user_id FROM projects WHERE id=?',(project_id,)).fetchone()[0]},ensure_ascii=False),proposal_id))
        return {'proposal_id':proposal_id,'status':'rejected'}
    except repositories.RepositoryError as e:raise WorkflowError(e.code) from None


def confirm_iteration(project_id,proposal_id,settings,approval=None):
    approval=Approval.model_validate(approval or {'confirmed':True})
    deadline=time.monotonic()+WORKFLOW_SECONDS-5
    run_id,tool_id=str(uuid4()),str(uuid4())
    try:
        with write_transaction(settings,deadline) as c:
            proposal=get_proposal(c,project_id,proposal_id)
            if proposal['created_experiment_id']:
                return {'experiment_id':proposal['created_experiment_id'],'already_confirmed':True}
            if proposal['rejected_at']:raise repositories.RepositoryError('INVALID_STATE')
            if c.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'",(project_id,)).fetchone():raise repositories.RepositoryError('RUN_IN_PROGRESS')
            row,source,report=iteration_source(c,project_id,proposal['source_experiment_id'])
            if report['id']!=proposal['source_report_id']:raise repositories.RepositoryError('STALE_REPORT')
            project=repositories.load_project_input(c,project_id)
            context,sources=role_context(c,project_id,row,source,report)
            if proposal.get('artifact_id'):sources.append(proposal['artifact_id'])
            decision={'decision':'approved','at':utc_now(),'actor_user_id':row['user_id'],
                'goals':approval.revised_goals or proposal['next_goals'],
                'constraints':approval.revised_constraints or proposal['constraints']}
            context['approved_strategy']={**decision,'suggested_changes':proposal['diff']}
            c.execute('UPDATE iteration_proposals SET decision_json=? WHERE id=?',(json.dumps(decision,ensure_ascii=False),proposal_id))
            c.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'build','running',?)",(run_id,project_id,utc_now()))
    except repositories.RepositoryError as e:raise WorkflowError(e.code) from None
    try:
        design,artifact_id=execute_role('zhuoyu',{'project_id':project_id,'round_number':row['round_number']+1,
            'project':project.model_dump(mode='json'),'sources':sources,'context':context},settings,run_id,deadline=deadline)
        with write_transaction(settings,deadline) as c:
            _,_,current=iteration_source(c,project_id,row['experiment_id'])
            if current['id']!=report['id']:raise repositories.RepositoryError('STALE_REPORT')
            latest=get_proposal(c,project_id,proposal_id)
            if latest['rejected_at'] or latest['created_experiment_id']:raise repositories.RepositoryError('INVALID_STATE')
            experiment_id=str(uuid4());now=utc_now()
            repositories.start_tool(c,run_id,tool_id,'create_experiment',now,
                {'agent':'琢玉','execution':'program','proposal_id':proposal_id,'source_artifact_id':artifact_id,'source_report_id':report['id'],'confirmed':True})
            repositories.save_experiment(c,project_id,design.plan,experiment_id,token_urlsafe(32),now,row['experiment_id'])
            c.execute('UPDATE iteration_proposals SET confirmed_at=?,created_experiment_id=? WHERE id=?',(now,experiment_id,proposal_id))
            repositories.finish_tool(c,tool_id,now,{'experiment_id':experiment_id,'artifact_id':artifact_id})
            repositories.complete_plan_run(c,run_id,now)
        return {'experiment_id':experiment_id,'run_id':run_id,'already_confirmed':False}
    except BaseException as error:
        failure=finish_failure(settings,run_id,error)
        if not isinstance(error,Exception):raise
        raise failure from None
