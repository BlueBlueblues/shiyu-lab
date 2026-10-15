import json

from app import repositories


def collaboration_state(connection, project_id):
    row = repositories.project_row(connection, project_id)
    if row is None:
        raise repositories.RepositoryError("NOT_FOUND")
    experiment = connection.execute("SELECT round_number FROM experiments WHERE id=?", (row["experiment_id"],)).fetchone()
    pending = connection.execute("""SELECT id,source_report_id FROM iteration_proposals
        WHERE project_id=? AND source_experiment_id=? AND created_experiment_id IS NULL AND rejected_at IS NULL
        ORDER BY created_at DESC,rowid DESC LIMIT 1""", (project_id, row["experiment_id"])).fetchone()
    source = connection.execute("""SELECT id,source_report_id,confirmed_at FROM iteration_proposals
        WHERE project_id=? AND created_experiment_id=?""", (project_id, row["experiment_id"])).fetchone()
    runs = repositories.list_runs(connection, project_id)
    tools = []
    for tool in connection.execute("""SELECT t.*,r.action FROM tool_calls t
            JOIN agent_runs r ON r.id=t.run_id WHERE r.project_id=? ORDER BY t.started_at,t.rowid""", (project_id,)):
        output = json.loads(tool["output_ref_json"] or "{}")
        inputs = json.loads(tool["input_summary_json"])
        tools.append({"id": tool["id"], "run_id": tool["run_id"], "tool_name": tool["tool_name"],
            "status": tool["status"], "error_code": tool["error_code"],
            "source_tool_id": inputs.get("source_tool_id"),
            "source_report_id": inputs.get("source_report_id") or inputs.get("report_id"),
            "proposal_id": inputs.get("proposal_id"), "execution": inputs.get("execution"), "source_artifact_id": inputs.get("source_artifact_id"),
            "experiment_id": output.get("experiment_id"), "report_id": output.get("report_id"),
            "output_proposal_id": output.get("proposal_id"),
            "response_count": output.get("response_count"), "cutoff_response_id": output.get("cutoff_response_id"),
            "model_call": output.get("model_call")})
    return {"round_number": experiment["round_number"] if experiment else None,
            "response_count": row["current_response_count"] or 0,
            "pending_proposal": dict(pending) if pending else None,
            "pending_stale": bool(pending and (pending["source_report_id"] != row["report_id"] or repositories.report_is_stale(row))),
            "source_proposal": dict(source) if source else None,
            "latest_run": runs[0] if runs else None, "tools": tools}


