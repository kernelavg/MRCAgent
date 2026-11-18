"""MRCIDE: локальные сборки ограниченного набора форматов без shell-интерполяции."""
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import db
import tools

LOCK = threading.RLock()
RUNNING = {}
TARGETS = ('android', 'windows', 'ios_simulator')


def _project(pid):
    if not db.get_project(pid):
        raise ValueError('проект не найден')
    return tools.workspace_for(pid)


def recipe(pid, target, scheme=''):
    root = _project(pid)
    if target == 'android':
        wrapper = root / ('gradlew.bat' if os.name == 'nt' else 'gradlew')
        if not wrapper.is_file():
            raise ValueError('Нет Gradle Wrapper в корне проекта (gradlew и gradle/wrapper).')
        if not (root / 'gradle' / 'wrapper' / 'gradle-wrapper.jar').is_file():
            raise ValueError('Не найден gradle/wrapper/gradle-wrapper.jar.')
        if not shutil.which('java'):
            raise ValueError('Не найдена Java. Установите JDK, подходящий проекту.')
        if not (os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or (root / 'local.properties').is_file()):
            raise ValueError('Не найден путь к Android SDK: задайте ANDROID_HOME или local.properties.')
        if os.name == 'nt':
            return ['cmd.exe', '/d', '/c', str(wrapper), 'assembleDebug', '--no-daemon']
        return ['sh', str(wrapper), 'assembleDebug', '--no-daemon']
    if target == 'windows':
        if os.name != 'nt':
            raise ValueError('Для EXE-сборки этого типа нужна Windows и Python с PyInstaller.')
        specs = sorted(root.glob('*.spec'))
        if len(specs) != 1:
            raise ValueError('Нужен ровно один PyInstaller .spec в корне проекта.')
        python = shutil.which('python') or shutil.which('python3')
        if not python:
            raise ValueError('Не найден Python с установленным PyInstaller.')
        return [python, '-m', 'PyInstaller', str(specs[0]), '--noconfirm', '--clean']
    if target == 'ios_simulator':
        if sys.platform != 'darwin':
            raise ValueError('Для iOS Simulator нужны macOS и Xcode.')
        if not shutil.which('xcodebuild'):
            raise ValueError('Не найден xcodebuild. Установите Xcode.')
        scheme = str(scheme or '').strip()
        if not re.fullmatch(r'[\w .-]+', scheme):
            raise ValueError('Укажите корректное имя Xcode Scheme.')
        projects = sorted(root.glob('*.xcworkspace')) or sorted(root.glob('*.xcodeproj'))
        if len(projects) != 1:
            raise ValueError('Нужен один .xcworkspace или .xcodeproj в корне проекта.')
        flag = '-workspace' if projects[0].suffix == '.xcworkspace' else '-project'
        return ['xcodebuild', flag, str(projects[0]), '-scheme', scheme, '-sdk', 'iphonesimulator',
                '-configuration', 'Debug', '-derivedDataPath', str(root / '.mrcide-derived'), 'build', 'CODE_SIGNING_ALLOWED=NO']
    raise ValueError('неизвестная платформа')


def diagnostics(pid, target, scheme=''):
    try:
        cmd = recipe(pid, target, scheme)
        return {'ready': True, 'command': cmd, 'message': 'Предварительные условия найдены; успешная сборка не гарантирована.'}
    except ValueError as exc:
        return {'ready': False, 'command': [], 'message': str(exc)}


def _find_artifacts(root, target, started):
    root = root.resolve()
    if target == 'android':
        candidates = root.glob('**/build/outputs/apk/debug/*.apk')
    elif target == 'windows':
        candidates = (root / 'dist').glob('*.exe')
    else:
        candidates = (root / '.mrcide-derived' / 'Build' / 'Products' / 'Debug-iphonesimulator').glob('*.app')
    artifacts = []
    for path in candidates:
        try:
            resolved = path.resolve()
            if root not in resolved.parents or path.is_symlink() or path.stat().st_mtime < started - 2:
                continue
            if target == 'android':
                if not zipfile.is_zipfile(path):
                    continue
                with zipfile.ZipFile(path) as archive:
                    if 'AndroidManifest.xml' not in archive.namelist():
                        continue
            if target == 'windows':
                with path.open('rb') as src:
                    header = src.read(64)
                    if len(header) < 64 or header[:2] != b'MZ':
                        continue
                    offset = struct.unpack_from('<I', header, 60)[0]
                    if offset < 64 or offset + 6 > path.stat().st_size:
                        continue
                    src.seek(offset)
                    signature = src.read(6)
                    if signature[:4] != b'PE\0\0' or struct.unpack_from('<H', signature, 4)[0] not in (0x14c, 0x8664, 0xaa64):
                        continue
            if target == 'ios_simulator':
                if not path.is_dir() or not (path / 'Info.plist').is_file():
                    continue
                output = root / '.mrcide-artifacts'
                output.mkdir(exist_ok=True)
                archive = shutil.make_archive(str(output / path.stem), 'zip', root_dir=str(path.parent), base_dir=path.name)
                resolved = Path(archive).resolve()
            artifacts.append(str(resolved.relative_to(root)).replace(os.sep, '/'))
        except (OSError, ValueError, zipfile.BadZipFile):
            continue
    return sorted(artifacts)


