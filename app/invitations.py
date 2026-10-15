import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import HTTPException
from pydantic import Field, SecretStr, StringConstraints, field_validator

from app.db import connect_db
from app.mail import MailDeliveryError
from app.passwords import LEGACY_ADMIN_ID
from app.schemas import ContractModel
from app.users import Username, create_user

GENERIC_FAILURE = "邀请码不可用或注册信息无法验证，请核对信息。"
CODE_FAILURE = "验证码错误、过期或已失效，请重新获取。"
OTP_TTL, COOLDOWN, MAX_ATTEMPTS = 600, 60, 5
MAIL_UNCERTAIN = "邮件发送结果或验证码状态未能确认，请稍后再试；原验证码如仍有效可继续使用。"


class CodeRequest(ContractModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)]
    invitation: Annotated[SecretStr, Field(min_length=43, max_length=43)]

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
            raise ValueError("邮箱格式无效")
        return value.casefold()

    @field_validator("invitation")
    @classmethod
    def valid_invitation(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value.get_secret_value()):
            raise ValueError("邀请码格式无效")
        return value


class InviteRegister(CodeRequest):
    username: Username
    password: Annotated[SecretStr, Field(min_length=12, max_length=128)]
    verification_code: Annotated[SecretStr, Field(min_length=6, max_length=6)]

    @field_validator("verification_code")
    @classmethod
    def numeric_code(cls, value):
        if not re.fullmatch(r"[0-9]{6}", value.get_secret_value()):
            raise ValueError("验证码必须为六位数字")
        return value


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_invitation(connection, expires_hours=24, max_uses=1):
    if not 0 < expires_hours <= 24 * 365 or not 1 <= max_uses <= 1000:
        raise ValueError("有效期须为 0–8760 小时，使用上限须为 1–1000")
    token, identity, now = secrets.token_urlsafe(32), str(uuid4()), int(time.time())
    connection.execute("""INSERT INTO invitations
        (id,token_hash,created_by,created_at,expires_at,max_uses) VALUES (?,?,?,?,?,?)""",
        (identity, digest(token), LEGACY_ADMIN_ID, now, now + int(expires_hours * 3600), max_uses))
    return identity, token


def available(connection, token, now):
    return connection.execute("""SELECT * FROM invitations WHERE token_hash=?
        AND revoked_at IS NULL AND expires_at>? AND used_count<max_uses""", (digest(token), now)).fetchone()


def code_hash(secret, identity, email, invitation_id, code):
    return hmac.new(secret.encode("utf-8"),
                    f"{identity}\0{email}\0{invitation_id}\0{code}".encode("utf-8"), hashlib.sha256).hexdigest()


def throttle(connection, action, email, token, ip, now):
    # SQLite 持久化，跨请求/进程共享；不信任客户端传入的 X-Forwarded-For。
    connection.execute("DELETE FROM registration_limits WHERE attempted_at<=?", (now - 3600,))
    dimensions = (("email", email, 5 if action == "send" else 20),
                  ("invitation", token, 10 if action == "send" else 30),
                  ("ip", ip, 20 if action == "send" else 60))
    for scope, value, maximum in dimensions:
        key, scope = digest(value), action + ":" + scope
        count = connection.execute("""SELECT COUNT(*) FROM registration_limits
            WHERE scope=? AND key_hash=? AND attempted_at>?""", (scope, key, now - 3600)).fetchone()[0]
        if count >= maximum:
            return HTTPException(429, "请求过于频繁，请稍后再试。", headers={"Retry-After": "3600"})
    for scope, value, _ in dimensions:
        connection.execute("INSERT INTO registration_limits VALUES (?,?,?)", (action + ":" + scope, digest(value), now))
    return None


