#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp.py — подключение внешних серверов по MCP (Model Context Protocol).

MRC Agent выступает клиентом MCP: человек вписывает сервер в Настройки → MCP,
приложение подключается к нему и отдаёт его инструменты всем агентам роя.
Дальше агент может вызвать инструмент действием:

    ```action
    {"tool":"mcp","server":"filesystem","name":"read_file","args":{"path":"/tmp/a.txt"}}
    ```

Поддержаны оба транспорта, которые есть в спецификации:
  * stdio — сервер запускается как процесс, JSON-RPC идёт построчно в stdin/stdout
    (так работают почти все серверы: npx, uvx, python -m …);
  * HTTP — Streamable HTTP: один POST на запрос, ответ либо JSON, либо поток SSE
    (Mcp-Session-Id запоминается и уходит обратно, как требует спецификация).

Модуль намеренно без внешних пакетов: только стандартная библиотека, чтобы
сборка в .exe не тянула лишние зависимости.

Лимитов по умолчанию нет: timeout=0 значит «ждать сколько нужно», число
инструментов и размер ответа не режутся. Потолок задаётся только если человек
сам вписал timeout у сервера или лимит в настройках.
"""

import json
import os
import queue
import shlex
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mrc-agent", "version": "1.6.0"}

TRANSPORTS = ("stdio", "http")

MAX_TOOL_NAME = 96          # имя инструмента в промпте: длиннее модель путает
MAX_SERVERS = 0             # 0 = сколько угодно серверов


class McpError(Exception):
    """Ошибка MCP с человеческой причиной: её видно в ленте и в настройках."""

    def __init__(self, reason, code=None):
        super().__init__(reason)
        self.reason = reason
        self.code = code


# --------------------------------------------------------------------------- #
# Транспорт stdio: процесс + построчный JSON
# --------------------------------------------------------------------------- #

class StdioTransport:
    """
    Сервер MCP как подпроцесс.

    Почему потоки: у pipe нет таймаута. Читаем stdout в отдельном потоке и
    складываем строки в очередь — тогда ожидание ответа управляемое, а stderr
    можно показать человеку, если сервер ругается (это главный источник
    «подключил и не работает»).
    """

    kind = "stdio"

    def __init__(self, command, args=None, env=None, cwd=None):
        self.argv = _argv(command, args)
        if not self.argv:
            raise McpError("не задана команда запуска MCP-сервера")
        self.cwd = cwd or None
        self.env = dict(os.environ)
        for k, v in (env or {}).items():
            if str(k).strip():
                self.env[str(k)] = str(v)
        self.proc = None
        self._q = queue.Queue()
        self._tail = []
        self._tail_lock = threading.Lock()
        self._closed = False

    def start(self):
        if self.proc and self.proc.poll() is None:
            return self
        win = os.name == "nt"
        try:
            self.proc = subprocess.Popen(
                self.argv if win else self.argv,          # список, без shell: без инъекций
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.cwd, env=self.env,
                shell=win)                                # на Windows .cmd/.bat иначе не стартуют
        except FileNotFoundError:
            raise McpError("команда не найдена: %s (проверьте, что она в PATH)" % self.argv[0])
        except OSError as e:
            raise McpError("не смог запустить сервер: %s" % e)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def _read_stdout(self):
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._q.put(line)
        self._q.put(None)                                  # процесс закрыл stdout

    def _read_stderr(self):
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                with self._tail_lock:
                    self._tail.append(line)
                    del self._tail[:-40]

    def send(self, payload):
        if not self.proc or self.proc.poll() is not None:
            raise McpError("сервер MCP не запущен: %s" % self.stderr_tail())
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise McpError("сервер закрыл stdin: %s" % (e or self.stderr_tail()))

    def read(self, timeout=0):
        try:
            line = self._q.get(timeout=timeout if timeout and timeout > 0 else None)
        except queue.Empty:
            raise McpError("сервер не ответил за %s с" % _fmt_sec(timeout))
        if line is None:
            raise McpError("сервер завершился, не ответив. stderr: %s" % self.stderr_tail())
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            raise McpError("сервер прислал не JSON-RPC: %s" % line[:200])

    def stderr_tail(self):
        with self._tail_lock:
            return " | ".join(self._tail[-6:]) or "вывода нет"

    def close(self):
        self._closed = True
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.proc.kill()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Транспорт HTTP: Streamable HTTP (JSON или SSE в ответе)
# --------------------------------------------------------------------------- #

class HttpTransport:
    """
    Сервер MCP по сети.

    Streamable HTTP отвечает либо application/json, либо text/event-stream.
    Второй случай — поток: разбираем события SSE и берём то, у которого наш id.
    Заголовок Mcp-Session-Id сервер выдаёт при initialize — возвращаем его в
    каждом следующем запросе, иначе сервер считает нас чужим.
    """

    kind = "http"

    def __init__(self, url, headers=None, timeout=0):
        self.url = (url or "").strip()
        if not self.url.startswith(("http://", "https://")):
            raise McpError("адрес MCP-сервера должен начинаться с http:// или https://")
        self.headers = {str(k): str(v) for k, v in (headers or {}).items() if str(k).strip()}
        self.timeout = timeout or 0
        self.session_id = ""
        self._closed = False

    def start(self):
        return self

    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs = {"Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL_VERSION}
        hdrs.update(self.headers)
        if self.session_id:
            hdrs["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=body, headers=hdrs, method="POST")
        wait = self.timeout if self.timeout and self.timeout > 0 else None
        try:
            with urllib.request.urlopen(req, timeout=wait) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "text/event-stream" in ctype:
                    data_lines = []
                    for raw_line in resp:
                        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                        elif not line and data_lines:
                            msg = _try_json("\n".join(data_lines))
                            data_lines = []
                            if isinstance(msg, dict) and msg.get("id") == payload.get("id") and ("result" in msg or "error" in msg):
                                return msg
                    return None
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            if e.code == 401:
                raise McpError("сервер требует авторизацию (401) — добавьте заголовок Authorization")
            raise McpError("HTTP %s от MCP-сервера: %s" % (e.code, detail or e.reason))
        except urllib.error.URLError as e:
            raise McpError("не достучался до MCP-сервера: %s" % (e.reason or e))
        except (socket.timeout, TimeoutError):
            raise McpError("MCP-сервер не ответил за %s с" % _fmt_sec(self.timeout))
        text = raw.decode("utf-8", "replace")
        if "text/event-stream" in ctype:
            return self._from_sse(text, payload.get("id"))
        if not text.strip():
            return None                                    # 202 Accepted: ответа нет
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise McpError("MCP-сервер ответил не JSON: %s" % text[:200])

    @staticmethod
    def _from_sse(text, want_id):
        """Разбор потока SSE: берём data: с нашим id, иначе последнее с result/error."""
        found = None
        data_lines = []
        for line in text.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
                continue
            if line.strip() == "" and data_lines:
                msg = _try_json("\n".join(data_lines))
                data_lines = []
                if msg is None:
                    continue
                if want_id is not None and msg.get("id") == want_id:
                    return msg
                if msg.get("result") is not None or msg.get("error"):
                    found = msg
        if data_lines:
            msg = _try_json("\n".join(data_lines))
            if msg is not None and (msg.get("result") is not None or msg.get("error")):
                found = msg
        return found

    def send(self, payload):
        return self._post(payload)

    def read(self, timeout=0):
        return None

    def stderr_tail(self):
        return ""

    def close(self):
        self._closed = True
        # сессию HTTP-сервера закрываем деликатно: DELETE — по спецификации
        if not self.session_id:
            return
        try:
            headers = dict(self.headers)
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
            req = urllib.request.Request(self.url, method="DELETE", headers=headers)
            urllib.request.urlopen(req, timeout=2).close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Сессия: initialize → tools/list → tools/call
# --------------------------------------------------------------------------- #

class Session:
    """Одно подключение к серверу MCP. Живёт в пуле между задачами агентов."""

    def __init__(self, server):
        self.server = server
        self.key = str(server.get("id") or server.get("name") or server.get("url") or "")
        self.transport = None
        self.ready = False
        self.server_info = {}
        self.tools = []
        self.last_error = ""
        self.opened_at = 0.0
        self._counter = 0
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()

    # ---------------- служебное ----------------
    def _next_id(self):
        self._counter += 1
        return self._counter

    def _timeout(self):
        t = self.server.get("timeout")
        try:
            return max(0, float(t or 0))
        except (TypeError, ValueError):
            return 0

    def _make_transport(self):
        kind = (self.server.get("transport") or "stdio").lower()
        if kind == "stdio":
            return StdioTransport(self.server.get("command") or "",
                                  self.server.get("args") or [],
                                  self.server.get("env") or {},
                                  self.server.get("cwd") or None)
        if kind == "http":
            return HttpTransport(self.server.get("url") or "",
                                 self.server.get("headers") or {},
                                 self._timeout())
        raise McpError("неизвестный транспорт: %s (нужен stdio или http)" % kind)

    def _rpc(self, method, params=None, timeout=None, notify=False):
        """Запрос JSON-RPC 2.0 с разбором ошибок сервера в человеческий текст."""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = self._next_id()
        with self._lock:
            if self.transport is None:
                self.transport = self._make_transport().start()
            if notify:
                try:
                    self.transport.send(payload)
                except McpError:
                    pass                                   # уведомление не критично
                return {}
            if self.transport.kind == "http":
                msg = self.transport.send(payload)
            else:
                self.transport.send(payload)
                msg = None
                while msg is None:                          # пропускаем чужие события
                    got = self.transport.read(timeout if timeout is not None else self._timeout())
                    if got.get("id") == payload["id"] and ("result" in got or "error" in got):
                        msg = got
        if msg is None:
            raise McpError("MCP-сервер не вернул ответ на %s" % method)
        if msg.get("error"):
            err = msg["error"]
            text = err.get("message") if isinstance(err, dict) else str(err)
            raise McpError("сервер вернул ошибку: %s" % (text or "без описания"),
                           (err or {}).get("code") if isinstance(err, dict) else None)
        return msg.get("result") or {}

    def open(self):
        """initialize + notifications/initialized. Повторный вызов — no-op."""
        with self._open_lock:
            if self.ready:
                return self
            result = self._rpc("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "roots": {"listChanged": False}},
                "clientInfo": CLIENT_INFO,
            })
            self.server_info = result.get("serverInfo") or {}
            proto = result.get("protocolVersion") or PROTOCOL_VERSION
            self.server_info.setdefault("protocolVersion", proto)
            self._rpc("notifications/initialized", {}, notify=True)
            self.ready = True
            self.opened_at = time.time()
            return self

    def list_tools(self):
        """tools/list с обходом курсоров: сервер вправе отдавать список страницами."""
        self.open()
        tools, cursor, seen = [], "", set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._rpc("tools/list", params)
            for t in result.get("tools") or []:
                name = str(t.get("name") or "").strip()
                if not name:
                    continue
                tools.append({
                    "name": name,
                    "description": str(t.get("description") or "").strip(),
                    "input_schema": t.get("inputSchema") or t.get("input_schema") or {},
                })
            cursor = str(result.get("nextCursor") or "")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        self.tools = tools
        return tools

    def call(self, name, args=None, timeout=None):
        """tools/call → нормализованный результат (текст + структура)."""
        self.open()
        if not str(name or "").strip():
            raise McpError("не указано имя MCP-инструмента")
        result = self._rpc("tools/call", {"name": str(name),
                                          "arguments": args if isinstance(args, dict) else {}},
                           timeout=timeout)
        return normalize_result(result)

    def close(self):
        self.ready = False
        if self.transport:
            try:
                self.transport.close()
            except Exception:  # noqa: BLE001
                pass
        self.transport = None


def normalize_result(result):
    """Ответ tools/call приводим к одному виду: text, картинки, структура, ошибка."""
    result = result or {}
    parts, images = [], []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "image":
            images.append(item)
        elif kind == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or res.get("uri") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    structured = result.get("structuredContent")
    text = "\n".join(p for p in parts if p).strip()
    if structured:
        text = (text + "\n\n" if text else "") + json.dumps(structured, ensure_ascii=False, indent=1)
    if images:
        text = (text + "\n\n" if text else "") + "картинок: %d" % len(images)
    return {"ok": not bool(result.get("isError")),
            "text": text or "пустой ответ",
            "is_error": bool(result.get("isError")),
            "structured": structured or {},
            "images": images}


# --------------------------------------------------------------------------- #
# Пул подключений
# --------------------------------------------------------------------------- #

_POOL = {}
_POOL_LOCK = threading.RLock()


def _pool_key(server):
    return "%s|%s|%s|%s" % (server.get("id") or server.get("name") or "",
                            server.get("transport") or "stdio",
                            server.get("command") or "",
                            server.get("url") or "")


def get_session(server, fresh=False):
    """Подключение к серверу: берём из пула, при сбое — один честный перезапуск."""
    key = _pool_key(server)
    with _POOL_LOCK:
        if fresh:
            old = _POOL.pop(key, None)
            if old:
                old.close()
        sess = _POOL.get(key)
        if sess and sess.ready:
            return sess
        if sess:
            sess.close()
        sess = Session(server)
        _POOL[key] = sess
        return sess


def drop(server_or_key):
    """Забыть подключение (сервер удалили или поменяли команду)."""
    with _POOL_LOCK:
        if isinstance(server_or_key, str):
            keys = [key for key, sess in _POOL.items()
                    if key == server_or_key or sess.server.get("id") == server_or_key]
        else:
            keys = [_pool_key(server_or_key)]
        sessions = [_POOL.pop(key) for key in keys if key in _POOL]
    for sess in sessions:
        sess.close()


def close_all():
    with _POOL_LOCK:
        items = list(_POOL.values())
        _POOL.clear()
    for s in items:
        s.close()


def pool_size():
    with _POOL_LOCK:
        return len(_POOL)


def _with_restart(server, action):
    """Один повтор после перезапуска: серверы stdio часто умирают от первого сбоя."""
    sess = get_session(server)
    try:
        return action(sess)
    except McpError:
        sess = get_session(server, fresh=True)
        return action(sess)


# --------------------------------------------------------------------------- #
# Публичный API для остального приложения
# --------------------------------------------------------------------------- #

def list_tools(server, timeout=None):
    """
    Подключиться и вернуть инструменты сервера.
    Результат: {"ok": True, "tools": [...], "server": "имя"} или {"ok": False, "reason": "..."}
    """
    if timeout is not None:
        server = dict(server or {})
        server["timeout"] = timeout
    name = (server or {}).get("name") or (server or {}).get("url") or "MCP"
    try:
        tools = _with_restart(server, lambda s: s.list_tools())
        return {"ok": True, "server": name, "tools": tools,
                "info": get_session(server).server_info}
    except McpError as e:
        return {"ok": False, "server": name, "tools": [], "reason": e.reason}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "server": name, "tools": [],
                "reason": "%s: %s" % (type(e).__name__, e)}


def call_tool(server, name, args=None, timeout=None):
    """Вызвать инструмент сервера. Всегда возвращает dict, не бросает наружу."""
    try:
        return get_session(server).call(name, args, timeout)
    except McpError as e:
        return {"ok": False, "text": "MCP: %s" % e.reason, "reason": e.reason}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "MCP: %s: %s" % (type(e).__name__, e),
                "reason": str(e)}


def ping(server):
    """Проверка связи без запроса инструментов — самый дешёвый способ «жив ли»."""
    try:
        sess = _with_restart(server, lambda s: (s.open(), s)[1])
        return {"ok": True, "info": sess.server_info}
    except McpError as e:
        return {"ok": False, "reason": e.reason}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}


# --------------------------------------------------------------------------- #
# Промпт агентов
# --------------------------------------------------------------------------- #

def prompt_block(servers, cache=None):
    """
    Текст для системного промпта: какие серверы подключены и что они умеют.

    Только включённые серверы. Схему аргументов даём компактно — списком ключей:
    полный JSON Schema в промпте на 100 агентов слишком дорог, а ключи модель
    понимает и сама заполняет по описанию инструмента.
    """
    cache = cache or {}
    lines = []
    for s in servers or []:
        if not int(s.get("enabled", 1)):
            continue
        tools = cache.get(s["id"]) or []
        if not tools:
            lines.append("- %s: инструментов нет (сервер ещё не проверяли)" % s["name"])
            continue
        lines.append("- %s (сервер «%s»):" % (s["name"], s.get("transport") or "stdio"))
        for t in tools:
            keys = list((t.get("input_schema") or {}).get("properties") or {}.keys())
            desc = str(t.get("description") or "").strip()
            lines.append("    · %s(%s)%s" % (t["name"], ", ".join(keys),
                                            (" — " + desc) if desc else ""))
    if not lines:
        return ""
    return ("MCP-ИНСТРУМЕНТЫ (внешние серверы, подключённые пользователем).\n"
            "Вызов — таким же блоком action:\n"
            '```action\n{"tool":"mcp","server":"<сервер>","name":"<инструмент>",'
            '"args":{...}}\n```\n'
            "server — имя сервера из списка ниже, name — имя инструмента, args — аргументы "
            "по названиям ключей. Ответ инструмента приходит текстом, его можно читать и "
            "использовать в работе.\n" + "\n".join(lines) + "\n")


def _one_line(text, limit=140):
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _argv(command, args):
    """Команда сервера → список аргументов. Строка режется по правилам shell, но без shell."""
    cmd = (command or "").strip()
    if not cmd:
        return []
    parts = shlex.split(cmd, posix=(os.name != "nt"))
    if os.name == "nt":
        parts = [p.strip('"') for p in parts]
    for a in args or []:
        parts.append(str(a))
    return parts


def _try_json(text):
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _fmt_sec(value):
    try:
        return ("%.0f" % float(value)).replace(".0", "")
    except (TypeError, ValueError):
        return "?"


# --------------------------------------------------------------------------- #
# Разбор того, что человек вписал в настройках
# --------------------------------------------------------------------------- #

def clean_server(name="", transport="stdio", command="", args=None, url="",
                 headers=None, env=None, timeout=0, enabled=1):
    """
    Нормализация сервера из настроек. Ошибки — ValueError с понятной причиной:
    их показываем прямо под полем, а не «500 internal error».
    """
    nm = " ".join(str(name or "").split()).strip()
    if not nm:
        raise ValueError("у сервера должно быть имя — по нему агенты его называют")
    kind = str(transport or "stdio").strip().lower()
    if kind not in TRANSPORTS:
        raise ValueError("транспорт бывает stdio или http")
    out = {"name": nm, "transport": kind,
           "timeout": _int_or_zero(timeout), "enabled": 1 if int(enabled or 0) else 0,
           "args": [str(a) for a in _as_list(args)],
           "headers": _as_dict(headers), "env": _as_dict(env),
           "command": "", "url": ""}
    if kind == "stdio":
        cmd = " ".join(str(command or "").split()).strip()
        if not cmd:
            raise ValueError("для stdio нужна команда запуска, например: npx -y @modelcontextprotocol/server-filesystem /tmp")
        out["command"] = cmd
        if not _argv(cmd, out["args"]):
            raise ValueError("команду не разобрать — проверьте кавычки")
    else:
        link = str(url or "").strip()
        if not link.startswith(("http://", "https://")):
            raise ValueError("адрес http-сервера начинается с http:// или https://")
        out["url"] = link
    if isinstance(args, str) and args.strip():
        out["args"] = shlex.split(args, posix=(os.name != "nt"))
    return out


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return shlex.split(value, posix=(os.name != "nt"))
    return list(value)


def _as_dict(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items() if str(k).strip()}
    out = {}
    for line in str(value).replace(";", "\n").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


def _int_or_zero(value):
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0
