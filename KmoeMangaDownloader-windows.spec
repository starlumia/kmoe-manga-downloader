# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules


project_root = Path.cwd()
src_path = str(project_root / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

hiddenimports = collect_submodules("kmdr")
common_datas = []

cli = Analysis(
    ["src/kmdr/windows_cli.py"],
    pathex=[str(project_root), src_path],
    binaries=[],
    datas=common_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
cli_pyz = PYZ(cli.pure)
cli_exe = EXE(
    cli_pyz,
    cli.scripts,
    [],
    name="kmdr-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    exclude_binaries=True,
    disable_windowed_traceback=False,
)

gui = Analysis(
    ["src/kmdr/windows_gui.py"],
    pathex=[str(project_root), src_path],
    binaries=[],
    datas=common_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
gui_pyz = PYZ(gui.pure)
gui_exe = EXE(
    gui_pyz,
    gui.scripts,
    [],
    name="Kmoe Manga Downloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    exclude_binaries=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    gui_exe,
    cli_exe,
    gui.binaries,
    gui.zipfiles,
    gui.datas,
    cli.binaries,
    cli.zipfiles,
    cli.datas,
    name="Kmoe Manga Downloader",
)
