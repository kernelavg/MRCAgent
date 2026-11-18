#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db.py — SQLite-хранилище MRC Agent. Всё состояние живёт здесь, поэтому
закрытие окна приложения не теряет ни одного агента, ни одного события.
"""

import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

def app_dir():
    """Папка приложения: рядом с .exe при сборке PyInstaller, иначе — папка проекта."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE = app_dir()
DATA = Path(os.environ.get("MS_DATA") or (BASE / "data"))
DB_PATH = DATA / "studio.db"

_lock = threading.RLock()
_conn = None


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE, name TEXT, picture TEXT,
    provider TEXT, created_at REAL, password_hash TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id TEXT, created_at REAL, expires REAL
);

CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY, user_id TEXT, title TEXT, project_id TEXT,
    created_at REAL, updated_at REAL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT,
    meta TEXT, created_at REAL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, user_id TEXT, name TEXT, goal TEXT, status TEXT,
    kind TEXT, progress REAL, agents_total INTEGER, agents_done INTEGER,
    workspace TEXT, created_at REAL, updated_at REAL, meta TEXT,
    tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0,
    brief TEXT, user_paused INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, project_id TEXT, idx INTEGER, nick TEXT, role TEXT,
    lane TEXT, status TEXT, activity TEXT, task TEXT, tokens INTEGER,
    files TEXT, model TEXT, started REAL, finished REAL, meta TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, agent_id TEXT,
    type TEXT, text TEXT, data TEXT, ts REAL
);

CREATE TABLE IF NOT EXISTS ide_builds (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, target TEXT NOT NULL,
    status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL,
    code INTEGER, message TEXT, artifacts TEXT, log_path TEXT
);

CREATE TABLE IF NOT EXISTS agent_questions (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, agent_id TEXT NOT NULL,
    question TEXT NOT NULL, status TEXT NOT NULL, answer TEXT,
    created_at REAL NOT NULL, deadline REAL NOT NULL, resolved_at REAL, decision TEXT, suggestion TEXT
);

CREATE TABLE IF NOT EXISTS agent_msgs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, from_agent TEXT,
    to_agent TEXT, text TEXT, ts REAL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);

-- скиллы: свои .md-инструкции для агентов (включаются переключателем)
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY, user_id TEXT, name TEXT, body TEXT, enabled INTEGER DEFAULT 1,
    created_at REAL, updated_at REAL
);

-- 1.6.0: подключённые MCP-серверы (внешние инструменты для агентов)
CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY, user_id TEXT, name TEXT, transport TEXT, command TEXT,
    args TEXT, url TEXT, headers TEXT, env TEXT, timeout INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1, tools TEXT, tools_at REAL, last_error TEXT,
    created_at REAL, updated_at REAL
);

