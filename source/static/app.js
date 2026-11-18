/* ==========================================================================
   MRC Agent — клиент. Без фреймворков: один файл, ноль зависимостей.
   ========================================================================== */
'use strict';
window.toastVoiceError = (message) => toast(message, 'err');

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};

const S = {
  user: null,
  settings: {},
  version: '',
  projects: [],
  chats: [],
  view: 'chats',
  activeChat: null,
  messages: [],
  project: null,          // активный проект (агенты)
  agents: [],
  agentMap: new Map(),
  events: [],             // последние события активного проекта
  filter: { lane: 'all', q: '' },
  drawerAgent: null,
  drawerTab: 'now',
  agentEvents: [],
  con: { events: [], last: 0, total: 0, timer: null, loading: false },
  ws: { project: null, agent: '', tree: [], file: null, content: '', mode: 'code', dirty: false,
        locked: false, binary: false },
  streaming: null,
  liveChats: new Map(),
  liveAgents: new Map(),
  es: null,
  tokens: 0,
};

/* ------------------------------------------------------------------ утилиты */

const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');          // имена файлов и ники идут в атрибуты

function fmtNum(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.0', '') + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1).replace('.0', '') + 'k';
  return String(n);
}
function timeAgo(ts) {
  if (!ts) return '';
  const d = Date.now() / 1000 - ts;
  if (d < 45) return 'только что';
  if (d < 3600) return Math.round(d / 60) + ' мин назад';
  if (d < 86400) return Math.round(d / 3600) + ' ч назад';
  return Math.round(d / 86400) + ' дн назад';
}
function clock(ts) {
  const d = new Date((ts || 0) * 1000);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function hashCode(s) {
  let h = 0;
  for (let i = 0; i < (s || '').length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}
function avatarStyle(name) {
  const h = hashCode(name || 'x') % 360;
  return `--a1:hsl(${h} 62% 52%);--a2:hsl(${(h + 48) % 360} 58% 34%)`;
}
function initials(name) {
  const n = (name || '·').trim();
  return n.slice(0, 2).toUpperCase();
}
function avatarHTML(name, cls = 'sm', ring = false) {
  return `<div class="avatar ${cls} ${ring ? 'ring' : ''}" style="${avatarStyle(name)}">${initials(name)}</div>`;
}
function agentAvatar(agent, size, ring) {
  // У каждого агента собственный детерминированный пиксельный маскот, не фотография.
  const seed = hashCode(String(agent.id || agent.nick || '') + ':' + String(agent.lane || ''));
  const body = ['#70e6d2', '#b4a3ff', '#ffbf7e', '#82c9ff', '#f493c0', '#e5dd86'][seed % 6];
  const eye = ['#18233d', '#18233d', '#f7f8ff'][seed % 3];
  const head = [
    '<path d="M16 18h32v6h5v22h-6v6H17v-6h-6V24h5z"/>',
    '<path d="M20 16h24v4h6v28h-6v5H20v-5h-6V20h6z"/>',
    '<path d="M14 20h36v25h-5v6H19v-6h-5z"/>',
    '<path d="M19 15h26v5h7v23h-6v8H18v-8h-6V20h7z"/>'
  ][Math.floor(seed / 6) % 4];
  const antenna = [
    '<path d="M29 10h6v9h-6zM27 7h10v5H27z"/>',
    '<path d="M14 13h5v10h-5zM45 13h5v10h-5z"/>',
    '<path d="M20 12h24v5H20z"/>',
    '<path d="M30 9h4v9h-4zM27 7h10v4H27z"/>'
  ][Math.floor(seed / 24) % 4];
  const eyes = [
    '<path d="M21 29h7v7h-7zM36 29h7v7h-7z"/>',
    '<path d="M20 30h9v5h-9zM35 30h9v5h-9z"/>',
    '<path d="M22 28h6v9h-6zM36 28h6v9h-6z"/>'
  ][Math.floor(seed / 96) % 3];
  const mouth = ['<path d="M26 41h12v3H26z"/>', '<path d="M26 40h4v3h4v-3h4v4H26z"/>', '<path d="M28 40h8v4h-8z"/>'][Math.floor(seed / 288) % 3];
  const svg = `<svg viewBox="0 0 64 64" aria-hidden="true" shape-rendering="crispEdges">
    <rect width="64" height="64" rx="12" fill="#10192c"/>
    <path d="M4 13h8v2H4zM52 48h8v2h-8zM8 50h4v4H8zM52 10h4v4h-4z" fill="${body}" opacity=".35"/>
    <g fill="${body}">${antenna}${head}</g>
    <path d="M18 23h28v3H18z" fill="#fff" opacity=".22"/>
    <g fill="${eye}">${eyes}${mouth}</g>
    <path d="M15 56h34v3H15z" fill="${body}" opacity=".55"/>
  </svg>`;
  return `<div class="avatar agent-portrait ${size} ${ring ? 'ring' : ''}" style="${avatarStyle(agent.nick)}" title="${esc(agent.role || '')}">${svg}<span class="agent-portrait-badge">${esc(initials(agent.role || agent.nick))}</span></div>`;
}
function bytes(n) {
  if (n < 1024) return n + ' Б';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' КБ';
  return (n / 1048576).toFixed(1) + ' МБ';
}

async function api(path, opts = {}) {
  const res = await fetch('/api/' + path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* пусто */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || data.reason || ('HTTP ' + res.status));
  return data;
}

/* ------------------------------------------------------------------ иконки активностей */

const ACT = {
  think: `<span class="act think"><svg viewBox="0 0 24 24"><circle cx="6" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="18" cy="12" r="2"/></svg></span>`,
  search: `<span class="act search"><svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6" class="lens"/><path d="m19 19-4.2-4.2"/><path class="sweep" d="M10.5 4.5v3" /></svg></span>`,
  browse: `<span class="act browse"><svg viewBox="0 0 24 24"><g class="globe"><circle cx="12" cy="12" r="8"/><ellipse class="equator" cx="12" cy="12" rx="3.4" ry="8"/><path d="M4 12h16"/></g></svg></span>`,
  code: `<span class="act code"><svg viewBox="0 0 24 24"><path class="br" d="m8 17-5-5 5-5"/><path class="br" d="m16 7 5 5-5 5"/><path class="caret" d="M12 9v6" stroke-width="2"/></svg></span>`,
  edit: `<span class="act edit"><svg viewBox="0 0 24 24"><g class="pencil"><path d="M4 20h4l10-10-4-4L4 16Z"/><path d="m13.5 6.5 4 4"/></g></svg></span>`,
  test: `<span class="act test"><svg viewBox="0 0 24 24"><path class="tick" d="m4 13 5 5L20 6"/></svg></span>`,
  review: `<span class="act review"><svg viewBox="0 0 24 24"><rect x="5" y="3.5" width="14" height="17" rx="2.5"/><path class="scan" d="M8 12h8" stroke-width="2"/></svg></span>`,
  write: `<span class="act write"><svg viewBox="0 0 24 24"><path d="M4 20h16"/><g class="pen"><path d="M5 17.5 16.5 6a2.1 2.1 0 0 1 3 3L8 20H5z"/><path d="m14.8 7.7 3 3"/></g></svg></span>`,
  msg: `<span class="act msg"><svg viewBox="0 0 24 24"><g class="bub"><path d="M20 12a7 7 0 0 1-7 7 7.6 7.6 0 0 1-3.2-.7L5 20l1.4-3.9A7 7 0 0 1 13 5a7 7 0 0 1 7 7Z"/></g></svg></span>`,
  tool: `<span class="act tool"><svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="m7.5 10.5 2.5 2-2.5 2"/><path d="M12.5 15h4"/></svg></span>`,
  done: `<span class="act done"><svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7"/></svg></span>`,
  error: `<span class="act error"><svg viewBox="0 0 24 24"><path d="M12 8v5"/><circle cx="12" cy="16.5" r=".6" fill="currentColor"/><circle cx="12" cy="12" r="8.5"/></svg></span>`,
  wait: `<span class="act wait"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg></span>`,
  plan: `<span class="act think"><svg viewBox="0 0 24 24"><path d="M8 7h9M8 12h9M8 17h6"/><circle cx="5" cy="7" r="1" fill="currentColor"/><circle cx="5" cy="12" r="1" fill="currentColor"/><circle cx="5" cy="17" r="1" fill="currentColor"/></svg></span>`,
  web: `<span class="act browse"><svg viewBox="0 0 24 24"><g class="globe"><circle cx="12" cy="12" r="8"/><ellipse class="equator" cx="12" cy="12" rx="3.4" ry="8"/><path d="M4 12h16"/></g></svg></span>`,
};
const actIcon = (kind) => ACT[kind] || ACT.think;
const LANE_ACT = {
  lead: 'think', research: 'search', design: 'think', code: 'code',
  test: 'test', review: 'review', docs: 'write',
};
const laneAct = (lane) => LANE_ACT[lane] || 'think';

/* ------------------------------------------------------------------ подсветка кода */

const KW = ('def class return if else elif for while import from as try except finally with lambda ' +
  'None True False and or not in is pass break continue async await yield global nonlocal assert del raise ' +
  'const let var function new this typeof instanceof export default null undefined true false ' +
  'public private void int float bool str async switch case break struct impl fn use pub mut')

function highlight(code, lang = '') {
  const src = esc(code);
  const re = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|"""[\s\S]*?"""|'[^'\n]*'|"[^"\n]*"|`[^`]*`|\b\d+(?:\.\d+)?\b|\b(?:[\w-]+)\b)/g;
  const kw = new Set(KW.split(/\s+/));
  return src.replace(re, (m) => {
    if (m.startsWith('//') || m.startsWith('#') || m.startsWith('/*')) return `<span class="tok-com">${m}</span>`;
    if (/^["'`]/.test(m)) return `<span class="tok-str">${m}</span>`;
    if (/^\d/.test(m)) return `<span class="tok-num">${m}</span>`;
    if (kw.has(m)) return `<span class="tok-kw">${m}</span>`;
    return m;
  });
}

/* ------------------------------------------------------------------ markdown */

function renderMarkdown(text) {
  const parts = String(text || '').split(/```/);
  let out = '';
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) {
      const raw = parts[i];
      const nl = raw.indexOf('\n');
      const lang = (nl > -1 ? raw.slice(0, nl) : '').trim() || 'text';
      const code = nl > -1 ? raw.slice(nl + 1) : raw;
      out += codeBlockHTML(code.replace(/\n$/, ''), lang);
    } else {
      out += mdText(parts[i]);
    }
  }
  return out;
}

function codeBlockHTML(code, lang) {
  const id = 'cb' + Math.random().toString(36).slice(2, 9);
  return `<div class="code-block" data-code="${id}">
    <div class="code-head">
      <span class="lang">${esc(lang)}</span>
      <button class="code-copy" data-copy="${id}"><svg class="ic"><use href="#i-copy"/></svg>копировать</button>
    </div>
    <pre class="block"><code>${highlight(code, lang)}</code></pre>
    <textarea class="hidden-src" style="display:none">${esc(code)}</textarea>
  </div>`;
}

function inlineMd(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/`([^`\n$]+)`/g, '<code>$1</code>')
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function mdText(t) {
  const lines = esc(t).split('\n');
  const out = [];
  let list = null;          // 'ul' | 'ol'
  let para = [];
  const flushPara = () => {
    if (para.length) { out.push('<p>' + para.join('<br>') + '</p>'); para = []; }
  };
  const closeList = () => { if (list) { out.push('</' + list + '>'); list = null; } };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) { flushPara(); closeList(); continue; }
    let m;
    if ((m = line.match(/^\s*###\s+(.*)$/))) { flushPara(); closeList(); out.push('<h3>' + inlineMd(m[1]) + '</h3>'); continue; }
    if ((m = line.match(/^\s*##\s+(.*)$/))) { flushPara(); closeList(); out.push('<h2>' + inlineMd(m[1]) + '</h2>'); continue; }
    if ((m = line.match(/^\s*#\s+(.*)$/))) { flushPara(); closeList(); out.push('<h1>' + inlineMd(m[1]) + '</h1>'); continue; }
    if ((m = line.match(/^\s*>\s?(.*)$/))) { flushPara(); closeList(); out.push('<blockquote>' + inlineMd(m[1]) + '</blockquote>'); continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      flushPara();
      if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
      out.push('<li>' + inlineMd(m[1]) + '</li>');
      continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
      out.push('<li>' + inlineMd(m[1]) + '</li>');
      continue;
    }
    closeList();
    para.push(inlineMd(line));
  }
  flushPara();
  closeList();
  return out.join('');
}

/* ------------------------------------------------------------------ тосты */

function toast(text, kind = 'info', ms = 3200) {
  const icons = { ok: '#i-check', err: '#i-close', info: '#i-spark' };
  const t = el('div', 'toast ' + kind,
    `<svg class="ic"><use href="${icons[kind] || icons.info}"/></svg><span>${esc(text)}</span>`);
  $('#toasts').appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 260); }, ms);
}

/* ------------------------------------------------------------------ вход */

async function boot() {
  try {
    const b = await api('bootstrap');
    S.settings = b.settings || {};
    S.version = b.version;
    if ($('#appVersion') && b.version) $('#appVersion').textContent = 'v' + b.version;
    applyTheme();
    if (!b.user) {
      showLogin(b);
      return;
    }
    S.user = b.user;
    S.projects = b.projects || [];
    S.chats = b.chats || [];
    showApp();
    startQuestions();
    connectSSE();
    if (S.chats.length) openChat(S.chats[0].id);
    else renderChatEmpty();
    renderPanel();
    renderProjects();
    syncProjectSelect();
    if (S.projects.length) selectProject(S.projects[0].id, { silent: true });
    $('#chatModelChip').textContent = S.settings.model || 'mercury-3';
    loadSkills();
    initMusic();
    // обучение — один раз на пользователя, кто-то его пройдёт и увидит чат
    if (b.onboarded === false) setTimeout(() => startTour(), 600);
  } catch (e) {
    console.error(e);
    toast('Не удалось загрузить приложение: ' + e.message, 'err', 6000);
  } finally {
    const splash = $('#appStartup');
    if (splash) {
      const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const remaining = reduced ? 0 : Math.max(0, 2200 - (Date.now() - (window.mrcStartupStarted || Date.now())));
      setTimeout(() => {
        splash.classList.add('is-leaving');
        setTimeout(() => splash.remove(), reduced ? 0 : 800);
      }, remaining);
    }
  }
}

function showLogin() {
  $('#login').classList.remove('hidden');
  $('#app').classList.add('hidden');
  setLoginMode('login');
  if ($('#loginPass')) $('#loginPass').value = '';
  if ($('#loginPass2')) $('#loginPass2').value = '';
  setTimeout(() => { const n = $('#loginNick'); if (n) n.focus(); }, 60);
}

function showApp() {
  const flash = sessionStorage.getItem('mrc_flash');
  if (flash) { sessionStorage.removeItem('mrc_flash'); setTimeout(() => toast(flash, 'ok'), 700); }
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
  const u = S.user || {};
  $('#railAvatar').innerHTML = u.picture
    ? `<img src="${esc(u.picture)}" alt="" style="width:100%;height:100%;object-fit:cover">`
    : initials(u.name);
  $('#railAvatar').style.cssText = avatarStyle(u.name || 'g');
  $('#profileAvatar').innerHTML = u.picture
    ? `<img src="${esc(u.picture)}" alt="" style="width:100%;height:100%;object-fit:cover">`
    : initials(u.name);
  $('#profileAvatar').style.cssText = avatarStyle(u.name || 'g');
  $('#profileName').textContent = u.name || '—';
  $('#profileEmail').textContent = 'проекты и чаты на этом компьютере';
  $('#profileProvider').textContent = 'вход по нику';
  if ($('#profileNick')) $('#profileNick').value = '';
  fillSettings();
}

/* ---------------- вход и регистрация (ник + пароль) ---------------- */

let loginMode = 'login';

function setLoginMode(mode) {
  loginMode = mode === 'register' ? 'register' : 'login';
  const reg = loginMode === 'register';
  document.querySelectorAll('.login-tab').forEach(t =>
    t.classList.toggle('is-on', t.dataset.mode === loginMode));
  $('#passRepeatWrap').hidden = !reg;
  $('#btnLoginText').textContent = reg ? 'Создать аккаунт' : 'Войти';
  $('#loginPass').setAttribute('autocomplete', reg ? 'new-password' : 'current-password');
  const note = $('#loginNote');
  note.classList.remove('bad');
  note.innerHTML = reg
    ? 'Ник — это имя аккаунта. Запомните пароль: восстановить его нельзя, ключи шифрования только у вас в базе.'
    : 'Пароль хранится только у вас: PBKDF2-хэш в <code>data/studio.db</code>, наружу ничего не уходит.';
  ($('#loginNick')).focus();
}

function loginError(text) {
  const note = $('#loginNote');
  note.classList.add('bad');
  note.textContent = text;
}

document.querySelectorAll('.login-tab').forEach(tab =>
  tab.addEventListener('click', () => setLoginMode(tab.dataset.mode)));

$('#passEye').addEventListener('click', () => {
  ['#loginPass', '#loginPass2'].forEach(sel => {
    const el = $(sel);
    if (el) el.type = el.type === 'password' ? 'text' : 'password';
  });
  $('#passEye').classList.toggle('is-on');
});

async function doLogin(ev) {
  if (ev) ev.preventDefault();
  const nick = ($('#loginNick').value || '').trim();
  const pass = $('#loginPass').value || '';
  const pass2 = $('#loginPass2').value || '';
  const reg = loginMode === 'register';

  if (!nick) return loginError('Напишите ник — под ним и будем работать.');
  if (pass.length < 6) return loginError('Пароль минимум 6 символов.');
  if (reg && pass !== pass2) return loginError('Пароли не совпадают.');

  const btn = $('#btnLogin');
  btn.disabled = true;
  try {
    const r = await api(reg ? 'auth/register' : 'auth/login',
                        { method: 'POST', body: { name: nick, password: pass } });
    if (r.claimed) sessionStorage.setItem('mrc_flash', 'Пароль установлен — теперь вход по нему.');
    location.reload();
  } catch (e) {
    btn.disabled = false;
    loginError(e.message);
  }
}
$('#loginForm').addEventListener('submit', doLogin);

$('#btnChangePass').addEventListener('click', async () => {
  const cur = $('#passCur').value, nw = $('#passNew').value;
  if (nw.length < 6) return toast('Новый пароль минимум 6 символов', 'err');
  try {
    await api('auth/password', { method: 'PUT', body: { current: cur, password: nw } });
    $('#passCur').value = ''; $('#passNew').value = '';
    toast('Пароль обновлён', 'ok');
  } catch (e) { toast(e.message, 'err'); }
});

/* ------------------------------------------------------------------ SSE */

function connectSSE() {
  if (S.es) S.es.close();
  const es = new EventSource('/api/stream');
  S.es = es;
  es.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    const [kind, id] = String(msg.topic || '').split(':');
    if (kind === 'project') handleProjectEvent(id, msg.payload);
    if (kind === 'chat') handleChatEvent(id, msg.payload);
  };
  let wasOpen = false;
  es.onopen = () => {
    if (wasOpen && S.activeChat) openChat(S.activeChat.id);
    wasOpen = true;
  };
  es.onerror = () => { /* EventSource сам переподключится */ };
}

function handleChatEvent(chatId, p) {
  if (!p) return;
  let live = S.liveChats.get(chatId) || { text: '', thought: '', busy: true, seq: 0 };
  if (p.seq && p.seq <= (live.seq || 0)) return;
  if (p.seq) live.seq = p.seq;
  if (p.type === 'token') live.text = p.text || '';
  if (p.type === 'thought') live.thought = p.text || '';
  S.liveChats.set(chatId, live);
  const visible = S.activeChat && S.activeChat.id === chatId;
  if (visible && (p.type === 'token' || p.type === 'thought')) {
    if (!S.streaming) startAssistantStream();
    updateAssistantStream(live);
  }
  if (p.type === 'done' || p.type === 'error') {
    if (visible) {
      if (!S.streaming) startAssistantStream();
      updateAssistantStream(live);
      finishAssistantStream(p.text, p.type === 'error');
      if (p.usage) {
        S.tokens += (p.usage.out || 0);
        $('#composerCost').textContent = fmtNum(S.tokens) + ' т';
      }
    }
    S.liveChats.delete(chatId);
  }
}

function handleProjectEvent(pid, p) {
  if (!p) return;
  if (S.project && S.project.id === pid) {
    if (p.agent && ['draft_start', 'token', 'thought'].includes(p.type)) {
      const key = pid + ':' + p.agent;
      let live = S.liveAgents.get(key) || { draft: '', thought: '' };
      if (p.type === 'draft_start') live = { draft: '', thought: '' };
      if (p.type === 'token') live.draft = p.text || '';
      if (p.type === 'thought') live.thought = p.text || '';
      S.liveAgents.set(key, live);
      if (S.drawerAgent && S.drawerAgent.id === p.agent && S.drawerTab === 'live') updateAgentLive();
      if (p.type === 'token') { const agent = S.agentMap.get(p.agent); if (agent) updateAgentCard(agent, live.draft.slice(-120)); }
      return;
    }
    // состояния агентов
    if (p.type === 'agent_state') {
      const a = S.agentMap.get(p.agent);
      if (a) {
        Object.assign(a, { status: p.status, activity: p.activity, task: p.task, tokens: p.tokens });
        updateAgentCard(a);
      }
    } else if (p.agent) {
      const a = S.agentMap.get(p.agent);
      S.events.push(p);
      if (S.events.length > 400) S.events.shift();
      if (S.drawerAgent && S.drawerAgent.id === p.agent) {
        S.agentEvents.push(p);
        if (S.drawerTab === 'now' || S.drawerTab === 'feed') renderDrawerBody();
      }
      if (a) {
        if (p.type === 'activity') {
          a.activity = (p.data && p.data.kind) || laneAct(a.lane);
          a.task = p.text;
          updateAgentCard(a, p.text);
        } else if (p.type === 'token') {
          updateAgentCard(a, p.text);
        } else if (p.type === 'file') {
          let files = [];
          try { files = JSON.parse(a.files || '[]'); } catch (e) { files = []; }
          if (!files.includes(p.text)) files.push(p.text);
          a.files = JSON.stringify(files);
        } else if (p.type === 'done') {
          a.status = 'done'; a.activity = 'done';
          updateAgentCard(a);
        } else if (p.type === 'error') {
          a.status = 'error'; a.activity = 'error';
          updateAgentCard(a, p.text);
        }
      }
    } else if (p.type === 'plan' || p.type === 'log' || p.type === 'done') {
      if (p.type === 'plan') toast(p.text, 'info', 2600);
      if (p.type === 'done') refreshProjectHeader();
    }
    refreshStats();
  }
  if (p.type === 'file') {
    const proj = S.ws.project;
    if (proj && proj.id === pid) loadTree(pid);
  }
  if (p.type === 'run_end') {
    setTimeout(() => { refreshAgents(); refreshProjects(); }, 400);
  }
}

/* ------------------------------------------------------------------ навигация */

$$('.rail-btn').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));
$$('[data-goto]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.goto)));

function setView(v) {
  S.view = v;
  $$('.rail-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === v));
  $$('.view').forEach((s) => s.classList.toggle('active', s.id === 'view-' + v));
  renderPanel();
  if (v === 'agents') { syncProjectSelect(); if (S.project) refreshAgents(); else renderAgents(); }
  if (v === 'projects') renderProjects();
  if (v === 'workspace') {
    syncProjectSelect();
    initWorkspace();
    if (S.project) refreshAgents().then(syncScopeSelect); else syncScopeSelect();
  }
  if (v === 'ide') { initIDE(); }
  if (v === 'skills') { loadSkills(); }
  if (v === 'plugins') { loadPlugins(); }
  if (v === 'mcp') { fillSettings(); renderMcp(); }
  if (v === 'settings') { fillSettings(); }
  if (window.innerWidth < 860) $('.panel').classList.remove('open');
}

$('#togglePanel').addEventListener('click', () => $('.panel').classList.toggle('open'));
window.addEventListener('mrc-language-changed', () => { if (S.user) renderPanel(); });

/* ------------------------------------------------------------------ панель */

function renderPanel() {
  const titles = { chats: 'Чаты', agents: 'Агенты', projects: 'Проекты', workspace: 'Файлы',
                   ide: 'MRCIDE Studio', skills: 'Скиллы', plugins: 'Плагины', mcp: 'MCP', settings: 'Ещё' };
  $('#panelTitle').textContent = titles[S.view] || 'Mercury';
  const body = $('#panelBody');
  const foot = $('#panelFoot');
  body.innerHTML = '';
  foot.innerHTML = '';
  const q = ($('#panelFilter').value || '').toLowerCase();

  if (S.view === 'ide') {
    $('#panelAction').style.display = 'none';
    body.innerHTML = '<div class="empty-tab">Выберите проект и запустите сборку в MRCIDE Studio.</div>';
    return;
  }

  if (S.view === 'mcp') {
    $('#panelAction').style.display = 'none';
    body.innerHTML = '<div class="empty-tab">Серверы и настройки MCP — в основной области</div>';
    return;
  }

  if (S.view === 'plugins') {
    $('#panelAction').style.display = 'none';
    body.innerHTML = '<div class="empty-tab">Управление плагинами — в основной области</div>';
    foot.innerHTML = '<div class="panel-foot-hint">Плагины и инструкции для агентов</div>';
    return;
  }

  if (S.view === 'skills') {
    $('#panelAction').style.display = 'none';
    if (!SK.list.length) body.innerHTML = '<div class="empty-tab">Скиллов пока нет</div>';
    SK.list.filter((sk) => !q || (sk.name || '').toLowerCase().includes(q)).forEach((sk) => {
      const item = el('button', 'list-item' + (sk.enabled ? '' : ' dim'),
        `<div class="li-icon"><svg class="ic"><use href="#i-skill"/></svg></div>
         <div class="li-main"><div class="li-title">${esc(sk.name)}</div>
         <div class="li-sub">${sk.enabled ? 'включён' : 'выключен'} · ${sk.lines} строк</div></div>`);
      item.addEventListener('click', () => openSkill(sk.id));
      body.appendChild(item);
    });
    foot.innerHTML = `<div class="panel-foot-hint">Скиллы видит каждый агент роя</div>`;
    return;
  }

  if (S.view === 'chats') {
    $('#panelAction').style.display = '';
    if (!S.chats.length) {
      body.innerHTML = '<div class="empty-tab">Пока нет чатов</div>';
    }
    S.chats.filter((c) => !q || (c.title || '').toLowerCase().includes(q)).forEach((c) => {
      const item = el('button', 'list-item' + (S.activeChat && S.activeChat.id === c.id ? ' active' : ''),
        `<div class="li-icon"><svg class="ic"><use href="#i-chat"/></svg></div>
         <div class="li-main"><div class="li-title">${esc(c.title || 'Без названия')}</div>
         <div class="li-sub">${timeAgo(c.updated_at)}</div></div>`);
      item.addEventListener('click', () => { openChat(c.id); if (window.innerWidth < 860) $('.panel').classList.remove('open'); });
      const del = el('button', 'list-del', `<svg class="ic" style="width:14px;height:14px"><use href="#i-close"/></svg>`);
      del.addEventListener('click', async (e) => {
        e.stopPropagation();
        await api('chats/' + c.id, { method: 'DELETE' });
        S.chats = S.chats.filter((x) => x.id !== c.id);
        if (S.activeChat && S.activeChat.id === c.id) { S.activeChat = null; renderChatEmpty(); }
        renderPanel(); toast('Чат удалён', 'ok');
      });
      item.appendChild(del);
      body.appendChild(item);
    });
    foot.innerHTML = `<button class="btn btn-ghost sm" style="width:100%" id="panelNewChat">
      <svg class="ic"><use href="#i-plus"/></svg>Новый чат</button>`;
    $('#panelNewChat').addEventListener('click', newChat);
  }

  if (S.view === 'agents') {
    $('#panelAction').style.display = 'none';
    body.appendChild(el('div', 'list-group', 'Проекты'));
    if (!S.projects.length) body.innerHTML += '<div class="empty-tab">Нет проектов</div>';
    S.projects.forEach((p) => {
      const item = el('button', 'list-item' + (S.project && S.project.id === p.id ? ' active' : ''),
        `<div class="li-icon"><svg class="ic"><use href="#i-folder"/></svg></div>
         <div class="li-main"><div class="li-title">${esc(p.name)}</div>
         <div class="li-sub">${p.agents_done}/${p.agents_total} · ${p.status}</div></div>`);
      item.addEventListener('click', () => { selectProject(p.id); if (window.innerWidth < 860) $('.panel').classList.remove('open'); });
      body.appendChild(item);
    });
    body.appendChild(el('div', 'list-group', 'Фильтр по роли'));
    const lanes = ['all', 'lead', 'main', 'research', 'design', 'code', 'test', 'review', 'docs'];
    lanes.forEach((l) => {
      const cnt = l === 'all' ? S.agents.length : S.agents.filter((a) => a.lane === l).length;
      const item = el('button', 'list-item' + (S.filter.lane === l ? ' active' : ''),
        `<div class="li-icon">${l === 'all' ? '<svg class="ic"><use href="#i-agents"/></svg>' : actIcon(laneAct(l))}</div>
         <div class="li-main"><div class="li-title">${laneTitle(l)}</div></div>
         <span class="chip">${cnt}</span>`);
      item.addEventListener('click', () => { S.filter.lane = l; renderPanel(); renderAgents(); });
      body.appendChild(item);
    });
    foot.innerHTML = `<button class="btn btn-ghost sm" style="width:100%" data-goto="projects">
      <svg class="ic"><use href="#i-plus"/></svg>Новый проект</button>`;
    $('[data-goto="projects"]', foot).addEventListener('click', () => setView('projects'));
  }

  if (S.view === 'projects') {
    $('#panelAction').style.display = 'none';
    S.projects.forEach((p) => {
      const item = el('button', 'list-item' + (S.project && S.project.id === p.id ? ' active' : ''),
        `<div class="li-icon"><svg class="ic"><use href="#i-folder"/></svg></div>
         <div class="li-main"><div class="li-title">${esc(p.name)}</div>
         <div class="li-sub">${Math.round((p.progress || 0) * 100)}% · ${p.status}</div></div>`);
      item.addEventListener('click', () => { selectProject(p.id); setView('agents'); });
      body.appendChild(item);
    });
    if (!S.projects.length) body.innerHTML = '<div class="empty-tab">Пока нет проектов</div>';
  }

  if (S.view === 'workspace') {
    $('#panelAction').style.display = 'none';
    body.appendChild(el('div', 'list-group', 'Проект'));
    S.projects.forEach((p) => {
      const item = el('button', 'list-item' + (S.ws.project && S.ws.project.id === p.id ? ' active' : ''),
        `<div class="li-icon"><svg class="ic"><use href="#i-folder"/></svg></div>
         <div class="li-main"><div class="li-title">${esc(p.name)}</div>
         <div class="li-sub">${p.agents_done}/${p.agents_total}</div></div>`);
      item.addEventListener('click', () => {
        S.ws.project = p; syncProjectSelect(); loadTree(p.id);
        if (window.innerWidth < 860) $('.panel').classList.remove('open');
      });
      body.appendChild(item);
    });
    if (!S.projects.length) body.innerHTML = '<div class="empty-tab">Нет проектов</div>';
  }

  if (S.view === 'settings') {
    $('#panelAction').style.display = 'none';
    // Навигация строится по самим карточкам: добавили карточку — появился пункт.
    const iconFor = (t) => {
      if (/Модель/.test(t)) return '#i-spark';
      if (/Доступы/.test(t)) return '#i-terminal';
      if (/Рой/.test(t)) return '#i-refresh';
      if (/Почта/.test(t)) return '#i-mail';
      if (/MCP/.test(t)) return '#i-plug';
      if (/Музыка/.test(t)) return '#i-note';
      if (/Обучение/.test(t)) return '#i-skill';
      if (/Внешний вид/.test(t)) return '#i-eye';
      if (/Данные/.test(t)) return '#i-db';
      if (/Пароль/.test(t)) return '#i-lock';
      return '#i-gear';
    };
    $$('.settings-scroll .card').forEach((card) => {
      const t = (card.querySelector('h4') && card.querySelector('h4').textContent || 'Настройки').trim();
      const item = el('button', 'list-item',
        `<div class="li-icon"><svg class="ic"><use href="${iconFor(t)}"/></svg></div>
         <div class="li-main"><div class="li-title">${esc(t)}</div></div>`);
      item.addEventListener('click', () => card.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      body.appendChild(item);
    });
    body.appendChild(el('div', 'list-group', 'Аккаунт'));
    const out = el('button', 'list-item',
      `<div class="li-icon"><svg class="ic"><use href="#i-logout"/></svg></div>
       <div class="li-main"><div class="li-title">Выйти</div></div>`);
    out.addEventListener('click', logout);
    body.appendChild(out);
  }
}

function laneTitle(l) {
  return { all: 'Все агенты', lead: 'Планировщик', research: 'Разведчики', design: 'Архитекторы',
    code: 'Программисты', test: 'Тестировщики', review: 'Ревьюеры', docs: 'Документаторы',
    main: 'Главный' }[l] || l;
}

$('#panelFilter').addEventListener('input', renderPanel);
$('#panelAction').addEventListener('click', () => { if (S.view === 'chats') newChat(); });

/* ------------------------------------------------------------------ чаты */

async function newChat() {
  const d = await api('chats', { method: 'POST', body: { title: 'Новый чат' } });
  S.chats.unshift(d.chat);
  renderPanel();
  openChat(d.chat.id);
  $('#composer').focus();
}

async function openChat(id) {
  const d = await api('chats/' + id);
  S.activeChat = d.chat;
  S.streaming = null;
  S.messages = d.messages || [];
  $('#chatTitle').textContent = d.chat.title || 'Чат';
  $('#chatEmpty').classList.toggle('hidden', S.messages.length > 0);
  const box = $('#messages');
  box.innerHTML = '';
  S.messages.forEach((m) => box.appendChild(messageNode(m.role, m.content)));
  let live = S.liveChats.get(id);
  if (d.live && (!live || (d.live.seq || 0) > (live.seq || 0))) live = d.live;
  if (d.live && live) S.liveChats.set(id, live);
  if (!d.live) { S.liveChats.delete(id); live = null; }
  if (live) { startAssistantStream(); updateAssistantStream(live); }
  scrollChat();
  renderPanel();
}

function renderChatEmpty() {
  S.activeChat = null;
  S.streaming = null;
  $('#chatTitle').textContent = 'Mercury';
  $('#messages').innerHTML = '';
  $('#chatEmpty').classList.remove('hidden');
}

function messageNode(role, content) {
  const n = el('div', 'msg ' + (role === 'user' ? 'user' : 'assistant'));
  const name = role === 'user' ? (S.user && S.user.name) || 'Вы' : 'Mercury';
  n.innerHTML = `
    ${role === 'user' ? '' : `<div class="msg-avatar" style="${avatarStyle('Mercury')}">M</div>`}
    <div class="msg-body">
      <div class="msg-role">${esc(name)}</div>
      <div class="msg-text">${renderMarkdown(content)}</div>
      <button class="btn btn-ghost sm msg-copy" type="button" title="Копировать сообщение">Копировать</button>
    </div>
    ${role === 'user' ? `<div class="msg-avatar" style="${avatarStyle(name)}">${initials(name)}</div>` : ''}`;
  n.querySelector('.msg-copy').addEventListener('click', () => copyText(content, 'Сообщение скопировано'));
  if (role === 'assistant') window.attachMessageVoice(n, () => content);
  return n;
}

async function copyText(text, success) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area = document.createElement('textarea');
      area.value = text; area.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(area); area.select();
      const ok = document.execCommand('copy'); area.remove();
      if (!ok) throw new Error('буфер недоступен');
    }
    toast(success, 'ok');
  } catch (e) { toast('Не удалось скопировать: ' + e.message, 'err'); }
}

function scrollChat() {
  const s = $('#chatScroll');
  s.scrollTop = s.scrollHeight;
}

function startAssistantStream() {
  const node = el('div', 'msg assistant');
  node.innerHTML = `<div class="msg-avatar" style="${avatarStyle('Mercury')}">M</div>
    <div class="msg-body"><div class="msg-role">Mercury</div>
    <details class="live-thought"><summary>Рассуждение модели</summary><div class="live-reason">Читаемое рассуждение недоступно. API может не возвращать его или передавать в зашифрованном виде.</div></details>
    <div class="msg-text"><div class="typing"><i></i><i></i><i></i></div></div><button class="btn btn-ghost sm msg-copy" type="button" title="Копировать сообщение" disabled>Копировать</button></div>`;
  $('#messages').appendChild(node);
  $('#chatEmpty').classList.add('hidden');
  S.streaming = { node, el: $('.msg-text', node) };
    scrollChat();
}

function updateAssistantStream(live) {
  if (!S.streaming) return;
  if (live.text) S.streaming.el.innerHTML = renderMarkdown(live.text) + '<span class="caret-blink">▍</span>';
  const thought = $('.live-reason', S.streaming.node);
  if (thought && live.thought) thought.textContent = live.thought;
  scrollChat();
}

function finishAssistantStream(text, isError) {
  if (!S.streaming) { if (text) $('#messages').appendChild(messageNode('assistant', text)); return; }
  S.streaming.el.innerHTML = renderMarkdown(text || '');
  const copy = S.streaming.el.parentElement.querySelector('.msg-copy');
  if (copy) { copy.disabled = false; copy.addEventListener('click', () => copyText(text || '', 'Сообщение скопировано')); }
  if (isError) S.streaming.el.style.color = 'var(--err)';
  if (!isError && text) window.attachMessageVoice(S.streaming.node, () => text);
  S.streaming = null;
  scrollChat();
}

async function waitForStream() {
  if (S.es && S.es.readyState === EventSource.OPEN) return;
  if (!S.es) connectSSE();
  const es = S.es;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { es.removeEventListener('open', ready); reject(new Error('Нет соединения с потоком событий')); }, 10000);
    function ready() { clearTimeout(timer); es.removeEventListener('open', ready); resolve(); }
    es.addEventListener('open', ready);
    if (es.readyState === EventSource.OPEN) ready();
  });
}

async function sendMessage(text) {
  if (!text.trim()) return;
  if (!S.activeChat) {
    const d = await api('chats', { method: 'POST', body: { title: text.slice(0, 48) } });
    S.chats.unshift(d.chat);
    S.activeChat = d.chat;
    $('#chatTitle').textContent = d.chat.title;
    renderPanel();
  }
  if (S.liveChats.has(S.activeChat.id)) { toast('Дождитесь ответа в этом чате', 'info'); return; }
  try { await waitForStream(); }
  catch (e) { toast(e.message + '. Повторите отправку.', 'err'); return; }
  const chatId = S.activeChat.id;
  S.liveChats.set(chatId, { text: '', thought: '', busy: true });
  const box = $('#messages');
  box.appendChild(messageNode('user', text));
  startAssistantStream();
  $('#chatEmpty').classList.add('hidden');
  scrollChat();
  $('#composer').value = '';
  autoGrow($('#composer'));
  try {
    await api(`chats/${chatId}/messages`, { method: 'POST', body: { text } });
  } catch (e) {
    toast(e.message, 'err');
    S.liveChats.delete(chatId);
    if (S.activeChat && S.activeChat.id === chatId) finishAssistantStream('Ошибка: ' + e.message, true);
  }
}

const composer = $('#composer');
function autoGrow(t) { t.style.height = 'auto'; t.style.height = Math.min(180, t.scrollHeight || 24) + 'px'; }
composer.addEventListener('input', () => autoGrow(composer));
composer.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(composer.value);
  }
});
$('#btnSend').addEventListener('click', () => sendMessage(composer.value));
$$('#suggest button').forEach((b) => b.addEventListener('click', () => {
  composer.value = b.dataset.s;
  autoGrow(composer);
  composer.focus();
}));
$('#btnRunSwarm').addEventListener('click', () => {
  const goal = composer.value.trim();
  if (!goal) { toast('Опишите цель в поле ввода — рой возьмёт её в работу', 'info'); return; }
  createProject(goal);
});

/* копирование блоков кода */
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-copy]');
  if (!btn) return;
  const block = btn.closest('.code-block');
  const src = $('.hidden-src', block);
  try {
    await navigator.clipboard.writeText(src.value);
    btn.classList.add('done');
    btn.innerHTML = '<svg class="ic"><use href="#i-check"/></svg>скопировано';
    setTimeout(() => {
      btn.classList.remove('done');
      btn.innerHTML = '<svg class="ic"><use href="#i-copy"/></svg>копировать';
    }, 1600);
  } catch (err) { toast('Не удалось скопировать', 'err'); }
});

