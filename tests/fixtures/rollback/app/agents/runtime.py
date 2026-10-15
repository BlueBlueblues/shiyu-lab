"""Bounded role runner. Model threads never write state or execute tools."""
import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty
from threading import Thread
from uuid import uuid4

from app.db import connect_db
from app.schemas import Plan
from app.agents.contracts import ROLE_OUTPUTS, ROLE_NAMES, RoleInput
from app.services.llm_client import call_model, extract_json_object, LLMError, CallInfo

PROMPTS = {'shiyu':'opportunity.txt','zhuoyu':'design.txt','jianyu':'review.txt','gongyu':'strategy.txt'}
TOOLS = {'shiyu':'make_validation_plan','zhuoyu':'make_validation_plan','jianyu':'analyze_experiment','gongyu':'propose_iteration'}

def now():
    return datetime.now(timezone.utc).isoformat()

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def latest_artifact(connection, project_id, role, round_number=None):
    sql = '''SELECT a.*,t.id AS task_id,t.round_number,t.version,t.role FROM agent_artifacts a
        JOIN agent_tasks t ON t.id=a.task_id WHERE t.project_id=? AND t.role=? AND t.status='succeeded' '''
    args = [project_id,role]
    if round_number is not None:
        sql += ' AND t.round_number=?';args.append(round_number)
    row=connection.execute(sql+' ORDER BY t.round_number DESC,t.version DESC LIMIT 1',args).fetchone()
    return {**dict(row),'output':json.loads(row['output_json'])} if row else None

def validate_evidence(refs, statistics, allowed=None):
    questions={q['id']:q for q in statistics['questions']}
    for ref in refs:
        r=ref.model_dump() if hasattr(ref,'model_dump') else ref
        q=questions.get(r['question_id'])
        if not q: raise ValueError('Unknown evidence question')
        if 'option_id' in r:
            if q['type']=='text' or r['option_id'] not in {o['id'] for o in q['options']}:raise ValueError('Unknown evidence option')
        elif q['type']!='text' or r['response_id'] not in {t['response_id'] for t in q['texts']}:raise ValueError('Unknown evidence response')
        if allowed is not None and canonical(r) not in allowed:raise ValueError('Evidence not in source report')

def validate_output(role, output, inputs):
    context=inputs.context
    if role=='zhuoyu':
        source=context.get('opportunity')
        if source and not context.get('approved_strategy'):
            if {h.id:h.statement for h in output.plan.hypotheses}!={h['id']:h['statement'] for h in source['hypotheses']}:raise ValueError('Designer changed opportunity hypotheses')
            for field in ['target_customer','value_proposition']:
                if getattr(output.plan,field)!=source[field]:
                    raise ValueError('plan.'+field+' must exactly equal context.opportunity.'+field+'; copy the unchanged source text')
        previous=context.get('source_plan')
        if previous and previous['landing']==output.plan.landing.model_dump() and previous['questions']==[q.model_dump() for q in output.plan.questions]:raise ValueError('No experimental change')
    if role=='jianyu':
        stats=context['statistics']
        for item in output.hypothesis_assessments:
            validate_evidence(item.evidence_refs,stats)
        import re
        known_percentages={float(o['percentage']) for q in stats['questions'] for o in q.get('options',[]) if o['percentage'] is not None}
        known_counts={stats['response_count'],*[q['answered_count'] for q in stats['questions']],
                      *[o['count'] for q in stats['questions'] for o in q.get('options',[])]}
        findings=[output.summary,*output.key_feedback,*[a.reason for a in output.hypothesis_assessments]]
        for text in findings:
            if any(float(n) not in known_percentages for n in re.findall(r'(\d+(?:\.\d+)?)\s*[%％]',text)):
                raise ValueError('Unsupported numeric percentage')
            if any(int(n) not in known_counts for n in re.findall(r'(\d+)\s*份(?:回答|反馈|问卷)',text)):
                raise ValueError('Unsupported response count')
        hypotheses={h['id']:set(h['question_ids']) for h in context['hypotheses']}
        for item in output.hypothesis_assessments:
            if any(ref.question_id not in hypotheses[item.hypothesis_id] for ref in item.evidence_refs):
                raise ValueError('Evidence does not address this hypothesis')
        if stats['response_count']==0:
            for item in output.hypothesis_assessments:
                if item.status!='insufficient_evidence' or item.evidence_refs:raise ValueError('Zero feedback requires insufficient evidence')
    if role=='gongyu':
        report=context['report']
        allowed={canonical(r) for r in context['permitted_evidence_refs']}
        for item in [*output.hypothesis_decisions,*output.suggested_changes]:
            validate_evidence(item.evidence_refs,report['statistics'],allowed)
    return output

