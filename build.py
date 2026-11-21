#!/usr/bin/env python3
"""build.py — package the extractor into a self-contained one-folder bundle.

Drives ``packaging/xenosaga2-extractor.spec`` and writes, under
``packaging/dist/``:

    Xenosaga-II-Extractor-windows/     (built on Windows)
      Xenosaga-II-Extractor.exe        the GUI launcher: double-click this
      xeno-cli.exe                     the engine the GUI drives
    Xenosaga-II-Extractor.app          (built on macOS: double-click this)
    Xenosaga-II-Extractor-macos/       (same build, bare folder form)

Both binaries live in one folder, so the GUI always finds its engine beside
it. Ship the platform folder (zip it), or on macOS the .app. After a build
the bundle's own self-tests are run (``xeno-cli self-test`` and the GUI's
``--self-test``), so a bundle that is missing a module fails here, not on
someone else's machine.

Usage:
    python build.py            # build for THIS platform
    python build.py --clean    # wipe this platform's previous output first

PyInstaller does not cross-compile: build Windows binaries on Windows, the
.app on macOS. Output and work files stay inside packaging/ (ignored by
packaging/.gitignore), never at the repository root.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "packaging" / "xenosaga2-extractor.spec"
DIST = ROOT / "packaging" / "dist"
WORK = ROOT / "packaging" / "build"

PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
NAME = "Xenosaga-II-Extractor"
BUNDLE = DIST / f"{NAME}-{PLATFORM}"
APP = DIST / f"{NAME}.app"  # macOS only


START_HERE = """Xenosaga II Extractor
====================

1. Double-click  Xenosaga-II-Extractor.exe
   Your web browser opens the kit's window (it runs on your own computer;
   nothing is uploaded). Keep the program running while you use it.

2. If Windows says "Windows protected your PC": click "More info", then
   "Run anyway". The program is not code-signed, so Windows warns the
   first time.

3. In the window, follow "Start here": unpack your disc images (.iso),
   then convert them. You need your own copy of the game; no game data
   comes with the kit.

Do not run it from inside the zip: extract the whole folder first
(right-click the zip > Extract All).

xeno-cli.exe is the command-line version, for typing commands in a
terminal. The _internal folder holds what the program needs; leave it
next to the two .exe files.

Help and pictures: https://github.com/LinuxJessi/Xenosaga2PythonExtractor
"""


def _run_selftest(argv) -> bool:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  could not run {argv[0]}: {exc}")
        return False
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ["(no output)"]
    print(f"  {' '.join(Path(a).name if i == 0 else a for i, a in enumerate(argv))}: "
          f"exit {r.returncode}, {tail[0]}")
    if r.returncode:                    # say which checks failed, not only the last line
        for line in (r.stdout + r.stderr).splitlines():
            if "FAIL" in line or "Traceback" in line or "Error" in line:
                print("    " + line.encode("ascii", "backslashreplace").decode("ascii"))
    return r.returncode == 0


def main() -> int:
    try:
        from PyInstaller.__main__ import run as pyinstaller
    except ImportError:
        sys.exit("PyInstaller is not installed in this Python.\n"
                 "  python -m pip install pyinstaller\n"
                 "then re-run:  python build.py")
    for mod, why in (("numpy", "the music renderer"), ("fontTools", "new alphabets: glyph outlines"),
                     ("uharfbuzz", "new alphabets: shaping")):
        try:
            __import__(mod)
        except ImportError:
            sys.exit(f"{mod} ({why}) is not installed in this Python, and the bundle must carry it.\n"
                     "  python -m pip install -r requirements.txt\n"
                     "then re-run:  python build.py")
    if not SPEC.exists():
        sys.exit(f"spec not found: {SPEC}")

    argv = ["--noconfirm", "--distpath", str(DIST), "--workpath", str(WORK), str(SPEC)]
    if "--clean" in sys.argv:
        argv.insert(0, "--clean")
        shutil.rmtree(BUNDLE, ignore_errors=True)
        if sys.platform == "darwin":
            shutil.rmtree(APP, ignore_errors=True)

    print(f"=== pyinstaller {' '.join(argv)} ===", flush=True)
    pyinstaller(argv)

    ext = ".exe" if sys.platform == "win32" else ""
    gui = BUNDLE / f"{NAME}{ext}"
    cli = BUNDLE / f"xeno-cli{ext}"
    ok = gui.exists() and cli.exists()
    print("\n" + "-" * 60)
    print(f"bundle : {BUNDLE}")
    print(f"  GUI  : {gui.name}  {'OK' if gui.exists() else 'MISSING'}")
    print(f"  CLI  : {cli.name}  {'OK' if cli.exists() else 'MISSING'}")
    if sys.platform == "darwin":
        app_ok = (APP / "Contents" / "MacOS" / NAME).exists()
        print(f"  app  : {APP.name}  {'OK' if app_ok else 'MISSING'}")
        ok = ok and app_ok
    if ok:
        print("self-tests from the bundle:")
        ok = _run_selftest([str(cli), "self-test"]) and ok
        ok = _run_selftest([str(gui), "--self-test"]) and ok
    if ok and sys.platform == "win32":
        (BUNDLE / "START HERE.txt").write_text(START_HERE, encoding="utf-8")
    if ok:
        what = APP.name if sys.platform == "darwin" else f"{BUNDLE.name}/ (zip it)"
        print(f"\nDone. Ship {what}.")
    else:
        print("\nBuild incomplete or a bundled self-test failed; see the output above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