/* ------------------------------------------------------- вложения (1.4.2) */
/* Любой формат: файл читается в браузере и уходит base64 в /api/uploads.
   Потом в запросе можно сослаться на него через «@» — агенты получат и имя, и текст. */

S.attachments = [];

function humanSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + ' Б';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' КБ';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' МБ';
  return (n / 1073741824).toFixed(2) + ' ГБ';
}

function fileIcon(kind) {
  return '#i-file';
}

async function uploadAttachment(file) {
  const data = await new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result));
    fr.onerror = () => rej(new Error('не смог прочитать файл'));
    fr.readAsDataURL(file);
  });
  const d = await api('uploads', { method: 'POST',
    body: { name: file.name, mime: file.type || '', data } });
  return d.file;
}

function renderChips() {
  const box = $('#npChips');
  if (!box) return;
  box.innerHTML = '';
  const list = S.attachments || [];
  box.hidden = list.length === 0;
  list.forEach((f) => {
    const chip = el('span', 'file-chip');
    chip.innerHTML = `<svg class="ic"><use href="${fileIcon(f.kind)}"/></svg>
      <span class="fc-name">${esc(f.name)}</span>
      <span class="fc-size">${esc(f.kind || '')}${f.kind ? ' · ' : ''}${esc(f.human || humanSize(f.size))}</span>
      <span class="fc-x" title="убрать файл">✕</span>`;
    $('.fc-x', chip).addEventListener('click', async () => {
      S.attachments = S.attachments.filter((x) => x.id !== f.id);
      renderChips(); refreshAtMenu();
      try { await api('files/' + f.id, { method: 'DELETE' }); } catch (e) { /* уже удалён */ }
    });
    box.appendChild(chip);
  });
}

