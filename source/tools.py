#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools.py — инструменты, которые дают агентам доступ к реальному миру:
файлы workspace, терминал, интернет (загрузка страниц).

Терминал по умолчанию БЕЗ ограничений: агенты запускают любые команды, какие
умеет система (пайпы, &&, редиректы, cd куда угодно). Команда стартует в папке
проекта, но ничем не заперта в ней. Если нужен «строгий режим» — включите
terminal_strict в настройках, тогда вернётся allowlist.
"""

import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import db

BASE = Path(__file__).resolve().parent
WORKSPACES = db.DATA / "workspace"

TEXT_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css", ".scss",
            ".json", ".md", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg",
            ".sh", ".bat", ".sql", ".svg", ".env", ".gitignore", ".rs", ".go", ".java"}

STRICT_ALLOWLIST = {
    "python", "python3", "pip", "pip3", "node", "npm", "npx", "git", "ls", "cat",
    "pwd", "echo", "mkdir", "touch", "rm", "cp", "mv", "grep", "find", "wc", "head",
    "tail", "curl", "pytest", "uvicorn", "flask", "deno", "bun", "yarn", "pnpm",
}

DANGEROUS = re.compile(r"(rm\s+-rf\s+/|:\(\)\{|mkfs|dd\s+if=|shutdown|reboot|>\s*/dev/sd)", re.I)


AGENT_DIR = "agents"          # <pid>/agents/<aid> — личный песочный workspace агента


def workspace_for(project_id, agent_id=None, create=True):
    """
    agent_id=None → общий (сводный) workspace проекта: сюда главный агент складывает итог.
    agent_id=<id> → личный workspace агента: он тут пишет, тестирует и ломает что хочет,
                    не задевая работу остальных.

    id проверяются по маске формата (p_/a_ + 16 hex) ДО работы с диском:
    «../» и прочий мусор не превращается в путь, а сразу даёт ValueError.
    """
    if not db.valid_project_id(project_id):
        raise ValueError("неверный id проекта")
    if agent_id and not db.valid_agent_id(agent_id):
        raise ValueError("неверный id агента")
    p = WORKSPACES / project_id
    if agent_id:
        p = p / AGENT_DIR / agent_id
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def agent_workspaces(project_id):
    """[(agent_id, path)] всех личных песочниц проекта."""
    if not db.valid_project_id(project_id):
        return []
    base = WORKSPACES / project_id / AGENT_DIR
    if not base.exists():
        return []
    return [(d.name, d) for d in sorted(base.iterdir())
            if d.is_dir() and db.valid_agent_id(d.name)]


def drop_workspace(project_id):
    """Удалить папку проекта целиком (удаление проекта не должно оставлять сирот на диске)."""
    if not db.valid_project_id(project_id):
        raise ValueError("неверный id проекта")
    p = WORKSPACES / project_id
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    return not p.exists()


def resolve(project_id, relpath, agent_id=None):
    """Безопасный путь внутри workspace (никаких ../ и абсолютных путей)."""
    root = workspace_for(project_id, agent_id, create=False).resolve()
    rel = str(relpath or "").replace("\\", "/").lstrip("/")
    parts = [x for x in rel.split("/") if x and x not in (".", "..")]
    target = (root / "/".join(parts)).resolve()
    if root not in target.parents and target != root:
        raise ValueError("путь вне workspace")
    return target


# --------------------------------------------------------------------------- #
# Файлы
# --------------------------------------------------------------------------- #


def write_file(project_id, relpath, content, agent_id=None):
    root = workspace_for(project_id, agent_id)          # здесь же проверка id
    p = resolve(project_id, relpath, agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p.relative_to(root)), "bytes": len(content.encode())}


MAX_TEXT = None


def read_file(project_id, relpath, max_bytes=MAX_TEXT, agent_id=None):
    p = resolve(project_id, relpath, agent_id)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(relpath)
    raw = p.read_bytes()
    if max_bytes is not None:
        raw = raw[:max_bytes]
    return raw.decode("utf-8", "replace")


def read_file_full(project_id, relpath, agent_id=None, max_bytes=MAX_TEXT):
    """
    Для редактора: отдаёт текст и честный флаг «файл обрезан».
    Если truncated=True — сохранять из редактора нельзя, иначе затрём остаток.
    """
    p = resolve(project_id, relpath, agent_id)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(relpath)
    size = p.stat().st_size
    raw = p.read_bytes()
    cut = max_bytes is not None and len(raw) > max_bytes
    return {"content": raw[:max_bytes].decode("utf-8", "replace"),
            "size": size, "truncated": cut, "binary": is_binary(raw[:4096])}


def is_binary(raw):
    if b"\x00" in raw:
        return True
    try:
        raw.decode("utf-8")
        return False
    except UnicodeDecodeError:
        pass
    # не utf-8, но и не мусор (cp1251 и прочее) — считаем текстом, если мало «странных» байт
    odd = sum(1 for b in raw if b < 9 or (13 < b < 32))
    return odd > max(2, len(raw) * 0.02)


def read_bytes(project_id, relpath, agent_id=None):
    """Бинарное чтение: для preview и скачивания (картинки, zip, exe)."""
    p = resolve(project_id, relpath, agent_id)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(relpath)
    return p.read_bytes()


def file_size(project_id, relpath, agent_id=None):
    return resolve(project_id, relpath, agent_id).stat().st_size


def delete_path(project_id, relpath, agent_id=None):
    p = resolve(project_id, relpath, agent_id)
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    return True


SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".idea", ".mypy_cache",
             ".pytest_cache", "dist", "build", ".next", ".cache", "target"}


def tree(project_id, max_files=None, agent_id=None):
    """Обход с ранней отсечкой тяжёлых папок — не тратим время на node_modules."""
    root = workspace_for(project_id, agent_id, create=False)
    if not root.exists():
        return []
    out = []
    skip = SKIP_DIRS | ({"agents"} if not agent_id else set())
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(root))
            out.append({
                "path": rel, "name": p.name, "size": st.st_size,
                "ext": p.suffix.lower(),
                "dir": str(Path(rel).parent) if str(Path(rel).parent) != "." else "",
                "mtime": int(st.st_mtime),
            })
            if max_files is not None and len(out) >= max_files:
                return out
    return out


def _tree_old(project_id, max_files=400):
    root = workspace_for(project_id)
    out = []
    skip = {"__pycache__", ".git", "node_modules", ".venv", "data.db"}
    for p in sorted(root.rglob("*")):
        if skip & set(p.parts):
            continue
        if p.is_file():
            rel = str(p.relative_to(root))
            out.append({
                "path": rel,
                "name": p.name,
                "size": p.stat().st_size,
                "ext": p.suffix.lower(),
                "dir": str(Path(rel).parent) if str(Path(rel).parent) != "." else "",
                "mtime": int(p.stat().st_mtime),
            })
        if len(out) >= max_files:
            break
    return out


# --------------------------------------------------------------------------- #
# Терминал
# --------------------------------------------------------------------------- #


def workspace_signature(project_id, agent_id=None):
    """Отпечаток состояния файлов: нужен защите от зацикливания (файлы не меняются → стоп)."""
    root = workspace_for(project_id, agent_id, create=False)
    if not root.exists():
        return ""
    parts = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            f = Path(dirpath) / fn
            try:
                st = f.stat()
            except OSError:
                continue
            parts.append("%s:%d:%d" % (f.relative_to(root), st.st_size, int(st.st_mtime * 1000)))
    return "|".join(parts)


# Приоритет ролей при конфликте: код важнее остальных, при равенстве — более полный/новый файл.
LANE_PRIORITY = {"main": 5, "code": 4, "design": 3, "docs": 2, "review": 2, "test": 2,
                 "research": 1, "lead": 1}


def _translit(text):
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    }
    out = []
    for ch in (text or "").lower():
        if ch in table:
            out.append(table[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("-")
    return re.sub(r"-+", "-", "".join(out)).strip("-")


def safe_slug(nick, extra="", limit=24):
    """Имя для файла-проигравшего: слаг + короткий хэш, обрезка только по границе слова."""
    base = _translit(nick) or "agent"
    words = [w for w in base.split("-") if w]
    slug = ""
    for w in words:
        if slug and len(slug) + 1 + len(w) > limit:
            break
        slug = (slug + "-" + w).strip("-")
        if len(slug) >= limit:
            break
    if not slug:
        slug = base[:limit]
    if len(slug) > limit:                     # одно длинное слово — режем по границе
        slug = slug[:limit].rstrip("-")
    digest = hashlib.sha1(("%s|%s" % (nick or "", extra or "")).encode("utf-8")).hexdigest()[:8]
    return "%s-%s" % (slug, digest)


def merge_meta(project_id):
    row = db.one("SELECT meta FROM projects WHERE id = ?", (project_id,))
    try:
        return (db.jload(row["meta"], {}) or {}).get("merge") or {}
    except Exception:  # noqa: BLE001
        return {}


def _save_merge_meta(project_id, data):
    row = db.one("SELECT meta FROM projects WHERE id = ?", (project_id,))
    meta = db.jload(row["meta"], {}) if row else {}
    meta = meta or {}
    meta["merge"] = data
    db.update_project(project_id, meta=json.dumps(meta, ensure_ascii=False))


def _conflict_path(root, rel, nick, aid):
    """_conflicts/<имя файла>/<слаг-ник>-<хэш>.<ext> — ничего не теряется и видно автора."""
    base = Path(rel)
    ext = base.suffix or ""
    name = safe_slug(nick, aid + rel) + ext
    return root / "_conflicts" / (str(base.name) or "файл") / name


def merge_workspaces(project_id, nicks=None, exclude=(), agents=None, limit=None):
    """
    Главный агент сводит работы: файлы из личных песочниц переезжают в общий workspace проекта.

    Конфликт решается один раз и по правилу: код важнее остальных ролей, при равенстве —
    более полный файл, затем более новый. Победитель кладётся по нормальному имени,
    проигравшие — в _conflicts/<имя>/<слаг-ник>-<хэш>.<ext>, чтобы ничего не терять
    и не плодить «index.html.Ищущий_информацию_сайт_п» рядом с настоящим файлом.
    """
    nicks = dict(nicks or {})
    exclude = set(exclude or ())
    lanes = {}
    for a in (agents or []):
        nicks.setdefault(a["id"], a.get("nick") or a["id"])
        lanes[a["id"]] = a.get("lane") or ""
    root = workspace_for(project_id)
    report = {"files": [], "conflicts": [], "agents": {}, "errors": []}
    meta = merge_meta(project_id)
    cands = {}

    if not db.valid_project_id(project_id):
        report["errors"].append("неверный id проекта")
        return report

    for aid, adir in agent_workspaces(project_id):
        if aid in exclude:
            continue
        nick = nicks.get(aid) or aid
        count = 0
        for dirpath, dirnames, filenames in os.walk(adir):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                src = Path(dirpath) / fn
                try:
                    rel = str(src.relative_to(adir)).replace("\\", "/")
                    if rel.startswith("_conflicts/"):
                        continue
                    st = src.stat()
                    cands.setdefault(rel, []).append({
                        "aid": aid, "nick": nick, "lane": lanes.get(aid, ""),
                        "src": src, "size": st.st_size, "mtime": st.st_mtime,
                        "bytes": src.read_bytes(),
                    })
                    count += 1
                except Exception as e:  # noqa: BLE001
                    report["errors"].append("%s: %s" % (fn, e))
        report["agents"][aid] = {"nick": nick, "files": count}

    paths = sorted(cands.keys())
    if limit:
        paths = paths[:limit]

    new_meta = {}
    for rel in paths:
        items = cands[rel]
        dest = root / rel
        if "_.." in rel or rel.startswith("/") or ".." in Path(rel).parts:
            report["errors"].append("%s: путь вне workspace" % rel)
            continue
        prev = meta.get(rel) or {}
        if dest.exists() and dest.is_file():
            try:
                items.append({"aid": prev.get("aid") or "project", "nick": prev.get("nick") or "проект",
                              "lane": prev.get("lane") or "main", "src": dest,
                              "size": dest.stat().st_size, "mtime": dest.stat().st_mtime,
                              "bytes": dest.read_bytes(), "existing": True})
            except Exception:  # noqa: BLE001
                pass

        def rank(c):
            return (LANE_PRIORITY.get(c["lane"], 1), c["size"], c["mtime"])

        items_sorted = sorted(items, key=rank, reverse=True)
        winner = items_sorted[0]
        losers = items_sorted[1:]

        same = dest.exists() and dest.is_file() and dest.read_bytes() == winner["bytes"]
        if not same:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(winner["bytes"])

        kept = []
        for c in losers:
            if c.get("bytes") == winner["bytes"]:
                continue
            try:
                keep = _conflict_path(root, rel, c["nick"], c["aid"])
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_bytes(c["bytes"])
                kept.append({"nick": c["nick"], "kept_as": str(keep.relative_to(root))})
            except Exception as e:  # noqa: BLE001
                report["errors"].append("%s: %s" % (rel, e))

        new_meta[rel] = {"aid": winner["aid"], "nick": winner["nick"],
                         "lane": winner["lane"], "size": winner["size"]}
        action = "same" if same else ("new" if not prev else "replaced")
        report["files"].append({"path": rel, "agent": winner["aid"], "nick": winner["nick"],
                                "action": action})
        if kept:
            report["conflicts"].append({"path": rel, "winner": winner["nick"],
                                        "winner_agent": winner["aid"], "losers": kept,
                                        "kept_as": kept[0]["kept_as"]})

    if limit:
        new_meta = dict(meta, **new_meta)      # частичное слияние не забывает прошлое
    try:
        _save_merge_meta(project_id, new_meta)
    except Exception as e:  # noqa: BLE001
        report["errors"].append("мета слияния: %s" % e)
    return report


def read_shared(project_id, relpath="", agent_id=None, max_bytes=None):
    """
    Общий проект и работы коллег — чтобы агент видел чужие файлы, не дожидаясь финала.
    Без аргументов — что вообще есть: файлы проекта и песочницы коллег.
    """
    if not db.valid_project_id(project_id):
        raise ValueError("неверный id проекта")
    if not relpath and not agent_id:
        project = [f["path"] for f in tree(project_id)]
        agents = []
        for aid, _ in agent_workspaces(project_id):
            files = [f["path"] for f in tree(project_id, agent_id=aid)]
            if files:
                agents.append({"agent": aid, "files": files})
        return {"project": project, "agents": agents}
    if agent_id:
        if not db.valid_agent_id(agent_id):
            raise ValueError("неверный id агента")
        return {"agent": agent_id, "path": relpath,
                "content": read_file(project_id, relpath, max_bytes=max_bytes, agent_id=agent_id)}
    return {"path": relpath, "content": read_file(project_id, relpath, max_bytes=max_bytes)}


# --------------------------------------------------------------------------- #
# Терминал
# --------------------------------------------------------------------------- #


def _decode(raw):
    """Вывод консоли: utf-8 → cp866 (русская Windows) → cp1251, иначе с потерями."""
    if not raw:
        return ""
    for enc in ("utf-8", "cp866", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _run(cmd, cwd, timeout, shell):
    """
    Один запуск: байты на выходе, чтобы кириллица Windows не превращалась в кракозябры.
    Ребёнок живёт в своей группе процессов — тогда по таймауту убивается вся ветка,
    а не только shell (иначе запущенный агентом сервер висел бы вечно).
    """
    kw = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if shell:
        kw["shell"] = True
    else:
        kw["stdin"] = subprocess.DEVNULL
    if os.name == "nt":
        # CREATE_NO_WINDOW (0x08000000) | CREATE_NEW_PROCESS_GROUP (0x200)
        kw["creationflags"] = 0x08000000 | 0x00000200
    else:
        kw["start_new_session"] = True          # своя сессия/группа → killpg
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    kw["env"] = env
    proc = subprocess.Popen(cmd, **kw)
    try:
        out, err = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=3)
        except Exception:  # noqa: BLE001
            out, err = b"", b""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)


def _kill_tree(proc):
    """Убить группу процессов ребёнка (на POSIX — killpg, на Windows — taskkill /T)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def run_command(project_id, cmd, timeout=20, allow=True, strict=False, agent_id=None):
    """
    Запуск команды. strict=False (по умолчанию) — полная свобода: любой
    интерпретатор, пайпы, &&, редиректы, любые каталоги.
    strict=True — старый allowlist для тех, кто хочет «поводок».
    """
    if not allow:
        return {"ok": False, "blocked": True, "out": "терминал выключен в настройках"}
    cmd = (cmd or "").strip()
    if not cmd:
        return {"ok": False, "out": "пустая команда"}

    if strict:
        if DANGEROUS.search(cmd):
            return {"ok": False, "blocked": True, "out": "команда похожа на разрушительную — заблокировано (строгий режим)"}
        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            return {"ok": False, "out": "не разобрал команду: %s" % e}
        if not parts:
            return {"ok": False, "out": "пустая команда"}
        exe = os.path.basename(parts[0])
        if exe not in STRICT_ALLOWLIST:
            return {"ok": False, "blocked": True,
                    "out": "команда «%s» не в allowlist (строгий режим включён в настройках)" % exe}

    cwd = str(workspace_for(project_id, agent_id))       # команда живёт в песочнице агента
    t0 = time.time()
    try:
        # свободный режим — через шелл (пайпы, &&, cd), строгий — ровно одна проверенная команда
        target = shlex.split(cmd) if strict else cmd
        proc = _run(target, cwd, timeout, shell=not strict)
        out = _decode(proc.stdout) + (("\n[stderr]\n" + _decode(proc.stderr)) if proc.stderr else "")
        return {"ok": proc.returncode == 0, "code": proc.returncode, "shell": not strict,
                "out": out.strip() or "(без вывода)", "ms": int((time.time() - t0) * 1000)}
    except subprocess.TimeoutExpired as e:
        part = _decode(e.output or b"") if getattr(e, "output", None) else ""
        tail = ("\n[вывод до таймаута]\n" + part.strip()) if part.strip() else ""
        return {"ok": False, "timeout": True,
                "out": "таймаут %dс — команда и её процессы сняты%s" % (timeout, tail),
                "ms": timeout * 1000}
    except FileNotFoundError:
        return {"ok": False, "out": "команда не найдена: %s" % cmd.split()[0]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "out": "%s: %s" % (type(e).__name__, e)}


