#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mailbox.py — почта для агентов: IMAP (читаем и ищем) + SMTP (отправляем).

Только стандартная библиотека: imaplib, smtplib, email. Пароль (app-password)
хранится локально в базе приложения и никогда не уходит в логи: в текстах
ошибок он заменяется на «***».

Почему отдельный модуль: агенту нужны не «сырые» письма, а понятный ответ.
Поэтому каждая функция возвращает dict с полем ok, а при неудаче — reason
словами («неверный пароль», «нет связи», «папка не найдена»), а не пустой
список: модель должна понимать, что случилось, и пробовать иначе.
Исключения наружу не выходят: любая поломка превращается в {"ok": False}.

Настройки (все необязательные, значения по умолчанию в скобках):
  mail_imap_host, mail_imap_port (993), mail_imap_enc (ssl|starttls|none)
  mail_smtp_host, mail_smtp_port (465), mail_smtp_enc (ssl|starttls|none)
  mail_user, mail_password, mail_from, mail_folder (INBOX)
  mail_allow_send (1)      — агенты могут отправлять письма
  mail_timeout (15)        — секунды на соединение/ответ
  mail_tls_verify (1)      — проверять сертификат сервера (0 = выключить)
  mail_max_bytes (50 МБ)   — сколько байт письма тянуть целиком (0 = без лимита)
  mail_search_cap (1000)   — сколько uid максимум разбирать за поиск (0 = без потолка)
  mail_reply_to / reply_to_addr — адрес для заголовка Reply-To