async function addFiles(fileList) {
  const list = Array.from(fileList || []);
  if (!list.length) return;
  for (const f of list) {
    try {
      toast('Загружаю ' + f.name + '…', 'info');
      const up = await uploadAttachment(f);
      S.attachments.push(up);
      renderChips(); refreshAtMenu();
      toast('Прикреплён ' + up.name + ' (' + (up.human || humanSize(up.size)) + ')', 'ok');
    } catch (e) {
      toast(f.name + ': ' + e.message, 'err');
    }
  }
}

$('#btnNpAttach').addEventListener('click', () => $('#npFileInput').click());
$('#npFileInput').addEventListener('change', async (e) => {
  await addFiles(e.target.files);
  e.target.value = '';
});

/* перетаскивание файлов прямо на поле цели */
['#newProjectCard', '#npGoal'].forEach((sel) => {
  const node = $(sel);
  if (!node) return;
  node.addEventListener('dragover', (e) => { e.preventDefault(); node.classList.add('drop-on'); });
  node.addEventListener('dragleave', () => node.classList.remove('drop-on'));
  node.addEventListener('drop', async (e) => {
    e.preventDefault(); node.classList.remove('drop-on');
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
      await addFiles(e.dataTransfer.files);
    }
  });
});

/* --- меню «@»: выбор прикреплённого файла прямо в тексте запроса --- */
const atState = { open: false, idx: 0, items: [] };

function atPool() {
  return (S.attachments || []).map((f) => ({ id: f.id, name: f.name, human: f.human || humanSize(f.size), kind: f.kind }));
}

function atQuery(text, caret) {
  const before = text.slice(0, caret);
  const m = /(^|\s)@([^\s@]*)$/.exec(before);
  return m ? m[2] : null;
}

function refreshAtMenu() {
  const ta = $('#npGoal');
  if (!ta || document.activeElement !== ta) { closeAtMenu(); return; }
  const q = atQuery(ta.value, ta.selectionStart);
  if (q === null) { closeAtMenu(); return; }
  const pool = atPool().filter((f) => !q || f.name.toLowerCase().includes(q.toLowerCase()));
  atState.open = true;
  atState.items = pool;
  atState.idx = 0;
  const menu = $('#npAtMenu');
  menu.innerHTML = pool.length
    ? pool.map((f, i) => `<div class="at-item${i === 0 ? ' on' : ''}" data-i="${i}">
         <svg class="ic"><use href="#i-file"/></svg><span class="ai-name">@${esc(f.name)}</span>
         <span class="ai-size">${esc(f.human)}</span></div>`).join('')
    : `<div class="at-empty">Прикреплённых файлов нет — нажмите «Прикрепить файл»</div>`;
  $$('.at-item', menu).forEach((node) => {
    node.addEventListener('mousedown', (e) => {
      e.preventDefault();
      pickAt(Number(node.dataset.i));
    });
  });
  placeAtMenu();
  menu.classList.remove('hidden');
}

function placeAtMenu() {
  const ta = $('#npGoal');
  const menu = $('#npAtMenu');
  const card = $('#newProjectCard');
  if (!ta || !menu || !card) return;
  const r = ta.getBoundingClientRect();
  const c = card.getBoundingClientRect();
  menu.style.left = Math.max(8, r.left - c.left) + 'px';
  menu.style.top = (r.bottom - c.top + 6) + 'px';
  card.style.position = 'relative';
}

function closeAtMenu() {
  atState.open = false;
  const menu = $('#npAtMenu');
  if (menu) menu.classList.add('hidden');
}

function pickAt(i) {
  const f = atState.items[i];
  if (!f) return;
  const ta = $('#npGoal');
  const pos = ta.selectionStart;
  const before = ta.value.slice(0, pos).replace(/@([^\s@]*)$/, '');
  const token = '@' + f.name + ' ';
  ta.value = before + token + ta.value.slice(pos);
  const caret = (before + token).length;
  ta.setSelectionRange(caret, caret);
  ta.focus();
  autoGrowNp();
  closeAtMenu();
}

function autoGrowNp() {
  const ta = $('#npGoal');
  if (!ta) return;
  ta.style.height = 'auto';
  ta.style.height = Math.min(200, ta.scrollHeight || 48) + 'px';
}

if ($('#npGoal')) {
  const ta = $('#npGoal');
  ta.addEventListener('input', refreshAtMenu);
  ta.addEventListener('click', refreshAtMenu);
  ta.addEventListener('blur', () => setTimeout(closeAtMenu, 120));
  ta.addEventListener('keydown', (e) => {
    if (!atState.open) return;
    const n = atState.items.length;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!n) return;
      atState.idx = (atState.idx + (e.key === 'ArrowDown' ? 1 : n - 1)) % n;
      $$('.at-item', $('#npAtMenu')).forEach((node, i) => node.classList.toggle('on', i === atState.idx));
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      if (!n) { closeAtMenu(); return; }
      e.preventDefault();
      pickAt(atState.idx);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeAtMenu();
    }
  });
}

/* --- прикрепить файлы к уже существующему проекту --- */
if ($('#wsAttach')) {
  $('#wsAttach').addEventListener('click', () => $('#wsFileInput').click());
  $('#wsFileInput').addEventListener('change', async (e) => {
    const pid = S.ws.project && S.ws.project.id;
    const picked = Array.from(e.target.files || []);
    e.target.value = '';
    if (!pid) { toast('Сначала выберите проект', 'info'); return; }
    const ids = [];
    for (const f of picked) {
      try {
        toast('Загружаю ' + f.name + '…', 'info');
        const up = await uploadAttachment(f);
        ids.push(up.id);
        toast('Прикреплён ' + up.name, 'ok');
      } catch (err) { toast(f.name + ': ' + err.message, 'err'); }
    }
    if (!ids.length) return;
    try {
      await api(`projects/${pid}/files`, { method: 'POST', body: { files: ids } });
      toast('Файлы прикреплены к проекту', 'ok');
      loadTree(pid);
    } catch (err) { toast(err.message, 'err'); }
  });
}

/* ------------------------------------------------------------------ проекты */

async function createProject(goal) {
  const agents = Number($('#npAgents').value) || 100;
  const files = (S.attachments || []).map((f) => f.id);
  const L = (window.showLoader || (() => ({ stage() {}, done() {} })))(
    'Собираю проект',
    files.length ? ['сохраняю вложения', 'создаю проект', 'поднимаю рой', 'агенты стартуют']
                 : ['создаю проект', 'готовлю workspace', 'поднимаю рой', 'агенты стартуют']);
  try {
    L.stage(files.length ? ('вложений: ' + files.length) : 'описываю цель', 0);
    const d = await api('projects', { method: 'POST', body: { goal, agents, files } });
    S.attachments = [];
    renderChips();
    L.stage('проект создан, раскладываю файлы', 1);
    S.projects.unshift(d.project);
    renderProjects(); syncProjectSelect(); renderPanel();
    await selectProject(d.project.id);
    setView('agents');
    L.stage('поднимаю ' + agents + ' агентов', 2);
    await api(`projects/${d.project.id}/start`, { method: 'POST' });
    L.stage('агенты стартуют', 3);
    await refreshProjects();
    refreshProjectHeader();
    refreshAgents();
    L.done('рой работает');
    setTimeout(() => toast('Рой запущен: ' + agents + ' агентов', 'ok'), 500);
  } catch (e) {
    L.done('не получилось');
    toast(e.message, 'err');
  }
}

$('#btnCreateProject').addEventListener('click', () => {
  const g = $('#npGoal').value.trim();
  if (!g) { toast('Опишите цель проекта', 'info'); return; }
  createProject(g);
  $('#npGoal').value = '';
  closeAtMenu();
});

function renderProjects() {
  const list = $('#projectsList');
  list.innerHTML = '';
  if (!S.projects.length) {
    list.innerHTML = `<div class="empty-state" style="margin-top:6vh">
      <div class="empty-icon"><svg class="ic"><use href="#i-folder"/></svg></div>
      <h3>Проектов пока нет</h3><p>Создайте первый — рой сразу начнáет работу.</p></div>`;
    return;
  }
  S.projects.forEach((p) => {
    const card = el('div', 'project-card');
    card.innerHTML = `
      <div class="pc-icon"><svg class="ic"><use href="#i-folder"/></svg></div>
      <div class="pc-main">
        <div class="pc-title">${esc(p.name)}</div>
        <div class="pc-goal">${esc(p.goal)}</div>
        <div class="pc-bar"><i style="width:${Math.round((p.progress || 0) * 100)}%"></i></div>
        <div class="pc-meta">
          <span>${p.agents_done}/${p.agents_total} агентов${p.agents_error ? ' · ошибок: ' + p.agents_error : ''}</span>
          <span>${fmtNum(p.tokens)} токенов</span>
          <span title="${esc(costTitle(p))}">${esc(costLabel(p))}</span>
          <span>${statusRu(p.status)}</span>
        </div>
      </div>
      <div class="pc-actions">
        <button class="icon-btn" title="Открыть агентов"><svg class="ic"><use href="#i-agents"/></svg></button>
        <button class="icon-btn" title="Файлы"><svg class="ic"><use href="#i-code"/></svg></button>
      </div>`;
    card.addEventListener('click', (e) => {
      const isFiles = e.target.closest('button[title="Файлы"]');
      selectProject(p.id);
      setView(isFiles ? 'workspace' : 'agents');
      if (isFiles) { syncProjectSelect(); initWorkspace(); }
    });
    list.appendChild(card);
  });
}

function statusRu(s) {
  return { idle: 'готов к запуску', running: 'работает', paused: 'пауза',
    done: 'завершён', stopped: 'остановлен', error: 'завершён с ошибками' }[s] || s;
}

async function refreshProjects() {
  try {
    const d = await api('projects');
    S.projects = d.projects;
    renderProjects(); renderPanel();
    if (S.project) {
      const p = S.projects.find((x) => x.id === S.project.id);
      if (p) { S.project = p; refreshProjectHeader(); refreshStats(); }
    }
  } catch (e) { /* тихо */ }
}

function syncProjectSelect() {
  const sel = $('#wsProject');
  sel.innerHTML = '';
  S.projects.forEach((p) => {
    const o = el('option', null, esc(p.name));
    o.value = p.id;
    sel.appendChild(o);
  });
  if (S.ws.project) sel.value = S.ws.project.id;
  sel.onchange = () => {
    const p = S.projects.find((x) => x.id === sel.value);
    if (p) { S.ws.project = p; S.ws.agent = ''; S.ws.file = null; syncScopeSelect(); loadTree(p.id); }
  };
}

/* ------------------------------------------------------------------ агенты */

async function selectProject(pid, opts = {}) {
  const p = S.projects.find((x) => x.id === pid);
  if (!p) return;
  S.project = p;
  S.ws.project = p;
  if (!opts.silent) renderPanel();
  await refreshAgents();
  refreshProjectHeader();
}

async function refreshAgents() {
  if (!S.project) { renderAgents(); return; }
  try {
    const d = await api(`projects/${S.project.id}/agents`);
    S.agents = d.agents || [];
    S.agentMap = new Map(S.agents.map((a) => [a.id, a]));
    renderFilters();
    renderAgents();
    refreshStats();
    refreshProjectHeader();
  } catch (e) { console.error(e); }
}

