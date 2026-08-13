"""Fallback alert for workflow failures outside the main pipeline."""

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage


sender = os.environ["QQ_EMAIL"]
recipient = os.environ["RECIPIENT_EMAIL"]
message = EmailMessage()
message["From"] = sender
message["To"] = recipient
message["Subject"] = "[告警] ME × Protein GitHub 工作流失败"
message.set_content("GitHub 工作流在主文献流水线以外的步骤失败，请查看 Actions 日志。\n\n" + " ".join(sys.argv[1:])[:1500])
with smtplib.SMTP_SSL("smtp.qq.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
    smtp.login(sender, os.environ["QQ_EMAIL_AUTH_CODE"])
    smtp.send_message(message)

