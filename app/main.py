from app.services.workflow import run_opportunity
from fastapi import Query
from typing import Literal
from app.services.history import agent_history, agent_dashboard, agent_stage_states, set_agent_stages
from app.agents.contracts import Approval, Rejection
from app.services.iterations import reject_iteration
import json
import secrets
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
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from starlette.exceptions import HTTPException

from app import repositories
from app.auth import require_admin, optional_user
from app.auth_routes import router as auth_router, AuthLimiter
from app.passwords import hash_password, LEGACY_ADMIN_ID
from app.invitation_routes import router as invitation_router
from app.config import Settings, load_settings
from app.db import get_db, init_db
from app.schemas import (
    ContractModel, DataResponse, ErrorDetails, ErrorResponse, ProjectCreate, ProjectCreated,
    ProjectDetail, ProjectSummary, Answers, Experiment, ExperimentEdit, Question, RawResponse, Statistics, Report,
    IterationConfirmation,
)
from app.services.recruitment import recruitment_view
from app.services import experiments
from app.services.statistics import calculate_statistics
from app.services.workflow import WorkflowError, run_plan, run_review
from app.services.reports import markdown_report
from app.services.iterations import run_iteration, get_proposal, confirm_iteration
from app.services.history import round_history, collaboration_state

APP_DIR = Path(__file__).resolve().parent
admin_router = APIRouter(
    dependencies=[Depends(require_admin)],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 422, 500)},
)
# 写事务须在发送 201 前提交，提交失败才能返回统一错误并回滚。
Database = Annotated[sqlite3.Connection, Depends(get_db, scope="function")]
public_router = APIRouter()


@admin_router.get("/openapi.json", include_in_schema=False)
def api_schema(request: Request):
    return request.app.openapi()


@admin_router.get("/docs", include_in_schema=False)
def api_docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title="试玉 · SHIYU LAB API")


@admin_router.get("/redoc", include_in_schema=False)
def api_redoc():
    return get_redoc_html(openapi_url="/openapi.json", title="试玉 · SHIYU LAB API")


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
        response = request.app.state.templates.TemplateResponse(request=request, name=template, context=context)
        response.headers["Cache-Control"] = "no-store"
        return response
    except TemplateNotFound:
        return error_response(500, "INTERNAL_ERROR", "页面模板尚未由成员 B 交付，页面集成未完成。")


@admin_router.get("/experiments/{experiment_id}/edit")
def experiment_edit_page(experiment_id: str, request: Request, connection: Database):
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    request.state.recruitment = recruitment_view(connection, row["project_id"], experiment_id, request.app.state.settings.public_base_url)
    return render_experiment(request, "experiment_edit.html", {
        "experiment": experiments.experiment_data(row, request.app.state.settings.public_base_url),
        "running_run_id": row["running_run_id"], "is_locked": row["published_at"] is not None,
    })


@admin_router.get("/api/experiments/{experiment_id}/recruitment-kit", response_model=DataResponse[dict])
def get_recruitment_kit(experiment_id: str, request: Request, connection: Database):
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    return {"data": recruitment_view(connection, row["project_id"], experiment_id, request.app.state.settings.public_base_url)}


@admin_router.get("/experiments/{experiment_id}/recruitment")
def recruitment_page(experiment_id: str, request: Request, connection: Database):
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    request.state.recruitment = recruitment_view(connection, row["project_id"], experiment_id, request.app.state.settings.public_base_url)
    return render_experiment(request, "recruitment.html", {"experiment": experiments.experiment_data(row, request.app.state.settings.public_base_url)})


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


@public_router.get("/")
def home(request: Request):
    request.state.current_user = optional_user(request)
    return render_experiment(request, "home.html", {})


@public_router.get("/login")
def login_page(request: Request):
    return render_experiment(request, "login.html", {})


@public_router.get("/register")
def register_page(request: Request):
    response = render_experiment(request, "register.html", {})
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.app.state.settings.invite_registration_enabled:
        response.status_code = 503
    return response


@admin_router.get("/profile")
def profile_page(request: Request):
    return render_experiment(request, "profile.html", {})


