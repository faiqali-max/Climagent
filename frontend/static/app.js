const API='';let TOKEN=localStorage.getItem('climagent_token');
async function api(path,opts={}){
  const h={'Content-Type':'application/json',...opts.headers};
  if(TOKEN)h['Authorization']='Bearer '+TOKEN;
  const r=await fetch(API+path,{...opts,headers:h});
  const o=await r.json().catch(()=>({}));
  if(r.status===401){TOKEN=null;localStorage.removeItem('climagent_token');location.href='/auth.html';throw new Error('Session expired');}
  if(!r.ok)throw new Error(o.error||'HTTP '+r.status);
  return o;
}
async function authed(){try{const u=await api('/api/auth/me');return u.user;}catch(e){return null;}}
function $(id){return document.getElementById(id)}
function showErr(id,m){const e=$(id);if(e){e.textContent=m;e.style.display='block';}}
function hideErr(id){const e=$(id);if(e)e.style.display='none';}
const fmtTs=ts=>ts?new Date(ts*1000).toLocaleString():'';
function renderMd(s){
  if(!s)return'';
  let h=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  h=h.replace(/^### (.+)$/gm,'<h3>$1</h3>');
  h=h.replace(/^## (.+)$/gm,'<h2>$1</h2>');
  h=h.replace(/^# (.+)$/gm,'h1>$1</h1>');
  h=h.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>');
  h=h.replace(/^- (.+)$/gm,'<li>$1</li>');
  h=h.replace(/\n/g,'<br>');
  return h;
}
// XSS-safe HTML escaping for interpolated user/agent content
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
// Marked rendering that neutralizes raw HTML (marks sanitizes only when we escape first)
function safeMarked(s){
  if(typeof marked==='undefined')return renderMd(s);
  return marked.parse(String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'),{breaks:true});
}
// ---------- Theme toggle (dark/light) ----------
function applyTheme(t){
  if(t==='light'||t==='dark'){document.documentElement.setAttribute('data-theme',t);localStorage.setItem('climagent_theme',t);}
}
function initTheme(){
  const saved=localStorage.getItem('climagent_theme');
  if(saved){applyTheme(saved);return;}
  if(window.matchMedia&&window.matchMedia('(prefers-color-scheme: light)').matches){applyTheme('light');}
  else{applyTheme('dark');}
}
function toggleTheme(){
  const cur=document.documentElement.getAttribute('data-theme');
  applyTheme(cur==='light'?'dark':'light');
}
// ---------- Mobile drawer ----------
function openDrawer(){const d=$('drawer');if(d){d.classList.add('open');}const o=$('drawer-overlay');if(o)o.classList.add('show');}
function closeDrawer(){const d=$('drawer');if(d){d.classList.remove('open');}const o=$('drawer-overlay');if(o)o.classList.remove('show');}
function initDrawer(){
  const b=$('burger');if(b)b.addEventListener('click',openDrawer);
  const o=$('drawer-overlay');if(o)o.addEventListener('click',closeDrawer);
  const d=$('drawer');
  if(d){
    const src=document.querySelector('.topbar nav');
    if(src&&!d.querySelector('nav')){
      const nav=document.createElement('nav');
      src.querySelectorAll('a.nav-link').forEach(a=>{nav.appendChild(a.cloneNode(true));});
      d.appendChild(nav);
    }
  }
}
function onLoadUI(){initTheme();initDrawer();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',onLoadUI);else onLoadUI();

// ---------- Ad-credit economy + Opt-in form (auto-responder) ----------
async function loadEarn(targetId, plan){
  try{
    const el=$(targetId);if(!el)return;
    if(plan&&(plan==='PREMIUM'||plan==='PROFESSIONAL')){el.innerHTML=`<div class="card"><h3>Ad-free plan</h3><p class="sub">Your plan includes ad-free, unlimited credits.</p></div>`;return;}
    const [c,ads]=await Promise.all([api('/api/credits'),api('/api/ads')]);
    const costs=c.costs||{};
    const costTxt=`Watch ${c.views_per_credit||2} ads = 1 credit &middot; 1 search = ${costs.search||1} cr &middot; 1 upload = ${costs.upload||2} cr &middot; 1 monitor run = ${costs.agent_run||1} cr`;
    const ad=(ads.ads||[])[0];
    el.innerHTML=`<div class="earn-card">
      <div>
        <div class="bal">${Number(c.balance)||0} <small>credits</small></div>
        <div class="sub" style="margin:4px 0 0">${costTxt}</div>
      </div>
      <div class="cta-row">
        ${ad?`<button class="primary" onclick="watchAd(${ad.id})">Watch ad +1 view</button>`:''}
        <button class="secondary" onclick="openOptin()">Opt-in to Real-Time Data</button>
      </div>
    </div>`;
  }catch(e){}
}
async function openOptin(){
  const el=$('optin-modal');if(!el)return;
  try{const st=await api('/api/opt-in/status');const s=st.status||{};
    $('optin-status').textContent=s.opted_in?('Status: '+s.status+' - '+s.coverage):'Not opted in yet.';
  }catch(e){}
  el.classList.add('open');
}
function closeOptin(){const el=$('optin-modal');if(el)el.classList.remove('open');}
async function watchAd(id){
  try{const r=await api('/api/ads/view',{method:'POST',body:JSON.stringify({ad_id:id})});
    const badge=$('plan-badge');const plan=badge?badge.dataset.plan:null;
    loadEarn('earn-card',plan);refreshCredits();
    if(r.credit_balance!=null){const c=r.progress&&r.progress.costs||{};alert('+1 credit! Balance: '+r.credit_balance+'. Costs: search '+c.search+' upload '+c.upload+' monitor run '+c.agent_run+'.');}
    else{alert('Please wait a moment before watching this ad again.');}
  }catch(e){alert(e.message);}
}
async function refreshCredits(){
  try{const c=await api('/api/credits');const badge=$('plan-badge');
    if(badge)badge.textContent=(badge.dataset.plan||'')+' | Credits: '+c.balance;
  }catch(e){}
}
async function submitOptin(){
  const types=Array.from(document.querySelectorAll('input[name="optin-types"]:checked')).map(x=>x.value);
  const consent=$('optin-consent');
  const err=$('optin-err');if(err)err.style.display='none';
  if(!consent||!consent.checked){if(err){err.textContent='Please agree to the consent checkbox.';err.style.display='block';}return;}
  if(!types.length){if(err){err.textContent='Select at least one data type.';err.style.display='block';}return;}
  try{
    const nameEL=$('optin-name'),emailEL=$('optin-email'),msgEL=$('optin-message');
    const r=await api('/api/opt-in',{method:'POST',body:JSON.stringify({
      name:nameEL?nameEL.value.trim():'',email:emailEL?emailEL.value.trim():'',
      consent:true,data_types:types,message:msgEL?msgEL.value.trim():''})});
    const s=r.status||{};
    const ok=$('optin-ok');if(ok)ok.textContent='Request received. Auto-responder: '+s.auto_response;
    if(err)err.style.display='none';
  }catch(e){if(err){err.textContent=e.message;err.style.display='block';}}
}
