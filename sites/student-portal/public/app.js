'use strict';
const app=document.querySelector('#app');
const labels={dashboard:'Dashboard',violations:'My Violations',notifications:'Notifications',appeals:'My Appeals',profile:'My Profile',settings:'User Settings',report:'System Report'};
let me=null,page='dashboard',offset=0,generation=0,busy=false,semesterId='';
const escapeHTML=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=v=>{if(!v)return '—';const raw=v.replace(' ','T');return new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(raw)?raw:raw+'Z').toLocaleString('en-PH',{timeZone:'Asia/Manila'})+' (Asia/Manila)';};
async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':me?.csrf||''}:{},body:data?JSON.stringify(data):undefined});if(!r.headers.get('Content-Type')?.includes('application/json'))throw Error('The portal could not complete this request. Please try again shortly.');const result=await r.json();if(!r.ok){if(r.status===401&&me){me=null;login();}throw Error(result.error||'Request failed');}return result;}
function toast(message){const el=document.querySelector('#toast');el.textContent=message;el.style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.style.display='none',4500);}
function preferences(){document.documentElement.dataset.dark=!!me?.preferences?.dark_mode;document.querySelector('.layout')?.classList.toggle('compact',!!me?.preferences?.compact_sidebar);}
function login(){generation++;if(new URLSearchParams(location.search).get('recovery')==='1'){recoveryRequest();return;}app.innerHTML=`<section class="login"><div class="card"><div class="seal">CBVMS</div><h1>Student Portal</h1><p class="muted">Your campus records, appeals, and updates in one place.</p>${loginNotice?`<p class="success" role="status">${escapeHTML(loginNotice)}</p>`:''}<form id="login"><label>Username<input name="username" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><p class="error" id="login-error" role="alert"></p><button class="btn">Sign in</button></form><a class="recovery-link" href="/?recovery=1">Forgot password?</a><p class="muted">Use your student username and password registered in CBVMS. New accounts become available after campus sync.</p></div></section>`;document.querySelector('#login').onsubmit=async e=>{e.preventDefault();const btn=e.target.querySelector('button');btn.disabled=true;try{me=await api('/api/login',Object.fromEntries(new FormData(e.target)));me=await api('/api/me');loginNotice='';routeAccount();}catch(err){document.querySelector('#login-error').textContent=err.message;}finally{btn.disabled=false;}};}
let recoveryChallenge=null,recoveryUsername='',recoveryTimer=null,loginNotice='';

async function recoveryApi(path, payload, fallback) {
 let response;
 try {
  response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
 } catch {
  throw Error('Recovery is temporarily unavailable. Check your internet connection and try again.');
 }
 if(!response.headers.get('Content-Type')?.includes('application/json'))
  throw Error('Recovery is temporarily unavailable. Please try again shortly.');
 let result;
 try { result=await response.json(); }
 catch { throw Error('Recovery is temporarily unavailable. Please try again shortly.'); }
 if(!response.ok) throw Error(result.error||fallback);
 return result;
}

function recoveryRequest(){
 generation++;recoveryChallenge=null;recoveryUsername='';
 app.innerHTML=`<section class="login"><div class="card"><div class="seal">CBVMS</div><h1>Reset your password</h1><p class="muted">Enter your student username or ID. If the account is eligible, a code will be sent to its registered email. Recovery needs internet and the campus SMTP computer online. Contact a campus administrator if you cannot access your verified email.</p><form id="recovery-request"><label>Username or student ID<input name="username" autocomplete="username" maxlength="200" required></label><p class="error" id="recovery-error" role="alert" aria-live="polite"></p><button class="btn" type="submit">Send recovery code</button></form><a class="recovery-link" href="/">Return to sign in</a></div></section>`;
 const form=document.querySelector('#recovery-request');
 form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;
  try {
   const username=form.elements.username.value.trim();
   const result=await recoveryApi('/api/recovery/request',{username},'Could not request a recovery code.');
   recoveryUsername=username;recoveryChallenge=result.challenge_id;recoveryCodeStep(result);
  } catch(error) { document.querySelector('#recovery-error')?.replaceChildren(document.createTextNode(error.message)); }
  finally { button.disabled=false; }
 };
}