@admin_router.get("/projects")
def projects_page(request: Request, connection: Database):
    return render_experiment(request, "projects.html", {
        "projects": repositories.list_projects(connection, request.state.current_user["id"])})


@admin_router.get("/projects/new")
def new_project_page(request: Request):
    return render_experiment(request, "project_new.html", {})


@admin_router.get("/projects/{project_id}")
def project_workspace(project_id: str, request: Request, connection: Database):
    connection.execute("BEGIN")
    detail = get_project(project_id, request, connection)["data"]
    request.state.recruitment = recruitment_view(connection, project_id, detail["experiment"]["id"] if detail["experiment"] else None, request.app.state.settings.public_base_url)
    request.state.collaboration = collaboration_state(connection, project_id)
    request.state.agents = agent_dashboard(connection, project_id)
    # 四阶段导航与角色卡共用这一份判定，避免同一屏出现两种状态口径。
    set_agent_stages(request, connection, project_id)
    request.state.plan_locked = bool(detail["experiment"] and
        repositories.experiment_record(connection, detail["experiment"]["id"])["round_number"] > 1)
    return render_experiment(request, "workspace.html", detail)


@admin_router.post("/api/projects", status_code=201, response_model=DataResponse[ProjectCreated])
def create_project(project: ProjectCreate, request: Request, connection: Database):
    project_id = repositories.create_project(connection, project, request.state.current_user["id"])
    return {"data": {"project_id": project_id, "status": "draft"}}


@admin_router.get("/api/projects", response_model=DataResponse[list[ProjectSummary]])
def list_projects(request: Request, connection: Database):
    return {"data": repositories.list_projects(connection, request.state.current_user["id"])}


@admin_router.get(
    "/api/projects/{project_id}", response_model=DataResponse[ProjectDetail],
    responses={404: {"model": ErrorResponse}},
)
def get_project(project_id: str, request: Request, connection: Database):
    project = repositories.get_project(connection, project_id, request.app.state.settings.public_base_url)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    return {"data": project}


def require_agent_execution(request: Request):
    if not request.app.state.settings.agent_execution_enabled:
        raise HTTPException(503, "AI 任务暂时维护中；账户、注册和已保存证据仍可使用。")


@admin_router.post("/api/projects/{project_id}/plan", response_model=DataResponse[dict])
def generate_project_plan(project_id: str, body: ContractModel, request: Request):
    require_agent_execution(request)
    return {"data": run_plan(project_id, request.app.state.settings)}


def load_report(connection, project_id, experiment_id=None, report_id=None):
    try:
        return repositories.latest_report(connection, project_id, experiment_id, report_id)
    except repositories.RepositoryError as error:
        raise WorkflowError(error.code) from None


def report_payload(report):
    return {key: report[key] for key in Report.model_fields}


@admin_router.post("/api/projects/{project_id}/review", response_model=DataResponse[dict])
def review_project(project_id: str, body: ContractModel, request: Request):
    require_agent_execution(request)
    return {"data": run_review(project_id, request.app.state.settings)}


@admin_router.get("/api/projects/{project_id}/report", response_model=DataResponse[Report])
def get_report(project_id: str, connection: Database, experiment_id: str | None = None, report_id: str | None = None):
    report = load_report(connection, project_id, experiment_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="尚无成功复盘报告。")
    return {"data": report_payload(report)}


