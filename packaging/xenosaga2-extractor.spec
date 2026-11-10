# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Xenosaga Episode II Python Extractor (release build).

The same shape as the Episode I and III kits: a one-folder bundle where a
single ``COLLECT`` puts BOTH binaries side by side, so the GUI always finds
its engine next to itself. Output goes under ``packaging/dist/`` (``build.py``
passes the paths), named for the platform so builds can coexist:

    packaging/dist/Xenosaga-II-Extractor-windows/   (built on Windows)
      Xenosaga-II-Extractor.exe     windowed launcher: the local web GUI
      xeno-cli.exe                  console: the CLI the GUI shells out to
      _internal/                    shared runtime

    packaging/dist/Xenosaga-II-Extractor.app        (built on macOS: double-click)
    packaging/dist/Xenosaga-II-Extractor-macos/     (same build, bare folder)

Every top-level module of the kit is bundled as a hidden import, found by
listing the repository, so a module added later (and imported lazily by
cli.py) is never left out of a release.

AV-friendliness, as in the sibling kits: one-folder rather than one-file (no
unpacking to a temp dir at launch), ``upx=False``, the GUI without a console
window and the CLI with one.

PyInstaller does not cross-compile: run it on the OS you want binaries for.

Build (from the kit root):
    python -m pip install pyinstaller
    python build.py --clean
"""
import sys
from pathlib import Path

HERE = Path(SPECPATH).resolve()
REPO = HERE.parent

PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")

ICON = HERE / "icon.ico"
ICON_ARG = str(ICON) if ICON.exists() else None

# README/LICENSE ride along so the unpacked folder explains itself.
DATAS = [
    (str(REPO / "README.md"), "."),
    (str(REPO / "LICENSE"), "."),
    (str(HERE / "tools" / "TOOLS.txt"), "tools"),
]
DATAS = [(src, dst) for src, dst in DATAS if Path(src).exists()]

# A portable ffmpeg dropped into packaging/tools/ (the release workflow
# downloads one per OS) lands in the bundle under tools/, where toolfind.py
# looks before PATH.
FFMPEG = HERE / "tools" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
BINARIES = [(str(FFMPEG), "tools")] if FFMPEG.exists() else []

# Every module of the kit (cli.py imports several of them lazily, inside the
# command functions; listing them all keeps a release complete).
HIDDEN = sorted(p.stem for p in REPO.glob("*.py") if p.stem not in ("gui", "cli", "build"))


def _analysis(entry):
    return Analysis(
        [str(REPO / entry)],
        pathex=[str(REPO)],
        binaries=BINARIES,
        datas=DATAS,
        hiddenimports=HIDDEN + ["cli"],
        hookspath=[],
        runtime_hooks=[],
        excludes=["tkinter", "test", "unittest"],
        noarchive=False,
    )


gui_a = _analysis("gui.py")
cli_a = _analysis("cli.py")

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name="Xenosaga-II-Extractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON_ARG,
)

cli_exe = EXE(
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="xeno-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=ICON_ARG,
)

coll = COLLECT(
    gui_exe,
    gui_a.binaries,
    gui_a.zipfiles,
    gui_a.datas,
    cli_exe,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    strip=False,
    upx=False,
    name=f"Xenosaga-II-Extractor-{PLATFORM}",
)

# macOS: also wrap the folder into a double-clickable .app. Both executables
# end up in Contents/MacOS/, so the GUI's sibling lookup for xeno-cli still
# resolves. PyInstaller ad-hoc-signs the bundle; a downloaded copy needs
# right-click -> Open once (unsigned developer).
if sys.platform == "darwin":
    ICNS = HERE / "icon.icns"
    app = BUNDLE(
        coll,
        name="Xenosaga-II-Extractor.app",
        icon=str(ICNS) if ICNS.exists() else None,
        bundle_identifier="io.github.linuxjessi.xenosaga2extractor",
        info_plist={
            "CFBundleName": "Xenosaga II Extractor",
            "CFBundleDisplayName": "Xenosaga II Extractor",
            "NSHighResolutionCapable": True,
        },
    )
