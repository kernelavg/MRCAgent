#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
files.py — вложения проекта: любые форматы, безопасные имена, извлечение текста.

Зачем отдельный модуль: агентам нужно не просто «файл лежит», а понимание, что
внутри. Поэтому здесь есть:
  * safe_name()    — имя без путей и запрещённых символов (никаких ../ и NUL);
  * save_base64()  — приём файла из браузера (base64 JSON, без multipart);
  * extract_text() — текст из txt/md/csv/json/кода, PDF, DOCX, XLSX, PPTX, ODT
                     и списка файлов из ZIP — только стандартная библиотека;
  * digest()       — короткая выжимка для промпта агента.

Всё остальное (картинки, архивы, видео, бинарники) не ломает работу: файл
сохраняется как есть, а в выжимке честно написано «бинарный файл, N КБ».
"""

import base64
import re
import zipfile
import zlib
from pathlib import Path

MAX_TEXT = 0                   # 0 = без потолка: текст файла отдаётся целиком

_BAD = set('/\\:*?"<>|\0\r\n\t')
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".ini", ".cfg", ".conf", ".env", ".toml", ".xml", ".html",
    ".htm", ".css", ".scss", ".js", ".jsx", ".ts", ".tsx", ".py", ".rb", ".php",
    ".java", ".kt", ".cs", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".sh", ".bat",
    ".ps1", ".sql", ".srt", ".vtt", ".tex", ".gradle", ".dockerfile",
}
OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi"}
ARCHIVE_EXT = {".zip", ".jar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}


def safe_name(raw, fallback="файл"):
    """Имя файла без пути: «../../etc/passwd» → «passwd», NUL и переводы строк выкидываем."""
    name = str(raw or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch not in _BAD).strip(" .")
    if not name:
        return fallback
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.strip(" .") or fallback
    ext = re.sub(r"[^0-9A-Za-z]", "", ext)[:16].lower()
    return stem + ("." + ext if ext else "")


def kind_of(name):
    ext = Path(str(name)).suffix.lower()
    if ext in TEXT_EXT:
        return "текст"
    if ext == ".pdf":
        return "pdf"
    if ext in OFFICE_EXT:
        return "офис"
    if ext in IMAGE_EXT:
        return "картинка"
    if ext in AUDIO_EXT:
        return "звук"
    if ext in VIDEO_EXT:
        return "видео"
    if ext in ARCHIVE_EXT:
        return "архив"
    return "файл"


def human(size):
    size = float(size or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return ("%d %s" % (size, unit)) if unit == "Б" else ("%.1f %s" % (size, unit))
        size /= 1024.0


def save_base64(data_b64, dest_dir, name, max_bytes=None):
    """
    Принимает base64 из браузера и кладёт файл в dest_dir под безопасным именем.
    Возвращает dict с именем, путём, размером. Бросает ValueError с текстом-причиной.
    """
    raw = str(data_b64 or "")
    if raw.startswith("data:"):                      # data URL из FileReader
        raw = raw.split("base64,", 1)[-1]
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        raise ValueError("пустой файл")
    pad = -len(raw) % 4
    try:
        blob = base64.b64decode(raw + "=" * pad)
    except Exception:
        raise ValueError("файл пришёл битым (base64 не читается)")
    if max_bytes and len(blob) > max_bytes:
        raise ValueError("файл больше %s (лимит в настройках)" % human(max_bytes))
    if not blob:
        raise ValueError("пустой файл")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    clean = safe_name(name)
    target = _free_path(dest, clean)
    target.write_bytes(blob)
    return {"name": target.name, "path": str(target), "size": len(blob)}


def _free_path(folder, name):
    """Если имя занято — «отчёт.pdf», «отчёт (2).pdf», …"""
    p = Path(folder) / name
    if not p.exists():
        return p
    stem, ext = p.stem, p.suffix
    for i in range(2, 1000):
        cand = Path(folder) / ("%s (%d)%s" % (stem, i, ext))
        if not cand.exists():
            return cand
    return Path(folder) / ("%s-%d%s" % (stem, len(list(Path(folder).iterdir())) + 1, ext))


# --------------------------------------------------------------------- текст


def _decode(raw):
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _printable_ratio(text):
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return good / float(len(text))


def _pdf_text(raw):
    """Best-effort: распаковываем потоки и вытаскиваем строки из Tj/TJ."""
    chunks = []
    for m in re.finditer(rb"stream\r?\n?(.*?)endstream", raw, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except Exception:
            pass
        if b"Tj" not in data and b"TJ" not in data:
            continue
        for s in re.finditer(rb"\((?:\\.|[^\\()])*\)", data):
            piece = s.group(0)[1:-1]
            piece = re.sub(rb"\\([()\\])", rb"\1", piece)
            piece = re.sub(rb"\\[0-7]{1,3}", b" ", piece)
            chunks.append(_decode(piece))
    text = " ".join(chunks)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


def _zip_text(path, suffix):
    """DOCX/XLSX/PPTX/ODT: достаём XML из архива и вычищаем теги."""
    parts = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if suffix in (".docx", ".odt"):
                order = ["word/document.xml"] if suffix == ".docx" else ["content.xml"]
            elif suffix in (".xlsx", ".ods"):
                order = [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)] or \
                        [n for n in names if n.endswith("content.xml")]
            elif suffix in (".pptx", ".odp"):
                slides = [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)]
                slides.sort(key=lambda s: int(re.findall(r"\d+", s)[-1]) if re.findall(r"\d+", s) else 0)
                order = slides or [n for n in names if n.endswith("content.xml")]
            else:
                order = []
            for n in order:
                try:
                    xml = z.read(n).decode("utf-8", "replace")
                except Exception:
                    continue
                xml = re.sub(r"</(w:p|a:p|text:p|text:h|row)>", "\n", xml)
                xml = re.sub(r"<c[^>]*?(?:/>|>.*?</c>)",
                             lambda m: re.sub(r"<[^>]+>", "", m.group(0)) + "\t"
                             if m.group(0).endswith("</c>") else "", xml)
                xml = re.sub(r"<[^>]+>", "", xml)
                xml = (xml.replace("&amp;", "&").replace("&lt;", "<")
                          .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'"))
                parts.append(xml)
    except Exception:
        return ""
    text = "\n".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _zip_list(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = [i.filename for i in z.infolist() if not i.filename.endswith("/")]
        head = ", ".join(names[:40])
        more = "" if len(names) <= 40 else " … ещё %d" % (len(names) - 40)
        return "в архиве %d файлов: %s%s" % (len(names), head, more)
    except Exception:
        return ""


def extract_text(path, limit=MAX_TEXT):
    """Текст файла (или "" если его не достать). Работает для любых форматов без падений."""
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        size = p.stat().st_size
        if size == 0:
            return ""
        if suffix in ARCHIVE_EXT:
            return _zip_list(p) if suffix in (".zip", ".jar") else ""
        if suffix == ".pdf":
            text = _pdf_text(p.read_bytes())
        elif suffix in OFFICE_EXT:
            text = _zip_text(p, suffix)
        elif suffix in TEXT_EXT or suffix == "":
            text = _decode(p.read_bytes())
        else:
            return ""
    except Exception:
        return ""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if _printable_ratio(text) < 0.75:
        return ""
    return text[:limit] if limit else text          # limit=0 — отдаём всё


def digest(path, name=None, size=None, limit=1200):
    """
    Одна-две строки для промпта: что за файл и что в нём.
    Именно это видит агент, когда пользователь прикрепил файл через «@».
    """
    p = Path(path)
    name = name or p.name
    try:
        size = p.stat().st_size if size is None else size
    except OSError:
        size = size or 0
    kind = kind_of(name)
    head = "%s · %s · %s" % (name, kind, human(size))
    text = extract_text(p, limit=limit)
    if text:
        one = re.sub(r"\s+", " ", text)[:limit]
        return "%s — внутри: %s" % (head, one)
    if kind == "картинка":
        return "%s — картинка, текст из неё не извлекается" % head
    if kind in ("звук", "видео"):
        return "%s — медиафайл, содержимое агенту не отдаётся" % head
    if kind == "архив":
        listing = _zip_list(p)
        return "%s — %s" % (head, listing or "архив, содержимое не распаковано")
    if kind == "pdf":
        return "%s — PDF без извлекаемого текста (скан или защита)" % head
    return "%s — бинарный файл, текст не извлекается" % head
