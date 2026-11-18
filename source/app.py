#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — MRC Agent: окно + фоновый демон с агентами.

Как это работает:
  1. app.py проверяет, жив ли фоновый сервер (data/…/studio.db + порт).
  2. Если нет — запускает его ОТДЕЛЬНЫМ процессом, который не умирает вместе с окном.
  3. Открывает своё окно: pywebview (WebView2), а если его нет — окно
     браузера в режиме --app (без вкладок и адресной строки). Вкладка
     обычного браузера — только последний запасной вариант.

Поэтому закрытие приложения не останавливает агентов: рой продолжает работать,
события пишутся в SQLite, а при следующем открытии вы видите всё как было.

  python app.py                 # окно + фоновый демон
  python app.py --foreground    # всё в одном процессе (для отладки)
  python app.py --no-open       # только демон (доступ с телефона по сети)
  python app.py --stop          # остановить демон и все рои
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

def base_dir():
    """Рядом с .exe (PyInstaller onefile распаковывает код во временную папку —
    __file__ там не наш, данные должны жить рядом с exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


FROZEN = bool(getattr(sys, "frozen", False))
BASE = base_dir()
DEFAULT_PORT = 8777


# --------------------------------------------------------------------------- #
# порт / health
# --------------------------------------------------------------------------- #


def port_free(host, port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def find_port(host, start):
    for p in range(start, start + 40):
        if port_free(host, p):
            return p
    raise RuntimeError("не удалось найти свободный порт рядом с %d" % start)


def health_info(port, timeout=1.5):
    """Здоровье + личность демона: instance/pid/данные."""
    try:
        headers = {"X-MRC-Instance": my_instance()}
        request = urllib.request.Request("http://127.0.0.1:%d/api/health" % port, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def health(port, timeout=1.5):
    return bool((health_info(port, timeout) or {}).get("ok"))


def my_instance():
    try:
        import db
        return db.instance_id()
    except Exception:  # noqa: BLE001
        return ""


def wait_health(port, seconds=25, instance=None, process=None):
    t0 = time.monotonic()
    expected = instance or my_instance()
    while time.monotonic() - t0 < seconds:
        info = health_info(port)
        if expected and info and info.get("ok") and info.get("instance") == expected:
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.25)
    return False


# --------------------------------------------------------------------------- #
# фоновый демон
# --------------------------------------------------------------------------- #


def spawn_daemon(port, host="127.0.0.1", resume=True):
    """Запускает server.py отдельным процессом, отвязанным от окна."""
    rotate_log(BASE / "daemon.log")             # 25: лог не растёт бесконечно
    if FROZEN:
        # в собранном exe нет server.py — просим тот же exe побыть сервером
        cmd = [sys.executable, "--daemon", "--host", host, "--port", str(port)]
    else:
        cmd = [sys.executable, str(BASE / "server.py"), "--host", host, "--port", str(port)]
    # 22: флаг --no-resume должен ДОХОДИТЬ до демона, иначе он будит проекты сам
    if not resume:
        cmd.append("--no-resume")
    # Фоновый процесс должен пережить закрытие окна и получить собственную
    # распаковку onefile. Используем публичный интерфейс PyInstaller 6.9+.
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if FROZEN:
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    kwargs = {"cwd": str(BASE), "stdin": subprocess.DEVNULL, "env": env}
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        log = open(BASE / "daemon.log", "ab", buffering=0)
        kwargs["stdout"] = log
        kwargs["stderr"] = log
    else:
        kwargs["start_new_session"] = True
        log = open(BASE / "daemon.log", "ab", buffering=0)
        kwargs["stdout"] = log
        kwargs["stderr"] = log
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        log.close()
    return proc


def rotate_log(path, limit=5 * 1024 * 1024, keep=2):
    """daemon.log не должен расти бесконечно: держим последние 5 МБ и одну копию."""
    try:
        if path.exists() and path.stat().st_size > limit:
            for i in range(keep - 1, 0, -1):
                src = path.with_suffix(path.suffix + ".%d" % i)
                dst = path.with_suffix(path.suffix + ".%d" % (i + 1))
                if src.exists():
                    if dst.exists():
                        dst.unlink()
                    src.rename(dst)
            first = path.with_suffix(path.suffix + ".1")
            if first.exists():
                first.unlink()
            path.rename(first)
    except OSError:
        pass


def find_daemon_port(prefer=0):
    """Порт демона без подсказки пользователя: файл порта → перебор окрестности 8777."""
    ports = []
    try:
        import db
        p = db.read_port_file()
        if p:
            ports.append(p)
    except Exception:  # noqa: BLE001
        pass
    if prefer:
        ports.append(prefer)
    ports += list(range(DEFAULT_PORT, DEFAULT_PORT + 40))
    seen = set()
    mine = my_instance()
    if not mine:
        return 0, None
    for p in ports:
        if p in seen:
            continue
        seen.add(p)
        info = health_info(p)
        if info and info.get("ok") and info.get("instance") == mine:
            return p, info
    return 0, None


def stop_daemon(port):
    info = health_info(port)
    mine = my_instance()
    if not mine or not info or info.get("instance") != mine:
        return False
    try:
        req = urllib.request.Request("http://127.0.0.1:%d/api/shutdown" % port,
                                     data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main():
    ap = argparse.ArgumentParser(prog="app.py", description="MRC Agent — 100 агентов на Mercury 3")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None, help="порт демона (по умолчанию 8777 или из data/daemon.port)")
    ap.add_argument("--no-open", action="store_true", help="не открывать окно/браузер")
    ap.add_argument("--foreground", action="store_true", help="сервер в этом же процессе")
    ap.add_argument("--no-window", action="store_true", help="без pywebview: окно браузера --app")
    ap.add_argument("--stop", action="store_true", help="остановить фоновый сервер и рои")
    ap.add_argument("--no-resume", action="store_true", help="не возобновлять проекты при старте")
    ap.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.port_explicit = args.port is not None
    if args.port is None:
        args.port = DEFAULT_PORT

    if args.stop:
        port = args.port
        if not args.port_explicit:
            found, info = find_daemon_port()
            if found:
                port = found
                print("нашёл демон на порту %d" % port)
            else:
                print("демон не найден — нечего останавливать")
                return 0
        ok = stop_daemon(port)
        print("сервер остановлен" if ok else "сервер не отвечал на 127.0.0.1:%d" % port)
        return 0

    # ---------- режим фонового сервера (так запускает себя собранный exe) ----------
    if args.daemon:
        import db
        import server
        db.connect()
        httpd = server.serve(args.host, args.port, resume=not args.no_resume)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    # ---------- режим «всё в одном процессе» ----------
    if args.foreground:
        import db
        import server
        db.connect()
        port = find_port(args.host, args.port)
        httpd = server.serve(args.host, port, resume=not args.no_resume)
        url = "http://%s:%d" % ("127.0.0.1" if args.host == "0.0.0.0" else args.host, port)
        banner(url)
        import threading
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        open_ui(url, args)
        return 0

    # ---------- ищем СВОЙ живой демон или поднимаем ----------
    mine = my_instance()
    start_port = args.port
    if not args.port_explicit:
        try:
            import db
            start_port = db.read_port_file() or DEFAULT_PORT
        except Exception:  # noqa: BLE001
            start_port = DEFAULT_PORT
    port = start_port
    info = health_info(port)
    if mine and info and info.get("ok") and info.get("instance") == mine:
        print("фоновый сервер уже работает на порту %d" % port)
    else:
        if info and info.get("ok"):
            # 24: на порту чужой демон (другая установка) — не подключаемся к нему
            print("порт %d занят другой программой — беру свободный" % port)
            port = find_port(args.host, port + 1)
        elif not port_free(args.host, port):
            port = find_port(args.host, port)
            print("порт занят, беру %d" % port)
        print("запускаю фоновый сервер на порту %d…" % port)
        proc = spawn_daemon(port, args.host, resume=not args.no_resume)
        if not wait_health(port, instance=mine, process=proc):
            tell_user("сервер не поднялся. Смотрите daemon.log рядом с приложением.")
            print("не удалось дождаться сервера — смотрите daemon.log")
            return 1

    url = "http://127.0.0.1:%d" % port
    banner(url)
    if args.no_open:
        print("агенты работают в фоне. Остановить: %s --stop"
              % ("MRCAgent.exe" if FROZEN else "python app.py"))
        return 0
    open_ui(url, args)
    return 0


def browser_app_path():
    """Chromium-браузеры открывают окно без вкладок и адресной строки (--app).
    Edge есть на любой Windows 10/11, Chrome — если установлен."""
    import shutil
    cands = []
    if sys.platform.startswith("win"):
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")]
        for r in roots:
            if not r:
                continue
            cands += [os.path.join(r, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(r, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    for name in ("msedge", "microsoft-edge", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "chrome"):
        path = shutil.which(name)
        if path:
            cands.append(path)
    for path in cands:
        if path and os.path.exists(path):
            return path
    return None


def open_app_window(url):
    """Окно приложения вместо вкладки: без адресной строки, без вкладок, свой профиль.
    Возвращает True, если окно запущено."""
    exe = browser_app_path()
    if not exe:
        return False
    profile = BASE / "data" / "app-profile"
    args = [exe, "--app=%s" % url, "--window-size=1380,900",
            "--no-first-run", "--no-default-browser-check",
            "--user-data-dir=%s" % profile]
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        print("окно приложения открыто (%s)" % os.path.basename(exe))
        log_line("окно приложения: %s" % os.path.basename(exe))
        return True
    except Exception as e:  # noqa: BLE001
        log_line("окно приложения не открылось: %s" % e)
        return False


def webview2_available():
    """Проверка Evergreen Runtime в реестре; None — платформу проверить нельзя."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        suffix = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for prefix in (r"SOFTWARE", r"SOFTWARE\WOW6432Node"):
                try:
                    with winreg.OpenKey(hive, prefix + "\\" + suffix) as key:
                        value, _ = winreg.QueryValueEx(key, "pv")
                        if isinstance(value, str) and value.strip() not in ("", "0.0.0.0"):
                            return True
                except OSError:
                    pass
        return False
    except Exception as exc:
        log_line("проверка WebView2 недоступна: %s" % exc)
        return None


