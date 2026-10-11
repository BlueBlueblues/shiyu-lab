import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from jinja2 import TemplateNotFound
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from app import repositories
from app.auth import require_admin
from app.config import Settings, load_settings
from app.db import get_db, init_db
from app.schemas import (
    ContractModel, DataResponse, ErrorDetails, ErrorResponse, ProjectCreate, ProjectCreated,
    ProjectDetail, ProjectSummary, Answers, Experiment, ExperimentEdit, Question, RawResponse, Statistics,
)
from app.services import experiments
from app.services.statistics import calculate_statistics
from app.services.workflow import WorkflowError, run_plan

APP_DIR = Path(__file__).resolve().parent
admin_router = APIRouter(
    dependencies=[Depends(require_admin)],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 422, 500)},
)
# 写事务须在发送 201 前提交，提交失败才能返回统一错误并回滚。
Database = Annotated[sqlite3.Connection, Depends(get_db, scope="function")]
public_router = APIRouter()


@admin_router.get("/api/experiments/{experiment_id}", response_model=DataResponse[Experiment])
def get_experiment(experiment_id: str, request: Request, connection: Database):
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    return {"data": experiments.experiment_data(row, request.app.state.settings.public_base_url)}


@admin_router.patch("/api/experiments/{experiment_id}", response_model=DataResponse[Experiment])
def patch_experiment(experiment_id: str, body: ExperimentEdit, request: Request, connection: Database):
    return {"data": experiments.edit_experiment(connection, experiment_id, body, request.app.state.settings.public_base_url)}


@admin_router.get("/api/experiments/{experiment_id}/responses", response_model=DataResponse[list[RawResponse]])
def get_responses(experiment_id: str, connection: Database):
    experiment, responses = repositories.response_snapshot(connection, experiment_id)
    experiments.require_record(experiment)
    return {"data": responses}


@admin_router.get("/api/experiments/{experiment_id}/statistics", response_model=DataResponse[Statistics])
def get_statistics(experiment_id: str, connection: Database):
    experiment, responses = repositories.response_snapshot(connection, experiment_id)
    experiments.require_record(experiment)
    if experiment["published_at"] is None:
        raise HTTPException(status_code=400, detail="实验尚未发布，不能统计反馈。")
    questions = [Question.model_validate(question) for question in json.loads(experiment["questions_json"])]
    return {"data": calculate_statistics(experiment_id, questions, responses)}


@admin_router.post("/api/experiments/{experiment_id}/publish", response_model=DataResponse[dict])
def publish_experiment(experiment_id: str, body: ContractModel, request: Request, connection: Database):
    return {"data": experiments.publish(connection, experiment_id, request.app.state.settings.public_base_url)}


def render_experiment(request, template, context):
    try:
        return request.app.state.templates.TemplateResponse(request=request, name=template, context=context)
    except TemplateNotFound:
        return error_response(500, "INTERNAL_ERROR", "页面模板尚未由成员 B 交付，页面集成未完成。")


@admin_router.get("/experiments/{experiment_id}/edit")
def experiment_edit_page(experiment_id: str, request: Request, connection: Database):
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    return render_experiment(request, "experiment_edit.html", {
        "experiment": experiments.experiment_data(row, request.app.state.settings.public_base_url),
        "running_run_id": row["running_run_id"], "is_locked": row["published_at"] is not None,
    })


def preview_context(experiment_id, request, connection):
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    return {"landing": json.loads(row["landing_json"]), "questions": json.loads(row["questions_json"]),
            "is_preview": True, "submit_url": None,
            "service_url": f"/experiments/{experiment_id}/preview/service",
            "survey_url": f"/experiments/{experiment_id}/preview/survey"}


@admin_router.get("/experiments/{experiment_id}/preview/service")
def preview_service(experiment_id: str, request: Request, connection: Database):
    return render_experiment(request, "service.html", preview_context(experiment_id, request, connection))


@admin_router.get("/experiments/{experiment_id}/preview/survey")
def preview_survey(experiment_id: str, request: Request, connection: Database):
    return render_experiment(request, "survey.html", preview_context(experiment_id, request, connection))


def public_context(token, request, connection):
    row = experiments.require_record(repositories.published_experiment(connection, token))
    data = experiments.experiment_data(row, request.app.state.settings.public_base_url)
    return {"landing": data["landing"], "questions": data["questions"], "is_preview": False,
            "service_url": data["service_url"], "survey_url": data["survey_url"],
            "submit_url": f"{request.app.state.settings.public_base_url.rstrip('/')}/api/public/e/{quote(token, safe='')}/responses"}


@public_router.get("/e/{public_token}")
def public_service(public_token: str, request: Request, connection: Database):
    return render_experiment(request, "service.html", public_context(public_token, request, connection))


@public_router.get("/e/{public_token}/survey")
def public_survey(public_token: str, request: Request, connection: Database):
    return render_experiment(request, "survey.html", public_context(public_token, request, connection))


