"""Windows CI smoke: onefile EXE, отдельный демон и статические файлы."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    source = Path(sys.argv[1]).resolve()
    if not source.is_file():
        raise RuntimeError('EXE не найден: ' + str(source))
    with tempfile.TemporaryDirectory(prefix='mrc-exe-smoke-') as folder:
        root = Path(folder)
        executable = root / 'MRCAgent.exe'
        shutil.copy2(source, executable)
        env = dict(os.environ)
        env['MS_DATA'] = str(root / 'data')
        env['MRC_NONINTERACTIVE'] = '1'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        def launch(arguments):
            proc = subprocess.run([str(executable)] + arguments, cwd=str(root), env=env, timeout=90)
            if proc.returncode:
                raise RuntimeError('EXE завершился с кодом %s; app.log=%s; daemon.log=%s' % (
                    proc.returncode,
                    (root / 'app.log').read_text(encoding='utf-8', errors='replace') if (root / 'app.log').exists() else 'нет',
                    (root / 'daemon.log').read_text(encoding='utf-8', errors='replace') if (root / 'daemon.log').exists() else 'нет'))
        try:
            launch(['--no-open', '--no-resume', '--port', str(port)])
            base = 'http://127.0.0.1:%d' % port
            with urllib.request.urlopen(base + '/', timeout=10) as resp:
                if b'MRC Agent' not in resp.read():
                    raise RuntimeError('HTML не найден')
            with urllib.request.urlopen(base + '/static/question-alert.wav', timeout=10) as resp:
                if resp.read(4) != b'RIFF':
                    raise RuntimeError('Аудио не найдено в собранном EXE')
            launch(['--no-open', '--no-resume'])
            port_file = root / 'data' / 'daemon.port'
            if port_file.read_text().strip() != str(port):
                raise RuntimeError('повторный запуск не нашёл свой демон')
            print('EXE запускается; демон пережил родителя; HTML и аудио доступны; повторный запуск нашёл демон')
        finally:
            launch(['--stop'])
            time.sleep(1)


if __name__ == '__main__':
    main()
