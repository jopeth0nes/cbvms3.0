/** Encrypted short-lived outbox for the existing registered campus SMTP sender. */
import { env } from 'cloudflare:workers';
import { createHmac, randomBytes, createCipheriv, createDecipheriv, timingSafeEqual } from 'node:crypto';
type MailEnv = { RECOVERY_HMAC_KEY?: string; RECOVERY_RELAY_KEY?: string };
export function recoveryConfig() { return env as unknown as MailEnv; }
export function recoveryAvailable() { return (recoveryConfig().RECOVERY_HMAC_KEY?.length || 0) >= 32; }
export function validEmail(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 254 && /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$/i.test(value)
    && !/\.(invalid|test|example|local)$/i.test(value);
}
function mailKey() { return createHmac('sha256', recoveryConfig().RECOVERY_HMAC_KEY!).update('cbvms/mail-encryption/v1').digest(); }
export function sealCode(id: string, code: string) {
  const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', mailKey(), iv);
  cipher.setAAD(Buffer.from(id));
  const sealed = Buffer.concat([cipher.update(code,'utf8'),cipher.final()]);
  return Buffer.concat([iv,cipher.getAuthTag(),sealed]).toString('base64');
}
function openCode(id: string, sealed: string) {
  const data = Buffer.from(sealed,'base64'), cipher = createDecipheriv('aes-256-gcm',mailKey(),data.subarray(0,12));
  cipher.setAAD(Buffer.from(id)); cipher.setAuthTag(data.subarray(12,28));
  return Buffer.concat([cipher.update(data.subarray(28)),cipher.final()]).toString('utf8');
}
/** Called only AFTER existing source/writer/Bearer protocol authentication. */
export async function recoveryMailSync(request: Request, path: string, refreshHeartbeat = true) {
  const db = env.DB!;
  const json = (value: unknown, status = 200) => Response.json(value,{status,headers:{'Cache-Control':'private, no-store'}});
  if (request.method !== 'POST') return json({error:'Method not allowed'},405);
  if (!recoveryAvailable()) return json({error:'Recovery service unavailable'},503);
  if (path === '/api/sync/mail/claim') {
    const now = Date.now(), claim = randomBytes(32).toString('hex');
    await db.batch([
      ...(refreshHeartbeat ? [db.prepare(`INSERT INTO portal_recovery_mail_status(singleton,last_seen) VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET last_seen=excluded.last_seen`).bind(now)] : []),
      db.prepare(`DELETE FROM portal_recovery_mail WHERE expires_at<?`).bind(now-86400000),
      db.prepare('UPDATE portal_recovery_mail SET sealed_code=NULL WHERE expires_at<=?').bind(now),
    ]);
    // At-most-once SMTP handoff. A lost response is never automatically redelivered.
    const row = await db.prepare(`UPDATE portal_recovery_mail SET status='claimed',claim=?,claimed_at=? WHERE id=(
      SELECT m.id FROM portal_recovery_mail m WHERE m.status='queued' AND m.expires_at>?
      AND (m.sealed_code IS NULL OR EXISTS(SELECT 1 FROM portal_recovery_challenges c WHERE c.id=m.id AND c.invalidated=0 AND c.consumed IS NULL AND c.expires_at>?))
      ORDER BY m.created_at LIMIT 1) AND status='queued' RETURNING *`).bind(claim,now,now,now).first<Record<string,any>>();
    if (!row) return json({messages:[]});
    let code: string | null = null;
    if (row.sealed_code) {
      try { code = openCode(row.id,row.sealed_code); }
      catch { await db.prepare("UPDATE portal_recovery_mail SET status='failed',sealed_code=NULL WHERE id=?").bind(row.id).run(); return json({messages:[]}); }
    }
    await db.prepare('UPDATE portal_recovery_mail SET sealed_code=NULL WHERE id=?').bind(row.id).run();
    return json({messages:[{id:row.id,claim,to:row.email,code,expires_at:row.expires_at}]});
  }
  if (path === '/api/sync/mail/receipt') {
    const raw = await request.text();
    if (raw.length > 2048) return json({error:'Invalid receipt'},400);
    let data: any; try { data=JSON.parse(raw); } catch { return json({error:'Invalid receipt'},400); }
    if (!data || typeof data.id!=='string' || data.id.length>200 || typeof data.claim!=='string' || !/^[a-f0-9]{64}$/.test(data.claim) || !['accepted','failed','uncertain'].includes(data.status)) return json({error:'Invalid receipt'},400);
    const row = await db.prepare(`UPDATE portal_recovery_mail SET status=? WHERE id=? AND claim=? AND status IN ('claimed',?) RETURNING id`).bind(data.status,data.id,data.claim,data.status).first();
    if (!row) return json({error:'Receipt conflict'},409);
    await db.prepare('UPDATE portal_recovery_challenges SET delivery=? WHERE id=?').bind(data.status==='accepted'?'accepted':'failed',data.id).run();
    return json({ok:true});
  }
  return json({error:'Not found'},404);
}

