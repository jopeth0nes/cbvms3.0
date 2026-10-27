import {build} from 'esbuild';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {Miniflare} from 'miniflare';
import {mkdtemp,readFile,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
const hash=s=>createHash('sha256').update(s).digest('hex');
const dir=await mkdtemp(join(tmpdir(),'cbvms-incremental-'));let mf;
try{
 await build({stdin:{contents:`import {GET,POST} from './app/api/[...path]/route.ts'; export {PasswordCrypto} from './lib/password-crypto';export default {fetch(r){return r.method==='GET'?GET(r):POST(r)}}`,resolveDir:resolve('.')},bundle:true,format:'esm',platform:'browser',external:['cloudflare:workers','node:*'],outfile:join(dir,'worker.mjs')});
 mf=new Miniflare({modulesRoot:dir,rootPath:dir,modules:true,scriptPath:join(dir,'worker.mjs'),compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],bindings:{SYNC_KEY:'legacy-fixture'},d1Databases:['DB'],r2Buckets:['BUCKET'],durableObjects:{PASSWORD_CRYPTO:{className:'PasswordCrypto',useSQLite:true}}});
 const db=await mf.getD1Database('DB');
 for(const name of (await readdir('drizzle')).filter(n=>n.endsWith('.sql')).sort()){
  const sql=await readFile('drizzle/'+name,'utf8');
  const statements=sql.includes('CREATE TRIGGER')?sql.split('--> statement-breakpoint'):sql.split('--> statement-breakpoint').flatMap(s=>s.split(';'));
  for(const stmt of statements.filter(s=>s.trim()))await db.prepare(stmt).run();
 }
 for(const id of ['s1','s2']){
  await db.prepare('INSERT INTO portal_students(id,email,name,snapshot,synced_at,active) VALUES(?,?,?,?,?,1)').bind(id,id+'@test.invalid','Original',JSON.stringify({profile:{name:'Original',email:id+'@test.invalid'},violations:[{id:-10,campus_violation_id:10}],academic_terms:[{id:1}]}),'baseline').run();
  await db.prepare('INSERT INTO portal_accounts(student_id,username,password_hash) VALUES(?,?,?)').bind(id,id,hash('fixture')).run();
  await db.prepare('INSERT INTO campus_shared_profiles(student_id,version,name,email) VALUES(?,0,?,?)').bind(id,'Original',id+'@test.invalid').run();
 }
 const tokenA='a'.repeat(40),tokenB='b'.repeat(40),actorA='c'.repeat(40),actorB='d'.repeat(40);
 for(const [id,token,session] of [['A',tokenA,actorA],['B',tokenB,actorB]]){
  await db.prepare('INSERT INTO campus_devices VALUES(?,?,1)').bind(id,hash(token)).run();
  await db.prepare("INSERT INTO campus_actors VALUES(?,'admin',1)").bind(id).run();
  await db.prepare('INSERT INTO campus_actor_sessions VALUES(?,?,?,unixepoch()+3600)').bind(hash(session),id,id).run();
  await db.prepare("INSERT INTO campus_profile_grants VALUES(?,'s1')").bind(id).run();
 }
 async function call(ops,device='A',extra={}){
  const r=await mf.dispatchFetch('https://portal.test/api/campus/operations',{method:'POST',headers:{Authorization:'Bearer '+(device==='A'?tokenA:tokenB),'X-CBVMS-Actor-Session':device==='A'?actorA:actorB,...extra},body:JSON.stringify({operations:ops})});return {status:r.status,data:await r.json()};
 }
 function op(device,payload,version=0,id='s1'){return {operation_id:randomUUID(),device_id:device,entity_type:'student',entity_id:id,expected_version:version,operation_type:'student.profile.patch',payload,payload_digest:hash(JSON.stringify(Object.fromEntries(Object.keys(payload).sort().map(k=>[k,payload[k]]))))};}
 const first=op('A',{name:'Accepted'});
 assert.equal((await call([first],'A',{'X-CBVMS-Actor-Session':''})).status,401);
 assert.equal((await call([{...first,role:'superadmin'}])).status,400);
 assert.equal((await call([op('A',{name:'No access'},0,'s2')])).status,403);
 const accepted=await call([first]);assert.equal(accepted.status,200);assert.equal(accepted.data.receipts[0].status,'accepted');
 assert.deepEqual(await call([first]),accepted);
 assert.equal((await db.prepare('SELECT count(*) n FROM campus_profile_changes').first()).n,1);
 assert.equal((await call([{...first,expected_version:1}])).status,409);
 const conflict=await call([op('B',{name:'Conflicting'})],'B');assert.equal(conflict.data.receipts[0].status,'conflict');
 assert.equal(JSON.parse(conflict.data.receipts[0].detail).current.name,'Accepted');
 const race=await Promise.all([call([op('A',{email:'one@test.invalid'},1)]),call([op('B',{email:'two@test.invalid'},1)],'B')]);
 assert.equal(race.filter(r=>r.data.receipts?.[0].status==='accepted').length,1);
 assert.equal((await db.prepare("SELECT version FROM campus_shared_profiles WHERE student_id='s1'").first()).version,2);
 await assert.rejects(db.prepare("UPDATE portal_students SET name='Stale' WHERE id='s1'").run());
 const snapshot=JSON.parse((await db.prepare("SELECT snapshot FROM portal_students WHERE id='s1'").first()).snapshot);
 assert.equal(snapshot.violations[0].id,-10);assert.equal(snapshot.academic_terms[0].id,1);
 await db.prepare('INSERT INTO campus_portal_imports VALUES(?,?,?,?,?,?,?)').bind('import','checkpoint','digest','now',1,1,JSON.stringify({s1:{10:{portal_violation_id:-10}}})).run();
 await db.prepare("INSERT INTO portal_lifecycle_authorities VALUES('authority','checkpoint',1,'root','superadmin',1)").run();
 const life=type=>db.prepare('INSERT INTO portal_lifecycle_operations(operation_id,student_id,import_id,source_checkpoint,payload_sha256,expected_username,expected_version,version,operation_type,authority_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(randomUUID(),'s1','import','checkpoint','digest','s1',type==='recycle'?0:1,type==='recycle'?1:2,type,'authority','fixture');
 await life('recycle').run();
 assert.equal((await call([op('A',{name:'Cannot reactivate'},2)])).data.receipts[0].status,'conflict');
 assert.equal((await db.prepare("SELECT active FROM portal_students WHERE id='s1'").first()).active,0);
 await life('restore').run();
 assert.equal((await call([op('A',{name:'Restored update'},2)])).data.receipts[0].status,'accepted');
 assert.equal((await db.prepare("SELECT name FROM portal_students WHERE id='s1'").first()).name,'Restored update');
 const r=await mf.dispatchFetch('https://portal.test/api/campus/changes?after=0',{headers:{Authorization:'Bearer '+tokenA,'X-CBVMS-Actor-Session':actorA}});
 const page=await r.json();assert.equal(page.events.length,3);assert.equal(page.next_cursor,3);
 const python=await promisify(execFile)(resolve('../../.venv/bin/python'),[resolve('../../tests/check_incremental_client.py'),String(await mf.ready)],{timeout:60000});
 console.log(python.stdout.trim());
 await db.prepare("UPDATE campus_actor_sessions SET expires=0 WHERE device_id='A'").run();
 assert.equal((await call([op('A',{name:'Expired'},3)])).status,401);
 assert.equal((await db.prepare('PRAGMA foreign_key_check').all()).results.length,0);
 console.log('PASS incremental D1: device/staff separation, ACL, strict payload, duplicate replay, CAS race, preserved negative mapping/history, lifecycle denial and post-restore update, journal, expiry.');
}finally{await mf?.dispose();await rm(dir,{recursive:true,force:true});}
