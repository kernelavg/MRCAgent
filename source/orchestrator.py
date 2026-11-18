#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orchestrator.py — сердце приложения: держит рой из 100 агентов, их статусы,
активности, сообщения между ними и доступ к инструментам (файлы/терминал/сеть).

Состояние пишется в SQLite, события — в шину (SSE). Поэтому закрытие окна
приложения ничего не убивает: сервер продолжает работать, а UI при открытии
просто догоняет поток событий.
"""

import hashlib
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import bus
import db
import mcp
import plugins
import questions
import files
import ide_build
import llm
import mailbox
import tools

# --------------------------------------------------------------------------- #
# Состав роя
# --------------------------------------------------------------------------- #

LANES = [
    # ключ,        подпись,        сколько,   иконка-активность
    ("lead",       "Планировщик",  1,  "think"),
    ("research",   "Разведчик",    15, "search"),
    ("design",     "Архитектор",   10, "think"),
    ("code",       "Программист",  33, "code"),
    ("test",       "Тестировщик",  12, "test"),
    ("review",     "Ревьюер",      15, "review"),
    ("docs",       "Документатор", 13, "write"),
    ("main",       "Главный",      1,  "review"),   # принимает работы, проверяет, сводит всё вместе
]

LANE_TITLE = {k: t for k, t, n, s in LANES}

ROLE_PROMPTS = {
    "main": ("Ты главный агент. Тебе присылают свои работы остальные агенты. "
             "Ты проверяешь общий workspace, находишь несостыковки, правишь их и сводишь всё "
             "в один рабочий результат: единый набор файлов, рабочие ссылки между страницами, README."),
    "lead": "Ты планировщик роя. Разбей цель на файлы и подзадачи, назови стек и порядок.",
    "research": "Ты разведчик. Ищешь факты: API, ограничения, подводные камни. Используй search и web.",
    "design": "Ты архитектор. Определяешь структуру файлов, интерфейсы и данные.",
    "code": "Ты программист. Пишешь рабочие файлы целиком, без заглушек, через инструмент write_file.",
    "test": "Ты тестировщик. Запускаешь код в терминале, пишешь тесты, ищешь падения.",
    "review": "Ты ревьюер. Ищешь реальные дефекты и пишешь их списком: файл, проблема, как чинить.",
    "docs": "Ты документатор. Пишешь README и инструкции по факту.",
}

PREFIX = ["Ka", "Ve", "No", "Lu", "Ze", "Or", "At", "Pi", "De", "Qu",
          "Ni", "As", "Co", "Ju", "He", "On", "Ru", "Si", "Te", "Um"]
SUFFIX = ["ra", "rix", "os", "en", "ix", "on", "us", "ia", "ar", "el"]

# Ники берутся из задачи: «Ищущий информацию: сайт про продукцию», а не «Ka+rix».
ROLE_WORDS = {
    "lead": ["Планирующий шаги", "Разбивающий на задачи", "Составляющий план"],
    "research": ["Ищущий информацию", "Читающий документацию", "Сравнивающий аналоги",
                 "Собирающий факты", "Проверяющий источники"],
    "design": ["Проектирующий структуру", "Рисующий макет", "Придумывающий навигацию",
               "Описывающий интерфейсы"],
    "code": ["Пишущий код", "Верстающий страницы", "Делающий формы", "Настраивающий запросы",
             "Правящий стили", "Собирающий данные", "Склеивающий модули", "Ускоряющий загрузку"],
    "test": ["Проверяющий работу", "Ловящий падения", "Гоняющий тесты", "Проверяющий края"],
    "review": ["Ищущий ошибки", "Ревьюящий код", "Проверяющий логику", "Смотрящий безопасность"],
    "docs": ["Пишущий инструкции", "Описывающий API", "Собирающий README"],
    "main": ["Главный проверяющий"],
}

STOP_WORDS = {
    "сделай", "сделайте", "сделать", "создай", "создайте", "создать", "напиши", "напишите",
    "написать", "нужен", "нужна", "нужно", "нужны", "хочу", "хотим", "чтобы", "который",
    "которая", "которое", "которые", "крутой", "крутую", "крутое", "крутые", "хороший",
    "хорошую", "классный", "классную", "пожалуйста", "мне", "нам", "этот", "эта", "это",
    "эти", "этой", "этого", "этих", "должен", "должна", "должно", "надо", "можно", "ещё",
    "еще", "очень", "просто", "весь", "всю", "все", "всё",
}


def topic_from_goal(goal, max_words=3, limit=26):
    """Короткая тема проекта из цели: «Сделай крутой сайт про продукцию этой компании» →
    «сайт про продукцию». Нужна для ников, работает без вызова модели."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9\-]+", (goal or "").lower())
    keep = [w for w in words if w not in STOP_WORDS and len(w) > 1]
    topic = " ".join(keep[:max_words]).strip()
    if len(topic) > limit:
        topic = topic[:limit].rsplit(" ", 1)[0]
    return topic or "проекта"


