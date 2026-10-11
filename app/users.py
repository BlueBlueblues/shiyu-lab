import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from pydantic import Field, SecretStr, StringConstraints, field_validator

from app.schemas import ContractModel
from app.passwords import hash_password

SESSION_COOKIE = "firstorder_session"
SESSION_SECONDS = 12 * 60 * 60
Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=32,
                                           pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]


class User(ContractModel):
    id: str
    username: str
    email: str | None
    created_at: str


class Register(ContractModel):
    username: Username
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] | None = None
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if value == "":
            return None
        if value is not None and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式无效")
        return value


class Login(ContractModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: Annotated[SecretStr, Field(min_length=1, max_length=128)]


def public_user(row):
    return {key: row[key] for key in User.model_fields}


def create_user(connection, body: Register):
    user = User(id=str(uuid4()), username=body.username, email=body.email,
                created_at=datetime.now(timezone.utc).isoformat())
    connection.execute("INSERT INTO users (id,username,email,password_hash,created_at) VALUES (?,?,?,?,?)",
        (user.id, user.username, user.email, hash_password(body.password.get_secret_value()), user.created_at))
    return user


def token_hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def session_user(connection, token):
    if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return None
    row = connection.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=? AND s.expires_at>?""", (token_hash(token), int(time.time()))).fetchone()
    return public_user(row) if row is not None else None


def revoke_session(connection, token):
    if token and re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))


def create_session(connection, user_id, previous_token=None):
    revoke_session(connection, previous_token)
    now = int(time.time())
    connection.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
    token = secrets.token_urlsafe(32)
    connection.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                       (token_hash(token), user_id, now, now + SESSION_SECONDS))
    return token
