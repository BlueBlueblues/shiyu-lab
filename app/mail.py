"""邮件适配边界；核心注册仅依赖 sender.send_code(email, code)。"""
import smtplib
import ssl
from email.message import EmailMessage
from email.headerregistry import Address

import httpx


class MailDeliveryError(Exception):
    """不给调用方返回供应商正文、收件人或认证信息。"""

    def __init__(self, message="邮件发送失败", *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class EmailSender:
    def __init__(self, settings):
        self.settings = settings

    def send_code(self, email, code):
        settings = self.settings
        subject = "试玉 · SHIYU LAB 邮箱验证码"
        content = f"试玉 · SHIYU LAB\n\n你的邀请码注册验证码是：{code}。10 分钟内有效。请勿转发；如非本人操作，请忽略。\n\n以试辨真，以攻成玉"
        try:
            if not settings.mail_from.strip() or "\r" in settings.mail_from or "\n" in settings.mail_from:
                raise MailDeliveryError()
            message = EmailMessage()
            message["From"] = settings.mail_from
            sender = message["From"]
            if (sender.defects or len(sender.addresses) != 1
                    or not sender.addresses[0].username or not sender.addresses[0].domain):
                raise MailDeliveryError()
            sender = str(Address(display_name="试玉 · SHIYU LAB", addr_spec=sender.addresses[0].addr_spec))
            if settings.mail_provider == "resend":
                if not settings.mail_api_key.get_secret_value() or settings.mail_api_key.get_secret_value() == "REPLACE_ME":
                    raise MailDeliveryError()
                response = httpx.post(
                    "https://api.resend.com/emails", timeout=15, follow_redirects=False,
                    headers={"Authorization": "Bearer " + settings.mail_api_key.get_secret_value()},
                    json={"from": sender, "to": [email], "subject": subject, "text": content})
                if not response.is_success:
                    raise MailDeliveryError()
                payload = response.json()
                identifier = payload.get("id") if isinstance(payload, dict) else None
                if not isinstance(identifier, str) or not identifier.strip():
                    raise MailDeliveryError(uncertain=True)
            elif settings.mail_provider == "smtp":
                if not settings.smtp_host:
                    raise MailDeliveryError()
                message.replace_header("From", sender)
                message["To"], message["Subject"] = email, subject
                message.set_content(content)
                context = ssl.create_default_context()
                if settings.smtp_tls == "ssl":
                    transport = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                                 timeout=15, context=context)
                else:
                    transport = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
                with transport as connection:
                    if settings.smtp_tls == "starttls":
                        connection.starttls(context=context)
                    if settings.smtp_user:
                        connection.login(settings.smtp_user, settings.smtp_password.get_secret_value())
                    if connection.send_message(message):
                        raise MailDeliveryError()
            else:
                raise MailDeliveryError()
        except MailDeliveryError as error:
            raise MailDeliveryError("邮件发送结果未确认" if error.uncertain else "邮件发送失败",
                                    uncertain=error.uncertain) from None
        except Exception:
            # 请求可能已到达供应商，不能把超时或异常响应视为确定未发送。
            raise MailDeliveryError("邮件发送结果未确认", uncertain=True) from None
