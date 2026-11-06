/** Exercise the exact prepared live-code wrapper with disposable D1 and credentials. */
import {createRequire} from 'node:module';
import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
import {pbkdf2Sync,createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const require = createRequire(new URL('../../../.runtime/cloud-tools/package.json',import.meta.url));
const {Miniflare} = require('miniflare');
const release = fileURLToPath(new URL('../../../.runtime/registration-release/',import.meta.url));
const assetSource = await readFile('public/app.js','utf8');
const mf = new Miniflare({rootPath:release,modulesRoot:release,modules:true,scriptPath:resolve(release,'worker.js'),
  compatibilityDate:'2026-05-15',compatibilityFlags:['nodejs_compat'],
  bindings:{CAMPUS_REGISTRATION_KEY:'fixture-only'},d1Databases:['DB'],r2Buckets:['BUCKET'],
  serviceBindings:{ASSETS:async()=>new Response(assetSource,{headers:{'Content-Type':'text/javascript','ETag':'fixture'}})},
  durableObjects:{PASSWORD_CRYPTO:{className:'PasswordCrypto',useSQLite:true}}});
try {
  const asset=await mf.dispatchFetch('https://portal.example/app.js');
  assert.equal(asset.status,200);
  assert.equal(asset.headers.get('Cache-Control'),'no-store');
  assert.equal(asset.headers.get('ETag'),null);
  assert.ok((await asset.text()).includes('function appealDecisionSummary('));
  const db = await mf.getD1Database('DB');
  for (const file of ['0000_furry_moira_mactaggert','0001_overrated_medusa','0002_sync_protocol','0016_campus_registration_owners','0017_campus_record_cursors']) {
    const sql = await readFile(`drizzle/${file}.sql`,'utf8');
    for (const statement of sql.replace(/^--.*$/gm,'').split(';').filter(s=>s.trim())) await db.prepare(statement).run();
  }
  const initial = 'Synthetic initial password';
  const hash = `pbkdf2_sha256$v1$600000$${'00'.repeat(16)}$${pbkdf2Sync(initial,Buffer.alloc(16),600000,32,'sha256').toString('hex')}`;
  const payload = {source_id:'00000000-0000-4000-8000-000000000001',lifecycle_version:0,active:true,
    profile:{student_id:'FIXTURE1',name:'Synthetic One'},username:'fixture-one',password_hash:hash,credential_version:1,must_change_password:true};
  const call = async (path, body, headers={}) => {
    const response = await mf.dispatchFetch('https://portal.example'+path, {method:body?'POST':'GET',
      headers:{Origin:'https://portal.example','Content-Type':'application/json',...headers},...(body?{body:JSON.stringify(body)}:{})});
    return {response,data:await response.json()};
  };
  assert.equal((await call('/api/campus/registrations',payload)).response.status,401);
  assert.equal((await call('/api/campus/registrations',payload,{Authorization:'Bearer fixture-only'})).response.status,201);
  assert.equal((await call('/api/login',{username:'fixture-one',password:'incorrect'})).response.status,401);
  const login = await call('/api/login',{username:'fixture-one',password:initial});
  assert.equal(login.response.status,200);
  const cookie = login.response.headers.get('Set-Cookie').split(';')[0];
  assert.match(login.response.headers.get('Set-Cookie'), /Secure/);
  const me = await call('/api/me',null,{Cookie:cookie});
  assert.equal(me.data.student_id,'FIXTURE1');
  assert.equal(me.data.must_change_password,true);
  assert.equal((await call('/api/page?page=dashboard',null,{Cookie:cookie})).response.status,403);
  const setup = await call('/api/setup/password',{password:'Synthetic changed password',confirmation:'Synthetic changed password'},
    {Cookie:cookie,'X-CSRF-Token':me.data.csrf});
  assert.equal(setup.response.status,200,JSON.stringify(setup.data));
  // Registration retries must preserve the student's website password change.
  await call('/api/campus/registrations',payload,{Authorization:'Bearer fixture-only'});
  const fresh = await call('/api/login',{username:'fixture-one',password:'Synthetic changed password'});
  assert.equal(fresh.response.status,200);
  const freshCookie = fresh.response.headers.get('Set-Cookie').split(';')[0];
  await call('/api/campus/registrations',{...payload,profile:{student_id:'FIXTURE2',name:'Synthetic Two'},username:'fixture-two'},{Authorization:'Bearer fixture-only'});
  const dashboard = await call('/api/page?page=dashboard&student_id=FIXTURE2',null,{Cookie:freshCookie});
  assert.equal(dashboard.data._student.student_id,'FIXTURE1');
  assert.equal(JSON.stringify(dashboard.data).includes('password_hash'),false);
  const snapshot={schema_version:3,profile:{student_id:'FIXTURE1',name:'Synthetic One'},academic_terms:[],term_history:{},
    violations:[{id:99,student_id:'FIXTURE1',status:'pending_review',violation:'Wrong uniform',has_snapshot:false,can_appeal:true,identity:'b'.repeat(64)}],notifications:[],appeals:[]};
  const records={student_id:'FIXTURE1',username:payload.username,source_id:payload.source_id,lifecycle_version:0,sequence:1,kind:'records',snapshot};
  const publish=(data=records,auth='fixture-only')=>call('/api/campus/records',data,{Authorization:'Bearer '+auth});
  assert.equal((await publish(records,'bad')).response.status,401);
  assert.equal((await publish({...records,source_id:'different-source'})).response.status,409);
  assert.equal((await publish({...records,snapshot:{...snapshot,profile:{student_id:'FIXTURE2'}}})).response.status,400);
  assert.equal((await publish()).data.status,'synced');
  assert.equal((await publish({...records,snapshot:{...snapshot,violations:[]}})).data.status,'unchanged');
  const cases=await call('/api/page?page=violations',null,{Cookie:freshCookie});
  assert.equal(cases.data._violations[0].id,99);
  assert.equal((await call('/api/evidence?id=99',null,{Cookie:cookie})).response.status,401); // superseded session
  const other=await db.prepare("SELECT snapshot FROM portal_students WHERE id='FIXTURE2'").first();
  assert.equal(JSON.parse(other.snapshot).violations.length,0);
  assert.equal((await publish({...records,sequence:2,snapshot:{...snapshot,violations:[{id:99,has_snapshot:true,evidence_sha256:'a'.repeat(64)}]}})).response.status,409);
  assert.equal((await publish({...records,sequence:2,snapshot:{...snapshot,profile:{...snapshot.profile,password_hash:'secret'}}})).response.status,400);
  const picture=Buffer.from('/9j/2Q==','base64');
  const digest=createHash('sha256').update(picture).digest('hex');
  const evidence={...records,kind:'evidence',violation_id:99,sha256:digest,file:picture.toString('base64')};
  assert.equal((await publish(evidence)).data.status,'evidence_verified');
  assert.equal((await publish({...records,sequence:2,snapshot:{...snapshot,violations:[{...snapshot.violations[0],has_snapshot:true,evidence_sha256:digest}]}})).data.status,'synced');
  const ownedPicture=await mf.dispatchFetch('https://portal.example/api/evidence?id=99',{headers:{Cookie:freshCookie}});
  assert.equal(ownedPicture.status,200);
  assert.deepEqual(Buffer.from(await ownedPicture.arrayBuffer()),picture);
  await db.prepare("UPDATE portal_accounts SET must_change_password=0 WHERE student_id='FIXTURE2'").run();
  const secondLogin=await call('/api/login',{username:'fixture-two',password:initial});
  const secondCookie=secondLogin.response.headers.get('Set-Cookie').split(';')[0];
  const denied=await mf.dispatchFetch('https://portal.example/api/evidence?id=99',{headers:{Cookie:secondCookie}});
  assert.equal(denied.status,404);
  assert.equal((await publish({...evidence,sha256:'0'.repeat(64)})).response.status,400);
  const currentMe=await call('/api/me',null,{Cookie:freshCookie});
  const appeal=await call('/api/appeals',{violation_id:99,reason:'Please review this original detection picture.',filename:'support.jpg',file:picture.toString('base64')},
    {Cookie:freshCookie,'X-CSRF-Token':currentMe.data.csrf});
  assert.equal(appeal.response.status,201,JSON.stringify(appeal.data));
  const pulled=await publish({...records,kind:'appeal_actions'});
  assert.equal(pulled.data.actions.length,1);
  assert.equal(pulled.data.actions[0].student_id,'FIXTURE1');
  assert.deepEqual(Buffer.from(pulled.data.actions[0].payload.file,'base64'),picture);
  assert.equal((await publish({...records,student_id:'FIXTURE2',username:'fixture-two',kind:'appeal_actions'})).data.actions.length,0);
  const actionId=pulled.data.actions[0].id;
  await publish({...records,student_id:'FIXTURE2',username:'fixture-two',kind:'appeal_receipt',action_id:actionId,status:'applied'});
  assert.equal((await publish({...records,kind:'appeal_actions'})).data.actions.length,1);
  assert.equal((await publish({...records,kind:'appeal_receipt',action_id:actionId,status:'applied'})).data.status,'acknowledged');
  assert.equal((await publish({...records,kind:'appeal_actions'})).data.actions.length,0);
  await call('/api/campus/registrations',{...payload,lifecycle_version:1,active:false},{Authorization:'Bearer fixture-only'});
  assert.equal((await call('/api/me',null,{Cookie:freshCookie})).response.status,401);
  assert.equal((await publish({...records,sequence:2})).response.status,409);
  console.log('PASS: exact release registration → login → required password setup → preserved changed password → student isolation → recycle revokes access.');
} finally {await mf.dispose();}
