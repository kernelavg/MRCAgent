#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auth.py — собственная регистрация: ник + пароль, всё локально.

Что делает:
  • register(nick, password)  — создаёт аккаунт, ник уникален без учёта регистра;
  • login(nick, password)     — проверяет пароль и выдаёт сессию;
  • rename(user_id, nick)     — сменить ник, не теряя проекты;
  • change_password(...)      — сменить пароль, зная текущий.

Пароли не хранятся: только PBKDF2-HMAC-SHA256 (260k итераций) + случайная соль,
формат строки "pbkdf2_sha256$итерации$соль$хэш". Сравнение — compare_digest.
Никакой почты, никаких внешних сервисов: аккаунт живёт в data/studio.db.
"""

import hashlib
import hmac
import re
import secrets
import time

import db

ITER = 260_000
NICK_MIN, NICK_MAX = 2, 32
PASS_MIN = 6
NICK_RE = re.compile(r"^[\w .\-]+$", re.UNICODE)


# --------------------------------------------------------------------------- #
# ники
# --------------------------------------------------------------------------- #

def normalize(nick):
    return re.sub(r"\s+", " ", (nick or "").strip())[:NICK_MAX]


def nick_key(nick):
    """Ключ уникальности: регистр и лишние пробелы не важны."""
    return "u%s@nick.local" % hashlib.sha1(
        re.sub(r"\s+", " ", normalize(nick).casefold()).encode("utf-8")).hexdigest()[:16]


def check_nick(nick):
    nick = normalize(nick)
    if not nick:
        raise ValueError("введите никнейм")
    if len(nick) < NICK_MIN:
        raise ValueError("ник короче %d символов" % NICK_MIN)
    if not nick.strip(" .-_"):
        raise ValueError("ник из одних точек и палочек — так нельзя")
    if not NICK_RE.match(nick):
        raise ValueError("в нике можно буквы, цифры, пробел, точку, дефис и подчёркивание")
    return nick


def check_password(password):
    if not password or len(password) < PASS_MIN:
        raise ValueError("пароль короче %d символов" % PASS_MIN)
    if len(password) > 200:
        raise ValueError("пароль слишком длинный")
    return password


# --------------------------------------------------------------------------- #
# пароли
# --------------------------------------------------------------------------- #

def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITER)
    return "pbkdf2_sha256$%d$%s$%s" % (ITER, salt, digest.hex())


def verify_password(password, stored):
    if not stored:
        return False
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                   bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(calc.hex(), digest)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# аккаунты
# --------------------------------------------------------------------------- #

def get_by_nick(nick):
    return db.one("SELECT * FROM users WHERE email = ?", (nick_key(nick),))


def register(nick, password):
    """
    Создать аккаунт. Возвращает (user, token, claimed).
    claimed=True — ник остался от старой версии (вход по одному нику), пароль задан сейчас.
    """
    nick = check_nick(nick)
    check_password(password)
    user = get_by_nick(nick)
    if user and user.get("password_hash"):
        raise ValueError("ник «%s» уже занят — выберите другой или войдите" % nick)
    if user:                     # старый аккаунт без пароля — забираем его себе
        db.q("UPDATE users SET password_hash = ?, name = ? WHERE id = ?",
             (hash_password(password), nick, user["id"]))
        user = db.one("SELECT * FROM users WHERE id = ?", (user["id"],))
        return user, db.create_session(user["id"]), True
    user = db.upsert_user(nick_key(nick), nick, None, "local")
    db.q("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user["id"]))
    user = db.one("SELECT * FROM users WHERE id = ?", (user["id"],))
    return user, db.create_session(user["id"]), False


def login(nick, password):
    """Вход по нику и паролю. Возвращает (user, token, claimed=False)."""
    nick = normalize(nick)
    if not nick or not password:
        raise ValueError("нужны ник и пароль")
    user = get_by_nick(nick)
    if not user:
        raise ValueError("нет такого ника — сначала зарегистрируйтесь")
    if not user.get("password_hash"):
        raise ValueError("под этим ником ещё нет пароля — откройте «Регистрация» "
                         "и задайте его этим же ником")
    if not verify_password(password, user["password_hash"]):
        raise ValueError("неверный пароль")
    return user, db.create_session(user["id"]), False


def has_password(user):
    return bool(user and user.get("password_hash"))


def rename(user_id, name):
    nick = check_nick(name)
    other = get_by_nick(nick)
    if other and other["id"] != user_id:
        raise ValueError("ник «%s» уже занят" % nick)
    db.q("UPDATE users SET name = ?, email = ? WHERE id = ?", (nick, nick_key(nick), user_id))
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))


def change_password(user_id, current, new):
    user = db.one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise ValueError("аккаунт не найден")
    if user.get("password_hash") and not verify_password(current, user["password_hash"]):
        raise ValueError("текущий пароль неверный")
    db.q("UPDATE users SET password_hash = ? WHERE id = ?",
         (hash_password(check_password(new)), user_id))
    return True


# --------------------------------------------------------------------------- #
# Ссылки предпросмотра
# --------------------------------------------------------------------------- #
# iframe предпросмотра живёт в sandbox без allow-same-origin: его запросы идут
# без cookie. Поэтому доступ к файлам проекта подтверждается подписью в пути:
#   /preview/<exp>-<hmac>/<pid>/<файл>
# Подпись = HMAC(секрет из базы, pid + exp). Живёт недолго и работает только на свой проект.

PREVIEW_TTL = 1800          # 30 минут: на один просмотр хватает с запасом


def _sign(pid, exp):
    secret = db.preview_secret().encode("utf-8")
    return hmac.new(secret, ("%s|%d" % (pid, exp)).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def preview_token(project_id, ttl=PREVIEW_TTL):
    if not db.valid_project_id(project_id):
        raise ValueError("неверный id проекта")
    exp = int(time.time()) + int(ttl)
    return "%d-%s" % (exp, _sign(project_id, exp)), exp


def check_preview_token(project_id, token):
    """True, если подпись верна и ещё не истекла."""
    if not db.valid_project_id(project_id):
        return False
    token = (token or "").strip()
    if "-" not in token:
        return False
    exp_s, sig = token.split("-", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(project_id, exp))