# --------------------------------------------------------------------------- #
# Интернет
# --------------------------------------------------------------------------- #


def _decode_page(body, ctype=""):
    """Кодировка из заголовка → из <meta charset> → utf-8 → cp1251. Иначе мусор на cp1251-сайтах."""
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    encs = []
    if m:
        encs.append(m.group(1))
    head = body[:4096].decode("ascii", "replace")
    m2 = re.search(r'(?i)<meta[^>]+charset=["\']?([\w\-]+)', head)
    if m2:
        encs.append(m2.group(1))
    encs += ["utf-8", "cp1251", "koi8-r"]
    for enc in encs:
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")


def _html_to_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", raw)
    txt = re.sub(r"(?s)<[^>]+>", " ", raw)
    txt = html.unescape(txt)
    txt = re.sub(r"[ \t\r\f\v]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()


def fetch_url(url, timeout=12, limit=6000, allow=True):
    if not allow:
        return {"ok": False, "blocked": True, "text": "сеть выключена в настройках"}
    if not re.match(r"^https?://", url or "", re.I):
        return {"ok": False, "text": "нужен http(s) URL"}
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MRCAgent/1.0",
        "Accept-Language": "ru,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(900_000)
            final = r.geturl()
            status = r.status
            ctype = r.headers.get("Content-Type") or ""
        raw = _decode_page(body, ctype)
        text = _html_to_text(raw)
        title = ""
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        if m:
            title = html.unescape(m.group(1)).strip()[:200]
        return {"ok": True, "url": final, "status": status, "title": title,
                "text": text[:limit], "chars": len(text)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "text": "%s: %s" % (type(e).__name__, e)}


def _clean_ddg(href):
    """//duckduckgo.com/l/?uddg=https%3A%2F%2Fsite → https://site"""
    href = html.unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        q = urllib.parse.urlparse(href).query
        got = urllib.parse.parse_qs(q).get("uddg")
        if got:
            return got[0]
    return href if href.startswith("http") else ""


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get_page(url, timeout=12, limit=900_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "ru,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(limit)
        return _decode_page(body, r.headers.get("Content-Type") or "")


def _parse_ddg(raw):
    """DuckDuckGo: html-версия и lite-версия."""
    out, seen = [], set()
    pat = (r'(?is)<a[^>]+(?:class="[^"]*result__a[^"]*"|class=[\'"]result-link[\'"])[^>]*'
           r'href="([^"]+)"[^>]*>(.*?)</a>')
    for m in re.finditer(pat, raw):
        link = _clean_ddg(m.group(1))
        title = html.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(2))).strip()
        if link and link not in seen:
            seen.add(link)
            out.append({"title": title[:160], "url": link})
    if not out:
        for m in re.finditer(r"(?i)uddg=([^&\"]+)", raw):
            link = urllib.parse.unquote(m.group(1))
            if link.startswith("http") and link not in seen:
                seen.add(link)
                out.append({"title": urllib.parse.urlparse(link).netloc, "url": link})
    return out


