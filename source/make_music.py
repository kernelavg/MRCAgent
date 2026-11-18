#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_music.py — своя музыкальная тема для MRC Agent (без сторонних сэмплов).

Зачем свой синтез: в комплект нельзя положить чужую песню (у «Aria Math» есть
правообладатель), а тишина в интерфейсе скучная. Поэтому тема считается здесь
целиком из синусов: мягкое «пианино» арпеджио, тихий пэд, капля шума — 16 тактов,
которые бесшовно зацикливаются.

Выход: static/music/theme.ogg  (Vorbis, стерео, ~53 секунды, бесшовный луп)

Запуск:  python3 make_music.py
"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
BPM = 72.0
BEAT = 60.0 / BPM
BAR = BEAT * 4
BARS = 16
DUR = BAR * BARS                     # ~53.3 с

# Cmaj7 → Am7 → Fmaj7 → G6  (по такту, повторяется 4 раза)
CHORDS = [
    [60, 64, 67, 71],
    [57, 60, 64, 67],
    [53, 57, 60, 64],
    [55, 59, 62, 64],
]

rng = np.random.default_rng(7)
N = int(SR * DUR)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12.0)


def tone(freq, dur, amp=1.0, bright=0.25):
    """Мягкий колокольчик: основной тон + два обертона с быстрым спадом."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    env = np.exp(-t * 2.6) * (1 - np.exp(-t * 220))       # атака + спад
    w = (np.sin(2 * np.pi * freq * t)
         + bright * np.sin(2 * np.pi * freq * 2 * t) * np.exp(-t * 3.2)
         + bright * 0.6 * np.sin(2 * np.pi * freq * 3 * t) * np.exp(-t * 5.0))
    return amp * env * w


def pad(freq, dur, amp=1.0):
    """Тихий пэд: две слегка расстроенные синусоиды и очень медленная пульсация."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    wob = 1 + 0.06 * np.sin(2 * np.pi * 0.13 * t)
    w = (np.sin(2 * np.pi * freq * t) + np.sin(2 * np.pi * (freq * 1.003) * t)) * 0.5
    fade = np.minimum(1.0, np.minimum(t / 2.5, (dur - t) / 2.5))
    return amp * wob * w * np.clip(fade, 0, 1)


left = np.zeros(N, dtype=np.float64)
right = np.zeros(N, dtype=np.float64)


def add(sig, at, pan=0.5, gain=1.0):
    """Кладём фрагмент в общий буфер с панорамой (0 — лево, 1 — право)."""
    i = int(at * SR)
    n = min(len(sig), N - i)
    if n <= 0:
        return
    left[i:i + n] += sig[:n] * gain * (1 - pan)
    right[i:i + n] += sig[:n] * gain * pan


# ---- арпеджио: восьмые, тихо, с «дыханием» по громкости
step = BEAT / 2
k = 0
t = 0.0
while t < DUR:
    bar = int(t // BAR) % len(CHORDS)
    chord = CHORDS[bar]
    note = chord[k % len(chord)]
    if k % 8 == 0:
        note -= 12                                     # раз в такт — низкая нота
    gain = 0.30 + 0.14 * np.sin(2 * np.pi * t / (BAR * 4))
    add(tone(hz(note), 3.2, amp=1.0), t, pan=0.35 + 0.3 * ((k % 3) / 2), gain=gain)
    # редкие высокие «капли»
    if k % 8 == 5:
        add(tone(hz(note + 24), 1.6, amp=0.6), t, pan=0.75, gain=0.10)
    t += step
    k += 1

# ---- пэд: по такту, тихо под арпеджио
for b in range(BARS):
    chord = CHORDS[b % len(CHORDS)]
    at = b * BAR
    for j, note in enumerate(chord[:3]):
        add(pad(hz(note), BAR * 1.15, amp=0.16), at, pan=0.3 + 0.2 * j, gain=1.0)

# ---- шумовой «воздух»: очень тихий фильтрованный шум, чтобы не было цифровой пустоты
noise = rng.normal(0, 1, N)
kernel = np.ones(600) / 600.0
noise = np.convolve(noise, kernel, mode="same")
noise /= (np.abs(noise).max() or 1)
left += noise * 0.012
right += noise * 0.012

# ---- бесшовный луп: хвост (2 с) подмешиваем в начало, потом обрезаем
tail = int(2.0 * SR)
left[:tail] += left[-tail:]
right[:tail] += right[-tail:]
left, right = left[:-tail], right[:-tail]

# ---- мягкая нормализация и лёгкое «тепло»
mix = np.stack([left, right], axis=1)
mix = np.tanh(mix * 1.2) / np.tanh(1.2)
peak = np.abs(mix).max() or 1
mix = mix / peak * 0.72

out = Path(__file__).resolve().parent / "static" / "music" / "theme.ogg"
out.parent.mkdir(parents=True, exist_ok=True)
# libsndfile в этой сборке падает на одном огромном буфере: пишем кусками по секунде
mix = np.ascontiguousarray(mix, dtype=np.float64)
with sf.SoundFile(str(out), "w", samplerate=SR, channels=2,
                  format="OGG", subtype="VORBIS") as f:
    for i in range(0, len(mix), SR):
        f.write(mix[i:i + SR])
print("готово: %s · %.1f с · %d КБ" % (out, len(mix) / SR, out.stat().st_size // 1024))
print("пик: %.2f · RMS: %.3f" % (np.abs(mix).max(), float(np.sqrt((mix ** 2).mean()))))
sys.exit(0)