function recoveryCodeStep(result){
 app.innerHTML=`<section class="login"><div class="card"><div class="seal">CBVMS</div><h1>Check your email</h1><p class="muted">${escapeHTML(result.message||'If this account is eligible for recovery, a code will be sent to its registered email.')} Enter the 6-digit verification code. It expires in 10 minutes.</p><details class="recovery-help"><summary>No code received?</summary><p class="muted">Recovery needs a previously verified email. If you are still signed in, verify your saved address under Profile → Recovery email. Otherwise contact a campus administrator.</p></details><form id="recovery-verify"><label>6-digit verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" required></label><p class="muted" id="recovery-status" role="status" aria-live="polite"></p><p class="error" id="recovery-error" role="alert" aria-live="polite"></p><button class="btn" type="submit">Verify code</button><button type="button" class="btn secondary" id="recovery-resend" disabled>Resend code</button></form><a class="recovery-link" href="/">Return to sign in</a></div></section>`;
 const form=document.querySelector('#recovery-verify'),resend=document.querySelector('#recovery-resend');
 form.elements.code.addEventListener('input',event=>{event.target.value=event.target.value.replace(/\D/g,'').slice(0,6);});
 form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;
  try {
   const result=await recoveryApi('/api/recovery/verify',{challenge_id:recoveryChallenge,code:form.elements.code.value},'Could not verify this code.');
   recoveryChallenge=null;recoveryUsername='';recoveryPassword(result);
  } catch(error) { const message=document.querySelector('#recovery-error');if(message)message.textContent=error.message;button.disabled=false; }
 };
 resend.onclick=async()=>{
  resend.disabled=true;
  try {
   const result=await recoveryApi('/api/recovery/request',{username:recoveryUsername},'Could not resend the code.');
   recoveryChallenge=result.challenge_id;const error=document.querySelector('#recovery-error');if(error)error.textContent='';const status=document.querySelector('#recovery-status');if(status)status.textContent=result.message||'';
   startRecoveryCooldown(result.resend_after,resend);
  } catch(error) { const message=document.querySelector('#recovery-error');if(message)message.textContent=error.message;resend.disabled=false; }
 };
 if(!recoveryUsername){resend.disabled=false;resend.textContent='Request a new code';resend.onclick=()=>{history.replaceState(null,'','/?recovery=1');recoveryRequest();};}
 else startRecoveryCooldown(result.resend_after,resend);
}

function startRecoveryCooldown(seconds,button){
 clearInterval(recoveryTimer);let remaining=Math.max(1,Number(seconds)||60);button.disabled=true;button.textContent=`Resend code in ${remaining}s`;
 recoveryTimer=setInterval(()=>{remaining--;if(remaining<=0){clearInterval(recoveryTimer);button.disabled=false;button.textContent='Resend code';}else button.textContent=`Resend code in ${remaining}s`;},1000);
}

