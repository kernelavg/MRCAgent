"""Локальный синтез русской речи Piper; текст не отправляется внешним сервисам."""
import io
import os
import sys
import threading
import wave
from pathlib import Path

_lock = threading.Lock()
_voice = None

class VoiceUnavailable(Exception):
    pass

def model_path():
    override = os.environ.get('MS_VOICE_MODEL')
    if override:
        return Path(override)
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return root / 'static' / 'voices' / 'ruslan.onnx'

def synthesize(text):
    global _voice
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Нет текста для озвучки')
    model = model_path()
    if not model.is_file() or not Path(str(model) + '.json').is_file():
        raise VoiceUnavailable('Голос Руслан не установлен. Выполните python download_voice.py и перезапустите MRCAgent.')
    if not _lock.acquire(blocking=False):
        raise VoiceUnavailable('Озвучивание уже выполняется. Дождитесь завершения.')
    try:
        if _voice is None:
            try:
                from piper import PiperVoice
            except ImportError as error:
                raise VoiceUnavailable('Не установлен Piper. Установите piper-tts==1.8.0 и перезапустите приложение.') from error
            _voice = PiperVoice.load(str(model), use_cuda=False)
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            _voice.synthesize_wav(text.strip(), wav)
        return output.getvalue()
    finally:
        _lock.release()