def execute_role(role, inputs, settings, run_id, *, deadline, validator=None):
    inputs=RoleInput.model_validate(inputs)
    if role == 'zhuoyu':
        inputs.context['design_constraints']={'max_questions':6,'min_questions':4,'allowed_question_types':['single','multiple','text'],'plan_top_level_fields':list(Plan.model_fields)}
    if role == 'gongyu':
        refs=[]
        for question in inputs.context['report']['statistics']['questions']:
            refs.extend({'question_id':question['id'],'option_id':o['id']} for o in question.get('options',[]))
            refs.extend({'question_id':question['id'],'response_id':t['response_id']} for t in question.get('texts',[]))
        inputs.context['permitted_evidence_refs']=refs
    prompt_text=(Path(__file__).resolve().parent.parent/'prompts'/PROMPTS[role]).read_text()
    schema=ROLE_OUTPUTS[role].model_json_schema()
    if role=='zhuoyu' and inputs.context.get('opportunity') and not inputs.context.get('approved_strategy'):
        source=inputs.context['opportunity']
        for field in ['target_customer','value_proposition']:
            schema['$defs']['Plan']['properties'][field]['const']=source[field]
    inputs.context['role_config']={'role':role,'contract_version':4,
        'prompt_digest':hashlib.sha256(prompt_text.encode()).hexdigest(),'output_schema_digest':digest(schema)}
    task_id,tool_id,artifact_id=str(uuid4()),str(uuid4()),str(uuid4())
    started=now(); raw=inputs.model_dump(mode='json')
    with connect_db(settings.database_path) as c:
        c.execute('BEGIN IMMEDIATE')
        run=c.execute('SELECT project_id,status FROM agent_runs WHERE id=?',(run_id,)).fetchone()
        if run is None or run['project_id']!=inputs.project_id or run['status']!='running':raise ValueError('Inactive run')
        for source in inputs.sources:
            if c.execute('SELECT 1 FROM agent_artifacts a JOIN agent_tasks t ON t.id=a.task_id WHERE a.id=? AND t.project_id=?',(source,inputs.project_id)).fetchone() is None:raise ValueError('Invalid handoff source')
        cached=c.execute("""SELECT a.id,a.output_json FROM agent_tasks t JOIN agent_artifacts a ON a.task_id=t.id
            WHERE t.project_id=? AND t.round_number=? AND t.role=? AND t.input_digest=? AND t.status='succeeded'
            ORDER BY t.version DESC LIMIT 1""",(inputs.project_id,inputs.round_number,role,digest(raw))).fetchone()
        if cached:
            return ROLE_OUTPUTS[role].model_validate_json(cached['output_json']),cached['id']
        version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM agent_tasks WHERE project_id=? AND round_number=? AND role=?',(inputs.project_id,inputs.round_number,role)).fetchone()[0]
        from app.repositories import start_tool
        start_tool(c,run_id,tool_id,TOOLS[role],started,{'agent':ROLE_NAMES[role],'task_id':task_id,'execution':'model','round_number':inputs.round_number,'input_version':version,'source_artifacts':inputs.sources})
        c.execute('''INSERT INTO agent_tasks(id,project_id,run_id,tool_id,round_number,role,version,status,started_at,input_json,input_digest)
          VALUES(?,?,?,?,?,?,?,'running',?,?,?)''',(task_id,inputs.project_id,run_id,tool_id,inputs.round_number,role,version,started,canonical(raw),digest(raw)))
    calls=[]
    try:
        # Maximum two calls, one role-wide 60s budget. Retry only invalid output, never transport failures.
        role_deadline=min(deadline,time.monotonic()+60)
        prompt=prompt_text
        prompt+='\n只输出一个JSON对象，顶层字段必须且只能为：'+','.join(schema['properties'])+'。不得复制输入外壳contract_version/project_id/round_number/sources/context，不得添加解释字段。\n输出必须符合下列JSON Schema：\n'+canonical(schema)
        candidate = None
        for attempt in range(2):
            budget=role_deadline-time.monotonic()
            if budget<=0:raise LLMError('LLM_TIMEOUT')
            q=Queue(maxsize=1)
            data=raw if attempt==0 else {**raw,'validation_retry':'Previous output violated the contract. Return a corrected full JSON object; do not invent evidence.','validation_errors':calls[-1].get('validation_errors',[]),'candidate_output':candidate,'required_question_mapping':calls[-1].get('required_question_mapping')}
            # Capture queue per invocation; late timed-out results cannot enter a later attempt.
            local_q=q
            def worker(data=data,budget=budget,target=local_q):
                try:target.put((call_model(prompt,data,settings,timeout=budget),None))
                except Exception as error:target.put((None,error))
            Thread(target=worker,daemon=True).start()
            try:reply,error=q.get(timeout=budget)
            except Empty:raise LLMError('LLM_TIMEOUT') from None
            if error:
                if isinstance(error,LLMError) and error.call_info:calls.append(asdict(error.call_info))
                raise error
            info=asdict(reply.call_info);calls.append(info)
            try:
                candidate=json.loads(extract_json_object(reply.text))
                result=ROLE_OUTPUTS[role].model_validate(candidate)
                validate_output(role,result,inputs)
                if validator:validator(result)
                break
            except (ValueError,TypeError,RecursionError) as validation_error:
                from pydantic import ValidationError
                if isinstance(validation_error,ValidationError):
                    allowed_fields=set(schema.get('properties',{}))
                    for definition in schema.get('$defs',{}).values():allowed_fields.update(definition.get('properties',{}))
                    info['validation_errors']=[{'type':item['type'],'path':[x if not isinstance(x,str) or x in allowed_fields else '<unknown_field>' for x in item['loc']],'limits':{k:v for k,v in item.get('ctx',{}).items() if k in {'max_length','min_length','actual_length'} and isinstance(v,int)}} for item in validation_error.errors(include_input=False,include_context=True,include_url=False)[:10]]
                    safe_rules={'Every question needs one quality check','Question checks must match hypothesis mapping','Quality check cannot invent hypothesis links','Three unique hypothesis IDs required','Three hypothesis decisions required'}
                    for item,diagnostic in zip(validation_error.errors(include_input=False,include_context=True,include_url=False)[:10],info['validation_errors']):
                        rule=str(item.get('ctx',{}).get('error',''))
                        if rule in safe_rules:diagnostic['rule']=rule
                    if role=='zhuoyu' and isinstance(candidate,dict) and isinstance(candidate.get('plan'),dict):
                        try:
                            plan=Plan.model_validate(candidate['plan'])
                            info['required_question_mapping']={q.id:[h.id for h in plan.hypotheses if q.id in h.question_ids] for q in plan.questions}
                        except ValueError:pass

                else:
                    info['validation_errors']=[{'type':type(validation_error).__name__,'rule':str(validation_error) if type(validation_error) is ValueError and str(validation_error) in {'Unknown evidence question','Unknown evidence option','Unknown evidence response','Evidence not in source report','No experimental change','Designer changed opportunity hypotheses','Designer changed opportunity scope','plan.target_customer must exactly equal context.opportunity.target_customer; copy the unchanged source text','plan.value_proposition must exactly equal context.opportunity.value_proposition; copy the unchanged source text','Unsupported numeric percentage','Unsupported response count','Evidence does not address this hypothesis','Zero feedback requires insufficient evidence'} else 'contract_validation'}]
                info.update(success=False,error_code='LLM_OUTPUT_INVALID',stage='role_validation')
                if attempt==1:raise LLMError('LLM_OUTPUT_INVALID',reply.call_info) from None
        else:raise LLMError('LLM_OUTPUT_INVALID')
        finished=now();output=result.model_dump(mode='json')
        meta={**calls[-1],'attempts':calls,'duration_seconds':round(sum(c['duration_seconds'] for c in calls),3)}
        with connect_db(settings.database_path) as c:
            c.execute('BEGIN IMMEDIATE')
            run=c.execute('SELECT status FROM agent_runs WHERE id=?',(run_id,)).fetchone()
            if run is None or run['status']!='running':raise LLMError('LLM_OUTPUT_INVALID')
            c.execute('INSERT INTO agent_artifacts VALUES(?,?,?,?,?)',(artifact_id,task_id,canonical(output),digest(output),finished))
            c.execute("UPDATE agent_tasks SET status='succeeded',finished_at=?,call_json=? WHERE id=? AND status='running'",(finished,canonical(meta),task_id))
            from app.repositories import finish_tool
            finish_tool(c,tool_id,finished,{'artifact_id':artifact_id,'task_id':task_id,'round_number':inputs.round_number,'model_call':meta})
        return result,artifact_id
    except BaseException as error:
        code=error.code if isinstance(error,LLMError) else 'INTERNAL_ERROR'
        with connect_db(settings.database_path) as c:
            c.execute("UPDATE agent_tasks SET status='failed',finished_at=?,error_code=?,call_json=? WHERE id=?",(now(),code,canonical({'attempts':calls}),task_id))
            c.execute("UPDATE tool_calls SET status='failed',finished_at=?,error_code=? WHERE id=? AND status='running'",(now(),code,tool_id))
        raise
