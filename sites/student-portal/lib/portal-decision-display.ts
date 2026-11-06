/** Patch only the violation renderer, preserving deployed login/recovery assets. */
export const decisionHelpers = String.raw`
function appealEligibilityText(r){
 if(r.appeal_status==='rejected')return 'Your appeal was rejected. See the decision below.';
 if(r.appeal_status==='approved')return 'Your appeal was approved. See the decision below.';
 const reason=r.appeal_eligibility_reason;
 return reason==='already_submitted'?'Your appeal has already been submitted. Check My Appeals for updates.':reason||'View your appeal status for this record.';
}
function appealDecisionSummary(r){
 if(!['approved','rejected'].includes(r.appeal_status))return '';
 return '<section class="appeal-decision"><h4>Appeal '+(r.appeal_status==='rejected'?'rejected':'approved')+'</h4>'+
 '<p><strong>Decision category:</strong> '+escapeHTML(r.appeal_decision_category_label||'Category not recorded')+'</p>'+
 '<p><strong>Administrator reason:</strong> '+escapeHTML(r.appeal_admin_notes||'Explanation not recorded')+'</p>'+
 '<p class="muted">Decided by '+escapeHTML(r.appeal_decided_by||'Not recorded')+' · '+time(r.appeal_decided_at)+'</p></section>';
}
`;
export const notificationHelpers = String.raw`
function updateNotificationBadge(value){
 const button=document.querySelector('[data-page="notifications"]');if(!button)return;
 const count=Number.isSafeInteger(value)&&value>0?value:0;
 let badge=button.querySelector('[data-notification-badge]');
 if(!count){if(badge)badge.remove();button.setAttribute('aria-label','Notifications');return;}
 if(!badge){badge=document.createElement('span');badge.setAttribute('data-notification-badge','');badge.setAttribute('aria-hidden','true');
  badge.style.cssText='display:inline-block;margin-left:8px;min-width:19px;padding:2px 6px;border-radius:999px;background:#dc2626;color:#fff;font-size:12px;font-weight:700;text-align:center;line-height:1.4';button.appendChild(badge);}
 badge.textContent=count>99?'99+':String(count);
 button.setAttribute('aria-label','Notifications, '+count+' unread');
}
`;
export const liveHelpers = String.raw`
let portalLiveBusy=false,portalPageFingerprint='';
function portalFingerprint(data){return JSON.stringify([page,offset,semesterId,data],(key,value)=>['_synced_at','appeal_remaining_seconds'].includes(key)?undefined:value);}
function rememberPortalPage(data){portalPageFingerprint=portalFingerprint(data);if(data._synced_at)me.synced_at=data._synced_at;updatePortalSyncText();}
function updatePortalSyncText(){const label=document.querySelector('#page-subtitle');if(label&&page!=='settings')label.textContent='Records update automatically while campus is online. Last synced '+(me.synced_at?new Date(me.synced_at).toLocaleString():'not yet')+'.';}
async function pollPortalLive(){
 if(!me||me.must_change_password||document.hidden||busy||portalLiveBusy)return;
 const currentGeneration=generation,currentMe=me;
 const showPage=['dashboard','violations','notifications','appeals'].includes(page)&&!document.querySelector('form');
 portalLiveBusy=true;
 try{
  const path=showPage?'/api/page?page='+page+'&offset='+offset+'&semester_id='+encodeURIComponent(semesterId):'/api/page?page=notifications&offset=0';
  const data=await api(path);
  if(me!==currentMe||generation!==currentGeneration)return;
  updateNotificationBadge(data._unread_count);
  if(data._synced_at){me.synced_at=data._synced_at;updatePortalSyncText();}
  if(showPage&&!busy&&!document.querySelector('form')&&portalFingerprint(data)!==portalPageFingerprint)render(data);
 }catch{}finally{portalLiveBusy=false;}
}
setInterval(pollPortalLive,5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollPortalLive();});
window.addEventListener('focus',pollPortalLive);
`;
export function patchPortalScript(source: string): string {
  if(!source.includes('function appealDecisionSummary(')) {
  const start=source.indexOf('function violations(rows)');
  const end=source.indexOf('\nfunction render(',start);
  if(start<0||end<0)throw Error('Unsupported portal renderer; preserve live asset');
  let renderer=source.slice(start,end);
  const reason="r.appeal_eligibility_reason||'View your appeal status for this record.'";
  const actions='<div class="actions">';
  if(!renderer.includes(reason)||!renderer.includes(actions))throw Error('Portal renderer changed; review required');
  renderer=renderer.replace(reason,'appealEligibilityText(r)').replace(actions,'${appealDecisionSummary(r)}'+actions);
  source=source.slice(0,start)+renderer+source.slice(end)+'\n'+decisionHelpers;
  }
  if(!source.includes('function updateNotificationBadge(')) {
    const marker='function render(d){';
    if(!source.includes(marker))throw Error('Portal page renderer changed; review required');
    source=source.replace(marker,marker+'updateNotificationBadge(d._unread_count);')+'\n'+notificationHelpers;
  }
  if(!source.includes('async function pollPortalLive(')) {
    const oldPoll="setInterval(()=>{if(me&&!me.must_change_password&&['dashboard','violations','notifications','appeals'].includes(page)&&!document.querySelector('form')&&!busy)load();},30000);";
    if(!source.includes(oldPoll))throw Error('Portal refresh loop changed; review required');
    source=source.replace(oldPoll,'');
    source=source.replace(/let notificationBadgeBusy=false;\s*setInterval\(async\(\)=>\{[\s\S]*?\},30000\);/,'');
    source=source.replace('function render(d){','function render(d){rememberPortalPage(d);')+'\n'+liveHelpers;
  }
  return source;
}