def open_ui(url, args):
    native_available = webview2_available()
    if native_available is False:
        log_line("WebView2 Runtime не найден — использую браузер вместо устаревшего движка")
    if not args.no_window and native_available is not False:
        try:
            import webview  # pywebview
            webview.create_window(
                "MRC Agent", url, width=1380, height=900,
                min_size=(1000, 660), background_color="#0B0C0F",
            )
            try:
                webview.start(gui="edgechromium" if sys.platform == "win32" else None)
            except TypeError:
                if sys.platform == "win32":
                    raise
                webview.start()
            print("окно закрыто — агенты продолжают работать в фоне.")
            again = "MRC Agent" if FROZEN else "python app.py"
            print("открыть снова: %s   ·   остановить: %s --stop" % (again, again))
            return
        except Exception as e:  # нет pywebview / нет WebView2 / нет GUI — откроем браузер
            note = "нативное окно недоступно (%s) — открываю окно браузера" % e
            print(note)
            log_line(note)
    opened = open_app_window(url)
    if not opened:
        try:
            opened = bool(webbrowser.open(url))
        except Exception:  # noqa: BLE001
            opened = False
    msg = "интерфейс открыт. Агенты работают в фоне — окно можно закрыть."
    if not opened:
        # 23: окна нет, браузер не открылся — нельзя молча висеть невидимым процессом
        msg = ("не удалось открыть окно и браузер автоматически.\n"
               "Откройте вручную: %s" % url)
        tell_user(msg, url)
        return
    print(msg)
    log_line(msg)


