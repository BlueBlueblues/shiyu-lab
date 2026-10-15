import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials, APIKeyCookie

from app.db import connect_db
from app.passwords import LEGACY_ADMIN_ID
from app.users import SESSION_COOKIE, session_user, public_user

basic = HTTPBasic(auto_error=False, realm="FirstOrderLab")
session_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False)


def require_same_origin(request: Request):
    settings = request.app.state.settings
    try:
        origin = urlsplit(request.headers.get("origin", ""))
        expected = urlsplit(settings.public_base_url)
        origin_port = origin.port if origin.port is not None else (443 if origin.scheme == "https" else 80)
        expected_port = expected.port if expected.port is not None else (443 if expected.scheme == "https" else 80)
        same_origin = (
            origin.scheme in {"http", "https"}
            and not any(char.isspace() for char in request.headers.get("origin", ""))
            and not origin.username and not origin.password
            and not origin.path and not origin.query and not origin.fragment
            and (origin.scheme, origin.hostname, origin_port)
            == (expected.scheme, expected.hostname, expected_port)
        )
    except ValueError:
        same_origin = False
    if not same_origin or request.headers.get("x-requested-with") != "FirstOrderLab":
        raise HTTPException(status_code=403, detail="管理写入必须来自受控同源页面并携带指定请求头。")


def optional_user(request: Request):
    with connect_db(request.app.state.settings.database_path) as connection:
        return session_user(connection, request.cookies.get(SESSION_COOKIE))


def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
    session_token: Annotated[str | None, Depends(session_cookie)],
) -> str:
    settings = request.app.state.settings
    with connect_db(settings.database_path) as connection:
        user = session_user(connection, session_token)
    user_matches = secrets.compare_digest(
        (credentials.username if credentials else "").encode("utf-8"),
        settings.admin_user.encode("utf-8"),
    )
    password_matches = secrets.compare_digest(
        (credentials.password if credentials else "").encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )
    if user is None and credentials and user_matches and password_matches:
        with connect_db(settings.database_path) as connection:
            user = public_user(connection.execute("SELECT * FROM users WHERE id=?", (LEGACY_ADMIN_ID,)).fetchone())
    if user is None:
        # 浏览器页面转到登录，JSON API 保留 401；兼容旧 Basic 验证脚本。
        if "text/html" in request.headers.get("accept", "") and not request.url.path.startswith("/api/"):
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        raise HTTPException(
            status_code=401, detail="请先登录。",
            headers={} if "application/json" in request.headers.get("accept", "") else
                {"WWW-Authenticate": 'Basic realm="FirstOrderLab"'},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_same_origin(request)
    request.state.current_user = user
    with connect_db(settings.database_path) as connection:
        params = request.path_params
        if "project_id" in params:
            owner = connection.execute("SELECT user_id FROM projects WHERE id=?", (params["project_id"],)).fetchone()
        elif "experiment_id" in params:
            owner = connection.execute("""SELECT p.user_id FROM experiments e
                JOIN projects p ON p.id=e.project_id WHERE e.id=?""", (params["experiment_id"],)).fetchone()
        elif "run_id" in params:
            owner = connection.execute("""SELECT p.user_id FROM agent_runs r
                JOIN projects p ON p.id=r.project_id WHERE r.id=?""", (params["run_id"],)).fetchone()
        else:
            return user["username"]
        if owner is None or owner["user_id"] != user["id"]:
            raise HTTPException(404, "项目不存在。" if "project_id" in params else "资源不存在。")
    return user["username"]