Ограничений на адресатов, папки и число писем нет: это личный инструмент
владельца, агент работает как полноценный почтовый клиент.
"""

import base64
import imaplib
import re
import smtplib
import socket
import ssl
import time
from datetime import datetime, timedelta
from email import message_from_bytes, policy
from email.header import Header, decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parsedate_to_datetime

DEFAULT_TIMEOUT = 15
DEFAULT_MAX_BYTES = 0
DEFAULT_SEARCH_CAP = 0

# Названия месяцев для IMAP SINCE — фиксированные английские.
# Локаль (strftime("%b")) использовать нельзя: в русской локали будет «05-ноя-2026»,
# а IMAP понимает только имена из спецификации.
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

CERT_REASON = "сертификат сервера не прошёл проверку (можно отключить mail_tls_verify)"


# --------------------------------------------------------------------------- #
# настройки
# --------------------------------------------------------------------------- #


def _to_int(value, default, errors=None, name=""):
    """Безопасно превращаем значение в int: «abc» или 12.7 не должны бросать ValueError."""
    if value is None or value == "":
        return default
    try:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int,)):
            return int(value)
        if isinstance(value, float):
            return int(value)
        text = str(value).strip().replace(" ", "")
        if not text:
            return default
        return int(text, 10)
    except (TypeError, ValueError):
        if errors is not None:
            errors.append("неверное число%s: %r (взято %s)" % (
                (" «%s»" % name) if name else "", value, default))
        return default


def cfg(settings):
    """Достаём настройки почты из общих настроек приложения. Никогда не бросает."""
    s = settings or {}
    errors = []
    try:
        user = str(s.get("mail_user") or "").strip()
        imap_host = str(s.get("mail_imap_host") or "").strip()
        smtp_host = str(s.get("mail_smtp_host") or "").strip() or imap_host
        return {
            "imap_host": imap_host,
            "imap_port": _to_int(s.get("mail_imap_port"), 993, errors, "порт IMAP"),
            "imap_enc": str(s.get("mail_imap_enc") or "ssl").lower(),
            "smtp_host": smtp_host,
            "smtp_port": _to_int(s.get("mail_smtp_port"), 465, errors, "порт SMTP"),
            "smtp_enc": str(s.get("mail_smtp_enc") or "ssl").lower(),
            "user": user,
            "password": str(s.get("mail_password") or ""),
            "sender": str(s.get("mail_from") or "").strip() or user,
            "folder": str(s.get("mail_folder") or "INBOX").strip() or "INBOX",
            "allow_send": bool(_to_int(s.get("mail_allow_send", 1), 1)),
            "timeout": _to_int(s.get("mail_timeout"), DEFAULT_TIMEOUT, errors, "mail_timeout"),
            "tls_verify": bool(_to_int(s.get("mail_tls_verify", 1), 1)),
            "max_bytes": _to_int(s.get("mail_max_bytes"), DEFAULT_MAX_BYTES, errors, "mail_max_bytes"),
            "search_cap": _to_int(s.get("mail_search_cap"), DEFAULT_SEARCH_CAP, errors, "mail_search_cap"),
            "reply_to_addr": str(s.get("reply_to_addr") or s.get("mail_reply_to") or "").strip(),
            "errors": errors,
        }
    except Exception as e:  # noqa: BLE001 — конфиг не должен ронять вызывающего
        return {
            "imap_host": "", "imap_port": 993, "imap_enc": "ssl",
            "smtp_host": "", "smtp_port": 465, "smtp_enc": "ssl",
            "user": "", "password": "", "sender": "", "folder": "INBOX",
            "allow_send": True, "timeout": DEFAULT_TIMEOUT, "tls_verify": True,
            "max_bytes": DEFAULT_MAX_BYTES, "search_cap": DEFAULT_SEARCH_CAP,
            "reply_to_addr": "",
            "errors": ["настройки не прочитались: %s" % type(e).__name__],
        }


def problems(c):
    """Чего не хватает для работы — списком, по-человечески."""
    out = []
    c = c or {}
    if not c.get("imap_host"):
        out.append("не задан IMAP-сервер (Настройки → Почта)")
    if not c.get("user"):
        out.append("не задан логин почты")
    if not c.get("password"):
        out.append("не задан пароль приложения")
    if not c.get("sender"):
        out.append("не задан адрес отправителя")
    for bad in c.get("errors") or []:
        out.append(str(bad))
    for key, name in (("imap_port", "IMAP"), ("smtp_port", "SMTP")):
        port = c.get(key)
        if not isinstance(port, int) or not (0 < port < 65536):
            out.append("неверный порт %s: %r" % (name, port))
    return out


def ready(settings):
    return not problems(cfg(settings))


# --------------------------------------------------------------------------- #
# мелочи: кодировки, папки, заголовки
# --------------------------------------------------------------------------- #


def _imap_utf7(name):
    """Имена папок в IMAP — modified UTF-7 («Отправленные» → «&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-»)."""
    out = []
    buf = ""

    def flush():
        nonlocal buf
        if not buf:
            return
        raw = buf.encode("utf-16-be")
        b64 = base64.b64encode(raw).decode("ascii").rstrip("=")
        out.append("&" + b64.replace("/", ",") + "-")
        buf = ""

    for ch in str(name or ""):
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def _imap_unutf7(name):
    name = str(name or "")
    if "&" not in name:
        return name

    def repl(m):
        body = m.group(1)
        if body == "":
            return "&"
        pad = "=" * (-len(body) % 4)
        try:
            return base64.b64decode(body.replace(",", "/") + pad).decode("utf-16-be", "replace")
        except Exception:  # noqa: BLE001
            return m.group(0)

    return re.sub(r"&([^-]*)-", repl, name)


def _clean(value):
    """
    Убираем управляющие символы. \\r\\n в query/папке/теме — это новая IMAP-команда
    (инъекция), поэтому всё, что меньше 0x20, превращается в пробел.
    """
    text = "" if value is None else str(value)
    text = "".join(" " if (ord(ch) < 0x20 or ord(ch) == 0x7F) else ch for ch in text)
    return text.strip()


def _quote(text):
    """Кавычки для IMAP: всегда bytes, чтобы работали русские запросы."""
    raw = _clean(text).replace("\\", "\\\\").replace('"', '\\"')
    return b'"' + raw.encode("utf-8") + b'"'


def _mailbox_arg(name):
    """
    Аргумент SELECT/LIST. imaplib не ставит кавычки сам, а «Sent Items»,
    «[Gmail]/Sent Mail» без кавычек — синтаксическая ошибка на сервере.
    Поэтому имя всегда в кавычках, с экранированием \\ и ".
    """
    raw = _imap_utf7(_clean(name)).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + raw + '"'


def _decode_bytes(raw, charset=None):
    """
    Текст из байтов: сначала заявленная кодировка, потом utf-8, cp1251,
    и в конце utf-8 с errors="replace". Так письма с x-unknown и unknown-8bit
    не превращаются в пустую строку.
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    seen = set()
    for enc in (charset, "utf-8", "cp1251"):
        if not enc:
            continue
        key = str(enc).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError, TypeError):
            continue
    return raw.decode("utf-8", "replace")


