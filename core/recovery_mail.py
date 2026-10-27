"""Deliver cloud password-recovery mail through the campus SMTP account."""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import parseaddr

from core.email_sender import load_smtp_config

logger = logging.getLogger(__name__)
_CODE = re.compile(r'^[0-9]{6}$')
_MAX_CODE_FUTURE_MS = 10 * 60 * 1000
_MAX_NOTIFICATION_FUTURE_MS = 24 * 60 * 60 * 1000


def _smtp_config(config=None):
    cfg = load_smtp_config() if config is None else config
    sender = str(cfg.get('sender_email') or '').strip()
    password = str(cfg.get('sender_password') or '').strip()
    host = str(cfg.get('host') or '').strip()
    try:
        port = int(cfg.get('port') or 587)
    except (TypeError, ValueError):
        return None
    if not sender or not password or not host or not 1 <= port <= 65535:
        return None
    if '\r' in sender or '\n' in sender or parseaddr(sender)[1] != sender:
        return None
    return {'sender': sender, 'password': password, 'host': host, 'port': port}


def smtp_ready(config=None):
    """Return whether local SMTP credentials are present and usable."""
    return _smtp_config(config) is not None


def check_smtp_connection(config=None):
    """Verify TLS and login without sending email; never expose provider errors."""
    smtp = _smtp_config(config)
    if smtp is None:
        return False
    try:
        with smtplib.SMTP(smtp['host'], smtp['port'], timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(smtp['sender'], smtp['password'])
        return True
    except Exception:
        return False


def _valid_recipient(address):
    return (isinstance(address, str) and len(address) <= 254
            and '\r' not in address and '\n' not in address
            and parseaddr(address)[1] == address and '@' in address)


def _message(sender, recipient, code):
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = recipient
    if code is None:
        msg['Subject'] = 'CBVMS password changed'
        msg.set_content(
            'Your CBVMS account password was changed, and cloud sessions were revoked. '
            'If this change was made while the campus desktop was offline, its updates '
            'will sync when it reconnects. If you did not make this change, contact '
            'your campus administrator immediately.'
        )
    else:
        msg['Subject'] = 'CBVMS verification code'
        msg.set_content(
            f'Your CBVMS verification code is: {code}\n\n'
            'This code expires in 10 minutes. If you did not request it, '
            'you can ignore this message. Do not share this code with anyone.'
        )
    return msg


def _send(config, recipient, code):
    """Send over required STARTTLS; return status without exposing mail errors."""
    msg = _message(config['sender'], recipient, code)
    try:
        server = smtplib.SMTP(config['host'], config['port'], timeout=15)
    except Exception:
        return 'failed'
    send_started = False
    try:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(config['sender'], config['password'])
        send_started = True
        refused = server.send_message(msg)
        return 'failed' if refused and recipient in refused else 'accepted'
    except Exception:
        # The server may have accepted DATA before the connection failed.
        return 'uncertain' if send_started else 'failed'
    finally:
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass


def _receipt(client, message, status):
    try:
        client.call('mail/receipt', {
            'id': message['id'], 'claim': message['claim'], 'status': status,
        })
        return True
    except Exception:
        logger.warning('Recovery email receipt could not be recorded.')
        return False


def process_recovery_mail_once(client, config=None, now_ms=None):
    """Claim at most one cloud message, deliver it, then report its outcome.

    No message or code is written locally. If SMTP's DATA result is ambiguous,
    the cloud receives ``uncertain`` and must not issue the same claim again.
    """
    smtp = _smtp_config(config)
    if smtp is None:
        return 'unconfigured'  # Do not claim until local delivery is possible.
    response = client.call('mail/claim', {})
    messages = response.get('messages') if isinstance(response, dict) else None
    if not messages:
        return 'empty'
    if not isinstance(messages, list) or len(messages) != 1 or not isinstance(messages[0], dict):
        logger.warning('Recovery email claim response was invalid.')
        return 'invalid'
    message = messages[0]
    if not isinstance(message.get('id'), str) or not message['id'] or not isinstance(message.get('claim'), str) or not message['claim']:
        logger.warning('Recovery email claim response was invalid.')
        return 'invalid'
    recipient, code = message.get('to'), message.get('code')
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        expires_at = int(message['expires_at'])
    except (KeyError, TypeError, ValueError):
        _receipt(client, message, 'failed')
        return 'failed'
    if expires_at <= current_ms:
        _receipt(client, message, 'failed')
        return 'expired'
    max_future = (_MAX_NOTIFICATION_FUTURE_MS if code is None
                  else _MAX_CODE_FUTURE_MS)
    if expires_at > current_ms + max_future:
        _receipt(client, message, 'failed')
        return 'failed'
    if ('code' not in message or not _valid_recipient(recipient)
            or (code is not None and (not isinstance(code, str) or not _CODE.fullmatch(code)))):
        _receipt(client, message, 'failed')
        return 'failed'
    try:
        status = _send(smtp, recipient, code)
    except Exception:
        # Defensive boundary: never log the exception since SMTP libraries may
        # include recipient addresses or message details in their text.
        logger.warning('Recovery email delivery failed unexpectedly.')
        status = 'uncertain'
    if status == 'uncertain':
        logger.warning('Recovery email delivery outcome is uncertain.')
    elif status == 'failed':
        logger.warning('Recovery email delivery failed.')
    _receipt(client, message, status)
    return status