def nick_for(lane, topic, n):
    """n — порядковый номер внутри роли (с 1). Уникальность добивается хвостом «· 2»."""
    words = ROLE_WORDS.get(lane) or ["Агент"]
    base = words[(n - 1) % len(words)]
    nick = "%s: %s" % (base, topic)
    if n > len(words):
        nick += " · %d" % ((n - 1) // len(words) + 1)
    return nick[:64]


def make_nicks(n):
    names = []
    for p in PREFIX:
        for s in SUFFIX:
            names.append(p + s)
    _rng = random.Random(42)      # локальный seed: не портим глобальный random приложения
    _rng.shuffle(names)
    out = []
    for i in range(max(n, 1)):
        out.append(names[i % len(names)])
    return out


# --------------------------------------------------------------------------- #
# События
# --------------------------------------------------------------------------- #


EPHEMERAL = {"token", "thought", "draft_start"}          # потоковые куски не пишем в базу: это 90 % её роста


def emit(pid, agent_id, etype, text="", data=None, nick=None, persist=None):
    """
    Событие в шину (SSE) и — если событие содержательное — в базу.
    token/дельта-события идут ТОЛЬКО в SSE: их десятки в секунду, историю они не несут.
    """
    # удобство: можно передать dict в text — он станет data
    if isinstance(text, dict):
        data = text if data is None else data
        text = data.get("cmd") or data.get("path") or data.get("url") or ""
    if not isinstance(text, str):
        text = str(text)
    if persist is None:
        persist = etype not in EPHEMERAL
    eid = db.add_event(pid, agent_id, etype, text, data or {}) if persist else 0
    payload = {"id": eid, "project": pid, "agent": agent_id, "nick": nick,
               "type": etype, "text": text, "data": data or {}, "ts": time.time()}
    bus.publish("project:" + pid, payload)
    return payload


def set_status(agent, status=None, activity=None, task=None, tokens=None, files=None, finished=False):
    kw = {}
    if status:
        kw["status"] = status
    if activity:
        kw["activity"] = activity
    if task is not None:
        kw["task"] = task
    if tokens is not None:
        kw["tokens"] = tokens
    if files is not None:
        kw["files"] = db.jdump(files)
    if finished:
        kw["finished"] = db.now()
    if kw:
        db.update_agent(agent["id"], **kw)
        if isinstance(agent, dict):
            agent.update(kw)          # локальная копия не должна отставать
        if "status" in kw or "activity" in kw:
            bus.publish("project:" + agent["project_id"], {
                "type": "agent_state", "agent": agent["id"], "nick": agent["nick"],
                "status": agent.get("status"),
                "activity": agent.get("activity"),
                "task": agent.get("task"),
                "tokens": agent.get("tokens"),
                "files": agent.get("files"),
                "project": agent["project_id"], "ts": time.time(),
            })


# --------------------------------------------------------------------------- #
# Менеджер проектов
# --------------------------------------------------------------------------- #


class Runtime:
    def __init__(self, pid):
        self.pid = pid
        self.stop = threading.Event()
        self.paused = threading.Event()
        self.thread = None
        self.started = time.time()
        self.tokens = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost = 0.0
        self.errors = 0
        self.done = 0
        self.total = 0
        self.lock = threading.Lock()
        self.client = None


class Manager:
    def __init__(self):
        self.runtimes = {}
        self.lock = threading.Lock()

    # ---------------- жизненный цикл ----------------
    def running(self, pid):
        rt = self.runtimes.get(pid)
        return bool(rt and rt.thread and rt.thread.is_alive())

    def ensure_agents(self, pid, total=None):
        existing = db.list_agents(pid)
        if existing:
            return existing
        settings = db.get_settings()
        total = int(total or settings.get("agents") or 100)
        project = db.get_project(pid) or {}
        topic = topic_from_goal(project.get("goal") or project.get("name") or "")
        # сколько кого: планировщик и главный всегда есть, остальное — по весам ролей
        workers = [l for l in LANES if l[0] not in ("lead", "main")]
        lead_n = 1 if total >= 2 else 0
        main_n = 1 if total >= 2 else 0
        rest = max(0, total - lead_n - main_n)
        weights = {lane: base_n for lane, _, base_n, _ in workers}
        counts = {lane: 0 for lane in weights}
        left = rest
        for lane in ("research", "design", "code", "test", "review", "docs"):   # по одному каждой роли
            if left <= 0:
                break
            counts[lane] = counts.get(lane, 0) + 1
            left -= 1
        if left > 0:                                                            # остальное — по весам
            tw = sum(weights.values()) or 1
            raw = {lane: left * weights[lane] / tw for lane in weights}
            add = {lane: int(v) for lane, v in raw.items()}
            more = left - sum(add.values())
            for lane in sorted(raw, key=lambda k: (raw[k] - int(raw[k]), weights[k]), reverse=True)[:more]:
                add[lane] += 1
            for lane in counts:
                counts[lane] += add.get(lane, 0)
        plan = []
        for lane, title, base_n, act in LANES:
            n = {"lead": lead_n, "main": main_n}.get(lane, counts.get(lane, 0))
            if n:
                plan.append([lane, title, n])

        # ники: роль + тема задачи, уникальные внутри проекта
        used, idx = set(), 0
        for lane, title, n in plan:
            for i in range(1, n + 1):
                if idx >= total:
                    break
                nick = nick_for(lane, topic, i)
                bump = 2
                while nick in used:
                    nick = "%s (%d)" % (nick_for(lane, topic, i), bump)
                    bump += 1
                used.add(nick)
                aid = db.add_agent(pid, idx + 1, nick, title, lane, "", settings.get("model"))
                tools.workspace_for(pid, aid)           # личный песочный workspace агента
                idx += 1
        return db.list_agents(pid)

    def start(self, pid, user_id, pending_only=False):
        """
        pending_only=True — продолжить с места, где остановились (после закрытия/краша):
        агенты, которые уже закончили, повторно не работают и не тратят токены.
        """
        with self.lock:
            if self.running(pid):
                return {"ok": False, "reason": "уже работает"}
            p = db.get_project(pid)
            if not p:
                return {"ok": False, "reason": "нет проекта"}
            settings = db.get_settings()
            client = llm.Client(api_key=settings.get("api_key"), base_url=settings.get("base_url"),
                                model=settings.get("model"))
            if not settings.get("demo_mode") and not client.ready:
                return {"ok": False, "reason": "нет API-ключа; добавьте ключ или включите демо-режим"}
            agents = self.ensure_agents(pid, None)

            if not pending_only:
                # явный перезапуск: все агенты начинают заново
                db.q("UPDATE agents SET status = 'idle', activity = 'wait', finished = NULL "
                     "WHERE project_id = ?", (pid,))
                agents = db.list_agents(pid)

            rt = Runtime(pid)
            rt.total = len(agents)
            rt.done = sum(1 for a in agents if a["status"] == "done")
            rt.pending_only = pending_only
            if pending_only:
                rt.tokens_in = p.get("tokens_in") or 0
                rt.tokens_out = p.get("tokens_out") or 0
            client.stop_event = rt.stop
            rt.client = client
            self.runtimes[pid] = rt
            db.update_project(pid, status="running", agents_total=rt.total,
                              agents_done=rt.done, user_paused=0,
                              progress=round(rt.done / max(1, rt.total), 3))
            if settings.get("demo_mode") and not rt.client.ready:
                _scaffold_demo(pid)
            rt.thread = threading.Thread(target=self._run_project, args=(rt, settings),
                                         daemon=True, name="swarm-" + pid)
            rt.thread.start()
            return {"ok": True, "agents": rt.total, "demo": not rt.client.ready}

    def pause(self, pid, paused=True):
        rt = self.runtimes.get(pid)
        if not rt:
            return False
        if paused:
            rt.paused.set()
            db.update_project(pid, status="paused", user_paused=1)   # ручная пауза переживёт перезапуск
            emit(pid, None, "log", "рой поставлен на паузу (продолжу только по кнопке)")
        else:
            rt.paused.clear()
            db.update_project(pid, status="running", user_paused=0)
            emit(pid, None, "log", "рой продолжает работу")
        return True

    def stop(self, pid, wait=False):
        rt = self.runtimes.get(pid)
        if not rt:
            return False
        rt.stop.set()
        rt.paused.clear()
        emit(pid, None, "log", "останавливаю рой: агенты дорабатывают текущий шаг")
        if wait and rt.thread and rt.thread.is_alive():
            rt.thread.join(timeout=8)                 # не оставляем сирот после удаления проекта
            self.runtimes.pop(pid, None)
        return True

    # ---------------- основной цикл ----------------
    def _run_project(self, rt, settings):
        pid = rt.pid
        agents = db.list_agents(pid)
        if getattr(rt, "pending_only", False):
            agents = [a for a in agents if a["status"] != "done"]
        fatal = False
        try:
            conc = max(1, min(24, int(settings.get("concurrency") or 6)))
            emit(pid, None, "plan", "рой запущен: %d агентов, параллельно %d"
                 % (len(agents), conc))
            att = attached_files(pid)
            if att:
                emit(pid, None, "log", "вложения пользователя (%d): %s"
                     % (len(att), ", ".join(a["name"] for a in att)),
                     {"files": [a["name"] for a in att]})

            # --- 2. планировщик работает ПЕРВЫМ: его план уходит в промпты остальным ---
            lead = next((a for a in agents if a["lane"] == "lead"), None)
            rest = [a for a in agents if a["lane"] != "main"]
            if lead:
                emit(pid, None, "log", "планировщик %s раскладывает цель на подзадачи" % lead["nick"])
                plan_text = self._run_agent(rt, lead, settings) or ""
                # главный работает отдельной фазой в конце — в общий пул его не берём
                rest = [a for a in agents if a["id"] != lead["id"] and a["lane"] != "main"]
                brief = (plan_text or (db.get_project(pid) or {}).get("brief") or "").strip()
                if not brief:                       # демо/пустой ответ — план по факту файлов
                    brief = _fallback_brief(pid, db.get_project(pid))
                db.update_project(pid, brief=brief)
                emit(pid, None, "plan", "план: " + brief[:700],
                     {"brief": brief, "by": lead["nick"]})
                if rt.stop.is_set():
                    raise _Stopped()

            # --- промежуточное слияние: раз в N секунд главный подмешивает работы ---
            # Агенты перестают быть слепыми: чужие файлы появляются в общем проекте
            # по ходу дела, и их видно через read_shared.
            merge_interval = int(settings.get("merge_interval") or 0)
            main_agent = next((a for a in db.list_agents(pid) if a["lane"] == "main"), None)
            stop_partial = threading.Event()

            last_partial = [0.0]

            def _partial_merge():
                """Подмешать работы агентов в общий проект: агенты видят чужие файлы."""
                if rt.stop.is_set():
                    return
                last_partial[0] = time.time()
                try:
                    agents_now = db.list_agents(pid)
                    rep = tools.merge_workspaces(
                        pid, {a["id"]: a["nick"] for a in agents_now},
                        exclude=[main_agent["id"]] if main_agent else [],
                        agents=agents_now)
                    n = len([f for f in rep["files"] if f["action"] in ("new", "replaced")])
                    if n:
                        emit(pid, (main_agent or {}).get("id"), "merge",
                             "промежуточное слияние: коллегам видно ещё %d файлов" % n,
                             {"files": n, "partial": True,
                              "conflicts": len(rep["conflicts"])},
                             nick=(main_agent or {}).get("nick"))
                except Exception as e:  # noqa: BLE001
                    emit(pid, None, "log", "промежуточное слияние не удалось: %s" % e)

            def _partial_merges():
                while not stop_partial.wait(max(1, merge_interval)):
                    _partial_merge()

            partial_thread = None
            if merge_interval > 0:
                partial_thread = threading.Thread(target=_partial_merges, daemon=True,
                                                  name="merge-" + pid)
                partial_thread.start()
            try:
                with ThreadPoolExecutor(max_workers=conc) as pool:
                    futs = [pool.submit(self._run_agent, rt, a, settings) for a in rest]
                    for f in futs:
                        try:
                            f.result()
                        except Exception as e:  # noqa: BLE001
                            with rt.lock:
                                rt.errors += 1
                            emit(pid, None, "error", "агент упал: %s" % e)
                        # конец круга одного агента — сразу показываем его файлы остальным
                        if merge_interval > 0 and time.time() - last_partial[0] >= 1.0:
                            _partial_merge()
            finally:
                stop_partial.set()
            # --- 3. главный агент: принимает работы, проверяет и сводит всё вместе ---
            if not rt.stop.is_set():
                self._run_main(rt, settings)
        except _Stopped:
            pass
        except Exception as e:  # noqa: BLE001
            fatal = True
            db.update_project(pid, status="error", progress=_progress(pid))
            emit(pid, None, "error", "рой остановился с ошибкой: %s" % e)
        finally:
            # --- 4. статус и прогресс по факту, а не «1.0 всегда» ---
            done, err, total = _tally(pid, rt)
            progress = round(done / max(1, total), 3)
            if rt.stop.is_set():
                status = "stopped"
            elif fatal or err or done < total:
                status = "error"
            else:
                status = "done"
            db.update_project(pid, status=status, progress=progress,
                              agents_done=done, agents_total=total,
                              tokens_in=rt.tokens_in, tokens_out=rt.tokens_out)
            if status == "stopped":
                emit(pid, None, "log", "рой остановлен: готово %d из %d" % (done, total))
            else:
                emit(pid, None, "done" if not err else "log",
                     "рой закончил: %d из %d агентов%s"
                     % (done, total, (" · ошибок: %d" % err) if err else ""))
            bus.publish("project:" + pid, {"type": "run_end", "project": pid,
                                           "status": status, "progress": progress})
            self.runtimes.pop(pid, None)

    # ---------------- главный агент ----------------
    def _run_main(self, rt, settings):
        """
        Финал прогона: главный агент забирает личные workspace всех агентов,
        проверяет их работы, сводит файлы в общий workspace проекта и пишет отчёт.
        Слияние делается кодом, а не моделью: результат будет даже без ключа и в демо.
        """
        pid = rt.pid
        agents = db.list_agents(pid)
        main = next((a for a in agents if a["lane"] == "main"), None)
        workers = [a for a in agents if a["lane"] != "main"]
        nicks = {a["id"]: a["nick"] for a in agents}

        if main is None:                      # старые проекты без главного — свести всё равно надо
            report = tools.merge_workspaces(pid, nicks, agents=agents)
            emit(pid, None, "merge", "свёл файлы %d агентов (главного агента в проекте не было)"
                 % len([1 for v in report["agents"].values() if v["files"]]), report)
            return

        nick = main["nick"]
        set_status(main, status="working", activity="review",
                   task="принимаю работы %d агентов" % len(workers), finished=False)
        emit(pid, main["id"], "activity", "принимаю работы %d агентов" % len(workers),
             {"kind": "review"}, nick=nick)

        # иду по агентам: у кого есть файлы в песочнице, те и сдали работу
        ready, empty = [], []
        for a in workers:
            files = tools.tree(pid, max_files=8, agent_id=a["id"])
            (ready if files else empty).append((a, files))
        emit(pid, main["id"], "log",
             "сдали работу: %d · пусто: %d" % (len(ready), len(empty)), {}, nick=nick)

        report = tools.merge_workspaces(pid, nicks, exclude=[main["id"]], agents=agents)
        for f in report["files"][:60]:
            if f["action"] in ("new", "replaced", "newer", "older"):
                emit(pid, main["id"], "file", f["path"],
                     {"path": f["path"], "agent": f["agent"], "nick": f["nick"],
                      "action": f["action"], "scope": "merge"}, nick=nick)
        merged = len([f for f in report["files"] if f["action"] in ("new", "replaced", "newer", "older")])
        emit(pid, main["id"], "merge",
             "свёл работы: файлов %d, конфликтов %d%s" % (
                 merged, len(report["conflicts"]),
                 " (проигравшие — в _conflicts/)" if report["conflicts"] else ""),
             {"files": merged, "conflicts": report["conflicts"][:20],
              "agents": [v["nick"] for v in report["agents"].values() if v["files"]]}, nick=nick)

        # проверка: что сдали, чего не хватает, кто молчал
        checks = _main_checks(pid, ready, empty, report)
        emit(pid, main["id"], "review",
             "проверка: файлов %d · агентов с работой %d · замечаний %d"
             % (checks["files"], checks["agents"], len(checks["issues"])),
             checks, nick=nick)

        # модель главного агента: читает свод и дописывает финальные правки/README
        task = ("Проверь общий workspace проекта и сведи работы %d агентов в один результат.\n"
                "Сдали работу: %s\nПусто: %s\nФайлы в общем workspace: %s\n"
                "Замечания кода-проверки: %s\n"
                "Поправь несостыковки (ссылки, подключения стилей/скриптов, единая структура) "
                "и допиши README, если его нет."
                % (len(ready),
                   ", ".join(a["nick"] for a, _ in ready[:20]) or "никто",
                   ", ".join(a["nick"] for a, _ in empty[:10]) or "нет",
                   ", ".join(f["path"] for f in tools.tree(pid, max_files=30)) or "пусто",
                   "; ".join(checks["issues"][:8]) or "нет"))
        try:
            if rt.client and rt.client.ready and not settings.get("demo_mode"):
                self._real_agent(rt, main, settings, task)
            else:
                self._demo_main(rt, main, ready, empty, checks)
            set_status(main, status="done", activity="done", finished=True)
            emit(pid, main["id"], "done", "проверил и свёл всё вместе", {}, nick=nick)
        except _Stopped:
            return
        except Exception as e:  # noqa: BLE001
            with rt.lock:
                rt.errors += 1
            set_status(main, status="error", activity="error", finished=True)
            emit(pid, main["id"], "error", "главный не смог: %s" % e, {}, nick=nick)
            return

        # отчёт всегда кладём файлом в общий workspace
        tools.write_file(pid, "ОТЧЁТ-СБОРКИ.md", _report_md(pid, checks, report, rt))
        with rt.lock:
            rt.done += 1
            db.update_project(pid, agents_done=rt.done,
                              progress=round(rt.done / max(1, rt.total), 3))

    def _demo_main(self, rt, main, ready, empty, checks):
        """
        Без ключа: главный честно собирает собранное — секции агентов в один index.html,
        стили в общий файл, README по факту. Ничего не выдумывает про модель.
        """
        pid, nick = rt.pid, main["nick"]
        steps = [
            ("review", "открываю общий workspace и смотрю, что сдали агенты"),
            ("review", "сверяю имена файлов, подключения стилей и скриптов"),
            ("write", "собираю index.html из секций агентов"),
            ("write", "склеиваю стили и дописываю README"),
        ]
        for kind, text in steps:
            if rt.stop.is_set():
                raise _Stopped()
            self._wait_if_paused(rt)
            set_status(main, activity=kind)
            emit(pid, main["id"], "activity", text, {"kind": kind}, nick=nick)
            time.sleep(random.uniform(0.4, 0.9))

        built = _assemble_site(pid)
        if built["index"]:
            emit(pid, main["id"], "file", "index.html",
                 {"path": "index.html", "action": "write", "scope": "project"}, nick=nick)
        if built["styles"]:
            emit(pid, main["id"], "file", "styles/all.css",
                 {"path": "styles/all.css", "action": "write", "scope": "project"}, nick=nick)
        tools.write_file(pid, "README.md", _readme_md(pid, checks))
        emit(pid, main["id"], "file", "README.md",
             {"path": "README.md", "action": "write", "scope": "project"}, nick=nick)
        emit(pid, main["id"], "log",
             "собрал страницу: секций %d, стилей %d" % (built["sections"], built["css"]),
             {}, nick=nick)

    def _wait_if_paused(self, rt):
        while rt.paused.is_set() and not rt.stop.is_set():
            time.sleep(0.25)

    def _run_agent(self, rt, agent, settings):
        pid = rt.pid
        nick = agent["nick"]
        try:
            if rt.stop.is_set():
                return
            self._wait_if_paused(rt)
            task = _agent_task(agent, db.get_project(pid))
            set_status(agent, status="working", activity=_lane_activity(agent["lane"]),
                       task=task)
            emit(pid, agent["id"], "activity", task, {"kind": _lane_activity(agent["lane"])},
                 nick=nick)
            # консоль: с чего агент начал (видно в карточке агента, вкладка «Консоль»)
            emit(pid, agent["id"], "console",
                 "старт · %s · %s · задача: %s" % (
                     place_of(self._scope(agent)),
                     "демо-режим" if not (rt.client and rt.client.ready and not settings.get("demo_mode"))
                     else "модель %s" % (settings.get("model") or "mercury-3"),
                     (task or "")[:300]),
                 {"kind": "start", "model": settings.get("model") or "mercury-3",
                  "scope": place_of(self._scope(agent)), "task": (task or "")[:1000]}, nick=nick)
            if rt.client and rt.client.ready and not settings.get("demo_mode"):
                text = self._real_agent(rt, agent, settings, task)
            else:
                text = self._demo_agent(rt, agent, settings, task)
            set_status(agent, status="done", activity="done", finished=True)
            emit(pid, agent["id"], "done", "закончил", {}, nick=nick)
            files_done = db.jload(agent.get("files"), []) or []
            emit(pid, agent["id"], "console",
                 "итог · файлов %d · токенов %s · %s" % (
                     len(files_done), agent.get("tokens") or 0,
                     "демо-режим" if not (rt.client and rt.client.ready and not settings.get("demo_mode"))
                     else "боевой режим"),
                 {"kind": "stop", "files": files_done[:50], "tokens": agent.get("tokens") or 0,
                  "answer": (text or "")[:1500]}, nick=nick)
            # готово → доклад главному агенту (он потом принимает и сводит работы)
            if agent["lane"] != "main":
                main = _main_agent(pid)
                if main and main["id"] != agent["id"]:
                    files = db.jload(agent.get("files"), []) or []
                    _send_msg(pid, agent, nick, main["nick"],
                              "готово: %s. файлы: %s" % (task, ", ".join(files) or "без файлов"))
            with rt.lock:
                rt.done += 1
                db.update_project(pid, agents_done=rt.done,
                                  progress=round(rt.done / max(1, rt.total), 3))
            return text or ""
        except _Stopped:
            set_status(agent, status="idle", activity="wait")
            return ""
        except Exception as e:  # noqa: BLE001
            if rt.stop.is_set():
                set_status(agent, status="idle", activity="wait")
                return ""
            with rt.lock:
                rt.errors += 1
            set_status(agent, status="error", activity="error", finished=True)
            emit(pid, agent["id"], "error", "%s: %s" % (type(e).__name__, e), {}, nick=nick)
            emit(pid, agent["id"], "console", "упал: %s: %s" % (type(e).__name__, e),
                 {"kind": "error"}, nick=nick)
            return ""

    # ---------------- демо-агент ----------------
    def _demo_agent(self, rt, agent, settings, task):
        pid = rt.pid
        nick = agent["nick"]
        lane = agent["lane"]
        peers = [a["nick"] for a in db.list_agents(pid) if a["id"] != agent["id"]]
        project = db.get_project(pid) or {}
        topic = topic_from_goal(project.get("goal") or project.get("name") or "")
        steps = _demo_steps(lane, nick, task, peers, topic, agent.get("idx") or 0)
        tokens = 0
        files = db.jload(agent.get("files"), []) or []
        for i, step in enumerate(steps):
            if rt.stop.is_set():
                return
            self._wait_if_paused(rt)
            kind, text, extra = step
            set_status(agent, activity=kind)
            payload = {"kind": kind}
            if extra.get("url"):
                payload["url"] = extra["url"]
            emit(pid, agent["id"], "activity", text, payload, nick=nick)
            time.sleep(extra.get("sleep", random.uniform(0.35, 1.1)))

            if extra.get("file"):
                path = extra["file"]
                try:
                    if extra.get("content"):
                        tools.write_file(pid, path, extra["content"], agent["id"])
                    files.append(path)
                    files = list(dict.fromkeys(files))
                    set_status(agent, tokens=tokens, files=files)
                    emit(pid, agent["id"], "file", path,
                         {"path": path, "action": extra.get("action", "edit")}, nick=nick)
                except Exception:
                    pass
            if extra.get("cmd"):
                # демо-режим НЕ выполняет команды на машине пользователя: показываем правдоподобный вывод
                fake = extra.get("fake_out") or _demo_cmd_out(extra["cmd"])
                emit(pid, agent["id"], "tool", "терминал: " + extra["cmd"],
                     {"cmd": extra["cmd"], "out": fake[:900], "ok": True, "demo": True}, nick=nick)
            if extra.get("msg"):
                _send_msg(pid, agent, nick, extra["msg"],
                          extra.get("msg_text", "принял, забираю этот кусок"))
            if extra.get("web"):
                payload_web = {"url": extra["web"]}
                emit(pid, agent["id"], "web", "открываю " + extra["web"], payload_web, nick=nick)
            tokens += random.randint(40, 220)
            set_status(agent, tokens=tokens)

    # ---------------- боевой агент ----------------
    def _real_agent(self, rt, agent, settings, task):
        pid = rt.pid
        nick = agent["nick"]
        client = rt.client
        project = db.get_project(pid)
        goal = project.get("goal") or ""
        scope = self._scope(agent)
        files_now = [f["path"] for f in tools.tree(pid, agent_id=scope)]
        brief = (project.get("brief") or "").strip()
        plan_part = ("ПЛАН ОТ ПЛАНИРОВЩИКА:\n%s\n\n" % brief) if brief else ""
        attach_part = _attach_prompt(pid)
        skills_part = _skills_prompt(project.get("user_id") or "") + plugins.prompt(project.get("user_id") or "")
        mcp_part = _mcp_prompt(project.get("user_id") or "", settings)
        where = ("общий workspace проекта (ты сводишь работы остальных)" if scope is None
                 else "твой личный workspace — пиши и тестируй здесь, ничего не сломаешь у других")
        system = (
            "%s\n\nТы один из 100 субагентов в MRC Agent. Твоё имя: %s.\n"
            "ЦЕЛЬ ПРОЕКТА: %s\n%s%s%s%sРабочее место: %s. Файлы сейчас: %s\n\n"
            "Ты умеешь пользоваться инструментами. Чтобы выполнить действие, верни блок:\n"
            '```action\n{"tool":"write_file","path":"index.html","content":"..."}\n```\n'
            'Доступные инструменты: write_file(path, content), read_file(path), '
            'read_shared(path, agent), run(cmd), search(query), web(url), '
            'msg(to_agent_nick, text), ask_user(question, suggestion), build_app(target, scheme).\n'
            'build_app запускает реальную локальную сборку общего проекта: target android, windows или ios_simulator; для iOS нужен scheme. Если задача требует собранное приложение, исполнитель сборки должен попытаться выполнить сборку, изучить лог и исправить проект. Не называй сборку успешной, пока не получен статус done. Если инструментов нет, сообщи об этом прямо.\n'
            'Если важны предпочтения пользователя и без них нельзя уверенно выбрать, вызови ask_user с question и предложенным вариантом suggestion: жди до двух минут; при отсутствии ответа самостоятельно выбери разумный вариант, объясни выбор и продолжай задачу.\n'
            'attached(name) — текст файла, который прикрепил пользователь '
            '(attached() без аргумента — список вложений).\n'
            'skill(name) — полный текст скилла пользователя (skill() — список скиллов).\n'
            'read_shared — общий проект и файлы коллег: read_shared() покажет все файлы, '
            'read_shared(path="index.html") прочитает общий, '
            'read_shared(agent="ник", path="файл") — чужую работу.\n'
            'run(cmd) — настоящий терминал без ограничений: пайпы, &&, любые утилиты, cd куда угодно.\n'
            'mcp(server, name, args) — инструмент внешнего MCP-сервера, подключённого пользователем '
            '(mcp() без аргументов — что доступно).\n'
            'ПОЧТА (если настроена в Настройки → Почта): '
            'mail_search(query, from, subject, days, unread, limit, folder) — найти письма; '
            'mail_read(uid, folder) — прочитать письмо целиком; '
            'mail_send(to, subject, body) — отправить письмо; mail_folders() — список папок.\n'
            'Если письма нужны для задачи — ищи их сам: mail_search(query="счёт", days=30). '
            'Пустой ответ поиска всегда содержит причину — прочитай её.\n'
            "Коллеги пишут тебе сообщения — их видно в диалоге, учитывай их.\n"
            "Когда закончишь — ответь текстом без блоков action: это и будет твой отчёт.\n"
            % (ROLE_PROMPTS.get(agent["lane"], ""), nick, goal, plan_part, attach_part,
               skills_part, mcp_part, where, ", ".join(files_now) or "пусто")
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": "Твоя задача: %s" % task}]
        tokens = 0
        final_text = ""
        pending_decisions = []
        inbox_mark = 0  # ранее полученные сообщения тоже входят в контекст


        # ---------- защита от зацикливания ----------
        max_rounds = int(settings.get("max_rounds") or 0)          # 0 = без лимита действий
        stall_rounds = max(2, int(settings.get("stall_rounds") or 3))
        # Переключатель в настройках: 0 = защита от зацикливания выключена совсем.
        # Время на агента (agent_timeout) остаётся — это страховка процесса, а не лимит действий.
        loop_guard = bool(int(settings.get("loop_guard", 1) or 0))
        agent_timeout = float(settings.get("agent_timeout") or 0)
        deadline = time.time() + agent_timeout if agent_timeout > 0 else None
        base_max_tokens = int(settings.get("max_tokens") or 8192)
        last_sig = tools.workspace_signature(pid, scope)
        still_rounds = 0
        repeats = {}
        rnd = 0

        while True:
            rnd += 1
            if max_rounds and rnd > max_rounds:
                emit(pid, agent["id"], "log", "остановился: достигнут лимит кругов %d" % max_rounds,
                     {}, nick=nick)
                raise RuntimeError("достигнут лимит кругов: задача не завершена")
            if deadline is not None and time.time() > deadline:
                emit(pid, agent["id"], "log",
                     "остановился: время на агента вышло (%d с)" % int(settings.get("agent_timeout") or 900),
                     {}, nick=nick)
                raise RuntimeError("время на агента вышло: задача не завершена")
            if rt.stop.is_set():
                raise _Stopped()
            self._wait_if_paused(rt)

            # --- входящие сообщения реально попадают в диалог агента ---
            fresh = _inbox_since(agent, inbox_mark)
            if fresh:
                inbox_mark = fresh[-1]["id"]
                messages.append({"role": "user", "content": "Сообщения от коллег:\n" + "\n".join(
                    "%s: %s" % (m["from_nick"] or "агент", m["text"]) for m in fresh)})

            set_status(agent, activity=_lane_activity(agent["lane"]))
            last_push = [0.0]
            buf = {"t": ""}

            emit(pid, agent["id"], "draft_start", "", {}, nick=nick)
            thoughts = []

            def on_token(piece, total, _buf=buf, _lp=last_push):
                _buf["t"] += piece
                emit(pid, agent["id"], "token", _buf["t"], {}, nick=nick)

            def on_thought(piece):
                thoughts.append(piece)
                emit(pid, agent["id"], "thought", "".join(thoughts), {}, nick=nick)

            limit = base_max_tokens
            t_round = [time.time()]
            res = client.chat(messages, effort=settings.get("reasoning") or "medium",
                              max_tokens=limit, on_token=on_token, on_thought=on_thought,
                              on_reason=lambda m: emit(pid, agent["id"], "log", m, {}, nick=nick))
            # --- обрыв по max_tokens не должен терять ответ молча ---
            if res.get("truncated"):
                emit(pid, agent["id"], "log",
                     "ответ обрезан по лимиту (%d) — повторяю с запасом" % limit, {}, nick=nick)
                limit = limit + limit + limit
                buf["t"] = ""
                thoughts.clear()
                emit(pid, agent["id"], "draft_start", "", {}, nick=nick)
                _usage(rt, agent, res, tokens)
                tokens += res["usage"]["out"]
                res2 = client.chat(messages, effort=settings.get("reasoning") or "medium",
                                   max_tokens=limit, on_token=on_token, on_thought=on_thought,
                                   on_reason=lambda m: emit(pid, agent["id"], "log", m, {}, nick=nick))
                res = res2
                if res.get("truncated"):
                    _usage(rt, agent, res, tokens)
                    emit(pid, agent["id"], "error",
                         "ответ снова обрезан по лимиту %d — задача не выполнена" % limit,
                         {}, nick=nick)
                    raise RuntimeError("ответ модели обрезан по лимиту токенов (%d)" % limit)
            _usage(rt, agent, res, tokens)
            tokens += res["usage"]["out"]
            with rt.lock:
                rt.tokens += res["usage"]["out"]
                rt.cost += llm.cost(res["usage"]["in"], res["usage"]["out"])
            set_status(agent, tokens=tokens)
            text = res["text"]
            if not text.strip():
                raise RuntimeError("модель вернула пустой ответ: задача не завершена")
            final_text = text
            for qid in pending_decisions:
                db.q("UPDATE agent_questions SET decision=? WHERE id=? AND status='timeout'", (text, qid))
                emit(pid, agent["id"], "question_decision", "Ответ агента после таймаута: " + text, {"question_id": qid}, nick=nick)
            pending_decisions.clear()
            emit(pid, agent["id"], "result", text[-1200:], {}, nick=nick)
            # консоль: круг модели — время, токены, размер ответа
            emit(pid, agent["id"], "console",
                 "круг %d · ответ %d симв. · in %s / out %s токенов · %.1f с" % (
                     rnd, len(text), res["usage"].get("in"), res["usage"].get("out"),
                     time.time() - (t_round[0] if t_round else time.time())),
                 {"kind": "llm", "round": rnd, "chars": len(text),
                  "in": res["usage"].get("in"), "out": res["usage"].get("out"),
                  "ms": int((time.time() - (t_round[0] if t_round else time.time())) * 1000),
                  "answer": text[:1500]}, nick=nick)

            actions, broken = _parse_actions_report(text)
            # сломанный JSON в блоке action раньше терялся молча — теперь это видно в консоли
            for frag, why in broken[:5]:
                emit(pid, agent["id"], "error",
                     "не разобрал действие (%s): %s" % (why, frag), {}, nick=nick)
            if broken:
                raise RuntimeError("действия модели содержат некорректный JSON; задача не выполнена")
            if not actions:
                break

            results = []
            for act in actions:
                if rt.stop.is_set():
                    raise _Stopped()
                # одинаковое действие третий раз подряд = агент застрял
                key = _action_key(act)
                repeats[key] = repeats.get(key, 0) + 1
                if loop_guard and repeats[key] > 3:
                    emit(pid, agent["id"], "log",
                         "остановился: одно и то же действие повторяется (%s)"
                         % (act.get("tool") or "?"), {}, nick=nick)
                    raise RuntimeError("агент зациклился: задача не завершена")
                action_start = time.time()
                out = self._exec_action(pid, agent, settings, act, nick, rt)
                if (act.get("tool") or "").lower() == "ask_user":
                    if deadline is not None:
                        deadline += time.time() - action_start
                if isinstance(out, str) and out.startswith("[question_timeout:"):
                    pending_decisions.append(out.split("]", 1)[0].split(":", 1)[1])
                results.append({"action": act, "out": out})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": "Результаты инструментов:\n" +
                             json.dumps(results, ensure_ascii=False)})

            # прогресс = изменились файлы в песочнице агента
            sig = tools.workspace_signature(pid, scope)
            if loop_guard and sig == last_sig and not any((a.get("tool") or "").lower() == "ask_user" for a in actions):
                still_rounds += 1
                if still_rounds >= stall_rounds:
                    emit(pid, agent["id"], "log",
                         "остановился: %d круга подряд без изменений в файлах (нет прогресса)"
                         % still_rounds, {}, nick=nick)
                    raise RuntimeError("нет прогресса: задача не завершена")
            elif sig != last_sig:
                still_rounds = 0
                last_sig = sig

        if agent["lane"] == "lead" and final_text.strip():
            db.update_project(pid, brief=final_text.strip())
        return final_text

    def _scope(self, agent):
        """Личный workspace агента; у главного агента — общий: он сводит чужие работы."""
        return None if agent["lane"] == "main" else agent["id"]

    def _exec_action(self, pid, agent, settings, act, nick, rt):
        tool = (act.get("tool") or "").lower()
        scope = self._scope(agent)
        place = "agent" if scope else "project"
        try:
            if tool == "build_app":
                if agent.get("lane") != "main":
                    return "Сборку общего проекта выполняет главный агент. Сообщи ему готовность своих файлов."
                if not settings.get("allow_terminal"):
                    return "Сборка недоступна: терминал выключен в настройках."
                build = ide_build.start(pid, act.get("target") or "", act.get("scheme") or "")
                emit(pid, agent["id"], "tool", "MRCIDE: начата сборка", {"build_id": build["id"]}, nick=nick)
                while not rt.stop.wait(0.5):
                    build = ide_build.get(pid, build["id"])
                    if build and build['status'] != 'running':
                        return json.dumps(build, ensure_ascii=False)
                ide_build.stop(pid, build['id'])
                raise _Stopped()
            if tool == "ask_user":
                question = act.get("question") or act.get("text") or ""
                suggestion = act.get("suggestion") or ""
                qid = questions.create(pid, agent["id"], question, suggestion=suggestion)
                emit(pid, agent["id"], "question", question, {"question_id": qid, "deadline": time.time() + 120}, nick=nick)
                set_status(agent, activity="жду ответа до двух минут")
                project = db.get_project(pid) or {}
                questions.notify_windows(nick, project.get("name") or "", question)
                state, answer = questions.wait(qid, rt.stop)
                if state == "cancelled":
                    raise _Stopped()
                if state == "answered":
                    emit(pid, agent["id"], "question_answered", "Пользователь ответил: " + answer, {"question_id": qid}, nick=nick)
                    return "Ответ пользователя: " + answer
                emit(pid, agent["id"], "question_timeout", "Ответа нет две минуты; агент выберет решение самостоятельно.", {"question_id": qid}, nick=nick)
                return "[question_timeout:%s] Ответа нет две минуты. Самостоятельно выбери разумное решение, явно зафиксируй выбор в отчёте и продолжай работу." % qid
            if tool == "write_file":
                path = act.get("path") or "notes.txt"
                content = act.get("content") or ""
                tools.write_file(pid, path, content, scope)
                files = db.jload(agent.get("files"), []) or []
                files = list(dict.fromkeys(files + [path]))
                set_status(agent, files=files)
                emit(pid, agent["id"], "file", path,
                     {"path": path, "action": "write", "scope": place}, nick=nick)
                return "записано: %s (%d символов)" % (path, len(content))
            if tool == "read_file":
                return tools.read_file(pid, act.get("path") or "", agent_id=scope)
            if tool == "read_shared":
                # общий проект и работы коллег — не дожидаясь финального слияния
                want = act.get("agent") or act.get("from_agent") or ""
                target = _resolve_agent(pid, want) if want else None
                res = tools.read_shared(pid, act.get("path") or "",
                                        agent_id=(target or {}).get("id"))
                emit(pid, agent["id"], "log",
                     "смотрю общий проект: %s" % (act.get("path") or "список файлов"),
                     {"kind": "shared"}, nick=nick)
                return json.dumps(res, ensure_ascii=False)
            if tool in ("attached", "attach", "attachment"):
                name = act.get("name") or act.get("file") or ""
                res = _attached_tool(pid, name)
                head = ("вложение: %s" % res["name"]) if res.get("name") else \
                       ("вложения: %d" % len(res.get("files") or []))
                emit(pid, agent["id"], "tool", head,
                     {"cmd": "attached %s" % name, "out": str(res.get("text") or res.get("reason") or "")[:800],
                      "ok": res.get("ok", False)}, nick=nick)
                # Полный текст передаётся без локального ограничения размера.
                return json.dumps(res, ensure_ascii=False)
            if tool in ("skill", "skills", "скилл"):
                uid = (db.get_project(pid) or {}).get("user_id") or ""
                res = _skill_tool(uid, act.get("name") or act.get("skill") or "")
                head = ("скилл: %s" % res["name"]) if res.get("name") else \
                       ("скиллы: %d" % len(res.get("skills") or []))
                emit(pid, agent["id"], "tool", head,
                     {"cmd": "skill %s" % (act.get("name") or act.get("skill") or ""),
                      "out": str(res.get("body") or res.get("reason") or "")[:800],
                      "ok": res.get("ok", False)}, nick=nick)
                return json.dumps(res, ensure_ascii=False)
            if tool == "run":
                cmd = act.get("cmd") or ""
                emit(pid, agent["id"], "tool", "терминал: " + cmd, {"cmd": cmd, "out": "", "ok": True}, nick=nick)
                t0 = time.time()
                res = tools.run_command(pid, cmd, timeout=int(settings.get("terminal_timeout") or 20),
                                        allow=bool(settings.get("allow_terminal")),
                                        strict=bool(settings.get("terminal_strict")),
                                        agent_id=agent["id"])
                full = res.get("out", "") or ""
                # в ленте вывод короткий, в консоли — целиком (до 20k), чтобы было видно, что реально произошло
                emit(pid, agent["id"], "tool", "терминал: " + cmd,
                     {"cmd": cmd, "out": full[:800], "out_full": full,
                      "ok": res.get("ok", False), "ms": int((time.time() - t0) * 1000),
                      "code": res.get("code")}, nick=nick)
                return full
            if tool == "search":
                q = act.get("query") or ""
                emit(pid, agent["id"], "activity", "ищу: " + q, {"kind": "search"}, nick=nick)
                res = tools.search_web(q, allow=bool(settings.get("allow_network")),
                                       searx=settings.get("searx_url") or "")
                if res.get("results"):
                    return json.dumps(res["results"], ensure_ascii=False)
                # пустого [] модель не понимает: отдаём причину словами
                why = res.get("text") or "; ".join(res.get("problems") or []) or "нет ответа"
                emit(pid, agent["id"], "log", "поиск не дал результатов: " + why, {}, nick=nick)
                return json.dumps({"ok": False, "results": [], "reason": why}, ensure_ascii=False)
            if tool == "web":
                url = act.get("url") or ""
                emit(pid, agent["id"], "web", "открываю " + url, {"url": url}, nick=nick)
                res = tools.fetch_url(url, allow=bool(settings.get("allow_network")))
                return (res.get("title", "") + "\n" + res.get("text", ""))
            if tool in ("mcp", "mcp_call", "mcp_tool"):
                server = act.get("server") or act.get("mcp") or ""
                name = act.get("name") or act.get("tool_name") or act.get("method") or ""
                args = act.get("args") or act.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except ValueError:
                        args = {}
                emit(pid, agent["id"], "tool", "MCP: %s → %s" % (server or "список", name or "инструменты"),
                     {"cmd": "mcp %s %s" % (server, name),
                      "out": json.dumps(args, ensure_ascii=False)[:400], "ok": True,
                      "mcp": True}, nick=nick)
                res = _mcp_tool(pid, server, name, args, settings)
                text = json.dumps(res, ensure_ascii=False)
                emit(pid, agent["id"], "tool", "MCP: %s" % (res.get("server") or server or "список"),
                     {"cmd": "mcp %s %s" % (server, name),
                      "out": (res.get("text") or res.get("reason") or text)[:800],
                      "ok": bool(res.get("ok")), "mcp": True,
                      "tools": [t.get("name") if isinstance(t, dict) else t
                                for t in (res.get("tools") or [])][:40]}, nick=nick)
                return text

            if tool == "msg":
                return _send_msg(pid, agent, nick, act.get("to_agent") or act.get("to") or "",
                                 act.get("text") or "")

            # ---------------- почта ----------------
            if tool.startswith("mail"):
                # «Интернет» в настройках выключает все внешние связи, включая почту:
                # иначе тумблер врал бы — агент по-прежнему ходил бы наружу
                if not bool(int(settings.get("allow_network", 1) or 0)):
                    why = "интернет выключен в настройках — почта тоже недоступна"
                    emit(pid, agent["id"], "log", "почта: " + why, {"mail": True}, nick=nick)
                    return json.dumps({"ok": False, "reason": why}, ensure_ascii=False)

            if tool in ("mail_search", "mail", "mail_find"):
                q = act.get("query") or act.get("q") or ""
                who = act.get("from") or act.get("sender") or ""
                subj = act.get("subject") or ""
                days = act.get("days", 30)
                unread = bool(act.get("unread"))
                limit = int(act.get("limit") or 10)
                folder = act.get("folder") or ""
                emit(pid, agent["id"], "activity", "почта: ищу письма", {"kind": "mail"}, nick=nick)
                res = mailbox.search(settings, query=q, sender=who, subject=subj, days=days,
                                     unread=unread, limit=limit, folder=folder)
                lines = []
                for m in res.get("messages") or []:
                    lines.append("#%s · %s · %s · %s%s" % (
                        m["uid"], m["date"], m["from"], m["subject"],
                        "" if not m["unread"] else " · непрочитано"))
                head = "письма: " + ("; ".join(lines) if lines else (res.get("reason") or "пусто"))
                emit(pid, agent["id"], "tool", "почта: поиск",
                     {"cmd": "mail_search %s" % json.dumps(
                         {"query": q, "from": who, "subject": subj, "days": days,
                          "unread": unread, "limit": limit}, ensure_ascii=False),
                      "out": head[:800], "out_full": head,
                      "ok": res.get("ok", False), "mail": True}, nick=nick)
                return json.dumps(res, ensure_ascii=False)

            if tool in ("mail_read", "mail_open"):
                uid = act.get("uid") or act.get("id") or act.get("n")
                folder = act.get("folder") or ""
                res = mailbox.read(settings, uid, folder=folder)
                if res.get("ok"):
                    head = "#%s · %s · %s\n\n%s" % (res["uid"], res["subject"], res["from"], res["body"])
                else:
                    head = "не прочитал: %s" % res.get("reason")
                emit(pid, agent["id"], "tool", "почта: читаю письмо %s" % uid,
                     {"cmd": "mail_read uid=%s" % uid, "out": head[:800], "out_full": head,
                      "ok": res.get("ok", False), "mail": True}, nick=nick)
                return json.dumps(res, ensure_ascii=False)

            if tool in ("mail_send", "mail_write"):
                res = mailbox.send(settings, to=act.get("to") or act.get("email") or "",
                                   subject=act.get("subject") or "",
                                   body=act.get("body") or act.get("text") or act.get("content") or "",
                                   cc=act.get("cc") or "", reply_to=act.get("reply_to") or "")
                out = ("отправлено: %s · %s" % (res.get("to"), res.get("subject"))
                       if res.get("ok") else "не отправил: %s" % res.get("reason"))
                emit(pid, agent["id"], "tool", "почта: отправка",
                     {"cmd": "mail_send to=%s" % (act.get("to") or ""),
                      "out": out[:800], "out_full": out,
                      "ok": res.get("ok", False), "mail": True}, nick=nick)
                return json.dumps(res, ensure_ascii=False)

            if tool == "mail_folders":
                res = mailbox.folders(settings)
                out = ", ".join(res.get("folders") or []) or (res.get("reason") or "")
                emit(pid, agent["id"], "tool", "почта: папки",
                     {"cmd": "mail_folders", "out": out[:800], "ok": res.get("ok", False),
                      "mail": True}, nick=nick)
                return json.dumps(res, ensure_ascii=False)
            return "неизвестный инструмент: %s" % tool
        except _Stopped:
            raise
        except Exception as e:  # noqa: BLE001
            emit(pid, agent["id"], "error", "инструмент %s: %s" % (tool, e), {}, nick=nick)
            return "ошибка: %s" % e


    # ---------------- восстановление после перезапуска ----------------
    def reconcile(self):
        """
        Приложение закрыли/убили — в базе могли остаться агенты в статусе working.
        Приводим состояние в порядок, чтобы UI не врал, и запоминаем, что продолжать.
        """
        db.q("UPDATE agents SET status = 'idle', activity = 'wait' "
             "WHERE status = 'working'")
        # running → paused, но ручную паузу не трогаем
        db.q("UPDATE projects SET status = 'paused' WHERE status = 'running' AND COALESCE(user_paused, 0) = 0")
        return db.many("SELECT id, status FROM projects")

    def resume_all(self, settings=None):
        """Поднимает проекты, которые работали до закрытия приложения."""
        settings = settings or db.get_settings()
        if not settings.get("resume_on_start"):
            return []
        jobs = []
        for p in db.many("SELECT id, status, user_paused FROM projects "
                         "WHERE status IN ('running','paused')"):
            if p["user_paused"]:
                # пользователь сам поставил на паузу — не будим без его команды
                emit(p["id"], None, "log", "проект остаётся на паузе: так поставил пользователь")
                continue
            agents = db.list_agents(p["id"])
            if not agents:
                continue
            if all(a["status"] == "done" for a in agents):
                db.update_project(p["id"], status="done", progress=1.0)
                continue
            jobs.append(p["id"])
        for pid in jobs:
            try:
                self.start(pid, None, pending_only=True)
                emit(pid, None, "log", "продолжаю работу после перезапуска приложения")
            except Exception as e:  # noqa: BLE001
                emit(pid, None, "error", "не смог возобновить: %s" % e)
        return jobs

    def shutdown_all(self):
        for pid in list(self.runtimes):
            try:
                self.runtimes[pid].stop.set()
                db.update_project(pid, status="paused")
            except Exception:
                pass


