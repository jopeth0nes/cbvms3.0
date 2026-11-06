/** Create-only campus registration. Existing cloud identities are never replaced. */
import { createHash, timingSafeEqual } from 'node:crypto';

const json = (data: unknown, status = 200) => Response.json(data, {status,
  headers: {'Cache-Control': 'private, no-store'}});
const fields = ['student_id', 'name', 'email', 'college_department', 'course',
  'report_year_level', 'report_section', 'student_status'];

export async function campusRegistration(request: Request, env: {DB: D1Database; CAMPUS_REGISTRATION_KEY?: string}) {
  const supplied = Buffer.from(request.headers.get('Authorization') || '');
  const expected = Buffer.from(`Bearer ${env.CAMPUS_REGISTRATION_KEY}`);
  if (!env.CAMPUS_REGISTRATION_KEY || supplied.length !== expected.length || !timingSafeEqual(supplied, expected))
    return json({error: 'Unauthorized'}, 401);
  if (request.method !== 'POST') return json({error: 'Method not allowed'}, 405);
  try {
    const reader = request.body?.getReader();
    if (!reader) return json({error: 'JSON object required'}, 400);
    const chunks: Uint8Array[] = []; let size = 0;
    while (true) {
      const {done, value} = await reader.read(); if (done) break;
      size += value.length;
      if (size > 16384) { await reader.cancel(); return json({error: 'Request too large'}, 413); }
      chunks.push(value);
    }
    const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!data || Array.isArray(data) || Object.keys(data).some(k => !['profile','username','password_hash','credential_version','must_change_password','source_id','lifecycle_version','active'].includes(k)))
      return json({error: 'Invalid registration'}, 400);
    if (typeof data.username !== 'string' || !data.username.trim() || data.username.length > 200 ||
        typeof data.password_hash !== 'string' || !/^pbkdf2_sha256\$v1\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}$/.test(data.password_hash) ||
        !Number.isSafeInteger(data.credential_version) || data.credential_version < 0 || typeof data.must_change_password !== 'boolean' ||
        typeof data.source_id !== 'string' || !/^[0-9a-f-]{36}$/.test(data.source_id) ||
        !Number.isSafeInteger(data.lifecycle_version) || data.lifecycle_version < 0 || typeof data.active !== 'boolean' ||
        !data.profile || Array.isArray(data.profile) || Object.keys(data.profile).some(k => !fields.includes(k)))
      return json({error: 'Invalid registration'}, 400);
    const profile: Record<string, string> = {};
    for (const key of fields) {
      const value = data.profile[key] ?? '';
      if (typeof value !== 'string' || value.length > 300 || /[\x00-\x1f]/.test(value)) return json({error: 'Invalid profile'}, 400);
      profile[key] = value.trim();
    }
    if (!profile.student_id || !profile.name) return json({error: 'Student identity required'}, 400);
    const owner = await env.DB.prepare('SELECT source_id,lifecycle_version,active,access_blocked FROM campus_registration_owners WHERE student_id=?').bind(profile.student_id).first<any>();
    const existing = await env.DB.prepare(`SELECT a.student_id,a.username,a.password_hash,p.active
      FROM portal_accounts a JOIN portal_students p ON p.id=a.student_id
      WHERE a.student_id=? OR a.username=?`).bind(profile.student_id, data.username.trim()).all<any>();
    if (existing.results.length) {
      if (owner?.source_id === data.source_id && existing.results.length === 1 &&
          existing.results[0].student_id === profile.student_id && existing.results[0].username === data.username.trim()) {
        if (data.lifecycle_version < owner.lifecycle_version) return json({error: 'Stale registration state'}, 409);
        if (data.lifecycle_version === owner.lifecycle_version && Number(data.active) !== owner.active)
          return json({error: 'Lifecycle version conflict'}, 409);
        if (data.lifecycle_version > owner.lifecycle_version) {
          const blocked = owner.access_blocked || (owner.active === 1 && existing.results[0].active === 0);
          await env.DB.batch([
            env.DB.prepare(`UPDATE portal_students SET active=? WHERE id=? AND EXISTS(
              SELECT 1 FROM campus_registration_owners WHERE student_id=? AND source_id=? AND lifecycle_version=?)`)
              .bind(data.active && !blocked ? 1 : 0, profile.student_id, profile.student_id, data.source_id, owner.lifecycle_version),
            env.DB.prepare(`DELETE FROM portal_sessions WHERE student_id=? AND EXISTS(
              SELECT 1 FROM campus_registration_owners WHERE student_id=? AND source_id=? AND lifecycle_version=?)`)
              .bind(profile.student_id, profile.student_id, data.source_id, owner.lifecycle_version),
            env.DB.prepare('UPDATE campus_registration_owners SET lifecycle_version=?,active=?,access_blocked=? WHERE student_id=? AND source_id=? AND lifecycle_version=?')
              .bind(data.lifecycle_version, Number(data.active), Number(blocked), profile.student_id, data.source_id, owner.lifecycle_version),
          ]);
        }
        return json({status: 'registered', password_preserved: true});
      }
      return json({status: 'existing_account', password_preserved: true});
    }
    if (await env.DB.prepare('SELECT 1 FROM portal_students WHERE id=?').bind(profile.student_id).first())
      return json({status: 'existing_account', password_preserved: true});
    if (!data.active) return json({status: 'not_published'});
    const snapshot = JSON.stringify({schema_version: 3, profile, academic_terms: [], term_history: {},
      enrollment_status: 'Registration received — academic records await campus sync',
      violations: [], notifications: [], appeals: [], suspension_history: [], preferences: {}});
    const email = createHash('sha256').update(profile.student_id).digest('hex') + '@cbvms.invalid';
    await env.DB.batch([
      env.DB.prepare('INSERT INTO portal_students(id,email,name,snapshot,synced_at,active) VALUES(?,?,?,?,?,1)')
        .bind(profile.student_id, email, profile.name, snapshot, new Date().toISOString()),
      env.DB.prepare('INSERT INTO portal_accounts(student_id,username,password_hash,must_change_password,credential_version) VALUES(?,?,?,?,?)')
        .bind(profile.student_id, data.username.trim(), data.password_hash, data.must_change_password ? 1 : 0, data.credential_version),
      env.DB.prepare('INSERT INTO campus_registration_owners(student_id,source_id,lifecycle_version,active) VALUES(?,?,?,1)')
        .bind(profile.student_id, data.source_id, data.lifecycle_version),
    ]);
    return json({status: 'registered'}, 201);
  } catch {
    return json({error: 'Registration not completed; retry or contact campus administrator'}, 409);
  }
}