def _parse_bing(raw):
    """Bing: <li class="b_algo"> … <h2><a href="…">Заголовок</a>."""
    out, seen = [], set()
    for block in re.findall(r'(?is)<li class="b_algo".*?</li>', raw):
        m = re.search(r'(?is)<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block)
        if not m:
            continue
        link = html.unescape(m.group(1))
        title = html.unescape(re.sub(r"(?s)<[^>]+>", "", m.group(2))).strip()
        if link.startswith("http") and link not in seen:
            seen.add(link)
            out.append({"title": title[:160], "url": link})
    return out


SEARCH_PROVIDERS = [
    ("duckduckgo", "https://html.duckduckgo.com/html/?q=%s", _parse_ddg),
    ("duckduckgo-lite", "https://lite.duckduckgo.com/lite/?q=%s", _parse_ddg),
    ("bing", "https://www.bing.com/search?q=%s&setlang=ru", _parse_bing),
]


def _parse_searx(raw):
    """SearXNG отдаёт JSON: {results:[{title,url,content}]}."""
    data = json.loads(raw)
    out, seen = [], set()
    for r in (data.get("results") or []):
        link = r.get("url") or ""
        if link.startswith("http") and link not in seen:
            seen.add(link)
            out.append({"title": (r.get("title") or "")[:160], "url": link,
                        "snippet": (r.get("content") or "")[:240]})
    return out


def search_web(query, timeout=12, allow=True, searx=""):
    """
    Поиск без API-ключей. Порядок: свой SearXNG (если задан в настройках) → DuckDuckGo →
    его lite-версия → Bing. Первый, кто вернул результат без капчи, побеждает.

    Если не вышло ни у кого — возвращаем не пустой список, а внятную причину,
    чтобы модель (и человек в ленте) понимала, что случилось: «капча», «пусто», «таймаут».
    """
    if not allow:
        return {"ok": False, "blocked": True, "results": [], "problems": ["сеть выключена в настройках"],
                "text": "сеть выключена в настройках"}
    q = urllib.parse.quote(query or "")
    problems = []

    if (searx or "").strip():
        base = searx.strip().rstrip("/")
        url = "%s/search?q=%s&format=json" % (base, q)
        try:
            raw = _get_page(url, timeout=timeout)
            results = _parse_searx(raw)[:8]
            if results:
                return {"ok": True, "provider": "searxng", "results": results, "problems": problems}
            problems.append("SearXNG: пусто")
        except Exception as e:  # noqa: BLE001
            problems.append("SearXNG: %s" % e)

    for name, tpl, parser in SEARCH_PROVIDERS:
        try:
            raw = _get_page(tpl % q, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            problems.append("%s: %s" % (name, (str(e) or type(e).__name__)[:80]))
            continue
        low = raw[:6000].lower()
        if "challenge" in low or "captcha" in low or "anomaly" in low or "unusual traffic" in low:
            problems.append("%s: капча" % name)
            continue
        results = parser(raw)[:8]
        if results:
            return {"ok": True, "provider": name, "results": results, "problems": problems}
        problems.append("%s: пусто" % name)
    return {"ok": False, "results": [], "problems": problems,
            "text": "поиск не дал результатов (%s)" % "; ".join(problems)}


SEARCH_PROVIDERS = [
    ("DuckDuckGo", "https://html.duckduckgo.com/html/?q=%s", _parse_ddg),
    ("DuckDuckGo lite", "https://lite.duckduckgo.com/lite/?q=%s", _parse_ddg),
    ("Bing", "https://www.bing.com/search?q=%s&setlang=ru", _parse_bing),
]
