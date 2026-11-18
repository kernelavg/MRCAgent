"""Скачивание выбранного голоса с проверкой контрольных сумм."""
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

BASE = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx'
FILES = [('ruslan.onnx', BASE, '72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f'), ('ruslan.onnx.json', BASE + '.json', '706a4fb17bc304abd07809b552deae615e64dcbffbfbd09854ba37ca59e88117')]
DEST = Path(__file__).resolve().parent / 'static' / 'voices'

def digest(path):
    state = hashlib.sha256()
    with open(path, 'rb') as source:
        while True:
            data = source.read(1048576)
            if not data:
                break
            state.update(data)
    return state.hexdigest()

def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url, expected in FILES:
        path = DEST / name
        if path.exists() and digest(path) == expected:
            print('Уже есть:', name)
            continue
        print('Скачивание:', name, flush=True)
        fd, tempname = tempfile.mkstemp(prefix='voice-', dir=DEST)
        try:
            request = urllib.request.Request(url, headers={'User-Agent':'MRCAgent-Voice-Installer'})
            with urllib.request.urlopen(request, timeout=180) as response, os.fdopen(fd, 'wb') as out:
                while True:
                    chunk = response.read(1048576)
                    if not chunk:
                        break
                    out.write(chunk)
            if digest(tempname) != expected:
                raise RuntimeError('Контрольная сумма голоса не совпала: ' + name)
            os.replace(tempname, path)
            print('Проверено:', name)
        finally:
            if os.path.exists(tempname):
                os.remove(tempname)
    print('Голос Руслан установлен. Условия данных: CC BY-NC-SA 4.0; см. документацию.')

if __name__ == '__main__':
    main()