-- Плагины: инструкции хранятся в отдельных markdown-файлах, MCP связан с владельцем.
CREATE TABLE IF NOT EXISTS plugins (
    id TEXT PRIMARY KEY, user_id TEXT, name TEXT, description TEXT, enabled INTEGER DEFAULT 0,
    docs TEXT, warnings TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS plugin_mcp (
    plugin_id TEXT NOT NULL, server_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY (plugin_id, server_id)
);

-- вложения пользователя: сначала «черновик» (project_id пустой), потом переезжают в проект
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY, user_id TEXT, project_id TEXT, name TEXT, path TEXT,
    size INTEGER, mime TEXT, kind TEXT, created_at REAL, meta TEXT
);
"""


def _migrate(conn):
    """Догоняем схему для баз, созданных прошлыми версиями."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    if "onboarded" not in cols:                      # 1.5.0: обучение при первом входе
        conn.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER DEFAULT 0")

    pcols = {r[1] for r in conn.execute("PRAGMA table_info(projects)")}
    for name, ddl in (("tokens_in", "INTEGER DEFAULT 0"),
                      ("tokens_out", "INTEGER DEFAULT 0"),
                      ("brief", "TEXT"),
                      ("user_paused", "INTEGER DEFAULT 0")):
        if name not in pcols:
            conn.execute("ALTER TABLE projects ADD COLUMN %s %s" % (name, ddl))

    qcols = {r[1] for r in conn.execute("PRAGMA table_info(agent_questions)")}
    if "decision" not in qcols:
        conn.execute("ALTER TABLE agent_questions ADD COLUMN decision TEXT")
    if "suggestion" not in qcols:
        conn.execute("ALTER TABLE agent_questions ADD COLUMN suggestion TEXT")

    # старые сообщения писали в to_agent ник — переводим на id, иначе адресат их не видит
    conn.execute("UPDATE agent_msgs SET to_agent = COALESCE((SELECT a.id FROM agents a "
                 "WHERE a.project_id = agent_msgs.project_id AND lower(a.nick) = lower(agent_msgs.to_agent)), "
                 "to_agent) WHERE to_agent IN (SELECT nick FROM agents)")

    # индексы: события ищутся по проекту и id, сообщения — по агенту
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_pid ON events(project_id, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agents_pid ON agents(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_msgs_pid ON agent_msgs(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_pid ON files(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id, project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_user ON skills(user_id, created_at)")


_PURGED = {"done": False}


def instance_id():
    """Стабильный id установки: по пути к базе. Нужен, чтобы окно не подключалось
    к чужому демону (баг «второй exe подхватывает первый»)."""
    import hashlib
    return hashlib.sha1(str(DB_PATH).encode("utf-8")).hexdigest()[:12]


def port_file():
    return DATA / "daemon.port"


def write_port_file(port):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        port_file().write_text(str(port), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def read_port_file():
    try:
        return int(port_file().read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        return 0


def connect():
    global _conn
    with _lock:
        if _conn is None:
            DATA.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _migrate(_conn)
            # 17: просроченные сессии не копятся в базе годами
            _conn.execute("DELETE FROM sessions WHERE expires < ?", (now(),))
            _conn.commit()
        return _conn


def q(sql, args=()):
    """INSERT/UPDATE/DELETE."""
    with _lock:
        c = connect()
        cur = c.execute(sql, args)
        c.commit()
        return cur


def one(sql, args=()):
    with _lock:
        c = connect()
        row = c.execute(sql, args).fetchone()
        return dict(row) if row else None


def many(sql, args=()):
    with _lock:
        c = connect()
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def rid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


# Формат id известен заранее: p_ + 16 hex (проект), a_ + 16 hex (агент).
# Всё, что не подходит под маску, отсекается ДО работы с файловой системой.
import re as _re

_PROJECT_ID = _re.compile(r"^p_[0-9a-f]{16}$")
_AGENT_ID = _re.compile(r"^a_[0-9a-f]{16}$")
_MCP_ID = _re.compile(r"^m_[0-9a-f]{16}$")


def valid_project_id(pid):
    return bool(_PROJECT_ID.match(str(pid or "")))


def valid_agent_id(aid):
    return bool(_AGENT_ID.match(str(aid or "")))


def valid_mcp_id(mid):
    return bool(_MCP_ID.match(str(mid or "")))


def now():
    return time.time()


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False)


def jload(s, default=None):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Пользователи и сессии
# --------------------------------------------------------------------------- #


def upsert_user(email, name, picture=None, provider="google", user_id=None):
    u = one("SELECT * FROM users WHERE email = ?", (email,))
    if u:
        q("UPDATE users SET name = ?, picture = COALESCE(?, picture) WHERE id = ?",
          (name or u["name"], picture, u["id"]))
        return one("SELECT * FROM users WHERE id = ?", (u["id"],))
    uid = user_id or rid("u_")
    q("INSERT INTO users (id, email, name, picture, provider, created_at) VALUES (?,?,?,?,?,?)",
      (uid, email, name, picture, provider, now()))
    return one("SELECT * FROM users WHERE id = ?", (uid,))


def purge_sessions():
    """Чистим просроченные сессии — иначе таблица растёт вечно."""
    q("DELETE FROM sessions WHERE expires < ?", (now(),))


def drop_other_sessions(user_id, keep_token=None):
    """Смена пароля закрывает все остальные входы."""
    if keep_token:
        q("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user_id, keep_token))
    else:
        q("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def create_session(user_id, days=30):
    tok = rid("s_") + uuid.uuid4().hex[:24]
    q("INSERT INTO sessions (token, user_id, created_at, expires) VALUES (?,?,?,?)",
      (tok, user_id, now(), now() + days * 86400))
    return tok


def user_by_session(token):
    if not token:
        return None
    row = one("SELECT * FROM sessions WHERE token = ?", (token,))
    if not row or row["expires"] < now():
        return None
    return one("SELECT * FROM users WHERE id = ?", (row["user_id"],))


def drop_session(token):
    q("DELETE FROM sessions WHERE token = ?", (token,))


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #

def _kv_get(key, default=None):
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:  # noqa: BLE001
        return row["value"]


def _kv_set(key, value):
    q("INSERT INTO settings (key, value) VALUES (?,?) "
      "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      (key, json.dumps(value, ensure_ascii=False)))


def preview_secret():
    """Секрет подписи ссылок предпросмотра. Лежит в базе, создаётся один раз."""
    import secrets as _secrets
    key = _kv_get("preview_secret")
    if not key:
        key = _secrets.token_hex(32)
        _kv_set("preview_secret", key)
    return key


def first_user_id():
    """Первый зарегистрированный пользователь = администратор этой установки."""
    row = one("SELECT id FROM users ORDER BY created_at ASC, rowid ASC LIMIT 1")
    return row["id"] if row else ""


DEFAULT_SETTINGS = {
    "api_key": "",
    "base_url": "https://api.inceptionlabs.ai/v1",
    "model": "mercury-3",
    "reasoning": "medium",
    "concurrency": 6,
    "agents": 100,
    "allow_terminal": 1,
    "allow_network": 1,
    "theme": "dark",
    "accent": "violet",
    "google_client_id": "",
    "google_client_secret": "",
    "demo_mode": 1,
    "terminal_timeout": 20,
    "terminal_strict": 0,
    # --- лимиты модели и защита от зацикливания (баг «агент вечно что-то делает») ---
    "max_tokens": 8192,        # один ответ модели: без искусственного потолка в 1200
    "max_rounds": 0,           # 0 = без лимита на количество действий агента
    "stall_rounds": 3,         # столько кругов без изменения файлов → стоп «нет прогресса»
    "agent_timeout": 0,        # 0 = без общего ограничения времени на агента
    "resume_on_start": 1,
    # --- 1.3.0 ---
    "loop_guard": 0,           # защита от зацикливания по желанию пользователя
    "merge_interval": 20,      # раз в сколько секунд главный подмешивает работы агентов (0 = только в конце)
    "searx_url": "",           # свой SearXNG, например http://127.0.0.1:8080
    # --- 1.4.0: почта (IMAP — читать/искать, SMTP — писать) ---
    "mail_imap_host": "",
    "mail_imap_port": 993,
    "mail_imap_enc": "ssl",    # ssl | starttls | none
    "mail_smtp_host": "",
    "mail_smtp_port": 465,
    "mail_smtp_enc": "ssl",
    "mail_user": "",
    "mail_password": "",       # пароль приложения; в UI не показывается
    "mail_from": "",
    "mail_folder": "INBOX",
    "mail_allow_send": 1,      # агенты могут отправлять письма
    "mail_timeout": 15,
    # --- 1.4.2: вложения любых форматов ---
    "max_upload_mb": 0,        # 1.5.0: по умолчанию без лимита; >0 — свой потолок в МБ
    # --- 1.5.0: музыка проекта и обучение ---
    "music_on": 1,             # музыка во время работы над проектом
    "music_volume": 35,        # 0..100
    "music_fade": 1,           # плавно поднимать громкость при старте
    # --- 1.6.0: MCP (внешние серверы инструментов) ---
    "mcp_on": 1,               # отдавать инструменты MCP агентам
    "mcp_timeout": 0,          # 0 = ждать сколько нужно; >0 — общий потолок в секундах
    "mcp_prompt": 1,           # показывать список MCP-инструментов в промпте агентов
}


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    for row in many("SELECT key, value FROM settings"):
        try:
            s[row["key"]] = json.loads(row["value"])
        except Exception:
            s[row["key"]] = row["value"]
    return s


def set_settings(patch):
    for k, v in (patch or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        q("INSERT INTO settings (key, value) VALUES (?,?) "
          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          (k, json.dumps(v, ensure_ascii=False)))


# --------------------------------------------------------------------------- #
# Скиллы: свои .md-инструкции для агентов
# --------------------------------------------------------------------------- #

SKILL_NAME_MAX = 80
SKILL_BODY_MAX = 400000            # символов в самом скилле (это код-инструкция, не файл данных)


def clean_skill(name, body):
    """Проверка и нормализация скилла. Бросает ValueError с текстом-причиной."""
    nm = re.sub(r"\s+", " ", str(name or "")).strip()
    bd = str(body or "").replace("\r\n", "\n").strip()
    if not nm:
        raise ValueError("у скилла должно быть имя")
    if not bd:
        raise ValueError("у скилла должен быть текст — это markdown-инструкция для агентов")
    return nm, bd


def add_skill(user_id, name, body, enabled=1):
    nm, bd = clean_skill(name, body)
    sid = rid("s_")
    now = time.time()
    q("INSERT INTO skills (id, user_id, name, body, enabled, created_at, updated_at) "
      "VALUES (?,?,?,?,?,?,?)", (sid, user_id, nm, bd, 1 if enabled else 0, now, now))
    return get_skill(sid)


def get_skill(skill_id):
    return one("SELECT * FROM skills WHERE id = ?", (skill_id,))


def list_skills(user_id):
    return many("SELECT * FROM skills WHERE user_id = ? ORDER BY created_at ASC", (user_id,))


def enabled_skills(user_id):
    return many("SELECT * FROM skills WHERE user_id = ? AND enabled = 1 ORDER BY created_at ASC",
                (user_id,))


def find_skill(user_id, name):
    """Скилл по имени (без учёта регистра, можно по началу имени)."""
    low = str(name or "").strip().lower()
    if not low:
        return None
    rows = list_skills(user_id)
    for r in rows:
        if (r["name"] or "").lower() == low:
            return r
    for r in rows:
        if (r["name"] or "").lower().startswith(low):
            return r
    return None


def update_skill(skill_id, name=None, body=None, enabled=None):
    row = get_skill(skill_id)
    if not row:
        return None
    nm, bd = clean_skill(row["name"] if name is None else name,
                         row["body"] if body is None else body)
    en = row["enabled"] if enabled is None else (1 if int(enabled) else 0)
    q("UPDATE skills SET name = ?, body = ?, enabled = ?, updated_at = ? WHERE id = ?",
      (nm, bd, en, time.time(), skill_id))
    return get_skill(skill_id)


def delete_skill(skill_id):
    row = get_skill(skill_id)
    if not row:
        return False
    q("DELETE FROM skills WHERE id = ?", (skill_id,))
    return True


# --------------------------------------------------------------------------- #
# MCP: подключённые серверы (внешние инструменты агентов)
# --------------------------------------------------------------------------- #

def add_mcp_server(user_id, rec):
    """
    rec — уже проверенная запись из mcp.clean_server(). Храним как есть:
    args/headers/env — JSON, чтобы сервер можно было перезапустить один в один.
    """
    mid = rid("m_")
    ts = time.time()
    q("INSERT INTO mcp_servers (id, user_id, name, transport, command, args, url, headers, "
      "env, timeout, enabled, tools, tools_at, last_error, created_at, updated_at) "
      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (mid, user_id, rec["name"], rec["transport"], rec.get("command") or "",
       jdump(rec.get("args") or []), rec.get("url") or "",
       jdump(rec.get("headers") or {}), jdump(rec.get("env") or {}),
       int(rec.get("timeout") or 0), 1 if rec.get("enabled", 1) else 0,
       "[]", 0, "", ts, ts))
    return get_mcp_server(mid)


def get_mcp_server(mid):
    return one("SELECT * FROM mcp_servers WHERE id = ?", (mid,))


def list_mcp_servers(user_id):
    return many("SELECT * FROM mcp_servers WHERE user_id = ? ORDER BY created_at ASC", (user_id,))


def update_mcp_server(mid, rec=None, enabled=None, tools=None, last_error=None):
    """Обновляем только то, что передали: имя/команду или флаги и кэш инструментов."""
    row = get_mcp_server(mid)
    if not row:
        return None
    sets, args = [], []
    if rec:
        sets += ["name = ?", "transport = ?", "command = ?", "args = ?", "url = ?",
                 "headers = ?", "env = ?", "timeout = ?"]
        args += [rec["name"], rec["transport"], rec.get("command") or "",
                 jdump(rec.get("args") or []), rec.get("url") or "",
                 jdump(rec.get("headers") or {}), jdump(rec.get("env") or {}),
                 int(rec.get("timeout") or 0)]
    if enabled is not None:
        sets.append("enabled = ?")
        args.append(1 if int(enabled) else 0)
    if tools is not None:
        sets += ["tools = ?", "tools_at = ?"]
        args += [jdump(tools), time.time()]
    if last_error is not None:
        sets.append("last_error = ?")
        args.append(str(last_error or "")[:500])
    if not sets:
        return row
    sets.append("updated_at = ?")
    args.append(time.time())
    args.append(mid)
    q("UPDATE mcp_servers SET %s WHERE id = ?" % ", ".join(sets), tuple(args))
    return get_mcp_server(mid)


def delete_mcp_server(mid):
    row = get_mcp_server(mid)
    if not row:
        return False
    q("DELETE FROM plugin_mcp WHERE server_id = ?", (mid,))
    q("DELETE FROM mcp_servers WHERE id = ?", (mid,))
    return True


def mcp_tools_cache(user_id):
    """{id сервера: [инструменты]} — чтобы промпт агентов не ждал сети."""
    out = {}
    for row in list_mcp_servers(user_id):
        items = jload(row.get("tools"), []) or []
        if items:
            out[row["id"]] = items
    return out


def enabled_mcp_servers(user_id):
    return [r for r in list_mcp_servers(user_id) if int(r.get("enabled") or 0)
            and (not plugin_for_server(r["id"]) or plugin_for_server(r["id"])["enabled"])]


def find_mcp_server(user_id, name):
    """Сервер по имени: агент называет сервер так, как человек его записал."""
    low = str(name or "").strip().lower()
    if not low:
        return None
    rows = list_mcp_servers(user_id)
    for r in rows:
        if (r["name"] or "").lower() == low:
            return r
    for r in rows:
        if (r["name"] or "").lower().startswith(low):
            return r
    for r in rows:
        if low in (r["name"] or "").lower():
            return r
    return None


def set_onboarded(user_id, flag=1):
    q("UPDATE users SET onboarded = ? WHERE id = ?", (1 if flag else 0, user_id))


def is_onboarded(user_id):
    row = one("SELECT onboarded FROM users WHERE id = ?", (user_id,))
    return bool(row and row["onboarded"])


# --------------------------------------------------------------------------- #
# Чаты
# --------------------------------------------------------------------------- #


def create_chat(user_id, title="Новый чат", project_id=None):
    cid = rid("c_")
    q("INSERT INTO chats (id, user_id, title, project_id, created_at, updated_at) "
      "VALUES (?,?,?,?,?,?)", (cid, user_id, title, project_id, now(), now()))
    return one("SELECT * FROM chats WHERE id = ?", (cid,))


def list_chats(user_id):
    return many("SELECT * FROM chats WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))


def add_message(chat_id, role, content, meta=None):
    mid = rid("m_")
    q("INSERT INTO messages (id, chat_id, role, content, meta, created_at) VALUES (?,?,?,?,?,?)",
      (mid, chat_id, role, content, jdump(meta or {}), now()))
    q("UPDATE chats SET updated_at = ? WHERE id = ?", (now(), chat_id))
    return one("SELECT * FROM messages WHERE id = ?", (mid,))


def chat_messages(chat_id, limit=200):
    rows = many("SELECT * FROM messages WHERE chat_id = ? ORDER BY created_at ASC LIMIT ?",
                (chat_id, limit))
    for r in rows:
        r["meta"] = jload(r["meta"], {})
    return rows


def rename_chat(chat_id, title):
    q("UPDATE chats SET title = ?, updated_at = ? WHERE id = ?", (title, now(), chat_id))


def delete_chat(chat_id):
    q("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    q("DELETE FROM chats WHERE id = ?", (chat_id,))


# --------------------------------------------------------------------------- #
# Проекты
# --------------------------------------------------------------------------- #


def create_project(user_id, name, goal, kind="build", workspace=None, agents_total=100):
    pid = rid("p_")
    q("INSERT INTO projects (id, user_id, name, goal, status, kind, progress, "
      "agents_total, agents_done, workspace, created_at, updated_at, meta) "
      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (pid, user_id, name, goal, "idle", kind, 0.0, agents_total, 0, workspace, now(), now(), "{}"))
    return one("SELECT * FROM projects WHERE id = ?", (pid,))


def update_project(pid, **kw):
    if not kw:
        return
    fields = ", ".join("%s = ?" % k for k in kw)
    q("UPDATE projects SET %s, updated_at = ? WHERE id = ?" % fields,
      tuple(kw.values()) + (now(), pid))


def list_projects(user_id):
    return many("SELECT * FROM projects WHERE user_id = ? ORDER BY created_at DESC", (user_id,))


def get_project(pid):
    return one("SELECT * FROM projects WHERE id = ?", (pid,))


def delete_project(pid):
    q("DELETE FROM agents WHERE project_id = ?", (pid,))
    q("DELETE FROM events WHERE project_id = ?", (pid,))
    q("DELETE FROM agent_msgs WHERE project_id = ?", (pid,))
    q("DELETE FROM projects WHERE id = ?", (pid,))


# --------------------------------------------------------------------------- #
# Агенты
# --------------------------------------------------------------------------- #


# ------------------------------------------------------------------ вложения

def add_file(user_id, name, path, size, mime="", kind="", project_id=None, meta=None):
    """Строка про файл. project_id=None — черновик, ещё не привязан к проекту."""
    fid = rid("f_")
    q("INSERT INTO files (id, user_id, project_id, name, path, size, mime, kind, created_at, meta) "
      "VALUES (?,?,?,?,?,?,?,?,?,?)",
      (fid, user_id, project_id, name, path, int(size or 0), mime or "", kind or "",
       now(), jdump(meta or {})))
    return one("SELECT * FROM files WHERE id = ?", (fid,))


def update_file(fid, **kw):
    if not kw:
        return get_file(fid)
    sets = ", ".join("%s = ?" % k for k in kw)
    q("UPDATE files SET %s WHERE id = ?" % sets, tuple(kw.values()) + (fid,))
    return get_file(fid)


def get_file(fid):
    return one("SELECT * FROM files WHERE id = ?", (fid,))


def list_files(user_id, project_id=None):
    if project_id:
        return many("SELECT * FROM files WHERE user_id = ? AND project_id = ? "
                    "ORDER BY created_at, name", (user_id, project_id))
    return many("SELECT * FROM files WHERE user_id = ? AND (project_id IS NULL OR project_id = '') "
                "ORDER BY created_at, name", (user_id,))


def delete_file_row(fid):
    q("DELETE FROM files WHERE id = ?", (fid,))
    return True


def project_files(project_id):
    return many("SELECT * FROM files WHERE project_id = ? ORDER BY created_at, name", (project_id,))


def add_agent(pid, idx, nick, role, lane, task="", model=""):
    aid = rid("a_")
    q("INSERT INTO agents (id, project_id, idx, nick, role, lane, status, activity, task, "
      "tokens, files, model, started, finished, meta) "
      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (aid, pid, idx, nick, role, lane, "idle", "wait", task, 0, "[]", model, None, None, "{}"))
    return aid


def update_agent(aid, **kw):
    if not kw:
        return
    fields = ", ".join("%s = ?" % k for k in kw)
    q("UPDATE agents SET %s WHERE id = ?" % fields, tuple(kw.values()) + (aid,))


def list_agents(pid):
    return many("SELECT * FROM agents WHERE project_id = ? ORDER BY idx", (pid,))


def get_agent(aid):
    return one("SELECT * FROM agents WHERE id = ?", (aid,))


# --------------------------------------------------------------------------- #
# События и сообщения агентов
# --------------------------------------------------------------------------- #


def add_event(pid, agent_id, etype, text, data=None):
    cur = q("INSERT INTO events (project_id, agent_id, type, text, data, ts) VALUES (?,?,?,?,?,?)",
            (pid, agent_id, etype, text, jdump(data or {}), now()))
    return cur.lastrowid


def events_since(pid, since_id=0, limit=400):
    rows = many("SELECT * FROM events WHERE project_id = ? AND id > ? ORDER BY id LIMIT ?",
                (pid, since_id, limit))
    for r in rows:
        r["data"] = jload(r["data"], {})
    return rows


def agent_events(aid, limit=200):
    rows = many("SELECT * FROM events WHERE agent_id = ? ORDER BY id DESC LIMIT ?", (aid, limit))
    for r in rows:
        r["data"] = jload(r["data"], {})
    return list(reversed(rows))


def agent_console(aid, since_id=0, limit=500):
    """
    Консоль агента: всё, что он делал, по порядку и с догрузкой по since_id.

    Отдаём те же события, что видит лента, но по возрастанию id — так их можно
    дописывать в терминал внизу, как в настоящей консоли. Полный вывод команд
    лежит в data["out_full"] (в ленте он обрезан до 800 символов).
    """
    rows = many("SELECT * FROM events WHERE agent_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
                (aid, int(since_id or 0), int(limit or 500)))
    for r in rows:
        r["data"] = jload(r["data"], {})
        r["console"] = True
    return rows


def agent_console_last(aid):
    row = one("SELECT MAX(id) AS last FROM events WHERE agent_id = ?", (aid,))
    return int((row or {}).get("last") or 0)


def add_agent_msg(pid, from_agent, to_agent, text):
    cur = q("INSERT INTO agent_msgs (project_id, from_agent, to_agent, text, ts) VALUES (?,?,?,?,?)",
            (pid, from_agent, to_agent, text, now()))
    return cur.lastrowid


def agent_msgs(aid, limit=100):
    return many(
        "SELECT * FROM agent_msgs WHERE from_agent = ? OR to_agent = ? ORDER BY id DESC LIMIT ?",
        (aid, aid, limit))


def agent_inbox(aid, limit=20):
    """Что реально пришло агенту (входящие), плюс кто отправил — для промптов."""
    return many(
        "SELECT m.*, a.nick AS from_nick FROM agent_msgs m "
        "LEFT JOIN agents a ON a.id = m.from_agent "
        "WHERE m.to_agent = ? ORDER BY m.id DESC LIMIT ?", (aid, limit))


def project_msgs(pid, limit=200):
    return many("SELECT * FROM agent_msgs WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (pid, limit))


def stats(pid=None):
    if pid:
        a = one("SELECT COUNT(*) n, SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) d, "
                "SUM(tokens) t FROM agents WHERE project_id = ?", (pid,))
        e = one("SELECT COUNT(*) n FROM events WHERE project_id = ?", (pid,))
        return {"agents": a["n"] or 0, "done": a["d"] or 0, "tokens": a["t"] or 0,
                "events": e["n"] or 0}
    a = one("SELECT COUNT(*) n, SUM(tokens) t FROM agents")
    return {"agents": a["n"] or 0, "tokens": a["t"] or 0}


def add_plugin(user_id, pid, content, docs):
    timestamp = time.time()
    with _lock:
        conn = connect()
        try:
            conn.execute('INSERT INTO plugins (id,user_id,name,description,enabled,docs,warnings,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                         (pid, user_id, content['name'], content['description'], 0, jdump(docs), jdump(content['warnings']), timestamp, timestamp))
            for rec in content['servers']:
                mid = rid('m_')
                conn.execute('INSERT INTO mcp_servers (id,user_id,name,transport,command,args,url,headers,env,timeout,enabled,tools,tools_at,last_error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (mid, user_id, rec['name'], rec['transport'], rec.get('command') or '', jdump(rec.get('args') or []), rec.get('url') or '', jdump(rec.get('headers') or {}), jdump(rec.get('env') or {}), rec.get('timeout') or 0, 0, '[]', 0, '', timestamp, timestamp))
                conn.execute('INSERT INTO plugin_mcp (plugin_id,server_id) VALUES (?,?)', (pid, mid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_plugin(pid)


def get_plugin(pid):
    return one('SELECT * FROM plugins WHERE id = ?', (pid,))


def list_plugins(user_id):
    return many('SELECT * FROM plugins WHERE user_id = ? ORDER BY created_at ASC', (user_id,))


def plugin_servers(pid):
    return many('SELECT m.* FROM mcp_servers m JOIN plugin_mcp p ON p.server_id=m.id WHERE p.plugin_id=?', (pid,))


def plugin_for_server(mid):
    return one('SELECT p.* FROM plugins p JOIN plugin_mcp pm ON pm.plugin_id=p.id WHERE pm.server_id=?', (mid,))


def toggle_plugin(pid, enabled):
    with _lock:
        conn = connect()
        try:
            value = 1 if enabled else 0
            conn.execute('UPDATE plugins SET enabled=?,updated_at=? WHERE id=?', (value, time.time(), pid))
            conn.execute('UPDATE mcp_servers SET enabled=?,updated_at=? WHERE id IN (SELECT server_id FROM plugin_mcp WHERE plugin_id=?)', (value, time.time(), pid))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return get_plugin(pid)


def delete_plugin(pid):
    with _lock:
        conn = connect()
        try:
            conn.execute('DELETE FROM mcp_servers WHERE id IN (SELECT server_id FROM plugin_mcp WHERE plugin_id=?)', (pid,))
            conn.execute('DELETE FROM plugin_mcp WHERE plugin_id=?', (pid,))
            conn.execute('DELETE FROM plugins WHERE id=?', (pid,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