MANAGER = Manager()


# --------------------------------------------------------------------------- #
# Тексты и скрипты
# --------------------------------------------------------------------------- #


def _lane_activity(lane):
    return {"lead": "think", "research": "search", "design": "think", "code": "code",
            "test": "test", "review": "review", "docs": "write",
            "main": "review"}.get(lane, "think")




# --------------------------------------------------------------------------- #
# Мелкие помощники роя
# --------------------------------------------------------------------------- #


class _Stopped(Exception):
    """Рой остановлен извне — не ошибка."""


def _tally(pid, rt=None):
    """Сколько реально готово/сломалось — по базе, а не по счётчику в памяти."""
    rows = db.many("SELECT status, COUNT(*) AS c FROM agents WHERE project_id = ? GROUP BY status", (pid,))
    by = {r["status"]: r["c"] for r in rows}
    total = sum(by.values()) or (rt.total if rt else 0)
    return by.get("done", 0), by.get("error", 0), total


def _progress(pid):
    agents = db.list_agents(pid)
    if not agents:
        return 0.0
    return round(sum(1 for a in agents if a["status"] == "done") / len(agents), 3)


def attached_files(pid):
    """
    Вложения пользователя к проекту: имя, путь в workspace и готовая выжимка.
    Пользователь ссылается на них через «@имя» — агенты получают и имена, и текст.
    """
    out = []
    try:
        for r in db.project_files(pid):
            meta = db.jload(r.get("meta") or "{}", {}) or {}
            out.append({"name": r["name"], "rel": meta.get("rel") or ("files/" + r["name"]),
                        "size": int(r["size"] or 0), "kind": r.get("kind") or files.kind_of(r["name"]),
                        "digest": meta.get("digest") or "", "path": r["path"]})
    except Exception:  # noqa: BLE001 — вложение не должно ронять рой
        pass
    return out