/** Independent least-privilege relay credential; never authorizes campus sync. */
export async function recoveryRelayRoute(request: Request) {
  const json = (value: unknown, status = 200) => Response.json(value, {status, headers: {'Cache-Control': 'private, no-store'}});
  const configured = recoveryConfig().RECOVERY_RELAY_KEY;
  const actual = Buffer.from(request.headers.get('Authorization') || '');
  const expected = Buffer.from('Bearer ' + (configured || ''));
  if (!configured || configured.length < 32 || actual.length !== expected.length || !timingSafeEqual(actual, expected))
    return json({error:'Unauthorized'},401);
  const path = new URL(request.url).pathname;
  if (!['/api/relay/mail/heartbeat','/api/relay/mail/status','/api/relay/mail/claim','/api/relay/mail/receipt'].includes(path))
    return json({error:'Not found'},404);
  if (!recoveryAvailable()) return json({error:'Recovery service unavailable'},503);
  const db = env.DB!;
  if (path === '/api/relay/mail/status') {
    if (request.method !== 'GET') return json({error:'Method not allowed'},405);
    const row = await db.prepare('SELECT last_seen FROM portal_recovery_mail_status WHERE singleton=1').first<{last_seen:number}>();
    const queue = await db.prepare('SELECT status,COUNT(*) count FROM portal_recovery_mail GROUP BY status').all();
    return json({online:!!row && row.last_seen>Date.now()-120000,last_seen:row?.last_seen || 0,queue:queue.results});
  }
  if (request.method !== 'POST') return json({error:'Method not allowed'},405);
  if (path === '/api/relay/mail/heartbeat') {
    const reader=request.body?.getReader();
    if (!reader) return json({error:'Invalid heartbeat'},400);
    let length=0,raw='';const decoder=new TextDecoder();
    while (true) {
      const {done,value}=await reader.read();if(done)break;
      length+=value.byteLength;
      if(length>128){await reader.cancel();return json({error:'Invalid heartbeat'},400);}
      raw+=decoder.decode(value,{stream:true});
    }
    let data:any;try{data=JSON.parse(raw+decoder.decode());}catch{return json({error:'Invalid heartbeat'},400);}
    if(!data || typeof data.ready!=='boolean')return json({error:'Invalid heartbeat'},400);
    await db.prepare(`INSERT INTO portal_recovery_mail_status(singleton,last_seen) VALUES(1,?)
      ON CONFLICT(singleton) DO UPDATE SET last_seen=excluded.last_seen`).bind(data.ready?Date.now():0).run();
    return json({ok:true});
  }
  // A claim cannot renew readiness. The relay must authenticate to SMTP first.
  const row=await db.prepare('SELECT last_seen FROM portal_recovery_mail_status WHERE singleton=1').first<{last_seen:number}>();
  if(path.endsWith('/claim') && (!row || row.last_seen<Date.now()-120000))return json({error:'SMTP relay offline'},503);
  return recoveryMailSync(request,path.replace('/api/relay/','/api/sync/'),false);
}
