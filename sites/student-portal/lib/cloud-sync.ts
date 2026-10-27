/** Protocol v2: a registered writer, immutable evidence, atomic publication + ack. */
import { env } from 'cloudflare:workers';
import { timingSafeEqual, createHash } from 'node:crypto';
import { recoveryMailSync } from './recovery-mail';
import { validHash } from './student-auth';
type Obj = Record<string, any>;
const db = () => env.DB!;
const bucket = () => env.BUCKET!;
const json = (value: unknown, status = 200) => Response.json(value, {status, headers: {'Cache-Control': 'private, no-store'}});
const hash = (value: Uint8Array) => createHash('sha256').update(value).digest('hex');
const recordIdentity = (sid: string, v: Obj) => v.identity || hash(new TextEncoder().encode(JSON.stringify([v.id, sid, v.timestamp ?? null, v.violation_code ?? null])));
export const evidenceKey = (sid: string, id: string, digest: string) => `original-v2/${encodeURIComponent(sid)}/${encodeURIComponent(id)}/${digest}`;
export async function cloudSync(request: Request, path: string, readBody: (r: Request) => Promise<Obj>) {
  const secret = (env as unknown as {SYNC_KEY?: string}).SYNC_KEY;
  const token = Buffer.from(request.headers.get('Authorization') || '');
  const expected = Buffer.from(`Bearer ${secret}`);
  if (!secret || token.length !== expected.length || !timingSafeEqual(token, expected)) return json({error: 'Unauthorized'}, 401);
  const control = await db().prepare('SELECT * FROM campus_sync_control WHERE singleton=1').first<Obj>();
  if (!control || control.protocol !== 2 || request.headers.get('X-CBVMS-Protocol') !== '2' ||
      request.headers.get('X-CBVMS-Source') !== control.source_id || request.headers.get('X-CBVMS-Writer') !== control.writer_id)
    return json({error: 'Sync paused: source registration or protocol mismatch.'}, 409);
  if (path.startsWith('/api/sync/mail/')) return recoveryMailSync(request, path);
  if (path === '/api/sync/audit' && request.method === 'GET') {
    const ids = await db().prepare('SELECT id,snapshot FROM portal_students ORDER BY id').all<Obj>();
    const records = ids.results.flatMap(s => (JSON.parse(s.snapshot).violations || []).map((v: Obj) => ({id: v.id, identity: recordIdentity(s.id, v)})));
    const accounts = await db().prepare('SELECT student_id,password_hash,credential_version,pending_action_id FROM portal_accounts').all<Obj>();
    const credentials = accounts.results.map(a => ({id: a.student_id, fingerprint: hash(new TextEncoder().encode(a.password_hash)), version: a.credential_version, pending: !!a.pending_action_id}));
    const profiles = ids.results.map(s => ({id: s.id, name: hash(new TextEncoder().encode(JSON.parse(s.snapshot).profile?.name || ''))}));
    let evidence_missing = 0, evidence_mismatched = 0;
    for (const s of ids.results) for (const v of JSON.parse(s.snapshot).violations || []) {
      if (!v.has_snapshot) continue;
      const key = v.evidence_sha256 ? evidenceKey(s.id, String(v.id), v.evidence_sha256) : `original/${encodeURIComponent(s.id)}/${encodeURIComponent(v.id)}`;
      const file = await bucket().get(key);
      if (!file) evidence_missing++;
      else if (v.evidence_sha256 && hash(new Uint8Array(await file.arrayBuffer())) !== v.evidence_sha256) evidence_mismatched++;
    }
    const queue = await db().prepare('SELECT kind,status,COUNT(*) AS count,MIN(created_at) AS oldest FROM portal_actions GROUP BY kind,status').all<Obj>();
    const queued = await db().prepare("SELECT id FROM portal_actions WHERE status='queued'").all<{id: string}>();
    return json({...control, records, credentials, profiles, evidence_missing, evidence_mismatched, ids: ids.results.map(r => r.id), queue: queue.results.map(q => ({...q, oldest_age_seconds: Math.max(0, Math.floor((Date.now()-Date.parse(q.oldest))/1000))})), queued_ids: queued.results.map(r => r.id), checked_at: new Date().toISOString()});
  }
  if (path === '/api/sync/actions' && request.method === 'GET') {
    const after = Number(new URL(request.url).searchParams.get('after') || 0);
    if (!Number.isSafeInteger(after) || after < 0) return json({error: 'Invalid cursor'}, 400);
    const rows = await db().prepare("SELECT rowid AS sequence,* FROM portal_actions WHERE status='queued' AND rowid>? ORDER BY rowid LIMIT 26").bind(after).all<Obj>();
    const actions = [];
    for (const row of rows.results.slice(0, 25)) {
      try {
        const payload = JSON.parse(row.payload);
        if (payload.evidence_key) {
          if (payload.evidence_key !== `appeals/${row.id}`) throw Error('Ownership mismatch');
          const file = await bucket().get(payload.evidence_key);
          if (!file) throw Error('Missing evidence');
          const bytes = new Uint8Array(await file.arrayBuffer());
          if (payload.evidence_sha256 && hash(bytes) !== payload.evidence_sha256) throw Error('Checksum mismatch');
          payload.file = Buffer.from(bytes).toString('base64');
        }
        actions.push({...row, payload});
      } catch { actions.push({id: row.id, student_id: row.student_id, kind: row.kind, sequence: row.sequence, error: 'Submission requires evidence or payload repair.'}); }
    }
    const pending = await db().prepare("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM portal_actions WHERE status='queued'").first<Obj>();
    return json({actions, has_more: rows.results.length > 25, pending: pending?.n, oldest_pending: pending?.oldest});
  }
  if (request.method !== 'POST') return json({error: 'Method not allowed'}, 405);
  // Removed v1 endpoints cannot bypass registration or publication fencing.
  if (!['/api/sync/evidence', '/api/sync/publish'].includes(path)) return json({error: 'Upgrade the campus client to protocol 2.'}, 409);
  const data = await readBody(request);
  if (path === '/api/sync/evidence') {
    if (data.generation !== control.generation) return json({error: 'Stale evidence writer'}, 409);
    if (typeof data.student_id !== 'string' || !Number.isSafeInteger(data.violation_id) || !/^[0-9a-f]{64}$/.test(data.sha256)) throw Error('Invalid evidence identity');
    const bytes = new Uint8Array(Buffer.from(data.file, 'base64'));
    if (!bytes.length || bytes.length > 10 * 1024 * 1024 || hash(bytes) !== data.sha256) throw Error('Invalid evidence checksum');
    const key = evidenceKey(data.student_id, String(data.violation_id), data.sha256);
    const previous = await bucket().get(key);
    if (!previous || hash(new Uint8Array(await previous.arrayBuffer())) !== data.sha256)
      await bucket().put(key, bytes, {sha256: data.sha256, httpMetadata: {contentType: 'image/jpeg'}});
    const verified = await bucket().get(key);
    if (!verified || hash(new Uint8Array(await verified.arrayBuffer())) !== data.sha256) throw Error('Evidence verification failed');
    return json({sha256: data.sha256});
  }
  if (!Number.isSafeInteger(data.generation) || data.generation !== control.generation || typeof data.run_id !== 'string' || !Array.isArray(data.students) || !Array.isArray(data.receipts)) return json({error: 'Stale publication'}, 409);
  const existing = await db().prepare('SELECT id,snapshot,active FROM portal_students').all<Obj>();
  const rosterBaseline = control.published_student_count ?? existing.results.length;
  if (!data.students.length || data.students.length < rosterBaseline * .8) return json({error: 'Roster reduction requires reconciliation'}, 409);
  if (new Set(data.students.map((s: Obj) => s.id)).size !== data.students.length) throw Error('Duplicate students');
  const old = new Map(existing.results.map(s => [s.id, s]));
  const identities = new Map(existing.results.flatMap(s => (JSON.parse(s.snapshot).violations || []).map((v: Obj) => [v.id, recordIdentity(s.id, v)] as const)));
  const statements = [db().prepare('INSERT INTO campus_sync_publications (generation,run_id) VALUES (?,?)').bind(data.generation + 1, data.run_id), db().prepare('UPDATE campus_sync_control SET generation=generation+1,run_id=?,published_student_count=? WHERE singleton=1 AND generation=?').bind(data.run_id, data.students.length, data.generation)];
  const guard = 'EXISTS (SELECT 1 FROM campus_sync_control WHERE singleton=1 AND generation=? AND run_id=?)';
  const fence = [data.generation + 1, data.run_id];
  for (const s of data.students) {
    if (typeof s.id !== 'string' || typeof s.name !== 'string' || typeof s.email !== 'string' || !s.snapshot || !s.account || !validHash(s.account.password_hash) || !Number.isSafeInteger(s.account.credential_version)) throw Error('Incomplete student publication');
    // Deactivation requires a separate reviewed administrative operation; neither
    // absence nor a pending registration flag may revoke a published account.
    if (old.get(s.id)?.active && !s.active) throw Error('Explicit deactivation review required');
    const previous = old.get(s.id) ? JSON.parse(old.get(s.id)!.snapshot) : null;
    for (const v of s.snapshot.violations || []) {
      const prior = previous?.violations?.find((x: Obj) => x.id === v.id);
      if (identities.has(v.id) && identities.get(v.id) !== v.identity) throw Error('Local integer-ID collision');
      if (v.has_snapshot) {
        if (!/^[0-9a-f]{64}$/.test(v.evidence_sha256)) throw Error('Unverified evidence');
        const file = await bucket().get(evidenceKey(s.id, String(v.id), v.evidence_sha256));
        if (!file || hash(new Uint8Array(await file.arrayBuffer())) !== v.evidence_sha256) throw Error('Missing evidence');
      }
    }
    const account = await db().prepare('SELECT * FROM portal_accounts WHERE student_id=?').bind(s.id).first<Obj>();
    if (account && (s.account.credential_version < account.credential_version ||
        (s.account.credential_version === account.credential_version && ![account.password_hash, account.pending_base_hash].includes(s.account.password_hash)))) throw Error('Campus credential revision conflict');
    if (s.snapshot.schema_version !== 3 || control.minimum_snapshot_version !== 3 || !Array.isArray(s.snapshot.academic_terms) || !s.snapshot.term_history) throw Error('Upgrade desktop: academic snapshot version 3 required');
    const snapshot = JSON.stringify(s.snapshot);
    if (snapshot.length > 1500000 || /"(?:password_hash|encoding|profile_photo|photo|capture_id|capture_hashes|provenance)"\s*:/.test(snapshot)) throw Error('Unsafe snapshot');
    statements.push(db().prepare(`INSERT INTO portal_students (id,email,name,snapshot,synced_at,active) SELECT ?,?,?,?,?,? WHERE ${guard} ON CONFLICT(id) DO UPDATE SET email=excluded.email,name=excluded.name,snapshot=excluded.snapshot,synced_at=excluded.synced_at`).bind(s.id, s.email, s.name, snapshot, new Date().toISOString(), s.active ? 1 : 0, ...fence));
    // A single SQL statement preserves concurrent student password edits. An
    // administrative reset (newer campus revision) supersedes pending changes.
    statements.push(db().prepare(`INSERT INTO portal_accounts (student_id,username,password_hash,must_change_password,credential_version) SELECT ?,?,?,?,? WHERE ${guard}
      ON CONFLICT(student_id) DO UPDATE SET username=excluded.username,
      password_hash=CASE WHEN pending_action_id IS NOT NULL AND pending_base_hash=excluded.password_hash AND portal_accounts.credential_version=excluded.credential_version THEN portal_accounts.password_hash ELSE excluded.password_hash END,
      must_change_password=CASE WHEN pending_action_id IS NOT NULL AND pending_base_hash=excluded.password_hash AND portal_accounts.credential_version=excluded.credential_version THEN portal_accounts.must_change_password ELSE excluded.must_change_password END,
      pending_base_hash=CASE WHEN pending_base_hash=excluded.password_hash AND portal_accounts.credential_version=excluded.credential_version THEN pending_base_hash ELSE NULL END,
      pending_action_id=CASE WHEN pending_base_hash=excluded.password_hash AND portal_accounts.credential_version=excluded.credential_version THEN pending_action_id ELSE NULL END,
      credential_version=excluded.credential_version WHERE excluded.credential_version>=portal_accounts.credential_version`).bind(s.id, s.account.username, s.account.password_hash, s.account.must_change_password ? 1 : 0, s.account.credential_version, ...fence));
  }
  for (const receipt of data.receipts) {
    if (!['applied', 'rejected'].includes(receipt.status)) throw Error('Invalid receipt');
    const action = await db().prepare('SELECT student_id,kind FROM portal_actions WHERE id=?').bind(receipt.id).first<Obj>();
    if (!action || !data.students.some((s: Obj) => s.id === action.student_id)) throw Error('Receipt without student snapshot');
    if (receipt.status === 'rejected' && action.kind === 'password') {
      const account = data.students.find((s: Obj) => s.id === action.student_id).account;
      statements.push(db().prepare(`UPDATE portal_accounts SET password_hash=?,must_change_password=?,pending_base_hash=NULL,pending_action_id=NULL WHERE pending_action_id=? AND credential_version<=? AND ${guard}`).bind(account.password_hash, account.must_change_password ? 1 : 0, receipt.id, account.credential_version, ...fence));
    }
    statements.push(db().prepare(`UPDATE portal_actions SET status=?,result=? WHERE id=? AND status='queued' AND ${guard}`).bind(receipt.status, receipt.status === 'applied' ? 'Saved to campus records.' : 'Campus review required.', receipt.id, ...fence));
  }
  const result = await db().batch(statements);
  if (!result[1].meta.changes) return json({error: 'Stale publication'}, 409);
  return json({generation: data.generation + 1, run_id: data.run_id});
}
