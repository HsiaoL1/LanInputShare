# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

project_root = Path(SPECPATH).parent
a = Analysis(
    [str(project_root / "src" / "lan_input_share" / "__main__.py")],
    pathex=[str(project_root / "src")],
    datas=[(str(project_root / "assets" / "lan-input-share.ico"), "assets")],
    hiddenimports=["Quartz", "AppKit", "Foundation", "ApplicationServices", "objc"],
    excludes=["win32api", "win32gui", "win32clipboard", "win32com", "pythoncom", "winreg"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LanInputShare",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    codesign_identity=os.environ.get("MACOS_CODESIGN_IDENTITY") or None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LanInputShare")
app = BUNDLE(
    coll,
    name="LanInputShare.app",
    bundle_identifier="com.laninputshare.app",
    info_plist={
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSAppleEventsUsageDescription": "读取 Finder 当前目录，以便粘贴文件前检查同名冲突。",
    },
)