def get(pid, build_id):
    row = db.one('SELECT id,project_id,target,status,created_at,finished_at,code,message,artifacts,log_path FROM ide_builds WHERE project_id=? AND id=?', (pid, build_id))
    if not row:
        return None
    row['artifacts'] = db.jload(row.get('artifacts'), [])
    path = Path(row.pop('log_path'))
    try:
        row['log'] = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        row['log'] = ''
    return row


def list_builds(pid):
    rows = db.many('SELECT id FROM ide_builds WHERE project_id=? ORDER BY created_at DESC', (pid,))
    return [get(pid, row['id']) for row in rows]


def start(pid, target, scheme=''):
    cmd = recipe(pid, target, scheme)
    root = _project(pid)
    with LOCK:
        if pid in RUNNING:
            raise ValueError('Для проекта уже выполняется сборка.')
        bid = db.rid('b_')
        logdir = db.DATA / 'ide-build-logs'
        logdir.mkdir(parents=True, exist_ok=True)
        path = logdir / (bid + '.log')
        path.write_text('MRCIDE Studio: запуск сборки ' + target + '\n', encoding='utf-8')
        now = time.time()
        db.q('INSERT INTO ide_builds (id,project_id,target,status,created_at,artifacts,log_path) VALUES (?,?,?,?,?,?,?)',
             (bid, pid, target, 'running', now, '[]', str(path)))
        state = {'stop': threading.Event(), 'process': None, 'id': bid}
        RUNNING[pid] = state
        thread = threading.Thread(target=_worker, args=(pid, bid, target, root, cmd, path, state, now), daemon=True)
        try:
            thread.start()
        except Exception:
            RUNNING.pop(pid, None)
            db.q("UPDATE ide_builds SET status='error',finished_at=?,message=? WHERE id=?", (time.time(), 'Не удалось запустить сборку', bid))
            raise
    return get(pid, bid)


def _worker(pid, bid, target, root, cmd, path, state, started):
    status = 'error'
    code = None
    message = ''
    artifacts = []
    try:
        kwargs = {'cwd': str(root), 'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE,
                  'stderr': subprocess.STDOUT, 'env': dict(os.environ)}
        kwargs['env']['PYTHONIOENCODING'] = 'utf-8'
        if os.name == 'nt':
            kwargs['creationflags'] = 0x08000000 | 0x00000200
        else:
            kwargs['start_new_session'] = True
        with path.open('a', encoding='utf-8') as log:
            with LOCK:
                if state['stop'].is_set():
                    raise InterruptedError('остановлено пользователем')
                proc = subprocess.Popen(cmd, cwd=kwargs['cwd'], stdin=kwargs['stdin'], stdout=kwargs['stdout'], stderr=kwargs['stderr'], env=kwargs['env'], creationflags=kwargs.get('creationflags', 0), start_new_session=kwargs.get('start_new_session', False))
                state['process'] = proc
            with proc.stdout:
                for line in iter(proc.stdout.readline, b''):
                    log.write(line.decode('utf-8', 'replace'))
                    log.flush()
            code = proc.wait()
        if state['stop'].is_set():
            status, message = 'cancelled', 'Сборка остановлена.'
        elif code != 0:
            status, message = 'error', 'Команда сборки завершилась с ошибкой.'
        else:
            artifacts = _find_artifacts(root, target, started)
            if artifacts:
                status, message = 'done', 'Команда сборки завершилась; артефакты найдены. Требуется проверка на целевом устройстве.'
            else:
                status, message = 'error', 'Команда завершилась без ошибки, но новый ожидаемый артефакт не найден.'
    except InterruptedError:
        status, message = 'cancelled', 'Сборка остановлена.'
    except Exception as exc:
        status, message = 'error', str(exc)
        try:
            with path.open('a', encoding='utf-8') as log:
                log.write('\nОшибка запуска: ' + str(exc) + '\n')
        except OSError:
            pass
    finally:
        db.q('UPDATE ide_builds SET status=?,finished_at=?,code=?,message=?,artifacts=? WHERE id=?',
             (status, time.time(), code, message, db.jdump(artifacts), bid))
        with LOCK:
            RUNNING.pop(pid, None)


def stop(pid, bid):
    with LOCK:
        state = RUNNING.get(pid)
        row = get(pid, bid)
        if not state or state.get('id') != bid or not row or row['status'] != 'running':
            return False
        state['stop'].set()
        process = state.get('process')
        if process:
            tools._kill_tree(process)
        return True


