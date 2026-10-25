import { build } from 'esbuild';
import { Miniflare } from 'miniflare';
import { mkdtemp, readFile, rm, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
const directory=await mkdtemp(join(tmpdir(),'cbvms-archive-'));let mf;
try {
 await build({stdin:{contents:`import {GET,POST} from './app/api/[...path]/route.ts'; export {PasswordCrypto} from './lib/password-crypto'; export default {fetch(r){return r.method==='GET'?GET(r):POST(r)}}`,resolveDir:resolve('.')},bundle:true,format:'esm',platform:'browser',external:['cloudflare:workers','node:*'],outfile:join(directory,'worker.mjs')});
 mf=new Miniflare({modulesRoot:directory,rootPath:directory,modules:true,scriptPath:join(directory,'worker.mjs'),compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],bindings:{SYNC_KEY:'fixture'},d1Databases:['DB'],r2Buckets:['BUCKET'],durableObjects:{PASSWORD_CRYPTO:{className:'PasswordCrypto',useSQLite:true}}});
 const db=await mf.getD1Database('DB'),bucket=await mf.getR2Bucket('BUCKET');
 for(const name of (await readdir('drizzle')).filter(n=>n.endsWith('.sql')).sort()) {
  const sql=await readFile('drizzle/'+name,'utf8');
  const statements=(name.startsWith('0006')||name.startsWith('0007'))?sql.split('--> statement-breakpoint'):sql.split('--> statement-breakpoint').flatMap(s=>s.split(';'));
  for(const stmt of statements.filter(s=>s.trim())) await db.prepare(stmt).run();
 }
 async function call(path,data,headers={}) {const r=await mf.dispatchFetch('https://portal.test'+path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json',Origin:'https://portal.test',...headers},...(data===undefined?{}:{body:JSON.stringify(data)})});return {status:r.status,data:await r.json(),cookie:r.headers.get('Set-Cookie')?.split(';')[0]};}
 await db.prepare("INSERT INTO campus_sync_control(singleton,source_id,writer_id) VALUES(1,'source','writer')").run();
 const headers={Authorization:'Bearer fixture','X-CBVMS-Protocol':'2','X-CBVMS-Source':'source','X-CBVMS-Writer':'writer'};
 const hash=createHash('sha256').update('Synthetic password').digest('hex');
 const students=['S1','S2'].map(id=>({id,email:id+'@test.invalid',name:id,active:true,account:{username:id,password_hash:hash,must_change_password:false,credential_version:1},snapshot:{schema_version:3,academic_terms:[],term_history:{enrollments:[]},profile:{student_id:id,name:id},violations:[],appeals:[]}}));
 async function publish(generation){return call('/api/sync/publish',{generation,run_id:crypto.randomUUID(),students,receipts:[]},headers);}
 assert.equal((await publish(0)).status,200);
 const mapping={S1:{'10':{portal_violation_id:-1,original_identity:'case'}}};
 await db.prepare('INSERT INTO campus_portal_imports VALUES(?,?,?,?,?,?,?)').bind('import','source-checkpoint','payload','now',1,1,JSON.stringify(mapping)).run();
 const session=await call('/api/login',{username:'S1',password:'Synthetic password'});assert.equal(session.status,200);
 await db.prepare("INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES('pending','S1','password','{}','now')").run();
 await bucket.put('private-fixture','preserved evidence');
 await db.prepare("INSERT INTO portal_lifecycle_authorities VALUES('authority','source-checkpoint',1,'superadmin','superadmin',1)").run();
 const command=crypto.randomUUID();
 const sql='INSERT INTO portal_lifecycle_operations(operation_id,student_id,import_id,source_checkpoint,payload_sha256,expected_username,expected_version,version,operation_type,authority_id,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)';
 await assert.rejects(db.prepare(sql).bind(crypto.randomUUID(),'S2','import','source-checkpoint','payload','S2',0,1,'recycle','authority','fixture').run());
 await db.prepare(sql).bind(command,'S1','import','source-checkpoint','payload','S1',0,1,'recycle','authority','fixture').run();
 assert.equal((await call('/api/me',undefined,{Cookie:session.cookie})).status,401);
 assert.equal((await call('/api/login',{username:'S1',password:'Synthetic password'})).status,401);
 assert.equal((await call('/api/login',{username:'S2',password:'Synthetic password'})).status,200);
 assert.equal((await publish(1)).status,200);
 assert.equal((await db.prepare("SELECT active FROM portal_students WHERE id='S1'").first()).active,0);
 assert.equal((await db.prepare("SELECT status FROM portal_actions WHERE id='pending'").first()).status,'queued');
 assert.deepEqual(JSON.parse((await db.prepare('SELECT mapping_json FROM campus_portal_imports').first()).mapping_json),mapping);
 assert.equal(await (await bucket.get('private-fixture')).text(),'preserved evidence');
 assert.equal((await db.prepare('SELECT operation_id FROM portal_lifecycle_operations').first()).operation_id,command);
 assert.equal((await db.prepare('PRAGMA foreign_key_check').all()).results.length,0);
 const restore=crypto.randomUUID();
 await db.prepare(sql).bind(restore,'S1','import','source-checkpoint','payload','S1',1,2,'restore','authority','restore fixture').run();
 assert.equal((await call('/api/me',undefined,{Cookie:session.cookie})).status,401);
 const restored=await call('/api/login',{username:'S1',password:'Synthetic password'});
 assert.equal(restored.status,200);
 assert.equal((await publish(2)).status,200);
 assert.equal((await db.prepare("SELECT active FROM portal_students WHERE id='S1'").first()).active,1);
 await assert.rejects(db.prepare(sql).bind(crypto.randomUUID(),'S1','import','source-checkpoint','payload','S1',0,1,'recycle','authority','late').run());
 assert.equal((await db.prepare("SELECT active FROM portal_students WHERE id='S1'").first()).active,1);
 assert.equal((await call('/api/sync/lifecycle',{operation:'recycle'},headers)).status,409);
 console.log('PASS: isolated D1/R2 archive, ownership denial, session invalidation, login denial, restore fresh login, stale recycle denial, later publication fence, receipt/action/evidence preservation.');
} finally {await mf?.dispose();await rm(directory,{recursive:true,force:true});}
