# -*- mode: python ; coding: utf-8 -*-
# Archivo de configuración para PyInstaller
# Puedes editar este archivo para personalizar el build

import sys
from pathlib import Path
from importlib.metadata import version
from PyInstaller.utils.hooks import collect_all

# Avoid packaging an incompatible voice encryption library again.
for package, expected in (('discord.py', '2.7.1'), ('davey', '0.1.7')):
    if version(package) != expected:
        raise RuntimeError(f'{package} debe ser {expected}. Instalá requirements.txt antes de compilar.')

# Recolectar todas las dependencias
datas = []
binaries = []
hiddenimports = []
server_image = Path(SPECPATH) / 'assets' / 'server.png'
if server_image.is_file():
    datas.append((str(server_image), 'assets'))
    datas.append((str(Path(SPECPATH) / 'assets' / 'server-header.png'), 'assets'))

# yt-dlp
tmp_ret = collect_all('yt_dlp')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# VLC
tmp_ret = collect_all('vlc')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# pynput
tmp_ret = collect_all('pynput')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

# Discord voice, encryption and bundled FFmpeg.
for package in ():
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

a = Analysis(
    ['shared_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['discord', 'nacl', 'davey', 'imageio_ffmpeg'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LosPiCompartido',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Sin ventana de consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(Path(SPECPATH) / 'assets' / 'server.ico') if (Path(SPECPATH) / 'assets' / 'server.ico').is_file() else None,
)