function recoveryPassword(result){
 clearInterval(recoveryTimer);const grant=result.grant;
 app.innerHTML=`<section class="login"><div class="card"><div class="seal">CBVMS</div><h1>Choose a new password</h1><p class="muted">Use at least 8 characters. Passwords can be up to 1024 characters. Your reset authorization expires in 5 minutes.</p><form id="recovery-complete"><label>New password<span class="password-row"><input name="password" type="password" minlength="8" maxlength="1024" autocomplete="new-password" required><button type="button" class="btn secondary" data-recovery-show="password" aria-label="Show new password">Show</button></span></label><label>Confirm new password<span class="password-row"><input name="confirmation" type="password" minlength="8" maxlength="1024" autocomplete="new-password" required><button type="button" class="btn secondary" data-recovery-show="confirmation" aria-label="Show password confirmation">Show</button></span></label><p class="error" id="recovery-error" role="alert" aria-live="polite"></p><button class="btn" type="submit">Save new password</button></form><a class="recovery-link" href="/">Return to sign in</a></div></section>`;
 const form=document.querySelector('#recovery-complete');
 form.querySelectorAll('[data-recovery-show]').forEach(button=>button.onclick=()=>{const field=form.elements[button.dataset.recoveryShow];field.type=field.type==='password'?'text':'password';button.textContent=field.type==='password'?'Show':'Hide';button.setAttribute('aria-label',field.type==='password'?(button.dataset.recoveryShow==='password'?'Show new password':'Show password confirmation'):(button.dataset.recoveryShow==='password'?'Hide new password':'Hide password confirmation'));});
 form.onsubmit=async event=>{
  event.preventDefault();const data=Object.fromEntries(new FormData(form));
  if(data.password!==data.confirmation){document.querySelector('#recovery-error').textContent='Passwords do not match.';return;}
  const button=form.querySelector('button[type="submit"]');button.disabled=true;
  try {
   await recoveryApi('/api/recovery/complete',{...data,grant},'Could not reset the password.');
   history.replaceState(null,'',location.pathname);loginNotice='Password updated. Sign in with your new password.';recoveryChallenge=null;login();
  } catch(error) { const message=document.querySelector('#recovery-error');if(message)message.textContent=error.message;button.disabled=false; }
 };
}
function routeAccount(){generation++;if(me.must_change_password)setupPassword();else shell();}
function setupPassword(){
 const setupGeneration=++generation;
 document.documentElement.dataset.dark=false;
 app.innerHTML=`<section class="setup-layout"><aside class="setup-brand"><div class="seal">C</div><h1>CBVMS</h1><p>CAMPUS ENTRANCE MONITORING</p><h2>A smarter view.<br>A safer campus.</h2><p>Computer Based Vision<br>Monitoring System</p><strong>One campus. Connected.</strong></aside><main class="setup-form"><p class="eyebrow">YOUR CAMPUS WORKSPACE</p><h1>Set your new password</h1><p class="muted">Before entering your student portal, replace your initial or temporary password with a password only you know.</p><form id="setup">${['New password','Confirm new password'].map((label,i)=>`<label>${label}<span class="password-row"><input name="${i?'confirmation':'password'}" type="password" minlength="8" autocomplete="new-password" aria-describedby="password-rules" required><button type="button" class="btn secondary" data-show="${i?'confirmation':'password'}">Show</button></span></label>`).join('')}<p class="muted" id="password-rules">Use at least 8 characters. Longer passphrases are welcome. Choose a different password; default credentials and account IDs are not allowed.</p><p class="error" id="setup-error" role="alert" aria-live="polite"></p><button class="btn" id="save-password">Save Password &amp; Continue</button><button class="btn secondary" type="button" id="setup-logout">Back to Login</button></form></main></section>`;
 const form=document.querySelector('#setup'),error=document.querySelector('#setup-error'),save=document.querySelector('#save-password');
 form.querySelectorAll('[data-show]').forEach(button=>button.onclick=()=>{const field=form.elements[button.dataset.show];field.type=field.type==='password'?'text':'password';button.textContent=field.type==='password'?'Show':'Hide';});
 form.onsubmit=async e=>{e.preventDefault();if(save.disabled)return;const data=Object.fromEntries(new FormData(form));if(data.password!==data.confirmation){error.textContent='New password and confirmation do not match.';return;}save.disabled=true;save.textContent='Saving password…';error.textContent='';try{const saved=await api('/api/setup/password',data);if(generation!==setupGeneration)return;me=saved;form.reset();const current=await api('/api/me');if(generation!==setupGeneration)return;me=current;routeAccount();}catch(err){error.textContent=err.message;}finally{save.disabled=false;save.textContent='Save Password & Continue';}};
 document.querySelector('#setup-logout').onclick=async()=>{generation++;try{await api('/api/logout',{});me=null;login();}catch(err){error.textContent=err.message;}};
}
function shell(){if(me?.must_change_password){setupPassword();return;}app.innerHTML=`<div class="layout"><aside><h1>CBVMS</h1><p class="brand-note">Student Entrance Camera-based Uniform,<br>Grooming, Accessory Recognition and Evaluation</p><div class="identity">${escapeHTML(me.name)}</div><div class="eyebrow">STUDENT WORKSPACE</div><nav>${Object.entries(labels).map(([id,label])=>`<button data-page="${id}">${label}</button>`).join('')}</nav><button class="logout">↪ Logout</button></aside><main><header class="welcome"><div class="seal">CBVMS</div><div><h2>Welcome, ${escapeHTML(me.name)}.</h2><p>Your campus workspace is ready.</p></div><span class="tag">STUDENT</span></header><div class="heading"><h2 id="page-title"></h2><button class="btn secondary" id="refresh">Refresh</button></div><p class="muted" id="page-subtitle"></p><section id="content" aria-live="polite"></section></main></div>`;document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{page=b.dataset.page;offset=0;load();});document.querySelector('.logout').onclick=async()=>{try{await api('/api/logout',{});me=null;login();}catch(e){toast(e.message);}};document.querySelector('#refresh').onclick=load;preferences();load();}
async function load(){const id=++generation;document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));document.querySelector('#page-title').textContent=labels[page];document.querySelector('#page-subtitle').textContent=page==='settings'?'Customize your portal appearance and preferences.':`Campus records last synced ${me.synced_at?new Date(me.synced_at).toLocaleString():'not yet'}. Requests are saved online and reach campus when its PC reconnects.`;const content=document.querySelector('#content');content.innerHTML='<div class="card muted">Loading your records…</div>';try{const data=await api(`/api/page?page=${page}&offset=${offset}&semester_id=${encodeURIComponent(semesterId)}`);if(id!==generation)return;render(data);}catch(e){if(id===generation&&me)content.innerHTML=`<div class="card error">${escapeHTML(e.message)} <button class="btn secondary" id="retry">Try again</button></div>`;document.querySelector('#retry')?.addEventListener('click',load);}}
function violations(rows){window.__caseDisplayIds=Object.fromEntries(rows.map(r=>[r.id,r.campus_record_id??r.id]));return rows.map(r=>`<article class="card"><div class="record-head"><h3>${escapeHTML(r.violation_label||r.violation_type)} <small class="muted">#${r.campus_record_id??r.id}</small></h3><span class="pill">${escapeHTML(r.appeal_status||r.status)}</span></div><p class="muted">Detected ${time(r.timestamp)} · ${escapeHTML(r.semester_name)} ${escapeHTML(r.school_year)}</p><p class="muted">Appeal deadline: ${time(r.appeal_deadline)}</p><p>${r.can_appeal?'You may appeal this record with an explanation and picture.':escapeHTML(r.appeal_eligibility_reason||'View your appeal status for this record.')}</p><div class="actions"><button class="btn secondary" data-evidence="${r.id}">View detection picture</button>${r.can_appeal?`<button class="btn" data-appeal="${r.id}">Submit appeal</button>`:''}</div><div id="evidence-${r.id}"></div><div id="appeal-${r.id}"></div></article>`).join('')||'<div class="card empty">No violations to show.</div>';}
function render(d){const c=document.querySelector('#content');let html='';
if(page==='dashboard'){const counts=d._violation_counts||{};html=`<div class="stats">${[['Pending records',counts.pending||0],['Confirmed records',counts.confirmed||0],['Pending appeals',d._pending_appeals||0]].map(([title,n])=>`<div class="card"><div class="muted">${title}</div><div class="number">${n}</div></div>`).join('')}</div><div class="card"><h3>Current semester standing</h3>${(d._strike_summary||[]).map(s=>`<div class="row"><strong>${escapeHTML(s.violation_label)}</strong><span>${s.active_count} / ${s.strike_limit} strikes</span></div>`).join('')}<p class="muted">${d._active_suspension?`Suspended until ${time(d._active_suspension.ends_at)}. ${escapeHTML(d._active_suspension.reason)}`:'No active suspension.'}</p></div><h3>Recent violations</h3>${violations(d._violations||[])}`;}
if(page==='violations')html=violations(d._violations||[]);
if(page==='notifications')html=`<button class="btn secondary" id="read-all">Mark all as read</button>`+(d._notifications||[]).map(n=>`<article class="card"><div class="record-head"><h3>${escapeHTML(n.title)}</h3><span class="pill">${n.is_read?'Read':'Unread'}</span></div><p>${escapeHTML(n.message)}</p><p class="muted">${time(n.created_at)}</p>${!n.is_read?`<button class="btn secondary" data-read="${n.id}">Mark as read</button>`:''}</article>`).join('');
if(page==='appeals')html=(d._appeals||[]).map(a=>`<article class="card"><div class="record-head"><h3>Appeal for violation #${a.campus_violation_id??a.violation_id}</h3><span class="pill">${escapeHTML(a.status)}</span></div><p>${escapeHTML(a.reason)}</p><p class="muted">Submitted ${time(a.submitted_at)}</p>${['approved','rejected'].includes(a.status)?`<p><strong>Decision category:</strong> ${escapeHTML(a.decision_category_display)}</p><p><strong>Admin response:</strong> ${escapeHTML(a.admin_notes||'Explanation not recorded')}</p><p class="muted">Decided by ${escapeHTML(a.decided_by||'Not recorded')} · ${time(a.decided_at)}</p>`:''}</article>`).join('')||'<div class="card empty">No appeals submitted.</div>';
if(page==='settings')html=`<div class="card"><h3>Appearance</h3>${toggle('dark_mode','Dark mode','Use a darker color scheme across the portal')}${toggle('compact_sidebar','Compact sidebar','Reduce sidebar spacing for more workspace')}</div>`;
if(page==='profile'){const s=d._student||{};html=`<div class="card"><h3>Student details</h3><dl class="details">${['student_id','name','college_department','course','report_year_level','report_section','email','student_status'].map(k=>`<dt>${escapeHTML(k.replaceAll('_',' '))}</dt><dd>${escapeHTML(s[k]||'—')}</dd>`).join('')}</dl></div><div class="card"><h3>Display name</h3><form id="profile"><label>Name<input name="name" value="${escapeHTML(s.name)}" maxlength="120" required></label><button class="btn">Save name</button></form></div><div class="card"><h3>Change password</h3><form id="password"><label>Current password<input name="current_password" type="password" autocomplete="current-password" required></label><label>New password<input name="password" type="password" minlength="8" autocomplete="new-password" required></label><label>Confirm new password<input name="confirmation" type="password" minlength="8" autocomplete="new-password" required></label><p class="muted">Use a different password with at least 8 characters. Default credentials and account IDs are not allowed.</p><button class="btn">Change password</button></form></div><div class="card"><h3>Recovery email</h3><p id="recovery-email-status" class="muted">Loading your recovery email status…</p><form id="recovery-email"><label>Email address<input name="email" type="email" autocomplete="email" maxlength="254" required></label><label>Current password<input name="current_password" type="password" autocomplete="current-password" required></label><p class="muted">We will send a verification code. You must verify this address before it can recover your account.</p><p class="error" id="recovery-email-error" role="alert" aria-live="polite"></p><button class="btn">Verify recovery email</button></form><div id="recovery-email-verify"></div></div>`;}
if(page==='report')html=`<div class="card"><h3>Send a report to the administrator</h3><form id="report"><label>Title<input name="title" maxlength="200" required></label><label>Category<select name="category">${['System Bug','Account Issue','Violation Dispute','Other'].map(v=>`<option>${v}</option>`).join('')}</select></label><label>Description<textarea name="description" maxlength="10000" required></textarea></label><button class="btn">Submit report</button></form></div>`;
if(['violations','notifications','appeals'].includes(page))html+=`<div class="pager">${offset?'<button class="btn secondary" id="previous">Previous</button>':''}${d._has_more?'<button class="btn secondary" id="next">Next</button>':''}</div>`;
if(['dashboard','violations','appeals','profile'].includes(page)) {
 const terms=d._academic_terms||[];
 const history=d._term_history||{};
 const filter=`<label>Report term<select id="term-filter"><option value="">All Terms</option><option value="legacy">Legacy / Unassigned</option>${terms.filter(t=>t.semester_code!=='legacy').map(t=>`<option value="${t.id}" ${String(t.id)===semesterId?'selected':''}>${escapeHTML(t.school_year)} — ${escapeHTML(t.semester_name)}</option>`).join('')}</select></label>`;
 html=filter+`<p class="pill">${escapeHTML(d._enrollment_status)}</p>`+html;
 if(page==='profile') html+=`<div class="card"><h3>Enrollment history</h3><p>Lifetime strikes: ${history.lifetime_issued||0} issued; ${history.lifetime_valid||0} currently valid.</p>${(history.enrollments||[]).map(e=>`<p>${escapeHTML(e.school_year)} · ${escapeHTML(e.semester_name)} · ${escapeHTML(e.status)} · ${escapeHTML(e.course)} · ${escapeHTML(e.report_year_level)} ${escapeHTML(e.report_section)}</p>`).join('')||'<p>Historical enrollment is unverified. Contact the superadmin for fresh-capture enrollment.</p>'}</div>`;
 if(page==='profile') {
  const termName=id=>{const term=terms.find(t=>t.id===id);return term?term.school_year+' · '+term.semester_name:'Legacy / Unassigned';};
  html+=`<div class="card"><h3>Discipline by term</h3>${(history.strikes_by_term||[]).map(s=>`<p>${escapeHTML(termName(s.semester_id))}: ${s.issued} issued; ${s.valid} currently valid.</p>`).join('')||'<p>No strikes recorded.</p>'}</div>`;
  html+=`<div class="card"><h3>Suspension history</h3>${(d._suspension_history||[]).map(s=>`<p>${time(s.starts_at)} — scheduled end ${time(s.ends_at)}<br>${escapeHTML(s.reason)}${s.effective_ended_at?`<br>Effective end: ${time(s.effective_ended_at)} · ${escapeHTML(s.effective_end_reason)}`:''}${s.lifted_at?`<br>Lifted: ${time(s.lifted_at)} · ${escapeHTML(s.lift_reason)}`:''}</p>`).join('')||'<p>No suspensions recorded.</p>'}</div>`;
  html+=`<div class="card"><h3>Enrollment reviews</h3>${(history.enrollment_reviews||[]).map(r=>`<p>${time(r.observed_at)} · ${escapeHTML(r.enrollment_status)}<br>${escapeHTML(r.resolution_reason||'Awaiting superadmin review')}</p>`).join('')||'<p>No enrollment reviews recorded.</p>'}</div>`;
 }
}
c.innerHTML=html;bind();if(page==='profile')bindRecoveryEmail();
const termFilter=document.querySelector('#term-filter');if(termFilter){termFilter.value=semesterId;termFilter.onchange=()=>{semesterId=termFilter.value;offset=0;load();};}}
function toggle(key,title,description){return `<label class="row"><span><strong>${title}</strong><p class="muted">${description}</p></span><input type="checkbox" data-pref="${key}" ${me.preferences?.[key]?'checked':''}></label>`;}
async function bindRecoveryEmail(){
 const status=document.querySelector('#recovery-email-status'),form=document.querySelector('#recovery-email'),error=document.querySelector('#recovery-email-error');
 try {
  const current=await api('/api/recovery/email');if(!status.isConnected)return;
  status.textContent=current.verified?`Verified recovery address: ${current.email}`:'No verified recovery email is set.';
  if(!current.verified&&current.registered_email){
   const option=document.createElement('div');option.id='registered-email-option';
   const help=document.createElement('p');help.className='muted';
   help.textContent=`Forgot your current password? Your signed-in session can verify the email already saved on your student record: ${current.registered_email}`;
   const button=document.createElement('button');button.type='button';button.className='btn secondary';button.textContent='Verify my saved email';
   option.append(help,button);status.after(option);
   button.onclick=async()=>{
    button.disabled=true;error.textContent='';
    try{const challenge=await api('/api/recovery/email/request',{use_registered_email:true});recoveryEmailVerify(challenge,{use_registered_email:true});}
    catch(err){error.textContent=err.message;}
    finally{button.disabled=false;}
   };
  }
 }
 catch(error){if(status.isConnected)status.textContent=error.message;}
 form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;error.textContent='';
  const fields=Object.fromEntries(new FormData(form));
  try {
   const challenge=await api('/api/recovery/email/request',fields);
   // Keep the reauthentication value only in this short-lived resend closure.
   // Remove it from the visible form immediately after the first request.
   form.elements.current_password.value='';
   const resendFields={email:fields.email,current_password:fields.current_password};fields.current_password='';
   recoveryEmailVerify(challenge,resendFields);
  } catch(err){error.textContent=err.message;}
  finally{button.disabled=false;}
 };
}

