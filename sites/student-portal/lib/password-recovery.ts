import { env } from 'cloudflare:workers';
import { createHmac, randomBytes, randomInt, createHash } from 'node:crypto';
import { csrfValid, studentSession, verifyPassword, hashPassword, passwordValid } from './student-auth';
import { recoveryAvailable, recoveryConfig, sealCode, validEmail } from './recovery-mail';

type Row = Record<string, any>;
const db = () => env.DB!;
const token = () => randomBytes(32).toString('hex');
const digest = (value: string) => createHash('sha256').update(value).digest('hex');
const mac = (...parts: string[]) => createHmac('sha256', recoveryConfig().RECOVERY_HMAC_KEY!).update(JSON.stringify(parts)).digest('hex');
const json = (data: unknown, status = 200) => Response.json(data, {status, headers: {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'}});
const invalid = () => json({error: 'Code or recovery link is invalid, expired, or no longer available. Request a new code or contact your campus administrator.'}, 400);
const limited = () => json({error: 'Too many recovery attempts. Try again later or contact your campus administrator.'}, 429);
const neutral = (id: string) => json({message: 'If this account is eligible for recovery, a code will be sent to its registered email.', challenge_id: id, resend_after: 60, expires_in: 600});
const idValid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
// A full signed-in session may verify only its preexisting, unambiguous contact.
// Changing to a requester-chosen address continues to require the current password.
async function registeredContact(studentId: string): Promise<string | null> {
  const row = await db().prepare("SELECT json_extract(snapshot,'$.profile.email') email FROM portal_students WHERE id=? AND active=1").bind(studentId).first<Row>();
  const email = typeof row?.email === 'string' ? row.email.trim().toLowerCase() : '';
  if (!validEmail(email)) return null;
  const matches = await db().prepare("SELECT COUNT(*) n FROM portal_students WHERE lower(trim(COALESCE(json_extract(snapshot,'$.profile.email'),'')))=?").bind(email).first<Row>();
  const conflict = await db().prepare('SELECT student_id FROM portal_recovery_emails WHERE email=? AND student_id<>?').bind(email,studentId).first();
  return matches?.n === 1 && !conflict ? email : null;
}
const assisted = `EXISTS(SELECT 1 FROM portal_recovery_assisted_challenges ac
 JOIN portal_recovery_assistance ap ON ap.id=ac.approval_id
 WHERE ac.challenge_id=portal_recovery_challenges.id AND ap.student_id=a.student_id
 AND ap.email=e.email AND ap.email_version=e.version AND ap.credential_version=a.credential_version
 AND ap.consumed_at IS NULL AND ap.expires_at>CAST(strftime('%s','now') AS INTEGER)*1000)`;
// Rechecked inside every state transition, not merely when a code is requested.
const eligible = `EXISTS (SELECT 1 FROM portal_accounts a JOIN portal_students s ON s.id=a.student_id
 JOIN portal_recovery_emails e ON e.student_id=a.student_id
 WHERE a.student_id=portal_recovery_challenges.student_id AND s.active=1
 AND NOT EXISTS(SELECT 1 FROM portal_student_lifecycle l WHERE l.student_id=s.id AND (l.recycled=1 OR l.access_blocked=1))
 AND a.password_hash=portal_recovery_challenges.credential_hash AND a.credential_version=portal_recovery_challenges.credential_version
 AND NOT EXISTS(SELECT 1 FROM portal_recovery_contact_proofs cp WHERE cp.challenge_id=portal_recovery_challenges.id
   AND (lower(trim(COALESCE(json_extract(s.snapshot,'$.profile.email'),'')))<>cp.contact_email
     OR (SELECT COUNT(*) FROM portal_students other WHERE lower(trim(COALESCE(json_extract(other.snapshot,'$.profile.email'),'')))=cp.contact_email)<>1))
 AND e.version=portal_recovery_challenges.email_version
 AND (portal_recovery_challenges.purpose='email' OR ((e.verified_at IS NOT NULL OR ${assisted}) AND e.email=portal_recovery_challenges.email AND a.pending_action_id IS NULL)))`;

async function budget(key: string, count: number, milliseconds: number) {
  const now = Date.now();
  const result = await db().prepare(`INSERT INTO portal_recovery_limits(id,key,at) SELECT ?,?,?
    WHERE (SELECT COUNT(*) FROM portal_recovery_limits WHERE key=? AND at>?)<?`)
    .bind(token(), key, now, key, now - milliseconds, count).run();
  return !!result.meta.changes;
}
async function readBody(request: Request): Promise<Row | null> {
  if (!(request.headers.get('Content-Type') || '').startsWith('application/json')) return null;
  // Stream-bound before allocation; Content-Length is not trusted.
  const reader = request.body?.getReader();
  if (!reader) return null;
  let bytes = 0, raw = '';
  const decoder = new TextDecoder();
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > 8192) { await reader.cancel(); return null; }
    raw += decoder.decode(value, {stream: true});
  }
  try { const data = JSON.parse(raw + decoder.decode()); return data && typeof data === 'object' && !Array.isArray(data) ? data : null; }
  catch { return null; }
}
export async function recoveryRoute(request: Request): Promise<Response> {
  const path = new URL(request.url).pathname;
  const emailFlow = path.startsWith('/api/recovery/email');
  if (request.method === 'GET' && path === '/api/recovery/email') {
    const session = await studentSession(request);
    if (!session || session.must_change_password) return json({error: 'Please sign in and finish password setup.'}, 401);
    const row = await db().prepare('SELECT email,verified_at FROM portal_recovery_emails WHERE student_id=?').bind(session.student_id).first<Row>();
    return json({verified: !!row?.verified_at, email: row?.verified_at ? row.email : null, registered_email: await registeredContact(session.student_id)});
  }
  if (!['/api/recovery/request','/api/recovery/verify','/api/recovery/complete','/api/recovery/email/request','/api/recovery/email/verify'].includes(path)) return json({error:'Not found.'},404);
  if (request.method !== 'POST') return json({error: 'Method not allowed.'}, 405);
  if (request.headers.get('Origin') !== new URL(request.url).origin) return json({error: 'Request origin rejected.'}, 403);
  if (!recoveryAvailable()) return json({error: 'Recovery email service is temporarily unavailable. Contact your campus administrator for identity-verified assistance.'}, 503);
  const data = await readBody(request);
  if (!data) return json({error: 'A JSON object of at most 8 KB is required.'}, 400);
  const network = mac('network', request.headers.get('CF-Connecting-IP') || 'unknown');
  // Separate from ordinary login. Global per-network rolling limits persist in D1.
  if (!await budget('network:' + network, 100, 3600000)) return limited();
  let session: Row | null = null;
  if (emailFlow) {
    session = await studentSession(request);
    if (!session || session.must_change_password) return json({error: 'Please sign in and finish password setup.'}, 401);
    if (!csrfValid(request, session)) return json({error: 'Please refresh and try again.'}, 403);
  }
  // Expired challenge material and one-hour budgets have bounded retention.
  await db().batch([
    db().prepare('DELETE FROM portal_recovery_limits WHERE at<?').bind(Date.now() - 3600000),
    db().prepare('DELETE FROM portal_recovery_challenges WHERE expires_at<?').bind(Date.now() - 86400000),
  ]);
  if (path.endsWith('/request')) {
    const mailStatus = await db().prepare('SELECT last_seen FROM portal_recovery_mail_status WHERE singleton=1').first<Row>();
    if (!mailStatus || mailStatus.last_seen < Date.now()-120000) return json({error:'Campus recovery email service is temporarily offline. Try again later or contact your campus administrator.'},503);
    if (!await budget('request-network:' + network, 30, 3600000)) return limited();
    const start = Date.now(), id = token();
    let account: Row | null;
    let email: string | null = null;
    const useRegisteredEmail = emailFlow && data.use_registered_email === true;
    if (emailFlow && useRegisteredEmail) {
      email = await registeredContact(session!.student_id);
      if (!email) return json({error:'The saved contact address cannot be verified online. Contact your campus administrator to review it.'},400);
      account = session;
    } else if (emailFlow) {
      email = typeof data.email === 'string' ? data.email.trim().toLowerCase() : '';
      if (!validEmail(email)) return json({error: 'Enter a valid personal email address.'}, 400);
      if (!await budget('reauth:' + mac(session!.student_id), 5, 3600000)) return limited();
      if (!await verifyPassword(data.current_password, session!.password_hash)) return json({error: 'Current password is incorrect.'}, 400);
      account = session;
    } else {
      const username = typeof data.username === 'string' ? data.username.trim() : '';
      if (!username || username.length > 200) return json({error: 'Enter your username or student ID (up to 200 characters).'}, 400);
      // Resolve ambiguous username/ID aliases conservatively; never guess identity.
      const matches = await db().prepare(`SELECT a.*,s.active,e.email,e.verified_at,COALESCE(e.version,0) AS email_version,
        (SELECT ap.id FROM portal_recovery_assistance ap WHERE ap.student_id=a.student_id AND ap.email=e.email
         AND ap.email_version=e.version AND ap.credential_version=a.credential_version AND ap.consumed_at IS NULL
         AND ap.expires_at>? ORDER BY ap.created_at DESC LIMIT 1) AS recovery_approval
        FROM portal_accounts a JOIN portal_students s ON s.id=a.student_id LEFT JOIN portal_recovery_emails e ON e.student_id=a.student_id
        WHERE a.username=? OR a.student_id=?`).bind(Date.now(), username, username).all<Row>();
      account = matches.results.length === 1 ? matches.results[0] : null;
      email = (account?.verified_at || account?.recovery_approval) && validEmail(account.email) ? account.email : null;
    }
    const key = mac('account', account?.student_id || 'unknown:' + data.username);
    const purpose = emailFlow ? 'email' : 'reset';
    const allowed = await budget('request:' + key, 6, 3600000) && await budget('cooldown:' + purpose + ':' + key, 1, 60000);
    if (allowed) {
      if (emailFlow) {
        // Authenticated proof authorizes invalidation, not automatic verification.
        await db().prepare(`INSERT INTO portal_recovery_emails(student_id,version) VALUES (?,1)
          ON CONFLICT(student_id) DO UPDATE SET version=version+1`).bind(session!.student_id).run();
        const current = await db().prepare('SELECT version FROM portal_recovery_emails WHERE student_id=?').bind(session!.student_id).first<Row>();
        account = {...session, email_version: current!.version};
      }
      const code = randomInt(0, 1000000).toString().padStart(6, '0');
      const sid = account?.student_id || null;
      const result = await db().batch([
        db().prepare('UPDATE portal_recovery_challenges SET invalidated=1 WHERE account_key=? AND purpose=?').bind(key,purpose),
        db().prepare(`INSERT INTO portal_recovery_challenges
          (id,purpose,account_key,student_id,email,email_version,credential_version,credential_hash,code_digest,created_at,expires_at,delivery)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).bind(id,purpose,key,sid,email,account?.email_version || 0,account?.credential_version || 0,account?.password_hash || '',mac('code',purpose,id,code),start,start+600000,email ? 'pending' : 'none'),
        ...(useRegisteredEmail ? [db().prepare('INSERT INTO portal_recovery_contact_proofs(challenge_id,contact_email) VALUES (?,?)').bind(id,email)] : []),
        ...(!emailFlow && account?.recovery_approval ? [db().prepare('INSERT INTO portal_recovery_assisted_challenges(challenge_id,approval_id) VALUES (?,?)').bind(id,account.recovery_approval)] : []),
      ]);
      const ready = await db().prepare(`SELECT id FROM portal_recovery_challenges WHERE id=? AND ${eligible}`).bind(id).first();
      if (result[1].meta.changes && email && ready) await db().prepare(`INSERT INTO portal_recovery_mail(id,email,sealed_code,created_at,expires_at) SELECT ?,?,?,?,? WHERE EXISTS(SELECT 1 FROM portal_recovery_challenges WHERE id=? AND invalidated=0 AND ${eligible})`).bind(id,email,sealCode(id,code),start,start+600000,id).run();
    }
    // Provider work is asynchronous. Small padding reduces lookup/existence timing signals.
    await new Promise(resolve => setTimeout(resolve, Math.max(0, 250 - (Date.now() - start))));
    return emailFlow && !allowed ? limited() : neutral(id);
  }
  if (path.endsWith('/verify')) {
    const purpose = emailFlow ? 'email' : 'reset';
    if (!idValid(data.challenge_id) || typeof data.code !== 'string' || !/^\d{6}$/.test(data.code)) return invalid();
    const challenge = await db().prepare('SELECT account_key FROM portal_recovery_challenges WHERE id=?').bind(data.challenge_id).first<Row>();
    if (!await budget('verify:' + (challenge?.account_key || mac('unknown', data.challenge_id)), 10, 3600000)) return invalid();
    const grant = token(), grantHash = digest(grant), now = Date.now();
    const result = await db().prepare(`UPDATE portal_recovery_challenges SET attempts=attempts+1,
      grant_hash=CASE WHEN code_digest=? THEN ? ELSE NULL END, grant_expires=CASE WHEN code_digest=? THEN ? ELSE NULL END
      WHERE id=? AND purpose=? AND invalidated=0 AND consumed IS NULL AND grant_hash IS NULL AND attempts<5 AND expires_at>?
      AND (? IS NULL OR student_id=?) AND ${eligible} RETURNING grant_hash,email,student_id,email_version`)
      .bind(mac('code',purpose,data.challenge_id,data.code),grantHash,mac('code',purpose,data.challenge_id,data.code),now+300000,
        data.challenge_id,purpose,now,session?.student_id || null,session?.student_id || null).first<Row>();
    if (result?.grant_hash !== grantHash) return invalid();
    if (!emailFlow) return json({grant, expires_in: 300});
    const claim = token();
    try {
      const changed = await db().batch([
        db().prepare(`UPDATE portal_recovery_challenges SET consumed=? WHERE id=? AND grant_hash=? AND consumed IS NULL AND invalidated=0 AND ${eligible}`).bind(claim,data.challenge_id,grantHash),
        db().prepare(`UPDATE portal_recovery_emails SET email=?,verified_at=?,version=version+1 WHERE student_id=? AND version=?
          AND EXISTS(SELECT 1 FROM portal_recovery_challenges WHERE id=? AND consumed=?)`).bind(result.email,now,session!.student_id,result.email_version,data.challenge_id,claim),
      ]);
      return changed[1].meta.changes ? json({ok:true}) : invalid();
    } catch { return json({error:'This email cannot be verified for this account. Contact your campus administrator.'},400); }
  }
  if (!idValid(data.grant)) return invalid();
  const grantHash = digest(data.grant), now = Date.now();
  const row = await db().prepare(`SELECT c.*,a.username FROM portal_recovery_challenges c JOIN portal_accounts a ON a.student_id=c.student_id
    WHERE c.grant_hash=? AND c.purpose='reset' AND c.consumed IS NULL AND c.invalidated=0 AND c.grant_expires>?`).bind(grantHash,now).first<Row>();
  if (!row) return invalid();
  if (!await budget('complete:' + row.account_key, 10, 3600000)) return limited();
  if (!passwordValid(data.password,data.confirmation,row)) return json({error:'Choose a personal password of 8–1024 characters and confirm it.'},400);
  if (await verifyPassword(data.password,row.credential_hash)) return json({error:'Choose a different password.'},400);
  const encoded = await hashPassword(data.password), claim = token(), action = crypto.randomUUID();
  const payload = JSON.stringify({password_hash:encoded,expected_hash:row.credential_hash,expected_version:row.credential_version,recovery:true});
  const changed = await db().batch([
    db().prepare(`UPDATE portal_recovery_challenges SET consumed=? WHERE id=? AND grant_hash=? AND purpose='reset'
      AND consumed IS NULL AND invalidated=0 AND grant_expires>? AND ${eligible}`).bind(claim,row.id,grantHash,Date.now()),
    db().prepare(`UPDATE portal_accounts SET password_hash=?,must_change_password=0,credential_version=credential_version+1,pending_base_hash=password_hash,pending_action_id=?
      WHERE student_id=? AND EXISTS(SELECT 1 FROM portal_recovery_challenges WHERE id=? AND consumed=?)`).bind(encoded,action,row.student_id,row.id,claim),
    db().prepare(`INSERT INTO portal_actions(id,student_id,kind,payload,created_at) SELECT ?,student_id,'password',?,?
      FROM portal_accounts WHERE student_id=? AND pending_action_id=?`).bind(action,payload,new Date().toISOString(),row.student_id,action),
    db().prepare(`DELETE FROM portal_sessions WHERE student_id=? AND EXISTS(SELECT 1 FROM portal_accounts WHERE student_id=? AND pending_action_id=?)`).bind(row.student_id,row.student_id,action),
    db().prepare(`UPDATE portal_recovery_emails SET verified_at=?,version=version+1 WHERE student_id=? AND email=? AND verified_at IS NULL
      AND EXISTS(SELECT 1 FROM portal_recovery_challenges c JOIN portal_recovery_assisted_challenges ac ON ac.challenge_id=c.id WHERE c.id=? AND c.consumed=?)`).bind(now,row.student_id,row.email,row.id,claim),
    db().prepare(`UPDATE portal_recovery_assistance SET consumed_at=? WHERE id IN(
      SELECT ac.approval_id FROM portal_recovery_assisted_challenges ac JOIN portal_recovery_challenges c ON c.id=ac.challenge_id WHERE c.id=? AND c.consumed=?) AND consumed_at IS NULL`).bind(now,row.id,claim),
    db().prepare(`INSERT INTO portal_recovery_mail(id,email,created_at,expires_at) SELECT ?,?,?,?
      WHERE EXISTS(SELECT 1 FROM portal_accounts WHERE student_id=? AND pending_action_id=?)`).bind(action,row.email,now,now+86400000,row.student_id,action),
  ]);
  if (!changed[0].meta.changes || !changed[1].meta.changes) return invalid();
  return json({ok:true}); // Deliberately no login cookie or normal session.
}
