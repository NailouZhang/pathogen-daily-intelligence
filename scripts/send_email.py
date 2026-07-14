#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Send the generated HTML email when SMTP secrets are configured.")
    parser.add_argument("--html", required=True)
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    host = os.getenv("SMTP_HOST", "")
    user = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    recipient = os.getenv("EMAIL_TO", "")
    sender = os.getenv("EMAIL_FROM", user)
    if not all([host, recipient, sender]):
        print("SMTP is not configured; email delivery skipped.")
        return 0
    port = int(os.getenv("SMTP_PORT", "587"))
    msg = EmailMessage()
    msg["Subject"] = args.subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content("Please view this message in an HTML-capable email client.")
    msg.add_alternative(Path(args.html).read_text(encoding="utf-8"), subtype="html")
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    print("Email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
