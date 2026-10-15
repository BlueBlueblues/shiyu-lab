import sqlite3
import time
from collections import deque
from threading import Lock
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth import require_same_origin
from app.db import get_db
from app.passwords import verify_password
from app.users import (Login, User, SESSION_COOKIE, SESSION_SECONDS,
                       public_user, create_session, revoke_session)
from app.schemas import ContractModel, DataResponse
from app.invitations import CodeRequest, InviteRegister, request_code, complete_registration

router = APIRouter(dependencies=[Depends(require_same_origin)])
Database = Annotated[sqlite3.Connection, Depends(get_db, scope="function")]


class AuthLimiter:
    # shortcut: 单进程限流，多 worker/多实例部署前改为共享限流存储。
    def __init__(self):
        self.attempts = {}
        self.lock = Lock()

    def check(self, key):
        now = time.monotonic()
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if v and v[-1] > now - 60}
            if key not in self.attempts and len(self.attempts) >= 1024:
                raise HTTPException(429, "认证请求过多，请稍后再试。", headers={"Retry-After": "60"})
            attempts = self.attempts.setdefault(key, deque())
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                raise HTTPException(429, "认证请求过多，请一分钟后再试。", headers={"Retry-After": "60"})
            attempts.append(now)


def rate_limit(request, username):
    limiter = request.app.state.auth_limiter
    limiter.check(("ip", request.client.host if request.client else "unknown"))
    limiter.check(("username", username.casefold()))


def require_registration(request: Request):
    settings = request.app.state.settings
    if not settings.invite_registration_enabled:
        raise HTTPException(503, "邀请注册暂未开放。")
    secret = settings.registration_otp_secret.get_secret_value()
    if len(secret.encode("utf-8")) < 32 or secret == "REPLACE_ME":
        raise HTTPException(503, "邀请注册暂不可用。")


@router.post("/api/auth/register/code", status_code=202, response_model=DataResponse[dict],
             dependencies=[Depends(require_registration)])
def send_registration_code(body: CodeRequest, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"data": request_code(request.app.state.settings, request.app.state.email_sender, body,
                                 request.client.host if request.client else "unknown")}


@router.post("/api/auth/register", status_code=201, response_model=DataResponse[User],
             dependencies=[Depends(require_registration)])
def register(body: InviteRegister, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"data": complete_registration(request.app.state.settings, body,
                                          request.client.host if request.client else "unknown")}


@router.post("/api/auth/login", response_model=DataResponse[User])
def login(body: Login, request: Request, response: Response, connection: Database):
    rate_limit(request, body.username)
    row = connection.execute("SELECT * FROM users WHERE username=?", (body.username,)).fetchone()
    encoded = row["password_hash"] if row is not None else request.app.state.dummy_password_hash
    matches = verify_password(body.password.get_secret_value(), encoded)
    if row is None or not matches:
        raise HTTPException(401, "用户名或密码错误。")
    token = create_session(connection, row["id"], request.cookies.get(SESSION_COOKIE))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=urlsplit(request.app.state.settings.public_base_url).scheme == "https",
                        samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"data": public_user(row)}


@router.post("/api/auth/logout", response_model=DataResponse[dict])
def logout(body: ContractModel, request: Request, response: Response, connection: Database):
    revoke_session(connection, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax",
                           secure=urlsplit(request.app.state.settings.public_base_url).scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return {"data": {"logged_out": True}}