def request_code(settings, sender, body, ip):
    identity = str(uuid4())
    token = body.invitation.get_secret_value()
    code = f"{secrets.randbelow(1000000):06d}"
    failure = None
    with connect_db(settings.database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        failure = throttle(connection, "send", body.email, token, ip, now)
        invite = available(connection, token, now)
        if failure is None and invite is None:
            failure = HTTPException(400, GENERIC_FAILURE)
        if failure is None:
            recent = connection.execute("""SELECT 1 FROM registration_challenges
                WHERE email=? AND created_at>? LIMIT 1""", (body.email, now - COOLDOWN)).fetchone()
            if recent:
                failure = HTTPException(429, "发送过于频繁，请一分钟后再试。", headers={"Retry-After": "60"})
        if failure is None:
            # 仅关闭旧的待发送记录；已启用验证码保留到新邮件确认受理。
            connection.execute("""UPDATE registration_challenges SET consumed_at=?
                WHERE email=? AND sent=0 AND consumed_at IS NULL""",
                               (now, body.email))
            connection.execute("""INSERT INTO registration_challenges
                (id,email,invitation_id,code_hash,created_at,expires_at) VALUES (?,?,?,?,?,?)""",
                (identity, body.email, invite["id"], code_hash(settings.registration_otp_secret.get_secret_value(),
                 identity, body.email, invite["id"], code), now, now + OTP_TTL))
    if failure:
        raise failure
    try:
        sender.send_code(body.email, code)
    except MailDeliveryError as error:
        try:
            with connect_db(settings.database_path) as connection:
                connection.execute("UPDATE registration_challenges SET consumed_at=? WHERE id=? AND sent=0",
                                   (int(time.time()), identity))
        except sqlite3.Error:
            raise HTTPException(503, MAIL_UNCERTAIN) from None
        raise HTTPException(503, MAIL_UNCERTAIN if error.uncertain else
                            "邮件暂时无法发送，请稍后再试；原验证码如仍有效可继续使用。") from None
    try:
        with connect_db(settings.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            pending = connection.execute("""SELECT expires_at FROM registration_challenges
                WHERE id=? AND sent=0 AND consumed_at IS NULL AND expires_at>?""", (identity, now)).fetchone()
            if pending is None or available(connection, token, now) is None:
                connection.execute("UPDATE registration_challenges SET consumed_at=? WHERE id=? AND sent=0", (now, identity))
                failure = HTTPException(503, MAIL_UNCERTAIN)
            else:
                connection.execute("""UPDATE registration_challenges SET consumed_at=?
                    WHERE email=? AND sent=1 AND consumed_at IS NULL""", (now, body.email))
                connection.execute("UPDATE registration_challenges SET sent=1 WHERE id=?", (identity,))
                expires_in = pending["expires_at"] - now
    except sqlite3.Error:
        raise HTTPException(503, MAIL_UNCERTAIN) from None
    if failure:
        raise failure
    return {"accepted": True, "cooldown_seconds": COOLDOWN, "expires_in": expires_in}


def complete_registration(settings, body, ip):
    token = body.invitation.get_secret_value()
    failure, user = None, None
    with connect_db(settings.database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        failure = throttle(connection, "verify", body.email, token, ip, now)
        invite = available(connection, token, now)
        if failure is None and invite is None:
            failure = HTTPException(400, GENERIC_FAILURE)
        challenge = None
        if failure is None:
            challenge = connection.execute("""SELECT * FROM registration_challenges
                WHERE email=? AND invitation_id=? AND sent=1 AND consumed_at IS NULL
                AND expires_at>? AND attempts<? ORDER BY created_at DESC LIMIT 1""",
                (body.email, invite["id"], now, MAX_ATTEMPTS)).fetchone()
            if challenge is None:
                failure = HTTPException(400, CODE_FAILURE)
        if failure is None:
            expected = code_hash(settings.registration_otp_secret.get_secret_value(), challenge["id"],
                                 body.email, invite["id"], body.verification_code.get_secret_value())
            if not hmac.compare_digest(expected, challenge["code_hash"]):
                connection.execute("""UPDATE registration_challenges SET attempts=attempts+1,
                    consumed_at=CASE WHEN attempts+1>=? THEN ? ELSE NULL END WHERE id=?""",
                    (MAX_ATTEMPTS, now, challenge["id"]))
                failure = HTTPException(400, CODE_FAILURE)
        if failure is None:
            connection.execute("UPDATE registration_challenges SET consumed_at=? WHERE id=?", (now, challenge["id"]))
            if connection.execute("SELECT 1 FROM users WHERE username=? OR lower(trim(email))=?",
                                  (body.username, body.email)).fetchone():
                failure = HTTPException(409, "无法完成注册，请核对信息或登录已有账号。")
            else:
                user = create_user(connection, body)
                connection.execute("UPDATE users SET email_verified_at=? WHERE id=?",
                                   (datetime.now(timezone.utc).isoformat(), user.id))
                updated = connection.execute("""UPDATE invitations SET used_count=used_count+1,last_used_at=?
                    WHERE id=? AND revoked_at IS NULL AND expires_at>? AND used_count<max_uses""",
                    (now, invite["id"], now))
                if updated.rowcount != 1:
                    raise RuntimeError("邀请注册事务冲突")
                connection.execute("INSERT INTO invitation_uses VALUES (?,?,?)", (invite["id"], user.id, now))
    # 失败计数也必须提交；在事务退出后抛出，不被 HTTP 异常回滚。
    if failure:
        raise failure
    return user
