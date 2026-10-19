"""Exercise real password auth against isolated local Workers preview storage."""
import base64
import json
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from auth.passwords import hash_password
from tests.evidence_fixture import picture_evidence

ORIGIN = 'http://127.0.0.1:8788'


def call(path, data=None, session=None, sync=False, headers=None):
    values = {'Content-Type': 'application/json', 'Origin': ORIGIN}
    if sync:
        values['Authorization'] = 'Bearer local-preview-only'
    if session:
        values.update({'Cookie': session.get('cookie', ''), 'X-CSRF-Token': session.get('csrf', '')})
    values.update(headers or {})
    request = Request(ORIGIN + path, data=None if data is None else json.dumps(data).encode(), headers=values)
    for _ in range(4):
        try:
            response = urlopen(request, timeout=60)
        except HTTPError as exc:
            response = exc
        raw = response.read()
        if response.status != 503 or b'worker restarted mid-request' not in raw:
            break
        time.sleep(0.3)
    content = json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw
    cookie = response.headers.get('Set-Cookie')
    if session is not None and cookie:
        session['cookie'] = cookie.split(';')[0]
        session['flags'] = cookie
    if session is not None and isinstance(content, dict) and content.get('csrf'):
        session['csrf'] = content['csrf']
    return response.status, content


def main():
    tag = uuid.uuid4().hex[:12]
    sid1, sid2 = 'AUTH1-' + tag, 'AUTH2-' + tag
    password = 'Temporary passphrase 123'
    account1 = {'username': sid1, 'password_hash': hash_password(password), 'must_change_password': True}
    account2 = {'username': sid2, 'password_hash': hash_password(password), 'must_change_password': False}
    assert call('/portal.html')[0] == 200
    assert call('/api/me', headers={'oai-authenticated-user-id': 'fake', 'oai-authenticated-user-email': 'fake@example.com'})[0] == 401
    assert call('/api/sync/actions')[0] == 401
    for sid, account, vid in ((sid1, account1, 101), (sid2, account2, 202)):
        snapshot = {'profile': {'student_id': sid, 'name': sid}, 'violations': [{'id': vid, 'can_appeal': True, 'appeal_deadline': '2099-01-01 00:00:00'}], 'notifications': [], 'appeals': []}
        assert call('/api/sync/student', {'student': {'id': sid, 'email': sid + '@example.test', 'name': sid, 'snapshot': snapshot, 'active': True}}, sync=True)[0] == 200
        assert call('/api/sync/account', {'student_id': sid, 'account': account}, sync=True)[0] == 200
    assert call('/api/login', {'username': sid1, 'password': 'wrong'})[0] == 401
    first = {}
    assert call('/api/login', {'username': sid1, 'password': password}, session=first)[0] == 200
    assert all(flag in first['flags'] for flag in ('HttpOnly', 'Secure', 'SameSite=Strict'))
    assert call('/api/page?page=dashboard', session=first)[0] == 403
    assert call('/api/setup/password', {'password': 'short', 'confirmation': 'short'}, session=first)[0] == 400
    old_session = dict(first)
    new_password = 'A personal cloud passphrase 123'
    status, setup = call('/api/setup/password', {'password': new_password, 'confirmation': new_password}, session=first)
    assert status == 200 and not setup['must_change_password'], (status, setup)
    assert call('/api/me', session=old_session)[0] == 401
    status, me = call('/api/me', session=first)
    assert status == 200 and me['student_id'] == sid1 and 'password_hash' not in me
    assert call('/api/preferences', {'dark_mode': True}, session=first, headers={'X-CSRF-Token': 'forged'})[0] == 403
    assert call('/api/preferences', {'dark_mode': True}, session=first)[0] == 200
    status, page = call('/api/page?page=violations&student_id=' + sid2, session=first)
    assert status == 200 and [v['id'] for v in page['_violations']] == [101]
    assert call('/api/evidence?id=202', session=first)[0] == 404
    # An old desktop snapshot cannot erase a password awaiting import.
    assert call('/api/sync/account', {'student_id': sid1, 'account': account1}, sync=True)[0] == 200
    second = {}
    assert call('/api/login', {'username': sid1, 'password': new_password}, session=second)[0] == 200
    # A newer desktop reset supersedes the pending cloud change and revokes sessions.
    account1['password_hash'] = hash_password('Reset on campus 123')
    account1['must_change_password'] = False
    assert call('/api/sync/account', {'student_id': sid1, 'account': account1}, sync=True)[0] == 200
    assert call('/api/me', session=second)[0] == 401
    assert call('/api/login', {'username': sid1, 'password': 'Reset on campus 123'}, session=second)[0] == 200
    assert call('/api/logout', {}, session=second)[0] == 200
    assert call('/api/me', session=second)[0] == 401
    third = {}
    assert call('/api/login', {'username': sid2, 'password': password}, session=third)[0] == 200
    assert call('/api/sync/account', {'student_id': sid2, 'account': None}, sync=True)[0] == 200
    assert call('/api/me', session=third)[0] == 401
    print('Student password auth checks passed: login, secure cookies, setup, isolation, CSRF, password reset, logout, and account deletion.')


if __name__ == '__main__':
    main()