# --------------------------------------------------------------------------- #
# Скиллы: пользовательские .md-инструкции, которые видит каждый агент
# --------------------------------------------------------------------------- #

SKILL_IN_PROMPT = 4000          # символов одного скилла в промпте; полный текст — инструментом


def _skills_prompt(user_id, limit=8):
    """Кусок системного промпта со включёнными скиллами (пустая строка, если их нет)."""
    try:
        rows = db.enabled_skills(user_id) if user_id else []
    except Exception:                       # noqa: BLE001 — скилл не должен ронять рой
        rows = []
    if not rows:
        return ""
    lines = ["СКИЛЛЫ (правила пользователя — соблюдай их в работе):"]
    for r in rows:
        body = (r["body"] or "").strip()
        lines.append("\n### %s\n%s" % (r["name"], body))
    lines.append('Полный текст скилла: инструмент skill(name="имя"). skill() — список.')
    return "\n".join(lines) + "\n"


def _mcp_prompt(user_id, settings=None):
    """
    Кусок системного промпта: подключённые серверы MCP и их инструменты.

    Список берём из кэша в базе: промпт собирается на каждого из 100 агентов,
    и ждать сеть/запуск процесса ради этого нельзя. Кэш обновляет «Проверить»
    в настройках (и первый же вызов инструмента).
    """
    st = settings or {}
    if not int(st.get("mcp_on", 1) or 0) or not int(st.get("mcp_prompt", 1) or 0):
        return ""
    try:
        servers = db.enabled_mcp_servers(user_id) if user_id else []
        cache = db.mcp_tools_cache(user_id) if user_id else {}
    except Exception:                       # noqa: BLE001 — MCP не должен ронять рой
        return ""
    if not servers:
        return ""
    return mcp.prompt_block(servers, cache)


