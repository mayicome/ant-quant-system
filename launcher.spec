# -*- mode: python ; coding: utf-8 -*-
# 打包命令：pyinstaller launcher.spec
# 生成的可执行文件在 dist/launcher.exe

import os

block_cipher = None

# 若项目根目录有 ant.ico/ant.png 等，则作为 exe/窗口图标（spec 同目录即项目根）
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

# 让按钮 logo 也能在打包后正常显示：把 launcher.py 里要用到的图片都打进 resources
resource_candidates = [
    "ant.ico",
    "ant.png",
    "ant_trade.png",
    "trade.png",
    "ant_strategy.png",
    "strategy.png",
    "ant_picker.png",
    "picker.png",
]
datas = []
for c in resource_candidates:
    p = os.path.join(_spec_dir, c)
    if os.path.isfile(p):
        datas.append((p, "."))

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    console=False,   # 不显示黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