def _has_surrogates(text):
    """policy.compat32 отдаёт «сырые» байты как суррогаты — их надо вернуть в текст."""
    return any(0xDC80 <= ord(ch) <= 0xDCFF for ch in text)


def _header_block(raw):
    """Байты блока заголовков письма (до первой пустой строки)."""
    if not raw:
        return b""
    for sep in (b"\r\n\r\n", b"\n\n"):
        idx = raw.find(sep)
        if idx >= 0:
            return raw[:idx]
    return raw


def _hdr_bytes(raw):
    """Значение заголовка из байтов: ASCII, «сырой» UTF-8, cp1251, =?..?=."""
    if not raw:
        return ""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "surrogateescape")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        text = _decode_bytes(raw)          # utf-8 → cp1251 → utf-8 с replace
    if "=?" in text:
        try:
            return str(make_header(decode_header(text))).strip()
        except Exception:  # noqa: BLE001
            return text.strip()
    return text.strip()


def _headers(raw):
    """
    Заголовки письма как {имя: [значения]} с правильными кодировками.
    Разбираем из байтов сами: policy.compat32 отдаёт «сырой» UTF-8 как \ufffd,
    а policy.default спасает не всё (например cp1251 без =?..?=).
    """
    out = {}
    try:
        cur = None
        for line in re.split(rb"\r\n|\n", _header_block(raw)):
            if not line:
                continue
            if line[:1] in (b" ", b"\t"):          # продолжение длинного заголовка
                if cur and out.get(cur):
                    out[cur][-1] += b" " + line.strip()
                continue
            if b":" not in line:
                continue
            name, value = line.split(b":", 1)
            cur = name.strip().lower().decode("ascii", "replace")
            out.setdefault(cur, []).append(value.strip())
    except Exception:  # noqa: BLE001
        return {}
    return {k: [_hdr_bytes(v) for v in vals] for k, vals in out.items()}


def _hdr_from(heads, name, default=""):
    vals = (heads or {}).get(name.lower()) or []
    return vals[0] if vals else default


def _hdr(value):
    """
    Заголовок письма в любом виде: =?UTF-8?B?...?=, «сырой» UTF-8 (без кодирования),
    cp1251, unknown-8bit. Возвращаем читаемую строку, а не «».
    """
    if value is None:
        return ""
    try:
        if isinstance(value, bytes):
            return _hdr_bytes(value)
        if isinstance(value, Header):
            # так policy.compat32 отдаёт заголовки с «сырыми» байтами
            try:
                return _hdr_bytes(value.encode(linesep="").encode("ascii", "surrogateescape"))
            except Exception:  # noqa: BLE001
                return str(value)
        text = str(value)
        if _has_surrogates(text):
            # вернуть исходные байты и декодировать правильно (utf-8 → cp1251 → replace)
            return _hdr_from_str(text.encode("utf-8", "surrogateescape")
                                 .decode("ascii", "surrogateescape"))
        return _hdr_from_str(text)
    except Exception:  # noqa: BLE001
        try:
            return str(value).strip()
        except Exception:  # noqa: BLE001
            return ""


def _hdr_from_str(text):
    text = text or ""
    if _has_surrogates(text):
        text = _decode_bytes(text.encode("utf-8", "surrogateescape"))
    if "=?" not in text:
        return text.strip()
    try:
        return str(make_header(decode_header(text))).strip()
    except Exception:  # noqa: BLE001
        return text.strip()


def _date(value):
    try:
        dt = parsedate_to_datetime(str(value))
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        pass
    return _clean(value)[:40]


