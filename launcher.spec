# -*- mode: python ; coding: utf-8 -*-
# 打包命令：pyinstaller launcher.spec
# 生成：dist/launcher.exe → 可复制到项目根覆盖 launcher.exe
#
# 只打包轻量引导 launcher_bootstrap.py（不含 PyQt）。
# GUI 由本机 python.exe 运行 launcher.py，避免关闭时
# 「Failed to remove temporary directory: ...\_MEI*」警告框。

import os

block_cipher = None

_spec_dir = os.path.dirname(os.path.abspath(SPECPATH))
icon_candidates = [
    "ant.ico",
    "ant.png",
]
icon_path = None
for c in icon_candidates:
    p = os.path.join(_spec_dir, c)
    if os.path.isfile(p):
        icon_path = p
        break

a = Analysis(
    ['launcher_bootstrap.py'],
    pathex=[_spec_dir],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 引导包刻意不含 GUI / 重量级库，保持 _MEI 可被干净删除
        "PyQt5",
        "PySide2",
        "PySide6",
        "matplotlib",
        "numpy",
        "pandas",
    ],
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
    name='launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
