# -*- mode: python ; coding: utf-8 -*-
"""
Сборка одного файла:  pyinstaller mrcagent.spec
Результат: dist/MRCAgent.exe   (рядом создаётся папка data/ — там база агентов)
"""

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=collect_dynamic_libs('piper'),
    datas=[
        ('static', 'static'),       # интерфейс кладём внутрь бандла
    ] + collect_data_files('piper', includes=['espeak-ng-data/**']),
    hiddenimports=['webview', 'piper.espeakbridge', 'onnxruntime'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='MRCAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX ломает подпись и часто триггерит антивирус
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                 # без чёрного окна консоли
    disable_windowed_traceback=False,
    icon='static/app.ico',
)