function recoveryEmailVerify(challenge,fields){
 const host=document.querySelector('#recovery-email-verify');if(!host)return;
 host.innerHTML=`<form id="recovery-email-code"><label>Verification code<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" required></label><p class="error" id="recovery-email-code-error" role="alert" aria-live="polite"></p><button class="btn" type="submit">Confirm recovery email</button><button type="button" id="recovery-email-resend" class="btn secondary" disabled>Resend code</button></form>`;
 const form=document.querySelector('#recovery-email-code'),resend=document.querySelector('#recovery-email-resend');
 form.elements.code.addEventListener('input',event=>{event.target.value=event.target.value.replace(/\D/g,'').slice(0,6);});
 form.onsubmit=async event=>{
  event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;
  try {
   await api('/api/recovery/email/verify',{challenge_id:challenge.challenge_id,code:form.elements.code.value});
   fields.current_password='';
   clearInterval(recoveryTimer);host.innerHTML='';document.querySelector('#recovery-email').reset();
   document.querySelector('#registered-email-option')?.remove();
   const resetLink=document.createElement('a');resetLink.href='/?recovery=1';resetLink.className='recovery-link';resetLink.textContent='Reset your forgotten password';host.append(resetLink);
   const status=document.querySelector('#recovery-email-status');status.textContent='Recovery email verified.';
   try { const data=await api('/api/recovery/email');status.textContent=data.verified?`Verified recovery address: ${data.email}`:'Recovery email verified.'; }
   catch { /* Verification already succeeded; status can be refreshed by reopening Profile. */ }
  } catch(error){document.querySelector('#recovery-email-code-error').textContent=error.message;button.disabled=false;}
 };
 resend.onclick=async()=>{
  resend.disabled=true;
  try { const next=await api('/api/recovery/email/request',fields);recoveryEmailVerify(next,fields); }
  catch(error){document.querySelector('#recovery-email-code-error').textContent=error.message;resend.disabled=false;}
 };
 startRecoveryCooldown(challenge.resend_after,resend);
}
function bind(){document.querySelector('#next')?.addEventListener('click',()=>{offset+=10;load();});document.querySelector('#previous')?.addEventListener('click',()=>{offset=Math.max(0,offset-10);load();});document.querySelectorAll('[data-pref]').forEach(el=>el.onchange=async()=>{try{me.preferences=await api('/api/preferences',{[el.dataset.pref]:el.checked});preferences();}catch(e){el.checked=!el.checked;toast(e.message);}});document.querySelectorAll('[data-read]').forEach(el=>el.onclick=()=>action('/api/notifications/read',{id:Number(el.dataset.read)}));document.querySelector('#read-all')?.addEventListener('click',()=>action('/api/notifications/read',{}));document.querySelectorAll('[data-evidence]').forEach(el=>el.onclick=async()=>{try{const r=await fetch(`/api/evidence?id=${el.dataset.evidence}`);if(!r.ok)throw Error('Picture unavailable.');const url=URL.createObjectURL(await r.blob());const img=document.createElement('img');img.className='evidence';img.alt='Original detection evidence';img.onload=()=>URL.revokeObjectURL(url);img.src=url;document.querySelector(`#evidence-${el.dataset.evidence}`).replaceChildren(img);}catch(e){toast(e.message);}});document.querySelectorAll('[data-appeal]').forEach(el=>el.onclick=()=>appealForm(Number(el.dataset.appeal)));for(const name of ['report','profile','password']){const f=document.querySelector('#'+name);if(f)f.onsubmit=async e=>{e.preventDefault();await action('/api/'+(name==='report'?'reports':name),Object.fromEntries(new FormData(f)),name==='profile');};}}
async function action(path,data,rebuild=false){if(busy)return;busy=true;try{const result=await api(path,data);if(path==='/api/password')me={...me,...result};toast(result.queued?'Saved online. Awaiting campus sync.':'Saved successfully.');if(rebuild){me=await api('/api/me');routeAccount();}else await load();}catch(e){toast(e.message);}finally{busy=false;}}
function appealForm(id){const displayId=window.__caseDisplayIds?.[id]??id;const host=document.querySelector(`#appeal-${id}`);host.innerHTML=`<form class="card"><h3>Appeal violation #${displayId}</h3><label>Explanation<textarea name="reason" minlength="20" maxlength="1000" required></textarea></label><label>Supporting picture (JPG, PNG, BMP; up to 10 MB)<input name="picture" type="file" accept=".jpg,.jpeg,.png,.bmp" required></label><button class="btn">Submit appeal</button></form>`;host.querySelector('form').onsubmit=async e=>{e.preventDefault();const f=e.target,btn=f.querySelector('button'),file=f.elements.picture.files[0];if(!file||file.size>10*1024*1024){toast('Choose a picture up to 10 MB.');return;}btn.disabled=true;try{const encoded=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});await api('/api/appeals',{violation_id:id,reason:f.elements.reason.value,filename:file.name,file:encoded});toast('Appeal saved online. Awaiting campus sync for administrator review.');await load();}catch(e){toast(e.message);btn.disabled=false;}};}
function resumeRecoveryChallenge(){
 const resume=new URLSearchParams(location.hash.slice(1)).get('challenge');
 if(!resume||!/^[0-9a-f]{64}$/.test(resume))return false;
 clearInterval(recoveryTimer);recoveryChallenge=resume;recoveryUsername='';recoveryCodeStep({resend_after:60});return true;
}
async function start(){if(new URLSearchParams(location.search).get('recovery')==='1'){
 if(!resumeRecoveryChallenge())recoveryRequest();return;}try{me=await api('/api/me');routeAccount();}catch{login();}}
window.addEventListener('hashchange',()=>{if(new URLSearchParams(location.search).get('recovery')==='1')resumeRecoveryChallenge();});
start();
setInterval(()=>{if(me&&!me.must_change_password&&['dashboard','violations','notifications','appeals'].includes(page)&&!document.querySelector('form')&&!busy)load();},30000);