def _mcp_tool(pid, server="", name="", args=None, settings=None):
    """
    Инструмент агента: вызвать инструмент подключённого MCP-сервера.

    Без server — отдаём список серверов и их инструментов (агент смотрит, что есть).
    """
    st = settings or db.get_settings()
    project = db.get_project(pid) or {}
    uid = project.get("user_id") or ""
    rows = db.enabled_mcp_servers(uid) if uid else []
    if not int(st.get("mcp_on", 1) or 0):
        return {"ok": False, "reason": "MCP выключен в настройках (Настройки → MCP)"}
    if not rows:
        return {"ok": False, "reason": "нет подключённых MCP-серверов",
                "servers": []}
    if not str(server or "").strip():
        return {"ok": True, "servers": [
            {"name": r["name"], "transport": r.get("transport") or "stdio",
             "tools": [t.get("name") for t in (db.jload(r.get("tools"), []) or [])],
             "last_error": r.get("last_error") or ""} for r in rows]}
    row = db.find_mcp_server(uid, server)
    if not row or not int(row.get("enabled") or 0):
        return {"ok": False, "reason": "такого MCP-сервера нет: %s" % server,
                "servers": [r["name"] for r in rows]}
    cfg = {"id": row["id"], "name": row["name"], "transport": row.get("transport") or "stdio",
           "command": row.get("command") or "", "args": db.jload(row.get("args"), []) or [],
           "url": row.get("url") or "", "headers": db.jload(row.get("headers"), {}) or {},
           "env": db.jload(row.get("env"), {}) or {},
           "timeout": int(row.get("timeout") or 0) or int(st.get("mcp_timeout") or 0)}
    if not str(name or "").strip():
        res = mcp.list_tools(cfg)
        if res.get("ok"):
            db.update_mcp_server(row["id"], tools=res["tools"], last_error="")
            return {"ok": True, "server": row["name"],
                    "tools": [{"name": t["name"], "description": t["description"],
                               "args": list((t.get("input_schema") or {}).get("properties") or {})}
                              for t in res["tools"]]}
        db.update_mcp_server(row["id"], last_error=res.get("reason") or "нет связи")
        return {"ok": False, "reason": res.get("reason") or "нет связи",
                "servers": [r["name"] for r in rows]}
    res = mcp.call_tool(cfg, name, args or {})
    if not res.get("ok"):
        db.update_mcp_server(row["id"], last_error=res.get("reason") or res.get("text") or "")
    return {"ok": res.get("ok", False), "server": row["name"], "tool": name,
            "text": res.get("text") or "", "images": res.get("images") or [],
            "structured": res.get("structured") or {},
            "reason": res.get("reason") or ""}