def _strip_html(raw):
    txt = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    txt = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", txt)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt)
    txt = (txt.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
              .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    txt = re.sub(r"[ \t\u00a0]+", " ", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def _part_text(part):
    """Текст части письма с fallback по кодировкам."""
    try:
        raw = part.get_payload(decode=True)
    except Exception:  # noqa: BLE001
        raw = None
    if raw is None:
        payload = part.get_payload(decode=False)
        if isinstance(payload, str):
            return payload
        return ""
    try:
        charset = part.get_content_charset()
    except Exception:  # noqa: BLE001
        charset = None
    return _decode_bytes(raw, charset)


def _body_of(msg):
    """Текст письма: text/plain в приоритете, HTML — как fallback."""
    plain, html = [], []
    for part in msg.walk():
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        disp = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue
        if ctype not in ("text/plain", "text/html"):
            continue
        text = _part_text(part)
        if text:
            (plain if ctype == "text/plain" else html).append(text)
    if plain:
        return "\n".join(plain).strip()
    if html:
        return _strip_html("\n".join(html))
    return ""


def _payload_size(part):
    """
    Размер вложения без декодирования: считаем по длине base64.
    Иначе read() декодирует каждое вложение только ради len().
    """
    try:
        raw = part.get_payload(decode=False)
    except Exception:  # noqa: BLE001
        return 0
    if isinstance(raw, list) or raw is None:
        return 0
    text = str(raw)
    cte = str(part.get("Content-Transfer-Encoding") or "").lower()
    if "base64" in cte:
        clean = re.sub(r"\s+", "", text)
        pad = clean.count("=")
        return max(0, len(clean) * 3 // 4 - pad)
    try:
        charset = part.get_content_charset() or "utf-8"
        return len(text.encode(charset, "replace"))
    except Exception:  # noqa: BLE001
        return len(text.encode("utf-8", "replace"))


def _attachments(msg):
    out = []
    for part in msg.walk():
        if "attachment" in str(part.get("Content-Disposition") or "").lower():
            out.append({"name": _hdr(part.get_filename()) or "файл",
                        "size": _payload_size(part)})
    return out


# --------------------------------------------------------------------------- #
# IMAP
# --------------------------------------------------------------------------- #


def _ssl_context(c):
    """Контекст TLS: по умолчанию проверяем сертификат, mail_tls_verify=0 — выключаем."""
    ctx = ssl.create_default_context()
    if not c.get("tls_verify", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _retry_connect(open_fn, attempts=2, delay=1.0):
    """
    Один повтор при временной ошибке соединения на этапе подключения.
    Отправку письма не повторяем никогда — иначе получатся дубликаты.
    """
    last = None
    for i in range(attempts):
        try:
            return open_fn()
        except (ConnectionError, socket.timeout, TimeoutError) as e:
            last = e
            if i + 1 < attempts:
                time.sleep(delay)
    raise last


def _open_imap(c):
    host, port, enc = c["imap_host"], c["imap_port"], c["imap_enc"]
    t = c["timeout"]
    ctx = _ssl_context(c)

    def connect():
        if enc == "none":
            conn = imaplib.IMAP4(host, port, timeout=t)
        elif enc == "starttls":
            conn = imaplib.IMAP4(host, port, timeout=t)
            conn.starttls(ctx)
        else:
            conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=t)
        try:
            conn.login(c["user"], c["password"])
        except Exception:
            _close(conn)
            raise
        return conn

    return _retry_connect(connect)


def _since_text(days):
    """SINCE dd-Mon-yyyy с фиксированными английскими месяцами (локаль не влияет)."""
    since = datetime.now() - timedelta(days=days)
    return "%02d-%s-%04d" % (since.day, MONTHS[since.month - 1], since.year)


def _criteria(query="", sender="", subject="", days=7, unread=False):
    crit = []
    if unread:
        crit.append(b"UNSEEN")
    days = _to_int(days, 0)
    if days > 0:
        crit.append(("SINCE %s" % _since_text(days)).encode())
    sender = _clean(sender)
    subject = _clean(subject)
    query = _clean(query)
    if sender:
        crit.append(b"FROM " + _quote(sender))
    if subject:
        crit.append(b"SUBJECT " + _quote(subject))
    if query:
        crit.append(b"TEXT " + _quote(query))
    if not crit:
        crit.append(b"ALL")
    return crit


def _uids(conn, crit, limit, cap=0):
    """Список uid, свежие первыми. cap>0 — не разбирать больше, чем cap (capped=True)."""
    args = list(crit)
    # русские слова в критериях — только с CHARSET UTF-8, иначе сервер молча ищет не то
    if any(any(b > 127 for b in a) for a in args):
        args = [b"CHARSET", b"UTF-8"] + args
    typ, data = conn.uid("SEARCH", *args)
    if typ != "OK":
        raise RuntimeError("сервер отказал в поиске: %s" % (_scrub(repr(data)),))
    raw = b" ".join(x for x in data if isinstance(x, (bytes, bytearray)))
    ids = sorted({int(x) for x in raw.split() if x.isdigit()}, reverse=True)
    capped = False
    if cap and len(ids) > cap:
        ids = ids[:cap]
        capped = True
    return ids[:max(1, _to_int(limit, 10))], capped


def _fetch_records(data):
    """
    Разбираем ответ FETCH на записи. Сервер вправе поставить UID и FLAGS как до тела,
    так и после литерала — поэтому метаданные собираем из всего ответа, кроме тела.
    """
    records, cur = [], None
    for item in (data or []):
        if isinstance(item, tuple):
            head = item[0] if len(item) > 0 else b""
            literal = item[1] if len(item) > 1 else b""
            head = head or b""
            if cur is not None and re.match(rb"\d+\s+\(", head):
                records.append(cur)
                cur = None
            if cur is None:
                cur = {"meta": b"", "body": b""}
            cur["meta"] += head
            cur["body"] += literal or b""
        elif isinstance(item, (bytes, bytearray)):
            chunk = bytes(item)
            if re.match(rb"\s*\d+\s+\(", chunk):
                if cur is not None:
                    records.append(cur)
                cur = {"meta": chunk, "body": b""}
            elif cur is not None:
                cur["meta"] += chunk
            else:
                cur = {"meta": chunk, "body": b""}
    if cur is not None:
        records.append(cur)
    return records


def _close(conn):
    if not conn:
        return
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


_LAST_PASSWORD = [""]


def _scrub(text, password=None):
    """Пароль не должен попадать в reason и в текст исключения."""
    out = str(text if text is not None else "")
    pw = password
    if pw is None:
        try:
            pw = _LAST_PASSWORD[0]
        except Exception:  # noqa: BLE001
            pw = ""
    if pw and len(str(pw)) >= 3:
        out = out.replace(str(pw), "***")
    return out


def _err(e, c=None):
    text = _scrub(str(e) or type(e).__name__, (c or {}).get("password"))
    low = text.lower()
    if "certificate_verify_failed" in low or "certificate verify failed" in low \
            or "certificateerror" in low or isinstance(e, ssl.SSLCertVerificationError):
        return CERT_REASON
    if "authenticationfailed" in low or "invalid credentials" in low or "login failed" in low:
        return "не пускает: проверьте логин и пароль приложения"
    if "certificate" in low:
        return CERT_REASON
    if "name or service not known" in low or "getaddrinfo" in low or "nodename nor servname" in low:
        return "сервер не найден: %s" % text[:120]
    if "refused" in low:
        return "сервер отказал в соединении (порт закрыт?)"
    if "ssl" in low:
        return "ошибка TLS: %s" % text[:120]
    return text[:200]


def _fail(ease, c=None, where="IMAP"):
    """Единый ответ на любую ошибку: наружу не летит исключение."""
    if isinstance(ease, ssl.SSLCertVerificationError):
        return {"ok": False, "reason": CERT_REASON}
    if isinstance(ease, (socket.timeout, TimeoutError)):
        return {"ok": False, "reason": "%s не ответил за %s с" % (where, (c or {}).get("timeout"))}
    if isinstance(ease, (ssl.SSLError, OSError)):
        host = (c or {}).get("imap_host" if where == "IMAP" else "smtp_host")
        return {"ok": False, "reason": "нет связи с %s (%s): %s" % (where, host, _err(ease, c))}
    return {"ok": False, "reason": "%s: %s" % (where, _err(ease, c))}


def search(settings, query="", sender="", subject="", days=7, unread=False,
           limit=10, folder="", timeout=None):
    """
    Поиск писем. Возвращает {"ok": True, "messages": [...]} либо
    {"ok": False, "reason": "..."} — чтобы агент понимал, почему пусто.

    Папка может быть любой, в том числе с пробелом («Sent Items», «[Gmail]/Sent Mail»):
    имя экранируется и берётся в кавычки. Ограничений на папки нет.
    """
    c = cfg(settings)
    _LAST_PASSWORD[0] = c.get("password") or ""
    miss = problems(c)
    if miss:
        return {"ok": False, "reason": _scrub("почта не настроена: " + "; ".join(miss)), "messages": []}
    box = _clean(folder or c["folder"] or "INBOX")
    query, sender, subject = _clean(query), _clean(sender), _clean(subject)
    conn = None
    try:
        conn = _open_imap(c)
        typ, _ = conn.select(_mailbox_arg(box), readonly=True)
        if typ != "OK":
            return {"ok": False, "reason": "папка «%s» не открылась" % box, "messages": []}
        uids, capped = _uids(conn, _criteria(query, sender, subject, days, unread),
                             limit, cap=c["search_cap"])
        if not uids:
            return {"ok": True, "messages": [], "folder": box,
                    "reason": "под эти условия писем нет (папка «%s», %s)"
                              % (box, "за %s дн." % days if days else "без ограничения по дате")}
        typ, data = conn.uid("FETCH", b",".join(str(u).encode() for u in uids).decode(),
                             "(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
        if typ != "OK":
            return {"ok": False, "reason": "сервер не отдал заголовки писем", "messages": []}
        out, skipped = [], []
        for rec in _fetch_records(data):
            meta = rec["meta"]
            m = re.search(rb"UID (\d+)", meta)
            if not m:
                # без uid письмо бесполезно: агенту нечего открывать. Причина — словами.
                subject_hint = ""
                try:
                    subject_hint = _hdr_from(_headers(rec["body"] or b""), "subject")
                except Exception:  # noqa: BLE001
                    pass
                skipped.append(subject_hint or "(без темы)")
                continue
            uid = int(m.group(1))
            body_raw = rec["body"] or b""
            heads = _headers(body_raw)
            flags = re.search(rb"FLAGS \(([^)]*)\)", meta)
            size = re.search(rb"RFC822\.SIZE (\d+)", meta)
            flag_txt = flags.group(1).decode("utf-8", "replace") if flags else ""
            out.append({
                "uid": uid,
                "from": _hdr_from(heads, "from"),
                "to": _hdr_from(heads, "to"),
                "subject": _hdr_from(heads, "subject") or "(без темы)",
                "date": _date(_hdr_from(heads, "date")),
                "unread": "\\Seen" not in flag_txt,
                "size": int(size.group(1)) if size else 0,
                "folder": box,
            })
        out.sort(key=lambda x: x["uid"], reverse=True)
        res = {"ok": True, "messages": out, "folder": box, "count": len(out)}
        if capped:
            res["capped"] = True
            res["reason"] = ("писем больше потолка разбора (%d) — показаны самые свежие; "
                             "сузьте запрос (mail_search_cap=0 снимает потолок)" % c["search_cap"])
        if skipped:
            res["skipped"] = skipped
            res["skipped_reason"] = ("%d писем сервер отдал без UID — открыть их нельзя, "
                                     "пропущены: %s" % (len(skipped), ", ".join(skipped[:5])))
            if not out:
                res["ok"] = False
                res["reason"] = res["skipped_reason"]
        return res
    except Exception as e:  # noqa: BLE001 — наружу ничего не летит
        res = _fail(e, c, "IMAP")
        res.setdefault("messages", [])
        return res
    finally:
        _close(conn)


def read(settings, uid, folder=""):
    """
    Письмо по uid: заголовки, текст, вложения (имена и размеры).

    Если письмо больше mail_max_bytes, тянем только начало (BODY.PEEK[]<0.N>)
    и ставим source_truncated=True — текст всё равно достаётся, а гигабайты
    вложений не грузятся в память.
    """
    c = cfg(settings)
    _LAST_PASSWORD[0] = c.get("password") or ""
    miss = problems(c)
    if miss:
        return {"ok": False, "reason": _scrub("почта не настроена: " + "; ".join(miss))}
    box = _clean(folder or c["folder"] or "INBOX")
    conn = None
    try:
        conn = _open_imap(c)
        typ, _ = conn.select(_mailbox_arg(box), readonly=True)
        if typ != "OK":
            return {"ok": False, "reason": "папка «%s» не открылась" % box}
        uid = _to_int(uid, 0)
        if not uid:
            return {"ok": False, "reason": "неверный uid: %r" % (uid,)}
        source_truncated = False
        size = 0
        limit = c["max_bytes"]
        try:
            typ, sdata = conn.uid("FETCH", str(uid), "(RFC822.SIZE)")
            if typ == "OK":
                recs = _fetch_records(sdata)
                m = None
                for rec in recs:
                    m = m or re.search(rb"RFC822\.SIZE (\d+)", rec["meta"])
                size = int(m.group(1)) if m else 0
        except Exception:  # noqa: BLE001
            size = 0
        if limit and size and size > limit:
            typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[]<0.%d>)" % limit)
            source_truncated = True
        else:
            typ, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            return {"ok": False, "reason": "письмо %s не найдено в «%s»" % (uid, box)}
        raw = b""
        for rec in _fetch_records(data):
            raw += rec["body"] or b""
        if not raw:
            return {"ok": False, "reason": "письмо %s пустое или удалено" % uid}
        msg = message_from_bytes(raw, policy=policy.default)
        heads = _headers(raw)
        body = _body_of(msg)
        if not body and source_truncated:
            body = _decode_bytes(raw)
        return {"ok": True, "uid": uid, "folder": box, "size": size,
                "from": _hdr_from(heads, "from"), "to": _hdr_from(heads, "to"),
                "cc": _hdr_from(heads, "cc"),
                "subject": _hdr_from(heads, "subject") or "(без темы)",
                "date": _date(_hdr_from(heads, "date")),
                "message_id": _clean(_hdr_from(heads, "message-id")),
                "attachments": _attachments(msg),
                "body": body, "truncated": source_truncated,
                "source_truncated": source_truncated}
    except Exception as e:  # noqa: BLE001
        return _fail(e, c, "IMAP")
    finally:
        _close(conn)


def folders(settings):
    """Список папок: агент должен видеть, где искать («Отправленные», «Спам»...)."""
    c = cfg(settings)
    _LAST_PASSWORD[0] = c.get("password") or ""
    miss = problems(c)
    if miss:
        return {"ok": False, "reason": _scrub("почта не настроена: " + "; ".join(miss)), "folders": []}
    conn = None
    try:
        conn = _open_imap(c)
        typ, data = conn.list()
        names = []
        for row in data or []:
            if not isinstance(row, (bytes, bytearray)):
                continue
            row = bytes(row).strip()
            m = re.search(rb'(?:"|NIL) ?(?:"([^"]*)"|([^ ]+))$', row)
            raw = (m.group(1) or m.group(2)) if m else b""
            if raw:
                names.append(_imap_unutf7(_decode_bytes(raw)))
        return {"ok": True, "folders": names or [c["folder"]]}
    except Exception as e:  # noqa: BLE001
        res = _fail(e, c, "IMAP")
        res["folders"] = []
        return res
    finally:
        _close(conn)


# --------------------------------------------------------------------------- #
# SMTP
# --------------------------------------------------------------------------- #


def _header_value(text, charset_fallback=True):
    """Значение заголовка без CR/LF: перевод строки внутри темы = инъекция заголовка."""
    return _clean(text)


def _rcpt_list(to, cc):
    """
    Получатели. Имя с запятой («Doe, John» <j@x.com>) не должно разваливаться на два
    адреса, поэтому разбираем через email.utils.getaddresses, а не режем по запятым.
    """
    raw = (to or "") + ", " + (cc or "")
    raw = raw.replace(";", ",")
    out = []
    for _name, addr in getaddresses([raw]):
        addr = (addr or "").strip()
        if addr and addr not in out:
            out.append(addr)
    return out


def send(settings, to="", subject="", body="", cc="", reply_to=""):
    """
    Отправить письмо. Ограничений на адресата нет — это личный инструмент владельца:
    любой адрес, любое число получателей, без белых списков и подтверждений.

    Два разных «reply»:
      * reply_to — Message-ID письма, на которое отвечаем: он попадает в
        In-Reply-To и References, чтобы клиент получателя собрал ветку;
      * адрес для заголовка Reply-To («ответить сюда») задаётся настройкой
        mail_reply_to или ключом reply_to_addr в settings — это не то же самое.

    Повтора отправки нет: при обрыве связи письмо не уходит второй раз (без дубликатов).
    """
    c = cfg(settings)
    _LAST_PASSWORD[0] = c.get("password") or ""
    miss = problems(c)
    if miss:
        return {"ok": False, "reason": _scrub("почта не настроена: " + "; ".join(miss))}
    if not c["allow_send"]:
        return {"ok": False, "reason": "отправка писем выключена в настройках (Почта → «Агенты могут писать письма»)"}
    to = _header_value(to)
    if not to:
        return {"ok": False, "reason": "не указан адресат (to)"}
    if not str(subject or "").strip() and not str(body or "").strip():
        return {"ok": False, "reason": "пустое письмо: нет ни темы, ни текста"}

    rcpts = _rcpt_list(to, cc)
    if not rcpts:
        return {"ok": False, "reason": "не разобрал ни одного адреса в «%s»" % to[:120]}

    srv = None
    try:
        # ---- письмо: любые ошибки заголовков превращаем в reason, а не в исключение ----
        msg = EmailMessage()
        msg["From"] = _header_value(c["sender"])
        msg["To"] = to
        msg["Subject"] = _header_value(subject) or "(без темы)"
        msg["Date"] = formatdate(localtime=True)      # с часовым поясом, не «UTC по умолчанию»
        msg["Message-ID"] = make_msgid(domain=((c["sender"].split("@")[-1] or "").strip()
                                               or "mrcagent.local"))
        if cc:
            msg["Cc"] = _header_value(cc)
        if reply_to:
            # старое поведение: это Message-ID, на который отвечаем
            msg["In-Reply-To"] = _clean(reply_to)
            msg["References"] = _clean(reply_to)
        if c.get("reply_to_addr"):
            msg["Reply-To"] = _header_value(c["reply_to_addr"])
        msg.set_content(body or "")

        # ---- соединение: один повтор только на подключении ----
        host, port, enc = c["smtp_host"], c["smtp_port"], c["smtp_enc"]
        ctx = _ssl_context(c)

        def connect():
            if enc == "none":
                s = smtplib.SMTP(host, port, timeout=c["timeout"])
            elif enc == "starttls":
                s = smtplib.SMTP(host, port, timeout=c["timeout"])
                s.starttls(context=ctx)
            else:
                s = smtplib.SMTP_SSL(host, port, timeout=c["timeout"], context=ctx)
            if c["user"]:
                s.login(c["user"], c["password"])
            return s

        srv = _retry_connect(connect)
        # ---- сама отправка: один раз, без повторов ----
        srv.send_message(msg, from_addr=c["sender"], to_addrs=rcpts)
        return {"ok": True, "to": to, "cc": cc, "rcpts": rcpts, "subject": str(msg["Subject"]),
                "message_id": str(msg["Message-ID"]), "bytes": len(bytes(msg))}
    except smtplib.SMTPAuthenticationError as e:
        return {"ok": False, "reason": _scrub("SMTP не пускает: %s (нужен пароль приложения)" % _err(e, c))}
    except ValueError as e:
        # например «header value appears to contain an embedded header»
        return {"ok": False, "reason": "письмо не собралось: %s" % _scrub(e)}
    except smtplib.SMTPException as e:
        return {"ok": False, "reason": _scrub("SMTP: %s" % _err(e, c))}
    except Exception as e:  # noqa: BLE001
        return _fail(e, c, "SMTP")
    finally:
        try:
            if srv:
                srv.quit()
        except Exception:  # noqa: BLE001
            pass


def test(settings):
    """Кнопка «Проверить связь»: заходим по IMAP и берём одно письмо."""
    c = cfg(settings)
    _LAST_PASSWORD[0] = c.get("password") or ""
    miss = problems(c)
    if miss:
        return {"ok": False, "reason": _scrub("почта не настроена: " + "; ".join(miss))}
    res = search(settings, days=0, limit=1)
    if not res.get("ok"):
        return res
    n = len(res.get("messages") or [])
    # «писем видно 1» читалось как «в ящике одно письмо» — говорим точнее:
    if n:
        text = "связь есть: папка «%s», письмо открылось (показано последнее)" % res.get("folder")
    else:
        text = "связь есть: папка «%s», писем в ней нет" % res.get("folder")
    return {"ok": True, "folder": res.get("folder"), "messages": n,
            "smtp": c["smtp_host"], "send_allowed": c["allow_send"],
            "tls_verify": c["tls_verify"], "text": text}
