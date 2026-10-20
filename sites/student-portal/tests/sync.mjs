/** Isolated real D1/R2 HTTP tests. No production credentials or endpoints. */
import { build } from 'esbuild';
import { Miniflare } from 'miniflare';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
const directory = await mkdtemp(join(tmpdir(), 'cbvms-sync-test-'));
let mf;
try {
  await build({stdin: {contents: `import {GET,POST} from './app/api/[...path]/route.ts'; export default {fetch(r) {return r.method === 'GET' ? GET(r) : POST(r)}};`, resolveDir: resolve('.')}, bundle: true, format: 'esm', platform: 'browser', external: ['cloudflare:workers', 'node:*'], outfile: join(directory, 'worker.mjs')});
  mf = new Miniflare({modulesRoot: directory, rootPath: directory, modules: true, scriptPath: join(directory, 'worker.mjs'), compatibilityDate: '2026-05-15', compatibilityFlags: ['nodejs_compat'], bindings: {SYNC_KEY: 'fixture-only'}, d1Databases: ['DB'], r2Buckets: ['BUCKET']});
  const db = await mf.getD1Database('DB');
  for (const name of ['0000_furry_moira_mactaggert', '0001_overrated_medusa', '0002_sync_protocol', '0003_academic_snapshots']) {
    const sql = await readFile(`drizzle/${name}.sql`, 'utf8');
    for (const statement of sql.split('--> statement-breakpoint').flatMap(s => s.split(';')).filter(s => s.trim())) await db.prepare(statement).run();
  }
  await db.prepare("INSERT INTO campus_sync_control (singleton,source_id,writer_id) VALUES (1,'fixture','writer')").run();
  let generation = 0;
  const syncHeaders = {'Authorization': 'Bearer fixture-only', 'X-CBVMS-Protocol': '2', 'X-CBVMS-Source': 'fixture', 'X-CBVMS-Writer': 'writer'};
  async function call(path, data, headers={}) {
    const response = await mf.dispatchFetch('https://portal.test' + path, {method: data === undefined ? 'GET' : 'POST', headers: {'Content-Type': 'application/json', Origin: 'https://portal.test', ...headers}, ...(data === undefined ? {} : {body: JSON.stringify(data)})});
    return {status: response.status, data: await response.json(), cookie: response.headers.get('Set-Cookie')?.split(';')[0]};
  }
  const hash = password => createHash('sha256').update(password).digest('hex');
  const students = ['S1','S2'].map((id,i) => ({id,email: `${id}@fixture.invalid`,name: id,active:true,account:{username:id,password_hash:hash('Initial password'),must_change_password:false,credential_version:1},snapshot:{schema_version:3,academic_terms:[],term_history:{enrollments:[]},profile:{student_id:id,name:id},violations:[{id:i+1,identity:hash(id),can_appeal:true,appeal_deadline:'2099-01-01 00:00:00'}],notifications:[],appeals:[]}}));
  const publish = (rows=students, receipts=[]) => call('/api/sync/publish', {generation,run_id:crypto.randomUUID(),students:rows,receipts}, syncHeaders);
  assert.equal((await call('/api/sync/audit')).status,401);
  assert.equal((await call('/api/sync/audit',undefined, {...syncHeaders,'X-CBVMS-Protocol':'1'})).status,409);
  assert.equal((await call('/api/sync/audit',undefined, {...syncHeaders,'X-CBVMS-Source':'wrong'})).status,409);
  assert.equal((await call('/api/sync/roster',{ids:[]},syncHeaders)).status,409);
  const oldSnapshot=structuredClone(students); delete oldSnapshot[0].snapshot.schema_version;
  assert.equal((await publish(oldSnapshot)).status,400);
  assert.equal((await publish()).status,200); generation++;
  assert.equal((await publish([])).status,409);
  const staleCredentials=structuredClone(students); staleCredentials[0].account.credential_version=0;
  assert.equal((await publish(staleCredentials)).status,400);
  assert.equal((await call('/api/sync/account',{student_id:'S1',account:null},syncHeaders)).status,409);
  const race = await Promise.all([publish(),publish()]);
  assert.equal(race.filter(r=>r.status===200).length,1); generation++;
  assert.equal((await call('/api/sync/publish',{generation:0,run_id:crypto.randomUUID(),students,receipts:[]},syncHeaders)).status,409);
  let session = await call('/api/login',{username:'S1',password:'Initial password'});
  assert.equal(session.status,200);
  let headers = {Cookie:session.cookie,'X-CSRF-Token':session.data.csrf};
  assert.equal((await call('/api/preferences',{dark_mode:true},{...headers,'X-CSRF-Token':'wrong'})).status,403);
  assert.equal((await call('/api/preferences',{dark_mode:true},headers)).status,200);
  assert.equal((await call('/api/evidence?id=2',undefined,headers)).status,404);
  const page = await call('/api/page?page=violations&student_id=S2',undefined,headers);
  assert.deepEqual(page.data._violations.map(v=>v.id),[1]);
  const profile = await call('/api/profile',{name:'New name',expected_name:'forged'},headers);
  assert.equal(profile.status,201);
  let actions = (await call('/api/sync/actions?after=0',undefined,syncHeaders)).data.actions;
  assert.equal(actions[0].payload.expected_name,'S1');
  // Missing evidence is isolated, with durable queue state untouched.
  await db.prepare("INSERT INTO portal_actions (id,student_id,kind,payload,created_at) VALUES ('missing','S2','appeals',?,?)").bind(JSON.stringify({evidence_key:'appeals/missing'}),new Date().toISOString()).run();
  for(let i=0;i<30;i++) await db.prepare("INSERT INTO portal_actions (id,student_id,kind,payload,created_at) VALUES (?,'S1','reports','{}',?)").bind('report'+i,new Date().toISOString()).run();
  const batch = (await call('/api/sync/actions?after=0',undefined,syncHeaders)).data;
  assert.equal(batch.actions.length,25); assert.equal(batch.has_more,true);
  assert.ok(batch.actions.find(a=>a.id==='missing').error);
  assert.ok((await call('/api/sync/actions?after='+batch.actions.at(-1).sequence,undefined,syncHeaders)).data.actions.length>0);
  // Password change survives stale export, then a newer campus reset wins.
  session = await call('/api/password',{current_password:'Initial password',password:'Personal cloud passphrase',confirmation:'Personal cloud passphrase'},headers);
  assert.equal(session.status,200);
  headers = {Cookie:session.cookie,'X-CSRF-Token':session.data.csrf};
  assert.equal((await publish()).status,200); generation++;
  assert.equal((await call('/api/me',undefined,headers)).status,200);
  students[0].account.password_hash=hash('Campus reset passphrase'); students[0].account.credential_version=2;
  assert.equal((await publish()).status,200); generation++;
  assert.equal((await call('/api/me',undefined,headers)).status,401);
  assert.equal((await call('/api/login',{username:'S1',password:'Campus reset passphrase'})).status,200);
  // An incomplete publication neither acknowledges a command nor changes snapshots.
  const bad = structuredClone(students); bad[0].snapshot.violations[0].has_snapshot=true; bad[0].snapshot.violations[0].evidence_sha256=hash('missing');
  assert.equal((await publish(bad,[{id:profile.data.id,status:'applied'}])).status,400);
  assert.equal((await db.prepare('SELECT status FROM portal_actions WHERE id=?').bind(profile.data.id).first()).status,'queued');
  assert.equal((await publish(students,[{id:profile.data.id,status:'applied'}])).status,200); generation++;
  assert.equal((await db.prepare('SELECT status FROM portal_actions WHERE id=?').bind(profile.data.id).first()).status,'applied');
  // Repair R2 loss even with the same checksum and verify publication ownership.
  const bytes = Buffer.from([255,216,255,1,2,3]); const digest=createHash('sha256').update(bytes).digest('hex');
  const evidence = {generation,student_id:'S1',violation_id:1,sha256:digest,file:bytes.toString('base64')};
  assert.equal((await call('/api/sync/evidence',evidence,syncHeaders)).data.sha256,digest);
  const bucket = await mf.getR2Bucket('BUCKET');
  await bucket.delete(`original-v2/S1/1/${digest}`);
  assert.equal((await call('/api/sync/evidence',evidence,syncHeaders)).data.sha256,digest);
  assert.ok(await bucket.get(`original-v2/S1/1/${digest}`));
  students[0].snapshot.violations[0].has_snapshot=true; students[0].snapshot.violations[0].evidence_sha256=digest;
  assert.equal((await publish()).status,200); generation++;
  const collision=structuredClone(students);collision[1].snapshot.violations[0].id=1;
  assert.equal((await publish(collision)).status,400);
  console.log('PASS: protocol/source gates, old clients, empty roster, racing/stale writers, auth/CSRF/isolation, profile bases, bounded queue, missing evidence, password reset, atomic ack, R2 repair, ID collision.');
} finally { await mf?.dispose(); await rm(directory,{recursive:true,force:true}); }
