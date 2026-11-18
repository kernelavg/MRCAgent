'use strict';
const byId = id => document.getElementById(id);
const state = {user:null, projects:[], selected:'', detail:null, events:[], cursor:0, timer:null, busy:false, drafts:new Map()};
const statusText = {idle:'Ожидает', running:'Работает', paused:'Пауза', done:'Завершён', error:'Ошибка', stopped:'Остановлен', working:'Работает', cancelled:'Отменена', interrupted:'Прервана'};
function node(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className=cls; if (text !== undefined) n.textContent=String(text); return n; }
function show(id, yes) { byId(id).classList.toggle('hidden', !yes); }
function notify(text) { const n=byId('toast'); n.textContent=text; n.classList.remove('hidden'); clearTimeout(notify.timer); notify.timer=setTimeout(()=>n.classList.add('hidden'),3500); }
async function api(path, method='GET', body=null) {
  const opt={method,credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}};
  if (body !== null) { opt.headers['Content-Type']='application/json'; opt.body=JSON.stringify(body); }
  const res=await fetch('/api/'+path,opt); let data;
  try { data=await res.json(); } catch(e) { throw new Error('Сервер вернул неверный ответ'); }
  if (!res.ok || data.ok === false) throw new Error(data.error || data.reason || 'Ошибка сервера');
  return data;
}
function connected(ok) { const c=byId('connection'); c.classList.toggle('off',!ok); c.lastChild.textContent=ok?' На связи':' Нет связи'; }
function renderProjects() {
  const box=byId('projects'); box.replaceChildren();
  byId('totalProjects').textContent=state.projects.length;
  byId('activeProjects').textContent=state.projects.filter(p=>p.status==='running').length;
  if (!state.projects.length) { box.append(node('div','empty','Проектов пока нет. Создайте проект на компьютере.')); return; }
  for (const p of state.projects) {
    const b=node('button','project'+(p.id===state.selected?' active':'')); b.type='button'; b.append(node('span','project-icon','◫'));
    const info=node('span','project-info'); info.append(node('b','',p.name || 'Проект'),node('small','',(statusText[p.status] || p.status) + ' · ' + (p.agents_done || 0) + ' / ' + (p.agents_total || 0) + ' агентов')); b.append(info,node('span','project-arrow','↗'));
    b.addEventListener('click',async()=>{ state.selected=p.id;state.cursor=0;state.events=[];renderProjects();await refreshProject(); }); box.append(b);
  }
}
function digitalAvatar(agent) {
  let seed=0; for (const c of String(agent.id || agent.nick || '')) seed=(seed+ c.charCodeAt(0)) % 10000;
  const color=['#70e6d2','#b4a3ff','#ffbf7e','#82c9ff','#f493c0','#e5dd86'][seed % 6];
  const box=node('span','avatar'); const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg');
  svg.setAttribute('viewBox','0 0 64 64');svg.setAttribute('shape-rendering','crispEdges');svg.setAttribute('aria-hidden','true');
  const parts=[['M16 18h32v6h5v22h-6v6H17v-6h-6V24h5z',color],['M29 9h6v10h-6zM27 7h10v5H27z',color],['M21 29h7v7h-7zM36 29h7v7h-7zM26 41h12v3H26z','#18233d']];
  for(const item of parts){const path=document.createElementNS(ns,'path');path.setAttribute('d',item[0]);path.setAttribute('fill',item[1]);svg.append(path);}box.append(svg);return box;
}
function renderAgents(agents) {
  const box=byId('agents'); box.replaceChildren();
  if (!agents.length) { box.append(node('div','empty','Агентов пока нет'));return; }
  for (const a of agents) { const c=node('article','agent'); const top=node('div','agent-top');top.append(digitalAvatar(a),node('b','',a.nick || 'Агент')); c.append(top,node('small','',a.role || ''),node('div','agent-status'+(a.status==='error'?' error':''), '● ' + (statusText[a.status] || a.status || 'Ожидает')));box.append(c); }
}
function renderEvents() {
  const box=byId('events');box.replaceChildren();
  const list=state.events.slice(-25).reverse();
  if (!list.length) {box.append(node('div','caption','События появятся после запуска агентов.'));return;}
  for (const e of list) {const row=node('div','event');row.append(node('i','','◦'),node('span','',e.text || e.type));box.append(row);}
}
function renderBuild(builds) {
  const build=builds[0];const box=byId('artifacts');box.replaceChildren();
  byId('buildStatus').textContent=build ? ((statusText[build.status] || build.status) + ' · ' + build.target) : 'Нет сборок';
  byId('buildLog').textContent=build ? ((build.log || '') + '\n' + (build.message || '')).trim() || 'Журнал пока пуст' : 'Ожидание сборки';
  if (!build || !build.artifacts || !build.artifacts.length) return;
  const pid=state.selected;
  api('preview-token/'+encodeURIComponent(pid)).then(data=>{if(state.selected!==pid)return;for(const path of build.artifacts){const a=node('a','','Скачать · '+path);a.href=data.base.replace('/preview/','/download/')+path.split('/').map(encodeURIComponent).join('/');a.setAttribute('download','');box.append(a);}}).catch(()=>{});
}
function renderQuestions(list) {
  const active=list.filter(q=>q.status==='pending' && q.deadline>Date.now()/1000);
  byId('pendingCount').textContent=active.length;byId('questionBadge').textContent=active.length;
  const box=byId('questions');
  const ids=active.map(q=>q.id).join('|');
  if (box.dataset.ids===ids && box.children.length) return;
  box.dataset.ids=ids;box.replaceChildren();
  if (!active.length) {box.append(node('div','empty','Сейчас вопросов нет. Агент ждёт ответ до двух минут.'));return;}
  for(const q of active){
    const card=node('article','panel question');card.append(node('span','eyebrow','АГЕНТ ЖДЁТ ОТВЕТ'),node('h3','',q.question),node('p','',(q.agent_name || 'Агент')+' · '+(q.project_name || 'Проект')));
    if(q.suggestion)card.append(node('p','','Предложение: '+q.suggestion));
    const area=node('textarea');area.placeholder='Напишите свой ответ';area.setAttribute('aria-label','Свой ответ');area.value=state.drafts.get(q.id) || '';area.addEventListener('input',()=>state.drafts.set(q.id,area.value));card.append(area);
    const controls=node('div','controls'); const send=node('button','primary','Отправить ответ');send.type='button';
    const answer=async value=>{if(!value.trim()){notify('Введите ответ');return;}try{await api('questions/'+encodeURIComponent(q.id)+'/answer','POST',{answer:value});state.drafts.delete(q.id);notify('Ответ отправлен');await refresh();}catch(e){notify(e.message);}};
    send.addEventListener('click',()=>answer(area.value));controls.append(send);
    if(q.suggestion){const accept=node('button','soft','Подтвердить вариант');accept.type='button';accept.addEventListener('click',()=>answer(q.suggestion));controls.append(accept);}
    card.append(controls);box.append(card);
  }
}
async function refreshProject() {
  const pid=state.selected;if(!pid)return;show('details',true);
  try {
    const responses=await Promise.all([api('projects/'+encodeURIComponent(pid)),api('projects/'+encodeURIComponent(pid)+'/events?since='+state.cursor),api('ide/'+encodeURIComponent(pid)+'/builds')]);
    if(pid!==state.selected)return;
    const data=responses[0];const p=data.project;state.detail=p;
    byId('projectName').textContent=p.name;byId('projectStatus').textContent=statusText[p.status] || p.status;
    byId('agentCount').textContent=(p.agents_done || 0)+' / '+(p.agents_total || 0);byId('tokens').textContent=Number(p.tokens || 0).toLocaleString('ru-RU');
    byId('progress').style.width=Math.max(0,Math.min(100,Math.round(Number(p.progress || 0) / .01)))+'%';
    byId('start').disabled=p.status==='running';byId('pause').disabled=p.status!=='running';byId('resume').disabled=p.status!=='paused';byId('stop').disabled=!['running','paused'].includes(p.status);
    renderAgents(data.agents || []);
    for(const e of responses[1].events || []) {state.events.push(e);state.cursor=Math.max(state.cursor,e.id || 0);}
    if(state.events.length>100)state.events=state.events.slice(-100);
    renderEvents();renderBuild(responses[2].builds || []);
  } catch(e) { notify('Проект: '+e.message); }
}
async function refresh() {
  if(state.busy || !state.user)return;state.busy=true;
  try{const result=await Promise.all([api('projects'),api('questions')]);state.projects=result[0].projects || [];if (!state.projects.some(p=>p.id===state.selected)) {state.selected=state.projects.length?state.projects[0].id:'';state.cursor=0;state.events=[];}show('details',!!state.selected);show('questionsWithoutProject',!state.selected);renderProjects();renderQuestions(result[1].questions || []);connected(true);if(state.selected)await refreshProject();}
  catch(e){connected(false);notify('Нет связи: '+e.message);}finally{state.busy=false;}
}
async function enter() {
  try{const b=await api('bootstrap');state.user=b.user;connected(true);show('login',!state.user);show('dashboard',!!state.user);if(state.user){await refresh();clearInterval(state.timer);state.timer=setInterval(refresh,5000);}}
  catch(e){connected(false);show('login',true);byId('loginError').textContent='Не удалось связаться с компьютером: '+e.message;}
}
byId('loginForm').addEventListener('submit',async e=>{e.preventDefault();const error=byId('loginError');error.textContent='';if(location.protocol==='http:'&&!byId('acceptHttp').checked){error.textContent='Подтвердите риск HTTP или используйте HTTPS.';return;}try{await api('auth/login','POST',{name:byId('name').value,password:byId('password').value});byId('password').value='';await enter();}catch(err){error.textContent=err.message;}});
byId('logout').addEventListener('click',async()=>{try{await api('auth/logout','POST');}catch(e){}clearInterval(state.timer);state.user=null;state.selected='';state.cursor=0;state.events=[];state.drafts.clear();show('details',false);show('dashboard',false);show('login',true);});
byId('refresh').addEventListener('click',refresh);
for(const tab of document.querySelectorAll('.tab')) tab.addEventListener('click',()=>{for(const t of document.querySelectorAll('.tab'))t.classList.toggle('active',t===tab);for(const panel of document.querySelectorAll('.tab-content'))panel.classList.toggle('hidden',panel.dataset.panel!==tab.dataset.tab);});
for(const action of ['start','pause','resume','stop'])byId(action).addEventListener('click',async()=>{if(!state.selected)return;if(action==='stop'&&!confirm('Остановить агентов проекта?'))return;try{await api('projects/'+encodeURIComponent(state.selected)+'/'+action,'POST',action==='start'?{pending_only:state.detail && state.detail.status==='error'}:null);notify('Команда отправлена');await refresh();}catch(e){notify(e.message);}});
if(location.protocol==='http:'){show('insecure',true);show('insecureLabel',true);}
enter();
