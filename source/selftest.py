#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py — проверка ключевых мест без интернета и без ключа модели.

Запуск:  python3 selftest.py      (Windows: py -3 selftest.py)

Что проверяется:
  * личный workspace у каждого агента и слияние работ главным агентом;
  * ники по задаче («Ищущий информацию: сайт про продукцию»);
  * отсутствие лимита действий (5 кругов подряд);
  * защита от зацикливания: повтор действия и «нет прогресса в файлах»;
  * сообщения между агентами, план планировщика, реальный usage и стоимость;
  * прочее из списка багов: битые бинарники, большие файлы, таймауты, пауза, сессии;
  * 1.3.0: обход путей, подписанный preview, настройки только для своих, Host/Origin,
    честный README, конфликты по ролям, все действия за раунд, read_shared,
    промежуточное слияние, причины поиска, health без лишнего, вход по IP, экранирование.
  * 1.4.2: вложения любых форматов — имена без путей, текст из txt/docx/xlsx/pdf,
    загрузка по HTTP, переезд в workspace проекта, скачивание, удаление, лимит размера,
    чужие файлы закрыты, агент видит вложения и умеет attached(name).
  * 1.4.0: консоль агента (полный вывод команд, догрузка по since, доступ только владельцу,
    .txt на скачивание) и почта (поиск/чтение/отправка на локальном IMAP+SMTP-стенде,
    русские запросы, непрочитанные, выключение отправки, причины ошибок).
Мок-модель поднимается на 127.0.0.1:8851 и умеет режимы normal / loop / stall / multi / many.
"""
import json
import os
import shutil
import subprocess
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path  # noqa: E402

os.environ.setdefault("MS_DATA", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class Mock(BaseHTTPRequestHandler):
    mode = "normal"          # normal | loop | stall | multi | many
    delay = 0.0              # искусственная пауза ответа (для тестов слияния по ходу дела)
    calls = {"n": 0, "truncated": 0}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Mock.calls["n"] += 1
        if Mock.delay:
            time.sleep(Mock.delay)
        msgs = body.get("messages") or []
        sys_txt = (msgs[0].get("content") or "").lower()
        assistant_turns = sum(1 for m in msgs if m.get("role") == "assistant")
        mode = Mock.mode
        truncated = False

        if "главный агент" in sys_txt:
            text = "Проверил общий workspace: всё на месте."
        elif mode == "loop":
            text = 'Пишу.\n```action\n{"tool":"write_file","path":"a.txt","content":"одно и то же"}\n```'
        elif mode == "stall":
            text = 'Смотрю.\n```action\n{"tool":"read_file","path":"a.txt"}\n```'
        elif mode == "many":
            # один ответ — 8 действий сразу: проверяем, что потолка в 6 больше нет
            if assistant_turns < 1:
                text = "Пишу сразу восемь частей.\n" + "\n".join(
                    '```action\n{"tool":"write_file","path":"много-%d.txt","content":"часть %d"}\n```'
                    % (i, i) for i in range(1, 9))
            else:
                text = "Готово: восемь частей за один ответ."
        elif mode == "shell":
            # длинный вывод команды: в ленте он обрезан, в консоли — целиком
            if assistant_turns < 1:
                text = ('Запускаю длинную команду.\n```action\n'
                        '{"tool":"run","cmd":"for i in $(seq 1 200); do echo file-$i; done"}\n```')
            else:
                text = "Команда отработала, смотрю вывод."
        elif mode == "mail":
            if assistant_turns < 1:
                text = ('Проверяю почту.\n```action\n'
                        '{"tool":"mail_search","query":"","days":30,"limit":5}\n```')
            else:
                text = "Почту проверил, письма вижу."
        elif mode == "multi":
            if assistant_turns < 5:
                i = assistant_turns + 1
                text = ('Пишу часть %d.\n```action\n{"tool":"write_file","path":"часть-%d.txt",'
                        '"content":"блок %d"}\n```' % (i, i, i))
            else:
                text = "Готово: пять частей."
        else:
            truncated = Mock.calls["truncated"] == 0
            if truncated:
                Mock.calls["truncated"] += 1
            text = ("План: 1) index.html 2) styles.css 3) app.js" if "планировщик" in sys_txt
                    else "Беру свою часть работы, пишу файл.")
            if truncated:
                text = text[:12]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(obj):
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
            self.wfile.flush()

        send({"model": "mock", "choices": [{"delta": {"content": text}}]})
        send({"model": "mock", "choices": [{"delta": {},
              "finish_reason": "length" if truncated else "stop"}]})
        send({"model": "mock", "choices": [], "usage": {"prompt_tokens": 700, "completion_tokens": 120}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def start_mock(port=8851):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Mock)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


import db
import llm
import mailbox
import orchestrator
import tools

RESULT = []


# --------------------------------------------------------------------------- #
# стенд: настоящий сервер на свободном порту (для блока A)
# --------------------------------------------------------------------------- #

import socket
import urllib.error
import urllib.parse
import urllib.request


BASE_DIR = Path(__file__).resolve().parent


def _searx_fixture():
    """Локальный «SearXNG» и локальная капча: настоящий интернет тестам не нужен."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if "/searx" in self.path:
                body = json.dumps({"results": [
                    {"title": "Свой поиск", "url": "https://example.org/a", "content": "текст"}]}).encode()
                ctype = "application/json"
            else:
                body = b"<html><body>captcha challenge</body></html>"
                ctype = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def _sse_probe(stand, cookie, own_pid, foreign_pid, wait=2.5):
    """Живой поток: своё событие должно прийти, чужое — нет."""
    import bus as _bus
    sock = socket.create_connection(("127.0.0.1", stand.port), timeout=5)
    try:
        sock.sendall(("GET /api/stream?project=%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                      "Cookie: %s\r\nAccept: text/event-stream\r\n\r\n"
                      % (own_pid, stand.port, cookie)).encode())
        sock.settimeout(0.4)
        time.sleep(0.5)
        _bus.publish("project:" + own_pid, {"type": "log", "text": "СВОЁ-СОБЫТИЕ", "project": own_pid})
        _bus.publish("project:" + foreign_pid, {"type": "log", "text": "ЧУЖОЕ-СОБЫТИЕ",
                                                "project": foreign_pid})
        buf = b""
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                continue
        text = buf.decode("utf-8", "replace")
        return {"own": "СВОЁ-СОБЫТИЕ" in text, "foreign": "ЧУЖОЕ-СОБЫТИЕ" in text}
    finally:
        sock.close()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Stand:
    """Живой сервер + запросы с cookie и произвольными заголовками."""

    def __init__(self):
        import server
        self.server = server
        self.port = free_port()
        self.httpd = server.serve("127.0.0.1", self.port, resume=False)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.5)
        self.base = "http://127.0.0.1:%d" % self.port

    def url(self, path):
        return self.base + path

    def req(self, path, method="GET", body=None, cookie=None,
            ctype="application/json", headers=None):
        data = None
        if body is not None:
            data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        r = urllib.request.Request(self.url(path), data=data, method=method)
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        if data is not None:
            r.add_header("Content-Type", ctype)
        if cookie:
            r.add_header("Cookie", cookie)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
        except urllib.error.URLError as e:
            # сервер закрыл соединение, не ответив (например, тело не влезло в лимит)
            return 0, b"", {"x-error": str(e)}

    def json(self, path, **kw):
        st, body, hdrs = self.req(path, **kw)
        try:
            return st, json.loads(body.decode("utf-8")), hdrs
        except Exception:  # noqa: BLE001
            return st, {}, hdrs

    def register(self, nick, password="secret123"):
        st, data, hdrs = self.req("/api/auth/register", "POST",
                                  {"name": nick, "password": password})
        cookie = (hdrs.get("Set-Cookie") or "").split(";")[0]
        return st, cookie, data

    def stop(self):
        try:
            self.httpd.shutdown()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# почта: локальный IMAP и SMTP — настоящий интернет тестам не нужен
# --------------------------------------------------------------------------- #

import socketserver
from email.header import Header
from email.message import EmailMessage
from email.utils import formatdate


