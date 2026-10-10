import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from app import repositories
from app.auth import require_admin
from app.config import Settings, load_settings
from app.db import get_db, init_db
from app.schemas import (
    DataResponse, ErrorDetails, ErrorResponse, ProjectCreate, ProjectCreated,
    ProjectDetail, ProjectSummary,
)

APP_DIR = Path(__file__).resolve().parent
admin_router = APIRouter(
    dependencies=[Depends(require_admin)],
    responses={status: {"model": ErrorResponse} for status in (401, 403, 422, 500)},
)
# 写事务须在发送 201 前提交，提交失败才能返回统一错误并回滚。
Database = Annotated[sqlite3.Connection, Depends(get_db, scope="function")]


@admin_router.get("/", response_class=PlainTextResponse)
def home():
    return "FirstOrder Lab — A01 临时管理首页（仅用于验证 HTTP Basic，等待成员 B 模板替换）。"


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