@admin_router.get("/api/projects/{project_id}/report.md")
def download_report(project_id: str, connection: Database, experiment_id: str | None = None, report_id: str | None = None):
    report = load_report(connection, project_id, experiment_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="尚无成功复盘报告。")
    return Response(markdown_report(report), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="firstorder-report-{report["id"]}.md"'})


@admin_router.get("/projects/{project_id}/report")
def report_page(project_id: str, request: Request, connection: Database,
                experiment_id: str | None = None, report_id: str | None = None):
    connection.execute("BEGIN")
    detail = repositories.get_project(connection, project_id, request.app.state.settings.public_base_url, experiment_id)
    if detail is None:
        raise WorkflowError("NOT_FOUND")
    report = load_report(connection, project_id, experiment_id, report_id)
    request.state.report_plan = report["plan_snapshot"] if report else None
    return render_experiment(request, "report.html", {"project": detail["project"],
        "report": report_payload(report) if report else None, "running_run_id": detail["running_run_id"],
        "report_download_url": f"/api/projects/{project_id}/report.md?experiment_id={detail['experiment']['id']}&report_id={report['id']}" if report else None})


@admin_router.post("/api/experiments/{experiment_id}/review", response_model=DataResponse[dict])
def review_round(experiment_id: str, body: ContractModel, request: Request, connection: Database):
    require_agent_execution(request)
    row = experiments.require_record(repositories.experiment_record(connection, experiment_id))
    return {"data": run_review(row["project_id"], request.app.state.settings, experiment_id)}


@admin_router.get("/api/projects/{project_id}/rounds", response_model=DataResponse[dict])
def get_rounds(project_id: str, connection: Database):
    return {"data": round_history(connection, project_id)}


@admin_router.get("/projects/{project_id}/rounds")
def rounds_page(project_id: str, request: Request, connection: Database):
    history = round_history(connection, project_id)
    detail = get_project(project_id, request, connection)["data"]
    return render_experiment(request, "rounds.html", {**history, **detail})


@admin_router.post("/api/projects/{project_id}/iterations", response_model=DataResponse[dict])
def propose_iteration(project_id: str, body: ContractModel, request: Request):
    require_agent_execution(request)
    return {"data": run_iteration(project_id, request.app.state.settings)}


@admin_router.get("/api/projects/{project_id}/iterations/{proposal_id}", response_model=DataResponse[dict])
def read_iteration(project_id: str, proposal_id: str, connection: Database):
    return {"data": get_proposal(connection, project_id, proposal_id)}


@admin_router.get("/projects/{project_id}/iterations/{proposal_id}")
def iteration_page(project_id: str, proposal_id: str, request: Request, connection: Database):
    detail = get_project(project_id, request, connection)["data"]
    proposal = get_proposal(connection, project_id, proposal_id)
    return render_experiment(request, "iteration.html", {**detail, "proposal": proposal})


@admin_router.post("/api/projects/{project_id}/iterations/{proposal_id}/confirm", response_model=DataResponse[dict])
def accept_iteration(project_id: str, proposal_id: str, body: Approval, request: Request):
    require_agent_execution(request)
    return {"data": confirm_iteration(project_id, proposal_id, request.app.state.settings, body)}


@admin_router.post("/api/projects/{project_id}/iterations/{proposal_id}/reject", response_model=DataResponse[dict])
def decline_iteration(project_id: str, proposal_id: str, body: Rejection, request: Request):
    return {"data": reject_iteration(project_id, proposal_id, body.reason, request.app.state.settings)}


@admin_router.get("/api/projects/{project_id}/agent-tasks", response_model=DataResponse[dict])
def agent_task_records(project_id: str, connection: Database):
    dashboard = agent_dashboard(connection, project_id)
    # 导航与角色卡共用同一份判定，前台按它刷新时不会出现两套口径。
    dashboard["stage_states"] = agent_stage_states(connection, project_id)
    return {"data": dashboard}


@admin_router.get("/projects/{project_id}/agents/{role}")
def agent_result_page(project_id: str, role: Literal["shiyu","zhuoyu","jianyu","gongyu"], request: Request,
                      connection: Database, round_number: Annotated[int, Query(ge=1)] | None = None):
    detail = get_project(project_id, request, connection)["data"]
    dashboard = agent_dashboard(connection, project_id, round_number)
    recruitment_experiment = connection.execute("SELECT id FROM experiments WHERE project_id=? AND round_number=?", (project_id,dashboard["round_number"])).fetchone()
    return render_experiment(request, "agent_result.html", {**detail,"dashboard":dashboard,"role":role,"recruitment_experiment_id":recruitment_experiment["id"] if recruitment_experiment else None})


@admin_router.post("/api/projects/{project_id}/agents/{role}/run", response_model=DataResponse[dict])
def run_single_role(project_id: str, role: Literal["shiyu","zhuoyu","jianyu","gongyu"], body: ContractModel, request: Request):
    require_agent_execution(request)
    settings = request.app.state.settings
    if role == "shiyu":
        return {"data": run_opportunity(project_id, settings)}
    if role == "zhuoyu":
        return {"data": run_plan(project_id, settings, design_only=True)}
    if role == "jianyu":
        return {"data": run_review(project_id, settings)}
    return {"data": run_iteration(project_id, settings)}


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


def wants_html(request: Request) -> bool:
    """True when the caller is a browser navigating, not a fetch/XHR client.

    app.js always sends Accept: application/json, so keeping the JSON envelope for those
    callers preserves every existing API contract.
    """
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def error_page_response(request: Request, status_code: int, message: str, code=None, headers=None, fields=None):
    """Render a readable error page for browser requests; keep JSON for API clients.

    公开链接（/e/<token> 与 /e/<token>/survey）是要发到手机上的：链接一失效，
    原先直接回一坨 JSON，手机上没有 viewport 声明会按 980px 缩放，字小到看不清。
    浏览器请求现在拿到的是同一套站点外壳下的错误页。

    code / fields 原样透传给 JSON 分支，调用方自带的错误码（如 DRAFT_NOT_PUBLISHED）
    不会被这里覆盖，API 契约保持不变。
    """
    if wants_html(request):
        try:
            templates = getattr(request.app.state, "templates", None)
            if templates is not None:
                response = templates.TemplateResponse(
                    request, "error.html",
                    {"status_code": status_code, "message": message},
                    status_code=status_code,
                )
                for key, value in (headers or {}).items():
                    response.headers[key] = value
                return response
        except Exception:      # 模板缺失或渲染失败时退回 JSON，绝不因为错误页把错误页也弄坏
            pass
    return error_response(status_code, code or {
        400: "INVALID_STATE", 401: "AUTH_REQUIRED", 403: "FORBIDDEN",
        404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR",
        429: "RATE_LIMITED", 500: "INTERNAL_ERROR", 503: "SERVICE_UNAVAILABLE",
    }.get(status_code, "INTERNAL_ERROR"), message, fields=fields, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.settings = settings if settings is not None else load_settings()
        configured = application.state.settings
        init_db(configured.database_path, configured.admin_user, configured.admin_password.get_secret_value())
        application.state.auth_limiter = AuthLimiter()
        from app.mail import EmailSender
        application.state.email_sender = EmailSender(configured)
        application.state.dummy_password_hash = hash_password(secrets.token_urlsafe(32))
        yield

    application = FastAPI(title="试玉 · SHIYU LAB", lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None)
    application.state.templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
    application.state.templates.env.globals["invitation_admin_id"] = LEGACY_ADMIN_ID
    application.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    application.include_router(admin_router)
    application.include_router(public_router)
    application.include_router(auth_router)
    application.include_router(invitation_router)
    from app.admin_cases import router as case_router
    application.include_router(case_router)

    @application.middleware("http")
    async def private_case_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/admin/cases" or request.url.path.startswith("/admin/cases/"):
            response.headers["Cache-Control"] = "no-store, private"
            response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.exception_handler(experiments.ExperimentError)
    async def experiment_error(request: Request, exception: experiments.ExperimentError):
        # 公开链接（/e/<token> 与 /e/<token>/survey）由这里报错：链接是发到手机上的，
        # 失效时读者应看到一页说明，而不是一坨 JSON。错误码原样透传。
        return error_page_response(request, exception.status_code, exception.message,
                                   code=exception.code, fields=exception.fields)

    @application.exception_handler(WorkflowError)
    async def workflow_error(request: Request, exception: WorkflowError):
        body = {"error": {"code": exception.code, "message": exception.message, "fields": {}}}
        if exception.run_id is not None:
            body["error"]["run_id"] = exception.run_id
        return JSONResponse(status_code=exception.status_code, content=body)

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exception: HTTPException):
        if exception.status_code == 303:
            return Response(status_code=303, headers=exception.headers)
        message = str(exception.detail) if exception.status_code < 500 or exception.status_code == 503 else "服务暂时无法完成请求。"
        return error_page_response(request, exception.status_code, message, headers=exception.headers)

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
