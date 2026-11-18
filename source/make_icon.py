#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_icon.py — собирает иконку приложения из исходного арта.

Источник: build/icon-source.png (квадрат 1024, знак на тёмной плашке со скруглением).
Результат: build/icon.ico (256…16 px) + build/icon.png + static/mark.png для интерфейса.

Почему так: арт один, а размеров много. Крупные размеры — чистый LANCZOS,
мелкие (≤32) — с лёгким unsharp, иначе тонкие лучи знака «замыливаются» в кашу.

Запуск:  python make_icon.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

try:
    import numpy as np
except ImportError:          # numpy не обязателен
    np = None

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
STATIC = HERE / "static"

SOURCE = BUILD / "icon-source.png"
SIZES = [256, 128, 64, 48, 40, 32, 24, 20, 16]

VIOLET = (124, 92, 255)
DEEP = (58, 32, 168)


# --------------------------------------------------------------------------- #
# запасной вариант: если арта нет — рисуем знак сами
# --------------------------------------------------------------------------- #

def _rounded(size, radius=0.225):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * radius), fill=255)
    return m


def draw_fallback(size=1024):
    """Фиолетовая плашка + «M» из узлов (старый знак) — на случай, если арта нет."""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.225), fill=VIOLET)
    w = int(size * 0.105)
    pts = [(size * 0.245, size * 0.695), (size * 0.245, size * 0.315),
           (size * 0.5, size * 0.545), (size * 0.755, size * 0.315), (size * 0.755, size * 0.695)]
    d.line(pts, fill=(255, 255, 255, 255), width=w, joint="curve")
    for (x, y) in (pts[0], pts[1], pts[3], pts[4]):
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=(255, 255, 255, 255))
    r = size * 0.098
    d.ellipse([size * .5 - r, size * .545 - r, size * .5 + r, size * .545 + r], fill=(255, 255, 255, 255))
    return tile


# --------------------------------------------------------------------------- #
# сборка размеров
# --------------------------------------------------------------------------- #

def frame_at(base, n):
    """Один размер иконки. Мелкие — через промежуточный шаг, чтобы края не рвались."""
    if n >= 128:
        return base.resize((n, n), Image.LANCZOS)
    step = base.resize((n * 4, n * 4), Image.LANCZOS).resize((n, n), Image.LANCZOS)
    return step.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=1))


def main():
    BUILD.mkdir(exist_ok=True)
    STATIC.mkdir(exist_ok=True)

    if SOURCE.exists():
        base = Image.open(SOURCE).convert("RGB")
        # скругляем углы здесь, поэтому в арте может лежать обычный квадрат
        base = base.resize((1024, 1024), Image.LANCZOS).convert("RGBA")
        base.putalpha(_rounded(1024))
        print("источник: build/icon-source.png (%dx%d)" % Image.open(SOURCE).size)
    elif (BUILD / "icon.ico").exists():
        print("арта нет, но build/icon.ico уже есть — оставляю как есть")
        return
    else:
        base = draw_fallback(1024)
        print("источник: нарисовал сам (арта build/icon-source.png нет)")

    frames = [frame_at(base, n) for n in SIZES]
    ico = BUILD / "icon.ico"
    frames[0].save(ico, format="ICO", sizes=[(n, n) for n in SIZES], append_images=frames[1:])

    base.resize((1024, 1024), Image.LANCZOS).save(BUILD / "icon.png")
    # знак для интерфейса (шапка, окно входа)
    base.resize((96, 96), Image.LANCZOS).save(STATIC / "mark.png")

    print("иконка: %s — %s" % (ico, ", ".join(str(n) for n in SIZES)))
    print("превью: build/icon.png (1024), static/mark.png (96)")


if __name__ == "__main__":
    main()