def reconcile():
    db.q("UPDATE ide_builds SET status='interrupted',finished_at=?,message=? WHERE status='running'", (time.time(), 'Процесс приложения был остановлен; сборка не подтверждена.'))


def export_workflow(pid, target, scheme=''):
    root = _project(pid)
    if target == 'android':
        if not (root / 'gradlew').is_file() or not (root / 'gradle' / 'wrapper' / 'gradle-wrapper.jar').is_file():
            raise ValueError('Для GitHub нужен Gradle Wrapper с gradlew и gradle-wrapper.jar.')
        runner = 'ubuntu-latest'
        steps = '''      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '17'
      - name: Build Android debug APK
        run: sh gradlew assembleDebug --no-daemon
      - uses: actions/upload-artifact@v4
        with:
          name: android-debug-apk
          path: '**/build/outputs/apk/debug/*.apk'
          if-no-files-found: error
'''
    elif target == 'windows':
        specs = sorted(root.glob('*.spec'))
        if len(specs) != 1:
            raise ValueError('Для GitHub нужен ровно один PyInstaller .spec в корне.')
        runner = 'windows-latest'
        steps = '''      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          architecture: x64
      - name: Install dependencies
        shell: pwsh
        run: |
          python -m pip install pyinstaller
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
          if (Test-Path requirements.txt) { python -m pip install -r requirements.txt; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
      - name: Build EXE
        shell: pwsh
        run: |
          $specs = @(Get-ChildItem -File -Filter '*.spec')
          if ($specs.Count -ne 1) { throw 'Expected exactly one spec file' }
          python -m PyInstaller $specs[0].Name --noconfirm --clean
          if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      - uses: actions/upload-artifact@v4
        with:
          name: windows-exe
          path: 'dist/*.exe'
          if-no-files-found: error
'''
    elif target == 'ios_simulator':
        scheme = str(scheme or '').strip()
        if not re.fullmatch(r'[\w .-]+', scheme):
            raise ValueError('Укажите корректное имя Xcode Scheme.')
        projects = sorted(root.glob('*.xcworkspace')) or sorted(root.glob('*.xcodeproj'))
        if len(projects) != 1:
            raise ValueError('Для GitHub нужен один Xcode workspace или project в корне.')
        if not re.fullmatch(r'[\w .-]+', projects[0].stem):
            raise ValueError('Имя Xcode проекта содержит недопустимые символы.')
        runner = 'macos-latest'
        import json
        file_name = json.dumps(projects[0].name, ensure_ascii=False)
        safe_scheme = json.dumps(scheme, ensure_ascii=False)
        flag = '-workspace' if projects[0].suffix == '.xcworkspace' else '-project'
        steps = '''      - name: Build iOS Simulator app (unsigned)
        run: xcodebuild %s %s -scheme %s -sdk iphonesimulator -configuration Debug -derivedDataPath build build CODE_SIGNING_ALLOWED=NO
      - uses: actions/upload-artifact@v4
        with:
          name: ios-simulator-app
          path: 'build/Build/Products/Debug-iphonesimulator/*.app'
          if-no-files-found: error
''' % (flag, file_name, safe_scheme)
    else:
        raise ValueError('неизвестная платформа')
    workflow = '''name: MRCIDE build
on:
  workflow_dispatch:
  push:
jobs:
  build:
    runs-on: %s
    env:
      PYTHONUTF8: '1'
      PYTHONIOENCODING: utf-8
    steps:
      - uses: actions/checkout@v4
%s''' % (runner, steps)
    path = tools.resolve(pid, '.github/workflows/mrcide-build.yml')
    if path.exists() and path.read_text(encoding='utf-8') != workflow:
        raise ValueError('Файл .github/workflows/mrcide-build.yml уже существует и отличается; он не перезаписан.')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(workflow, encoding='utf-8')
    return {'path': '.github/workflows/mrcide-build.yml', 'message': 'Workflow создан в проекте. Для облачной сборки загрузите исходники в GitHub и запустите workflow там.'}


def export_project(pid):
    """ZIP сводного проекта для ручного размещения в репозитории, без личных каталогов."""
    import io
    root = _project(pid).resolve()
    buf = io.BytesIO()
    excluded_dirs = {'agents', '.git', '.gradle', '.venv', 'venv', 'node_modules', '__pycache__', '.mrcide-derived', '.mrcide-artifacts', 'build', 'dist'}
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in excluded_dirs and not (Path(folder) / d).is_symlink()]
            for name in files:
                p = Path(folder) / name
                if p.is_symlink() or not p.is_file() or root not in p.resolve().parents:
                    continue
                lower = name.lower()
                if lower.startswith('.env') or lower in ('local.properties', 'credentials.json', '.netrc') or lower.endswith(('.p12', '.pfx', '.jks', '.keystore', '.pem', '.key', '.db', '.sqlite', '.sqlite3')):
                    continue
                archive.write(p, str(p.relative_to(root)).replace(os.sep, '/'))
    return buf.getvalue()