def _skill_tool(user_id, name=""):
    """Инструмент агента: список скиллов или полный текст одного."""
    try:
        rows = db.list_skills(user_id) if user_id else []
    except Exception:                       # noqa: BLE001
        rows = []
    if not name:
        return {"ok": True, "skills": [{"name": r["name"],
                                        "enabled": bool(r["enabled"]),
                                        "chars": len(r["body"] or "")} for r in rows]}
    if not (name or "").strip().lstrip("@"):
        return {"ok": True, "skills": [{"name": r["name"]} for r in rows]}
    row = db.find_skill(user_id, str(name).strip().lstrip("@"))
    if not row:
        return {"ok": False, "reason": "такого скилла нет: %s" % name,
                "skills": [r["name"] for r in rows]}
    return {"ok": True, "name": row["name"], "enabled": bool(row["enabled"]),
            "body": row["body"] or ""}


def _attach_prompt(pid, limit=12):
    """Кусок системного промпта про вложения (пустая строка, если файлов нет)."""
    att = attached_files(pid)
    if not att:
        return ""
    lines = ["ВЛОЖЕНИЯ ПОЛЬЗОВАТЕЛЯ (в его задаче они помечены через @):"]
    for a in att:
        lines.append("- @%s → %s (%s, %s)" % (a["name"], a["rel"], a["kind"], files.human(a["size"])))
        if a["digest"]:
            lines.append("  внутри: " + a["digest"])
    lines.append('Полный текст любого вложения: инструмент attached(name="имя файла").')
    return "\n".join(lines) + "\n"


def _attached_tool(pid, name=""):
    """Инструмент агента: список вложений или текст одного файла."""
    att = attached_files(pid)
    if not name:
        return {"ok": True, "files": [{"name": a["name"], "path": a["rel"],
                                       "kind": a["kind"], "size": a["size"]} for a in att]}
    low = str(name).strip().lstrip("@").lower()
    match = next((a for a in att if a["name"].lower() == low), None) or \
            next((a for a in att if a["name"].lower().startswith(low)), None)
    if not match:
        return {"ok": False, "reason": "такого вложения нет: %s" % name,
                "files": [a["name"] for a in att]}
    text = files.extract_text(match["path"], limit=getattr(files, "MAX_TEXT", 20000))
    return {"ok": True, "name": match["name"], "kind": match["kind"],
            "size": match["size"], "path": match["rel"],
            "text": text or "текст не извлекается (бинарный формат или скан)"}


