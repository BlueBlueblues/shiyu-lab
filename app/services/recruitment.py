"""Owner-scoped, read-only views of the artifact that actually built an experiment."""
import json
import re
from urllib.parse import urlsplit
from app.agents.recruitment_contracts import ColdStartKit, LINK_PLACEHOLDER, TIME_PLACEHOLDER

CHANNEL_LABELS = {'class_or_course':'班级与课程', 'student_club':'校园社团', 'offline_event':'线下活动',
                  'topic_community':'主题社区', 'professional_network':'行业网络', 'personal_network':'个人关系'}

def recruitment_view(connection, project_id, experiment_id, public_base_url):
    view = {'state':'empty', 'kit':None, 'experiment_id':experiment_id, 'project_id':project_id,
            'public_url':None, 'source_artifact_id':None, 'failed_attempt':False}
    if experiment_id is None:
        latest = connection.execute("SELECT status FROM agent_tasks WHERE project_id=? AND role='zhuoyu' ORDER BY started_at DESC,rowid DESC LIMIT 1", (project_id,)).fetchone()
        view['failed_attempt'] = bool(latest and latest['status']=='failed')
        return view
    experiment = connection.execute('SELECT * FROM experiments WHERE id=? AND project_id=?',
                                    (experiment_id, project_id)).fetchone()
    if experiment is None:
        return view
    latest = connection.execute("""SELECT status FROM agent_tasks WHERE project_id=? AND round_number=?
        AND role='zhuoyu' ORDER BY started_at DESC,rowid DESC LIMIT 1""",
        (project_id,experiment['round_number'])).fetchone()
    view['failed_attempt'] = bool(latest and latest['status']=='failed')
    # The create tool stores the exact source_artifact_id, even when that artifact was cached.
    row = connection.execute("""SELECT a.id,a.output_json,t.version FROM tool_calls build
        JOIN agent_runs run ON run.id=build.run_id
        JOIN agent_artifacts a ON a.id=json_extract(build.input_summary_json,'$.source_artifact_id')
        JOIN agent_tasks t ON t.id=a.task_id
        WHERE run.project_id=? AND t.project_id=? AND t.round_number=? AND t.role='zhuoyu'
        AND t.status='succeeded' AND build.tool_name='create_experiment' AND build.status='succeeded'
        AND json_extract(build.output_ref_json,'$.experiment_id')=?
        ORDER BY build.rowid DESC LIMIT 1""", (project_id,project_id,experiment['round_number'],experiment_id)).fetchone()
    view['state'] = 'legacy'
    if row is None:
        return view
    try:
        output = json.loads(row['output_json'])
        if output.get('cold_start_kit') is None:
            return view
        kit = ColdStartKit.model_validate(output['cold_start_kit'])
        from app.agents.contracts import ExperimentDesign
        design = ExperimentDesign.model_validate(output)
    except (ValueError,TypeError):
        view['state']='invalid'
        return view
    # Never trust a model-supplied URL or a request Host; only the configured base and stored token.
    base = urlsplit(public_base_url)
    if experiment['published_at'] and base.scheme in {'https','http'} and base.hostname and not base.username and not base.password and not base.query and not base.fragment and base.path in {'','/'} and re.fullmatch(r'[A-Za-z0-9_-]{22,}',experiment['public_token']):
        view['public_url']=public_base_url.rstrip('/')+'/e/'+experiment['public_token']+'/survey'
    link = view['public_url'] or LINK_PLACEHOLDER
    community = kit.community_message.replace(LINK_PLACEHOLDER,link).replace(TIME_PLACEHOLDER,'时长待试填确认')
    private = kit.private_invitation.replace(TIME_PLACEHOLDER,'时长待试填确认').replace(LINK_PLACEHOLDER,link)
    outline = '\n'.join([private, *[f'{i}. {q.question}\n   追问：'+ '；'.join(q.follow_ups) for i,q in enumerate(kit.interview_questions,1)],
                         '注意：先征得同意，可随时拒绝；不收集与验证无关的敏感信息。', *kit.interview_notes])
    view.update(state='ready',kit=kit.model_dump(mode='json'),target_customer=design.plan.target_customer,
                validation_goal=connection.execute('SELECT validation_goal FROM projects WHERE id=?',(project_id,)).fetchone()[0],
                source_artifact_id=row['id'],source_version=row['version'],round_number=experiment['round_number'],
                community_message=community,private_invitation=private,interview_outline=outline,channel_labels=CHANNEL_LABELS)
    return view
