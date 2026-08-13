from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from .io_utils import RadarError


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value: raise RadarError(f"Missing required environment variable: {name}")
    return value


def send_html(subject: str, html_body: str) -> None:
    sender, auth_code, recipient = _env("QQ_EMAIL"), _env("QQ_EMAIL_AUTH_CODE"), _env("RECIPIENT_EMAIL")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content("ME × Protein 周报已生成，请使用支持 HTML 的邮件客户端查看。")
    message.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(sender, auth_code)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise RadarError(f"QQ SMTP delivery failed: {type(exc).__name__}") from exc


def send_alert(issue_date: str, error: str) -> None:
    sender, auth_code, recipient = _env("QQ_EMAIL"), _env("QQ_EMAIL_AUTH_CODE"), _env("RECIPIENT_EMAIL")
    message = EmailMessage()
    message["From"], message["To"] = sender, recipient
    message["Subject"] = f"[告警] ME × Protein 周报失败 | {issue_date}"
    message.set_content(f"本期自动任务未完成，历史记录未写入。\n\n错误：{error[:2000]}\n\n请在 GitHub Actions 日志中查看详情。")
    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(sender, auth_code)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise RadarError(f"QQ SMTP alert failed: {type(exc).__name__}") from exc