def _fallback_brief(pid, project):
    """План по факту: если планировщик не вернул текст (демо/сбой), строим его из цели и файлов."""
    goal = (project or {}).get("goal") or "проект"
    files = [f["path"] for f in tools.tree(pid)]
    return ("1) цель: %s\n2) файлы в работе: %s\n3) порядок: каркас → код → проверка → README"
            % (goal, ", ".join(files) or "пока пусто"))


def _inbox_mark(agent):
    rows = db.agent_inbox(agent["id"], 1)
    return rows[0]["id"] if rows else 0


def _inbox_since(agent, mark):
    return db.many(
        "SELECT m.id, m.text, m.from_agent, a.nick AS from_nick FROM agent_msgs m "
        "LEFT JOIN agents a ON a.id = m.from_agent "
        "WHERE m.to_agent = ? AND m.id > ? ORDER BY m.id ASC",
        (agent["id"], mark or 0))


def _resolve_agent(pid, who):
    """Ник, @ник, начало ника или id → запись агента."""
    who = (who or "").strip()
    if not who:
        return None
    agents = db.list_agents(pid)
    for a in agents:
        if a["id"] == who:
            return a
    low = who.lower().lstrip("@")
    for a in agents:
        if (a["nick"] or "").lower() == low:
            return a
    for a in agents:
        if (a["nick"] or "").lower().startswith(low):
            return a
    return None


def _send_msg(pid, agent, nick, to_raw, text):
    """
    Сообщение коллеге. В базе адресат хранится по id — иначе получатель его не найдёт
    (баг: писали ник, а искали по id). В событие кладём и ник, чтобы UI показывал человека.
    """
    txt = (text or "").strip()
    if not txt:
        return "пустое сообщение"
    target = _resolve_agent(pid, to_raw)
    if not target:
        return "нет такого агента: %s" % (to_raw or "?")
    if target["id"] == agent["id"]:
        return "себе писать незачем"
    db.add_agent_msg(pid, agent["id"], target["id"], txt)
    # текст сообщения — отдельным аргументом: раньше сюда попадал dict и текст терялся
    emit(pid, agent["id"], "message", txt,
         {"to": target["id"], "to_nick": target["nick"], "from_nick": nick, "text": txt}, nick=nick)
    return "отправлено %s" % target["nick"]


def _main_agent(pid):
    return next((a for a in db.list_agents(pid) if a["lane"] == "main"), None)


def _main_checks(pid, ready, empty, report):
    """Проверки, которые делает главный: без модели, по факту файлов."""
    tree = tools.tree(pid, max_files=60)
    paths = [f["path"] for f in tree]
    issues = []
    if not paths:
        issues.append("в общем workspace нет файлов: агенты ничего не сдали")
    html = [p for p in paths if p.lower().endswith((".html", ".htm"))]
    for h in html[:5]:
        try:
            body = tools.read_file(pid, h)
        except Exception:  # noqa: BLE001
            continue
        if "<html" in body.lower() and "</html>" not in body.lower():
            issues.append("%s: не закрыт </html>" % h)
        for m in re.finditer(r'(?:href|src)="([^"#:]+)"', body):
            ref = m.group(1).lstrip("./")
            if ref and not ref.startswith(("http", "mailto", "tel", "data:", "/")) and ref not in paths:
                issues.append("%s: ссылка на несуществующий %s" % (h, ref))
    for c in report["conflicts"][:5]:
        issues.append("конфликт в %s: оставил версию «%s», остальные — в %s"
                      % (c["path"], c["winner"], (c.get("kept_as") or "_conflicts/")))
    if not any(p.lower().startswith("readme") for p in paths):
        issues.append("нет README")
    return {"files": len(paths), "agents": len(ready), "empty": [a["nick"] for a, _ in empty],
            "issues": issues[:20], "paths": paths[:40]}


def _assemble_site(pid):
    """
    Главный агент сводит разрозненные куски в одну страницу:
    sections/*.html → index.html (с базовой вёрсткой и склеенными стилями агентов).
    Работает детерминированно, поэтому результат есть даже без ключа модели.
    """
    project = db.get_project(pid) or {}
    title = project.get("name") or "Проект"
    files = [f["path"] for f in tools.tree(pid, max_files=400)]
    sections = sorted(f for f in files if f.startswith("sections/") and f.endswith(".html"))
    css = sorted(f for f in files if f.startswith("styles/") and f.endswith(".css") and "all.css" not in f)
    if not sections and not css:
        return {"index": False, "styles": False, "sections": 0, "css": 0}

    css_all = []
    for rel in css:
        try:
            css_all.append("/* %s */\n%s" % (rel, tools.read_file(pid, rel)))
        except Exception:  # noqa: BLE001
            pass
    if css_all:
        tools.write_file(pid, "styles/all.css", "\n".join(css_all))

    parts, seen = [], set()
    for rel in sections:
        try:
            body = tools.read_file(pid, rel).strip()
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"<h[12][^>]*>(.*?)</h[12]>", body, re.S | re.I)
        heading = re.sub(r"<[^>]+>", "", m.group(1)).strip().lower() if m else rel
        if heading in seen:          # два агента сделали «Тарифы» — оставляем один блок
            continue
        seen.add(heading)
        parts.append(body)

    if parts:
        html = ("<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                "<title>%s</title>\n<style>\n%s\n%s\n</style>\n</head>\n<body>\n"
                "<header class=\"site-head\"><span class=\"logo\">%s</span>"
                "<nav><a href=\"#top\">Главная</a><a href=\"#contact\">Контакты</a></nav></header>\n"
                "<main>\n%s\n</main>\n"
                "<footer id=\"contact\"><b>%s</b><p>Собрано роем MRC Agent: %d агентов, %d файлов.</p></footer>\n"
                "</body>\n</html>\n"
                % (title, _BASE_CSS, "\n".join(css_all), title,
                   "\n".join(parts), title,
                   len(db.list_agents(pid)), len(files)))
        tools.write_file(pid, "index.html", html)
    return {"index": bool(parts), "styles": bool(css_all),
            "sections": len(parts), "css": len(css_all)}


_BASE_CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,Arial,sans-serif;
  color:#14161a;background:#fff;line-height:1.55}
.site-head{display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:18px clamp(16px,5vw,64px);border-bottom:1px solid #eceef2;position:sticky;top:0;
  background:rgba(255,255,255,.92);backdrop-filter:blur(8px)}
