import { env } from 'cloudflare:workers';
import { timingSafeEqual } from 'node:crypto';
import { pbkdf2Async } from '@noble/hashes/pbkdf2';
import { sha256 } from '@noble/hashes/sha256';
import { bytesToHex, hexToBytes } from '@noble/hashes/utils';

type Row = Record<string, any>;
const db = () => env.DB!;
const random = () => bytesToHex(crypto.getRandomValues(new Uint8Array(32)));
const digest = (s: string) => bytesToHex(sha256(new TextEncoder().encode(s)));
const json = (data: unknown, status = 200) => Response.json(data, { status, headers: { 'Cache-Control': 'private, no-store' } });
const COOKIE = '__Host-cbvms_session';
const DEFAULTS = new Set(['password', 'password123', 'student123', 'admin123', 'superadmin123']);
const DUMMY_HASH = 'pbkdf2_sha256$v1$600000$00000000000000000000000000000000$0000000000000000000000000000000000000000000000000000000000000000';
function equals(a: string, b: string) {
  const x = new TextEncoder().encode(a), y = new TextEncoder().encode(b);
  return x.length === y.length && timingSafeEqual(x, y);
}
export function validHash(value: unknown): value is string {
  return typeof value === 'string' && (/^[0-9a-f]{64}$/.test(value) || /^pbkdf2_sha256\$v1\$(?:[1-9][0-9]{5}|1[0-9]{6}|2000000)\$[0-9a-f]{32}\$[0-9a-f]{64}$/.test(value));
}
export async function verifyPassword(password: unknown, encoded: string): Promise<boolean> {
  if (typeof password !== 'string' || password.length > 1024 || !validHash(encoded)) return false;
  if (/^[0-9a-f]{64}$/.test(encoded)) return equals(digest(password), encoded);
  const parts = encoded.split('$');
  const hash = await pbkdf2Async(sha256, password, hexToBytes(parts[3]), { c: Number(parts[2]), dkLen: 32, asyncTick: 10 });
  return equals(bytesToHex(hash), parts[4]);
}
async function hashPassword(password: string) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const value = await pbkdf2Async(sha256, password, salt, { c: 600000, dkLen: 32, asyncTick: 10 });
  return `pbkdf2_sha256$v1$600000$${bytesToHex(salt)}$${bytesToHex(value)}`;
}
function cookie(request: Request) {
  return (request.headers.get('Cookie') || '').split(';').map(s => s.trim()).find(s => s.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1) || '';
}
function setCookie(response: Response, token: string, seconds: number) {
  response.headers.set('Set-Cookie', `${COOKIE}=${token}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=${seconds}`);
  return response;
}
export function csrfValid(request: Request, session: Row) {
  return request.headers.get('Origin') === new URL(request.url).origin && equals(request.headers.get('X-CSRF-Token') || '', session.csrf);
}
export async function studentSession(request: Request): Promise<Row | null> {
  const token = cookie(request);
  if (!/^[0-9a-f]{64}$/.test(token)) return null;
  const row = await db().prepare('SELECT a.*,s.csrf,s.expires,s.credential_hash FROM portal_sessions s JOIN portal_accounts a ON a.student_id=s.student_id JOIN portal_students p ON p.id=a.student_id WHERE s.token_hash=? AND s.expires>? AND p.active=1')
    .bind(digest(token), Date.now()).first<Row>();
  return row && equals(row.credential_hash, digest(row.password_hash)) ? row : null;
}
async function issueSession(account: Row) {
  const token = random(), csrf = random();
  const seconds = account.must_change_password ? 600 : 8 * 3600;
  const expires = Date.now() + seconds * 1000;
  await db().prepare('DELETE FROM portal_sessions WHERE expires<=?').bind(Date.now()).run();
  const inserted = await db().prepare('INSERT INTO portal_sessions (token_hash,student_id,credential_hash,csrf,expires) SELECT ?,a.student_id,?,?,? FROM portal_accounts a JOIN portal_students p ON p.id=a.student_id WHERE a.student_id=? AND a.password_hash=? AND p.active=1')
    .bind(digest(token), digest(account.password_hash), csrf, expires, account.student_id, account.password_hash).run();
  if (!inserted.meta.changes) return json({ error: 'Credentials changed. Please sign in again.' }, 401);
  const student = await db().prepare('SELECT name FROM portal_students WHERE id=?').bind(account.student_id).first<Row>();
  return setCookie(json({ student_id: account.student_id, username: account.username, name: student?.name, csrf, expires: expires / 1000, must_change_password: !!account.must_change_password }), token, seconds);
}
async function rateLimit(request: Request, username: string) {
  const time = Date.now(), window = Math.floor(time / 60000);
  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const keys = ['ip:' + digest(ip), 'account:' + digest(username)];
  await db().prepare('DELETE FROM portal_login_limits WHERE expires<?').bind(time).run();
  for (const key of keys) {
    const row = await db().prepare('INSERT INTO portal_login_limits (key,attempts,expires) VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1 RETURNING attempts').bind(key + ':' + window, time + 120000).first<{ attempts: number }>();
    if ((row?.attempts || 0) > (key.startsWith('ip:') ? 15 : 10)) return false;
  }
  return true;
}
export async function authRoute(request: Request, data: Row): Promise<Response | null> {
  const path = new URL(request.url).pathname;
  if (!['/api/login', '/api/logout', '/api/setup/password', '/api/password'].includes(path)) return null;
  if (request.method !== 'POST') return json({ error: 'Method not allowed.' }, 405);
  if (request.headers.get('Origin') !== new URL(request.url).origin) return json({ error: 'Request origin rejected.' }, 403);
  if (path === '/api/login') {
    const username = typeof data.username === 'string' ? data.username.trim().slice(0, 200) : '';
    if (!await rateLimit(request, username)) return json({ error: 'Too many sign-in attempts. Try again in a minute.' }, 429);
    const account = await db().prepare('SELECT a.* FROM portal_accounts a JOIN portal_students p ON p.id=a.student_id WHERE a.username=? AND p.active=1').bind(username).first<Row>();
    const verified = await verifyPassword(data.password, account?.password_hash || DUMMY_HASH);
    if (!account || !verified) return json({ error: 'Incorrect student username or password. If newly registered, wait for campus sync.' }, 401);
    return issueSession(account);
  }
  const session = await studentSession(request);
  if (!session) return json({ error: 'Please sign in.' }, 401);
  if (!csrfValid(request, session)) return json({ error: 'Please refresh and try again.' }, 403);
  if (path === '/api/logout') {
    await db().prepare('DELETE FROM portal_sessions WHERE token_hash=?').bind(digest(cookie(request))).run();
    return setCookie(json({ ok: true }), '', 0);
  }
  if (path === '/api/setup/password' && !session.must_change_password) return json({ error: 'Password setup is already complete.' }, 409);
  const password = data.password;
  if (typeof password !== 'string' || password !== data.confirmation || password.length < 8 || password.length > 1024 || DEFAULTS.has(password.toLowerCase()) || [session.username, session.student_id].includes(password)) return json({ error: 'Choose a personal password of 8–1024 characters and confirm it.' }, 400);
  if (!session.must_change_password && !await verifyPassword(data.current_password, session.password_hash)) return json({ error: 'Current password is incorrect.' }, 400);
  if (await verifyPassword(password, session.password_hash)) return json({ error: 'Choose a different password.' }, 400);
  if (session.pending_action_id) return json({ error: 'Your last password change is still syncing. Try again after campus sync.' }, 409);
  const hash = await hashPassword(password), id = crypto.randomUUID();
  const payload = { password_hash: hash, expected_hash: session.password_hash, expected_version: session.credential_version, expected_must_change: session.must_change_password };
  const result = await db().batch([
    db().prepare('UPDATE portal_accounts SET password_hash=?,must_change_password=0,pending_base_hash=?,pending_action_id=? WHERE student_id=? AND password_hash=? AND pending_action_id IS NULL').bind(hash, session.password_hash, id, session.student_id, session.password_hash),
    db().prepare("INSERT INTO portal_actions (id,student_id,kind,payload,created_at) SELECT ?,student_id,'password',?,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM portal_accounts WHERE student_id=? AND pending_action_id=?").bind(id, JSON.stringify(payload), session.student_id, id),
  ]);
  if (!result[0].meta.changes) return json({ error: 'Credentials changed. Please sign in again.' }, 401);
  await db().prepare('DELETE FROM portal_sessions WHERE student_id=?').bind(session.student_id).run();
  return issueSession({ ...session, password_hash: hash, must_change_password: 0 });
}
