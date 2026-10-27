import { env } from 'cloudflare:workers';
import { authRoute, studentSession, csrfValid } from '../../../lib/student-auth';
import { createHash } from 'node:crypto';
import { incrementalSync } from '../../../lib/incremental-sync';
import { cloudSync, evidenceKey } from '../../../lib/cloud-sync';

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
async function route(request: Request) {
  const url = new URL(request.url), path = url.pathname;
  if (path.startsWith('/api/campus/')) return incrementalSync(request);
  if (path.startsWith('/api/sync/')) return cloudSync(request, path, body);
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
    const pending = queued.some(a => { if (a.kind !== 'appeals') return false; const p = JSON.parse(a.payload); return String(p.portal_violation_id ?? p.violation_id) === String(v.id); });
    const deadline = v.appeal_deadline && new Date(v.appeal_deadline.replace(' ', 'T') + 'Z').getTime();
    return { ...v, can_appeal: v.can_appeal && !pending && (!deadline || deadline > Date.now()), appeal_eligibility_reason: pending ? 'Appeal saved; awaiting campus sync.' : v.appeal_eligibility_reason };
  });
  if (request.method === 'GET' && path === '/api/page') {
    const page = url.searchParams.get('page') || 'dashboard';
    const selectedTerm = url.searchParams.get('semester_id') || '';
    const termMatches = (id: unknown) => !selectedTerm || (selectedTerm === 'legacy' ? id == null || (snapshot.academic_terms || []).some((t: Obj) => t.semester_code === 'legacy' && String(t.id) === String(id)) : String(id) === selectedTerm);
    const scopedViolations = violations.filter((v: Obj) => termMatches(v.semester_id));
    const offset = Math.max(0, Math.min(Number(url.searchParams.get('offset') || 0) || 0, 100000));
    const data: Obj = { _academic_terms: snapshot.academic_terms || [], _term_history: snapshot.term_history || {}, _enrollment_status: snapshot.enrollment_status || 'Enrollment unverified — legacy record', _suspension_history: snapshot.suspension_history || [], _unread_count: notifications.filter((n: Obj) => !n.is_read).length, _synced_at: student.synced_at, _current_term: snapshot.term || {}, _strike_summary: snapshot.strikes || [], _active_suspension: snapshot.suspension || null };
    if (selectedTerm) {
      data._term_history = {...data._term_history, enrollments: (data._term_history.enrollments || []).filter((e: Obj) => termMatches(e.semester_id)), strikes_by_term: (data._term_history.strikes_by_term || []).filter((e: Obj) => termMatches(e.semester_id)), enrollment_reviews: (data._term_history.enrollment_reviews || []).filter((e: Obj) => termMatches(e.semester_id))};
      data._suspension_history = data._suspension_history.filter((s: Obj) => termMatches(violations.find((v: Obj) => v.id === s.violation_id)?.semester_id));
    }
    if (['dashboard', 'profile'].includes(page)) data._student = snapshot.profile;
    if (['dashboard', 'violations'].includes(page)) {
      const size = page === 'dashboard' ? 5 : 10;
      data._violations = scopedViolations.slice(offset, offset + size); data._has_more = scopedViolations.length > offset + size;
      data._violation_counts = {pending: scopedViolations.filter((v: Obj) => ['pending_review','unreviewed'].includes(v.status)).length, resolved: scopedViolations.filter((v: Obj) => ['dismissed','resolved'].includes(v.status) || v.appeal_status === 'approved').length, confirmed: scopedViolations.filter((v: Obj) => !['pending_review','unreviewed','dismissed','resolved'].includes(v.status) && v.appeal_status !== 'approved').length};
      data._pending_appeals = (snapshot.appeals || []).filter((a: Obj) => a.status === 'pending').length + queued.filter(a => a.kind === 'appeals').length;
    } else if (page === 'notifications') { data._notifications = notifications.slice(offset, offset + 10); data._has_more = notifications.length > offset + 10; }
    else if (page === 'appeals') {
      const pending = result.results.filter(a => a.kind === 'appeals' && a.status !== 'applied').map(a => ({ ...JSON.parse(a.payload), id: a.id, status: a.status === 'queued' ? 'Awaiting campus sync' : 'Campus review needed', admin_notes: a.result, submitted_at: a.created_at.replace('T', ' ').slice(0, 19) }));
      const rows = [...pending, ...(snapshot.appeals || [])].filter((a: Obj) => !selectedTerm || violations.some((v: Obj) => String(v.id) === String(a.portal_violation_id ?? a.violation_id) && termMatches(v.semester_id))); data._appeals = rows.slice(offset, offset + 10); data._has_more = rows.length > offset + 10;
    } else if (!['profile', 'settings', 'report'].includes(page)) return json({ error: 'Unknown page' }, 400);
    return json(data);
  }
  if (request.method === 'GET' && path === '/api/evidence') {
    const id = url.searchParams.get('id') || '';
    if (!violations.some((v: Obj) => String(v.id) === id)) return json({ error: 'Record not found.' }, 404);
    const violation = violations.find((v: Obj) => String(v.id) === id);
    const file = await bucket().get(violation.evidence_sha256 ? evidenceKey(sid, id, violation.evidence_sha256) : originalKey(sid, id));
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
    payload.violation_identity = v.identity;
    // Administrative imports retain the campus case identity while allocating a
    // separate portal key. Capture both from the owned snapshot, never the form.
    if (v.campus_record_id !== undefined) {
      if (!Number.isSafeInteger(v.campus_record_id) || v.campus_record_id <= 0) throw Error('Invalid campus case mapping.');
      payload.portal_violation_id = v.id;
      payload.violation_id = v.campus_record_id;
      payload.campus_violation_id = v.campus_record_id;
    } else {
      delete payload.portal_violation_id;
      delete payload.campus_violation_id;
    }
    payload.evidence_sha256 = createHash('sha256').update(bytes).digest('hex');
    key = `appeals/${id}`; dedupe = `appeal:${sid}:${v.id}`;
    await bucket().put(key, bytes); payload.evidence_key = key; delete payload.file;
  } else if (path === '/api/profile') {
    payload.expected_name = snapshot.profile?.name;
    if (typeof payload.name !== 'string' || !payload.name.trim() || payload.name.length > 120) throw Error('Name required.');
  } else if (path === '/api/notifications/read') {
    if (payload.id && !notifications.some((n: Obj) => Number(n.id) === Number(payload.id))) throw Error('Notification not found.');
  } else if (path === '/api/reports') {
    if (!['System Bug', 'Account Issue', 'Violation Dispute', 'Other'].includes(payload.category) || typeof payload.title !== 'string' || !payload.title.trim() || payload.title.length > 200 || typeof payload.description !== 'string' || !payload.description.trim() || payload.description.length > 10000) throw Error('Valid report required.');
  } else return json({ error: 'Not found' }, 404);
  const count = await db().prepare("SELECT COUNT(*) AS n FROM portal_actions WHERE student_id=? AND status='queued'").bind(sid).first<{ n: number }>();
  if ((count?.n || 0) >= 100) { if (key) await bucket().delete(key); return json({ error: 'Wait for pending requests to sync.' }, 429); }
  try { await db().prepare(`INSERT INTO portal_actions (id,student_id,kind,payload,created_at,dedupe) VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?)`).bind(id, sid, path.slice(5), JSON.stringify(payload), dedupe).run(); }
  catch (error) { if (key) await bucket().delete(key); throw error; }
  return json({ id, queued: true }, 201);
}
export async function GET(request: Request) { try { return await route(request); } catch { return json({ error: 'Unable to load records. Please try again.' }, 500); } }
export async function POST(request: Request) { try { return await route(request); } catch { return json({ error: 'Request could not be saved. Refresh the record and try again.' }, 400); } }
