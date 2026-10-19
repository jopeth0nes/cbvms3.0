import { env } from 'cloudflare:workers';
import { authRoute, studentSession, csrfValid, syncAccount } from '../../../lib/student-auth';
import { timingSafeEqual } from 'node:crypto';

export const dynamic = 'force-dynamic';
type Obj = Record<string, any>;
const db = () => { if (!env.DB) throw Error('Cloud database unavailable.'); return env.DB; };
const bucket = () => { if (!env.BUCKET) throw Error('Evidence storage unavailable.'); return env.BUCKET; };
const now = () => new Date().toISOString();
const json = (value: unknown, status = 200) => Response.json(value, { status, headers: { 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' } });
const originalKey = (sid: string, id: string) => `original/${encodeURIComponent(sid)}/${encodeURIComponent(id)}`;
async function body(request: Request): Promise<Obj> {
  const raw = await request.text();
  if (raw.length > 15 * 1024 * 1024) throw Error('Request too large.');
  const value = JSON.parse(raw);
  if (!value || Array.isArray(value) || typeof value !== 'object') throw Error('JSON object required.');
  return value;
}
function picture(encoded: string, filename: string) {
  if (typeof encoded !== 'string' || !/\.(jpe?g|png|bmp)$/i.test(filename)) throw Error('Picture required.');
  const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
  const valid = (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) || (bytes[0] === 137 && bytes[1] === 80 && bytes[2] === 78 && bytes[3] === 71) || (bytes[0] === 66 && bytes[1] === 77);
  if (!valid || !bytes.length || bytes.length > 10 * 1024 * 1024) throw Error('Choose a picture up to 10 MB.');
  return bytes;
}
async function sync(request: Request, path: string) {
  const secret = (env as unknown as { SYNC_KEY?: string }).SYNC_KEY;
  const token = new TextEncoder().encode(request.headers.get('Authorization') || '');
  const expected = new TextEncoder().encode(`Bearer ${secret}`);
  if (!secret || token.length !== expected.length || !timingSafeEqual(token, expected)) return json({ error: 'Unauthorized' }, 401);
  if (path === '/api/sync/actions' && request.method === 'GET') {
    const rows = await db().prepare("SELECT * FROM portal_actions WHERE status='queued' ORDER BY created_at LIMIT 1").all<Obj>();
    const actions = [];
    for (const row of rows.results) {
      const payload = JSON.parse(row.payload);
      if (payload.evidence_key) {
        const file = await bucket().get(payload.evidence_key);
        if (!file) throw Error('Evidence missing.');
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = ''; for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        payload.file = btoa(binary);
      }
      actions.push({ ...row, payload });
    }
    return json({ actions });
  }
  if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
  const data = await body(request);
  if (path === '/api/sync/ack') {
    if (!['applied', 'rejected'].includes(data.status)) throw Error('Invalid status.');
    if (data.status === 'rejected') {
      const action = await db().prepare("SELECT payload FROM portal_actions WHERE id=? AND kind='password'").bind(data.id).first<{ payload: string }>();
      if (action) await db().prepare('UPDATE portal_accounts SET password_hash=pending_base_hash,must_change_password=?,pending_base_hash=NULL,pending_action_id=NULL WHERE pending_action_id=?').bind(JSON.parse(action.payload).expected_must_change ? 1 : 0, data.id).run();
    }
    await db().prepare("UPDATE portal_actions SET status=?,result=? WHERE id=? AND status='queued'").bind(data.status, String(data.result || ''), data.id).run();
    return json({ ok: true });
  }
  if (path === '/api/sync/account') {
    if (!await db().prepare('SELECT id FROM portal_students WHERE id=?').bind(data.student_id).first()) throw Error('Student not synced.');
    if (data.account === null) {
      await db().batch([
        db().prepare('DELETE FROM portal_sessions WHERE student_id=?').bind(data.student_id),
        db().prepare('DELETE FROM portal_accounts WHERE student_id=?').bind(data.student_id),
      ]);
    } else await syncAccount(data.student_id, data.account);
    return json({ ok: true });
  }
  if (path === '/api/sync/student') {
    const s = data.student;
    if (!s?.id || typeof s.email !== 'string' || !s.email.includes('@') || !s.snapshot) throw Error('Student ID, email and snapshot required.');
    const snapshot = JSON.stringify(s.snapshot);
    if (snapshot.length > 1500000) throw Error('Snapshot exceeds storage limit.');
    await db().prepare('INSERT INTO portal_students (id,email,name,snapshot,synced_at,active) VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET email=excluded.email,name=excluded.name,snapshot=excluded.snapshot,synced_at=excluded.synced_at,active=excluded.active')
      .bind(String(s.id), s.email.trim().toLowerCase(), String(s.name), snapshot, now(), s.active ? 1 : 0).run();
    return json({ ok: true });
  }
  if (path === '/api/sync/evidence') {
    const student = await db().prepare('SELECT snapshot FROM portal_students WHERE id=?').bind(data.student_id).first<{ snapshot: string }>();
    if (!student || !JSON.parse(student.snapshot).violations.some((v: Obj) => String(v.id) === String(data.violation_id))) throw Error('Record not owned by student.');
    await bucket().put(originalKey(data.student_id, String(data.violation_id)), picture(data.file, 'original.jpg'), { httpMetadata: { contentType: 'image/jpeg' } });
    return json({ ok: true });
  }
  if (path === '/api/sync/roster') {
    if (!Array.isArray(data.ids) || data.ids.some((id: unknown) => typeof id !== 'string')) throw Error('Student IDs required.');
    const roster = await db().prepare('SELECT id FROM portal_students WHERE active=1').all<{ id: string }>();
    const current = new Set(data.ids), removed = roster.results.filter(row => !current.has(row.id));
    if (removed.length) await db().batch(removed.flatMap(row => [
      db().prepare('UPDATE portal_students SET active=0 WHERE id=?').bind(row.id),
      db().prepare('DELETE FROM portal_sessions WHERE student_id=?').bind(row.id),
    ]));
    return json({ ok: true });
  }
  return json({ error: 'Not found' }, 404);
}
async function route(request: Request) {
  const url = new URL(request.url), path = url.pathname;
  if (path.startsWith('/api/sync/')) return sync(request, path);
  if (['/api/login', '/api/logout', '/api/setup/password', '/api/password'].includes(path)) {
    const response = await authRoute(request, request.method === 'POST' ? await body(request) : {});
    if (response) return response;
  }
  const session = await studentSession(request);
  if (!session) return json({ error: 'Please sign in with your student username and password.' }, 401);
  const student = await db().prepare('SELECT * FROM portal_students WHERE id=? AND active=1').bind(session.student_id).first<Obj>();
  if (!student) return json({ error: 'Student account unavailable. Contact the administrator.' }, 401);
  const sid = student.id, snapshot = JSON.parse(student.snapshot);
  const pref = await db().prepare('SELECT value FROM portal_preferences WHERE student_id=?').bind(sid).first<{ value: string }>();
  const preferences = pref ? JSON.parse(pref.value) : (snapshot.preferences || {});
  if (request.method === 'GET' && path === '/api/me') return json({ student_id: sid, username: session.username, name: student.name, email: snapshot.profile?.email || '', preferences, csrf: session.csrf, must_change_password: !!session.must_change_password, synced_at: student.synced_at });
  if (session.must_change_password) return json({ error: 'Set your new password before entering the student portal.', must_change_password: true }, 403);
  const result = await db().prepare('SELECT * FROM portal_actions WHERE student_id=? ORDER BY created_at DESC LIMIT 1000').bind(sid).all<Obj>();
  const queued = result.results.filter(a => a.status === 'queued');
  const read = result.results.filter(a => a.kind === 'notifications/read' && a.status !== 'rejected');
  const readIds = new Set(read.map(a => JSON.parse(a.payload).id).filter(Boolean).map(String));
  const readAll = read.filter(a => !JSON.parse(a.payload).id).map(a => a.created_at).sort().pop();
  const notifications = (snapshot.notifications || []).map((n: Obj) => ({ ...n, is_read: n.is_read || readIds.has(String(n.id)) || (readAll && new Date(n.created_at.replace(' ', 'T') + 'Z') <= new Date(readAll)) }));
  const violations = (snapshot.violations || []).map((v: Obj) => {
    const pending = queued.some(a => a.kind === 'appeals' && String(JSON.parse(a.payload).violation_id) === String(v.id));
    const deadline = v.appeal_deadline && new Date(v.appeal_deadline.replace(' ', 'T') + 'Z').getTime();
    return { ...v, can_appeal: v.can_appeal && !pending && (!deadline || deadline > Date.now()), appeal_eligibility_reason: pending ? 'Appeal saved; awaiting campus sync.' : v.appeal_eligibility_reason };
  });
  if (request.method === 'GET' && path === '/api/page') {
    const page = url.searchParams.get('page') || 'dashboard';
    const offset = Math.max(0, Math.min(Number(url.searchParams.get('offset') || 0) || 0, 100000));
    const data: Obj = { _unread_count: notifications.filter((n: Obj) => !n.is_read).length, _synced_at: student.synced_at, _current_term: snapshot.term || {}, _strike_summary: snapshot.strikes || [], _active_suspension: snapshot.suspension || null };
    if (['dashboard', 'profile'].includes(page)) data._student = snapshot.profile;
    if (['dashboard', 'violations'].includes(page)) {
      const size = page === 'dashboard' ? 5 : 10;
      data._violations = violations.slice(offset, offset + size); data._has_more = violations.length > offset + size;
      data._violation_counts = snapshot.counts || {};
      data._pending_appeals = (snapshot.appeals || []).filter((a: Obj) => a.status === 'pending').length + queued.filter(a => a.kind === 'appeals').length;
    } else if (page === 'notifications') { data._notifications = notifications.slice(offset, offset + 10); data._has_more = notifications.length > offset + 10; }
    else if (page === 'appeals') {
      const pending = result.results.filter(a => a.kind === 'appeals' && a.status !== 'applied').map(a => ({ ...JSON.parse(a.payload), id: a.id, status: a.status === 'queued' ? 'Awaiting campus sync' : 'Campus review needed', admin_notes: a.result, submitted_at: a.created_at.replace('T', ' ').slice(0, 19) }));
      const rows = [...pending, ...(snapshot.appeals || [])]; data._appeals = rows.slice(offset, offset + 10); data._has_more = rows.length > offset + 10;
    } else if (!['profile', 'settings', 'report'].includes(page)) return json({ error: 'Unknown page' }, 400);
    return json(data);
  }
  if (request.method === 'GET' && path === '/api/evidence') {
    const id = url.searchParams.get('id') || '';
    if (!violations.some((v: Obj) => String(v.id) === id)) return json({ error: 'Record not found.' }, 404);
    const file = await bucket().get(originalKey(sid, id));
    if (!file) return json({ error: 'Picture has not synced yet.' }, 404);
    return new Response(file.body, { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff' } });
  }
  if (request.method !== 'POST') return json({ error: 'Not found' }, 404);
  if (!csrfValid(request, session)) return json({ error: 'Request origin rejected.' }, 403);
  const payload = await body(request), id = crypto.randomUUID();
  if (path === '/api/preferences') {
    if (Object.entries(payload).some(([k, v]) => !['dark_mode', 'compact_sidebar'].includes(k) || typeof v !== 'boolean')) throw Error('Invalid preference.');
    const value = { ...preferences, ...payload };
    await db().prepare('INSERT INTO portal_preferences (student_id,value) VALUES (?,?) ON CONFLICT(student_id) DO UPDATE SET value=excluded.value').bind(sid, JSON.stringify(value)).run();
    return json(value);
  }
  let key: string | null = null, dedupe: string | null = null;
  if (path === '/api/appeals') {
    const v = violations.find((v: Obj) => String(v.id) === String(payload.violation_id));
    if (!v?.can_appeal) throw Error('Appeal unavailable.');
    if (typeof payload.reason !== 'string' || payload.reason.trim().length < 20 || payload.reason.length > 1000) throw Error('Explanation required.');
    const bytes = picture(payload.file, payload.filename);
    key = `appeals/${id}`; dedupe = `appeal:${sid}:${v.id}`;
    await bucket().put(key, bytes); payload.evidence_key = key; delete payload.file;
  } else if (path === '/api/profile') {
    if (typeof payload.name !== 'string' || !payload.name.trim() || payload.name.length > 120) throw Error('Name required.');
  } else if (path === '/api/notifications/read') {
    if (payload.id && !notifications.some((n: Obj) => Number(n.id) === Number(payload.id))) throw Error('Notification not found.');
  } else if (path === '/api/reports') {
    if (!['System Bug', 'Account Issue', 'Violation Dispute', 'Other'].includes(payload.category) || typeof payload.title !== 'string' || !payload.title.trim() || payload.title.length > 200 || typeof payload.description !== 'string' || !payload.description.trim() || payload.description.length > 10000) throw Error('Valid report required.');
  } else return json({ error: 'Not found' }, 404);
  const count = await db().prepare("SELECT COUNT(*) AS n FROM portal_actions WHERE student_id=? AND status='queued'").bind(sid).first<{ n: number }>();
  if ((count?.n || 0) >= 100) { if (key) await bucket().delete(key); return json({ error: 'Wait for pending requests to sync.' }, 429); }
  try { await db().prepare('INSERT INTO portal_actions (id,student_id,kind,payload,created_at,dedupe) VALUES (?,?,?,?,?,?)').bind(id, sid, path.slice(5), JSON.stringify(payload), now(), dedupe).run(); }
  catch (error) { if (key) await bucket().delete(key); throw error; }
  return json({ id, queued: true }, 201);
}
export async function GET(request: Request) { try { return await route(request); } catch { return json({ error: 'Unable to load records. Please try again.' }, 500); } }
export async function POST(request: Request) { try { return await route(request); } catch { return json({ error: 'Request could not be saved. Refresh the record and try again.' }, 400); } }