function refreshProjectHeader() {
  const p = S.project;
  const pill = $('#runStatus');
  if (!p) {
    pill.className = 'status-pill';
    pill.innerHTML = '<i></i><span>нет проекта</span>';
    $('#btnStart').disabled = true; $('#btnPause').disabled = true; $('#btnStop').disabled = true;
    return;
  }
  const running = p.running || p.status === 'running';
  pill.className = 'status-pill ' + (running ? 'running' : (p.status === 'paused' ? 'paused' : (p.status === 'done' ? 'done' : '')));
  pill.innerHTML = `<i></i><span>${statusRu(p.status)}</span>`;
  $('#btnStart').disabled = running;
  $('#btnPause').disabled = !running;
  $('#btnStop').disabled = !running;
  const label = running ? 'Работает' : (p.status === 'idle' ? 'Запустить' : (p.status === 'done' ? 'Заново' : (p.status === 'error' ? 'Повторить ошибки' : (p.user_paused ? 'Продолжить' : 'Запустить'))));
  const icon = running ? '#i-refresh' : '#i-play';
  $('#btnStart').innerHTML = `<svg class="ic"><use href="${icon}"/></svg>${label}`;
  // 5: ручная пауза видна и не снимается сама
  $('#btnPause').classList.toggle('active', !!p.user_paused);
  $('#btnPause').title = p.user_paused ? 'Пауза поставлена вручную — сама не снимется' : 'Поставить на паузу';
  renderPlan(p);
}

function renderPlan(p) {
  const strip = $('#planStrip');
  if (!strip) return;
  const brief = (p && p.brief || '').trim();
  strip.hidden = !brief;
  if (brief) $('.plan-text', strip).textContent = brief.length > 320 ? brief.slice(0, 320) + '…' : brief;
}

function costLabel(p) {
  // 8: не выдумываем $, показываем только то, что реально посчитано по usage модели
  const c = (p && typeof p.cost === 'number') ? p.cost : 0;
  if (c > 0) return '$' + (c < 0.01 ? c.toFixed(4) : c.toFixed(2));
  return p && p.tokens ? 'демо' : '$0';
}

function costTitle(p) {
  if (!p) return '';
  if (p.cost_exact) return 'реальные токены: вход ' + fmtNum(p.tokens_in) + ' · выход ' + fmtNum(p.tokens_out);
  return 'демо-режим: токены условные, стоимость не считается';
}

function refreshStats() {
  const working = S.agents.filter((a) => a.status === 'working').length;
  const done = S.agents.filter((a) => a.status === 'done').length;
  const tokens = S.agents.reduce((s, a) => s + (a.tokens || 0), 0);
  $('#stWorking').textContent = working;
  $('#stDone').textContent = done;
  $('#stTokens').textContent = fmtNum(tokens);
  const costEl = $('#stCost');
  if (costEl) {
    const proj = S.projects.find((x) => S.project && x.id === S.project.id) || S.project || {};
    costEl.textContent = costLabel(proj);
    costEl.parentElement.title = costTitle(proj);
  }
  const badge = $('#badgeAgents');
  badge.textContent = working ? String(working) : '';
  badge.classList.toggle('zero', !working);
  if (S.project) {
    const p = S.projects.find((x) => x.id === S.project.id);
    if (p) { p.agents_working = working; p.agents_done = done; }
  }
}

function renderFilters() {
  const box = $('#laneFilters');
  box.innerHTML = '';
  const lanes = ['all', 'lead', 'main', 'research', 'design', 'code', 'test', 'review', 'docs'];
  lanes.forEach((l) => {
    const cnt = l === 'all' ? S.agents.length : S.agents.filter((a) => a.lane === l).length;
    if (l !== 'all' && !cnt) return;
    const b = el('button', 'filter' + (S.filter.lane === l ? ' active' : ''),
      `${l === 'all' ? '' : actIcon(laneAct(l))}${laneTitle(l)}<b>${cnt}</b>`);
    b.addEventListener('click', () => { S.filter.lane = l; renderFilters(); renderAgents(); });
    box.appendChild(b);
  });
}

let agentGridRendered = false;

function renderAgents() {
  const grid = $('#agentsGrid');
  const emptyBox = $('#agentsEmpty');
  if (!S.project) {
    grid.innerHTML = '';
    emptyBox.hidden = false;
    return;
  }
  emptyBox.hidden = true;
  const q = S.filter.q.toLowerCase();
  const list = S.agents.filter((a) =>
    (S.filter.lane === 'all' || a.lane === S.filter.lane) &&
    (!q || (a.nick || '').toLowerCase().includes(q) || (a.task || '').toLowerCase().includes(q)));

  grid.innerHTML = '';
  list.forEach((a) => grid.appendChild(agentCard(a)));
  agentGridRendered = true;
}

function agentCard(a) {
  const card = el('button', 'agent-card');
  card.dataset.agent = a.id;
  const st = a.status || 'idle';
  card.classList.add(st === 'done' ? 'done' : st === 'working' ? 'working' : st === 'error' ? 'error' : 'idle');
  const kind = st === 'done' ? 'done' : st === 'error' ? 'error' : st === 'idle' ? 'wait' : (a.activity || laneAct(a.lane));
  card.innerHTML = `
    <div class="ac-top">
      ${agentAvatar(a, 'md', st === 'working')}
      <div class="ac-id">
        <div class="ac-nick" title="${esc(a.nick)}">${esc(a.nick)}</div>
        <div class="ac-role">${esc(a.role)}</div>
      </div>
    </div>
    <div class="ac-act">${actIcon(kind)}<span class="ac-act-text">${esc(shortTask(a, st))}</span></div>
    <div class="ac-foot">
      <span class="ac-dot"></span>
      <span class="ac-tokens">${fmtNum(a.tokens || 0)} т</span>
    </div>`;
  card.addEventListener('click', () => openDrawer(a.id));
  return card;
}

function shortTask(a, st) {
  if (st === 'done') return 'закончил · ' + (a.role || '');
  if (st === 'idle') return 'ждёт запуска';
  if (st === 'error') return 'ошибка — открой карточку';
  return a.task || a.role || '';
}

function updateAgentCard(a, liveText) {
  const card = $(`.agent-card[data-agent="${a.id}"]`);
  if (!card) return;
  const st = a.status || 'idle';
  card.className = 'agent-card ' + (st === 'done' ? 'done' : st === 'working' ? 'working' : st === 'error' ? 'error' : 'idle');
  const av = $('.avatar', card);
  if (av) av.classList.toggle('ring', st === 'working');
  const kind = st === 'done' ? 'done' : st === 'error' ? 'error' : st === 'idle' ? 'wait' : (a.activity || laneAct(a.lane));
  const actBox = $('.ac-act', card);
  if (actBox) {
    actBox.innerHTML = actIcon(kind) +
      `<span class="ac-act-text">${esc(liveText && st === 'working' ? liveText : shortTask(a, st))}</span>`;
  }
  const tok = $('.ac-tokens', card);
  if (tok) tok.textContent = fmtNum(a.tokens || 0) + ' т';
}

$('#agentSearch').addEventListener('input', (e) => { S.filter.q = e.target.value; renderAgents(); });

$('#btnStart').addEventListener('click', async () => {
  if (!S.project) return;
  try {
    const d = await api(`projects/${S.project.id}/start`, { method: 'POST', body: { pending_only: S.project.status === 'error' } });
    if (d.ok === false) toast(d.reason || 'не удалось', 'err');
    else toast('Рой запущен', 'ok');
    await refreshProjects();
    refreshProjectHeader();
    setTimeout(() => { refreshAgents(); refreshProjects(); }, 400);
  } catch (e) { toast(e.message, 'err'); }
});
$('#btnPause').addEventListener('click', async () => {
  if (!S.project) return;
  const p = S.projects.find((x) => x.id === S.project.id);
  const paused = p && p.status === 'paused';
  await api(`projects/${S.project.id}/${paused ? 'resume' : 'pause'}`, { method: 'POST' });
  toast(paused ? 'Продолжаю' : 'Пауза', 'info');
  refreshProjects();
  setTimeout(refreshProjectHeader, 200);
});
$('#btnStop').addEventListener('click', async () => {
  if (!S.project) return;
  await api(`projects/${S.project.id}/stop`, { method: 'POST' });
  toast('Останавливаю рой', 'info');
  setTimeout(() => { refreshAgents(); refreshProjects(); }, 400);
});

/* ------------------------------------------------------------------ drawer */

async function openDrawer(aid) {
  const a = S.agentMap.get(aid);
  if (!a) return;
  stopConsole();
  S.con = { events: [], last: 0, total: 0, timer: null, loading: false };
  S.drawerAgent = a;
  S.drawerTab = 'now';
  S.agentEvents = S.events.filter((e) => e.agent === aid);
  const d = $('#drawer');
  d.classList.add('open');
  d.setAttribute('aria-hidden', 'false');
  $('#drawerScrim').hidden = false;
  try {
    const full = await api('agents/' + aid);
    S.drawerAgent = full.agent;
    S.agentEvents = full.events || [];
    S.drawerMessages = full.messages || [];
  } catch (e) { S.drawerMessages = []; }
  renderDrawer();
}

function closeDrawer() {
  stopConsole();
  $('#drawer').classList.remove('open');
  $('#drawer').setAttribute('aria-hidden', 'true');
  $('#drawerScrim').hidden = true;
  S.drawerAgent = null;
}
$('#drawerScrim').addEventListener('click', closeDrawer);

function renderDrawer() {
  const a = S.drawerAgent;
  if (!a) return;
  const st = a.status || 'idle';
  const kind = st === 'done' ? 'done' : st === 'error' ? 'error' : st === 'idle' ? 'wait' : (a.activity || laneAct(a.lane));
  let files = [];
  try { files = JSON.parse(a.files || '[]'); } catch (e) { files = []; }
  const d = $('#drawer');
  d.innerHTML = `
    <div class="dr-head">
      <div class="dr-top">
        ${agentAvatar(a, 'lg', st === 'working')}
        <div class="dr-name">
          <h3>${esc(a.nick)}</h3>
          <p>${esc(a.role)} · ${st === 'done' ? 'закончил' : st === 'working' ? 'работает' : st === 'error' ? 'ошибка' : 'ждёт'}</p>
        </div>
        <button class="icon-btn" id="drClose"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>
      <div class="dr-now">${actIcon(kind)}<span class="ac-act-text">${esc(a.task || 'нет активной задачи')}</span></div>
    </div>
    <div class="dr-tabs">
      ${['now:Сейчас', 'live:Черновик', 'con:Консоль', 'feed:Лента', 'msgs:Сообщения', 'files:Файлы'].map((x) => {
        const [k, t] = x.split(':');
        return `<button class="dr-tab ${S.drawerTab === k ? 'active' : ''}" data-tab="${k}">${t}</button>`;
      }).join('')}
    </div>
    <div class="dr-body" id="drBody"></div>`;
  $('#drClose').addEventListener('click', closeDrawer);
  $$('.dr-tab', d).forEach((b) => b.addEventListener('click', () => {
    S.drawerTab = b.dataset.tab;
    stopConsole();
    renderDrawer();
  }));
  renderDrawerBody();
}

function renderDrawerBody() {
  const a = S.drawerAgent;
  const box = $('#drBody');
  if (!a || !box) return;
  if (S.drawerTab === 'live') {
    box.innerHTML = `<div class="live-panel"><details open><summary>Черновик ответа</summary><pre class="agent-draft"></pre></details><details><summary>Рассуждение модели</summary><pre class="agent-thought"></pre></details><p class="hint">Черновик — текущий ответ, ещё не окончательный. Рассуждение видно только если его предоставляет API модели. Данные доступны во время работы вкладки и не сохраняются в историю.</p></div>`;
    updateAgentLive(); return;
  }
  if (S.drawerTab === 'con') { renderConsole(a, box); return; }
  if (S.drawerTab === 'msgs') {
    const msgs = S.drawerMessages || [];
    box.innerHTML = msgs.length ? msgs.map((m) => `
      <div class="msgrow">
        <div style="flex:1">
          <div class="mr-to">${m.from_agent === a.id
            ? '→ <span class="mr-who">' + esc(m.to_nick || 'коллеге') + '</span>'
            : '← от <span class="mr-who">' + esc(m.from_nick || 'коллеги') + '</span>'} · ${clock(m.ts)}</div>
          <div class="mr-text">${esc(m.text)}</div>
        </div>
        <button class="btn btn-ghost sm agent-msg-copy" title="Копировать сообщение">Копировать</button>
      </div>`).join('') : '<div class="empty-tab">Пока нет переписки: агент никому не писал и ему не писали</div>';
    box.querySelectorAll('.agent-msg-copy').forEach((button, i) => button.addEventListener('click', () => copyText(msgs[i].text || '', 'Сообщение скопировано')));
    return;
  }
  if (S.drawerTab === 'files') {
    let files = [];
    try { files = JSON.parse(a.files || '[]'); } catch (e) { files = []; }
    if (!files.length) { box.innerHTML = '<div class="empty-tab">Агент ещё не трогал файлы</div>'; return; }
    // файлы лежат в ЛИЧНОМ workspace агента — открываем именно его песочницу
    box.innerHTML = `<div class="empty-tab" style="text-align:left;padding:0 0 10px">Личный workspace агента.
      Главный агент забирает отсюда работу и сводит её в общий проект.</div>` +
      files.map((f) => `<button class="ws-file">
      <svg class="ic"><use href="#i-file"/></svg><span>${esc(f)}</span></button>`).join('');
    // путь кладём через dataset, а не в разметку: имя файла приходит от модели
    Array.from(box.querySelectorAll('.ws-file')).forEach((b, i) => { b.dataset.open = files[i]; });
    $$('[data-open]', box).forEach((b) => b.addEventListener('click', () => {
      S.ws.project = S.project;
      S.ws.agent = a.id;                     // смотрим песочницу этого агента
      setView('workspace');
      syncProjectSelect();
      syncScopeSelect();
      loadTree(S.project.id).then(() => openFile(b.dataset.open));
      closeDrawer();
    }));
    return;
  }

  const evs = (S.agentEvents || []).slice();
  const list = S.drawerTab === 'now' ? evs.slice(-12) : evs;
  if (!list.length) { box.innerHTML = '<div class="empty-tab">Событий пока нет</div>'; return; }
  box.innerHTML = '<div class="tl">' + list.map(tlItem).join('') + '</div>';
  box.scrollTop = box.scrollHeight;
}

function updateAgentLive() {
  if (!S.project || !S.drawerAgent) return;
  const live = S.liveAgents.get(S.project.id + ':' + S.drawerAgent.id) || {};
  const draft = $('.agent-draft'); const thought = $('.agent-thought');
  if (draft) draft.textContent = live.draft || 'Ожидание черновика…';
  if (thought) thought.textContent = live.thought || 'Читаемое рассуждение недоступно: API может не возвращать его или передавать в зашифрованном виде.';
}

/* ------------------------------------------------------------------ консоль агента */

const CON_MARK = { llm: '»', start: '▶', stop: '✓', error: '✖' };

function conEventToLines(e) {
  const d = e.data || {};
  const ts = clock(e.ts);
  const push = (cls, mark, text) => ({ cls, mark, ts, text: String(text == null ? '' : text) });
  if (e.type === 'tool') {
    // событие «команда запущена» приходит без вывода — в консоли его не дублируем
    if (!d.out_full && (d.out === '' || d.out == null) && d.ok !== false) return [];
    const lines = [push('cmd', '$', d.cmd || e.text || 'команда')];
    const body = d.out_full || d.out || '';
    if (body) {
      body.split('\n').slice(0, 500).forEach((l) => lines.push(push(d.ok === false ? 'out err' : 'out', '', l)));
    }
    const tail = d.ok === false ? 'код: ошибка' : (d.ms != null ? d.ms + ' мс' : '');
    if (tail) lines.push(push('sys', '', tail));
    return lines;
  }
  if (e.type === 'file') {
    return [push('file', '✎', (d.action === 'write' ? 'создал ' : 'изменил ') + (d.path || e.text || ''))];
  }
  if (e.type === 'console') {
    const kind = d.kind || 'sys';
    if (kind === 'llm') return [push('llm', CON_MARK.llm, e.text || 'ответ модели')];
    return [push(kind === 'error' ? 'err' : 'done', CON_MARK[kind] || '·', e.text || '')];
  }
  if (e.type === 'activity') return [push('sys', '·', (d.kind ? d.kind + ': ' : '') + (e.text || ''))];
  if (e.type === 'error') return [push('err', '✖', e.text || 'ошибка')];
  if (e.type === 'result') {
    const raw = (e.text || '').trim();
    return [push('llm', '↩', raw.length > 420 ? raw.slice(0, 420) + ' …' : raw)];
  }
  if (e.type === 'done') return [push('done', '✓', 'закончил')];
  if (e.type === 'web') return [push('sys', '↗', 'открыл ' + ((d.url || '').slice(0, 120)))];
  if (e.type === 'message') {
    const who = d.to_nick || d.to || 'коллеге';
    const what = d.text || e.text || '';
    return [push('sys', '→', 'сообщение ' + who + (what ? ': ' + (what.length > 200 ? what.slice(0, 200) + ' …' : what) : ''))];
  }
  return [push('sys', '·', e.text || e.type)];
}

