"""IPROG transport. Secrets stay in the process environment; errors stay generic."""
import json
import re
import urllib.request
from core.sms_config import get_token

ENDPOINT = "https://www.iprogsms.com/api/v1/sms_messages"


def configured():
    return bool(get_token())


def normalize_phone(value):
    phone = re.sub(r"[\s()+-]", "", value or "")
    if re.fullmatch(r"09\d{9}", phone):
        phone = "63" + phone[1:]
    return phone if re.fullmatch(r"639\d{9}", phone) else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the token to a redirected endpoint.


def check_token(token):
    """Read-only authentication check; never sends a message or exposes responses."""
    request = urllib.request.Request('https://www.iprogsms.com/api/v1/account/sms_credits',
        data=json.dumps({'api_token':token.strip()}).encode(),
        headers={'Content-Type':'application/json','User-Agent':'CBVMS-SMS/1'},method='GET')
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request,timeout=15) as response:
            result = json.loads(response.read(65536))
        if result.get('status') == 'success' and isinstance(result.get('data'),dict) and 'load_balance' in result['data']:
            return True, 'IPROG token verified.'
        if str(result.get('message','')).strip().lower() == 'invalid token':
            return False, 'IPROG rejected this token as invalid. Copy your current API token from your IPROG account.'
        return False, 'IPROG did not verify this token. Check your account API settings.'
    except Exception:
        return False, 'Could not verify the token with IPROG. Check internet access and retry.'


def send_sms(phone, message):
    token = get_token()
    if not token or not normalize_phone(phone):
        return "failed", None
    body = json.dumps(dict(api_token=token, phone_number=normalize_phone(phone),
                           message=message)).encode("utf-8")
    request = urllib.request.Request(ENDPOINT, data=body,
                                   headers={"Content-Type": "application/json", "User-Agent": "CBVMS-SMS/1"}, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=15) as response:
            result = json.loads(response.read(65536))
        if isinstance(result, dict) and str(result.get("status")) == "200" and result.get("message_id"):
            return "accepted", str(result["message_id"])
        return "failed", None
    except Exception:
        # Timeout may mean IPROG accepted the SMS. Never automatically retry it.
        return "unknown", None
