#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py — HTTP + SSE сервер MRC Agent (только stdlib).

Маршруты:
  /                        UI
  /static/*                статика
  /api/auth/register|login       регистрация и вход (ник + пароль)
  /api/...                 REST API (см. README)
  /api/stream              SSE-поток событий (агенты, токены, файлы)
  /preview/<pid>/<path>    живой preview HTML из workspace
  /download/<pid>/<path>   скачивание файла
"""

import json
import mimetypes
import re
import os
import sys
import posixpath
import shutil
import threading
import time
import traceback
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import auth
import bus
import db
import files
import ide_build
import llm
import mailbox
import mcp
import plugins
import questions
import orchestrator
import tools
import voice_tts
from orchestrator import MANAGER

BASE = Path(__file__).resolve().parent


def _static_dir():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", BASE)) / "static"
    return BASE / "static"


STATIC = _static_dir()
COOKIE = "ms_session"

# Какие Host принимаем. Всё остальное — 403: это защита от DNS rebinding и от
# обращения к приложению со стороннего сайта. `--host <свой-ip>` добавляет свой
# адрес, MS_ALLOW_HOSTS=1.2.3.4,my.lan — дополнительные (для доступа с телефона).
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def allow_hosts(*extra):
    for h in extra:
        for piece in str(h or "").split(","):
            piece = piece.strip().lower()
            if piece and piece not in ("0.0.0.0", "::"):
                ALLOWED_HOSTS.add(piece)
    return ALLOWED_HOSTS
VERSION = "1.6.0"


# --------------------------------------------------------------------------- #
# Мелочи
# --------------------------------------------------------------------------- #


DB_SKIP = {"token"}            # что не имеет смысла досылать при переподключении


class LoginGuard:
    """
    Простейшая защита от перебора: 8 неудач за 10 минут на пару IP+ник → пауза,
    с каждым следующим промахом пауза удваивается. Своих же не блокирует надолго.
    """

    def __init__(self, limit=8, window=600):
        self.limit = limit
        self.window = window
        self.fails = {}
        self.lock = threading.Lock()

    def _key(self, ip, nick=None):
        # Считаем по IP, а не по нику: иначе посторонний «выключает» вход владельцу ника.
        return ("ip|%s" % (ip or "?"))[:120]

    def delay_for(self, ip, nick=None):
        """Сколько секунд подождать перед проверкой пароля (нарастающая задержка)."""
        with self.lock:
            rec = self.fails.get(self._key(ip))
            if not rec:
                return 0
            count, until = rec
            return max(0, until - time.time())

    def wait_for(self, ip, nick=None):
        return self.delay_for(ip, nick)

    def fail(self, ip, nick=None):
        with self.lock:
            k = self._key(ip)
            count, _ = self.fails.get(k, (0, 0))
            count += 1
            if count >= self.limit:
                pause = min(30, 1.5 * (2 ** (count - self.limit)))    # 1.5с, 3с, 6с … максимум 30с
                self.fails[k] = (count, time.time() + pause)
            else:
                self.fails[k] = (count, 0)

    def ok(self, ip, nick=None):
        with self.lock:
            self.fails.pop(self._key(ip), None)


LOGIN_GUARD = LoginGuard()


def sse_user_owns(user, pid):
    """Есть ли у пользователя такой проект (для фильтра событий)."""
    if not db.valid_project_id(pid) or not user:
        return False
    p = db.get_project(pid)
    return bool(p and p["user_id"] == user["id"])


def sse_visible(user, topic):
    """Можно ли отдать это событие пользователю: только свои проекты и свои чаты."""
    if not user:
        return False
    if topic.startswith("project:"):
        return sse_user_owns(user, topic.split(":", 1)[1])
    if topic.startswith("chat:"):
        c = db.one("SELECT user_id FROM chats WHERE id = ?", (topic.split(":", 1)[1],))
        return bool(c and c["user_id"] == user["id"])
    return False


def id_of_mcp(row):
    """Стабильный ключ пула подключений для строки из базы."""
    return row["id"] if row else ""


def mcp_cfg(row):
    """Строка БД → конфиг для mcp.py (JSON-поля разворачиваем, таймаут общий если у сервера 0)."""
    st = db.get_settings()
    common = int(st.get("mcp_timeout") or 0)
    own = int(row.get("timeout") or 0)
    return {"id": row["id"], "name": row["name"], "transport": row.get("transport") or "stdio",
            "command": row.get("command") or "", "args": db.jload(row.get("args"), []) or [],
            "url": row.get("url") or "", "headers": db.jload(row.get("headers"), {}) or {},
            "env": db.jload(row.get("env"), {}) or {},
            "timeout": own or common, "enabled": int(row.get("enabled") or 0)}


SECRET_HEADERS = ("authorization", "x-api-key", "api-key", "token", "cookie")


def public_mcp(row):
    """Сервер для интерфейса: секретные заголовки не отдаём, только флаг «задан»."""
    headers = db.jload(row.get("headers"), {}) or {}
    safe, secret = {}, []
    for k, v in headers.items():
        if any(s in str(k).lower() for s in SECRET_HEADERS):
            secret.append(k)
        else:
            safe[k] = v
    tools = db.jload(row.get("tools"), []) or []
    owner = db.plugin_for_server(row["id"])
    return {"plugin_name": owner["name"] if owner else "",
            "plugin_enabled": bool(owner["enabled"]) if owner else True,
            "id": row["id"], "name": row["name"], "transport": row.get("transport") or "stdio",
            "command": row.get("command") or "", "args": db.jload(row.get("args"), []) or [],
            "url": row.get("url") or "", "headers": safe, "secret_headers": secret,
            "env_keys": list((db.jload(row.get("env"), {}) or {}).keys()),
            "timeout": int(row.get("timeout") or 0),
            "enabled": bool(int(row.get("enabled") or 0)),
            "tools": tools, "tool_count": len(tools),
            "tools_at": row.get("tools_at") or 0,
            "last_error": row.get("last_error") or ""}


def public_settings():
    s = db.get_settings()
    key = s.get("api_key") or ""
    return {
        "model": s.get("model"),
        "base_url": s.get("base_url"),
        "reasoning": s.get("reasoning"),
        "concurrency": s.get("concurrency"),
        "agents": s.get("agents"),
        "allow_terminal": bool(s.get("allow_terminal")),
        "allow_network": bool(s.get("allow_network")),
        "theme": s.get("theme"),
        "accent": s.get("accent"),
        "demo_mode": bool(s.get("demo_mode")),
        "terminal_timeout": s.get("terminal_timeout"),
        "terminal_strict": bool(s.get("terminal_strict")),
        "loop_guard": bool(s.get("loop_guard", 1)),
        "merge_interval": s.get("merge_interval"),
        "searx_url": s.get("searx_url") or "",
        # почта: всё, кроме пароля; пароль только флагом «задан»
        "mail_imap_host": s.get("mail_imap_host") or "",
        "mail_imap_port": s.get("mail_imap_port") or 993,
        "mail_imap_enc": s.get("mail_imap_enc") or "ssl",
        "mail_smtp_host": s.get("mail_smtp_host") or "",
        "mail_smtp_port": s.get("mail_smtp_port") or 465,
        "mail_smtp_enc": s.get("mail_smtp_enc") or "ssl",
        "mail_user": s.get("mail_user") or "",
        "mail_from": s.get("mail_from") or "",
        "mail_folder": s.get("mail_folder") or "INBOX",
        "mail_allow_send": bool(int(s.get("mail_allow_send", 1) or 0)),
        "mail_ready": not mailbox.problems(mailbox.cfg(s)),
        "mail_problems": mailbox.problems(mailbox.cfg(s)),
        "mail_password_set": bool(s.get("mail_password")),
        # --- 1.5.0: музыка ---
        "music_on": bool(int(s.get("music_on", 1) or 0)),
        "music_volume": int(s.get("music_volume") if s.get("music_volume") is not None else 35),
        "music_fade": bool(int(s.get("music_fade", 1) or 0)),
        # --- 1.6.0: MCP ---
        "mcp_on": bool(int(s.get("mcp_on", 1) or 0)),
        "mcp_timeout": int(s.get("mcp_timeout") or 0),
        "mcp_prompt": bool(int(s.get("mcp_prompt", 1) or 0)),
        "has_key": bool(key),
        "key_hint": (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("задан" if key else ""),
    }


def console_text(agent, rows):
    """Консоль агента как обычный .txt — его удобно приложить к отчёту или отправить другу."""
    import datetime
    lines = ["MRC Agent · консоль агента",
             "ник:    %s" % agent.get("nick"),
             "роль:   %s" % agent.get("role"),
             "статус: %s" % agent.get("status"),
             "задача: %s" % (agent.get("task") or ""),
             "-" * 72]
    for e in rows:
        ts = datetime.datetime.fromtimestamp(e.get("ts") or 0).strftime("%H:%M:%S")
        d = e.get("data") or {}
        kind = (d.get("kind") or "") if e.get("type") == "console" else ""
        if e.get("type") == "tool":
            lines.append("%s  $ %s" % (ts, d.get("cmd") or e.get("text") or ""))
            body = d.get("out_full") or d.get("out") or ""
            for ln in str(body).splitlines() or [""]:
                lines.append("        " + ln)
            continue
        if e.get("type") == "file":
            lines.append("%s  ✎ %s" % (ts, d.get("path") or e.get("text")))
            continue
        if e.get("type") == "console" and kind == "llm":
            lines.append("%s  🧠 %s" % (ts, e.get("text") or ""))
            continue
        lines.append("%s  · %s" % (ts, e.get("text") or ""))
    return "\n".join(lines) + "\n"


def setting_llm_client():
    s = db.get_settings()
    return llm.Client(api_key=s.get("api_key"), base_url=s.get("base_url"), model=s.get("model"))


def user_public(u):
    if not u:
        return None
    return {"id": u["id"], "email": u["email"], "name": u["name"],
            "picture": u["picture"], "provider": u["provider"]}


def project_public(p):
    agents = db.list_agents(p["id"])
    working = sum(1 for a in agents if a["status"] == "working")
    done = sum(1 for a in agents if a["status"] == "done")
    tokens = sum(a["tokens"] or 0 for a in agents)
    tin = p.get("tokens_in") or 0
    tout = p.get("tokens_out") or 0
    # если реального usage нет (демо), честно показываем оценку и не выдаём за факт
    exact = bool(tin or tout)
    cost = round(llm.cost(tin, tout), 4) if exact else 0.0
    return {
        "id": p["id"], "name": p["name"], "goal": p["goal"], "status": p["status"],
        "kind": p["kind"], "progress": p["progress"] or 0, "created_at": p["created_at"],
        "agents_total": len(agents), "agents_working": working, "agents_done": done,
        "agents_error": sum(1 for a in agents if a["status"] == "error"),
        "tokens": tokens, "cost": cost, "cost_exact": exact,
        "tokens_in": tin, "tokens_out": tout, "brief": p.get("brief") or "",
        "agents_with_work": sum(1 for a in agents if (a["files"] or "[]") not in ("", "[]")),
        "user_paused": bool(p.get("user_paused")),
        "running": MANAGER.running(p["id"]),
    }


# --------------------------------------------------------------------------- #
# Фоновый чат-ответ
# --------------------------------------------------------------------------- #


CHAT_LIVE = {}
CHAT_LIVE_LOCK = threading.Lock()


def chat_snapshot(chat_id):
    with CHAT_LIVE_LOCK:
        state = CHAT_LIVE.get(chat_id)
        return dict(state) if state and state.get("busy") else None


def chat_event(chat_id, payload):
    with CHAT_LIVE_LOCK:
        state = CHAT_LIVE.get(chat_id)
        if state is not None:
            if payload.get("type") == "token":
                state["text"] = payload.get("text") or ""
            if payload.get("type") == "thought":
                state["thought"] = payload.get("text") or ""
            if payload.get("type") in ("done", "error"):
                state["busy"] = False
            state["seq"] += 1
            payload["seq"] = state["seq"]
        bus.publish("chat:" + chat_id, payload)


def run_chat(chat_id, text, user_id):
    try:
        answer_chat(chat_id, text, user_id)
    except Exception as exc:
        message = "Ошибка модели: %s" % exc
        db.add_message(chat_id, "assistant", message, {"error": True})
        chat_event(chat_id, {"type": "error", "text": message})
    finally:
        with CHAT_LIVE_LOCK:
            CHAT_LIVE.pop(chat_id, None)


def answer_chat(chat_id, text, user_id):
    topic = "chat:" + chat_id
    client = setting_llm_client()
    history = db.chat_messages(chat_id)[-12:]
    msgs = [{"role": "system", "content":
             "Ты Mercury — ассистент внутри MRC Agent. Отвечай по-русски, коротко и по делу. "
             "Код давай в блоках с указанием языка. Если просят сделать проект — предложи "
             "запустить рой агентов на вкладке «Проекты»."}]
    for m in history:
        msgs.append({"role": m["role"], "content": m["content"]})

    if not (client.ready and not db.get_settings().get("demo_mode")):
        # демо-ответ: без ключа, но осмысленный
        reply = (
            "**Демо-режим.** Ключ Inception не задан, поэтому отвечаю локальной заглушкой.\n\n"
            "Твой запрос: «%s»\n\n"
            "Что будет с настоящим ключом:\n"
            "- ответ Mercury 3 придёт потоком, токен за токеном;\n"
            "- код — в блоках с кнопкой «копировать»;\n"
            "- на вкладке **Агенты** можно поднять 100 субагентов и смотреть, кто что делает.\n\n"
            "```python\n# пример: как вставить ключ\nimport os\nos.environ['INCEPTION_API_KEY'] = 'sk-...'\n```"
            % text[:140]
        )
        for i in range(0, len(reply), 28):
            chat_event(chat_id, {"type": "token", "text": reply[:i + 28]})
            time.sleep(0.045)
        db.add_message(chat_id, "assistant", reply)
        chat_event(chat_id, {"type": "done", "text": reply})
        return

    buf = []
    thoughts = []

    def on_token(piece, total):
        buf.append(piece)
        chat_event(chat_id, {"type": "token", "text": "".join(buf)})

    def on_thought(piece):
        thoughts.append(piece)
        chat_event(chat_id, {"type": "thought", "text": "".join(thoughts)})

    try:
        res = client.chat(msgs, effort=db.get_settings().get("reasoning") or "medium",
                          max_tokens=8192, on_token=on_token, on_thought=on_thought)
        reply = res["text"]
        db.add_message(chat_id, "assistant", reply,
                       {"tokens": res["usage"], "model": res["model"]})
        chat_event(chat_id, {"type": "done", "text": reply,
                            "usage": res["usage"], "latency": round(res["latency"], 2)})
    except Exception as e:  # noqa: BLE001
        err = (("".join(buf) + "\n\n") if buf else "") + "Ошибка модели: %s" % e
        db.add_message(chat_id, "assistant", err, {"error": True})
        chat_event(chat_id, {"type": "error", "text": err})


def public_skill(row):
    """Скилл для интерфейса: тело отдаём целиком — его правят в редакторе."""
    if not row:
        return None
    return {"id": row["id"], "name": row["name"], "body": row["body"],
            "enabled": bool(row["enabled"]), "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "chars": len(row["body"] or ""),
            "lines": (row["body"] or "").count("\n") + 1}



def public_plugin(row):
    servers = db.plugin_servers(row['id'])
    return {'id': row['id'], 'name': row['name'], 'description': row['description'],
            'enabled': bool(row['enabled']), 'docs': db.jload(row['docs'], []),
            'warnings': db.jload(row['warnings'], []),
            'servers': [{'id': r['id'], 'name': r['name'], 'transport': r['transport'],
                         'command': r['command'], 'args': db.jload(r['args'], []),
                         'url': r['url'], 'enabled': bool(r['enabled'])} for r in servers]}


MUSIC_EXT = (".ogg", ".mp3", ".wav", ".flac", ".m4a", ".opus")


def music_dir():
    d = db.DATA / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def music_custom():
    """Своя музыка пользователя (первый файл в data/music) или None."""
    try:
        for p in sorted(music_dir().iterdir()):
            if p.is_file() and p.suffix.lower() in MUSIC_EXT:
                return p
    except OSError:
        pass
    return None


def music_default():
    """Встроенная тема из бандла (static/music/theme.ogg)."""
    try:
        for p in sorted((STATIC / "music").glob("theme.*")):
            if p.is_file() and p.suffix.lower() in MUSIC_EXT:
                return p
    except OSError:
        pass
    return None


def music_state():
    custom = music_custom()
    default = music_default()
    theme = custom or default
    s = db.get_settings()
    return {"ok": True,
            "custom": bool(custom),
            "has_default": bool(default),
            "has_theme": bool(theme),
            "name": theme.name if theme else "",
            "size": theme.stat().st_size if theme else 0,
            "url": "/api/music/file",
            "on": bool(int(s.get("music_on", 1) or 0)),
            "volume": int(s.get("music_volume") if s.get("music_volume") is not None else 35),
            "fade": bool(int(s.get("music_fade", 1) or 0))}


def public_file(row, with_digest=False):
    """Что видит браузер: имя, размер, тип и ссылку на скачивание."""
    meta = db.jload((row.get("meta") if isinstance(row, dict) else None) or "{}", {}) or {}
    out = {"id": row["id"], "name": row["name"], "size": int(row["size"] or 0),
           "human": files.human(row["size"]), "kind": row.get("kind") or files.kind_of(row["name"]),
           "mime": row.get("mime") or "", "project_id": row.get("project_id") or "",
           "url": "/api/files/%s/download" % row["id"]}
    if with_digest:
        out["digest"] = meta.get("digest") or files.digest(row["path"], row["name"], row["size"])
    return out


def promote_files(user, pid, ids):
    """
    Черновики вложений переезжают в workspace проекта, в папку files/.
    Так их видят агенты (общий workspace) и они попадают в «Файлы» проекта.
    """
    moved = []
    if not isinstance(ids, list):
        return moved
    folder = tools.workspace_for(pid) / "files"
    for fid in ids[:500]:
        row = db.get_file(str(fid))
        if not row or row["user_id"] != user["id"]:
            continue
        if row["project_id"] == pid:
            moved.append(row)
            continue
        src = Path(row["path"])
        if not src.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = files._free_path(folder, files.safe_name(row["name"]))
        try:
            shutil.move(str(src), str(target))
        except Exception:
            continue
        digest = files.digest(str(target))[:800]
        row = db.update_file(row["id"], project_id=pid, name=target.name, path=str(target),
                             meta=db.jdump({"rel": "files/" + target.name, "digest": digest}))
        moved.append(row)
    return moved


# --------------------------------------------------------------------------- #
# HTTP-обработчик
# --------------------------------------------------------------------------- #


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MRCAgent/" + VERSION

    # ---------------- базовое ----------------
    def log_message(self, fmt, *args):
        if os.environ.get("MS_VERBOSE"):
            BaseHTTPRequestHandler.log_message(self, fmt, *args)

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, obj, code=200, headers=None):
        self._send(code, json.dumps(obj, ensure_ascii=False), headers=headers)

    def err(self, msg, code=400):
        self.json({"ok": False, "error": str(msg)}, code)


    def _drain(self, n):
        """Дочитать и выбросить тело, чтобы браузер получил ответ, а не «сеть пропала»."""
        left = int(n)
        while left > 0:
            try:
                chunk = self.rfile.read(min(left, 262144))
            except Exception:                       # noqa: BLE001
                self.close_connection = True
                return
            if not chunk:
                self.close_connection = True
                return
            left -= len(chunk)

    def body_json(self, limit=None):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        if limit is not None and n > int(limit):
            self.close_connection = True
            raise ValueError("слишком большой запрос")
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        m = c.get(name)
        return m.value if m else None

    def current_user(self):
        return db.user_by_session(self.cookie(COOKIE))

    # ---------------- A4: с какого адреса пришли ----------------
    def host_ok(self):
        raw = (self.headers.get("Host") or "").strip().lower()
        if not raw:
            return True                      # HTTP/1.0 без Host — локальные утилиты
        host = raw
        if host.startswith("["):             # [::1]:8777
            host = host[1:host.find("]")] if "]" in host else host
        elif host.count(":") == 1:
            host = host.split(":")[0]
        return host in ALLOWED_HOSTS

    def origin_ok(self):
        """Изменяющие запросы: Origin, если он есть, обязан совпадать со своим."""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True                      # curl, скрипты, тесты — без Origin
        if origin == "null":                 # iframe в sandbox: ему доступ только к preview
            return False
        return origin.lower() == self.base_url().lower()

    def agent_of_project(self, aid, pid):
        if not db.valid_agent_id(aid):
            return None
        a = db.get_agent(aid)
        return a if (a and a["project_id"] == pid) else None

    def is_loopback(self):
        host = (self.client_address or ("",))[0]
        return host in ("127.0.0.1", "::1", "localhost")

    def wants_json(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return ctype == "application/json"

    def base_url(self):
        host = self.headers.get("Host") or ("127.0.0.1:%d" % self.server.server_address[1])
        scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
        return "%s://%s" % (scheme, host)

    # ---------------- роутинг ----------------
    def do_GET(self):
        try:
            self.route("GET")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.err("внутренняя ошибка: %s" % e, 500)

    def do_POST(self):
        try:
            self.route("POST")
        except ValueError as e:                 # 19: лимит тела запроса
            self.err(str(e), 413)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.err("внутренняя ошибка: %s" % e, 500)

    def do_PUT(self):
        try:
            self.route("PUT")
        except ValueError as e:                 # 19: лимит тела запроса
            self.err(str(e), 413)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.err("внутренняя ошибка: %s" % e, 500)

    def do_DELETE(self):
        try:
            self.route("DELETE")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.err("внутренняя ошибка: %s" % e, 500)

    def route(self, method):
        if not self.host_ok():
            return self.err("этот адрес не обслуживается: откройте 127.0.0.1", 403)
        if method in ("POST", "PUT", "DELETE") and not self.origin_ok():
            return self.err("запрос пришёл с чужого сайта", 403)
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)

        pid_hint = ""
        if path in ("/", "/index.html"):
            return self.serve_static("index.html")
        if path.startswith("/static/"):
            return self.serve_static(path[len("/static/"):])
        if path.startswith("/preview/"):
            return self.serve_workspace_file(path[len("/preview/"):], download=False,
                                             agent=(query.get("agent") or [""])[0], signed=True)
        if path.startswith("/download/"):
            return self.serve_workspace_file(path[len("/download/"):], download=True,
                                             agent=(query.get("agent") or [""])[0], signed=True)
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")

        # --- auth ---
        if path == "/api/stream":
            user = self.current_user()
            if not user:
                return self.err("нужен вход", 401)
            last = self.headers.get("Last-Event-ID") or (query.get("last_id") or [0])[0]
            try:
                last_id = int(last or 0)
            except (TypeError, ValueError):
                last_id = 0
            return self.sse(last_id, (query.get("project") or [pid_hint] or [""])[0], user)

        # --- api ---
        if path.startswith("/api/"):
            return self.api(method, path[len("/api/"):], query)

        return self._send(404, "not found", "text/plain; charset=utf-8")

    # ---------------- статика ----------------
    def serve_static(self, rel):
        rel = rel.replace("\\", "/").lstrip("/")
        if ".." in rel:
            return self._send(403, "forbidden", "text/plain")
        p = STATIC / rel
        if not p.exists() or not p.is_file():
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix in (".js", ".css", ".html"):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    def serve_workspace_file(self, rest, download=False, agent="", signed=False):
        """
        Файл проекта для preview/download.

        Доступ — только по подписанной ссылке: /preview/<exp-hmac>/<pid>/<путь>.
        Подпись выдаёт API после входа и проверки владельца проекта; живёт 30 минут
        и привязана к pid. Поэтому:
          * `..`/`%2e%2e` в pid или пути не проходят (маска id + resolve),
          * чужой проект не открывается даже со своей подписью,
          * а подресурсы iframe (css/js/картинки) работают без cookie — подпись в пути.
        """
        parts = rest.split("/", 2) if signed else rest.split("/", 1)
        if signed:
            if len(parts) != 3:
                return self._send(401, "нужна подписанная ссылка", "text/plain; charset=utf-8")
            token, pid, rel = parts[0], parts[1], parts[2]
            if not db.valid_project_id(pid):
                return self._send(404, "проект не найден", "text/plain; charset=utf-8")
            if not auth.check_preview_token(pid, token):
                return self._send(401, "ссылка предпросмотра недействительна", "text/plain; charset=utf-8")
            project = db.get_project(pid)
            if not project:
                return self._send(404, "проект не найден", "text/plain; charset=utf-8")
        else:
            if len(parts) != 2:
                return self._send(400, "bad path", "text/plain")
            pid, rel = parts[0], parts[1]
            if not db.valid_project_id(pid):
                return self._send(404, "проект не найден", "text/plain; charset=utf-8")
            user = self.current_user()
            project = db.get_project(pid)
            if not user or not project or project["user_id"] != user["id"]:
                return self._send(401, "нужен вход", "text/plain; charset=utf-8")
        if agent and not db.valid_agent_id(agent):
            return self._send(404, "агент не найден", "text/plain; charset=utf-8")
        if agent:
            a = db.get_agent(agent)
            if not a or a["project_id"] != pid:
                return self._send(404, "агент не найден", "text/plain; charset=utf-8")
        try:
            content = tools.read_bytes(pid, rel, agent or None)   # бинарно: картинки и zip не портятся
        except Exception:
            return self._send(404, "файл не найден", "text/plain; charset=utf-8")
        ctype = mimetypes.guess_type(rel)[0] or "text/plain"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        headers = {}
        if download:
            name = posixpath.basename(rel)
            ascii_name = re.sub(r'[^A-Za-z0-9._-]', '_', name) or 'download'
            encoded = urllib.parse.quote(name, safe='')
            headers["Content-Disposition"] = "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, encoded)
        headers["X-Content-Type-Options"] = "nosniff"
        self._send(200, content, ctype, headers)

    # ---------------- SSE ----------------
    def sse(self, last_id=0, pid=None, user=None):
        """
        Поток событий. Если браузер переподключился (EventSource сам присылает Last-Event-ID),
        сначала досылаем пропущенное из базы, чтобы при закрытии окна ничего не терялось.

        Отдаём только события проектов и чатов этого пользователя: поток общий на процесс,
        а рой у каждого свой.
        """
        sub = bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            if last_id and pid and sse_user_owns(user, pid):
                try:
                    missed = [e for e in db.events_since(pid, int(last_id), limit=500) if e["type"] not in DB_SKIP]
                except Exception:  # noqa: BLE001
                    missed = []
                for e in missed:
                    self.wfile.write(bus.sse_format("project:" + pid, {
                        "id": e["id"], "project": pid, "agent": e["agent_id"],
                        "type": e["type"], "text": e["text"],
                        "data": db.jload(e.get("data"), {}) or {}, "ts": e["ts"],
                        "replay": True}).encode("utf-8"))
                if missed:
                    self.wfile.write(bus.sse_format("project:" + pid, {
                        "type": "replay_end", "project": pid, "count": len(missed)}).encode("utf-8"))
                self.wfile.flush()
            last_ping = time.time()
            while True:
                try:
                    item = sub.q.get(timeout=1.0)
                    if not sse_visible(user, item["topic"]):
                        continue
                    self.wfile.write(bus.sse_format(item["topic"], item["payload"]).encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    if time.time() - last_ping > 12:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
                if self.wfile.closed:
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(sub)
            self.close_connection = True

    # ---------------- REST ----------------
    def api(self, method, path, query):
        parts = [p for p in path.split("/") if p]
        user = self.current_user()

        # публичное
        if path == "health":
            # Посторонним — только «жив/версия». Путь к данным, pid и instance выдаём,
            # если вызывающий уже знает instance (это умеет только локальный app.py,
            # который читает db.instance_id() из своей папки): без входа не утекает.
            base = {"ok": True, "version": VERSION}
            known = (self.headers.get("X-MRC-Instance") or "").strip()
            if user or (known and known == db.instance_id()):
                base.update({"instance": db.instance_id(), "data": str(db.DATA),
                             "pid": os.getpid(), "demo": not public_settings()["has_key"]})
            return self.json(base)
        if path == "bootstrap":
            return self.json({
                "ok": True, "user": user_public(user),
                "onboarded": db.is_onboarded(user["id"]) if user else True,
                "settings": public_settings(),
                "projects": [project_public(p) for p in db.list_projects(user["id"])] if user else [],
                "chats": db.list_chats(user["id"]) if user else [],
                "version": VERSION,
            })
        if path in ("auth/register", "auth/login") and method == "POST":
            body = self.body_json()
            nick = body.get("name")
            pwd = body.get("password")
            ip = (self.client_address or ("?",))[0]
            # Считаем по IP и не блокируем вход насовсем: после серии промахов
            # добавляем задержку (1.5 → 3 → 6 … до 30 с), но верный пароль всё равно пускает.
            if path == "auth/login":
                wait = LOGIN_GUARD.delay_for(ip)
                if wait > 0:
                    time.sleep(min(wait, 30))
            try:
                if path == "auth/register":
                    u, token, claimed = auth.register(nick, pwd)
                else:
                    u, token, claimed = auth.login(nick, pwd)
            except ValueError as e:
                if path == "auth/login":
                    LOGIN_GUARD.fail(ip, nick)
                return self.err(str(e), 400)
            LOGIN_GUARD.ok(ip, nick)
            return self.json({"ok": True, "user": user_public(u), "claimed": claimed},
                             headers={"Set-Cookie": "%s=%s; Path=/; HttpOnly; Max-Age=%d; SameSite=Strict"
                                                    % (COOKIE, token, 30 * 86400)})
        if path == "auth/logout" and method == "POST":
            tok = self.cookie(COOKIE)
            if tok:
                db.drop_session(tok)
            return self.json({"ok": True}, headers={"Set-Cookie": "%s=; Path=/; Max-Age=0" % COOKIE})
        if path == "shutdown" and method == "POST":
            host = (self.client_address or ("",))[0]
            if host not in ("127.0.0.1", "::1", "localhost"):
                return self.err("остановка доступна только с этой машины", 403)
            orchestrator.MANAGER.shutdown_all()
            mcp.close_all()                      # дочерние MCP-процессы не должны пережить приложение
            self.json({"ok": True, "bye": True})
            import threading as _t
            _t.Timer(0.4, lambda: os._exit(0)).start()
            return
        if not user:
            return self.err("нужен вход", 401)

        if path == "voice/speak" and method == "POST":
            if not self.wants_json():
                return self.err("ожидается JSON", 415)
            body = self.body_json(limit=1048576)
            text = body.get("text") if isinstance(body, dict) else None
            try:
                audio = voice_tts.synthesize(text)
            except ValueError as error:
                return self.err(str(error), 400)
            except voice_tts.VoiceUnavailable as error:
                return self.err(str(error), 503)
            except Exception:
                return self.err("Не удалось создать озвучку. Проверьте установку голоса Piper.", 500)
            return self._send(200, audio, "audio/wav", headers={"X-Content-Type-Options":"nosniff"})

        if path == "questions" and method == "GET":
            return self.json({"ok": True, "questions": questions.list_for(user["id"])})
        if len(parts) == 3 and parts[0] == "questions" and parts[2] == "answer" and method == "POST":
            body = self.body_json()
            answer = body.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                return self.err("введите ответ", 400)
            if not questions.answer(parts[1], user["id"], answer):
                return self.err("вопрос не найден или время ответа уже вышло", 409)
            return self.json({"ok": True})

        if path == "resume_all" and method == "POST":
            pids = orchestrator.MANAGER.resume_all()
            return self.json({"ok": True, "resumed": pids})

        if path == "mail/test" and method == "POST":
            s = db.get_settings()
            res = mailbox.test(s)
            return self.json(res if res.get("ok") else {"ok": False,
                                                        "error": res.get("reason") or "нет связи"})

        # ---------------- обучение при первом входе ----------------
        if path == "onboard/done" and method == "POST":
            db.set_onboarded(user["id"], 1)
            return self.json({"ok": True, "onboarded": True})
        if path == "onboard/again" and method == "POST":
            db.set_onboarded(user["id"], 0)          # «показать обучение ещё раз» из настроек
            return self.json({"ok": True, "onboarded": False})

        # ---------------- музыка проекта ----------------
        if path == "music" and method == "GET":
            return self.json(music_state())

        if path == "music" and method == "POST":
            # своя музыка: тот же base64, что и у вложений, но кладём в data/music
            body = self.body_json(limit=256 * 1048576)
            name = body.get("name") or "theme.ogg"
            try:
                saved = files.save_base64(body.get("data") or "", music_dir(), name)
            except ValueError as e:
                return self.err(str(e), 400)
            target = music_dir() / ("theme" + (Path(saved["name"]).suffix.lower() or ".ogg"))
            src = Path(saved["path"])
            try:
                if src.name != target.name:
                    if target.exists():
                        target.unlink()
                    src.replace(target)              # сначала переименовываем свой файл…
            except OSError as e:
                return self.err("не смог сохранить музыку: %s" % e, 500)
            for old in music_dir().iterdir():        # …и только потом убираем прежние треки
                if old.is_file() and old.name != target.name:
                    try:
                        old.unlink()
                    except OSError:
                        pass
            return self.json({"ok": True, "music": music_state()})

        if path == "music" and method == "DELETE":
            custom = music_custom()
            if custom:
                try:
                    custom.unlink()
                except OSError:
                    pass
            return self.json({"ok": True, "music": music_state()})

        if path == "music/file" and method == "GET":
            theme = music_custom() or music_default()
            if not theme:
                return self.err("музыки нет", 404)
            ctype = {".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
                     ".flac": "audio/flac", ".m4a": "audio/mp4", ".opus": "audio/ogg"}.get(
                         theme.suffix.lower(), "application/octet-stream")
            data = theme.read_bytes()
            total = len(data)
            rng = (self.headers.get("Range") or "").strip()
            # Range нужен аудио: браузер просит кусок, чтобы знать длину и уметь перематывать
            if rng.startswith("bytes="):
                try:
                    part = rng[6:].split(",")[0]
                    a, _, b = part.partition("-")
                    start = int(a) if a else 0
                    end = int(b) if b else total - 1
                    start = max(0, min(start, total - 1))
                    end = max(start, min(end, total - 1))
                except ValueError:
                    start, end = 0, total - 1
                return self._send(206, data[start:end + 1], ctype=ctype,
                                  headers={"Cache-Control": "no-cache",
                                           "Accept-Ranges": "bytes",
                                           "Content-Range": "bytes %d-%d/%d" % (start, end, total)})
            return self._send(200, data, ctype=ctype,
                              headers={"Cache-Control": "no-cache", "Accept-Ranges": "bytes"})

        # Плагины импортируются выключенными и не запускают код при установке.
        if path == 'plugins' and method == 'GET':
            return self.json({'ok': True, 'plugins': [public_plugin(r) for r in db.list_plugins(user['id'])]})
        if path == 'plugins/import' and method == 'POST':
            body = self.body_json()
            try:
                content = plugins.inspect_archive(body.get('name'), body.get('data') or '')
                row = plugins.install(user['id'], content)
            except (ValueError, OSError) as e:
                return self.err(str(e), 400)
            return self.json({'ok': True, 'plugin': public_plugin(row)})
        if len(parts) == 4 and parts[0] == 'plugins' and parts[2] == 'docs' and method == 'GET':
            row = db.get_plugin(parts[1])
            if not row or row['user_id'] != user['id']:
                return self.err('плагин не найден', 404)
            docs = db.jload(row['docs'], [])
            try:
                item = docs[int(parts[3])]
                if int(parts[3]) < 0:
                    raise IndexError()
                text = (db.DATA / 'plugins' / row['id'] / item['file']).read_text(encoding='utf-8')
            except (ValueError, IndexError, OSError, UnicodeError):
                return self.err('документ не найден', 404)
            return self.json({'ok': True, 'name': item['name'], 'body': text})
        if len(parts) == 2 and parts[0] == 'plugins':
            row = db.get_plugin(parts[1])
            if not row or row['user_id'] != user['id']:
                return self.err('плагин не найден', 404)
            if method == 'PUT':
                body = self.body_json()
                if not isinstance(body.get('enabled'), bool):
                    return self.err('нужно указать enabled: true или false', 400)
                enabled = body['enabled']
                servers = db.plugin_servers(row['id'])
                updated = db.toggle_plugin(row['id'], enabled)
                if not enabled:
                    for server in servers:
                        mcp.drop(mcp_cfg(server))
                return self.json({'ok': True, 'plugin': public_plugin(updated)})
            if method == 'DELETE':
                for server in db.plugin_servers(row['id']):
                    mcp.drop(mcp_cfg(server))
                db.delete_plugin(row['id'])
                shutil.rmtree(db.DATA / 'plugins' / row['id'], ignore_errors=True)
                return self.json({'ok': True})

        # ---------------- MCP: внешние серверы инструментов ----------------
        if path == "mcp" and method == "GET":
            st = db.get_settings()
            return self.json({"ok": True,
                              "servers": [public_mcp(r) for r in db.list_mcp_servers(user["id"])],
                              "on": bool(int(st.get("mcp_on", 1) or 0)),
                              "timeout": int(st.get("mcp_timeout") or 0),
                              "pool": mcp.pool_size()})

        if path == "mcp" and method == "POST":
            body = self.body_json()
            try:
                rec = mcp.clean_server(name=body.get("name"), transport=body.get("transport"),
                                       command=body.get("command"), args=body.get("args"),
                                       url=body.get("url"), headers=body.get("headers"),
                                       env=body.get("env"), timeout=body.get("timeout"),
                                       enabled=body.get("enabled", 1))
            except ValueError as e:
                return self.err(str(e), 400)
            mid = str(body.get("id") or "")
            if mid:
                row = db.get_mcp_server(mid)
                if not row or row["user_id"] != user["id"]:
                    return self.err("сервер не найден", 404)
                mcp.drop(id_of_mcp(row))
                row = db.update_mcp_server(mid, rec=rec, last_error="")
                # команду поменяли — старый кэш инструментов уже врёт
                db.update_mcp_server(mid, tools=[])
            else:
                row = db.add_mcp_server(user["id"], rec)
            return self.json({"ok": True, "server": public_mcp(row)})

        if len(parts) >= 2 and parts[0] == "mcp" and parts[1] != "settings":
            row = db.get_mcp_server(parts[1])
            if not row or row["user_id"] != user["id"]:
                return self.err("сервер не найден", 404)
            cfg = mcp_cfg(row)
            owner_plugin = db.plugin_for_server(row['id'])
            if owner_plugin and not owner_plugin['enabled']:
                return self.err('Сначала включите плагин в разделе «Плагины»', 403)

            if len(parts) == 2 and method == "DELETE":
                mcp.drop(cfg)
                db.delete_mcp_server(row["id"])
                return self.json({"ok": True})

            # «Проверить» — реально подключаемся и просим список инструментов
            if len(parts) == 3 and parts[2] == "test" and method == "POST":
                res = mcp.list_tools(cfg)
                if res.get("ok"):
                    tools_now = [{"name": t["name"], "description": t["description"],
                                  "input_schema": t["input_schema"]} for t in res["tools"]]
                    db.update_mcp_server(row["id"], tools=tools_now, last_error="")
                    return self.json({"ok": True, "tools": tools_now,
                                      "count": len(tools_now),
                                      "info": res.get("info") or {},
                                      "server": public_mcp(db.get_mcp_server(row["id"]))})
                db.update_mcp_server(row["id"], last_error=res.get("reason") or "нет связи")
                return self.json({"ok": False, "reason": res.get("reason") or "нет связи",
                                  "server": public_mcp(db.get_mcp_server(row["id"]))})

            # ручной вызов инструмента из интерфейса: видно, что сервер реально работает
            if len(parts) == 3 and parts[2] == "call" and method == "POST":
                body = self.body_json()
                res = mcp.call_tool(cfg, body.get("tool") or "", body.get("args") or {})
                return self.json(res)

            if len(parts) == 2 and method == "PUT":
                body = self.body_json()
                if "enabled" in body:
                    row = db.update_mcp_server(row["id"], enabled=body.get("enabled"))
                    if not int(row.get("enabled") or 0):
                        mcp.drop(cfg)
                    return self.json({"ok": True, "server": public_mcp(row)})
                try:
                    rec = mcp.clean_server(name=body.get("name", row["name"]),
                                           transport=body.get("transport", row["transport"]),
                                           command=body.get("command", row["command"]),
                                           args=body.get("args"), url=body.get("url", row["url"]),
                                           headers=body.get("headers"), env=body.get("env"),
                                           timeout=body.get("timeout", row["timeout"]),
                                           enabled=1)
                except ValueError as e:
                    return self.err(str(e), 400)
                mcp.drop(cfg)
                row = db.update_mcp_server(row["id"], rec=rec, tools=[], last_error="")
                return self.json({"ok": True, "server": public_mcp(row)})
            return self.err("неизвестное действие с MCP-сервером", 404)

        # общий тумблер MCP и таймаут
        if path == "mcp/settings" and method == "POST":
            body = self.body_json()
            patch = {}
            if "on" in body:
                patch["mcp_on"] = 1 if body.get("on") else 0
            if "prompt" in body:
                patch["mcp_prompt"] = 1 if body.get("prompt") else 0
            if "timeout" in body:
                patch["mcp_timeout"] = max(0, int(body.get("timeout") or 0))
            db.set_settings(patch)
            return self.json({"ok": True, "settings": public_settings()})

        # ---------------- скиллы агентов (свои .md-инструкции) ----------------
        if path == "skills" and method == "GET":
            return self.json({"ok": True,
                              "skills": [public_skill(r) for r in db.list_skills(user["id"])]})

        if path == "skills" and method == "POST":
            body = self.body_json()
            try:
                row = db.add_skill(user["id"], body.get("name"), body.get("body"),
                                   enabled=1 if body.get("enabled", 1) else 0)
            except ValueError as e:
                return self.err(str(e), 400)
            return self.json({"ok": True, "skill": public_skill(row)})

        if path == "skills/import" and method == "POST":
            body = self.body_json(limit=64 * 1048576)
            data = body.get("data") or ""
            try:
                raw = files.save_base64(data, db.DATA / "tmp-skill", body.get("name") or "скилл.md")
                text = files.extract_text(raw["path"])
                Path(raw["path"]).unlink(missing_ok=True)
            except ValueError as e:
                return self.err(str(e), 400)
            if not (text or "").strip():
                return self.err("в файле нет текста — нужен markdown (.md или .txt)", 400)
            name = body.get("name") or "скилл.md"
            name = re.sub(r"\.(md|markdown|txt)$", "", str(name), flags=re.I) or "скилл"
            try:
                row = db.add_skill(user["id"], name, text)
            except ValueError as e:
                return self.err(str(e), 400)
            return self.json({"ok": True, "skill": public_skill(row)})

        if len(parts) >= 2 and parts[0] == "skills":
            row = db.get_skill(parts[1])
            if not row or row["user_id"] != user["id"]:
                return self.err("скилл не найден", 404)
            if len(parts) == 3 and parts[2] == "md" and method == "GET":
                name = (row["name"] or "скилл").replace("/", "-") + ".md"
                disp = "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name)
                return self._send(200, (row["body"] or "").encode("utf-8"),
                                  ctype="text/markdown; charset=utf-8",
                                  headers={"Content-Disposition": disp})
            if len(parts) == 2 and method == "PUT":
                body = self.body_json()
                try:
                    upd = db.update_skill(row["id"], name=body.get("name"), body=body.get("body"),
                                          enabled=body.get("enabled"))
                except ValueError as e:
                    return self.err(str(e), 400)
                return self.json({"ok": True, "skill": public_skill(upd)})
            if len(parts) == 2 and method == "DELETE":
                db.delete_skill(row["id"])
                return self.json({"ok": True})

        # ---------------- вложения: любой формат ----------------
        # Файл идёт base64 в JSON: multipart в стандартной библиотеке 3.13 уже нет (cgi удалён),
        # а свой разборщик — лишний повод для бага. 25 МБ файла ≈ 34 МБ base64.
        if path == "uploads" and method == "POST":
            st = db.get_settings()
            limit_mb = int(st.get("max_upload_mb") or 0)      # 0 = без лимита
            # Тело (base64) держим с запасом: настоящий лимит — на размер самого файла,
            # иначе на «слишком большой файл» человек увидит «слишком большой запрос».
            # по умолчанию лимита нет (1.5.0): единственный потолок — память процесса
            cap = max(64 * 1048576, int(limit_mb * 1048576 * 1.5)) if limit_mb else None
            try:
                body = self.body_json(limit=int(cap) if cap is not None else None)
            except ValueError as e:
                where = (" — лимит на файл %s задан в настройках" % files.human(limit_mb * 1048576)
                         if limit_mb else "")
                return self.err("файл слишком большой для загрузки%s (%s)" % (where, e), 413)
            name = body.get("name") or "файл"
            draft = db.DATA / "uploads" / db.rid("u_")
            try:
                saved = files.save_base64(body.get("data") or "", draft, name,
                                          max_bytes=(limit_mb * 1048576) or None)
            except ValueError as e:
                return self.err(str(e), 400)
            row = db.add_file(user["id"], saved["name"], saved["path"], saved["size"],
                              body.get("mime") or "", files.kind_of(saved["name"]),
                              meta={"digest": files.digest(saved["path"])[:800]})
            return self.json({"ok": True, "file": public_file(row, with_digest=True)})

        if path == "uploads" and method == "GET":
            return self.json({"ok": True, "files": [public_file(r) for r in
                                                    db.list_files(user["id"])]})

        if parts and parts[0] == "files" and len(parts) >= 2:
            row = db.get_file(parts[1])
            if not row or row["user_id"] != user["id"]:
                return self.err("файл не найден", 404)
            if len(parts) == 3 and parts[2] == "download" and method == "GET":
                p = Path(row["path"])
                if not p.exists():
                    return self.err("файла нет на диске", 404)
                try:
                    data = p.read_bytes()
                except OSError as e:
                    return self.err("не смог прочитать файл: %s" % e, 500)
                disp = "attachment; filename*=UTF-8''%s" % urllib.parse.quote(row["name"])
                return self._send(200, data,
                                  ctype=row["mime"] or "application/octet-stream",
                                  headers={"Content-Disposition": disp})
            if len(parts) == 2 and method == "DELETE":
                try:
                    Path(row["path"]).unlink()
                except OSError:
                    pass
                db.delete_file_row(row["id"])
                return self.json({"ok": True})

        if path == "settings/test_provider" and method == "POST":
            if not self.wants_json():
                return self.json({"ok": False, "error": "ожидается application/json"})
            body = self.body_json()
            if not isinstance(body, dict):
                return self.json({"ok": False, "error": "ожидается объект JSON"})
            settings = db.get_settings()
            key_value = body.get("api_key") if "api_key" in body else settings.get("api_key")
            url_value = body.get("base_url") if "base_url" in body else settings.get("base_url")
            model_value = body.get("model") if "model" in body else settings.get("model")
            if not all(isinstance(value, str) for value in (key_value, url_value, model_value)):
                return self.json({"ok": False, "error": "API-ключ, Base URL и модель должны быть строками"})
            key = key_value.strip()
            url = url_value.strip()
            model = model_value.strip()
            if not key or not url or not model:
                return self.json({"ok": False, "error": "укажите API-ключ, Base URL и модель"})
            if "api_key" not in body and user["id"] != db.first_user_id() and url != settings.get("base_url"):
                return self.json({"ok": False, "error": "только администратор может использовать сохранённый ключ с другим URL"})
            try:
                client = llm.Client(api_key=key, base_url=url, model=model, timeout=20, retries=0)
                result = client.probe([{"role": "user", "content": "Ответь одним словом: ок"}], max_tokens=10)
                return self.json({"ok": True, "model": result["model"],
                                  "latency": round(result["latency"], 3),
                                  "sample": result["text"][:300].replace(key, "[скрыто]")})
            except Exception as exc:
                error = str(exc).replace(key, "[скрыто]") or "не удалось проверить провайдера"
                return self.json({"ok": False, "error": error})

        if path == "settings" and method == "PUT":
            if not self.wants_json():
                return self.err("настройки принимаются только как application/json", 415)
            body = self.body_json()
            patch = {}
            for k in ("model", "base_url", "reasoning", "concurrency", "agents",
                      "allow_terminal", "allow_network", "theme", "accent", "demo_mode",
                      "terminal_timeout", "terminal_strict",
                      "loop_guard", "merge_interval", "searx_url",
                      "mail_imap_host", "mail_imap_port", "mail_imap_enc",
                      "mail_smtp_host", "mail_smtp_port", "mail_smtp_enc",
                      "mail_user", "mail_from", "mail_folder",
                      "mail_allow_send", "mail_timeout", "max_upload_mb",
                      "music_on", "music_volume", "music_fade", "mcp_on", "mcp_prompt", "mcp_timeout"):
                if k in body:
                    patch[k] = body[k]
            if "reasoning" in patch and patch["reasoning"] not in ("instant", "low", "medium", "high"):
                return self.err("неизвестный уровень Effort", 400)
            if body.get("mail_password"):
                patch["mail_password"] = str(body["mail_password"]).strip()
            if body.get("clear_mail_password"):
                patch["mail_password"] = ""
            # base_url и доступ к терминалу меняет только администратор (первый пользователь):
            # иначе любой, кто вошёл, уводит ключ на свой сервер.
            admin = (user["id"] == db.first_user_id())
            ignored = []
            if not admin:
                # пароль почты — секрет установки: его меняет только администратор.
                # (адреса и порты тоже: иначе чужой вход уводит письма на свой сервер)
                for k in ("base_url", "allow_terminal", "mail_password",
                          "mail_imap_host", "mail_imap_port", "mail_smtp_host",
                          "mail_smtp_port", "mail_user", "mail_from"):
                    if k in patch:
                        patch.pop(k, None)
                        ignored.append(k)
            if body.get("api_key"):
                patch["api_key"] = body["api_key"].strip()
            if body.get("clear_key"):
                patch["api_key"] = ""
            db.set_settings(patch)
            return self.json({"ok": True, "settings": public_settings(),
                              "ignored": ignored, "admin": admin})

        # ---- MRCIDE: сборки проекта ----
        if parts[0] == "ide" and len(parts) >= 3:
            pid = parts[1]
            project = db.get_project(pid) if db.valid_project_id(pid) else None
            if not project or project["user_id"] != user["id"]:
                return self.err("проект не найден", 404)
            action = parts[2]
            if action == "export.zip" and len(parts) == 3 and method == "GET":
                data = ide_build.export_project(pid)
                return self._send(200, data, "application/zip", {"Content-Disposition": 'attachment; filename="mrcide-project.zip"'})
            if action == "builds" and len(parts) == 3 and method == "GET":
                return self.json({"ok": True, "builds": ide_build.list_builds(pid)})
            if action == "builds" and len(parts) == 4 and method == "GET":
                result = ide_build.get(pid, parts[3])
                return self.json({"ok": True, "build": result}) if result else self.err("сборка не найдена", 404)
            if action in ("check", "start", "workflow") and len(parts) == 3 and method == "POST":
                body = self.body_json()
                target = body.get("target") or ""
                scheme = body.get("scheme") or ""
                try:
                    if action == "check":
                        return self.json({"ok": True, "check": ide_build.diagnostics(pid, target, scheme)})
                    if action == "workflow":
                        return self.json({"ok": True, "workflow": ide_build.export_workflow(pid, target, scheme)})
                    if not db.get_settings().get("allow_terminal"):
                        return self.err("сборка выключена: включите терминал в настройках", 403)
                    return self.json({"ok": True, "build": ide_build.start(pid, target, scheme)})
                except ValueError as exc:
                    return self.err(str(exc), 400)
            if action == "stop" and len(parts) == 4 and method == "POST":
                self.body_json()
                return self.json({"ok": ide_build.stop(pid, parts[3])})

        # ---- подписанная ссылка предпросмотра ----
        if parts[0] == "preview-token" and len(parts) >= 2 and method == "GET":
            pid = parts[1]
            if not db.valid_project_id(pid):
                return self.err("проект не найден", 404)
            p = db.get_project(pid)
            if not p or p["user_id"] != user["id"]:
                return self.err("проект не найден", 404)
            token, exp = auth.preview_token(pid)
            return self.json({"ok": True, "token": token, "exp": exp,
                              "base": "/preview/%s/%s/" % (token, pid)})

        if path == "profile" and method == "PUT":
            body = self.body_json()
            try:
                u = auth.rename(user["id"], body.get("name"))
            except ValueError as e:
                return self.err(str(e), 400)
            return self.json({"ok": True, "user": user_public(u)})
        if path == "auth/password" and method == "PUT":
            body = self.body_json()
            try:
                auth.change_password(user["id"], body.get("current"), body.get("password"))
                db.drop_other_sessions(user["id"], keep_token=self.cookie(COOKIE))
            except ValueError as e:
                return self.err(str(e), 400)
            return self.json({"ok": True})


        # ---- чаты ----
        if parts[0] == "chats":
            if len(parts) == 1 and method == "GET":
                return self.json({"ok": True, "chats": db.list_chats(user["id"])})
            if len(parts) == 1 and method == "POST":
                body = self.body_json()
                c = db.create_chat(user["id"], body.get("title") or "Новый чат",
                                   body.get("project_id"))
                return self.json({"ok": True, "chat": c})
            if len(parts) >= 2:
                cid = parts[1]
                chat = db.one("SELECT * FROM chats WHERE id = ?", (cid,))
                if not chat or chat["user_id"] != user["id"]:
                    return self.err("чат не найден", 404)
                if len(parts) == 2 and method == "GET":
                    return self.json({"ok": True, "chat": chat,
                                      "messages": db.chat_messages(cid), "live": chat_snapshot(cid)})
                if len(parts) == 2 and method == "DELETE":
                    db.delete_chat(cid)
                    return self.json({"ok": True})
                if len(parts) == 3 and parts[2] == "messages" and method == "POST":
                    body = self.body_json()
                    text = (body.get("text") or "").strip()
                    if not text:
                        return self.err("пустое сообщение")
                    with CHAT_LIVE_LOCK:
                        if cid in CHAT_LIVE:
                            return self.err("дождитесь ответа в этом чате", 409)
                        CHAT_LIVE[cid] = {"text": "", "thought": "", "busy": True, "seq": 0}
                    try:
                        m = db.add_message(cid, "user", text)
                        if chat["title"] in ("Новый чат", "", None):
                            db.rename_chat(cid, text[:48])
                        threading.Thread(target=run_chat, args=(cid, text, user["id"]),
                                         daemon=True).start()
                    except Exception:
                        with CHAT_LIVE_LOCK:
                            CHAT_LIVE.pop(cid, None)
                        raise
                    return self.json({"ok": True, "message": m})
                if len(parts) == 3 and parts[2] == "title" and method == "PUT":
                    body = self.body_json()
                    db.rename_chat(cid, (body.get("title") or "")[:80])
                    return self.json({"ok": True})

        # ---- проекты ----
        if parts[0] == "projects":
            if len(parts) == 1 and method == "GET":
                return self.json({"ok": True, "projects":
                                  [project_public(p) for p in db.list_projects(user["id"])]})
            if len(parts) == 1 and method == "POST":
                body = self.body_json()
                goal = (body.get("goal") or "").strip()
                if not goal:
                    return self.err("нужна цель проекта")
                name = (body.get("name") or goal[:42]).strip()
                try:
                    agents = int(body.get("agents") or db.get_settings().get("agents") or 100)
                except (TypeError, ValueError):
                    return self.err("agents должен быть числом")
                agents = max(1, min(500, agents))      # потолок: 500 строк, не 100000
                p = db.create_project(user["id"], name, goal, "build", None, agents)
                tools.workspace_for(p["id"])
                attached = promote_files(user, p["id"], body.get("files") or [])
                for r in attached:
                    orchestrator.emit(p["id"], None, "log",
                                      "пользователь прикрепил файл: %s (%s)"
                                      % (r["name"], files.human(r["size"])),
                                      {"file": r["name"], "kind": r["kind"]})
                MANAGER.ensure_agents(p["id"], agents)
                return self.json({"ok": True, "project": project_public(db.get_project(p["id"]))})
            if len(parts) >= 2:
                pid = parts[1]
                p = db.get_project(pid)
                if not p or p["user_id"] != user["id"]:
                    return self.err("проект не найден", 404)
                if len(parts) == 2 and method == "GET":
                    return self.json({"ok": True, "project": project_public(p),
                                      "agents": db.list_agents(pid),
                                      "messages": db.project_msgs(pid)[:60]})
                if len(parts) == 2 and method == "DELETE":
                    MANAGER.stop(pid, wait=True)       # даём потокам завершиться
                    for build in ide_build.list_builds(pid):
                        if build['status'] == 'running':
                            ide_build.stop(pid, build['id'])
                    db.delete_project(pid)
                    tools.drop_workspace(pid)          # и папку на диске тоже
                    return self.json({"ok": True})
                if len(parts) == 3:
                    action = parts[2]
                    if action == "start":
                        res = MANAGER.start(pid, user["id"], pending_only=bool(self.body_json().get("pending_only")))
                        return self.json(res)
                    if action == "stop":
                        return self.json({"ok": MANAGER.stop(pid)})
                    if action == "pause":
                        return self.json({"ok": MANAGER.pause(pid, True)})
                    if action == "resume":
                        return self.json({"ok": MANAGER.pause(pid, False)})
                    if action == "agents":
                        return self.json({"ok": True, "agents": db.list_agents(pid)})
                    if action == "files":
                        if method == "GET":
                            return self.json({"ok": True, "files": [
                                public_file(r, with_digest=True) for r in db.project_files(pid)]})
                        if method == "POST":
                            body = self.body_json()
                            added = promote_files(user, pid, body.get("files") or [])
                            for r in added:
                                orchestrator.emit(pid, None, "log",
                                                  "пользователь прикрепил файл: %s (%s)"
                                                  % (r["name"], files.human(r["size"])),
                                                  {"file": r["name"]})
                            return self.json({"ok": True, "added": len(added), "files": [
                                public_file(r, with_digest=True) for r in db.project_files(pid)]})
                    if action == "events":
                        try:
                            since = int((query.get("since") or ["0"])[0])
                        except (TypeError, ValueError):
                            return self.err("since должен быть числом")
                        since = max(0, since)
                        return self.json({"ok": True, "events": db.events_since(pid, since)})

        # ---- агенты ----
        if parts[0] == "agents" and len(parts) >= 2:
            aid = parts[1]
            a = db.get_agent(aid)
            if not a:
                return self.err("агент не найден", 404)
            p = db.get_project(a["project_id"])
            if not p or p["user_id"] != user["id"]:
                return self.err("нет доступа", 403)
            # ---- консоль агента: по порядку, с догрузкой по since ----
            if len(parts) >= 3 and parts[2] in ("console", "console.txt"):
                since = int((query.get("since") or ["0"])[0] or 0)
                limit = min(1000, max(1, int((query.get("limit") or ["500"])[0] or 500)))
                rows = db.agent_console(aid, since, limit)
                if parts[2] == "console.txt":
                    rows = []
                    cursor = 0
                    while True:
                        batch = db.agent_console(aid, cursor, 1000)
                        rows.extend(batch)
                        if len(batch) < 1000:
                            break
                        cursor = batch[-1]["id"]
                    txt = console_text(a, rows)
                    return self._send(200, txt, ctype="text/plain; charset=utf-8",
                                      headers={"Content-Disposition":
                                               'attachment; filename="console-%s.txt"' % aid})
                return self.json({
                    "ok": True,
                    "agent": {"id": a["id"], "nick": a["nick"], "role": a["role"],
                              "status": a["status"], "activity": a["activity"],
                              "task": a["task"], "lane": a["lane"],
                              "files": db.jload(a.get("files"), []) or []},
                    "events": rows,
                    "last": rows[-1]["id"] if rows else since,
                    "since": since,
                    "total": db.agent_console_last(aid),
                    "project": {"id": p["id"], "name": p["name"]},
                })

            evs = db.agent_events(aid, 200)
            msgs = db.agent_msgs(aid, 60)
            # ники вместо id: иначе в UI «сообщение от другого агента» без имени
            agents = {x["id"]: x["nick"] for x in db.list_agents(a["project_id"])}
            for m in msgs:
                m["from_nick"] = agents.get(m["from_agent"], m["from_agent"])
                m["to_nick"] = agents.get(m["to_agent"], m["to_agent"])
            return self.json({"ok": True, "agent": a,
                              "events": evs, "messages": msgs,
                              "project": {"id": p["id"], "name": p["name"], "goal": p["goal"]}})

        # ---- workspace ----
        if parts[0] == "workspace" and len(parts) >= 2:
            pid = parts[1]
            if not db.valid_project_id(pid):
                return self.err("проект не найден", 404)
            p = db.get_project(pid)
            if not p or p["user_id"] != user["id"]:
                return self.err("нет доступа", 403)
            if len(parts) == 3 and parts[2] == "tree":
                aid = (query.get("agent") or [""])[0] or None
                if aid and not self.agent_of_project(aid, pid):
                    return self.err("агент не найден", 404)
                return self.json({"ok": True, "agent": aid or "",
                                  "files": tools.tree(pid, agent_id=aid)})
            if len(parts) == 3 and parts[2] == "file" and method == "GET":
                rel = (query.get("path") or [""])[0]
                aid = (query.get("agent") or [""])[0]
                if aid and not self.agent_of_project(aid, pid):
                    return self.err("агент не найден", 404)
                try:
                    info = tools.read_file_full(pid, rel, agent_id=aid or None)
                    st = os.stat(tools.resolve(pid, rel, aid or None))
                except Exception as e:  # noqa: BLE001
                    return self.err("не прочитать: %s" % e, 404)
                return self.json({"ok": True, "path": rel, "content": info["content"],
                                  "size": info["size"], "truncated": info["truncated"],
                                  "binary": info["binary"], "mtime": int(st.st_mtime)})
            if len(parts) == 3 and parts[2] == "file" and method == "PUT":
                body = self.body_json()
                rel = body.get("path") or (query.get("path") or [""])[0]
                content = body.get("content") or ""
                aid = body.get("agent") or (query.get("agent") or [""])[0] or None
                if aid and not self.agent_of_project(aid, pid):
                    return self.err("агент не найден", 404)
                r = tools.write_file(pid, rel, content, aid)
                orchestrator.emit(pid, None, "file", rel, {"path": rel, "action": "save"})
                return self.json({"ok": True, "file": r})
            if len(parts) == 3 and parts[2] == "delete" and method == "POST":
                body = self.body_json()
                tools.delete_path(pid, body.get("path") or "")
                return self.json({"ok": True})

        if parts[0] == "stats":
            return self.json({"ok": True, "stats": db.stats(),
                              "sse_clients": bus.counts(),
                              "runtimes": len(MANAGER.runtimes)})

        return self.err("неизвестный маршрут: %s" % path, 404)


def serve(host="127.0.0.1", port=8777, resume=True):
    db.connect()
    allow_hosts(host, os.environ.get("MS_ALLOW_HOSTS", ""))
    httpd = ThreadingHTTPServer((host, port), Handler)
    db.write_port_file(httpd.server_address[1])  # записываем только после успешного bind
    httpd.daemon_threads = True
    ide_build.reconcile()
    print("MRC Agent: http://%s:%d" % (host, port), flush=True)

    # после перезапуска: чиним статусы и продолжаем незаконченные рои
    try:
        orchestrator.MANAGER.reconcile()
        if resume:
            def _later():
                time.sleep(0.8)
                pids = orchestrator.MANAGER.resume_all()
                if pids:
                    print("возобновил проектов: %d" % len(pids), flush=True)
            threading.Thread(target=_later, daemon=True).start()
    except Exception as e:  # noqa: BLE001
        print("не удалось восстановить состояние: %s" % e, flush=True)
    return httpd


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="server.py", description="MRC Agent — фоновый сервер")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--no-resume", action="store_true", help="не возобновлять проекты при старте")
    args = ap.parse_args(argv)

    httpd = serve(args.host, args.port, resume=not args.no_resume)
    s = db.get_settings()
    demo = not (s.get("api_key") and not s.get("demo_mode"))
    print("  данные:  %s" % db.DB_PATH, flush=True)
    print("  режим:   %s" % ("демо (нет ключа)" if demo else "Mercury 3"), flush=True)
    print("  стоп:    Ctrl+C", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостанавливаюсь", flush=True)


if __name__ == "__main__":
    main()