function renderConsole(a, box) {
  const live = a.status === 'working';
  box.innerHTML = `
    <div class="con-bar">
      <span class="con-dot ${live ? 'live' : ''}"></span>
      <span class="con-stat" id="conStat">${live ? 'агент работает — вывод идёт вживую'
        : 'агент ' + (a.status === 'error' ? 'упал' : a.status === 'done' ? 'закончил' : 'ждёт')}</span>
      <button class="btn btn-ghost sm" id="conRefresh">Обновить</button>
      <button class="btn btn-ghost sm" id="conCopy">Копировать лог</button>
      <button class="btn btn-ghost sm" id="conSave">Скачать .txt</button>
    </div>
    <div class="con" id="conBox"><div class="con-empty">Загружаю консоль…</div></div>`;
  $('#conRefresh').addEventListener('click', () => loadConsole(a.id, 0, true));
  $('#conSave').addEventListener('click', () => downloadConsole(a.id));
  $('#conCopy').addEventListener('click', async () => {
    try {
      const res = await fetch(`/api/agents/${a.id}/console.txt`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error('сервер ответил ' + res.status);
      await copyText(await res.text(), 'Лог агента скопирован');
    } catch (e) { toast('Не удалось получить лог: ' + e.message, 'err'); }
  });
  loadConsole(a.id, 0, true).then(startConsole);
}

function paintConsole() {
  const box = $('#conBox');
  if (!box) return;
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  const evs = S.con.events;
  if (!evs.length) { box.innerHTML = '<div class="con-empty">Консоль пуста: агент ещё ничего не делал</div>'; return; }
  const html = [];
  evs.forEach((e) => {
    conEventToLines(e).forEach((l) => {
      html.push(`<div class="con-line ${esc(l.cls)}">` +
        `<span class="con-ts">${l.ts}</span>` +
        `<span class="con-mark">${esc(l.mark)}</span>` +
        `<span class="con-text">${esc(l.text)}</span></div>`);
    });
  });
  box.innerHTML = html.join('');
  if (atBottom) box.scrollTop = box.scrollHeight;
  const stat = $('#conStat');
  if (stat) stat.textContent = `строк ${html.length} · событий ${evs.length}`;
}

async function loadConsole(aid, since, replace) {
  if (S.con.loading) return;
  S.con.loading = true;
  try {
    const d = await api(`agents/${aid}/console?since=${since || 0}&limit=600`);
    if (replace || !since) S.con.events = d.events || [];
    else S.con.events = S.con.events.concat(d.events || []);
    S.con.last = d.last || S.con.last;
    S.con.total = d.total || 0;
    paintConsole();
  } catch (e) {
    const box = $('#conBox');
    if (box && replace) box.innerHTML = `<div class="con-empty">Не удалось загрузить консоль: ${esc(e.message)}</div>`;
  } finally {
    S.con.loading = false;
  }
}

function startConsole() {
  stopConsole();
  const a = S.drawerAgent;
  if (!a) return;
  S.con.timer = setInterval(() => {
    if (S.drawerTab !== 'con' || !S.drawerAgent) return stopConsole();
    loadConsole(S.drawerAgent.id, S.con.last, false);
  }, 1200);
}

function stopConsole() {
  if (S.con.timer) { clearInterval(S.con.timer); S.con.timer = null; }
}

async function downloadConsole(aid) {
  try {
    const res = await fetch(`/api/agents/${aid}/console.txt`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error('сервер ответил ' + res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `console-${(S.drawerAgent && S.drawerAgent.nick) || aid}.txt`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    toast('Консоль сохранена в файл', 'ok');
  } catch (e) { toast('Не удалось скачать консоль: ' + e.message, 'err'); }
}

function tlItem(e) {
  const kind = e.type === 'activity' ? ((e.data && e.data.kind) || 'think')
    : e.type === 'file' ? 'edit'
    : e.type === 'tool' ? 'tool'
    : e.type === 'message' ? 'msg'
    : e.type === 'web' ? 'web'
    : e.type === 'result' ? 'done'
    : e.type === 'error' ? 'error'
    : e.type === 'done' ? 'done' : 'think';
  let text = e.text || '';
  let out = '';
  if (e.type === 'tool' && e.data) {
    out = `<div class="tl-out ${e.data.ok === false ? 'err' : ''}">$ ${esc(e.data.cmd || '')}\n${esc(e.data.out || '')}</div>`;
    text = 'терминал';
  } else if (e.type === 'message' && e.data) {
    text = `→ ${esc(e.data.to || '')}: ${esc(e.data.text || '')}`;
  } else if (e.type === 'file' && e.data) {
    text = `${e.data.action === 'write' ? 'создал' : 'изменил'} <code>${esc(e.data.path || e.text)}</code>`;
  } else if (e.type === 'web' && e.data) {
    text = 'открыл <code>' + esc((e.data.url || '').slice(0, 60)) + '</code>';
  } else if (e.type === 'token' || e.type === 'result') {
    text = '<span style="color:var(--muted)">' + esc(text.slice(-220)) + '</span>';
  }
  return `<div class="tl-item">
    <div class="tl-ic">${actIcon(kind)}</div>
    <div class="tl-main"><div class="tl-text">${text}</div>
    <div class="tl-meta"><span>${clock(e.ts)}</span></div>${out}</div>
  </div>`;
}

/* ------------------------------------------------------------------ workspace */

function initWorkspace() {
  if (!S.ws.project) S.ws.project = S.project || S.projects[0];
  if (S.ws.project) {
    $('#wsProject').value = S.ws.project.id;
    syncScopeSelect();
    loadTree(S.ws.project.id);
  } else {
    $('#wsTree').innerHTML = '<div class="empty-tab">Нет проектов</div>';
  }
}

// У каждого агента свой workspace. В списке: «Проект (сводный)» + все агенты проекта.
function syncScopeSelect() {
  const sel = $('#wsScope');
  if (!sel) return;
  const pid = S.ws.project && S.ws.project.id;
  const agents = (S.agents || []).filter((a) => !pid || a.project_id === pid);
  sel.innerHTML = '<option value="">Проект (сводный)</option>' +
    agents.map((a) => `<option value="${esc(a.id)}">${esc(a.nick)}${a.lane === 'main' ? ' · главный' : ''}</option>`).join('');
  sel.value = S.ws.agent || '';
  sel.onchange = () => {
    S.ws.agent = sel.value;
    S.ws.file = null;
    $('#wsEditor').value = '';
    $('#wsFilePath').textContent = S.ws.agent ? 'личный workspace агента' : 'общий workspace проекта';
    loadTree(S.ws.project.id);
  };
}

async function loadTree(pid) {
  try {
    const q = S.ws.agent ? '?agent=' + encodeURIComponent(S.ws.agent) : '';
    const d = await api(`workspace/${pid}/tree` + q);
    S.ws.tree = d.files || [];
    renderTree();
  } catch (e) { $('#wsTree').innerHTML = '<div class="empty-tab">Не удалось прочитать workspace</div>'; }
}

function renderTree() {
  const box = $('#wsTree');
  box.innerHTML = '';
  if (!S.ws.tree.length) {
    box.innerHTML = '<div class="empty-tab">' + (S.ws.agent
      ? 'У этого агента пока пусто в личном workspace'
      : 'Пусто. Агенты ещё ничего не сдали, либо выберите агента в списке сверху.') + '</div>';
    return;
  }
  S.ws.tree.forEach((f) => {
    const b = el('button', 'ws-file' + (S.ws.file === f.path ? ' active' : ''),
      `<svg class="ic"><use href="#i-file"/></svg><span>${esc(f.path)}</span>`);
    b.title = f.path + ' · ' + bytes(f.size);
    b.addEventListener('click', () => openFile(f.path));
    box.appendChild(b);
  });
}

async function openFile(path) {
  if (!S.ws.project) return;
  try {
    const sc = S.ws.agent ? '&agent=' + encodeURIComponent(S.ws.agent) : '';
    const d = await api(`workspace/${S.ws.project.id}/file?path=` + encodeURIComponent(path) + sc);
    S.ws.file = path;
    S.ws.content = d.content;
    S.ws.locked = !!d.truncated || !!d.binary;
    S.ws.binary = !!d.binary;
    $('#wsEditor').value = d.binary ? 'Это бинарный файл — откройте предпросмотр или скачайте.' : d.content;
    $('#wsEditor').readOnly = !!d.binary;
    $('#wsFilePath').textContent = path;
    $('#wsFileMeta').textContent = bytes(d.size) + ' · изменён ' + timeAgo(d.mtime);
    $('#wsSave').disabled = S.ws.locked;      // 16: не даём затереть остаток файла
    if (d.binary) toast('Бинарный файл: текстовый редактор недоступен, есть предпросмотр и скачивание', 'info');
    else if (d.truncated) toast('Файл больше лимита редактора — показан фрагмент, сохранение выключено', 'info');
    renderTree();
    if (S.ws.mode === 'preview') showPreview();
  } catch (e) { toast(e.message, 'err'); }
}

$('#wsSave').addEventListener('click', async () => {
  if (!S.ws.project || !S.ws.file) return;
  if (S.ws.locked) { toast('Этот файл нельзя сохранять из редактора: он больше лимита или бинарный', 'info'); return; }
  try {
    await api(`workspace/${S.ws.project.id}/file`, {
      method: 'PUT', body: { path: S.ws.file, content: $('#wsEditor').value, agent: S.ws.agent || '' },
    });
    toast('Файл сохранён', 'ok');
    loadTree(S.ws.project.id);
  } catch (e) { toast(e.message, 'err'); }
});
$('#wsCopy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('#wsEditor').value); toast('Скопировано', 'ok'); }
  catch (e) { toast('Не удалось скопировать', 'err'); }
});
$('#wsDownload').addEventListener('click', async () => {
  // скачивание идёт по той же подписи, что и предпросмотр
  const url = await previewUrl(S.ws.project.id, S.ws.file);
  if (!url) return;
  const sc = S.ws.agent ? '?agent=' + encodeURIComponent(S.ws.agent) : '';
  location.href = url.replace('/preview/', '/download/') + sc;
});
$('#wsRefresh').addEventListener('click', () => {
  if (S.ws.project) loadTree(S.ws.project.id);
  if (S.ws.file) openFile(S.ws.file);
});
$$('#wsMode button').forEach((b) => b.addEventListener('click', () => {
  S.ws.mode = b.dataset.mode;
  $$('#wsMode button').forEach((x) => x.classList.toggle('active', x === b));
  if (S.ws.mode === 'preview') showPreview();
  else {
    $('#wsPreviewWrap').hidden = true;
    $('#wsEditor').style.display = '';
  }
}));

function showPreview() {
  if (!S.ws.project) { toast('Сначала выберите проект', 'info'); return; }
  const isHtml = (f) => /\.(html?|svg)$/i.test(f || '');
  if (!isHtml(S.ws.file)) {
    const idx = S.ws.tree.find((f) => /(^|\/)index\.html?$/i.test(f.path));
    const any = S.ws.tree.find((f) => isHtml(f.path));
    const target = idx || any;
    if (!target) { toast('В проекте нет HTML-файлов для preview', 'info'); return; }
    openFile(target.path);
    setTimeout(() => { if (isHtml(S.ws.file)) showPreview(); }, 350);
    return;
  }
  $('#wsEditor').style.display = 'none';
  $('#wsPreviewWrap').hidden = false;
  previewUrl(S.ws.project.id, S.ws.file).then((url) => { if (url) $('#wsPreview').src = url; });
}

/* Подписанная ссылка предпросмотра: iframe в sandbox без allow-same-origin,
   его подресурсы (css/js/картинки) идут без cookie — поэтому подпись в пути. */
async function previewUrl(pid, rel) {
  try {
    const d = await api('preview-token/' + pid);
    const path = String(rel || '').split('/').map(encodeURIComponent).join('/');
    return d.base + path + '?t=' + Date.now();
  } catch (e) {
    toast('Не удалось открыть preview: ' + e.message, 'err');
    return '';
  }
}

/* ------------------------------------------------------------------ настройки */

function fillSettings() {
  const s = S.settings || {};
  if ($('#setModel')) $('#setModel').value = s.model || 'mercury-3';
  if ($('#setBaseUrl')) $('#setBaseUrl').value = s.base_url || '';
  if ($('#setReasoning')) $('#setReasoning').value = s.reasoning || 'medium';
  if ($('#chatEffort')) $('#chatEffort').value = s.reasoning || 'medium';
  if ($('#setConcurrency')) $('#setConcurrency').value = s.concurrency || 6;
  if ($('#setDemo')) $('#setDemo').checked = !!s.demo_mode;
  if ($('#setTerminal')) $('#setTerminal').checked = !!s.allow_terminal;
  if ($('#setStrict')) $('#setStrict').checked = !!s.terminal_strict;
  if ($('#setNetwork')) $('#setNetwork').checked = !!s.allow_network;
  if ($('#setTimeout')) $('#setTimeout').value = s.terminal_timeout || 20;
  if ($('#setLoopGuard')) $('#setLoopGuard').checked = s.loop_guard !== false;
  if ($('#setMergeInterval')) $('#setMergeInterval').value = (s.merge_interval == null ? 20 : s.merge_interval);
  if ($('#setSearx')) $('#setSearx').value = s.searx_url || '';
  // ---- почта ----
  if ($('#setMailImapHost')) $('#setMailImapHost').value = s.mail_imap_host || '';
  if ($('#setMailImapPort')) $('#setMailImapPort').value = s.mail_imap_port || 993;
  if ($('#setMailImapEnc')) $('#setMailImapEnc').value = s.mail_imap_enc || 'ssl';
  if ($('#setMailSmtpHost')) $('#setMailSmtpHost').value = s.mail_smtp_host || '';
  if ($('#setMailSmtpPort')) $('#setMailSmtpPort').value = s.mail_smtp_port || 465;
  if ($('#setMailSmtpEnc')) $('#setMailSmtpEnc').value = s.mail_smtp_enc || 'ssl';
  if ($('#setMailUser')) $('#setMailUser').value = s.mail_user || '';
  if ($('#setMailFrom')) $('#setMailFrom').value = s.mail_from || '';
  if ($('#setMailFolder')) $('#setMailFolder').value = s.mail_folder || 'INBOX';
  if ($('#setMailSend')) $('#setMailSend').checked = s.mail_allow_send !== false;
  if ($('#mailHint')) {
    const miss = (s.mail_problems || []).filter(Boolean);
    $('#mailHint').textContent = s.mail_password_set
      ? (miss.length ? 'Пароль сохранён, но пока не хватает: ' + miss.join('; ')
                     : 'Пароль сохранён, почта готова. Агенты умеют mail_search / mail_read / mail_send.')
      : (miss.length ? 'Ещё не настроено: ' + miss.join('; ')
                     : 'Пароль не задан — агенты почту не видят.');
  }
  if ($('#setLight')) $('#setLight').checked = (s.theme === 'light');
  // ---- музыка и обучение (1.5.0) ----
  if ($('#setMusicOn')) $('#setMusicOn').checked = s.music_on !== false;
  if ($('#setMusicVolume')) {
    $('#setMusicVolume').value = (s.music_volume == null ? 35 : s.music_volume);
    if ($('#musicVolLabel')) $('#musicVolLabel').textContent = $('#setMusicVolume').value;
  }
  if ($('#setMusicFade')) $('#setMusicFade').checked = s.music_fade !== false;
  // ---- MCP (1.6.0) ----
  if ($('#setMcpOn')) $('#setMcpOn').checked = s.mcp_on !== false;
  if ($('#setMcpPrompt')) $('#setMcpPrompt').checked = s.mcp_prompt !== false;
  if ($('#setMcpTimeout')) $('#setMcpTimeout').value = (s.mcp_timeout == null ? 0 : s.mcp_timeout);
  $('#keyHint').textContent = s.has_key
    ? 'Ключ сохранён: ' + (s.key_hint || '•••')
    : 'Ключ не задан — работает демо-режим.';
  $$('#accents .sw').forEach((b) => b.classList.toggle('active', b.dataset.accent === (s.accent || 'violet')));
}

async function saveSettings(patch, quiet) {
  try {
    const d = await api('settings', { method: 'PUT', body: patch });
    S.settings = d.settings;
    applyTheme();
    fillSettings();
    if (window.syncMusicFromSettings) syncMusicFromSettings();
    if (!quiet) toast('Настройки сохранены', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

function applyTheme() {
  document.documentElement.dataset.theme = S.settings.theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.accent = S.settings.accent || 'violet';
}

$('#btnTestProvider').addEventListener('click', async () => {
  const button = $('#btnTestProvider');
  const output = $('#providerTestResult');
  const spinner = button.querySelector('.provider-spinner');
  const patch = { base_url: $('#setBaseUrl').value.trim(), model: $('#setModel').value.trim() };
  const key = $('#setApiKey').value.trim();
  if (key) patch.api_key = key;
  button.disabled = true;
  spinner.hidden = false;
  output.className = 'hint';
  output.textContent = 'Проверяю провайдера…';
  try {
    const result = await api('settings/test_provider', { method: 'POST', body: patch });
    output.className = 'hint provider-ok';
    output.textContent = '✓ модель ответила за ' + Number(result.latency).toFixed(2) + ' с: «' + result.sample + '»';
  } catch (e) {
    output.className = 'hint provider-error';
    output.textContent = 'Ошибка: ' + e.message;
  } finally {
    spinner.hidden = true;
    button.disabled = false;
  }
});

$('#btnSaveSettings').addEventListener('click', () => {
  const patch = {
    model: $('#setModel').value.trim() || 'mercury-3',
    base_url: $('#setBaseUrl').value.trim(),
    reasoning: $('#setReasoning').value,
    concurrency: Number($('#setConcurrency').value) || 6,
    demo_mode: $('#setDemo').checked ? 1 : 0,
    allow_terminal: $('#setTerminal').checked ? 1 : 0,
    terminal_strict: $('#setStrict').checked ? 1 : 0,
    allow_network: $('#setNetwork').checked ? 1 : 0,
    terminal_timeout: Number($('#setTimeout').value) || 20,
    loop_guard: $('#setLoopGuard') && $('#setLoopGuard').checked ? 1 : 0,
    merge_interval: Number($('#setMergeInterval') && $('#setMergeInterval').value) || 0,
    searx_url: ($('#setSearx') && $('#setSearx').value.trim()) || '',
    mail_imap_host: ($('#setMailImapHost') && $('#setMailImapHost').value.trim()) || '',
    mail_imap_port: Number(($('#setMailImapPort') || {}).value) || 993,
    mail_imap_enc: ($('#setMailImapEnc') && $('#setMailImapEnc').value) || 'ssl',
    mail_smtp_host: ($('#setMailSmtpHost') && $('#setMailSmtpHost').value.trim()) || '',
    mail_smtp_port: Number(($('#setMailSmtpPort') || {}).value) || 465,
    mail_smtp_enc: ($('#setMailSmtpEnc') && $('#setMailSmtpEnc').value) || 'ssl',
    mail_user: ($('#setMailUser') && $('#setMailUser').value.trim()) || '',
    mail_from: ($('#setMailFrom') && $('#setMailFrom').value.trim()) || '',
    mail_folder: ($('#setMailFolder') && $('#setMailFolder').value.trim()) || 'INBOX',
    mail_allow_send: $('#setMailSend') && $('#setMailSend').checked ? 1 : 0,
    theme: $('#setLight').checked ? 'light' : 'dark',
    music_on: ($('#setMusicOn') && !$('#setMusicOn').checked) ? 0 : 1,
    music_volume: Number(($('#setMusicVolume') || {}).value) || 0,
    music_fade: ($('#setMusicFade') && !$('#setMusicFade').checked) ? 0 : 1,
  };
  const mailPass = $('#setMailPassword') && $('#setMailPassword').value.trim();
  if (mailPass) patch.mail_password = mailPass;
  const key = $('#setApiKey').value.trim();
  if (key) patch.api_key = key;
  saveSettings(patch);
  if (key) $('#setApiKey').value = '';
  if (mailPass && $('#setMailPassword')) $('#setMailPassword').value = '';
});
$('#btnSaveMcp').addEventListener('click', () => saveSettings({
  mcp_on: $('#setMcpOn').checked ? 1 : 0,
  mcp_prompt: $('#setMcpPrompt').checked ? 1 : 0,
  mcp_timeout: Number($('#setMcpTimeout').value) || 0
}));
if ($('#chatEffort')) $('#chatEffort').addEventListener('change', () => saveSettings({ reasoning: $('#chatEffort').value }, true));
$('#btnClearKey').addEventListener('click', () => saveSettings({ clear_key: true }));
if ($('#btnClearMailPass')) {
  $('#btnClearMailPass').addEventListener('click', () => saveSettings({ clear_mail_password: true }));
}
if ($('#btnMailTest')) {
  $('#btnMailTest').addEventListener('click', async () => {
    const out = $('#mailTestOut');
    if (out) out.textContent = 'Проверяю IMAP…';
    try {
      const d = await api('mail/test', { method: 'POST' });
      if (out) out.textContent = d.ok ? ('Готово: ' + (d.text || 'связь есть')) : ('Не вышло: ' + (d.error || 'нет связи'));
      toast(d.ok ? 'Почта отвечает' : 'Почта не отвечает', d.ok ? 'ok' : 'err');
    } catch (e) {
      if (out) out.textContent = 'Ошибка: ' + e.message;
      toast(e.message, 'err');
    }
  });
}
$('#setDemo').addEventListener('change', () => saveSettings({ demo_mode: $('#setDemo').checked ? 1 : 0 }, true));
if ($('#setLoopGuard')) {
  $('#setLoopGuard').addEventListener('change',
    () => saveSettings({ loop_guard: $('#setLoopGuard').checked ? 1 : 0 }, true));
}
$('#btnWipe').addEventListener('click', async () => {
  if (!confirm('Удалить все проекты и чаты? Файлы в workspace останутся.')) return;
  for (const p of S.projects) { try { await api('projects/' + p.id, { method: 'DELETE' }); } catch (e) {} }
  for (const c of S.chats) { try { await api('chats/' + c.id, { method: 'DELETE' }); } catch (e) {} }
  S.projects = []; S.chats = []; S.project = null; S.ws.project = null;
  renderProjects(); renderPanel(); renderChatEmpty(); refreshAgents();
  toast('Данные удалены', 'ok');
});

$$('#accents .sw').forEach((b) => b.addEventListener('click', () => saveSettings({ accent: b.dataset.accent }, true)));
$('#setLight').addEventListener('change', () => saveSettings({ theme: $('#setLight').checked ? 'light' : 'dark' }, true));

async function logout() {
  await api('auth/logout', { method: 'POST' });
  location.reload();
}
$('#btnLogout').addEventListener('click', logout);

/* ------------------------------------------------------------------ палитра */

const COMMANDS = [
  { id: 'chats', title: 'Чаты', icon: '#i-chat', run: () => setView('chats') },
  { id: 'agents', title: 'Агенты', icon: '#i-agents', run: () => setView('agents') },
  { id: 'projects', title: 'Проекты', icon: '#i-folder', run: () => setView('projects') },
  { id: 'workspace', title: 'Файлы проекта', icon: '#i-code', run: () => setView('workspace') },
  { id: 'settings', title: 'Настройки', icon: '#i-gear', run: () => setView('settings') },
  { id: 'newchat', title: 'Новый чат', icon: '#i-plus', run: newChat },
  { id: 'newproj', title: 'Новый проект (рой агентов)', icon: '#i-spark', run: () => setView('projects') },
  { id: 'start', title: 'Запустить рой', icon: '#i-play', run: () => $('#btnStart').click() },
  { id: 'pause', title: 'Пауза / продолжить', icon: '#i-pause', run: () => $('#btnPause').click() },
  { id: 'stop', title: 'Остановить рой', icon: '#i-stop', run: () => $('#btnStop').click() },
  { id: 'preview', title: 'Preview HTML', icon: '#i-eye', run: () => { setView('workspace'); S.ws.mode = 'preview'; showPreview(); } },
];

function openPalette() {
  $('#paletteWrap').hidden = false;
  $('#paletteInput').value = '';
  renderPalette('');
  $('#paletteInput').focus();
}
function closePalette() { $('#paletteWrap').hidden = true; }

function renderPalette(q) {
  const list = $('#paletteList');
  const items = COMMANDS.filter((c) => !q || c.title.toLowerCase().includes(q.toLowerCase()));
  list.innerHTML = items.map((c, i) => `<button class="palette-item ${i === 0 ? 'active' : ''}" data-cmd="${c.id}">
    <svg class="ic"><use href="${c.icon}"/></svg>${c.title}</button>`).join('') ||
    '<div class="empty-tab">Ничего не найдено</div>';
  $$('.palette-item', list).forEach((b) => b.addEventListener('click', () => {
    const cmd = COMMANDS.find((c) => c.id === b.dataset.cmd);
    closePalette();
    if (cmd) cmd.run();
  }));
}

$('#paletteInput').addEventListener('input', (e) => renderPalette(e.target.value));
$('#paletteWrap').addEventListener('click', (e) => { if (e.target.id === 'paletteWrap') closePalette(); });
$('#kbdHint').addEventListener('click', openPalette);

document.addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  if (e.key === 'Escape') { closePalette(); closeDrawer(); }
  if (mod && e.key === 'Enter') { const c = $('#composer'); if (document.activeElement === c) sendMessage(c.value); }
});

/* ------------------------------------------------------------------ ws-редактор */

$('#wsEditor').addEventListener('input', () => { S.ws.dirty = true; });
window.addEventListener('beforeunload', (e) => {
  if (S.ws.dirty) { /* оставляем браузеру решать */ }
});

/* периодическое обновление статистики */
setInterval(async () => {
  if (!S.user) return;
  if (S.view === 'agents' && S.project) {
    try {
      const d = await api(`projects/${S.project.id}/agents`);
      const changed = JSON.stringify(d.agents.map((a) => [a.id, a.status, a.tokens]));
      S.agents = d.agents;
      S.agentMap = new Map(S.agents.map((a) => [a.id, a]));
      refreshStats();
    } catch (e) {}
  }
}, 6000);

boot();

/* ==========================================================================
   1.5.0 — скиллы, плавная загрузка проекта, музыка, обучение
   ========================================================================== */

/* ------------------------------------------------------------------ скиллы */

const SK = { list: [], cur: null, dirty: false };

const SKILL_TEMPLATES = [
  { name: 'Стиль кода',
    body: '# Стиль кода\n\n- Пиши короткими файлами, один файл — одна ответственность.\n'
        + '- Комментарии по-русски и только там, где неочевидно.\n'
        + '- Никаких заглушек «TODO»: если делаешь — делай.\n'
        + '- В отчёте перечисляй файлы и что в них проверено.\n' },
  { name: 'Как проверять работу',
    body: '# Как проверять работу\n\n- Перед отчётом запусти то, что написал: `run("python -c ...")`.\n'
        + '- Проверяй не только «работает», но и «что если данных нет».\n'
        + '- Если проверка не удалась — так и напиши, а не «всё готово».\n' },
  { name: 'Тон общения',
    body: '# Тон\n\n- Пиши по-русски, без канцелярита и без эмодзи.\n'
        + '- Короткие абзацы, списки вместо простыней.\n'
        + '- Сначала результат, потом детали.\n' },
];

async function loadSkills() {
  try {
    const d = await api('skills');
    SK.list = d.skills || [];
  } catch (e) {
    SK.list = [];
    toast('Не удалось загрузить скиллы: ' + e.message, 'err');
  }
  renderSkills();
  if (S.view === 'skills') renderPanel();
}

function renderSkills() {
  const list = $('#skillsList');
  const empty = $('#skillsEmpty');
  if (!list) return;
  list.innerHTML = '';
  if (empty) empty.hidden = !!SK.list.length;
  SK.list.forEach((sk) => {
    const card = el('div', 'skill-card' + (sk.enabled ? '' : ' off'));
    card.innerHTML = `
      <div class="skill-head">
        <div class="skill-ic"><svg class="ic"><use href="#i-skill"/></svg></div>
        <div style="flex:1;min-width:0">
          <div class="skill-name">${esc(sk.name)}</div>
          <div class="skill-meta">${sk.chars} симв. · ${sk.lines} строк · ${sk.enabled ? 'включён' : 'выключен'}</div>
        </div>
      </div>
      <div class="skill-preview">${esc((sk.body || '').replace(/^#+\s*/gm, '').replace(/\n{2,}/g, ' · ').replace(/\n/g, ' ').slice(0, 240))}</div>
      <div class="skill-foot">
        <input type="checkbox" class="switch" title="Включить или выключить скилл"
               ${sk.enabled ? 'checked' : ''} data-toggle="${esc(sk.id)}">
        <span class="grow"></span>
        <button class="btn btn-ghost sm" data-edit="${esc(sk.id)}">Править</button>
        <button class="btn btn-ghost sm" data-md="${esc(sk.id)}" title="Скачать .md"><svg class="ic"><use href="#i-download"/></svg></button>
        <button class="btn btn-ghost sm" data-del="${esc(sk.id)}" title="Удалить"><svg class="ic"><use href="#i-trash"/></svg></button>
      </div>`;
    const toggle = card.querySelector('[data-toggle]');
    toggle.addEventListener('change', () => toggleSkill(sk.id, toggle.checked));
    card.querySelector('[data-edit]').addEventListener('click', () => openSkill(sk.id));
    card.querySelector('[data-md]').addEventListener('click', () => exportSkill(sk));
    card.querySelector('[data-del]').addEventListener('click', () => deleteSkill(sk));
    list.appendChild(card);
  });
}

async function toggleSkill(id, on) {
  try {
    await api('skills/' + id, { method: 'PUT', body: { enabled: on ? 1 : 0 } });
    await loadSkills();
    toast(on ? 'Скилл включён — агенты его видят' : 'Скилл выключен — агенты его не видят', 'ok');
  } catch (e) { toast(e.message, 'err'); await loadSkills(); }
}

function skillModal() {
  let wrap = $('#skillEditor');
  if (wrap) return wrap;
  wrap = el('div', 'modal-wrap');
  wrap.id = 'skillEditor';
  wrap.hidden = true;
  wrap.innerHTML = `
    <div class="modal">
      <div class="modal-head">
        <h3 id="skillModalTitle">Скилл</h3>
        <button class="icon-btn" id="skillModalClose" title="Закрыть"><svg class="ic"><use href="#i-close"/></svg></button>
      </div>
      <div class="modal-body">
        <div class="field"><label>Название</label>
          <input class="skill-input" id="skillName" placeholder="Например: Стиль кода" maxlength="80"></div>
        <div class="field"><label>Текст скилла (markdown)</label>
          <textarea class="skill-md" id="skillBody" spellcheck="false"
            placeholder="# Правила&#10;&#10;- пиши коротко&#10;- проверяй вывод команд"></textarea>
          <div class="skill-help">Этот текст получает каждый агент роя. Markdown не обязателен,
            но заголовки и списки читаются лучше. Агент может дочитать целиком: <code>skill("имя")</code>.</div>
        </div>
      </div>
      <div class="modal-foot">
        <label class="switch-row inline"><div><b>Включён</b></div>
          <input type="checkbox" id="skillEnabled" class="switch" checked></label>
        <span class="grow"></span>
        <button class="btn btn-ghost sm" id="skillExport">Скачать .md</button>
        <button class="btn btn-primary sm" id="skillSave">Сохранить</button>
      </div>
    </div>`;
  $('#app').appendChild(wrap);
  const close = () => { wrap.classList.remove('on'); setTimeout(() => { wrap.hidden = true; }, 200); };
  $('#skillModalClose').addEventListener('click', close);
  wrap.addEventListener('click', (e) => { if (e.target === wrap) close(); });
  $('#skillSave').addEventListener('click', saveSkill);
  $('#skillExport').addEventListener('click', () => {
    const sk = SK.list.find((x) => x.id === SK.cur);
    if (sk) exportSkill(sk);
  });
  wrap._close = close;
  return wrap;
}

function openSkill(id) {
  const wrap = skillModal();
  const sk = id ? SK.list.find((x) => x.id === id) : null;
  SK.cur = sk ? sk.id : null;
  $('#skillModalTitle').textContent = sk ? 'Скилл' : 'Новый скилл';
  $('#skillName').value = sk ? sk.name : '';
  $('#skillBody').value = sk ? sk.body : '';
  $('#skillEnabled').checked = sk ? !!sk.enabled : true;
  $('#skillExport').style.display = sk ? '' : 'none';
  wrap.hidden = false;
  requestAnimationFrame(() => wrap.classList.add('on'));
  setTimeout(() => $('#skillName').focus(), 80);
}

async function saveSkill() {
  const name = $('#skillName').value.trim();
  const body = $('#skillBody').value;
  const enabled = $('#skillEnabled').checked ? 1 : 0;
  if (!name) { toast('У скилла должно быть имя', 'info'); return; }
  if (!body.trim()) { toast('Напишите текст скилла — это инструкция для агентов', 'info'); return; }
  try {
    if (SK.cur) await api('skills/' + SK.cur, { method: 'PUT', body: { name, body, enabled } });
    else await api('skills', { method: 'POST', body: { name, body, enabled } });
    skillModal()._close();
    await loadSkills();
    toast(SK.cur ? 'Скилл сохранён' : 'Скилл создан', 'ok');
    SK.cur = null;
  } catch (e) { toast(e.message, 'err'); }
}

async function deleteSkill(sk) {
  if (!confirm('Удалить скилл «' + sk.name + '»? Это навсегда.')) return;
  try {
    await api('skills/' + sk.id, { method: 'DELETE' });
    await loadSkills();
    toast('Скилл удалён', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function exportSkill(sk) {
  try {
    const res = await fetch('/api/skills/' + sk.id + '/md', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (sk.name || 'скилл').replace(/[\\/:*?"<>|]/g, '-') + '.md';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  } catch (e) { toast('Не удалось скачать .md: ' + e.message, 'err'); }
}

async function importSkillFile(file) {
  if (!file) return;
  try {
    const b64 = await fileToBase64(file);
    const d = await api('skills/import', { method: 'POST', body: { name: file.name, data: b64 } });
    await loadSkills();
    toast('Скилл «' + (d.skill || {}).name + '» импортирован', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ''));
    fr.onerror = () => reject(new Error('не смог прочитать файл'));
    fr.readAsDataURL(file);
  });
}

if ($('#btnSkillNew')) $('#btnSkillNew').addEventListener('click', () => openSkill(null));
if ($('#btnSkillNew2')) $('#btnSkillNew2').addEventListener('click', () => openSkill(null));
if ($('#btnSkillImport')) $('#btnSkillImport').addEventListener('click', () => $('#skillFileInput').click());
if ($('#skillFileInput')) {
  $('#skillFileInput').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    importSkillFile(f);
  });
}
if ($('#btnSkillSamples')) {
  $('#btnSkillSamples').addEventListener('click', async () => {
    for (const t of SKILL_TEMPLATES) {
      try { await api('skills', { method: 'POST', body: { name: t.name, body: t.body } }); } catch (e) { /* уже есть */ }
    }
    await loadSkills();
    toast('Примеры добавлены — правьте под себя', 'ok');
  });
}

/* ------------------------------------------------- загрузчик проекта (плавно) */

let LOADER_T = null;

function showLoader(title, steps, minMs) {
  const wrap = $('#loader');
  if (!wrap) return { stage() {}, done() {} };
  steps = steps || ['создаю проект', 'раскладываю файлы', 'поднимаю рой', 'агенты стартуют'];
  $('#loaderTitle').textContent = title || 'Собираю проект';
  $('#loaderText').textContent = steps[0];
  $('#loaderBar').style.width = '6%';
  const box = $('#loaderSteps');
  box.innerHTML = steps.map((s, i) => `<div class="loader-step${i === 0 ? ' active' : ''}" data-i="${i}">
      <span class="dot"></span><span>${esc(s)}</span></div>`).join('');
  wrap.hidden = false;
  wrap.setAttribute('aria-hidden', 'false');
  requestAnimationFrame(() => wrap.classList.add('on'));
  let pct = 6;
  let stopped = false;
  const started = Date.now();
  // Минимум показа: проект в демо-режиме создаётся за 0,2 с, а «плавно» — это когда
  // анимация успевает пройти, а не мигнуть. Работу не тормозим: ждём только на выходе.
  const minShow = minMs == null ? 2400 : minMs;
  // пока идёт настоящая работа — полоска медленно ползёт, но не врёт: до 90% без события
  const tick = () => {
    if (stopped) return;
    pct = Math.min(90, pct + Math.random() * 2.2 + 0.5);
    $('#loaderBar').style.width = pct.toFixed(1) + '%';
    LOADER_T = setTimeout(tick, 320);
  };
  LOADER_T = setTimeout(tick, 320);
  return {
    stage(text, at) {
      if (text) $('#loaderText').textContent = text;
      const items = box.querySelectorAll('.loader-step');
      const idx = at == null ? -1 : Number(at);
      items.forEach((it, i) => {
        it.classList.toggle('active', idx === i);
        it.classList.toggle('done', idx > i);
      });
      if (idx >= 0) pct = Math.max(pct, ((idx + 1) / steps.length) * 82);
      $('#loaderBar').style.width = Math.min(92, pct).toFixed(1) + '%';
    },
    done(text) {
      stopped = true;
      clearTimeout(LOADER_T);
      if (text) $('#loaderText').textContent = text;
      box.querySelectorAll('.loader-step').forEach((it) => {
        it.classList.remove('active'); it.classList.add('done');
      });
      $('#loaderBar').style.width = '100%';
      const wait = Math.max(0, minShow - (Date.now() - started));
      setTimeout(() => {
        wrap.classList.remove('on');
        setTimeout(() => { wrap.hidden = true; wrap.setAttribute('aria-hidden', 'true'); }, 460);
      }, wait + 380);
    },
  };
}

/* ------------------------------------------------------------------ музыка */

const MUS = { on: true, volume: 35, fade: true, custom: false, ready: false, fading: null };

function musicEl() { return $('#musicAudio'); }

async function initMusic() {
  const el = musicEl();
  if (!el) return;
  try {
    const d = await api('music');
    MUS.custom = !!d.custom;
    MUS.on = d.on !== false;
    MUS.volume = Number(d.volume == null ? 35 : d.volume);
    MUS.fade = d.fade !== false;
    if (d.has_theme) el.src = d.url + '?v=' + Date.now();
    updateMusicHint(d);
  } catch (e) { /* музыки может не быть — не беда */ }
  el.volume = (MUS.volume / 100) * 0.9;
  MUS.ready = true;
  syncMusicButton();
  // Автовоспроизведение браузер разрешает только после действия человека:
  // включаем на первом же клике/клавише, если музыка включена в настройках.
  const kick = () => {
    startMusic();
    window.removeEventListener('pointerdown', kick);
    window.removeEventListener('keydown', kick);
  };
  window.addEventListener('pointerdown', kick, { once: true });
  window.addEventListener('keydown', kick, { once: true });
}

function updateMusicHint(d) {
  const hint = $('#musicHint');
  if (!hint) return;
  if (!d || !d.has_theme) {
    hint.textContent = 'Трека нет — загрузите свой файл, он будет играть по кругу.';
    return;
  }
  hint.textContent = (d.custom ? 'Играет ваша музыка: ' : 'Встроенная тема: ')
    + d.name + ' · ' + Math.max(1, Math.round((d.size || 0) / 1024)) + ' КБ. '
    + 'Хотите другую песню — загрузите свой файл (.ogg, .mp3, .wav, .flac): громкость и выключение остаются здесь.';
}

function syncMusicButton() {
  const b = $('#btnMusic');
  if (!b) return;
  const playing = MUS.on && !musicEl().paused;
  b.classList.toggle('playing', playing);
  b.title = playing ? 'Музыка играет — нажмите, чтобы выключить' : 'Музыка выключена — нажмите, чтобы включить';
  b.querySelector('span').textContent = playing ? 'Играет' : 'Музыка';
}

function applyVolume(soft) {
  const el = musicEl();
  const target = (MUS.volume / 100) * 0.9;
  if (MUS.fading) { clearInterval(MUS.fading); MUS.fading = null; }
  if (!soft || !MUS.fade) { el.volume = target; return; }
  const from = el.volume;
  const t0 = Date.now();
  MUS.fading = setInterval(() => {
    const k = Math.min(1, (Date.now() - t0) / 2200);
    el.volume = from + (target - from) * k;
    if (k >= 1) { clearInterval(MUS.fading); MUS.fading = null; }
  }, 60);
}

async function startMusic() {
  const el = musicEl();
  if (!el || !MUS.on || !el.src) return;
  el.loop = true;
  try {
    el.volume = MUS.fade ? 0 : (MUS.volume / 100) * 0.9;
    await el.play();
    applyVolume(true);
  } catch (e) { /* браузер запретил автоплей — включится по клику */ }
  syncMusicButton();
}

function stopMusic() {
  const el = musicEl();
  if (!el) return;
  el.pause();
  syncMusicButton();
}

// после «Сохранить» в настройках музыка подхватывает новое состояние без перезагрузки
function syncMusicFromSettings() {
  const st = S.settings || {};
  MUS.on = st.music_on !== false;
  MUS.fade = st.music_fade !== false;
  if (st.music_volume != null) {
    MUS.volume = Number(st.music_volume) || 0;
    if (musicEl()) musicEl().volume = (MUS.volume / 100) * 0.9;
  }
  if (MUS.on && musicEl() && musicEl().paused) startMusic();
  if (!MUS.on) stopMusic();
  syncMusicButton();
}

async function toggleMusic() {
  MUS.on = !MUS.on;
  if (MUS.on) startMusic(); else stopMusic();
  syncMusicButton();
  try { await saveSettings({ music_on: MUS.on ? 1 : 0 }, true); } catch (e) { /* не критично */ }
}

if ($('#btnMusic')) $('#btnMusic').addEventListener('click', toggleMusic);
if ($('#setMusicVolume')) {
  $('#setMusicVolume').addEventListener('input', () => {
    MUS.volume = Number($('#setMusicVolume').value) || 0;
    $('#musicVolLabel').textContent = MUS.volume;
    musicEl().volume = (MUS.volume / 100) * 0.9;
  });
  $('#setMusicVolume').addEventListener('change',
    () => saveSettings({ music_volume: MUS.volume }, true));
}
if ($('#setMusicOn')) {
  $('#setMusicOn').addEventListener('change', async () => {
    MUS.on = $('#setMusicOn').checked;
    if (MUS.on) startMusic(); else stopMusic();
    await saveSettings({ music_on: MUS.on ? 1 : 0 }, true);
  });
}
if ($('#setMusicFade')) {
  $('#setMusicFade').addEventListener('change',
    () => { MUS.fade = $('#setMusicFade').checked; saveSettings({ music_fade: MUS.fade ? 1 : 0 }, true); });
}
if ($('#btnMusicUpload')) {
  $('#btnMusicUpload').addEventListener('click', () => $('#musicFileInput').click());
}
if ($('#musicFileInput')) {
  $('#musicFileInput').addEventListener('change', async (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';
    if (!f) return;
    if (f.size > 200 * 1024 * 1024) { toast('Файл больше 200 МБ — это уже не фоновая музыка', 'info'); return; }
    try {
      toast('Загружаю трек…', 'info');
      const b64 = await fileToBase64(f);
      const d = await api('music', { method: 'POST', body: { name: f.name, data: b64 } });
      MUS.custom = true;
      musicEl().src = '/api/music/file?v=' + Date.now();
      updateMusicHint((d.music || {}));
      if (MUS.on) startMusic();
      toast('Трек загружен — играет по кругу', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  });
}
if ($('#btnMusicReset')) {
  $('#btnMusicReset').addEventListener('click', async () => {
    try {
      const d = await api('music', { method: 'DELETE' });
      MUS.custom = false;
      if ((d.music || {}).has_theme) musicEl().src = '/api/music/file?v=' + Date.now();
      updateMusicHint(d.music || {});
      if (MUS.on) startMusic();
      toast('Снова встроенная тема', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* ---------------------------------------------------------------- обучение */

const TOUR = [
  {
    title: 'Это MRC Agent',
    text: 'Сто агентов под управлением одной цели: они делят работу, пишут файлы, '
        + 'запускают код и собирают готовый проект. Разберёмся за минуту, где что лежит.',
    list: ['Слева — разделы приложения', 'В центре — то, с чем вы работаете', 'Справа — список внутри раздела'],
    art: 'mark',
  },
  {
    title: 'Что вы умеете',
    text: 'Агентами управляет цель. Вы описываете, что нужно получить, — рой сам решает, '
        + 'кто планирует, кто пишет, кто проверяет.',
    list: ['<b>Чаты</b> — поговорить с моделью', '<b>Агенты</b> — живая работа роя по проекту',
          '<b>Проекты</b> — готовые файлы и отчёт сборки'],
    art: 'agents',
  },
  {
    title: 'Новый проект за один шаг',
    text: 'Напишите цель в карточке нового проекта — например «бот приёма заявок на Python» — '
        + 'и нажмите «Создать и запустить». Появится плавная загрузка, а дальше рой начнёт работу.',
    list: ['Число агентов можно оставить по умолчанию (100)',
           'Прогресс видно в разделе «Агенты»', 'Остановить и продолжить можно в любой момент'],
    art: 'play',
  },
  {
    title: 'Файлы и вложения',
    text: 'К цели можно приложить любые файлы: ТЗ, таблицы, PDF, картинки. Напишите «@» в поле цели — '
        + 'откроется список вложений, стрелками выберите нужный, Enter — и он подставится в текст.',
    list: ['Скинули два файла — пишете «@», выбираете первый, потом «@» и второй',
           'Файлы попадают в папку проекта, агенты читают их содержимое',
           'Размер и объём текста больше не ограничены'],
    art: 'files',
  },
  {
    title: 'Скиллы — ваши правила для агентов',
    text: 'Раздел «Скиллы» — это markdown-инструкции, которые видит каждый агент: стиль кода, '
        + 'тон ответов, что обязательно проверять. Скилл можно править, выключать и удалять.',
    list: ['Создать → написать правила → Сохранить', 'Выключенный скилл агенты не видят',
           'Свой .md можно импортировать и скачать обратно'],
    art: 'skill',
  },
  {
    title: 'Музыка и тишина',
    text: 'Пока идёт проект, тихо играет музыка — по кругу. Выключить можно кнопкой с колонкой '
        + 'слева внизу или в настройках, там же громкость.',
    list: ['Свой трек загружается в Настройки → Музыка', 'Громкость от 0 до 100',
           'Всё запоминается между запусками'],
    art: 'note',
  },
  {
    title: 'Всё, можно начинать',
    text: 'Ключ модели задаётся в «Ещё → Модель». Без ключа работает демо-режим: интерфейс '
        + 'и рой живые, но текст отвечает заглушка.',
    list: ['Это обучение можно открыть снова: Ещё → Обучение',
           'Клавиатура: Ctrl/Cmd+K — быстрый переход', 'Готово — жмите «Начать»'],
    art: 'check',
  },
];

const TOUR_ART = {
  mark: '<svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="40" fill="none" stroke="rgba(124,92,255,.55)" stroke-width="1.5"/><circle cx="60" cy="60" r="26" fill="none" stroke="rgba(34,211,238,.5)" stroke-width="1.5"/><circle cx="60" cy="60" r="11" fill="rgba(124,92,255,.35)"/><circle cx="60" cy="60" r="5" fill="#fff"/></svg>',
  agents: '<svg viewBox="0 0 120 120"><g fill="rgba(124,92,255,.5)"><circle cx="34" cy="38" r="8"/><circle cx="60" cy="30" r="8"/><circle cx="86" cy="38" r="8"/><circle cx="30" cy="66" r="8"/><circle cx="90" cy="66" r="8"/><circle cx="46" cy="88" r="8"/><circle cx="74" cy="88" r="8"/></g><circle cx="60" cy="60" r="12" fill="rgba(34,211,238,.75)"/></svg>',
  play: '<svg viewBox="0 0 120 120"><rect x="18" y="24" width="84" height="72" rx="12" fill="none" stroke="rgba(124,92,255,.5)" stroke-width="1.5"/><path d="M50 48l24 12-24 12z" fill="rgba(34,211,238,.85)"/><rect x="28" y="84" width="40" height="4" rx="2" fill="rgba(124,92,255,.5)"/></svg>',
  files: '<svg viewBox="0 0 120 120"><path d="M30 20h32l14 14v66H30z" fill="none" stroke="rgba(124,92,255,.55)" stroke-width="1.6"/><path d="M62 20v14h14" fill="none" stroke="rgba(124,92,255,.55)" stroke-width="1.6"/><rect x="40" y="48" width="30" height="4" rx="2" fill="rgba(34,211,238,.7)"/><rect x="40" y="60" width="24" height="4" rx="2" fill="rgba(34,211,238,.5)"/><rect x="40" y="72" width="28" height="4" rx="2" fill="rgba(34,211,238,.35)"/></svg>',
  skill: '<svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="16" fill="none" stroke="rgba(34,211,238,.8)" stroke-width="1.8"/><g stroke="rgba(124,92,255,.6)" stroke-width="1.8" stroke-linecap="round"><path d="M60 20v10M60 90v10M20 60h10M90 60h10M32 32l7 7M81 81l7 7M32 88l7-7M81 39l7-7"/></g></svg>',
  note: '<svg viewBox="0 0 120 120"><path d="M50 78V36l34-8v42" fill="none" stroke="rgba(34,211,238,.75)" stroke-width="2"/><circle cx="42" cy="80" r="9" fill="rgba(124,92,255,.7)"/><circle cx="76" cy="72" r="9" fill="rgba(124,92,255,.55)"/></svg>',
  check: '<svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="34" fill="none" stroke="rgba(62,207,142,.8)" stroke-width="2"/><path d="M44 61l11 11 22-24" fill="none" stroke="rgba(62,207,142,.95)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

const TR = { i: 0, voice: true, audio: null };

function tourVoiceWanted() {
  const cb = $('#setTourVoice');
  return cb ? cb.checked : true;
}

function startTour() {
  const wrap = $('#tour');
  if (!wrap) return;
  TR.i = 0;
  TR.voice = tourVoiceWanted();
  if ($('#tourVoice')) $('#tourVoice').checked = TR.voice;
  wrap.hidden = false;
  wrap.setAttribute('aria-hidden', 'false');
  requestAnimationFrame(() => wrap.classList.add('on'));
  renderTour();
}

function tourStepSpeech(i) {
  const st = TOUR[i];
  const text = st.title + '. ' + st.text + ' ' + (st.list || []).join('. ').replace(/<[^>]+>/g, '');
  // Сначала пробуем записанный голос из комплекта, если его нет — системный синтез речи.
  const src = '/static/onboard/step' + (i + 1) + '.mp3';
  try {
    if (TR.audio) { TR.audio.pause(); TR.audio = null; }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    if (!TR.voice) return;
    const a = new Audio(src);
    a.volume = 0.9;
    const fallback = () => {
      if (!TR.voice || !window.speechSynthesis) return;
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'ru-RU';
      u.rate = 1.02;
      window.speechSynthesis.speak(u);
    };
    a.onerror = fallback;
    TR.audio = a;
    const p = a.play();
    if (p && p.catch) p.catch(fallback);
  } catch (e) { /* без звука тоже работает */ }
}

function renderTour() {
  const st = TOUR[TR.i];
  $('#tourNo').textContent = (TR.i + 1) + ' / ' + TOUR.length;
  $('#tourTitle').textContent = st.title;
  $('#tourText').textContent = st.text;
  $('#tourList').innerHTML = (st.list || []).map((x) => '<li>' + x + '</li>').join('');
  $('#tourArt').innerHTML = TOUR_ART[st.art] || TOUR_ART.mark;
  $('#tourDots').innerHTML = TOUR.map((_, i) => `<i class="${i === TR.i ? 'on' : ''}"></i>`).join('');
  $('#tourPrev').disabled = TR.i === 0;
  $('#tourNext').textContent = TR.i === TOUR.length - 1 ? 'Начать' : 'Дальше';
  tourStepSpeech(TR.i);
}

function endTour(mark) {
  const wrap = $('#tour');
  if (TR.audio) { TR.audio.pause(); TR.audio = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  wrap.classList.remove('on');
  setTimeout(() => { wrap.hidden = true; wrap.setAttribute('aria-hidden', 'true'); }, 300);
  if (mark) api('onboard/done', { method: 'POST' }).catch(() => {});
}

if ($('#tourNext')) {
  $('#tourNext').addEventListener('click', () => {
    if (TR.i >= TOUR.length - 1) { endTour(true); toast('Приятной работы', 'ok'); return; }
    TR.i += 1; renderTour();
  });
  $('#tourPrev').addEventListener('click', () => { if (TR.i > 0) { TR.i -= 1; renderTour(); } });
  $('#tourSkip').addEventListener('click', () => endTour(true));
  $('#tourClose').addEventListener('click', () => endTour(true));
  $('#tourVoice').addEventListener('change', () => {
    TR.voice = $('#tourVoice').checked;
    if (!TR.voice) {
      if (TR.audio) { TR.audio.pause(); TR.audio = null; }
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    } else tourStepSpeech(TR.i);
  });
}
if ($('#btnTourAgain')) {
  $('#btnTourAgain').addEventListener('click', () => {
    setView('chats');
    startTour();
  });
}

/* MCP: серверы и ручная проверка */
async function renderMcp() {
  const list = $('#mcpList');
  if (!list) return;
  try {
    const data = await api('mcp');
    list.replaceChildren();
    if (!data.servers.length) { list.textContent = 'Нет подключённых серверов.'; return; }
    data.servers.forEach((server) => {
      const box = document.createElement('div');
      box.className = 'field';
      const title = document.createElement('b');
      title.textContent = server.name + ' (' + server.transport + ') — инструментов: ' + server.tool_count + (server.plugin_name ? ' · плагин: ' + server.plugin_name : '');
      const info = document.createElement('div');
      info.className = 'hint';
      info.textContent = !server.plugin_enabled ? 'Плагин выключен: включите его в разделе «Плагины».' : (server.last_error || (server.tools || []).map((t) => t.name).join(', ') || 'Ещё не проверен');
      const controls = document.createElement('div');
      controls.className = 'field-row wrap';
      function button(label, action) {
        const b = document.createElement('button'); b.className = 'btn btn-ghost sm'; b.textContent = label;
        b.addEventListener('click', async () => {
          b.disabled = true;
          try { await action(); } catch (e) { toast(e.message, 'err'); }
          finally { b.disabled = false; }
        });
        controls.appendChild(b);
      }
      const url = 'mcp/' + encodeURIComponent(server.id);
      button('Проверить', async () => {
        info.textContent = 'Подключение…';
        try { await api(url + '/test', { method: 'POST' }); }
        finally { await renderMcp(); }
      });
      button(server.enabled ? 'Выключить' : 'Включить', async () => {
        await api(url, { method: 'PUT', body: { enabled: !server.enabled } }); await renderMcp();
      });
      button('Вызвать', async () => {
        const name = prompt('Имя инструмента', (server.tools[0] || {}).name || '');
        if (!name) return;
        const raw = prompt('Аргументы JSON', '{}');
        if (raw === null) return;
        const result = await api(url + '/call', { method: 'POST', body: { tool: name, args: JSON.parse(raw) } });
        info.textContent = JSON.stringify(result, null, 2);
      });
      button('Удалить', async () => {
        if (!confirm('Удалить сервер «' + server.name + '»?')) return;
        await api(url, { method: 'DELETE' }); await renderMcp();
      });
      box.append(title, info, controls); list.appendChild(box);
    });
  } catch (e) { list.textContent = 'Ошибка MCP: ' + e.message; }
}
function mcpFields() {
  const http = $('#mcpTransport').value === 'http';
  $('#mcpStdioFields').hidden = http; $('#mcpHttpFields').hidden = !http;
}
if ($('#mcpTransport')) {
  $('#mcpTransport').addEventListener('change', mcpFields);
  $('#btnMcpImport').addEventListener('click', () => $('#mcpFile').click());
  $('#mcpFile').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    let added = 0;
    try {
      const parsed = JSON.parse(await file.text());
      const servers = parsed.mcpServers || parsed.servers;
      if (!servers || Array.isArray(servers) || typeof servers !== 'object') throw new Error('Ожидается объект mcpServers');
      for (const [name, cfg] of Object.entries(servers)) {
        if (!cfg || typeof cfg !== 'object') throw new Error('Некорректная настройка: ' + name);
        await api('mcp', { method: 'POST', body: { name, transport: cfg.url ? 'http' : 'stdio', command: cfg.command, args: cfg.args, env: cfg.env, url: cfg.url, headers: cfg.headers, timeout: cfg.timeout || 0, enabled: 0 } });
        added += 1;
      }
      toast('Импортировано серверов: ' + added + '. Они выключены; проверьте настройки.', 'ok', 6000);
    } catch (e) { toast('Ошибка импорта после ' + added + ' серверов: ' + e.message, 'err', 6000); }
    finally { event.target.value = ''; await renderMcp(); }
  });
  $('#btnMcpReset').addEventListener('click', () => {
    ['mcpName', 'mcpCommand', 'mcpEnv', 'mcpUrl', 'mcpHeaders'].forEach((id) => { $('#' + id).value = ''; });
    $('#mcpTransport').value = 'stdio'; $('#mcpTimeout').value = 0; mcpFields();
  });
  $('#btnMcpDemo').addEventListener('click', () => {
    $('#mcpName').value = 'Демо'; $('#mcpTransport').value = 'stdio';
    $('#mcpCommand').value = 'python mcp_demo_server.py'; mcpFields();
  });
  $('#btnMcpFiles').addEventListener('click', () => {
    $('#mcpName').value = 'Файлы'; $('#mcpTransport').value = 'stdio';
    $('#mcpCommand').value = 'npx -y @modelcontextprotocol/server-filesystem /tmp'; mcpFields();
  });
  $('#btnMcpHttp').addEventListener('click', () => { $('#mcpTransport').value = 'http'; mcpFields(); $('#mcpUrl').focus(); });
  $('#btnMcpAdd').addEventListener('click', async () => {
    try {
      await api('mcp', { method: 'POST', body: {
        name: $('#mcpName').value, transport: $('#mcpTransport').value,
        command: $('#mcpCommand').value, env: $('#mcpEnv').value,
        url: $('#mcpUrl').value, headers: $('#mcpHeaders').value,
        timeout: Number($('#mcpTimeout').value) || 0, enabled: 1
      } });
      toast('Сервер добавлен. Нажмите «Проверить».', 'ok'); await renderMcp();
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* Плагины: импорт не запускает код, включение требует подтверждения. */
const PLUGINS = { list: [] };
async function loadPlugins() {
  try {
    const data = await api('plugins');
    PLUGINS.list = data.plugins || [];
    renderPlugins();
  } catch (e) { toast('Не удалось загрузить плагины: ' + e.message, 'err'); }
}
function renderPlugins() {
  const list = $('#pluginsList');
  if (!list) return;
  list.replaceChildren();
  $('#pluginsEmpty').hidden = !!PLUGINS.list.length;
  PLUGINS.list.forEach((plugin) => {
    const card = el('div', 'skill-card' + (plugin.enabled ? '' : ' off'));
    const docs = plugin.docs.map((d, i) => `<button class="btn btn-ghost sm plugin-doc" data-doc="${i}">${esc(d.name)}</button>`).join('');
    const warnings = plugin.warnings.map((w) => `<div>${esc(w)}</div>`).join('');
    card.innerHTML = `<div class="skill-head"><div class="skill-ic"><svg class="ic"><use href="#i-plug"/></svg></div><div style="flex:1;min-width:0"><div class="skill-name">${esc(plugin.name)}</div><div class="skill-meta">${plugin.enabled ? 'Включён' : 'Выключен'} · инструкций: ${plugin.docs.length}</div></div></div>
      <div class="skill-preview">${esc(plugin.description || 'Плагин для агентов')}</div>
      ${docs ? `<div class="plugin-label">Инструкции · нажмите, чтобы прочитать</div><div class="plugin-docs">${docs}</div>` : ''}
      ${warnings ? `<div class="plugin-warning">Не поддерживается или пропущено: ${warnings}</div>` : ''}
      <div class="plugin-document" hidden><b></b><pre></pre></div>
      <div class="skill-foot"><label class="plugin-toggle"><input type="checkbox" class="switch" ${plugin.enabled ? 'checked' : ''}><span>${plugin.enabled ? 'Включён' : 'Выключен'}</span></label><span class="grow"></span><button class="btn btn-ghost sm plugin-delete" title="Удалить">Удалить</button></div>`;
    const toggle = card.querySelector('.plugin-toggle input');
    toggle.addEventListener('change', async () => {
      if (toggle.checked) {
        const details = plugin.servers.map(m => m.transport === 'stdio' ? [m.command].concat(m.args).join(' ') : m.url).join('\n');
        const warning = plugin.servers.length ? '\n\nMCP может запустить программы и получить доступ к сети с вашими правами:\n' + details : '\n\nИнструкции плагина будут переданы агентам.';
        if (!confirm('Включить плагин «' + plugin.name + '»? Используйте только доверенные плагины.' + warning)) { toggle.checked = false; return; }
      }
      toggle.disabled = true;
      try {
        await api('plugins/' + encodeURIComponent(plugin.id), {method:'PUT',body:{enabled:toggle.checked}});
        await loadPlugins();
        toast('Состояние плагина изменено', 'ok');
      } catch (e) { toast(e.message, 'err'); await loadPlugins(); }
    });
    card.querySelector('.plugin-delete').addEventListener('click', async () => {
      if (!confirm('Удалить плагин «' + plugin.name + '», его инструкции и связанные MCP-серверы?')) return;
      try { await api('plugins/' + encodeURIComponent(plugin.id), {method:'DELETE'}); await loadPlugins(); toast('Плагин удалён', 'ok'); }
      catch (e) { toast(e.message, 'err'); }
    });
    card.querySelectorAll('[data-doc]').forEach((button) => button.addEventListener('click', async () => {
      try {
        const doc = await api('plugins/' + encodeURIComponent(plugin.id) + '/docs/' + button.dataset.doc);
        const box = card.querySelector('.plugin-document');
        box.querySelector('b').textContent = doc.name;
        box.querySelector('pre').textContent = doc.body;
        box.hidden = false;
      } catch (e) { toast(e.message, 'err'); }
    }));
    list.appendChild(card);
  });
}
$('#btnPluginImport').addEventListener('click', () => $('#pluginFile').click());
$('#btnPluginImportEmpty').addEventListener('click', () => $('#pluginFile').click());
$('#pluginFile').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  try {
    const data = await fileToBase64(file);
    const result = await api('plugins/import', {method:'POST',body:{name:file.name,data}});
    await loadPlugins();
    toast('Плагин «' + result.plugin.name + '» добавлен выключенным. Проверьте его состав.', 'ok', 6000);
  } catch (e) { toast('Не удалось импортировать: ' + e.message, 'err', 6000); }
});

/* Вопросы доступны из любого раздела и сохраняются на сервере. */
let questionTimer = null;
let questionKnown = new Set();
let questionSignature = '';
function startQuestions() {
  if (questionTimer) return;
  const toggle = el('button', 'agent-questions-toggle');
  toggle.type = 'button'; toggle.textContent = 'Вопросы агентов';
  const panel = el('section', 'agent-questions-panel');
  panel.hidden = true; panel.setAttribute('aria-label', 'Вопросы агентов');
  document.body.append(toggle, panel);
  toggle.addEventListener('click', () => { panel.hidden = !panel.hidden; refreshQuestions(true); });
  async function refreshQuestions(force = false) {
    if (!S.user) { toggle.hidden = true; panel.hidden = true; return; }
    toggle.hidden = false;
    try {
      const r = await api('questions');
      const list = r.questions || [];
      const pending = list.filter(q => q.status === 'pending');
      toggle.textContent = 'Вопросы агентов' + (pending.length ? ' · ' + pending.length : '');
      for (const q of pending) {
        if (!questionKnown.has(q.id)) {
          questionKnown.add(q.id);
          toast('Агент задал вопрос: ' + q.question, 'info', 8000);
        }
      }
      const signature = JSON.stringify(list);
      if (!force && (signature === questionSignature || panel.contains(document.activeElement))) return;
      questionSignature = signature;
      panel.innerHTML = '<strong>Вопросы агентов</strong><p class="hint">После двух минут без ответа агент продолжит самостоятельно.</p>' + (list.length ? '' : '<p class="hint">Пока вопросов нет.</p>');
      for (const q of list) {
        const card = el('div', 'agent-question');
        const status = { pending: 'Ожидает ответа до ' + new Date(q.deadline / 0.001).toLocaleTimeString('ru-RU'), answered: 'Ответ получен', timeout: 'Время вышло — агент продолжает самостоятельно', cancelled: 'Ожидание отменено' }[q.status] || q.status;
        card.innerHTML = '<strong>' + esc(q.agent_name || 'Агент') + ' · ' + esc(q.project_name || 'Проект') + '</strong><small>' + esc(status) + '</small><div>' + esc(q.question) + '</div>' + (q.suggestion ? '<small>Предлагаемый вариант: ' + esc(q.suggestion) + '</small>' : '') + (q.answer ? '<small>Ваш ответ: ' + esc(q.answer) + '</small>' : '') + (q.decision ? '<small>Ответ агента после таймаута: ' + esc(q.decision) + '</small>' : '');
        if (q.status === 'pending') {
          const input = document.createElement('textarea'); input.placeholder = 'Впишите свой ответ агенту';
          const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = 'Отправить свой ответ';
          const confirm = document.createElement('button'); confirm.type = 'button'; confirm.textContent = 'Подтвердить предложенный вариант';
          async function sendAnswer(value) {
            btn.disabled = true; confirm.disabled = true;
            try { await api('questions/' + encodeURIComponent(q.id) + '/answer', { method:'POST', body:{answer:value} }); toast('Ответ отправлен агенту', 'ok'); questionSignature = ''; await refreshQuestions(true); }
            catch (e) { toast(e.message, 'err'); await refreshQuestions(true); }
            finally { btn.disabled = false; confirm.disabled = false; }
          }
          btn.addEventListener('click', () => { if (!input.value.trim()) { input.focus(); return; } sendAnswer(input.value); });
          if (q.suggestion) { confirm.addEventListener('click', () => sendAnswer('Подтверждаю предложенный вариант: ' + q.suggestion)); card.append(confirm); }
          card.append(input, btn);
        }
        panel.append(card);
      }
    } catch (e) { /* при потере сети повторим при следующем опросе */ }
  }
  refreshQuestions(); questionTimer = setInterval(refreshQuestions, 2000);
}

/* MRCIDE Studio: локальные сборки и подготовка GitHub workflow. */
let ideTimer = null;
let ideCurrentBuild = null;
function ideArgs() { return {target: $('#ideTarget').value, scheme: $('#ideScheme').value.trim()}; }
function idePid() { return $('#ideProject').value; }
function initIDE() {
  const select = $('#ideProject');
  const previous = select.value || (S.project && S.project.id) || '';
  select.innerHTML = S.projects.length ? S.projects.map(p => '<option value="' + esc(p.id) + '">' + esc(p.name) + '</option>').join('') : '<option value="">Сначала создайте проект</option>';
  if (S.projects.some(p => p.id === previous)) select.value = previous;
  ideCurrentBuild = null;
  refreshIDE();
  if (!ideTimer) ideTimer = setInterval(() => { if (S.view === 'ide') refreshIDE(); }, 2000);
}
async function refreshIDE() {
  const pid = idePid();
  if (!pid) { $('#ideBuildStatus').textContent = 'Создайте или выберите проект'; return; }
  try {
    const data = await api('ide/' + encodeURIComponent(pid) + '/builds');
    if (pid !== idePid()) return;
    const builds = data.builds || [];
    const b = builds[0]; ideCurrentBuild = b || null;
    $('#ideStop').disabled = !b || b.status !== 'running';
    if (!b) { $('#ideBuildStatus').textContent = 'Пока сборок нет'; $('#ideLog').textContent = 'Ожидание запуска…'; $('#ideArtifacts').innerHTML = ''; return; }
    const labels = {running:'Сборка выполняется',done:'Сборка завершена',error:'Ошибка сборки',cancelled:'Сборка остановлена',interrupted:'Сборка прервана'};
    $('#ideBuildStatus').textContent = (labels[b.status] || b.status) + ' · ' + b.target;
    const log = $('#ideLog'); const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 60;
    log.textContent = (b.log || '') + '\n' + (b.message || '');
    if (stick) log.scrollTop = log.scrollHeight;
    const container = $('#ideArtifacts'); container.innerHTML = '';
    if (b.artifacts && b.artifacts.length) {
      const token = await api('preview-token/' + encodeURIComponent(pid));
      if (pid !== idePid()) return;
      for (const path of b.artifacts) {
        const link = document.createElement('a');
        link.href = token.base.replace('/preview/', '/download/') + path.split('/').map(encodeURIComponent).join('/');
        link.textContent = 'Скачать: ' + path; link.download = '';
        container.appendChild(link);
      }
    }
  } catch (e) { $('#ideBuildStatus').textContent = 'Не удалось получить состояние: ' + e.message; }
}
$('#ideProject').addEventListener('change', () => { ideCurrentBuild = null; refreshIDE(); });
$('#ideTarget').addEventListener('change', () => { $('#ideSchemeWrap').hidden = $('#ideTarget').value !== 'ios_simulator'; $('#ideCheckResult').textContent = 'Проверьте инструменты перед сборкой.'; });
$('#ideMode').addEventListener('change', () => {
  const cloud = $('#ideMode').value === 'github';
  $('#ideBuild').textContent = cloud ? 'Подготовить workflow' : 'Собрать';
  $('#ideCheck').disabled = cloud;
  $('#ideExport').hidden = !cloud;
  $('#ideModeHint').textContent = cloud ? 'Экспорт workflow в проект. Загрузите проект в GitHub и запустите Actions вручную: автоматическая отправка пока не реализована.' : 'Локальная сборка запускает код проекта. Используйте только доверенные проекты.';
});
$('#ideCheck').addEventListener('click', async () => {
  if (!idePid()) return toast('Выберите проект', 'err');
  try { const d = await api('ide/' + encodeURIComponent(idePid()) + '/check', {method:'POST',body:ideArgs()}); $('#ideCheckResult').textContent = d.check.message; }
  catch (e) { $('#ideCheckResult').textContent = e.message; }
});
$('#ideBuild').addEventListener('click', async () => {
  const pid = idePid(); if (!pid) return toast('Выберите проект', 'err');
  const cloud = $('#ideMode').value === 'github';
  if (!cloud && !window.confirm('Сборка запустит код и инструменты из проекта на вашем компьютере. Продолжить?')) return;
  $('#ideBuild').disabled = true;
  try {
    const d = await api('ide/' + encodeURIComponent(pid) + (cloud ? '/workflow' : '/start'), {method:'POST', body:ideArgs()});
    if (cloud) $('#ideCheckResult').textContent = d.workflow.message + ' Файл: ' + d.workflow.path;
    else { $('#ideCheckResult').textContent = 'Запрос на реальную сборку отправлен.'; await refreshIDE(); }
  } catch (e) { $('#ideCheckResult').textContent = e.message; }
  finally { $('#ideBuild').disabled = false; }
});
$('#ideStop').addEventListener('click', async () => {
  if (!ideCurrentBuild || !idePid()) return;
  try { await api('ide/' + encodeURIComponent(idePid()) + '/stop/' + encodeURIComponent(ideCurrentBuild.id), {method:'POST',body:{}}); await refreshIDE(); }
  catch (e) { toast(e.message, 'err'); }
});
$('#ideOpenFiles').addEventListener('click', async () => { const id=idePid(); if (id) await selectProject(id); setView('workspace'); });
$('#ideOpenAgents').addEventListener('click', async () => { const id=idePid(); if (id) await selectProject(id); setView('agents'); });

$('#ideExport').addEventListener('click', () => { const pid = idePid(); if (!pid) return toast('Выберите проект', 'err'); location.href = '/api/ide/' + encodeURIComponent(pid) + '/export.zip'; });
