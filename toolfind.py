"""toolfind.py — locate the helper programs the kit shells out to.

ffmpeg (movies) is optional. A release bundle
carries a portable ffmpeg under ``tools/`` next to the executables (see
``packaging/``); a source checkout may have one dropped into
``packaging/tools/``. Both are tried before PATH, so the bundled copy wins.

    python toolfind.py            # print what was found
    python toolfind.py --self-test
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional

HERE = Path(__file__).resolve().parent


def _search_dirs() -> List[Path]:
    dirs: List[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / "tools")
        dirs.append(Path(sys.executable).resolve().parent / "tools")
    dirs += [HERE / "tools", HERE / "packaging" / "tools"]
    return dirs


def find_tool(name: str, extra_dirs: Iterable[Path] = ()) -> Optional[str]:
    """Full path of ``name`` (``ffmpeg``), or None."""
    exe = name + (".exe" if os.name == "nt" else "")
    for d in list(extra_dirs) + _search_dirs():
        p = Path(d) / exe
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def self_test() -> int:
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    with tempfile.TemporaryDirectory() as td:
        fake = Path(td) / ("fakeprog" + (".exe" if os.name == "nt" else ""))
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        check(find_tool("fakeprog", [Path(td)]) == str(fake), "a bundled tools/ copy is found first")
        check(find_tool("no-such-program-xs2") is None, "a missing program is None, not an error")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    for n in ("ffmpeg",):
        print(f"{n:11s} {find_tool(n) or 'not found'}")
