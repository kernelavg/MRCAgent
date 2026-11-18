#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mailbox.py — тесты почтового модуля без реальной сети.

Запуск:  python3 -m unittest test_mailbox -v

Как устроено:
  * FakeIMAP — настоящий TCP-сервер в отдельном потоке. Он понимает минимум IMAP,
    который нужен модулю, и умеет то, чем реальные серверы ломают клиентов:
      - ставит UID и FLAGS ПОСЛЕ тела письма (пункт 3);
      - отдаёт письма без UID (пункт 3);
      - ругается на папку с пробелом, если её не взяли в кавычки (пункт 2);
      - отдаёт заголовки в «сыром» UTF-8 и тело в x-unknown (пункты 5 и 9);
      - отдаёт тысячи uid для проверки потолка (пункт 10).
  * FakeSMTP — заглушка вместо smtplib.SMTP/SMTP_SSL: запоминает письмо и
    количество отправок (пункты 4, 6, 8, 12).

Каждый тест написан так, чтобы падать на старом mailbox.py (до 1.4.1):
проверки смотрят на то, что реально уходило в сокет и в SMTP.
"""

import imaplib
import os
import re
import smtplib
import socketserver
import ssl
import sys
import threading
import time
import unittest
from email.header import Header
from email.utils import formatdate, parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mailbox  # noqa: E402


# --------------------------------------------------------------------------- #
# фейковый IMAP
# --------------------------------------------------------------------------- #


class FakeIMAP:
    """
    Мини-IMAP на настоящем сокете. Все принятые строки команд складывает в self.commands,
    чтобы тест мог проверить, что именно ушло серверу (кавычки, отсутствие \\r\\n и т.п.).
    """

    def __init__(self):
        self.commands = []
        self.messages = []            # [{"uid": int|None, "flags": str, "raw": bytes, "size": int}]
        self.uid_after_body = False   # UID/FLAGS после литерала (как у Exchange/Dovecot)
        self.synth_uids = False       # отдавать письма и для «лишних» uid (проверка потолка)
        self.folder_error_echos = 0   # сколько раз сервер ругнулся на имя папки
        self.extra_search_uids = []   # доп. uid для проверки потолка
        self.size_override = None     # подменить RFC822.SIZE
        self.fail_first_connect = False
        self.connects = 0
        self.big_body_pad = 0
        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), self._handler())
        self._srv.daemon_threads = True
        self.port = self._srv.server_address[1]
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    # ---- письма ----
    def add(self, subject="Тема", sender="Банк <bank@example.org>", body="текст",
            uid=1, flags="\\Seen", raw=None, size=None, charset="utf-8",
            raw_utf8_headers=False, days_ago=1):
        if raw is None:
            if charset == "utf-8" and not raw_utf8_headers:
                subj_hdr = Header(subject, "utf-8").encode()
            else:
                subj_hdr = subject        # «сырой» UTF-8 без =?utf-8?...
            head = ("From: %s\r\nTo: agent@example.org\r\nSubject: %s\r\n"
                    "Date: %s\r\n" % (sender, subj_hdr,
                                      formatdate(time.time() - days_ago * 86400, localtime=True)))
            payload = body.encode("cp1251" if charset == "x-unknown" else "utf-8", "replace")
            raw = (head + "MIME-Version: 1.0\r\n"
                   "Content-Type: text/plain; charset=%s\r\n"
                   "Content-Transfer-Encoding: 8bit\r\n\r\n" % charset).encode("utf-8") + payload
        self.messages.append({"uid": uid, "flags": flags, "raw": raw,
                              "size": size if size is not None else len(raw)})
        return uid

    def stop(self):
        try:
            self._srv.shutdown()
            self._srv.server_close()
        except Exception:  # noqa: BLE001
            pass

    # ---- сервер ----
    def _handler(self):
        fx = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"* OK [CAPABILITY IMAP4rev1] fake ready\r\n")
                authed = False
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    text = line.decode("utf-8", "surrogateescape").rstrip("\r\n")
                    if not text.strip():
                        continue
                    fx.commands.append(text)
                    tag, cmd, rest = self._split(text)
                    up = cmd.upper()
                    if up == "CAPABILITY":
                        self.wfile.write(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n")
                        self._ok(tag, "CAPABILITY")
                    elif up == "LOGIN":
                        bits = rest.replace('"', "").split()
                        if len(bits) >= 2 and bits[0] == "agent@example.org" \
                                and bits[1] == "app-password-42":
                            authed = True
                            self._ok(tag, "LOGIN")
                        else:
                            self.wfile.write(("%s NO [AUTHENTICATIONFAILED] bad credentials\r\n"
                                              % tag).encode())
                    elif up in ("NOOP", "CLOSE", "CHECK"):
                        self._ok(tag, up)
                    elif up == "SELECT":
                        if not authed:
                            self.wfile.write(("%s NO not logged in\r\n" % tag).encode())
                            continue
                        box = rest.strip()
                        # реальный сервер не примет имя с пробелом без кавычек
                        if " " in box and not box.startswith('"'):
                            fx.folder_error_echos += 1
                            self.wfile.write(("%s BAD could not parse command "
                                              "(нужны кавычки)\r\n" % tag).encode())
                            continue
                        if box.startswith('"') and not box.endswith('"'):
                            fx.folder_error_echos += 1
                            self.wfile.write(("%s BAD unbalanced quotes\r\n" % tag).encode())
                            continue
                        self.wfile.write(("* %d EXISTS\r\n* OK [UIDVALIDITY 1] fake\r\n"
                                          % len(fx.messages)).encode())
                        self._ok(tag, "SELECT")
                    elif up == "LIST":
                        if not authed:
                            self.wfile.write(("%s NO not logged in\r\n" % tag).encode())
                            continue
                        self.wfile.write(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
                        self.wfile.write(b'* LIST (\\HasNoChildren) "/" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"\r\n')
                        self.wfile.write(b'* LIST (\\HasNoChildren) "/" "Sent Items"\r\n')
                        self._ok(tag, "LIST")
                    elif up == "UID":
                        if not authed:
                            self.wfile.write(("%s NO not logged in\r\n" % tag).encode())
                            continue
                        sub, arg = (rest.split(" ", 1) + [""])[:2]
                        what = sub.upper()
                        if what == "SEARCH":
                            self._search(tag, arg)
                        elif what == "FETCH":
                            self._fetch(tag, arg)
                        else:
                            self._ok(tag, what)
                    elif up == "LOGOUT":
                        self.wfile.write(b"* BYE fake bye\r\n")
                        self._ok(tag, "LOGOUT")
                        return
                    else:
                        self._ok(tag, up)

            @staticmethod
            def _split(text):
                parts = text.split(" ", 2)
                tag = parts[0]
                cmd = parts[1] if len(parts) > 1 else ""
                rest = parts[2] if len(parts) > 2 else ""
                return tag, cmd, rest

            def _ok(self, tag, cmd):
                self.wfile.write(("%s OK %s completed\r\n" % (tag, cmd)).encode())

            @staticmethod
            def _raw_headers(raw):
                block = raw
                for sep in (b"\r\n\r\n", b"\n\n"):
                    idx = block.find(sep)
                    if idx >= 0:
                        block = block[:idx]
                        break
                keep, wanted = [], {"from", "to", "subject", "date"}
                take_next = False
                for line in re.split(rb"\r\n|\n", block):
                    if line[:1] in (b" ", b"\t") and keep:
                        keep.append(line)          # продолжение предыдущего
                        continue
                    name = line.split(b":", 1)[0].strip().lower() if b":" in line else b""
                    take_next = name.decode("ascii", "replace") in wanted
                    if take_next:
                        keep.append(line)
                return b"\r\n".join(keep) + b"\r\n\r\n"

            def _search(self, tag, arg):
                uids = [m["uid"] for m in fx.messages if m.get("uid")]
                uids += fx.extra_search_uids
                uids = sorted(uids, reverse=True)
                self.wfile.write(b"* SEARCH " + " ".join(str(u) for u in uids).encode()
                                 + b"\r\n")
                self._ok(tag, "SEARCH")

            def _fetch(self, tag, arg):
                bits = arg.split(" ", 1)
                spec = bits[1] if len(bits) > 1 else ""
                ids = [int(x) for x in re.findall(r"\d+", bits[0]) if x]
                want_size_only = "RFC822.SIZE" in spec.upper() and "BODY" not in spec.upper()
                page = re.search(r"<(\d+)\.(\d+)>", spec)
                items = list(fx.messages)
                if fx.synth_uids:
                    known = {m["uid"] for m in items}
                    for u in ids:
                        if u not in known:
                            items.append({"uid": u, "flags": "\\Seen",
                                          "raw": ("Subject: письмо %d\r\nFrom: x@y.z\r\n"
                                                  "Date: Mon, 10 Nov 2026 10:00:00 +0300\r\n\r\n"
                                                  % u).encode("utf-8"),
                                          "size": 40})
                for m in items:
                    if m.get("uid") not in ids:
                        continue
                    if want_size_only:
                        size = fx.size_override if fx.size_override is not None else m["size"]
                        self.wfile.write(("* %d FETCH (UID %d RFC822.SIZE %d)\r\n"
                                          % (m["uid"], m["uid"], size)).encode())
                        continue
                    raw = m["raw"]
                    if fx.big_body_pad:
                        raw = raw + (b"X" * fx.big_body_pad)
                    if page:
                        start, count = int(page.group(1)), int(page.group(2))
                        raw = raw[start:start + count]
                    if "HEADER.FIELDS" in spec.upper():
                        # сервер отдаёт заголовки КАК ЕСТЬ, байтами: ни кодировок, ни правок
                        head = self._raw_headers(m["raw"])
                        item = "BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]"
                    else:
                        head = raw
                        item = "BODY[]"
                    flags = m["flags"] or ""
                    size = fx.size_override if fx.size_override is not None else m["size"]
                    pre = "UID %d FLAGS (%s) RFC822.SIZE %d " % (m["uid"], flags, size)
                    post = " UID %d FLAGS (%s)" % (m["uid"], flags)
                    if fx.uid_after_body:
                        # UID и FLAGS ТОЛЬКО после тела — старый код терял uid
                        start_line = "* %d FETCH (RFC822.SIZE %d %s {%d}\r\n" % (
                            m["uid"], size, item, len(head))
                        self.wfile.write(start_line.encode())
                        self.wfile.write(head)
                        self.wfile.write(("%s)\r\n" % post).encode())
                    else:
                        start_line = "* %d FETCH (%s%s {%d}\r\n" % (
                            m["uid"], pre, item, len(head))
                        self.wfile.write(start_line.encode())
                        self.wfile.write(head)
                        self.wfile.write(b")\r\n")
                self._ok(tag, "FETCH")

        return H


# --------------------------------------------------------------------------- #
# заглушка SMTP
# --------------------------------------------------------------------------- #


class FakeSMTP:
    """Вместо smtplib.SMTP/SMTP_SSL: письмо никуда не уходит, всё запоминается."""

    sent = []
    connects = 0
    fail_connects = 0          # сколько первых подключений «упадёт» с ConnectionError
    fail_send = 0              # сколько первых отправок упадёт с SMTPException
    send_calls = 0
    last_kwargs = {}

    def __init__(self, host="", port=0, timeout=None, context=None):
        type(self).connects += 1
        if type(self).fail_connects > 0:
            type(self).fail_connects -= 1
            raise ConnectionRefusedError("fake: connection refused")
        self.host, self.port, self.context = host, port, context
        self.quit_called = False

    # --- API, который использует модуль ---
    def login(self, user, password):
        if password == "wrong":
            raise imaplib.IMAP4.error("x")  # не используется, на случай ошибок
        return (235, b"ok")

    def starttls(self, context=None):
        self.context = context
        return (220, b"ok")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        type(self).send_calls += 1
        if type(self).fail_send > 0:
            type(self).fail_send -= 1
            import smtplib
            raise smtplib.SMTPServerDisconnected("fake: разрыв во время отправки")
        type(self).last_kwargs = {"from": from_addr, "to": to_addrs}
        type(self).sent.append({"raw": msg.as_bytes(), "msg": msg,
                                "to": list(to_addrs or []), "from": from_addr})

    def quit(self):
        self.quit_called = True
        return (221, b"bye")


# --------------------------------------------------------------------------- #
# база тестов
# --------------------------------------------------------------------------- #


class MailTest(unittest.TestCase):
    def setUp(self):
        self.imap = FakeIMAP()
        FakeSMTP.sent = []
        FakeSMTP.connects = 0
        FakeSMTP.send_calls = 0
        FakeSMTP.fail_connects = 0
        FakeSMTP.fail_send = 0
        FakeSMTP.last_kwargs = {}
        self._orig_smtp = smtplib.SMTP
        self._orig_smtp_ssl = smtplib.SMTP_SSL
        smtplib.SMTP = FakeSMTP
        smtplib.SMTP_SSL = FakeSMTP
        self._orig_sleep = mailbox.time.sleep
        mailbox.time.sleep = lambda s: None           # не ждём секунду в тестах

    def tearDown(self):
        smtplib.SMTP = self._orig_smtp
        smtplib.SMTP_SSL = self._orig_smtp_ssl
        mailbox.time.sleep = self._orig_sleep
        self.imap.stop()

    def settings(self, **extra):
        s = {
            "mail_imap_host": "127.0.0.1", "mail_imap_port": self.imap.port,
            "mail_imap_enc": "none", "mail_smtp_host": "127.0.0.1", "mail_smtp_port": 2525,
            "mail_smtp_enc": "none", "mail_user": "agent@example.org",
            "mail_password": "app-password-42", "mail_from": "agent@example.org",
            "mail_folder": "INBOX", "mail_allow_send": 1, "mail_timeout": 3,
        }
        s.update(extra)
        return s


# --------------------------------------------------------------------------- #
# 1. проверка сертификата
# --------------------------------------------------------------------------- #


class TestTLS(unittest.TestCase):
    def test_ssl_context_is_passed_and_verifies_by_default(self):
        """IMAP4_SSL должен получать контекст с проверкой сертификата (пункт 1)."""
        seen = {}

        class Boom(Exception):
            pass

        orig = imaplib.IMAP4_SSL

        def fake(host, port=993, ssl_context=None, timeout=None):
            seen["ctx"] = ssl_context
            seen["timeout"] = timeout
            raise ConnectionRefusedError("stop")

        imaplib.IMAP4_SSL = fake
        try:
            c = mailbox.cfg({"mail_imap_host": "h", "mail_user": "u", "mail_password": "p",
                             "mail_from": "u@h"})
            with self.assertRaises(ConnectionRefusedError):
                mailbox._open_imap(c)
        finally:
            imaplib.IMAP4_SSL = orig
        ctx = seen.get("ctx")
        self.assertIsNotNone(ctx, "контекст TLS не передан — сертификат не проверяется")
        self.assertTrue(ctx.check_hostname, "check_hostname выключен по умолчанию")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_tls_verify_zero_disables_check(self):
        ctx = mailbox._ssl_context({"tls_verify": False})
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)

    def test_certificate_error_has_readable_reason(self):
        """Ошибка сертификата → понятный reason про mail_tls_verify."""
        orig = imaplib.IMAP4_SSL

        def fake(host, port=993, ssl_context=None, timeout=None):
            raise ssl.SSLCertVerificationError(1, "certificate verify failed: self signed")

        imaplib.IMAP4_SSL = fake
        try:
            res = mailbox.search({"mail_imap_host": "h", "mail_user": "u",
                                  "mail_password": "p", "mail_from": "u@h"}, days=1)
        finally:
            imaplib.IMAP4_SSL = orig
        self.assertFalse(res["ok"])
        self.assertIn("сертификат", res["reason"])
        self.assertIn("mail_tls_verify", res["reason"])


# --------------------------------------------------------------------------- #
# 2. папки с пробелом
# --------------------------------------------------------------------------- #


class TestFolderQuoting(MailTest):
    def test_folder_with_space_is_quoted(self):
        """«Sent Items» и «[Gmail]/Sent Mail» открываются: имя в кавычках (пункт 2)."""
        self.imap.add(subject="Письмо в отправленных", uid=7)
        for box in ("Sent Items", "[Gmail]/Sent Mail", "INBOX"):
            res = mailbox.search(self.settings(), days=30, folder=box)
            self.assertTrue(res["ok"], "папка %r не открылась: %s" % (box, res.get("reason")))
            self.assertEqual(res["messages"][0]["subject"], "Письмо в отправленных")
        self.assertEqual(self.imap.folder_error_echos, 0,
                         "сервер ругался на имя папки — кавычек не было")
        selects = [c for c in self.imap.commands if " SELECT " in c or " EXAMINE " in c]
        self.assertTrue(any('"Sent Items"' in c for c in selects), selects)

    def test_mailbox_arg_quotes_explicitly(self):
        """
        Кавычки ставим САМИ, не надеясь на stdlib: imaplib._astring с кавычками
        появился только в Python 3.13, на 3.9–3.12 select уходил без них (пункт 2).
        """
        self.assertEqual(mailbox._mailbox_arg("Sent Items"), '"Sent Items"')
        self.assertEqual(mailbox._mailbox_arg("[Gmail]/Sent Mail"), '"[Gmail]/Sent Mail"')
        # кириллица → modified UTF-7, кавычки экранируются
        arg = mailbox._mailbox_arg('Мои "папки"')
        self.assertTrue(arg.startswith('"') and arg.endswith('"'), arg)
        self.assertIn('\\"', arg, arg)
        self.assertNotIn("\r", arg)

    def test_read_in_folder_with_space(self):
        self.imap.add(subject="В отправленных", uid=3, body="тело письма")
        res = mailbox.read(self.settings(), 3, folder="Sent Items")
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertIn("тело письма", res["body"])
        self.assertEqual(self.imap.folder_error_echos, 0)


# --------------------------------------------------------------------------- #
# 3. UID после тела письма
# --------------------------------------------------------------------------- #


class TestUidParsing(MailTest):
    def test_uid_and_flags_after_body(self):
        """Сервер ставит UID/FLAGS после литерала — uid не должен стать 0 (пункт 3)."""
        self.imap.uid_after_body = True
        self.imap.add(subject="Прочитанное", uid=11, flags="\\Seen")
        self.imap.add(subject="Непрочитанное", uid=12, flags="")
        res = mailbox.search(self.settings(), days=30, limit=10)
        self.assertTrue(res["ok"], res.get("reason"))
        got = {m["subject"]: m for m in res["messages"]}
        self.assertEqual(set(got), {"Прочитанное", "Непрочитанное"})
        self.assertEqual(got["Прочитанное"]["uid"], 11)
        self.assertEqual(got["Непрочитанное"]["uid"], 12)
        self.assertFalse(got["Прочитанное"]["unread"], "прочитанное помечено непрочитанным")
        self.assertTrue(got["Непрочитанное"]["unread"])

    def test_message_without_uid_is_skipped_with_reason(self):
        class NoUidIMAP(FakeIMAP):
            def _handler(fx_self):
                h = FakeIMAP._handler(fx_self)

                class H(h):
                    def _fetch(self, tag, arg):
                        # сервер отдал заголовки, но UID не сказал
                        head = ("Subject: Без UID\r\nFrom: x@y.z\r\n"
                                "Date: Mon, 10 Nov 2026 10:00:00 +0300\r\n\r\n").encode("utf-8")
                        self.wfile.write(b"* 1 FETCH (FLAGS (\\Seen) "
                                         b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {%d}\r\n"
                                         % len(head))
                        self.wfile.write(head)
                        self.wfile.write(b")\r\n")
                        self._ok(tag, "FETCH")
                return H

        fx = NoUidIMAP()
        fx.add(subject="Без UID", uid=1)
        try:
            res = mailbox.search({"mail_imap_host": "127.0.0.1", "mail_imap_port": fx.port,
                                  "mail_imap_enc": "none", "mail_user": "agent@example.org",
                                  "mail_password": "app-password-42",
                                  "mail_from": "agent@example.org", "mail_timeout": 3}, days=30)
        finally:
            fx.stop()
        self.assertFalse(res["ok"], "письмо без uid отдано как валидное: %r" % (res,))
        self.assertIn("без UID", res["reason"])
        self.assertFalse([m for m in res.get("messages") or [] if m["uid"] == 0])


# --------------------------------------------------------------------------- #
# 4. дата с часовым поясом
# --------------------------------------------------------------------------- #


class TestSendDate(MailTest):
    def test_date_has_timezone(self):
        """Date должен быть с поясом (formatdate(localtime=True)), а не «UTC» (пункт 4)."""
        res = mailbox.send(self.settings(), to="friend@example.org",
                           subject="Итоги", body="текст")
        self.assertTrue(res["ok"], res.get("reason"))
        raw = FakeSMTP.sent[-1]["raw"].decode("utf-8", "replace")
        date_line = [l for l in raw.splitlines() if l.lower().startswith("date:")][0]
        dt = parsedate_to_datetime(date_line.split(":", 1)[1].strip())
        self.assertIsNotNone(dt.tzinfo, "в Date нет часового пояса: %s" % date_line)
        self.assertIsNotNone(dt.utcoffset())
        # время письма близко к локальному, а не сдвинуто на 3 часа
        self.assertLess(abs(time.time() - dt.timestamp()), 300)


# --------------------------------------------------------------------------- #
# 5 и 9. кодировки тела и заголовков
# --------------------------------------------------------------------------- #


class TestCharsets(MailTest):
    def test_unknown_charset_body_is_not_empty(self):
        """charset=x-unknown → тело читается через cp1251, а не пустая строка (пункт 5)."""
        self.imap.add(subject="Кодировка", uid=5, body="Привет из письма",
                      charset="x-unknown")
        res = mailbox.read(self.settings(), 5)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertIn("Привет из письма", res["body"],
                      "тело пустое или битое: %r" % res["body"])

    def test_unknown_8bit_charset(self):
        self.imap.add(subject="unknown-8bit", uid=6, body="Проверка unknown-8bit",
                      charset="unknown-8bit")
        res = mailbox.read(self.settings(), 6)
        self.assertTrue(res["ok"])
        self.assertIn("Проверка", res["body"])

    def test_raw_utf8_subject_reads_as_text(self):
        """Тема «сырым» UTF-8 (без =?utf-8?...?=) должна читаться как есть (пункт 9)."""
        self.imap.add(subject="Привет мир", uid=8, raw_utf8_headers=True)
        res = mailbox.search(self.settings(), days=30)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertEqual(res["messages"][0]["subject"], "Привет мир",
                         repr(res["messages"][0]["subject"]))

    def test_encoded_subject_still_works(self):
        self.imap.add(subject="Счёт за ноябрь", uid=9)
        res = mailbox.search(self.settings(), days=30)
        self.assertEqual(res["messages"][0]["subject"], "Счёт за ноябрь")

    def test_header_cp1251(self):
        raw = (b"From: =?cp1251?B?yiAiID8/Pw==?= <x@y.z>\r\nSubject: "
               b"\xcf\xf0\xe8\xe2\xe5\xf2\r\nDate: Mon, 10 Nov 2026 10:00:00 +0300\r\n\r\n")
        self.imap.add(uid=10, raw=raw)
        res = mailbox.search(self.settings(), days=30)
        self.assertIn("Привет", res["messages"][0]["subject"],
                      repr(res["messages"][0]["subject"]))


# --------------------------------------------------------------------------- #
# 6. получатели с запятой в имени
# --------------------------------------------------------------------------- #


class TestRecipients(MailTest):
    def test_name_with_comma_is_one_recipient(self):
        res = mailbox.send(self.settings(),
                           to='"Doe, John" <j@x.com>, a@b.c',
                           subject="Кому", body="текст")
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertEqual(FakeSMTP.last_kwargs["to"], ["j@x.com", "a@b.c"],
                         FakeSMTP.last_kwargs["to"])

    def test_semicolons_and_group(self):
        res = mailbox.send(self.settings(), to="a@b.c; d@e.f", subject="Т", body="Б")
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertEqual(FakeSMTP.last_kwargs["to"], ["a@b.c", "d@e.f"])


# --------------------------------------------------------------------------- #
# 7. инъекция через перевод строки
# --------------------------------------------------------------------------- #


class TestInjection(MailTest):
    def test_crlf_in_search_fields_does_not_add_command(self):
        """\\r\\n в query/sender/subject/folder не превращается в новую IMAP-команду (пункт 7)."""
        self.imap.add(subject="Обычное", uid=1)
        before = len(self.imap.commands)
        mailbox.search(self.settings(), query="счёт\r\nUID STORE 1 +FLAGS (\\Deleted)",
                       sender="a@b.c\nNOOP", subject="тема\rX", folder="INBOX\r\nNOOP")
        new = self.imap.commands[before:]
        # одна команда на строку: если бы \r\n прошёл, строк стало бы больше
        self.assertEqual(len(new), 6, "лишние команды из данных: %r" % (new,))
        bodies = [c.split(" ", 1)[1] for c in new if " " in c]
        for body in bodies:
            self.assertFalse(body.upper().startswith("UID STORE"), "инъекция: %r" % (new,))
            self.assertFalse(body.upper().startswith("NOOP"), "инъекция: %r" % (new,))
        search = [c for c in new if "UID SEARCH" in c]
        self.assertEqual(len(search), 1, new)
        # опасный текст остался ВНУТРИ кавычек — как безобидная строка поиска
        self.assertIn('TEXT "счёт  UID STORE', search[0])

    def test_crlf_in_headers_does_not_raise(self):
        res = mailbox.send(self.settings(), to="a@b.c",
                           subject="Тема\r\nBcc: evil@example.org", body="текст")
        self.assertTrue(res["ok"], res.get("reason"))
        msg = FakeSMTP.sent[-1]["msg"]
        self.assertIsNone(msg.get("Bcc"), "перевод строки создал новый заголовок Bcc")
        raw = FakeSMTP.sent[-1]["raw"].decode("utf-8", "replace")
        self.assertFalse([l for l in raw.splitlines() if l.lower().startswith("bcc:")],
                         "в письме появилась строка Bce: %r" % raw[:200])
        self.assertNotIn("\n", str(msg.get("Subject")), "в теме остался перевод строки")


# --------------------------------------------------------------------------- #
# 8. никаких исключений наружу
# --------------------------------------------------------------------------- #


class TestNoExceptions(MailTest):
    def test_bad_port_in_settings(self):
        c = mailbox.cfg({"mail_imap_host": "h", "mail_imap_port": "abc",
                         "mail_user": "u", "mail_password": "p", "mail_from": "u@h"})
        self.assertEqual(c["imap_port"], 993)
        self.assertTrue(any("порт" in p for p in mailbox.problems(c)),
                        mailbox.problems(c))

    def test_broken_settings_never_raise(self):
        for s in ({}, None, {"mail_imap_port": "x", "mail_smtp_port": [1]},
                  {"mail_timeout": "abc"}, {"mail_max_bytes": "мусор"}):
            for fn in (mailbox.cfg,):
                fn(s)
            res = mailbox.search(s or {}, days=1)
            self.assertIn("ok", res)
            self.assertIn("reason", res)
            self.assertFalse(res["ok"])

    def test_crlf_in_to_does_not_raise(self):
        res = mailbox.send(self.settings(), to="a@b.c\r\nBcc: x@y.z",
                           subject="Т", body="Б")
        self.assertIsInstance(res, dict)
        self.assertIn("ok", res)

    def test_smtp_error_returns_reason(self):
        FakeSMTP.fail_send = 1
        res = mailbox.send(self.settings(), to="a@b.c", subject="Т", body="Б")
        self.assertFalse(res["ok"])
        self.assertIn("reason", res)


# --------------------------------------------------------------------------- #
# 10. большие письма и потолок поиска
# --------------------------------------------------------------------------- #


class TestLimits(MailTest):
    def test_big_message_is_taken_partially(self):
        """Письмо больше mail_max_bytes тянется началом через BODY.PEEK[]<0.N> (пункт 10)."""
        self.imap.add(subject="Большое", uid=4, body="Начало большого письма")
        self.imap.size_override = 90 * 1024 * 1024       # сервер говорит: 90 МБ
        self.imap.big_body_pad = 0
        res = mailbox.read(self.settings(mail_max_bytes=4096), 4)
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertTrue(res.get("source_truncated"), "source_truncated не выставлен")
        self.assertIn("Начало большого письма", res["body"])
        fetches = [c for c in self.imap.commands if "FETCH" in c.upper()]
        self.assertTrue(any("<0.4096>" in c for c in fetches), fetches[-3:])

    def test_no_limit_means_full_fetch(self):
        self.imap.add(subject="Обычное", uid=4, body="целиком")
        res = mailbox.read(self.settings(mail_max_bytes=0), 4)
        self.assertTrue(res["ok"])
        self.assertFalse(res.get("source_truncated"))
        self.assertIn("целиком", res["body"])

    def test_attachment_size_without_decoding(self):
        """Размер вложения считается по длине base64, без декодирования (пункт 10)."""
        payload = b"B" * 3000
        import base64 as b64
        raw = (("From: a@b.c\r\nSubject: Вложение\r\nMIME-Version: 1.0\r\n"
                "Content-Type: multipart/mixed; boundary=BOUND\r\n\r\n"
                "--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nтело\r\n"
                "--BOUND\r\nContent-Type: application/octet-stream\r\n"
                "Content-Disposition: attachment; filename=big.bin\r\n"
                "Content-Transfer-Encoding: base64\r\n\r\n").encode("utf-8")
               + b64.encodebytes(payload) + b"\r\n--BOUND--\r\n")
        self.imap.add(uid=15, raw=raw)
        res = mailbox.read(self.settings(), 15)
        self.assertTrue(res["ok"], res.get("reason"))
        att = res["attachments"][0]
        self.assertEqual(att["name"], "big.bin")
        self.assertEqual(att["size"], len(payload), att["size"])

    def test_search_cap(self):
        """Потолок разбора: 1500 uid, mail_search_cap=1000 → capped=True (пункт 10)."""
        self.imap.add(subject="Одно", uid=1)
        self.imap.synth_uids = True
        self.imap.extra_search_uids = list(range(100, 1600))
        res = mailbox.search(self.settings(mail_search_cap=1000), days=0, limit=5000)
        self.assertTrue(res.get("capped"), "capped не выставлен")
        self.assertEqual(len(res["messages"]), 1000)
        self.assertIn("потолок", res["reason"])

    def test_search_cap_zero_is_unlimited(self):
        self.imap.add(subject="Одно", uid=1)
        self.imap.synth_uids = True
        self.imap.extra_search_uids = list(range(100, 260))
        res = mailbox.search(self.settings(mail_search_cap=0), days=0, limit=5000)
        self.assertFalse(res.get("capped"))
        self.assertEqual(len(res["messages"]), 161)


# --------------------------------------------------------------------------- #
# 11. мелочи: SINCE, повтор подключения, reply_to, пароль
# --------------------------------------------------------------------------- #


class TestSmallThings(MailTest):
    def test_since_uses_english_months(self):
        """SINCE с фиксированными английскими месяцами, локаль не влияет (пункт 11)."""
        import datetime as _dt
        RU = ("янв", "фев", "мар", "апр", "мая", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек")

        class RuDatetime(mailbox.datetime):
            """datetime, который ведёт себя как при русской локали."""
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 5, 12, 0, 0)

            def strftime(self, fmt):
                if "%b" in fmt:
                    return "%02d-%s-%04d" % (self.day, RU[self.month - 1], self.year)
                return mailbox.datetime.strftime(self, fmt)

        orig = mailbox.datetime
        mailbox.datetime = RuDatetime
        try:
            crit = mailbox._criteria(days=30)
        finally:
            mailbox.datetime = orig
        text = b" ".join(crit).decode()
        expect_day = _dt.date(2026, 11, 5) - _dt.timedelta(days=30)
        expect = "SINCE %02d-%s-%04d" % (expect_day.day,
                                         mailbox.MONTHS[expect_day.month - 1],
                                         expect_day.year)
        self.assertIn(expect, text, text)
        self.assertNotIn("окт", text)
        self.assertNotIn("ноя", text)

    def test_one_retry_on_connect(self):
        """Временная ошибка подключения: ровно один повтор (пункт 11)."""
        FakeSMTP.fail_connects = 1
        res = mailbox.send(self.settings(), to="a@b.c", subject="Т", body="Б")
        self.assertTrue(res["ok"], res.get("reason"))
        self.assertEqual(FakeSMTP.connects, 2, "ожидался один повтор подключения")
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_no_retry_on_send(self):
        """Отправку не повторяем: иначе дубликаты (пункт 11)."""
        FakeSMTP.fail_send = 1
        res = mailbox.send(self.settings(), to="a@b.c", subject="Т", body="Б")
        self.assertFalse(res["ok"])
        self.assertEqual(FakeSMTP.send_calls, 1, "письмо отправлялось повторно")
        self.assertEqual(len(FakeSMTP.sent), 0)

    def test_reply_to_keeps_old_meaning_and_adds_addr(self):
        res = mailbox.send(self.settings(reply_to_addr="team@example.org"),
                           to="a@b.c", subject="Re", body="Б",
                           reply_to="<prev@example.org>")
        self.assertTrue(res["ok"], res.get("reason"))
        msg = FakeSMTP.sent[-1]["msg"]
        self.assertEqual(msg["In-Reply-To"], "<prev@example.org>")
        self.assertEqual(msg["Reply-To"], "team@example.org")

    def test_password_never_in_reason(self):
        real = imaplib.IMAP4

        def fake(host, port=143, timeout=None):
            raise real.error(
                "AUTH failed for password app-password-42 (login agent@example.org)")

        imaplib.IMAP4 = fake
        try:
            res = mailbox.search(self.settings(), days=1)
        finally:
            imaplib.IMAP4 = real
        self.assertFalse(res["ok"])
        self.assertNotIn("app-password-42", res["reason"], res["reason"])
        self.assertIn("***", res["reason"])


# --------------------------------------------------------------------------- #
# полный доступ: без белых списков и ограничений папок
# --------------------------------------------------------------------------- #


class TestMailTestText(unittest.TestCase):
    """Кнопка «Проверить связь» не должна врать про количество писем."""

    def setUp(self):
        self.imap = FakeIMAP()
        self.addCleanup(self.imap.stop)
        self.cfg = {"mail_imap_host": "127.0.0.1", "mail_imap_port": self.imap.port,
                    "mail_imap_enc": "none", "mail_user": "agent@example.org",
                    "mail_password": "app-password-42", "mail_from": "agent@example.org"}

    def test_text_says_letter_opened(self):
        self.imap.add(subject="Привет", uid=1)
        res = mailbox.test(self.cfg)
        self.assertTrue(res["ok"], res)
        self.assertIn("открылось", res["text"], res["text"])
        self.assertNotIn("писем видно", res["text"], res["text"])

    def test_text_says_no_letters(self):
        res = mailbox.test(self.cfg)
        self.assertTrue(res["ok"], res)
        self.assertIn("писем в ней нет", res["text"], res["text"])


class TestNoRestrictions(MailTest):
    def test_agent_sends_to_any_addresses(self):
        """Агент отправляет на любые адреса, без белых списков и подтверждений."""
        targets = ["random-guy@nowhere.example", "a+tag@b.c", "кто-то@пример.рф",
                   "x@y.z, second@y.z"]
        for to in targets:
            res = mailbox.send(self.settings(), to=to, subject="Проверка", body="текст")
            self.assertTrue(res["ok"], "адрес %r не принят: %s" % (to, res.get("reason")))
        self.assertEqual(len(FakeSMTP.sent), 4)
        all_rcpts = [r for s in FakeSMTP.sent for r in s["to"]]
        self.assertIn("random-guy@nowhere.example", all_rcpts)
        self.assertIn("кто-то@пример.рф", all_rcpts)

    def test_agent_reads_any_folders(self):
        """Любая папка, включая нестандартную и с пробелом — без ограничений."""
        self.imap.add(subject="Спам-письмо", uid=2)
        for box in ("INBOX", "Sent Items", "Спам", "Projects/2026 Q4", "[Gmail]/All Mail"):
            res = mailbox.search(self.settings(), days=30, folder=box)
            self.assertTrue(res["ok"], "папка %r: %s" % (box, res.get("reason")))

    def test_send_allowed_by_default(self):
        """Отправка включена по умолчанию: mail_allow_send не задан — можно."""
        s = self.settings()
        s.pop("mail_allow_send")
        res = mailbox.send(s, to="a@b.c", subject="Т", body="Б")
        self.assertTrue(res["ok"], res.get("reason"))

    def test_public_api_still_returns_dicts(self):
        """Сигнатуры и форма ответа не изменились: dict с ok/reason."""
        for fn, args in ((mailbox.search, ({}, )), (mailbox.read, ({}, 1)),
                         (mailbox.folders, ({}, )), (mailbox.send, ({}, )),
                         (mailbox.test, ({}, ))):
            res = fn(*args)
            self.assertIsInstance(res, dict)
            self.assertIn("ok", res)
            if not res["ok"]:
                self.assertIn("reason", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
