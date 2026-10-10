import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

basic = HTTPBasic(auto_error=False, realm="FirstOrderLab")


def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)],
) -> str:
    settings = request.app.state.settings
    user_matches = secrets.compare_digest(
        (credentials.username if credentials else "").encode("utf-8"),
        settings.admin_user.encode("utf-8"),
    )
    password_matches = secrets.compare_digest(
        (credentials.password if credentials else "").encode("utf-8"),
        settings.admin_password.get_secret_value().encode("utf-8"),
    )
    if not (credentials and user_matches and password_matches):
        raise HTTPException(
            status_code=401, detail="需要管理员身份验证。",
            headers={"WWW-Authenticate": 'Basic realm="FirstOrderLab"'},
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
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
    return settings.admin_user
