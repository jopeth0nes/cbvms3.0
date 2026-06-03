"""SMTP email sender for CBVMS — sends login credentials to newly-enrolled students."""

from __future__ import annotations

import json
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "smtp_config.json"

_DEFAULTS: dict = {
    "host": "smtp.gmail.com",
    "port": 587,
    "sender_email": "",
    "sender_password": "",
    "anthropic_api_key": "",
}


def load_smtp_config() -> dict:
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = dict(_DEFAULTS)
            cfg.update({k: v for k, v in data.items() if k in _DEFAULTS})
            return cfg
    except Exception as exc:
        print(f"[EmailSender] config load failed: {exc}")
    return dict(_DEFAULTS)


def save_smtp_config(config: dict) -> None:
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CONFIG_PATH.write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[EmailSender] config save failed: {exc}")


def is_configured() -> bool:
    cfg = load_smtp_config()
    return bool(cfg.get("sender_email") and cfg.get("sender_password"))


def send_credentials(
    to_email: str,
    student_name: str,
    student_id: str,
    username: str,
    password: str,
) -> tuple[bool, str]:
    """Send login credentials to a student's email address.

    Returns (success, error_message). error_message is empty on success.
    """
    cfg = load_smtp_config()
    sender = cfg.get("sender_email", "").strip()
    app_password = cfg.get("sender_password", "").strip()
    host = cfg.get("host", "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int(cfg.get("port", 587) or 587)

    if not sender or not app_password:
        return False, "SMTP not configured. Set sender email and password in Settings → Email."

    subject = "CBVMS — Your Student Login Credentials"
    html_body = f"""
<html><body style="font-family:Arial,sans-serif;color:#222;max-width:520px">
  <div style="background:#1a1a2e;padding:24px 32px;border-radius:8px 8px 0 0">
    <h2 style="color:#4A9EFF;margin:0">CBVMS</h2>
    <p style="color:#aaa;margin:4px 0 0">Computer Based Vision Monitoring System</p>
  </div>
  <div style="padding:24px 32px;border:1px solid #ddd;border-top:none;border-radius:0 0 8px 8px">
    <p>Hello <strong>{student_name}</strong>,</p>
    <p>You have been successfully enrolled in CBVMS. Below are your login credentials:</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr style="background:#f5f5f5">
        <td style="padding:10px 14px;font-weight:bold;width:40%">Student ID</td>
        <td style="padding:10px 14px">{student_id}</td>
      </tr>
      <tr>
        <td style="padding:10px 14px;font-weight:bold">Username</td>
        <td style="padding:10px 14px;font-family:monospace;font-size:15px">{username}</td>
      </tr>
      <tr style="background:#f5f5f5">
        <td style="padding:10px 14px;font-weight:bold">Password</td>
        <td style="padding:10px 14px;font-family:monospace;font-size:15px">{password}</td>
      </tr>
    </table>
    <p style="color:#888;font-size:13px">
      Please keep your credentials confidential. You can use them to log in to the
      CBVMS student portal to view your violation records and submit appeals.
    </p>
  </div>
</body></html>
"""
    text_body = (
        f"Hello {student_name},\n\n"
        f"You have been enrolled in CBVMS.\n\n"
        f"Student ID : {student_id}\n"
        f"Username   : {username}\n"
        f"Password   : {password}\n\n"
        "Please keep your credentials confidential.\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(sender, app_password)
            server.sendmail(sender, to_email, msg.as_string())
        return True, ""
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP authentication failed. Check sender email and app password."
    except smtplib.SMTPRecipientsRefused:
        return False, f"Recipient address rejected by server: {to_email}"
    except TimeoutError:
        return False, "Connection timed out. Check SMTP host and port."
    except Exception as exc:
        return False, str(exc)
