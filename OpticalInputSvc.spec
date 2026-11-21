# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

mp_datas, mp_binaries, mp_hidden = collect_all('mediapipe')

a = Analysis(
    ['main.py'],
    pathex=['src'],
    binaries=mp_binaries,
    datas=mp_datas,
    hiddenimports=[
        'pystray._win32',
        'PIL.ImageTk',
        'PIL._tkinter_finder',
        *mp_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OpticalInputSvc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
