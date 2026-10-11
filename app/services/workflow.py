import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from queue import Empty, Queue
from secrets import token_urlsafe
from threading import Thread
from uuid import uuid4

from pydantic import ValidationError

from app import repositories
from app.config import Settings
from app.db import connect_db
from app.schemas import Plan
from app.services.llm_client import ERRORS as LLM_ERRORS, LLMError
from app.services.planning import generate_plan
from app.services.reviews import generate_review
from app.services.statistics import calculate_statistics

WORKFLOW_SECONDS = 60.0
MODEL_SECONDS = 40.0
ERRORS = {
    **LLM_ERRORS,
    "NOT_FOUND": (404, "项目或运行记录不存在。"),
    "RUN_IN_PROGRESS": (409, "该项目已有任务运行中，请稍后再试。"),
    "EXPERIMENT_LOCKED": (409, "实验已发布，不能重新生成计划。"),
    "INVALID_STATE": (400, "任务状态已改变，请刷新后重试。"),
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


def make_validation_plan(project_id: str, settings: Settings, deadline: float):
    with connect_db(settings.database_path) as connection:
        project = repositories.load_project_input(connection, project_id)
    results = Queue(maxsize=1)

    def request_plan():
        try:
            results.put((generate_plan(project, settings), None))
        except Exception as error:
            results.put((None, error))

    # 同步适配器不可强制取消；等待超时后丢弃晚到结果，线程不操作数据库。
    wait_seconds = min(MODEL_SECONDS, remaining(deadline))
    Thread(target=request_plan, daemon=True).start()
    try:
        result, error = results.get(timeout=wait_seconds)
    except Empty:
        raise WorkflowError("LLM_TIMEOUT") from None
    if error is not None:
        raise error
    remaining(deadline)
    try:
        plan = Plan.model_validate(result.plan.model_dump())
    except (ValidationError, AttributeError, TypeError):
        raise LLMError("LLM_OUTPUT_INVALID", getattr(result, "call_info", None)) from None
    return plan, asdict(result.call_info)


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


def run_plan(project_id: str, settings: Settings) -> dict:
    cleanup_deadline = time.monotonic() + WORKFLOW_SECONDS
    # 在 60 秒总预算内预留失败日志事务时间，避免耗尽预算后才尝试清理。
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
        tool_id = str(uuid4())
        summary = {"project_id": project_id, "data_kind": project.data_kind, "model": settings.llm_model,
                   "input_lengths": {name: len(value) for name, value in project.model_dump().items()
                                     if isinstance(value, str) and name != "data_kind"}}
        with write_transaction(settings, deadline) as connection:
            repositories.start_tool(connection, run_id, tool_id, "make_validation_plan", utc_now(), summary)
        plan, model_call = make_validation_plan(project_id, settings, deadline)
        counts = {"hypothesis_count": len(plan.hypotheses), "question_count": len(plan.questions)}
        with write_transaction(settings, deadline) as connection:
            repositories.finish_tool(connection, tool_id, utc_now(),
                                     {"project_id": project_id, **counts, "model_call": model_call})
        tool_id = str(uuid4())
        with write_transaction(settings, deadline) as connection:
            repositories.start_tool(connection, run_id, tool_id, "create_experiment", utc_now(),
                                    {"project_id": project_id, **counts})
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


def run_review(project_id: str, settings: Settings) -> dict:
    cleanup_deadline = time.monotonic() + WORKFLOW_SECONDS
    deadline = cleanup_deadline - min(5.0, WORKFLOW_SECONDS / 2)
    run_id, tool_id = str(uuid4()), str(uuid4())
    try:
        with write_transaction(settings, deadline) as connection:
            project, experiment_id = repositories.start_review_run(connection, project_id, run_id, utc_now())
    except repositories.RepositoryError as error:
        raise WorkflowError(error.code) from None
    completed = False
    failure = WorkflowError("INTERNAL_ERROR", run_id)
    try:
        with write_transaction(settings, deadline) as connection:
            repositories.start_tool(connection, run_id, tool_id, "analyze_experiment", utc_now(),
                                    {"experiment_id": experiment_id, "data_kind": project.data_kind,
                                     "model": settings.llm_model})
        with connect_db(settings.database_path) as connection:
            experiment, responses = repositories.response_snapshot(connection, experiment_id)
            detail = repositories.get_project(connection, project_id, settings.public_base_url)
            plan = Plan.model_validate({**detail["plan"], "questions": json.loads(experiment["questions_json"])})
            statistics = calculate_statistics(experiment_id, plan.questions, responses)
        results = Queue(maxsize=1)

        def request_review():
            try:
                results.put((generate_review(project, plan, statistics, settings), None))
            except Exception as error:
                results.put((None, error))

        wait_seconds = min(MODEL_SECONDS, remaining(deadline))
        Thread(target=request_review, daemon=True).start()
        try:
            result, error = results.get(timeout=wait_seconds)
        except Empty:
            raise WorkflowError("LLM_TIMEOUT") from None
        if error is not None:
            raise error
        review, call_info = result
        remaining(deadline)
        report_id = str(uuid4())
        with write_transaction(settings, deadline) as connection:
            created_at = utc_now()
            repositories.save_report(connection, report_id, experiment_id, run_id, statistics, review, created_at)
            repositories.finish_tool(connection, tool_id, created_at,
                                     {"report_id": report_id, "response_count": statistics.response_count,
                                      "cutoff_response_id": statistics.cutoff_response_id, "model_call": asdict(call_info)})
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
