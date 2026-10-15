import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from secrets import token_urlsafe
from uuid import uuid4

from pydantic import ValidationError

from app import repositories
from app.config import Settings
from app.db import connect_db
from app.schemas import Plan
from app.services.llm_client import ERRORS as LLM_ERRORS, LLMError
from app.services.statistics import calculate_statistics

WORKFLOW_SECONDS = 150.0
MODEL_SECONDS = 60.0
ERRORS = {
    **LLM_ERRORS,
    "NOT_FOUND": (404, "项目或运行记录不存在。"),
    "RUN_IN_PROGRESS": (409, "该项目已有任务运行中，请稍后再试。"),
    "EXPERIMENT_LOCKED": (409, "实验已发布，不能重新生成计划。"),
    "INVALID_STATE": (400, "任务状态已改变，请刷新后重试。"),
    "STALE_REPORT": (409, "来源报告已有新反馈或被更新，请重新复盘并生成迭代方案。"),
    "INTERNAL_ERROR": (500, "生成任务未完成，请稍后手动重试。"),
    "INTERRUPTED": (500, "任务已中断，请手动重试。"),
}


class WorkflowError(Exception):
    def __init__(self, code: str, run_id: str | None = None, model_call: dict | None = None):
        self.code = code
        self.status_code, self.message = ERRORS[code]
        self.run_id = run_id
        self.model_call = model_call
        super().__init__(self.message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise WorkflowError("LLM_TIMEOUT")
    return seconds


@contextmanager
def write_transaction(settings: Settings, deadline: float):
    with connect_db(settings.database_path) as connection:
        connection.execute("PRAGMA busy_timeout = %d" % min(5000, int(remaining(deadline) * 1000)))
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.execute("PRAGMA busy_timeout = %d" % min(5000, int(remaining(deadline) * 1000)))
        # commit 也受剩余预算限制；异常由 connect_db 回滚整份草稿。
        connection.commit()


def create_experiment(project_id: str, plan: Plan, settings: Settings,
                      run_id: str, tool_id: str, deadline: float) -> str:
    plan = Plan.model_validate(plan.model_dump())
    with write_transaction(settings, deadline) as connection:
        run = connection.execute("SELECT status, project_id, action FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if run is None or run["status"] != "running" or run["project_id"] != project_id or run["action"] != "plan":
            raise repositories.RepositoryError("INVALID_STATE")
        experiment_id = repositories.save_experiment(
            connection, project_id, plan, str(uuid4()), token_urlsafe(32), utc_now(),
        )
        finished_at = utc_now()
        repositories.finish_tool(connection, tool_id, finished_at, {"experiment_id": experiment_id})
        repositories.complete_plan_run(connection, run_id, finished_at)
    return experiment_id


def run_plan(project_id: str, settings: Settings, *, stop_after_opportunity=False, design_only=False) -> dict:
    cleanup_deadline = time.monotonic() + WORKFLOW_SECONDS
    # 总预算内预留 5 秒失败清理；模型等待以外仍留出校验与写事务时间。
    deadline = cleanup_deadline - min(5.0, WORKFLOW_SECONDS / 2)
    run_id = str(uuid4())
    try:
        with write_transaction(settings, deadline) as connection:
            project = repositories.start_plan_run(connection, project_id, run_id, utc_now())
    except repositories.RepositoryError as error:
        raise WorkflowError(error.code) from None

    completed = False
    failure = WorkflowError("INTERNAL_ERROR", run_id)
    try:
        from app.agents.runtime import execute_role
        role_input = {"project_id":project_id,"round_number":1,"project":project.model_dump(mode="json"),
                      "sources":[],"context":{"history":[]}}
        if design_only:
            from app.agents.runtime import latest_artifact
            with connect_db(settings.database_path) as c:
                previous = latest_artifact(c,project_id,"shiyu",1)
            if previous is None:
                raise WorkflowError("INVALID_STATE")
        opportunity, opportunity_id = execute_role("shiyu", role_input, settings, run_id, deadline=deadline)
        if stop_after_opportunity:
            with write_transaction(settings,deadline) as c:
                repositories.complete_plan_run(c,run_id,utc_now())
            completed=True
            return {"project_id":project_id,"artifact_id":opportunity_id,"run_id":run_id,"status":"opportunity_ready"}
        design, design_id = execute_role("zhuoyu", {**role_input,"sources":[opportunity_id],
            "context":{"opportunity":opportunity.model_dump(mode="json")}}, settings, run_id, deadline=deadline)
        plan = design.plan
        counts = {"hypothesis_count":len(plan.hypotheses),"question_count":len(plan.questions)}
        tool_id = design_id
        tool_id_plan = tool_id
        tool_id = str(uuid4())
        with write_transaction(settings, deadline) as connection:
            repositories.start_tool(connection, run_id, tool_id, "create_experiment", utc_now(),
                                    {"agent": "琢玉", "project_id": project_id, "source_artifact_id": tool_id_plan, "execution":"program", **counts})
        experiment_id = create_experiment(project_id, plan, settings, run_id, tool_id, deadline)
        completed = True
        return {"project_id": project_id, "experiment_id": experiment_id,
                "run_id": run_id, "status": "planned"}
    except LLMError as error:
        failure = WorkflowError(error.code, run_id, asdict(error.call_info) if error.call_info else None)
        raise failure from None
    except repositories.RepositoryError as error:
        failure = WorkflowError(error.code, run_id)
        raise failure from None
    except WorkflowError as error:
        failure = WorkflowError(error.code, run_id, error.model_call)
        raise failure from None
    except Exception:
        raise failure from None
    except BaseException:
        failure = WorkflowError("INTERRUPTED", run_id)
        raise
    finally:
        if not completed:
            with write_transaction(settings, cleanup_deadline) as connection:
                repositories.fail_run(connection, run_id, utc_now(), failure.code,
                                      failure.message, failure.model_call)


def run_review(project_id: str, settings: Settings, experiment_id=None) -> dict:
    cleanup_deadline = time.monotonic() + WORKFLOW_SECONDS
    deadline = cleanup_deadline - min(5.0, WORKFLOW_SECONDS / 2)
    run_id, tool_id = str(uuid4()), str(uuid4())
    try:
        with write_transaction(settings, deadline) as connection:
            project, experiment_id = repositories.start_review_run(connection, project_id, run_id, utc_now(), experiment_id)
    except repositories.RepositoryError as error:
        raise WorkflowError(error.code) from None
    completed = False
    failure = WorkflowError("INTERNAL_ERROR", run_id)
    try:
        with write_transaction(settings, deadline) as connection:
            repositories.start_tool(connection, run_id, tool_id, "analyze_experiment", utc_now(),
                                    {"agent": "鉴玉", "execution":"program", "experiment_id": experiment_id, "data_kind": project.data_kind,
                                     "model": settings.llm_model})
        with connect_db(settings.database_path) as connection:
            experiment, responses = repositories.response_snapshot(connection, experiment_id)
            detail = repositories.get_project(connection, project_id, settings.public_base_url, experiment_id)
            plan = Plan.model_validate({**detail["plan"], "questions": json.loads(experiment["questions_json"])})
            statistics = calculate_statistics(experiment_id, plan.questions, responses)
        from app.agents.runtime import execute_role, latest_artifact
        with connect_db(settings.database_path) as connection:
            round_number = connection.execute("SELECT round_number FROM experiments WHERE id=?",(experiment_id,)).fetchone()[0]
            source = latest_artifact(connection,project_id,"zhuoyu",round_number)
        review, review_artifact = execute_role("jianyu", {
            "project_id":project_id,"round_number":round_number,"project":project.model_dump(mode="json"),
            "sources":[source["id"]] if source else [],
            "context":{"hypotheses":[h.model_dump() for h in plan.hypotheses],
                       "questions":[q.model_dump() for q in plan.questions],
                       "statistics":statistics.model_dump(mode="json"),"experiment_id":experiment_id,
                       "published_plan":plan.model_dump(mode="json")}}, settings,run_id,deadline=deadline)
        remaining(deadline)
        report_id = str(uuid4())
        with write_transaction(settings, deadline) as connection:
            created_at = utc_now()
            repositories.save_report(connection, report_id, experiment_id, run_id, statistics, review, created_at)
            repositories.finish_tool(connection, tool_id, created_at,
                                     {"report_id": report_id, "experiment_id": experiment_id,
                                      "evidence_source": "responses", "response_count": statistics.response_count,
                                      "cutoff_response_id": statistics.cutoff_response_id, "artifact_id": review_artifact, "execution":"program"})
            repositories.complete_plan_run(connection, run_id, created_at)
        completed = True
        return {"report_id": report_id, "run_id": run_id, "response_count": statistics.response_count,
                "cutoff_response_id": statistics.cutoff_response_id}
    except LLMError as error:
        failure = WorkflowError(error.code, run_id, asdict(error.call_info) if error.call_info else None)
        raise failure from None
    except repositories.RepositoryError as error:
        failure = WorkflowError(error.code, run_id)
        raise failure from None
    except WorkflowError as error:
        failure = WorkflowError(error.code, run_id, error.model_call)
        raise failure from None
    except Exception:
        raise failure from None
    except BaseException:
        failure = WorkflowError("INTERRUPTED", run_id)
        raise
    finally:
        if not completed:
            with write_transaction(settings, cleanup_deadline) as connection:
                repositories.fail_run(connection, run_id, utc_now(), failure.code, failure.message, failure.model_call)


def run_opportunity(project_id,settings):
    """Independent 识玉 reassessment; published experiments are NEVER rewritten."""
    from app.agents.runtime import execute_role, latest_artifact
    deadline=time.monotonic()+WORKFLOW_SECONDS-5
    run_id=str(uuid4())
    try:
        with write_transaction(settings,deadline) as c:
            project=repositories.load_project_input(c,project_id)
            if c.execute("SELECT 1 FROM agent_runs WHERE project_id=? AND status='running'",(project_id,)).fetchone():
                raise repositories.RepositoryError('RUN_IN_PROGRESS')
            current=repositories.project_row(c,project_id)
            number=current['round_number'] or 1
            history=[];sources=[]
            if current['published_at']:
                for row in c.execute('''SELECT e.round_number,e.plan_json,r.statistics_json,r.review_json
                    FROM experiments e LEFT JOIN reports r ON r.id=(SELECT id FROM reports WHERE experiment_id=e.id ORDER BY created_at DESC,id DESC LIMIT 1)
                    WHERE e.project_id=? ORDER BY e.round_number DESC LIMIT 10''',(project_id,)):
                    history.append({'round_number':row['round_number'],'published_plan':json.loads(row['plan_json']),
                        'statistics':json.loads(row['statistics_json']) if row['statistics_json'] else None,
                        'review':json.loads(row['review_json']) if row['review_json'] else None})
                analysis=latest_artifact(c,project_id,'jianyu',number)
                if analysis:sources.append(analysis['id'])
            c.execute("INSERT INTO agent_runs(id,project_id,action,status,started_at) VALUES(?,?,'plan','running',?)",(run_id,project_id,utc_now()))
    except repositories.RepositoryError as error:
        raise WorkflowError(error.code) from None
    try:
        _,artifact_id=execute_role('shiyu',{'project_id':project_id,'round_number':number,
             'project':project.model_dump(mode='json'),'sources':sources,'context':{'history':history}},settings,run_id,deadline=deadline)
        with write_transaction(settings,deadline) as c:repositories.complete_plan_run(c,run_id,utc_now())
        return {'project_id':project_id,'artifact_id':artifact_id,'run_id':run_id,'status':'opportunity_ready','published_content_changed':False}
    except BaseException as error:
        from app.services.iterations import finish_failure
        failure=finish_failure(settings,run_id,error)
        if not isinstance(error,Exception):raise
        raise failure from None
