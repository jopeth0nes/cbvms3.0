/** Real isolated Worker login/setup after the provider repair; synthetic passwords only. */
import {build} from 'esbuild';
import {Miniflare} from 'miniflare';
import {mkdtemp,readFile,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {spawnSync} from 'node:child_process';
const dir=await mkdtemp(join(tmpdir(),'cbvms-account-repair-'));let mf;
const hash=s=>createHash('sha256').update(s).digest('hex');
try {
 await build({stdin:{contents:`import {GET,POST} from './app/api/[...path]/route';export {PasswordCrypto} from './lib/password-crypto';export default {fetch(r){return r.method==='GET'?GET(r):POST(r)}}`,resolveDir:resolve('.')},bundle:true,format:'esm',platform:'browser',external:['cloudflare:workers','node:*'],outfile:join(dir,'worker.mjs')});
 mf=new Miniflare({modulesRoot:dir,rootPath:dir,modules:true,scriptPath:join(dir,'worker.mjs'),compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],bindings:{RECOVERY_HMAC_KEY:'synthetic-repair-fixture-secret-32bytes'},d1Databases:['DB'],r2Buckets:['BUCKET'],durableObjects:{PASSWORD_CRYPTO:{className:'PasswordCrypto',useSQLite:true}}});
 const db=await mf.getD1Database('DB');
 for(const file of (await readdir('drizzle')).filter(f=>f.endsWith('.sql')&&!f.startsWith('0008')).sort())
  for(const stmt of (await readFile('drizzle/'+file,'utf8')).split('--> statement-breakpoint').filter(s=>s.trim())) await db.prepare(stmt).run();
 const run=async(sql,...args)=>db.prepare(sql).bind(...args).run();
 const first=async(sql,...args)=>db.prepare(sql).bind(...args).first();
 await run("INSERT INTO portal_students VALUES('S1','s1@test','Synthetic student','{}','now',1)");
 await run('INSERT INTO portal_accounts(student_id,username,password_hash,must_change_password,credential_version,pending_base_hash,pending_action_id) VALUES(?,?,?,0,1,?,?)','S1','S1',hash('Previous chosen password'),hash('Previous temporary'),'old-change');
 await run("INSERT INTO portal_actions(id,student_id,kind,payload,created_at) VALUES('old-change','S1','password','{}','now')");
 const args={operation_id:randomUUID(),account:await first("SELECT * FROM portal_accounts WHERE student_id='S1'"),student:await first("SELECT * FROM portal_students WHERE id='S1'"),password_hash:hash('Latest temporary password'),version:2,snapshot:'{}',actor:'superadmin',reason:'Synthetic approved reset'};
 const root=resolve('../..');
 const p=spawnSync(join(root,'.venv/bin/python'),['-c','import sys,json; from core.provider_account_repair import repair_batch; print(json.dumps(repair_batch(**json.load(sys.stdin))))'],{cwd:root,input:JSON.stringify(args),encoding:'utf8'});
 assert.equal(p.status,0,p.stderr);
 await db.batch(JSON.parse(p.stdout).map(s=>db.prepare(s.sql).bind(...s.params)));
 let ip=0;
 const call=async(path,data,headers={})=>{
  const r=await mf.dispatchFetch('https://portal.test'+path,{method:'POST',headers:{Origin:'https://portal.test','Content-Type':'application/json','CF-Connecting-IP':'192.0.2.'+(++ip),...headers},body:JSON.stringify(data)});
  return {status:r.status,data:await r.json(),cookie:r.headers.get('set-cookie')?.split(';')[0]};
 };
 assert.equal((await call('/api/login',{username:'S1',password:'Previous chosen password'})).status,401);
 const login=await call('/api/login',{username:'S1',password:'Latest temporary password'});
 assert.equal(login.status,200);assert.equal(login.data.must_change_password,true);
 const setup=await call('/api/setup/password',{password:'New personal password',confirmation:'New personal password'},{Cookie:login.cookie,'X-CSRF-Token':login.data.csrf});
 assert.equal(setup.status,200,JSON.stringify(setup.data));assert.equal(setup.data.must_change_password,false);
 assert.equal((await call('/api/login',{username:'S1',password:'Latest temporary password'})).status,401);
 const fresh=await call('/api/login',{username:'S1',password:'New personal password'});
 assert.equal(fresh.status,200);assert.equal(fresh.data.must_change_password,false);
 assert.equal((await first("SELECT status FROM portal_actions WHERE id='old-change'")).status,'rejected');
 const pending=await first("SELECT payload FROM portal_actions WHERE student_id='S1' AND status='queued'");
 assert.equal(JSON.parse(pending.payload).expected_version,2);
 console.log('PASS: account repair → temporary login → mandatory password setup → personal-password login; old credentials denied.');
} finally {await mf?.dispose();await rm(dir,{recursive:true,force:true});}