def round_history(connection, project_id):
    if not connection.in_transaction:
        connection.execute("BEGIN")
    rows = connection.execute("SELECT * FROM experiments WHERE project_id=? ORDER BY round_number", (project_id,)).fetchall()
    rounds = []
    for row in rows:
        reports = [dict(item) for item in connection.execute("""SELECT id,created_at,response_count,cutoff_response_id
            FROM reports WHERE experiment_id=? ORDER BY created_at DESC,id DESC""", (row["id"],))]
        detail = repositories.get_project(connection, project_id, "", row["id"])
        proposal = connection.execute("SELECT id FROM iteration_proposals WHERE created_experiment_id=?", (row["id"],)).fetchone()
        rounds.append({"id": row["id"], "round_number": row["round_number"],
            "parent_experiment_id": row["parent_experiment_id"], "created_at": row["created_at"],
            "status": detail["project"]["status"], "published_at": row["published_at"],
            "plan": detail["plan"], "reports": reports,
            "response_count": connection.execute("SELECT COUNT(*) FROM responses WHERE experiment_id=?", (row["id"],)).fetchone()[0],
            "proposal_id": proposal["id"] if proposal else None})
    comparisons = []
    by_id = {row["id"]: row for row in rounds}
    for current in rounds:
        previous = by_id.get(current["parent_experiment_id"])
        if previous is None:
            continue
        comparison = {"previous_round": previous["round_number"], "current_round": current["round_number"],
                      "metrics": [], "excluded_questions": [], "available": False}
        if not previous["reports"] or not current["reports"]:
            comparisons.append(comparison)
            continue
        comparison["available"] = True
        before = repositories.latest_report(connection, project_id, previous["id"])
        after = repositories.latest_report(connection, project_id, current["id"])
        comparison.update(previous_report_id=before["id"],current_report_id=after["id"],
                          previous_response_count=before["response_count"],current_response_count=after["response_count"],
                          stale=before["stale"] or after["stale"])
        old_questions = {q["id"]: q for q in previous["plan"]["questions"]}
        old_stats = {q["id"]: q for q in before["statistics"]["questions"]}
        new_stats = {q["id"]: q for q in after["statistics"]["questions"]}
        for question in current["plan"]["questions"]:
            qid = question["id"]
            # shortcut: only identical survey contracts are comparable; semantic equivalence needs human mapping.
            if old_questions.get(qid) != question or question["type"] == "text":
                comparison["excluded_questions"].append(qid)
                continue
            for old, new in zip(old_stats[qid]["options"], new_stats[qid]["options"]):
                comparison["metrics"].append({"question_id": qid, "question_title": question["title"],
                    "option_id": new["id"], "option_label": new["label"],
                    "previous_count": old["count"], "current_count": new["count"],
                    "previous_denominator": old_stats[qid]["answered_count"],
                    "current_denominator": new_stats[qid]["answered_count"],
                    "previous_percentage": old["percentage"], "current_percentage": new["percentage"],
                    "change_percentage_points": round(new["percentage"]-old["percentage"],2)
                        if old["percentage"] is not None and new["percentage"] is not None else None})
        comparisons.append(comparison)
    proposals = [dict(row) for row in connection.execute("""SELECT id,source_experiment_id,source_report_id,
        created_at,confirmed_at,created_experiment_id FROM iteration_proposals WHERE project_id=? ORDER BY created_at DESC,rowid DESC""", (project_id,))]
    return {"rounds": rounds, "comparisons": comparisons, "proposals": proposals,
            "evidence_boundary": "仅记录问卷自报兴趣与意向；未采集实际预约、付款和访问量。回答份数不等于独立用户数。"}


def agent_history(connection, project_id):
    """Private owner-scoped task and artifact snapshots; no provider secrets or hidden reasoning."""
    rows=connection.execute('''SELECT t.*,a.id AS artifact_id,a.output_json,a.output_digest
        FROM agent_tasks t LEFT JOIN agent_artifacts a ON a.task_id=t.id
        WHERE t.project_id=? ORDER BY t.round_number,t.started_at,t.version''',(project_id,)).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        for raw,key in [('input_json','input'),('output_json','output'),('call_json','model_call')]:
            value=item.pop(raw);item[key]=json.loads(value) if value else None
        result.append(item)
    return result


def agent_dashboard(connection,project_id,round_number=None):
    from app.agents.contracts import ROLE_NAMES
    row=repositories.project_row(connection,project_id)
    if row is None:raise repositories.RepositoryError('NOT_FOUND')
    current=round_number or row['round_number'] or 1
    tasks=agent_history(connection,project_id)
    pending=connection.execute('''SELECT id,rejected_at,created_experiment_id FROM iteration_proposals
        WHERE project_id=? AND source_experiment_id=? ORDER BY created_at DESC LIMIT 1''',(project_id,row['experiment_id'])).fetchone()
    # Include a next-round task while approval has authorized design but no draft exists yet.
    cards=[]
    for role,name in ROLE_NAMES.items():
        matching=[t for t in tasks if t['role']==role and t['round_number']==current]
        task=matching[-1] if matching else None
        inherited=None
        if role=='shiyu' and not task and current>1:
            inherited=next((t for t in reversed(tasks) if t['role']=='shiyu' and t['status']=='succeeded'),None)
        state=task['status'] if task else 'inherited' if inherited else 'pending'
        if role=='zhuoyu' and task and task['status']=='succeeded':
            built=connection.execute('SELECT 1 FROM experiments WHERE project_id=? AND round_number=?',(project_id,current)).fetchone()
            if not built:state='awaiting_build'
        if role=='gongyu' and pending and task and task['status']=='succeeded':
            state='rejected' if pending['rejected_at'] else 'succeeded' if pending['created_experiment_id'] else 'awaiting_confirmation'
        cards.append({'role':role,'name':name,'state':state,'task':task,'inherited':inherited})
    return {'round_number':current,'cards':cards,'tasks':tasks,'pending_proposal':dict(pending) if pending else None}