.logo{font-weight:700;letter-spacing:-.02em;min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.site-head nav{display:flex;gap:18px;font-size:14px}
.site-head a{color:#4b5563;text-decoration:none}
main{padding:clamp(16px,4vw,40px) clamp(16px,5vw,64px);display:grid;gap:20px;
  grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.block,section.block{margin:0;padding:32px;border:1px solid #eceef2;border-radius:18px;background:#fbfbfd}
.block h2{margin:0 0 10px;font-size:clamp(20px,2.4vw,28px);letter-spacing:-.02em}
.block p{margin:0 0 18px;color:#4b5563}
.btn{display:inline-block;padding:11px 20px;border-radius:999px;background:#111827;color:#fff;
  text-decoration:none;font-size:14px;font-weight:600}
.btn:hover{background:#7c5cff}
footer{padding:36px clamp(16px,5vw,64px);border-top:1px solid #eceef2;color:#6b7280;font-size:14px}
footer p{margin:6px 0 0}
"""


def _report_md(pid, checks, report, rt):
    p = db.get_project(pid) or {}
    lines = ["# Отчёт сборки", "",
             "**Проект:** %s" % (p.get("name") or ""),
             "**Цель:** %s" % (p.get("goal") or ""), "",
             "## Что сделано", "",
             "- агентов с работой: %d" % checks["agents"],
             "- файлов в общем workspace: %d" % checks["files"],
             "- токенов: %s" % (("вход %d / выход %d" % (rt.tokens_in, rt.tokens_out))
                                if (rt.tokens_in or rt.tokens_out) else "демо-режим, расход условный"),
             "",
             "## Файлы", ""]
    lines += ["- `%s`" % x for x in checks["paths"]] or ["- пусто"]
    lines += ["", "## Замечания главного", ""]
    lines += ["- %s" % i for i in checks["issues"]] or ["- замечаний нет"]
    if checks["empty"]:
        lines += ["", "## Ничего не сдали", ""] + ["- %s" % n for n in checks["empty"][:20]]
    return "\n".join(lines) + "\n"


def _readme_md(pid, checks):
    p = db.get_project(pid) or {}
    return ("# %s\n\n%s\n\nСобрано роем MRC Agent: %d агентов, %d файлов.\n\n"
            "## Файлы\n\n%s\n" % (
                p.get("name") or "Проект", p.get("goal") or "", checks["agents"], checks["files"],
                "\n".join("- `%s`" % x for x in checks["paths"]) or "- пусто"))


def place_of(scope):
    """Куда агент пишет: общий проект или личная песочница (для консоли и логов)."""
    return "песочница" if scope else "проект"


def _action_key(act):
    """Отпечаток действия: одинаковые write_file/run ловим как зацикливание."""
    try:
        return (act.get("tool") or "?") + "|" + hashlib.sha1(
            json.dumps(act, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return str(act)[:64]


def _usage(rt, agent, res, _tokens):
    """Реальный usage модели → проект (иначе стоимость в UI приходится выдумывать)."""
    u = res.get("usage") or {}
    tin, tout = int(u.get("in") or 0), int(u.get("out") or 0)
    with rt.lock:
        rt.tokens_in += tin
        rt.tokens_out += tout
    try:
        db.q("UPDATE projects SET tokens_in = COALESCE(tokens_in, 0) + ?, "
             "tokens_out = COALESCE(tokens_out, 0) + ? WHERE id = ?", (tin, tout, rt.pid))
    except Exception:  # noqa: BLE001
        pass


def _demo_cmd_out(cmd):
    """Правдоподобный вывод для демо: команды на машине пользователя не выполняются."""
    c = (cmd or "").lower()
    if "-c" in c and "print(" in c:
        import re as _re
        m = _re.search(r"print\((['\"])(.*?)\1\)", cmd)
        return m.group(2) if m else "ok"
    if "test" in c or "pytest" in c:
        return "collected 7 items\n\ntests/test_app.py ....... [100%]\n\n7 passed in 0.42s"
    if "npm" in c or "build" in c:
        return "vite v5.0.10 building for production...\n✓ 42 modules transformed.\ndist/index.html  0.46 kB\ndist/assets/index-4f2a.js  18.7 kB\n✓ built in 1.21s"
    if "git" in c:
        return "[main 9c1f2ab] демо-коммит\n 5 files changed, 214 insertions(+)"
    if "pip" in c or "install" in c:
        return "Successfully installed 4 packages (демо: установка не выполнялась)"
    if "ls" in c or "dir" in c:
        return "index.html\nstyles.css\napp.js\nREADME.md"
    return "(демо) команда не выполнялась на этой машине — включите ключ и выключите «Демо» для настоящего терминала"



def _agent_task(agent, project):
    goal = (project or {}).get("goal") or "проект"
    lane = agent["lane"]
    g = goal[:90]
    return {
        "lead": "разложить цель «%s» на файлы и порядок работ" % g,
        "research": "выяснить требования и API для «%s»" % g,
        "design": "спроектировать структуру и интерфейсы для «%s»" % g,
        "code": "написать модуль проекта «%s»" % g,
        "test": "проверить модули и найти падения для «%s»" % g,
        "review": "найти дефекты в коде «%s»" % g,
        "docs": "описать проект «%s» в README" % g,
    }.get(lane, "помочь с «%s»" % g)


DEMO_FILES = [
    ("index.html", "text"),
    ("style.css", "text"),
    ("app.js", "text"),
    ("README.md", "text"),
    ("main.py", "text"),
]


def _scaffold_demo(pid):
    """Демо-проект: реальные файлы, чтобы Workspace, preview и скачивание работали."""
    html = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MRC Demo</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
  <header><span class="dot"></span> demo project · generated by 100 agents</header>
  <main>
    <h1>Привет из workspace</h1>
    <p>Этот файл собрали агенты MRC Agent. Правь его и смотри preview.</p>
    <button id="btn">Нажми меня</button>
    <p id="out"></p>
  </main>
  <script src="app.js"></script>
</body>
</html>
"""
    css = """*{box-sizing:border-box}
body{
  margin:0;min-height:100vh;display:grid;place-items:center;
  font:16px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif;
  background:radial-gradient(1200px 600px at 20% -10%,#1b1440,#0a0b0f 60%);
  color:#e9eaf0;
}
header{position:fixed;top:0;left:0;right:0;padding:14px 20px;font-size:13px;
  color:#8c90a0;display:flex;gap:10px;align-items:center;border-bottom:1px solid rgba(255,255,255,.07)}
.dot{width:8px;height:8px;border-radius:50%;background:#7c5cff;box-shadow:0 0 12px #7c5cff}
main{max-width:520px;padding:40px 24px;text-align:center}
h1{font-size:28px;margin:0 0 10px;letter-spacing:-.02em}
p{color:#a7abbb}
button{margin-top:18px;padding:11px 22px;border-radius:12px;border:1px solid rgba(255,255,255,.12);
  background:linear-gradient(180deg,#2a2350,#211b3f);color:#fff;font-size:15px;cursor:pointer;
  transition:transform .15s ease, box-shadow .15s ease}
button:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(124,92,255,.35)}
#out{color:#7c5cff;font-weight:600;min-height:24px}
"""
    js = """const btn = document.getElementById('btn');
const out = document.getElementById('out');
let n = 0;
btn.addEventListener('click', () => {
  n += 1;
  out.textContent = 'нажатий: ' + n;
  btn.animate([{transform:'scale(.96)'},{transform:'scale(1)'}],{duration:150});
});
"""
    readme = """# Demo project

Файлы этого проекта созданы агентами MRC Agent в демо-режиме.
Чтобы получить настоящий код — вставь API-ключ Inception в настройках
и выключи демо-режим.

## Запуск

```bash
python main.py
# или открой index.html в браузере
```
"""
    main_py = '''"""Точка входа демо-проекта."""

def main() -> None:
    print("hello from mercury studio")


if __name__ == "__main__":
    main()
'''
    for name, content in (("index.html", html), ("style.css", css), ("app.js", js),
                          ("README.md", readme), ("main.py", main_py)):
        try:
            tools.write_file(pid, name, content)
        except Exception:
            pass


def _demo_steps(lane, nick, task, peers=None, topic="проект", idx=0):
    """
    Сценарий демо-агента. Каждый агент пишет НАСТОЯЩИЕ файлы, но в своём личном workspace —
    главный агент потом сведёт их в общий. Ники и тексты привязаны к теме проекта.
    """
    real_peers = [p for p in (peers or []) if p and p != nick]
    peer = random.choice(real_peers) if real_peers else "lead"
    n = "%02d" % (idx or 1)
    base = []

    if lane == "lead":
        base = [
            ("think", "читаю цель и прикидываю объём: %s" % topic, {}),
            ("think", "раскладываю работу на %d частей" % random.randint(5, 9), {}),
            ("msg", "передаю план разведке", {"msg": peer, "msg_text": "план готов, смотри файлы"}),
            ("write", "фиксирую план и порядок этапов",
             {"file": "план.md", "action": "write", "content":
              "# План\n\nТема: %s\n\n1. разведка фактов\n2. структура и макет\n"
              "3. секции и стили\n4. проверка\n5. сборка и README\n" % topic}),
        ]
    elif lane == "research":
        base = [
            ("search", "ищу: " + topic, {"web": "https://duckduckgo.com/?q=" + topic.replace(" ", "+")}),
            ("browse", "открываю документацию и вычитываю разделы", {"web": "https://docs.python.org/3/"}),
            ("think", "свожу находки в короткие тезисы", {}),
            ("write", "складываю факты в заметку",
             {"file": "исследование-%s.md" % n, "action": "write", "content":
              "# Разведка: %s\n\n- аудитория: те, кто ищет %s\n"
              "- что показать в первом экране: выгода и цена\n"
              "- обязательные блоки: оффер, преимущества, тарифы, отзывы, контакты\n"
              "- конкуренты делают ставку на простые формы и мобильную вёрстку\n" % (topic, topic)}),
            ("msg", "отправляю тезисы архитектору",
             {"msg": peer, "msg_text": "нашёл %d ограничений по теме «%s»" % (random.randint(2, 6), topic)}),
        ]
    elif lane == "design":
        base = [
            ("think", "проектирую структуру: %s" % topic, {}),
            ("write", "описываю блоки и сетку",
             {"file": "структура-%s.md" % n, "action": "write", "content":
              "# Структура\n\n1. шапка с меню\n2. первый экран\n3. секция «%s»\n"
              "4. тарифы / условия\n5. отзывы\n6. подвал с контактами\n" % topic}),
            ("msg", "согласую контракт с программистом",
             {"msg": peer, "msg_text": "классы и имена файлов такие, не меняй"}),
        ]
    elif lane == "code":
        block = random.choice(["Оффер", "Преимущества", "Тарифы", "Отзывы", "Контакты", "Вопросы"])
        base = [
            ("code", "пишу секцию «%s»" % block, {}),
            ("write", "складываю секцию в файл",
             {"file": "sections/%s-%s.html" % (n, _slug(block)), "action": "write", "content":
              '<section class="block block-%s">\n  <h2>%s</h2>\n'
              '  <p>%s — коротко и по делу.</p>\n  <a class="btn" href="#contact">Оставить заявку</a>\n'
              '</section>\n' % (_slug(block), block, topic.capitalize())}),
            ("write", "добавляю стили блока",
             {"file": "styles/%s-%s.css" % (n, _slug(block)), "action": "write", "content":
              '.block-%s { padding: 48px 24px; border-radius: 16px; }\n'
              '.block-%s h2 { font-size: 28px; margin: 0 0 12px; }\n' % (_slug(block), _slug(block))}),
            ("test", "проверяю разметку", {"cmd": "python -c \"print('собрано')\""}),
            ("msg", "отдаю работу на ревью", {"msg": peer, "msg_text": "секция «%s» готова" % block}),
        ]
    elif lane == "test":
        base = [
            ("test", "запускаю прогон", {"cmd": "python -m pytest -q"}),
            ("test", "проверяю краевые случаи: пустой ввод", {"cmd": "python -c \"print('ok')\""}),
            ("write", "складываю отчёт прогона",
             {"file": "тесты/проверка-%s.md" % n, "action": "write", "content":
              "# Проверка %s\n\n- секции открываются: да\n- ссылки в меню: 5 из 5\n"
              "- мобильная ширина 375px: без горизонтального скролла\n- замечаний: 0\n" % n}),
        ]
    elif lane == "review":
        base = [
            ("review", "читаю код и ищу дефекты", {"file": "index.html", "action": "read"}),
            ("think", "свожу замечания по severity", {}),
            ("write", "пишу ревью файлом",
             {"file": "ревью-%s.md" % n, "action": "write", "content":
              "# Ревью %s\n\n- high: у кнопки нет aria-label\n- med: отступы в секции тарифов разные\n"
              "- low: alt у картинок пустой\n" % n}),
            ("msg", "возвращаю дефекты программисту",
             {"msg": peer, "msg_text": "3 замечания: 1 high, 2 med"}),
        ]
    else:  # docs
        base = [
            ("write", "пишу инструкции",
             {"file": "docs/как-собрать-%s.md" % n, "action": "write", "content":
              "# Как собрать «%s»\n\nВсе секции лежат в `sections/`, стили — в `styles/`.\n"
              "Главный агент сводит их в `index.html`.\n" % topic}),
            ("think", "формулирую ограничения и как запускать", {}),
            ("browse", "сверяюсь с документацией", {"web": "https://docs.python.org/3/"}),
        ]

    if random.random() < 0.5:
        base.insert(random.randint(0, len(base)), ("think", random.choice([
            "перечитываю задачу и уточняю формулировку",
            "проверяю, что не дублирую работу соседа",
            "смотрю текущее состояние своего workspace",
        ]), {}))
    return base


def _slug(text):
    """«Преимущества» → «преимущества»; для имён файлов."""
    import re as _re
    return _re.sub(r"[^\wа-яё]+", "-", (text or "").lower()).strip("-")[:24] or "часть"


def _parse_actions_report(text):
    """Разбираем блоки action. Возвращаем и сломанные — чтобы это было видно в консоли."""
    out, broken = [], []
    for m in re.finditer(r"```(?:action|json)?\s*(\{.*?\})\s*```", text or "", flags=re.S):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("tool"):
                out.append(obj)
            else:
                broken.append((m.group(1)[:200], "нет поля tool"))
        except Exception as e:  # noqa: BLE001
            broken.append((m.group(1)[:200], str(e)[:120]))
    return out, broken       # без потолка: выполняем все действия из ответа модели


def _parse_actions(text):
    return _parse_actions_report(text)[0]
