/** Isolated D1 registration tests; never connects to production. */
import {createRequire} from 'node:module';
import {mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {pbkdf2Sync} from 'node:crypto';
import assert from 'node:assert/strict';
const require = createRequire(new URL('../../../.runtime/cloud-tools/package.json', import.meta.url));
const {transform} = require('esbuild');
const {Miniflare} = require('miniflare');
const dir = await mkdtemp(join(tmpdir(),'cbvms-registration-'));
let mf;
try {
  const source = await readFile('lib/campus-registration.ts','utf8');
  const compiled = await transform(source + '\nexport default {fetch(r,e){return campusRegistration(r,e)}}', {loader:'ts',format:'esm'});
  await writeFile(join(dir,'worker.mjs'),compiled.code);
  mf = new Miniflare({rootPath:dir,modulesRoot:dir,modules:true,scriptPath:join(dir,'worker.mjs'),
    compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],bindings:{CAMPUS_REGISTRATION_KEY:'fixture-only'},d1Databases:['DB']});
  const db = await mf.getD1Database('DB');
  for (const file of ['0000_furry_moira_mactaggert','0001_overrated_medusa','0002_sync_protocol','0016_campus_registration_owners']) {
    const sql = await readFile(`drizzle/${file}.sql`,'utf8');
    for (const statement of sql.replace(/^--.*$/gm,'').split(';').filter(s=>s.trim())) await db.prepare(statement).run();
  }
  const hash = `pbkdf2_sha256$v1$600000$${'00'.repeat(16)}$${pbkdf2Sync('Synthetic password',Buffer.alloc(16),600000,32,'sha256').toString('hex')}`;
  const payload = {source_id:'00000000-0000-4000-8000-000000000001',lifecycle_version:0,active:true,
    profile:{student_id:'S1',name:'Synthetic',course:'Information Technology'},username:'synthetic',password_hash:hash,credential_version:1,must_change_password:true};
  async function call(data=payload, token='fixture-only',method='POST') {
    const response = await mf.dispatchFetch('https://portal.example/api/campus/registrations',
      {method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},...(method==='POST'?{body:JSON.stringify(data)}:{})});
    return {status:response.status,data:await response.json()};
  }
  assert.equal((await call(payload,'bad')).status,401);
  assert.equal((await call(payload,'fixture-only','GET')).status,405);
  assert.equal((await call({...payload,photo:'private'})).status,400);
  assert.equal((await call({...payload,profile:{...payload.profile,name:'x'.repeat(17000)}})).status,413);
  assert.equal((await call()).status,201);
  assert.equal((await call()).data.status,'registered');
  assert.equal((await db.prepare('SELECT count(*) AS n FROM portal_students').first()).n,1);
  await db.prepare("UPDATE portal_accounts SET password_hash='website-password-preserved' WHERE student_id='S1'").run();
  assert.equal((await call()).data.status,'registered');
  assert.equal((await db.prepare("SELECT password_hash FROM portal_accounts WHERE student_id='S1'").first()).password_hash,'website-password-preserved');
  assert.equal((await call({...payload,source_id:'00000000-0000-4000-8000-000000000002'})).data.status,'existing_account');
  assert.equal((await call({...payload,lifecycle_version:1,active:false})).data.status,'registered');
  assert.equal((await db.prepare("SELECT active FROM portal_students WHERE id='S1'").first()).active,0);
  assert.equal((await call()).status,409);
  assert.equal((await call({...payload,lifecycle_version:2,active:true})).status,200);
  await db.prepare("INSERT INTO portal_students(id,email,name,snapshot,synced_at) VALUES('OLD','old@example.invalid','Old','{}','2026-01-01')").run();
  await db.prepare("INSERT INTO portal_accounts(student_id,username,password_hash) VALUES('OLD','old-login','old-hash')").run();
  const existing = {...payload,profile:{student_id:'OLD',name:'Old'},username:'old-login'};
  assert.equal((await call(existing)).data.status,'existing_account');
  assert.equal((await db.prepare("SELECT password_hash FROM portal_accounts WHERE student_id='OLD'").first()).password_hash,'old-hash');
  assert.equal((await call({...payload,profile:{student_id:'NEW',name:'New'},username:'old-login'})).data.status,'existing_account');
  assert.equal(await db.prepare("SELECT 1 FROM portal_students WHERE id='NEW'").first(),null);
  console.log('PASS: create, retry, auth, size/type validation, existing passwords, username collision, lifecycle ownership, stale state, recycle and restore.');
} finally {await mf?.dispose(); await rm(dir,{recursive:true,force:true});}