def log_line(text):
    """Пишем в app.log: без консоли (exe без окна) иначе ничего не видно."""
    try:
        with open(BASE / "app.log", "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except OSError:
        pass


def tell_user(text, url=""):
    """Сказать человеку хоть чем-то, если консоли и окна нет."""
    log_line(text)
    if os.environ.get("MRC_NONINTERACTIVE") == "1":
        return
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, "MRC Agent", 0x00000040)  # MB_ICONINFORMATION
            return
        except Exception:  # noqa: BLE001
            pass
    print(text)
    if url:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass


def banner(url):
    line = "─" * 58
    print("\n  \033[1mMRC Agent\033[0m")
    print("  " + line)
    print("  UI:      \033[36m%s\033[0m" % url)
    try:
        import db
        data_path = db.DB_PATH                    # уважаем MS_DATA, если он задан
    except Exception:  # noqa: BLE001
        data_path = BASE / "data" / "studio.db"
    print("  данные:  %s" % data_path)
    print("  стоп:    %s" % ("MRCAgent.exe --stop" if FROZEN else "python app.py --stop"))
    print("  " + line + "\n", flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        log_line("ошибка запуска: " + traceback.format_exc())
        tell_user("Не удалось запустить MRC Agent: %s\n\nПроверьте app.log и daemon.log рядом с программой. Если папка защищена, перенесите EXE в папку пользователя." % exc)
        sys.exit(1)
