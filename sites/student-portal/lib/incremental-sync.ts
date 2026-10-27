/** Explicitly enrolled campus profile protocol; never accepts provider credentials. */
import { env } from 'cloudflare:workers';
import { createHash } from 'node:crypto';
type Obj = Record<string, any>;
const db = () => env.DB!;
const hash = (s: string) => createHash('sha256').update(s).digest('hex');
const json = (v: unknown, status = 200) => Response.json(v, {status, headers: {'Cache-Control':'private, no-store'}});
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const canonical = (p: Obj) => JSON.stringify(Object.fromEntries(Object.keys(p).sort().map(k => [k,p[k]])));
async function body(request: Request): Promise<Obj> {
  const reader=request.body?.getReader(); if (!reader) throw Error('Invalid body');
  let size=0; const parts: Uint8Array[]=[];
  try { while (true) { const {done,value}=await reader.read(); if(done) break;
    size+=value.byteLength; if(size>65536) throw Error('Body too large'); parts.push(value);
  }} finally { await reader.cancel(); }
  const bytes=new Uint8Array(size); let offset=0;
  for(const part of parts){bytes.set(part,offset);offset+=part.length;}
  const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  if(!value || Array.isArray(value) || typeof value!=='object') throw Error('Invalid body');
  return value;
}
function operation(value: Obj, device: string) {
  const allowed=['operation_id','device_id','entity_type','entity_id','expected_version','operation_type','payload','payload_digest'];
  if(!value || typeof value!=='object' || Object.keys(value).some(k=>!allowed.includes(k)) ||
    !uuid.test(value.operation_id) || value.device_id!==device || value.entity_type!=='student' ||
    typeof value.entity_id!=='string' || !value.entity_id.length || value.entity_id.length>128 ||
    value.operation_type!=='student.profile.patch' || !Number.isSafeInteger(value.expected_version) || value.expected_version<0)
    throw Error('Invalid operation');
  const p=value.payload;
  if(!p || Array.isArray(p) || typeof p!=='object' || !Object.keys(p).length ||
    Object.entries(p).some(([k,v])=>!['name','email'].includes(k) || typeof v!=='string' || v.length>254 || (k==='name'&&!v.trim()))) throw Error('Invalid profile');
  const encoded=canonical(p); if(hash(encoded)!==value.payload_digest) throw Error('Payload digest mismatch');
  return {...value, encoded};
}
function receipt(row: Obj) {
  return {operation_id:row.operation_id,device_id:row.device_id,entity_type:'student',entity_id:row.student_id,
    operation_type:'student.profile.patch',expected_version:row.expected_version,payload_digest:row.payload_digest,
    status:row.status,version:row.version,detail:row.result};
}
export async function incrementalSync(request: Request) {
  const url=new URL(request.url);
  const bearer=request.headers.get('Authorization') || '';
  const actorToken=request.headers.get('X-CBVMS-Actor-Session') || '';
  if(!bearer.startsWith('Bearer ') || bearer.length>512 || actorToken.length<32 || actorToken.length>256)
    return json({error:'Application device and staff session required'},401);
  const sessionHash=hash(actorToken);
  const auth=await db().prepare(`SELECT d.device_id,a.actor_id FROM campus_devices d
    JOIN campus_actor_sessions s ON s.device_id=d.device_id JOIN campus_actors a ON a.actor_id=s.actor_id
    WHERE d.token_hash=? AND s.token_hash=? AND d.enabled=1 AND a.enabled=1 AND s.expires>unixepoch()`)
    .bind(hash(bearer.slice(7)),sessionHash).first<Obj>();
  if(!auth) return json({error:'Application authentication required'},401);
  if(request.method==='GET' && url.pathname==='/api/campus/changes') {
    const raw=url.searchParams.get('after') || '0'; const after=Number(raw);
    if(!/^\d+$/.test(raw)||!Number.isSafeInteger(after))return json({error:'Invalid cursor'},400);
    const rows=await db().prepare(`SELECT c.* FROM campus_profile_changes c JOIN campus_profile_grants g ON g.student_id=c.student_id
      WHERE g.actor_id=? AND c.cursor>? ORDER BY c.cursor LIMIT 25`).bind(auth.actor_id,after).all<Obj>();
    const events=rows.results.map(r=>({cursor:r.cursor,operation_id:r.operation_id,payload:JSON.parse(r.payload)}));
    return json({after_cursor:after,next_cursor:events.at(-1)?.cursor ?? after,events});
  }
  if(request.method!=='POST'||url.pathname!=='/api/campus/operations')return json({error:'Not found'},404);
  let operations: Obj[];
  try {const data=await body(request);
    if(Object.keys(data).some(k=>k!=='operations')||!Array.isArray(data.operations)||!data.operations.length||data.operations.length>10)throw Error('Invalid batch');
    operations=data.operations.map(v=>operation(v,auth.device_id));
    if(new Set(operations.map(o=>o.operation_id)).size!==operations.length)throw Error('Duplicate operation');
  } catch {return json({error:'Invalid bounded operation batch'},400);}
  const receipts=[];
  for(const op of operations) {
    // Retry races recover the original immutable receipt. Reads are not the write fence.
    let finished=false;
    for(let attempt=0;attempt<3&&!finished;attempt++) {
      const result=await db().batch([
        db().prepare('SELECT * FROM campus_profile_operations WHERE operation_id=?').bind(op.operation_id),
        db().prepare(`SELECT p.*,s.active,COALESCE(l.recycled,0) AS recycled,COALESCE(l.access_blocked,0) AS access_blocked FROM campus_shared_profiles p JOIN portal_students s ON s.id=p.student_id LEFT JOIN portal_student_lifecycle l ON l.student_id=p.student_id JOIN campus_profile_grants g ON g.student_id=p.student_id
          WHERE p.student_id=? AND g.actor_id=?`).bind(op.entity_id,auth.actor_id)
      ]);
      const old=result[0].results[0] as Obj|undefined, profile=result[1].results[0] as Obj|undefined;
      if(!profile)return json({error:'Student profile not granted or not adopted'},403);
      if(old) {
        if(old.device_id!==auth.device_id||old.actor_id!==auth.actor_id||old.student_id!==op.entity_id||old.expected_version!==op.expected_version||old.payload!==op.encoded||old.payload_digest!==op.payload_digest)
          return json({error:'Operation identity already used'},409);
        receipts.push(receipt(old));finished=true;break;
      }
      const restricted=!profile.active || profile.recycled || profile.access_blocked;
      const accepted=profile.version===op.expected_version && !restricted;
      const detail=accepted?{}:{reason:restricted?'Student access restricted':'Version conflict',expected_version:op.expected_version,server_version:profile.version,proposed:op.payload,current:{name:profile.name,email:profile.email}};
      try {
        const applied=await db().batch([
          db().prepare(`INSERT INTO campus_profile_operations(operation_id,device_id,actor_id,session_hash,student_id,expected_version,payload,payload_digest,status,version,result)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)`).bind(op.operation_id,auth.device_id,auth.actor_id,sessionHash,op.entity_id,op.expected_version,op.encoded,op.payload_digest,accepted?'accepted':'conflict',accepted?profile.version+1:profile.version,JSON.stringify(detail)),
          db().prepare('SELECT * FROM campus_profile_operations WHERE operation_id=?').bind(op.operation_id)
        ]);
        receipts.push(receipt(applied[1].results[0] as Obj));finished=true;
      } catch { if(attempt===2)return json({error:'Operation not acknowledged; retry the same operation ID'},409); }
    }
  }
  return json({receipts});
}