class MailFixture:
    """Крошечный IMAP + SMTP на localhost. Письма и отправленное — в памяти."""

    def __init__(self):
        self.messages = []          # [(uid, flags, raw bytes)]
        self.sent = []              # то, что «отправили» агенты
        self.user, self.password = "agent@example.org", "app-password-42"
        self._add("Здравствуйте! Ваш счёт за ноябрь: 1 234 ₽. Оплатить до 30 числа.",
                  "Счёт за ноябрь", "Банк", "bank@example.org", seen=True, days=1)
        self._add("Отчёт во вложении, всё по плану. Созвонимся в среду.",
                  "Отчёт по проекту", "Иван", "ivan@example.org", seen=False, days=3)
        self.imap_port = free_port()
        self.smtp_port = free_port()
        self._start_imap()
        self._start_smtp()

    # ---- письма ----
    def _add(self, body, subject, name, addr, seen=True, days=0):
        msg = EmailMessage()
        # имя в заголовке кодируем как настоящий сервер: =?utf-8?b?...?=
        msg["From"] = "%s <%s>" % (Header(name, "utf-8").encode(), addr)
        msg["To"] = "agent@example.org"
        msg["Subject"] = Header(subject, "utf-8").encode()
        msg["Date"] = formatdate(time.time() - days * 86400, localtime=True)
        msg.set_content(body)
        self.messages.append((len(self.messages) + 1, "\\Seen" if seen else "", bytes(msg)))

    def _match(self, raw, crit):
        msg = __import__("email").message_from_bytes(raw)
        body = (msg.get_payload(decode=True) or b"").decode("utf-8", "replace")
        low = (" ".join([str(msg.get("From") or ""), str(msg.get("Subject") or ""), body])).lower()
        for word in crit.get("words") or []:
            if word.lower() not in low:
                return False
        return True

    # ---- IMAP ----
    def _start_imap(self):
        fx = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"* OK [CAPABILITY IMAP4rev1] fake imap ready\r\n")
                authed = False
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", "replace").rstrip("\r\n")
                    if not text.strip():
                        continue
                    parts = text.split(" ", 2)
                    tag = parts[0]
                    cmd = (parts[1] if len(parts) > 1 else "").upper()
                    rest = parts[2] if len(parts) > 2 else ""
                    if cmd == "CAPABILITY":
                        self.wfile.write(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n")
                        self._ok(tag, "CAPABILITY")
                    elif cmd == "LOGIN":
                        bits = rest.replace('"', "").split()
                        if len(bits) >= 2 and bits[0] == fx.user and bits[1] == fx.password:
                            authed = True
                            self._ok(tag, "LOGIN")
                        else:
                            self.wfile.write(b"%s NO [AUTHENTICATIONFAILED] bad credentials\r\n"
                                             % tag.encode())
                    elif cmd in ("NOOP", "CLOSE", "CHECK"):
                        self._ok(tag, cmd)
                    elif cmd == "SELECT":
                        if not authed:
                            self.wfile.write(b"%s NO not logged in\r\n" % tag.encode())
                            continue
                        self.wfile.write(b"* %d EXISTS\r\n* OK [UIDVALIDITY 1] fake\r\n"
                                         % len(fx.messages))
                        self._ok(tag, "SELECT")
                    elif cmd == "LIST":
                        if not authed:
                            self.wfile.write(b"%s NO not logged in\r\n" % tag.encode())
                            continue
                        self.wfile.write(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
                        self.wfile.write(b'* LIST (\\HasNoChildren) "/" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"\r\n')
                        self._ok(tag, "LIST")
                    elif cmd == "UID":
                        if not authed:
                            self.wfile.write(b"%s NO not logged in\r\n" % tag.encode())
                            continue
                        sub = rest.split(" ", 1)
                        what = sub[0].upper()
                        arg = sub[1] if len(sub) > 1 else ""
                        if what == "SEARCH":
                            uids = fx._search(arg)
                            self.wfile.write(b"* SEARCH " + " ".join(str(u) for u in uids).encode()
                                             + b"\r\n")
                            self._ok(tag, "SEARCH")
                        elif what == "FETCH":
                            fx._fetch(self, arg)
                            self._ok(tag, "FETCH")
                        else:
                            self._ok(tag, what)
                    elif cmd == "LOGOUT":
                        self.wfile.write(b"* BYE fake imap bye\r\n")
                        self._ok(tag, "LOGOUT")
                        return
                    else:
                        self._ok(tag, cmd)

            def _ok(self, tag, cmd):
                self.wfile.write(("%s OK %s completed\r\n" % (tag, cmd)).encode())

        class S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._imap = S(("127.0.0.1", self.imap_port), H)
        threading.Thread(target=self._imap.serve_forever, daemon=True).start()

    def _search(self, arg):
        tokens = re.findall(r'"(?:[^"\\]|\\.)*"|\S+', arg)
        crit = {"unseen": False, "words": []}
        skip = False
        for t in tokens:
            up = t.upper()
            if skip:
                skip = False
                continue
            if up in ("CHARSET",):
                skip = True
                continue
            if up == "UNSEEN":
                crit["unseen"] = True
            elif up in ("TEXT", "FROM", "SUBJECT", "BODY"):
                continue
            elif t.startswith('"'):
                crit["words"].append(t.strip('"').replace('\\"', '"'))
        out = []
        for uid, flags, raw in self.messages:
            if crit["unseen"] and "\\Seen" in flags:
                continue
            if crit["words"] and not self._match(raw, crit):
                continue
            out.append(uid)
        return out

    def _fetch(self, h, arg):
        bits = arg.split(" ", 1)
        ids = [int(x) for x in bits[0].split(",") if x.strip().isdigit()]
        want = bits[1] if len(bits) > 1 else ""
        for uid, flags, raw in self.messages:
            if uid not in ids:
                continue
            msg = __import__("email").message_from_bytes(raw)
            if "HEADER.FIELDS" in want.upper():
                head = b""
                for field in ("From", "To", "Subject", "Date"):
                    if msg.get(field):
                        head += ("%s: %s\r\n" % (field, msg.get(field))).encode("utf-8")
                head += b"\r\n"
                item = "BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]"
            else:
                head = raw
                item = "BODY[]"
            h.wfile.write(("* %d FETCH (UID %d FLAGS (%s) RFC822.SIZE %d %s {%d}\r\n"
                           % (uid, uid, flags or "", len(raw), item, len(head))).encode())
            h.wfile.write(head)
            h.wfile.write(b")\r\n")

    # ---- SMTP ----
    def _start_smtp(self):
        fx = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 fake smtp ready\r\n")
                rcpt = []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", "replace").rstrip("\r\n")
                    up = text.upper()
                    if up.startswith(("EHLO", "HELO")):
                        self.wfile.write(b"250-fake\r\n250 AUTH PLAIN LOGIN\r\n")
                    elif up.startswith("AUTH"):
                        self.wfile.write(b"235 2.7.0 authentication successful\r\n")
                    elif up.startswith("MAIL FROM"):
                        d = text.find(":")
                        sender = text[d + 1:].strip().strip("<>")
                        if "wrong" in sender:
                            self.wfile.write(b"550 5.1.0 sender rejected\r\n")
                            continue
                        self.wfile.write(b"250 2.1.0 ok\r\n")
                    elif up.startswith("RCPT TO"):
                        d = text.find(":")
                        rcpt.append(text[d + 1:].strip().strip("<>"))
                        self.wfile.write(b"250 2.1.5 ok\r\n")
                    elif up.startswith("DATA"):
                        self.wfile.write(b"354 end with . on its own line\r\n")
                        chunks = []
                        while True:
                            row = self.rfile.readline()
                            if not row or row in (b".\r\n", b".\n"):
                                break
                            chunks.append(row)
                        raw = b"".join(chunks).replace(b"\r\n..", b"\r\n.")
                        parsed = __import__("email").message_from_bytes(raw)
                        body = parsed.get_payload(decode=True) or b""
                        fx.sent.append({
                            "to": rcpt[:], "subject": str(parsed.get("Subject") or ""),
                            "from": str(parsed.get("From") or ""),
                            "body": body.decode("utf-8", "replace"),
                            "raw": raw,
                        })
                        rcpt = []
                        self.wfile.write(b"250 2.0.0 queued as FAKE1\r\n")
                    elif up.startswith("QUIT"):
                        self.wfile.write(b"221 bye\r\n")
                        return
                    elif up.startswith("RSET"):
                        self.wfile.write(b"250 ok\r\n")
                    else:
                        self.wfile.write(b"250 ok\r\n")

        class S(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._smtp = S(("127.0.0.1", self.smtp_port), H)
        threading.Thread(target=self._smtp.serve_forever, daemon=True).start()

    def settings(self, **extra):
        s = {"mail_imap_host": "127.0.0.1", "mail_imap_port": self.imap_port,
             "mail_imap_enc": "none", "mail_smtp_host": "127.0.0.1",
             "mail_smtp_port": self.smtp_port, "mail_smtp_enc": "none",
             "mail_user": self.user, "mail_password": self.password,
             "mail_from": "agent@example.org", "mail_folder": "INBOX",
             "mail_allow_send": 1, "mail_timeout": 5}
        s.update(extra)
        return s

    def stop(self):
        for srv in (getattr(self, "_imap", None), getattr(self, "_smtp", None)):
            try:
                srv.shutdown()
            except Exception:  # noqa: BLE001
                pass


def check(name, cond, extra=""):
    RESULT.append((name, bool(cond)))
    print(("  OK  " if cond else "  ПРОВАЛ") + "  " + name + ("  — " + str(extra) if extra else ""))


def run_project(uid, goal, agents, seconds=120, wait_stop=True):
    pid = db.create_project(uid, "T", goal, agents_total=agents)["id"]
    orchestrator.MANAGER.ensure_agents(pid, agents)
    orchestrator.MANAGER.start(pid, uid)
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(0.4)
        if not orchestrator.MANAGER.running(pid):
            break
    return pid


def main():
    start_mock(8851)
    db.connect()

    # ---------------- 1. ники по задаче ----------------
    topic = orchestrator.topic_from_goal("Сделай крутой сайт про продукцию этой компании и тд")
    check("ники: тема вытащена из цели", topic == "сайт про продукцию", topic)
    nick = orchestrator.nick_for("research", topic, 1)
    check("ники: по роли и задаче", nick == "Ищущий информацию: сайт про продукцию", nick)

    # ---------------- 2. личный workspace у каждого ----------------
    db.set_settings({"demo_mode": 1, "agents": 12, "concurrency": 5, "resume_on_start": 0, "api_key": ""})
    u = db.upsert_user("self@t.t", "Тест", provider="local")
    uid = u["id"]
    pid, uid = u["id"], u["id"]
    pid = run_project(uid, "Сделай крутой сайт про продукцию этой компании и тд", 12)
    agents = db.list_agents(pid)
    sand_boxes = tools.agent_workspaces(pid)
    check("workspace: песочница у каждого агента", len(sand_boxes) == len(agents),
          "%d песочниц на %d агентов" % (len(sand_boxes), len(agents)))
    with_work = [aid for aid, _ in sand_boxes if tools.tree(pid, agent_id=aid)]
    check("workspace: агенты реально пишут в свои песочницы", len(with_work) >= len(agents) - 1,
          "%d из %d" % (len(with_work), len(agents)))
    project_files = [f["path"] for f in tools.tree(pid)]
    check("workspace: песочницы не протекают в общий вид", not any(p.startswith("agents/") for p in project_files))

    # изоляция: одинаковое имя файла в двух песочницах не мешает
    tools.write_file(pid, "общий.txt", "версия А", with_work[0])
    tools.write_file(pid, "общий.txt", "версия Б", with_work[1])
    a = tools.read_file(pid, "общий.txt", agent_id=with_work[0])
    b = tools.read_file(pid, "общий.txt", agent_id=with_work[1])
    check("workspace: изоляция агентов", a == "версия А" and b == "версия Б")

    # ---------------- 3. главный агент сводит всё вместе ----------------
    check("главный: есть в рое", any(x["lane"] == "main" for x in agents))
    merged = [p for p in project_files if p.startswith("sections/") or p.startswith("styles/")]
    check("главный: работы агентов слились в проект", bool(merged), "%d файлов" % len(merged))
    index = tools.read_file(pid, "index.html")
    check("главный: собрал index.html из секций",
          "<section" in index and "<style>" in index and "styles/" in index,
          "%d секций" % index.count("<section"))
    heads = re.findall(r"<h2>(.*?)</h2>", index, re.S)
    check("главный: чужие стили вшиты, одинаковых блоков нет",
          index.count("/* styles/") >= 1 and len(heads) == len(set(h.strip().lower() for h in heads)),
          "%d заголовков" % len(heads))
    check("главный: положил отчёт", "ОТЧЁТ-СБОРКИ.md" in project_files)
    ev = db.events_since(pid, 0, limit=5000)
    check("главный: есть события merge и review",
          any(e["type"] == "merge" for e in ev) and any(e["type"] == "review" for e in ev))
    check("сообщения: агенты докладывают главному",
          any(e["type"] == "message" for e in ev) and len(db.agent_msgs(
              [x["id"] for x in agents if x["lane"] == "main"][0])) > 0)

    # ---------------- 4. боевой режим: без лимита действий + защита ----------------
    db.set_settings({"api_key": "sk", "base_url": "http://127.0.0.1:8851", "demo_mode": 0,
                     "model": "mock", "agents": 3, "concurrency": 2, "stall_rounds": 3,
                     "agent_timeout": 30, "max_rounds": 0, "loop_guard": 1})

    Mock.mode = "multi"
    Mock.calls.update({"n": 0, "truncated": 0})
    pid2 = run_project(uid, "проверка лимита действий", 3)
    parts = [f["path"] for aid, _ in tools.agent_workspaces(pid2) for f in tools.tree(pid2, agent_id=aid)]
    check("лимит действий снят: 5 кругов подряд", len([p for p in parts if p.startswith("часть-")]) >= 5,
          "%d файлов" % len(parts))

    Mock.mode = "loop"
    pid3 = run_project(uid, "проверка зацикливания", 3)
    logs = [e["text"] for e in db.events_since(pid3, 0, limit=4000) if e["type"] == "log"]
    check("защита: повтор действия останавливает агента",
          any("повторяется" in t for t in logs))
    check("защита: кругов конечное число", Mock.calls["n"] < 60, "%d вызовов" % Mock.calls["n"])

    Mock.mode = "stall"
    pid4 = run_project(uid, "проверка застоя", 3)
    logs4 = [e["text"] for e in db.events_since(pid4, 0, limit=4000) if e["type"] == "log"]
    check("защита: нет прогресса в файлах → стоп", any("без изменений" in t for t in logs4))

    # ---------------- 5. обрыв ответа, план, usage, стоимость ----------------
    Mock.mode = "normal"
    Mock.calls.update({"n": 0, "truncated": 0})
    pid5 = run_project(uid, "лендинг с тарифами", 4)
    p5 = db.get_project(pid5)
    logs5 = [e["text"] for e in db.events_since(pid5, 0, limit=4000) if e["type"] == "log"]
    check("обрыв по лимиту: повтор, а не тишина", any("обрезан" in t for t in logs5))
    check("план планировщика сохранён", "План" in (p5["brief"] or ""), (p5["brief"] or "")[:28])
    check("usage и стоимость по факту", p5["tokens_in"] > 0 and p5["tokens_out"] > 0
          and llm.cost(p5["tokens_in"], p5["tokens_out"]) > 0,
          "%s/%s токенов" % (p5["tokens_in"], p5["tokens_out"]))
    check("token-события не пишутся в базу",
          not [e for e in db.events_since(pid5, 0, limit=5000) if e["type"] == "token"])

    # ---------------- 6. прочее из списка багов ----------------
    raw = bytes(range(256)) * 8
    (tools.workspace_for(pid) / "b.bin").write_bytes(raw)
    check("бинарные файлы: без порчи", tools.read_bytes(pid, "b.bin") == raw)
    # 1.5.0: лимит редактора поднят с 2 МБ до 32 МБ — 3 МБ теперь читается целиком
    (tools.workspace_for(pid) / "big.txt").write_text("x" * 3_000_000)
    check("файл на 3 МБ читается целиком (лимит поднят)", not tools.read_file_full(pid, "big.txt")["truncated"])
    (tools.workspace_for(pid) / "huge.txt").write_text("x" * 33_560_000)
    check("файл больше 32 МБ читается целиком", len(tools.read_file_full(pid, "huge.txt")["content"]) == 33_560_000 and not tools.read_file_full(pid, "huge.txt")["truncated"])
    t0 = time.time()
    r = tools.run_command(pid, "python3 -c \"import time; time.sleep(20)\"", timeout=2)
    check("таймаут команды снимает процессы", time.time() - t0 < 5 and "таймаут" in r["out"])
    (tools.workspace_for(pid) / "node_modules" / "x").mkdir(parents=True, exist_ok=True)
    check("node_modules не обходится", not any("node_modules" in f["path"] for f in tools.tree(pid)))

    db.update_project(pid5, status="paused", user_paused=1)
    db.set_settings({"resume_on_start": 1})
    check("ручная пауза не снимается сама", pid5 not in orchestrator.MANAGER.resume_all())
    tk = db.create_session(uid)
    db.drop_other_sessions(uid, keep_token=tk)
    check("смена пароля закрывает другие входы", db.user_by_session(tk) is not None)


    # ======================= 1.3.0: БЛОК A (посторонние) =======================
    st = Stand()
    try:
        # администратор = первый пользователь установки; сессию ему выдаём напрямую,
        # потому что «первым» в этой базе уже был создан пользователь выше по тесту
        first_uid = db.first_user_id()
        adm_cookie = "ms_session=" + db.create_session(first_uid)
        adm = db.user_by_session(adm_cookie.split("=", 1)[1])
        st_code2, us2_cookie, _ = st.register("Второй")
        us2 = db.user_by_session(us2_cookie.split("=", 1)[1])
        check("A1: два пользователя на стенде",
              bool(adm and us2) and adm["id"] != us2["id"] and adm["id"] == first_uid)

        pid_a = db.create_project(adm["id"], "Сайт", "сайт про продукцию", agents_total=2)["id"]
        tools.workspace_for(pid_a)
        tools.write_file(pid_a, "index.html", "<html><body>мой сайт</body></html>")
        tools.write_file(pid_a, "styles/all.css", "body{color:#111}")
        pid_b = db.create_project(us2["id"], "Чужой", "чужой проект", agents_total=1)["id"]

        # --- A1: обход путей ---
        leaked = []
        codes = []
        for path in ("/preview/%2e%2e/studio.db", "/preview/..%2fstudio.db",
                     "/preview/%2e%2e%2fstudio.db", "/download/%2e%2e/studio.db",
                     "/preview/%s/../studio.db" % pid_a,
                     "/download/%s/../../studio.db" % pid_a):
            code, body, _ = st.req(path)
            codes.append(code)
            if b"SQLite" in body:
                leaked.append(path)
        check("A1: обход путей не отдаёт базу", not leaked and all(c in (400, 401, 404) for c in codes),
              "коды %s" % codes)

        check("A1: preview без подписи закрыт",
              st.req("/preview/%s/index.html" % pid_a)[0] == 401
              and st.req("/preview/%s/index.html" % pid_a, cookie=adm_cookie)[0] == 401)

        code, tok_data, _ = st.json("/api/preview-token/%s" % pid_a, cookie=adm_cookie)
        token = tok_data.get("token") or ""
        check("A1: подпись выдаётся владельцу", code == 200 and "-" in token, code)
        code_own, body_own, hdrs_own = st.req("/preview/%s/%s/index.html" % (token, pid_a))
        code_css, body_css, _ = st.req("/preview/%s/%s/styles/all.css" % (token, pid_a))
        check("A1: по подписи открываются свои файлы и подресурсы",
              code_own == 200 and "мой сайт" in body_own.decode("utf-8", "replace")
              and code_css == 200 and b"color:#111" in body_css,
              "%s/%s" % (code_own, code_css))
        check("A1: подпись чужого проекта не подходит",
              st.req("/preview/%s/%s/index.html" % (token, pid_b))[0] in (401, 404))
        bad = [st.req("/preview/%s/%s/%s" % (token, pid_a, rel))[0]
               for rel in ("../studio.db", "%2e%2e/studio.db", "../../studio.db")]
        check("A1: с подписью обход пути тоже не работает", all(c in (400, 401, 404) for c in bad), bad)
        check("A1: подпись на несуществующий проект не выдаётся",
              st.req("/api/preview-token/%s" % db.rid("p_"), cookie=adm_cookie)[0] == 404)

        junk = db.rid("p_")[:-1] + "zz"
        try:
            tools.workspace_for(junk)
            raised = False
        except ValueError:
            raised = True
        check("A1: workspace_for не принимает мусорный id и не создаёт папок",
              raised and not (tools.WORKSPACES / junk).exists())
        try:
            tools.resolve(pid_a, "x.txt", agent_id="../../etc")
            raised2 = False
        except ValueError:
            raised2 = True
        check("A1: мусорный agent_id отсекается", raised2)

        # --- A2: настройки ---
        db.set_settings({"base_url": "https://api.inceptionlabs.ai/v1", "allow_terminal": 1})
        check("A2: настройки без входа → 401",
              st.req("/api/settings", "PUT", {"base_url": "http://зло"})[0] == 401)
        check("A2: настройки с text/plain → 415",
              st.req("/api/settings", "PUT", {"base_url": "http://зло"},
                     cookie=adm_cookie, ctype="text/plain")[0] == 415)
        st.req("/api/settings", "PUT", {"base_url": "http://зло.example", "allow_terminal": 0},
               cookie=us2_cookie)
        s_after = db.get_settings()
        check("A2: base_url и терминал меняет только админ",
              s_after["base_url"] == "https://api.inceptionlabs.ai/v1" and int(s_after["allow_terminal"]) == 1,
              s_after["base_url"])
        st.req("/api/settings", "PUT", {"model": "проверка-не-админ", "base_url": "http://зло2"},
               cookie=us2_cookie)
        check("A2: остальные поля не-админ менять может",
              db.get_settings()["model"] == "проверка-не-админ"
              and db.get_settings()["base_url"] == "https://api.inceptionlabs.ai/v1")
        st.req("/api/settings", "PUT", {"base_url": "http://127.0.0.1:9/v1", "model": "mock"},
               cookie=adm_cookie)
        check("A2: админ меняет base_url", db.get_settings()["base_url"] == "http://127.0.0.1:9/v1")
        db.set_settings({"base_url": "http://127.0.0.1:8851", "model": "mock"})

        # --- A3: поток и возобновление ---
        check("A3: /api/resume_all без входа → 401",
              st.req("/api/resume_all", "POST", {})[0] == 401)
        check("A3: /api/stream без входа → 401", st.req("/api/stream")[0] == 401)

        # --- A4: Host и Origin ---
        check("A4: чужой Host → 403", st.req("/", headers={"Host": "evil.example"})[0] == 403)
        check("A4: чужой Origin → 403",
              st.req("/api/auth/login", "POST", {"name": "Второй", "password": "secret123"},
                     headers={"Origin": "http://evil.example"})[0] == 403)
        code, _, hdrs = st.req("/api/auth/login", "POST", {"name": "Второй", "password": "secret123"},
                               headers={"Origin": st.base})
        setc = hdrs.get("Set-Cookie") or ""
        check("A4: свой Origin проходит, cookie SameSite=Strict",
              code == 200 and "SameSite=Strict" in setc, "%s %s" % (code, setc[-24:]))

        # SSE: чужие проекты не попадают в поток
        vis = st.server.sse_visible
        check("A3: SSE-фильтр по своим проектам",
              vis(adm, "project:" + pid_a) and not vis(adm, "project:" + pid_b)
              and not vis(None, "project:" + pid_a))

        # живая проверка: чужое событие в поток не приходит
        got = _sse_probe(st, us2_cookie, pid_b, pid_a)
        check("A3: чужой проект не приходит в SSE", got["foreign"] is False and got["own"] is True,
              "своё %s / чужое %s" % (got["own"], got["foreign"]))

        # --- N6: health ---
        code, health, _ = st.json("/api/health")
        check("N6: health без входа — только ok и version",
              code == 200 and set(health) == {"ok", "version"}, sorted(health))
        code, health2, _ = st.json("/api/health", headers={"X-MRC-Instance": db.instance_id()})
        check("N6: health с известным instance даёт данные (нужно app.py)",
              "data" in health2 and "pid" in health2)

        # --- N7: вход по IP с задержкой ---
        guard = st.server.LOGIN_GUARD
        saved_limit = guard.limit
        guard.limit = 2
        guard.fails.clear()
        for _ in range(3):
            st.req("/api/auth/login", "POST", {"name": "Второй", "password": "неверный"})
        delay = guard.delay_for(("127.0.0.1"))
        code_ok, _, hdrs_ok = st.req("/api/auth/login", "POST",
                                     {"name": "Второй", "password": "secret123"})
        check("N7: после промахов верный пароль всё равно пускает (не 429)",
              code_ok == 200 and delay > 0, "задержка %.1fс, код %s" % (delay, code_ok))
        check("N7: лимит считается по IP, а не по нику",
              "ip|" in guard._key("127.0.0.1") and "Второй" not in guard._key("127.0.0.1"))
        guard.fails.clear()
        guard.limit = saved_limit

        # --- N5: поиск ---
        searx_srv = _searx_fixture()
        saved = tools.SEARCH_PROVIDERS
        tools.SEARCH_PROVIDERS = [("DuckDuckGo", "http://127.0.0.1:%d/captcha?q=%%s" % searx_srv[1],
                                   tools._parse_ddg)]
        res_captcha = tools.search_web("проверка")
        check("N5: причина вместо пустого [] (капча)",
              res_captcha["ok"] is False and "капча" in (res_captcha.get("text") or ""),
              res_captcha.get("text", "")[:60])
        res_searx = tools.search_web("проверка", searx="http://127.0.0.1:%d/searx" % searx_srv[1])
        check("N5: свой SearXNG отдаёт результаты",
              res_searx["ok"] and res_searx["provider"] == "searxng"
              and res_searx["results"][0]["url"].startswith("http"), res_searx.get("provider"))
        tools.SEARCH_PROVIDERS = saved
        searx_srv[0].shutdown()

        # --- N1: README честен, доступ агентов не урезан ---
        readme = (BASE_DIR / "README.md").read_text(encoding="utf-8")
        check("N1: README не обещает изоляцию, а говорит правду",
              "полный доступ" in readme and "ничего не стоит" not in readme)

    finally:
        st.stop()


    # ======================= 1.3.0: БЛОК N =======================
    # --- N2: конфликты по ролям, проигравшие в _conflicts ---
    pid_n2 = db.create_project(uid, "Конфликт", "конфликт файлов", agents_total=2)["id"]
    a_code = db.add_agent(pid_n2, 1, "Пишущий код: конфликт", "Программист", "code")
    a_res = db.add_agent(pid_n2, 2, "Ищущий информацию: конфликт", "Разведчик", "research")
    tools.workspace_for(pid_n2)
    tools.workspace_for(pid_n2, a_code)
    tools.workspace_for(pid_n2, a_res)
    tools.write_file(pid_n2, "index.html", "<html>версия кода</html>", a_code)
    tools.write_file(pid_n2, "index.html", "<html>версия разведки</html>", a_res)
    agents_n2 = db.list_agents(pid_n2)
    nicks_n2 = {a["id"]: a["nick"] for a in agents_n2}
    rep_n2 = tools.merge_workspaces(pid_n2, nicks_n2, agents=agents_n2)
    got_n2 = [f["path"] for f in tools.tree(pid_n2)]
    winner_n2 = tools.read_file(pid_n2, "index.html")
    check("N2: при конфликте выигрывает код", "версия кода" in winner_n2, winner_n2[:40])
    losers_n2 = [p for p in got_n2 if p.startswith("_conflicts/")]
    check("N2: проигравший лежит в _conflicts/<имя>/", len(losers_n2) == 1
          and losers_n2[0].startswith("_conflicts/index.html/"), losers_n2)
    check("N2: мусора вида index.html.ник рядом нет",
          not [p for p in got_n2 if p.startswith("index.html.") and "/" not in p])
    if losers_n2:
        name = losers_n2[0].rsplit("/", 1)[-1]
        slug = name.rsplit("-", 1)[0]
        words = tools._translit("Ищущий информацию: конфликт").split("-")
        check("N2: имя конфликта не обрезано посреди слова",
              re.match(r"^[0-9a-f]{8}\.html$", name.rsplit("-", 1)[1]) is not None
              and all(part in words for part in slug.split("-")), name)
        check("N2: в отчёте слияния конфликт назван",
              bool(rep_n2["conflicts"]) and rep_n2["conflicts"][0]["winner"].startswith("Пишущий код"))

    # --- N3 и пункт «неограниченное число действий» ---
    db.set_settings({"api_key": "sk", "base_url": "http://127.0.0.1:8851", "demo_mode": 0,
                     "model": "mock", "agents": 2, "concurrency": 2, "stall_rounds": 3,
                     "agent_timeout": 30, "max_rounds": 0, "loop_guard": 1})
    Mock.mode = "many"
    Mock.calls.update({"n": 0, "truncated": 0})
    pid_n3 = run_project(uid, "восемь действий за один ответ", 2)
    written = [f["path"] for aid, _ in tools.agent_workspaces(pid_n3)
               for f in tools.tree(pid_n3, agent_id=aid) if f["path"].startswith("много-")]
    check("N3: все 8 действий из одного ответа выполнены",
          len(set(written)) >= 8, "%d файлов" % len(set(written)))
    check("N3: неограниченное число действий за раунд (было 6)",
          len(set(written)) > 6 and not [e for e in db.events_since(pid_n3, 0, limit=3000)
                                         if e["type"] == "log" and "обрезан" in (e["text"] or "")])

    # --- N4: read_shared и промежуточное слияние ---
    pid_n4 = db.create_project(uid, "Общий", "общий проект", agents_total=2)["id"]
    a4 = db.add_agent(pid_n4, 1, "Пишущий код: общий", "Программист", "code")
    a5 = db.add_agent(pid_n4, 2, "Читающий: общий", "Разведчик", "research")
    tools.workspace_for(pid_n4)
    tools.workspace_for(pid_n4, a4)
    tools.workspace_for(pid_n4, a5)
    tools.write_file(pid_n4, "плана.md", "# план", None)          # общий проект
    tools.write_file(pid_n4, "код.py", "print(1)", a4)            # работа коллеги
    shared = tools.read_shared(pid_n4)
    shared_file = tools.read_shared(pid_n4, "код.py", agent_id=a4)
    check("N4: read_shared показывает общий проект и песочницы коллег",
          "плана.md" in shared["project"] and any(x["agent"] == a4 for x in shared["agents"]),
          shared["project"])
    check("N4: read_shared читает чужой файл", "print(1)" in shared_file["content"])

    Mock.mode = "multi"                   # агенты пишут файлы по кругам — есть что сливать
    Mock.calls.update({"n": 0, "truncated": 0})
    db.set_settings({"merge_interval": 1, "agents": 4, "concurrency": 1})
    Mock.delay = 0.4                      # прогон длиннее одного тика слияния
    pid_n4b = run_project(uid, "промежуточное слияние", 4, seconds=180)
    Mock.delay = 0.0
    Mock.mode = "normal"
    merge_evs = [e for e in db.events_since(pid_n4b, 0, limit=6000) if e["type"] == "merge"]
    partial = [e for e in merge_evs if (e["data"] or {}).get("partial")]
    check("N4: промежуточное слияние идёт по ходу прогона", bool(partial),
          "%d из %d событий слияния" % (len(partial), len(merge_evs)))
    db.set_settings({"merge_interval": 20})

    # --- переключатель защиты от зацикливания ---
    Mock.mode = "loop"
    db.set_settings({"loop_guard": 0, "agent_timeout": 6, "agents": 2, "concurrency": 2})
    Mock.calls.update({"n": 0, "truncated": 0})
    pid_ng = run_project(uid, "без защиты", 2, seconds=120)
    logs_ng = [e["text"] for e in db.events_since(pid_ng, 0, limit=5000) if e["type"] == "log"]
    check("защита выключается тумблером (loop_guard=0): остановок по повтору нет",
          not any("повторяется" in (t or "") for t in logs_ng)
          and not any("без изменений" in (t or "") for t in logs_ng),
          "вызовов %d" % Mock.calls["n"])
    db.set_settings({"loop_guard": 1})

    # --- N8: экранирование в app.js ---
    js = (BASE_DIR / "static" / "app.js").read_text(encoding="utf-8")
    # берём esc() построчно: от объявления до строки, где заканчиваются замены кавычек
    js_lines = js.splitlines()
    i0 = next(i for i, l in enumerate(js_lines) if l.startswith("const esc"))
    i1 = next(i for i in range(i0, len(js_lines)) if "&#39;" in js_lines[i] and "replace" in js_lines[i])
    js_esc = "\n".join(js_lines[i0:i1 + 1]) + "\n"
    # строку собираем через json.dumps — так кавычки не надо экранировать вручную
    js_test = ("\nvar bad = %s;\nvar out = esc(bad);\n"
               "if (/&quot;/.test(out) === false) { process.exit(2); }\n"
               "if (/&#39;/.test(out) === false) { process.exit(3); }\n"
               "if (out.indexOf('<') >= 0) { process.exit(4); }\n") % json.dumps("a\"b'c<d>&")
    probe = js_esc + js_test
    tmp_js = os.path.join(tempfile.mkdtemp(), "esc.js")
    open(tmp_js, "w", encoding="utf-8").write(probe)
    node = shutil.which("node")
    if node:
        rc = subprocess.run([node, tmp_js], capture_output=True).returncode
        check("N8: esc() экранирует кавычки (проверено в node)", rc == 0, "код %d" % rc)
    else:
        check("N8: esc() экранирует кавычки", "&quot;" in js_esc and "&#39;" in js_esc, "без node — по коду")
    check("N8: имя файла не попадает в разметку атрибутом",
          'data-open="${esc(' not in js and "b.dataset.open" in js)


    # ================= 1.4.0: КОНСОЛЬ АГЕНТА =================
    demo_agent = next((x for x in agents if (db.jload(x.get("files"), []) or [])), agents[0])
    con = db.agent_console(demo_agent["id"])
    kinds = [e["type"] for e in con]
    check("консоль: у агента видно, что он делал", len(con) >= 3 and "file" in kinds and "console" in kinds,
          "%d событий" % len(con))
    check("консоль: есть первая и последняя строка",
          any((e["data"] or {}).get("kind") == "start" for e in con)
          and any((e["data"] or {}).get("kind") == "stop" for e in con))
    check("консоль: события по порядку, без дыр",
          all(con[i]["id"] < con[i + 1]["id"] for i in range(len(con) - 1)))

    # длинный вывод команды: в ленте коротко, в консоли целиком
    db.set_settings({"api_key": "sk", "base_url": "http://127.0.0.1:8851", "demo_mode": 0,
                     "model": "mock", "agents": 2, "concurrency": 2, "agent_timeout": 30,
                     "loop_guard": 1, "stall_rounds": 3, "max_rounds": 0})
    Mock.mode = "shell"
    Mock.calls.update({"n": 0, "truncated": 0})
    pid_con = run_project(uid, "длинный вывод команды", 2)
    tool_evs = [e for e in db.events_since(pid_con, 0, limit=8000) if e["type"] == "tool"]
    with_full = [e for e in tool_evs if len(((e["data"] or {}).get("out_full") or "")) > 800]
    check("консоль: полный вывод команды, а не обрезанные 800 символов", bool(with_full),
          "%d симв. в out_full" % (len(with_full[0]["data"]["out_full"]) if with_full else 0))
    short_feed = [e for e in with_full if len((e["data"] or {}).get("out") or "") <= 800]
    check("консоль: в ленте тот же вывод остаётся коротким", bool(short_feed))

    # HTTP: консоль только владельцу, догрузка по since, .txt на скачивание
    st_con = Stand()
    u_con = db.upsert_user("console@t.t", "Консоль", provider="local")
    pid_http = db.create_project(u_con["id"], "Консоль", "проверка", agents_total=1)["id"]
    a_http = db.add_agent(pid_http, 1, "Пишущий код: консоль", "Программист", "code")
    db.add_event(pid_http, a_http, "console", "старт · песочница", {"kind": "start"})
    db.add_event(pid_http, a_http, "tool", "терминал: ls -la",
                 {"cmd": "ls -la", "out": "a.txt", "out_full": "a.txt\nb.txt", "ok": True})
    db.add_event(pid_http, a_http, "console", "круг 1 · ответ 40 симв.", {"kind": "llm", "round": 1})
    cookie_con = "ms_session=" + db.create_session(u_con["id"])
    pc, data_con, _ = st_con.json("/api/agents/%s/console?since=0" % a_http, cookie=cookie_con)
    check("консоль: HTTP отдаёт консоль владельцу",
          pc == 200 and len(data_con.get("events") or []) == 3 and data_con.get("last"),
          "%d событий" % len(data_con.get("events") or []))
    pc2, data_since, _ = st_con.json("/api/agents/%s/console?since=%s" % (a_http, data_con.get("last")),
                                     cookie=cookie_con)
    check("консоль: догрузка по since не дублирует", pc2 == 200 and not (data_since.get("events") or []))
    u_other_con = db.upsert_user("other-con@t.t", "Чужой", provider="local")
    cookie_other_con = "ms_session=" + db.create_session(u_other_con["id"])
    pc3, _, _ = st_con.req("/api/agents/%s/console" % a_http, cookie=cookie_other_con)
    check("консоль: чужой не видит", pc3 == 403, "код %d" % pc3)
    pc4, _, _ = st_con.req("/api/agents/%s/console" % a_http)
    check("консоль: без входа — 401", pc4 == 401, "код %d" % pc4)
    pc5, txt_con, _ = st_con.req("/api/agents/%s/console.txt" % a_http, cookie=cookie_con)
    txt_tree = txt_con.decode("utf-8")
    check("консоль: скачивается .txt с полным выводом",
          pc5 == 200 and "$ ls -la" in txt_tree and "b.txt" in txt_tree)
    st_con.stop()

    # ================= 1.4.0: ПОЧТА =================
    saved_mail = {k: db.get_settings().get(k) for k in db.DEFAULT_SETTINGS if k.startswith("mail_")}
    fx = MailFixture()
    db.set_settings(fx.settings())
    cfg_mail = db.get_settings()

    miss = mailbox.search({}, days=30)
    check("почта: без настроек — причина словами, а не пустой список",
          miss.get("ok") is False and "не настроена" in (miss.get("reason") or ""))

    found = mailbox.search(cfg_mail, days=30, limit=5)
    subjects = [m["subject"] for m in found.get("messages") or []]
    check("почта: поиск видит письма", bool(found.get("ok")) and "Счёт за ноябрь" in subjects
          and "Отчёт по проекту" in subjects, subjects)

    ru = mailbox.search(cfg_mail, query="счёт", days=30)
    check("почта: русский запрос идёт через CHARSET UTF-8",
          [m["subject"] for m in ru.get("messages") or []] == ["Счёт за ноябрь"],
          [m["subject"] for m in ru.get("messages") or []])

    unread = mailbox.search(cfg_mail, days=30, unread=True)
    check("почта: непрочитанные фильтруются",
          [m["subject"] for m in unread.get("messages") or []] == ["Отчёт по проекту"])

    one = mailbox.read(cfg_mail, 1)
    check("почта: читает письмо целиком", bool(one.get("ok")) and "1 234" in (one.get("body") or ""),
          (one.get("body") or "")[:40])

    sent = mailbox.send(cfg_mail, to="friend@example.org", subject="Итоги", body="Во вложении отчёт")
    check("почта: отправка письма", bool(sent.get("ok")) and bool(fx.sent)
          and "Во вложении" in fx.sent[-1]["body"], sent.get("message_id"))

    db.set_settings({"mail_allow_send": 0})
    off = mailbox.send(db.get_settings(), to="friend@example.org", subject="x", body="y")
    db.set_settings({"mail_allow_send": 1})
    check("почта: отправку можно выключить тумблером",
          off.get("ok") is False and "выключена" in (off.get("reason") or ""))

    bad = mailbox.search(fx.settings(mail_password="wrong"), days=30)
    check("почта: неверный пароль — внятная причина",
          bad.get("ok") is False and "не пускает" in (bad.get("reason") or ""),
          (bad.get("reason") or "")[:60])

    folders = mailbox.folders(cfg_mail)
    check("почта: видит папки (в т.ч. русские имена)", bool(folders.get("ok"))
          and "INBOX" in (folders.get("folders") or []), folders.get("folders"))

    # агент сам пользуется почтой
    Mock.mode = "mail"
    Mock.calls.update({"n": 0, "truncated": 0})
    pid_mail = run_project(uid, "проверь почту и скажи, что там", 2)
    mail_evs = [e for e in db.events_since(pid_mail, 0, limit=8000)
                if e["type"] == "tool" and (e["data"] or {}).get("mail")]
    check("почта: агент сам ищет письма инструментом",
          bool(mail_evs) and "Счёт за ноябрь" in ((mail_evs[0]["data"] or {}).get("out_full") or ""),
          (mail_evs[0]["data"].get("out") or "")[:60] if mail_evs else "нет события")
    # тумблер «Интернет» выключает и почту — иначе он врал бы
    Mock.calls.update({"n": 0, "truncated": 0})
    Mock.mode = "mail"
    db.set_settings({"allow_network": 0})
    pid_netoff = run_project(uid, "почта при выключенной сети", 2)
    net_off = [e for e in db.events_since(pid_netoff, 0, limit=8000)
               if "интернет выключен" in (e["text"] or "")]
    db.set_settings({"allow_network": 1})
    check("почта: тумблер «Интернет» выключает и почту", bool(net_off),
          (net_off[0]["text"] if net_off else "нет события"))

    Mock.mode = "normal"
    fx.stop()
    db.set_settings(saved_mail)

    # --- ПОЛНЫЙ ДОСТУП АГЕНТОВ: ничего не урезано ---
    pid_free = db.create_project(uid, "Свобода", "полный доступ", agents_total=1)["id"]
    tools.workspace_for(pid_free)
    tools.write_file(pid_free, "a.txt", "x")
    escaped = db.DATA.parent / "ESCAPED-agent.txt"
    if escaped.exists():
        escaped.unlink()
    res_free = tools.run_command(pid_free, "echo x > ../../../ESCAPED-agent.txt && echo ok",
                                timeout=15, allow=True, strict=False)
    check("агент: терминал без ограничений — пишет файл вне песочницы",
          "ok" in (res_free.get("out") or "") and escaped.exists(),
          (res_free.get("out") or "")[:60])
    web_srv = _searx_fixture()
    web_local = tools.fetch_url("http://127.0.0.1:%d/searx" % web_srv[1], allow=True)
    check("агент: открывает любой URL, включая localhost",
          web_local.get("ok") is True and web_local.get("status") == 200,
          "status=%s" % web_local.get("status"))
    web_srv[0].shutdown()
    check("агент: настройки по умолчанию — терминал и сеть включены, строгий режим выключен",
          int(db.get_settings()["allow_terminal"]) == 1
          and int(db.get_settings()["allow_network"]) == 1
          and int(db.get_settings()["terminal_strict"]) == 0)


    # ======================= 1.4.2: вложения любых форматов =======================
    # Пользователь прикрепляет файлы и ссылается на них в запросе через «@».
    # Проверяем: имена, извлечение текста, загрузку по HTTP, переезд в проект,
    # скачивание, удаление, лимит размера, чужой доступ и то, что агент видит вложения.
    import base64 as _b64
    import files
    import zipfile as _zip

    check("F1: имя файла без путей и мусора",
          files.safe_name("../../etc/passwd") == "passwd"
          and files.safe_name("a\x00b\n.txt") == "ab.txt"
          and files.safe_name("Отчёт 2026.docx") == "Отчёт 2026.docx"
          and files.safe_name("") == "файл",
          files.safe_name("../../etc/passwd"))

    def _fixtures():
        d = Path(tempfile.mkdtemp())
        (d / "цель.txt").write_bytes("Сделай бота приёма заявок".encode("utf-8"))
        with _zip.ZipFile(d / "тз.docx", "w") as z:
            z.writestr("word/document.xml",
                       "<w:document><w:body><w:p><w:r><w:t>ТЗ на телеграм-бота</w:t></w:r></w:p>"
                       "</w:body></w:document>")
        with _zip.ZipFile(d / "прайс.xlsx", "w") as z:
            z.writestr("xl/worksheets/sheet1.xml",
                       "<worksheet><sheetData><row><c><v>Эспрессо</v></c><c><v>250</v></c></row>"
                       "</sheetData></worksheet>")
        content = b"BT /F1 12 Tf 72 720 Td (Oplata 48000 rubley) Tj ET"
        import zlib as _zlib
        stream = _zlib.compress(content)
        (d / "договор.pdf").write_bytes(
            b"%PDF-1.4\n4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n"
            + stream + b"\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
        return d

    fx = _fixtures()
    check("F2: текст из txt/docx/xlsx/pdf извлекается",
          "бота приёма заявок" in files.extract_text(str(fx / "цель.txt"))
          and "ТЗ на телеграм-бота" in files.extract_text(str(fx / "тз.docx"))
          and "Эспрессо" in files.extract_text(str(fx / "прайс.xlsx"))
          and "48000" in files.extract_text(str(fx / "договор.pdf")))
    (fx / "снимок.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    check("F3: у бинарника понятная выжимка, а не пустота и не падение",
          "не извлекается" in files.digest(str(fx / "снимок.png"))
          and "офис" in files.digest(str(fx / "тз.docx")),
          files.digest(str(fx / "снимок.png"))[:70])

    st_f = Stand()
    try:
        code, cookie, _ = st_f.register("Вложения")
        owner = db.user_by_session(cookie.split("=", 1)[1])
        ids = {}
        for name in ("цель.txt", "тз.docx", "прайс.xlsx", "договор.pdf"):
            blob = (fx / name).read_bytes()
            code, data, _ = st_f.json("/api/uploads", method="POST", cookie=cookie,
                                      body={"name": name, "mime": "application/octet-stream",
                                            "data": _b64.b64encode(blob).decode()})
            ids[name] = (data.get("file") or {}).get("id")
            if code != 200:
                check("F4: загрузка %s" % name, False, data)
                break
        check("F4: четыре файла разных форматов загружены",
              all(ids.values()) and code == 200, list(ids.values()))

        code, data, _ = st_f.json("/api/uploads", cookie=cookie)
        check("F5: черновики видны до создания проекта",
              code == 200 and len(data.get("files") or []) == 4, len(data.get("files") or []))

        (fx / "большой.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        db.set_settings({"max_upload_mb": 1})
        code, data, _ = st_f.json("/api/uploads", method="POST", cookie=cookie,
                                  body={"name": "большой.bin",
                                        "data": _b64.b64encode((fx / "большой.bin").read_bytes()).decode()})
        check("F6: лимит размера — причина словами, без падения",
              code == 400 and "больше" in (data.get("error") or ""), data.get("error"))
        db.set_settings({"max_upload_mb": 25})

        code, data, _ = st_f.json("/api/uploads", method="POST", cookie=cookie,
                                  body={"name": "битый.txt", "data": "!!!не-base64!!!"})
        check("F7: битый base64 — понятная ошибка",
              code == 400 and "битым" in (data.get("error") or ""), data.get("error"))

        goal = "Сделай из @цель.txt и @тз.docx телеграм-бота по @договор.pdf"
        code, data, _ = st_f.json("/api/projects", method="POST", cookie=cookie,
                                  body={"goal": goal, "agents": 3,
                                        "files": [ids["цель.txt"], ids["тз.docx"], ids["договор.pdf"]]})
        pid_f = (data.get("project") or {}).get("id")
        check("F8: проект создан с тремя вложениями", code == 200 and bool(pid_f), code)
        check("F9: «@файл» в цели сохранился как есть",
              "@цель.txt" in (db.get_project(pid_f) or {}).get("goal", ""))

        ws_files = [f["path"] for f in tools.tree(pid_f)]
        check("F10: файлы лежат в workspace проекта (папка files/)",
              all(("files/" + n) in ws_files for n in ("цель.txt", "тз.docx", "договор.pdf")),
              ws_files[:6])

        code, data, _ = st_f.json("/api/projects/%s/files" % pid_f, cookie=cookie)
        names = [f["name"] for f in (data.get("files") or [])]
        check("F11: сервер отдаёт список вложений проекта",
              code == 200 and len(names) == 3 and "тз.docx" in names, names)

        fid = ids["тз.docx"]
        code, body, hdrs = st_f.req("/api/files/%s/download" % fid, cookie=cookie)
        check("F12: скачивание отдаёт тот же файл",
              code == 200 and body == (fx / "тз.docx").read_bytes()
              and "attachment" in (hdrs.get("Content-Disposition") or ""), code)

        code, data, _ = st_f.json("/api/projects/%s/files" % pid_f, method="POST",
                                  cookie=cookie, body={"files": [ids["прайс.xlsx"]]})
        check("F13: файл можно прикрепить к уже идущему проекту",
              code == 200 and (data.get("added") == 1) and len(data.get("files") or []) == 4,
              data.get("added"))

        prompt = orchestrator._attach_prompt(pid_f)
        check("F14: агент видит вложения в промпте (имена + что внутри)",
              "@цель.txt" in prompt and "бота приёма заявок" in prompt and "attached(" in prompt)

        one = orchestrator._attached_tool(pid_f, "тз.docx")
        gone = orchestrator._attached_tool(pid_f, "нет-такого")
        check("F15: инструмент attached() отдаёт текст и честную причину",
              one.get("ok") and "телеграм-бота" in (one.get("text") or "")
              and gone.get("ok") is False and "нет" in (gone.get("reason") or ""))

        # чужой пользователь не видит ни список, ни файл
        _c2, cookie2, _ = st_f.register("Посторонний")
        code2, data2, _ = st_f.json("/api/files/%s/download" % fid, cookie=cookie2)
        code3, data3, _ = st_f.json("/api/projects/%s/files" % pid_f, cookie=cookie2)
        check("F16: чужие вложения закрыты",
              code2 in (403, 404) and code3 in (403, 404), "%s/%s" % (code2, code3))

        # свежий черновик: прежние четыре уже переехали в проект
        code, data, _ = st_f.json("/api/uploads", method="POST", cookie=cookie,
                                  body={"name": "лишний.txt", "data": _b64.b64encode(
                                      "мусор".encode("utf-8")).decode()})
        draft = [f for f in (db.list_files(owner["id"]) if owner else []) if not f["project_id"]]
        code, data, _ = st_f.json("/api/files/%s" % draft[0]["id"], method="DELETE", cookie=cookie)
        left = [f for f in db.list_files(owner["id"]) if not f["project_id"]]
        check("F17: удаление вложения убирает и строку, и файл",
              code == 200 and len(left) == len(draft) - 1
              and not Path(draft[0]["path"]).exists(), len(left))
    finally:
        st_f.stop()

    # ================== 1.5.0: скиллы, музыка, обучение, без лимитов ==================
    # Скиллы — свои .md-инструкции для агентов: создать, править, включать/выключать, удалять,
    # импортировать из файла, выгружать обратно. Плюс музыка проекта (своя или встроенная),
    # обучение при первом входе и снятые лимиты на размер файла и объём текста.
    import files as _files_mod

    check("G1: лимиты сняты — размер файла и текст без потолка",
          int(db.DEFAULT_SETTINGS.get("max_upload_mb")) == 0
          and int(getattr(_files_mod, "MAX_TEXT", -1)) == 0,
          "max_upload_mb=%r MAX_TEXT=%r" % (db.DEFAULT_SETTINGS.get("max_upload_mb"),
                                            getattr(_files_mod, "MAX_TEXT", None)))

    long_text = ("строка про бота и заявки %d\n" % 1) * 2000     # ~54 000 символов
    big_txt = fx / "длинный.txt"
    big_txt.write_text(long_text, encoding="utf-8")
    got = _files_mod.extract_text(str(big_txt))
    check("G2: длинный текст из файла отдаётся целиком",
          len(got) == len(long_text.rstrip("\n")) or len(got) >= len(long_text) - 2,
          "%d из %d символов" % (len(got), len(long_text)))

    st_s = Stand()
    try:
        code_s, cookie_s, _ = st_s.register("Скиллы")
        owner_s = db.user_by_session(cookie_s.split("=", 1)[1])

        code, data, _ = st_s.json("/api/skills", method="POST", cookie=cookie_s,
                                  body={"name": "Стиль кода",
                                        "body": "# Стиль\n\nПиши коротко, без воды. Комментарии по-русски."})
        sk = (data.get("skill") or {})
        check("G3: скилл создаётся и сразу включён",
              code == 200 and sk.get("id") and sk.get("enabled") in (1, True), data)

        skid = sk.get("id") or "no-such-id"
        code, data, _ = st_s.json("/api/skills", cookie=cookie_s)
        check("G4: список скиллов отдаётся", code == 200 and len(data.get("skills") or []) == 1,
              len(data.get("skills") or []))

        code, data, _ = st_s.json("/api/skills/%s" % skid, method="PUT", cookie=cookie_s,
                                  body={"enabled": 0})
        check("G5: скилл выключается переключателем",
              code == 200 and (data.get("skill") or {}).get("enabled") in (0, False), data)

        code, data, _ = st_s.json("/api/skills/%s" % skid, method="PUT", cookie=cookie_s,
                                  body={"name": "Стиль кода v2", "body": "# Стиль\n\nЕщё короче.",
                                       "enabled": 1})
        check("G6: скилл можно переименовать и переписать",
              code == 200 and (data.get("skill") or {}).get("name") == "Стиль кода v2", data)

        code, data, _ = st_s.json("/api/skills", method="POST", cookie=cookie_s,
                                  body={"name": "", "body": ""})
        check("G7: пустое имя — причина словами, а не падение",
              code == 400 and bool(data.get("error")), data.get("error"))

        md = "# Из файла\n\nПроверяй вывод команд перед отчётом.".encode("utf-8")
        code, data, _ = st_s.json("/api/skills/import", method="POST", cookie=cookie_s,
                                  body={"name": "из-файла.md", "data": _b64.b64encode(md).decode()})
        sk2 = (data.get("skill") or {})
        check("G8: скилл импортируется из .md-файла",
              code == 200 and sk2.get("id") and "Проверяй вывод" in (sk2.get("body") or ""), data)

        sk2id = sk2.get("id") or "no-such-id"
        code, body_md, hdrs = st_s.req("/api/skills/%s/md" % sk2id, cookie=cookie_s)
        check("G9: скилл выгружается обратно в .md",
              code == 200 and body_md.decode("utf-8") == md.decode("utf-8")
              and "attachment" in (hdrs.get("Content-Disposition") or ""), code)

        _sp = getattr(orchestrator, "_skills_prompt", lambda *_: "")
        _st_tool = getattr(orchestrator, "_skill_tool", lambda *_: {"ok": False, "reason": ""})
        prompt_on = _sp(owner_s["id"]) if owner_s else ""
        _upd = getattr(db, "update_skill", lambda *a, **k: None)
        _upd(skid, enabled=0)
        prompt_off = _sp(owner_s["id"]) if owner_s else ""
        _upd(skid, enabled=1)
        check("G10: в промпт агентов попадают только включённые скиллы",
              "Ещё короче" in prompt_on and "Ещё короче" not in prompt_off,
              (prompt_on[:60] if prompt_on else "пусто"))

        tool_ok = _st_tool(owner_s["id"], "Стиль кода v2")
        tool_no = _st_tool(owner_s["id"], "нет-такого")
        check("G11: агент читает скилл инструментом skill(name)",
              tool_ok.get("ok") and "Ещё короче" in (tool_ok.get("body") or "")
              and tool_no.get("ok") is False and "нет" in (tool_no.get("reason") or ""), tool_no)

        code2, cookie2, _ = st_s.register("Чужой скиллы")
        code_x, data_x, _ = st_s.json("/api/skills/%s" % skid, method="PUT", cookie=cookie2,
                                      body={"enabled": 0})
        code_y, data_y, _ = st_s.json("/api/skills", cookie=cookie2)
        check("G12: чужие скиллы не видны и не меняются",
              code_x in (403, 404) and len(data_y.get("skills") or []) == 0,
              "%s / %s" % (code_x, len(data_y.get("skills") or [])))

        import mcp as mcp_api
        demo = str(Path(__file__).resolve().parent / "mcp_demo_server.py")
        code_m, result_m, _ = st_s.json("/api/mcp", method="POST", cookie=cookie_s,
            body={"name": "MCP тест", "transport": "stdio", "command": sys.executable, "args": [demo], "timeout": 5})
        mid = result_m.get("server", {}).get("id", "")
        check("MCP API: создание", code_m == 200 and bool(mid))
        code_m, result_m, _ = st_s.json("/api/mcp/" + mid + "/test", method="POST", cookie=cookie_s, body={})
        check("MCP API: проверка и кэш", code_m == 200 and result_m.get("count") == 4)
        code_m, result_m, _ = st_s.json("/api/mcp/" + mid + "/call", method="POST", cookie=cookie_s,
                                       body={"tool": "echo", "args": {"text": "проверка"}})
        check("MCP API: вызов", code_m == 200 and result_m.get("text") == "проверка")
        code_m, _, _ = st_s.json("/api/mcp/" + mid, method="DELETE", cookie=cookie2)
        check("MCP API: чужой сервер защищён", code_m == 404)
        code_m, _, _ = st_s.json("/api/mcp/settings", method="POST", cookie=cookie_s, body={"on": 1})
        check("MCP API: настройки не перекрыты маршрутом ID", code_m == 200)
        st_s.json("/api/mcp/" + mid, method="DELETE", cookie=cookie_s)
        check("MCP API: удаление закрывает процесс", mcp_api.pool_size() == 0)

        # --- музыка проекта ---
        code, data, _ = st_s.json("/api/music", cookie=cookie_s)
        check("G13: музыка — состояние и встроенная тема на месте",
              code == 200 and data.get("ok") and data.get("has_default") is True
              and data.get("volume") is not None, data)

        code, data, _ = st_s.json("/api/settings", method="PUT", cookie=cookie_s,
                                  body={"music_on": 0, "music_volume": 70})
        stt = db.get_settings()
        check("G14: музыку можно выключить и задать громкость",
              code == 200 and int(stt.get("music_on") or 0) == 0 and int(stt.get("music_volume") or 0) == 70,
              (stt.get("music_on"), stt.get("music_volume")))

        theme_bytes = b"OggS" + b"\x00" * 200
        code, data, _ = st_s.json("/api/music", method="POST", cookie=cookie_s,
                                  body={"name": "своя.ogg", "data": _b64.b64encode(theme_bytes).decode()})
        check("G15: свою музыку можно загрузить", code == 200 and (data.get("music") or {}).get("custom"),
              data)
        code, body_mus, _ = st_s.req("/api/music/file", cookie=cookie_s)
        check("G16: /api/music/file отдаёт загруженный трек",
              code == 200 and body_mus == theme_bytes, code)
        code, data, _ = st_s.json("/api/music", method="DELETE", cookie=cookie_s)
        check("G17: свою музыку можно убрать — вернётся встроенная",
              code == 200 and not (data.get("music") or {}).get("custom"), data)

        # --- обучение при первом входе ---
        code, data, _ = st_s.json("/api/bootstrap", cookie=cookie_s)
        check("G18: новому пользователю обучение ещё не пройдено",
              code == 200 and data.get("onboarded") is False, data.get("onboarded"))
        code, data, _ = st_s.json("/api/onboard/done", method="POST", cookie=cookie_s)
        code2, data2, _ = st_s.json("/api/bootstrap", cookie=cookie_s)
        code3, data3, _ = st_s.json("/api/onboard/done", method="POST", cookie=cookie_s)
        check("G19: «обучение пройдено» запоминается и повтор не ломает",
              code == 200 and data2.get("onboarded") is True and code3 == 200,
              (code, data2.get("onboarded"), code3))

        # --- интерфейс: всё, что должно быть на странице ---
        code, html, _ = st_s.req("/static/index.html", cookie=cookie_s)
        htxt = html.decode("utf-8")
        check("G20: в интерфейсе есть скиллы, загрузчик, музыка и обучение",
              all(m in htxt for m in ('data-view="skills"', 'id="skillsList"', 'id="loader"',
                                      'id="musicAudio"', 'id="tour"')), "нет части разметки")

        code, js, _ = st_s.req("/static/app.js", cookie=cookie_s)
        jtxt = js.decode("utf-8")
        check("G21: в скрипте есть логика скиллов, загрузчика, музыки и обучения",
              all(m in jtxt for m in ("loadSkills", "showLoader", "startMusic", "startTour")),
              "нет части кода")

        # --- вложения без настроек не режутся ---
        db.set_settings({"max_upload_mb": 0})
        payload = b"z" * (12 * 1024 * 1024)
        code, data, _ = st_s.json("/api/uploads", method="POST", cookie=cookie_s,
                                  body={"name": "большой-12мб.bin",
                                        "data": _b64.b64encode(payload).decode()})
        check("G22: при снятом лимите файл на 12 МБ загружается",
              code == 200 and (data.get("file") or {}).get("size") == len(payload),
              (code, (data.get("file") or {}).get("size")))
        # --- озвучка обучения и отдача музыки кусками ---
        steps = sorted((BASE_DIR / "static" / "onboard").glob("step*.mp3"))
        check("G23: озвучка обучения лежит в комплекте",
              len(steps) == 7 and all(p.stat().st_size > 20000 for p in steps)
              and all(p.read_bytes()[:2] in (b"\xff\xfb", b"\xff\xf3", b"ID") for p in steps),
              "файлов: %d, размеры: %s" % (len(steps), [p.stat().st_size // 1024 for p in steps]))

        code, body_a, hdrs_a = st_s.req("/static/onboard/step1.mp3", cookie=cookie_s)
        check("G24: озвучка шага отдаётся как audio/mpeg",
              code == 200 and "audio/mpeg" in (hdrs_a.get("Content-Type") or "")
              and len(body_a) > 20000, (code, hdrs_a.get("Content-Type")))

        code, body_r, hdrs_r = st_s.req("/api/music/file", cookie=cookie_s,
                                        headers={"Range": "bytes=0-1023"})
        check("G25: музыка отдаётся кусками (Range 206) — браузер знает длину",
              code == 206 and len(body_r) == 1024
              and "bytes 0-1023/" in (hdrs_r.get("Content-Range") or ""),
              (code, hdrs_r.get("Content-Range")))
    finally:
        st_s.stop()

    # MCP: живые stdio и Streamable HTTP, включая сессионные заголовки.
    import mcp
    import mcp_demo_server
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    demo_path = str(Path(__file__).resolve().parent / "mcp_demo_server.py")
    cfg = mcp.clean_server(name="Тест MCP", command=sys.executable, args=[demo_path], timeout=5)
    cfg["id"] = "test-mcp"
    check("MCP: арифметика и унарные знаки", mcp_demo_server.calc("10 - -3") == 13 and mcp_demo_server.calc("2*-3+1") == -5)
    check("MCP: stdio initialize", mcp.ping(cfg)["ok"])
    listing = mcp.list_tools(cfg)
    check("MCP: stdio список инструментов", listing["ok"] and len(listing["tools"]) == 4)
    check("MCP: stdio вызов", mcp.call_tool(cfg, "calc", {"expr": "2+3"})["text"] == "5")
    check("MCP: ошибка инструмента не убивает сервер", not mcp.call_tool(cfg, "calc", {"expr": "1/0"})["ok"] and mcp.ping(cfg)["ok"])
    img = {"type": "image", "mimeType": "image/png", "data": "YWJj"}
    normalized = mcp.normalize_result({"content": [img]})
    check("MCP: данные картинки сохраняются", normalized["images"] == [img])
    row = db.add_mcp_server(uid, cfg)
    agent_answer = orchestrator._mcp_tool(pid, "Тест MCP", "echo", {"text": "агент"}, {"mcp_on": 1})
    check("MCP: вызов инструментов агентом", agent_answer.get("text") == "агент")
    db.update_mcp_server(row["id"], enabled=0)
    check("MCP: выключенный сервер недоступен агентам", not orchestrator._mcp_tool(pid, "Тест MCP", "echo", {}, {"mcp_on": 1}).get("ok"))
    mcp.drop(row["id"])
    db.delete_mcp_server(row["id"])
    mcp.drop("test-mcp")
    check("MCP: удаление сессии по ID", mcp.pool_size() == 0)
    seen = []
    class MCPStub(BaseHTTPRequestHandler):
        def log_request(self, code="-", size="-"):
            pass
        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((msg["method"], self.headers.get("Mcp-Session-Id")))
            if msg["method"] == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            if msg["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "stub"}, "capabilities": {}}
            elif msg["method"] == "tools/list":
                result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "http ok"}]}
            payload = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}).encode()
            if self.path == "/sse":
                payload = b"data: " + payload + b"\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if self.path == "/sse" else "application/json")
            self.send_header("Mcp-Session-Id", "session-test")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def do_DELETE(self):
            seen.append(("DELETE", self.headers.get("Mcp-Session-Id")))
            self.send_response(204)
            self.end_headers()
    stub = ThreadingHTTPServer(("127.0.0.1", 0), MCPStub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    remote = mcp.clean_server(name="HTTP", transport="http", url="http://127.0.0.1:%d/mcp" % stub.server_port, timeout=5)
    try:
        check("MCP: HTTP список", mcp.list_tools(remote)["ok"])
        check("MCP: HTTP вызов", mcp.call_tool(remote, "echo")["text"] == "http ok")
        mcp.drop(remote)
        sse = mcp.clean_server(name="SSE", transport="http", url="http://127.0.0.1:%d/sse" % stub.server_port, timeout=5)
        check("MCP: HTTP ответы SSE", mcp.list_tools(sse)["ok"] and mcp.call_tool(sse, "echo")["text"] == "http ok")
        mcp.drop(sse)
        check("MCP: HTTP session id и DELETE", ("tools/list", "session-test") in seen and ("DELETE", "session-test") in seen)
    finally:
        mcp.close_all()
        stub.shutdown()
        stub.server_close()
    check("MCP: промпт с инструментами", "echo" in mcp.prompt_block([{"id": "http", "name": "HTTP", "enabled": 1}], {"http": [{"name": "echo"}]}))
    check("скилл без обрезки", len(db.clean_skill("имя", "а".zfill(410000))[1]) == 410000)

    passed = sum(1 for _, ok in RESULT if ok)
    print("\nитог: %d/%d" % (passed, len(RESULT)))
    return 0 if passed == len(RESULT) else 1


if __name__ == "__main__":
    sys.exit(main())
