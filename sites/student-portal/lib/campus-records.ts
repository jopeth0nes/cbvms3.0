/** Incremental records for identities created by the scoped registration bridge. */
import {createHash, timingSafeEqual} from 'node:crypto';
const json = (data: unknown, status=200) => Response.json(data,{status,headers:{'Cache-Control':'private, no-store'}});
const keyFor = (sid:string,id:number,digest:string) => `original-v2/${encodeURIComponent(sid)}/${id}/${digest}`;
export async function campusRecords(request: Request, env: any) {
  const supplied=Buffer.from(request.headers.get('Authorization')||'');
  const expected=Buffer.from(`Bearer ${env.CAMPUS_REGISTRATION_KEY}`);
  if (!env.CAMPUS_REGISTRATION_KEY || supplied.length!==expected.length || !timingSafeEqual(supplied,expected)) return json({error:'Unauthorized'},401);
  if (request.method!=='POST') return json({error:'Method not allowed'},405);
  try {
    const reader=request.body?.getReader(); if(!reader) return json({error:'Body required'},400);
    let size=0; const chunks:Uint8Array[]=[];
    while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>15*1024*1024){await reader.cancel();return json({error:'Request too large'},413);}chunks.push(value);}
    const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if(!data || typeof data.student_id!=='string' || typeof data.username!=='string' || typeof data.source_id!=='string' ||
      !Number.isSafeInteger(data.lifecycle_version) || !Number.isSafeInteger(data.sequence) || data.sequence<1)
      return json({error:'Invalid records envelope'},400);
    const owned=await env.DB.prepare(`SELECT 1 FROM campus_registration_owners o JOIN portal_students p ON p.id=o.student_id
      JOIN portal_accounts a ON a.student_id=o.student_id WHERE o.student_id=? AND o.source_id=? AND o.lifecycle_version=?
      AND o.active=1 AND o.access_blocked=0 AND p.active=1 AND a.username=?`).bind(data.student_id,data.source_id,data.lifecycle_version,data.username).first();
    if(!owned) return json({error:'Campus ownership or lifecycle mismatch'},409);
    if(data.kind==='appeal_actions') {
      const action=await env.DB.prepare(`SELECT id,student_id,kind,payload,created_at FROM portal_actions
        WHERE student_id=? AND kind='appeals' AND status='queued' ORDER BY created_at,id LIMIT 1`).bind(data.student_id).first<any>();
      if(!action) return json({status:'actions',actions:[]});
      const payload=JSON.parse(action.payload);
      if(payload.evidence_key!==`appeals/${action.id}` || !/^[0-9a-f]{64}$/.test(payload.evidence_sha256) || !/^[0-9a-f]{64}$/.test(payload.violation_identity))
        return json({error:'Appeal needs evidence or identity repair'},409);
      const file=await env.BUCKET.get(payload.evidence_key);
      if(!file || file.size>10*1024*1024) return json({error:'Appeal picture unavailable'},409);
      const bytes=Buffer.from(await file.arrayBuffer());
      if(createHash('sha256').update(bytes).digest('hex')!==payload.evidence_sha256) return json({error:'Appeal picture checksum mismatch'},409);
      return json({status:'actions',actions:[{...action,payload:{...payload,file:bytes.toString('base64')}}]});
    }
    if(data.kind==='appeal_receipt') {
      if(typeof data.action_id!=='string' || !['applied','rejected'].includes(data.status)) return json({error:'Invalid appeal receipt'},400);
      const message=data.status==='applied'?'Received by campus; awaiting administrator review.':'Campus review required: invalid or conflicting submission.';
      // Revalidate lifecycle inside this write as recycle may race the read above.
      const result=await env.DB.prepare(`UPDATE portal_actions SET status=?,result=? WHERE id=? AND student_id=? AND kind='appeals' AND status='queued'
        AND EXISTS(SELECT 1 FROM campus_registration_owners o JOIN portal_students p ON p.id=o.student_id JOIN portal_accounts a ON a.student_id=o.student_id
          WHERE o.student_id=? AND o.source_id=? AND o.lifecycle_version=? AND o.active=1 AND o.access_blocked=0 AND p.active=1 AND a.username=?)`)
        .bind(data.status,message,data.action_id,data.student_id,data.student_id,data.source_id,data.lifecycle_version,data.username).run();
      return json({status:'acknowledged',changed:result.meta.changes});
    }
    if(data.kind==='evidence') {
      if(!Number.isSafeInteger(data.violation_id) || data.violation_id<1 || !/^[0-9a-f]{64}$/.test(data.sha256) || typeof data.file!=='string') return json({error:'Invalid evidence'},400);
      const bytes=Buffer.from(data.file,'base64');
      if(!bytes.length || bytes.length>10*1024*1024 || createHash('sha256').update(bytes).digest('hex')!==data.sha256 ||
        !(bytes[0]===255&&bytes[1]===216 || bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])) || bytes.subarray(0,2).toString()==='BM')) return json({error:'Invalid evidence'},400);
      const key=keyFor(data.student_id,data.violation_id,data.sha256);
      await env.BUCKET.put(key,bytes,{httpMetadata:{contentType:bytes[0]===255?'image/jpeg':bytes[0]===137?'image/png':'image/bmp'}});
      return json({status:'evidence_verified',sha256:data.sha256});
    }
    const snapshot=data.snapshot;
    const allowed=['schema_version','academic_terms','term_history','enrollment_status','suspension_history','profile','term','strikes','suspension','counts','violations','notifications','appeals','preferences'];
    if(data.kind!=='records' || !snapshot || snapshot.schema_version!==3 || snapshot.profile?.student_id!==data.student_id ||
       Object.keys(snapshot).some(k=>!allowed.includes(k)) || !Array.isArray(snapshot.violations) || !Array.isArray(snapshot.notifications) || !Array.isArray(snapshot.appeals)) return json({error:'Invalid records'},400);
    const serialized=JSON.stringify(snapshot);
    if(Buffer.byteLength(serialized)>2*1024*1024 || /"(?:password_hash|encoding|photo|profile_photo|session_token|file_data)"\s*:/.test(serialized)) return json({error:'Unsafe records'},400);
    const ids=new Set();
    for(const v of snapshot.violations){
      if(!Number.isSafeInteger(v.id)||v.id<1||ids.has(v.id)|| (v.student_id!==undefined&&v.student_id!==data.student_id)) return json({error:'Invalid violation owner'},400);
      ids.add(v.id);
      if(v.has_snapshot && (!/^[0-9a-f]{64}$/.test(v.evidence_sha256) || !await env.BUCKET.head(keyFor(data.student_id,v.id,v.evidence_sha256)))) return json({error:'Evidence not uploaded'},409);
    }
    // The conditional update and cursor share one transaction. Old workers cannot roll records back.
    const gate=`EXISTS(SELECT 1 FROM campus_registration_owners o JOIN portal_accounts a ON a.student_id=o.student_id WHERE o.student_id=portal_students.id AND o.source_id=? AND o.lifecycle_version=? AND o.active=1 AND o.access_blocked=0 AND a.username=?)
      AND NOT EXISTS(SELECT 1 FROM campus_record_cursors c WHERE c.student_id=portal_students.id AND c.lifecycle_version=? AND c.sequence>=?)`;
    const result=await env.DB.batch([
      env.DB.prepare(`UPDATE portal_students SET snapshot=?,synced_at=? WHERE id=? AND active=1 AND ${gate}`)
        .bind(serialized,new Date().toISOString(),data.student_id,data.source_id,data.lifecycle_version,data.username,data.lifecycle_version,data.sequence),
      env.DB.prepare(`INSERT INTO campus_record_cursors(student_id,lifecycle_version,sequence) SELECT id,?,? FROM portal_students WHERE id=? AND active=1 AND ${gate}
        ON CONFLICT(student_id) DO UPDATE SET lifecycle_version=excluded.lifecycle_version,sequence=excluded.sequence`)
        .bind(data.lifecycle_version,data.sequence,data.student_id,data.source_id,data.lifecycle_version,data.username,data.lifecycle_version,data.sequence),
    ]);
    return json({status:result[0].meta.changes?'synced':'unchanged'});
  }catch{return json({error:'Records not synced; campus will retry'},409);}
}