@public_router.post("/api/public/e/{public_token}/responses", response_model=DataResponse[dict], status_code=201)
def submit_response(public_token: str, body: Answers, connection: Database, response: Response):
    data = experiments.submit(connection, public_token, body)
    response.status_code = 200 if data["duplicate"] else 201
    return {"data": data}


@admin_router.get("/")
def home(request: Request, connection: Database):
    return render_experiment(request, "projects.html", {"projects": repositories.list_projects(connection)})


@admin_router.get("/projects/new")
def new_project_page(request: Request):
    return render_experiment(request, "project_new.html", {})


@admin_router.get("/projects/{project_id}")
def project_workspace(project_id: str, request: Request, connection: Database):
    detail = get_project(project_id, request, connection)["data"]
    return render_experiment(request, "workspace.html", detail)


@admin_router.post("/api/projects", status_code=201, response_model=DataResponse[ProjectCreated])
def create_project(project: ProjectCreate, connection: Database):
    project_id = repositories.create_project(connection, project)
    return {"data": {"project_id": project_id, "status": "draft"}}


@admin_router.get("/api/projects", response_model=DataResponse[list[ProjectSummary]])
def list_projects(connection: Database):
    return {"data": repositories.list_projects(connection)}


@admin_router.get(
    "/api/projects/{project_id}", response_model=DataResponse[ProjectDetail],
    responses={404: {"model": ErrorResponse}},
)
def get_project(project_id: str, request: Request, connection: Database):
    project = repositories.get_project(connection, project_id, request.app.state.settings.public_base_url)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    return {"data": project}


@admin_router.post("/api/projects/{project_id}/plan", response_model=DataResponse[dict])
def generate_project_plan(project_id: str, body: ContractModel, request: Request):
    return {"data": run_plan(project_id, request.app.state.settings)}


@admin_router.get("/api/projects/{project_id}/runs", response_model=DataResponse[list[dict]])
def list_project_runs(project_id: str, connection: Database):
    try:
        return {"data": repositories.list_runs(connection, project_id)}
    except repositories.RepositoryError as error:
        raise WorkflowError(error.code) from None


@admin_router.get("/api/runs/{run_id}", response_model=DataResponse[dict])
def get_run(run_id: str, connection: Database):
    run = repositories.get_run(connection, run_id)
    if run is None:
        raise WorkflowError("NOT_FOUND")
    return {"data": run}


def error_response(status_code, code, message, fields=None, headers=None):
    body = ErrorResponse(error=ErrorDetails(code=code, message=message, fields=fields or {}))
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.settings = settings if settings is not None else load_settings()
        init_db(application.state.settings.database_path)
        yield

    application = FastAPI(title="FirstOrder Lab", lifespan=lifespan)
    application.state.templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    application.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    application.include_router(admin_router)
    application.include_router(public_router)

    @application.exception_handler(experiments.ExperimentError)
    async def experiment_error(request: Request, exception: experiments.ExperimentError):
        return error_response(exception.status_code, exception.code, exception.message, exception.fields)

    @application.exception_handler(WorkflowError)
    async def workflow_error(request: Request, exception: WorkflowError):
        body = {"error": {"code": exception.code, "message": exception.message, "fields": {}}}
        if exception.run_id is not None:
            body["error"]["run_id"] = exception.run_id
        return JSONResponse(status_code=exception.status_code, content=body)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exception: HTTPException):
        code = {
            400: "INVALID_STATE", 401: "AUTH_REQUIRED", 403: "FORBIDDEN",
            404: "NOT_FOUND", 422: "VALIDATION_ERROR",
            500: "INTERNAL_ERROR", 502: "LLM_UPSTREAM_ERROR", 504: "LLM_TIMEOUT",
        }.get(exception.status_code, "INTERNAL_ERROR")
        message = str(exception.detail) if exception.status_code < 500 else "服务暂时无法完成请求。"
        return error_response(exception.status_code, code, message, headers=exception.headers)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exception: RequestValidationError):
        messages = {
            "missing": "此字段为必填项。", "string_too_short": "文本去除首尾空白后不能为空。",
            "string_too_long": "文本超过允许长度。", "greater_than_equal": "数值低于允许范围。",
            "less_than_equal": "数值超过允许范围。", "extra_forbidden": "不支持此字段。",
            "json_invalid": "请求体必须是有效 JSON。",
        }
        fields = {
            ".".join(str(part) for part in error["loc"] if part != "body") or "$":
            messages.get(error["type"], "字段格式或取值不符合要求。")
            for error in exception.errors()
        }
        return error_response(422, "VALIDATION_ERROR", "输入校验失败，请检查字段。", fields=fields)

    @application.exception_handler(sqlite3.Error)
    @application.exception_handler(Exception)
    async def internal_error(request: Request, exception: Exception):
        return error_response(500, "INTERNAL_ERROR", "服务暂时无法完成请求，请稍后重试。")

    @application.get("/health")
    def health():
        return {"data": {"status": "ok"}}

    return application


app = create_app()
