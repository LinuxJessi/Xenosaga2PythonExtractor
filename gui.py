"""gui.py — Local web GUI for the Xenosaga Episode II Python Extractor.

Run me directly:
    python gui.py                 # opens your browser on a free localhost port
    python gui.py --no-browser    # just print the URL
    python gui.py --self-test

Stdlib only. The same design as the Episode I kit's GUI: a small HTTP server
on 127.0.0.1, a page with one card per command, and every command run as a
``cli.py`` subprocess whose output streams back live (Server-Sent Events).

Unlike the Episode I GUI, the cards are not written by hand. They are built
from ``cli.py``'s own argparse parser when the GUI starts: every subcommand
becomes a card, every option a field (a checkbox for on/off flags, a list box
for options that repeat, a picker for disc images and folders). A command
added to ``cli.py`` later shows up here with no change to this file; the
few hand-written touches (friendly help text, which field gets which picker)
are hints keyed by option name and fall back to plain text fields.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import re
import shlex
import shutil
import socket
import socketserver
import string
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "cli.py"
PY = sys.executable or "python3"
sys.path.insert(0, str(ROOT))      # the kit's modules, also under `python -I`

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    _cli_name = "xeno-cli.exe" if os.name == "nt" else "xeno-cli"
    _cli_exe = Path(sys.executable).resolve().parent / _cli_name
    CLI_ARGV: List[str] = [str(_cli_exe)]
    CLI_TARGET = _cli_exe
    RUNTIME_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    CLI_ARGV = [PY, str(CLI)]
    CLI_TARGET = CLI
    RUNTIME_DIR = ROOT

TITLE = "Xenosaga II Extractor"


def _fatal(title: str, body: str) -> None:
    msg = f"{title}\n\n{body}"
    print(f"[gui] FATAL: {msg}", file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, body, f"{TITLE} — {title}", 0x10)
        except Exception:
            pass
    elif sys.platform == "darwin" and FROZEN:
        # A double-clicked .app has no console; show the error in a dialog.
        try:
            esc = body.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog "{esc}" with title "{TITLE} — {title}" '
                 'buttons {"OK"} default button 1 with icon stop'],
                timeout=120, check=False,
            )
        except Exception:
            pass
    sys.exit(2)


# ---------------------------------------------------------------------------
# The command list, read from cli.py's own parser
# ---------------------------------------------------------------------------

class _Captured(Exception):
    def __init__(self, parser):
        super().__init__("parser captured")
        self.parser = parser


def cli_parser(module=None) -> argparse.ArgumentParser:
    """cli.py's top-level parser, without running a command.

    ``cli.main`` builds its parser inline and calls ``parse_args`` once; for
    the length of one call, ``parse_args`` is replaced by a hook that raises
    with the parser in hand. If cli.py ever grows a ``build_parser()``, that
    is used instead.
    """
    if module is None:
        import cli as module  # noqa: F401  (also tells PyInstaller to bundle it)
    if hasattr(module, "build_parser"):
        return module.build_parser()
    original = argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        raise _Captured(self)

    argparse.ArgumentParser.parse_args = grab
    saved_argv = sys.argv
    try:
        try:
            module.main([])
        except TypeError:
            sys.argv = [str(CLI)]
            module.main()
    except _Captured as c:
        return c.parser
    finally:
        argparse.ArgumentParser.parse_args = original
        sys.argv = saved_argv
    raise RuntimeError("cli.main() returned without building a parser")


# Friendlier text for the commands the kit has today. A command without an
# entry here gets its argparse help line, so new commands still read fine.
COMMAND_HELP = {
    "list": "Print every entry of the disc's table of contents: chain, sector, size and "
            "in-game path. Chain 0 is the shared data (both discs), chain 1 is Disc 1's "
            "movies and voices, chain 2 is Disc 2's.",
    "dump": "Unpack the disc images into <code>OUT/dump/chain0</code>, <code>chain1</code> "
            "and <code>chain2</code>, with <code>OUT/manifest.csv</code> listing every object "
            "(sector, size, sha1, kind). Give both discs to get all three chains. "
            "ARX-compressed objects are written decompressed.",
    "verify": "Re-hash a dump against its manifest. Compressed objects are recompressed "
              "with the kit's copy of Monolith's packer, so a clean run proves the codec "
              "both ways.",
    "browse": "Convert a dump into files your computer opens, under <code>OUT/browse/</code>: "
              "textures and UI sheets as PNG, every model as a <code>.glb</code> Blender opens "
              "(textured, with its skeleton), samples as WAV, songs as MIDI + SoundFont "
              "and a WAV rendered the way the PS2 plays it, dialogue as UTF-8, scripts as readable listings, "
              "movies as MP4 (ffmpeg).",
    "model": "One model straight from the disc to a <code>.glb</code> for Blender: the mesh with the "
             "textures and skeleton of the same name. <code>browse</code> does every model at once.",
    "sb": "Disassemble one scene or map script, with every native call named and every "
          "spoken line shown beside the instruction that uses it. "
          "Example path: <code>cf\\sc1010.sb</code>.",
    "text-export": "Write the game's text to an editable UTF-8 tree, one file per text object: dialogue banks, "
                   "menus (item, skill and location names, help, the database, the Skill Tutorial), the battle names "
                   "and messages, the battle banners, the quest, citizen and treasure text, movie subtitles and the "
                   "save's title in the memory-card browser. <code>#</code> lines are notes (a banner, a tutorial "
                   "command, the memory-card slot number, a reading of Japanese leftovers). Strings in the "
                   "executables are <em>exe-export</em>.",
    "text-import": "Write an edited text tree back into a <em>copy</em> of the disc: dialogue banks, the inline "
                   "script text (Japan and the demo), menus, battle names and messages, quest, citizen and treasure "
                   "text, movie subtitles and the save's title in the memory-card browser. Each kind keeps to the "
                   "limit its loader allows (banks and menus within their room, a longer battle string moves the "
                   "rest of its section down within the file's sectors, quest texts within their fields); anything past "
                   "a limit is refused and nothing is written. The memory-card slot number and the Skill Tutorial's "
                   "commands are kept; a battle banner edited in one bank but not its copies gets a note. An unedited "
                   "tree imports as no change; every write is read back.",
    "dialogue": "Every spoken and displayed line in the order the game plays it: one transcript per scene, field "
                "and battle script with speakers, choices, battle banners and branches, the movies' and real-time "
                "events' subtitles "
                "as SRT, and <code>dialogue.csv</code>. Each line keeps its bank number, so it maps back to the "
                "<em>text-export</em> block you edit. <em>Voice</em> also cuts one WAV per timed line (ffmpeg). "
                "Reading only: it writes nothing to the disc.",
    "font-extract": "The two fonts (<code>font0</code> dialogue, <code>font1</code> menus) as PNG sheets of 20x24 "
                    "cells, a metrics CSV of each cell's left and right spacing, and the table of which byte draws "
                    "which cell. Edit the sheets to draw new letters for a translation, then use "
                    "<em>font-rebuild</em>.",
    "font-rebuild": "Build a font file from edited sheets and metrics, starting from the retail font as "
                    "<em>Ref</em>. A folder without any sheet or metrics is refused unless <em>Allow unchanged</em> "
                    "is ticked. Put the new font on a copy of the disc with <em>patch</em> "
                    "(<code>chain0:font0.us=...</code>).",
    "natives": "List every script native (the calls a script makes into the game, such as "
               "<code>bat.msgPut</code>) with how often it is called and the argument types seen. "
               "<em>Output</em> also writes a listing of every script.",
    "recolor": "Hue-shift named textures in every copy on a <em>copy</em> of the disc, found by their txy entry "
               "name (standalone and inside effect files); the default recolours KOS-MOS's hair. "
               "<em>Xtx</em> colourises one true-colour UI sheet instead, such as the title menu words. "
               "Colours only: it cannot redraw words in a texture.",
    "exe-export": "Write the strings the game's code points at (the boot executable, OV01.OVL and "
                  "OV02.OVL: <em>Saving...</em>, the memory-card prompts, <em>Total Exp</em>) to an editable "
                  "tree, one <code>### @ADDRESS max N bytes</code> block per string.",
    "exe-import": "Write the edited strings back <em>in place</em> into a copy of the disc: each at most its "
                  "<code>max N</code> bytes, the rest of its slot filled with zeros. Run it <em>last</em>: "
                  "afterwards the executable is not the retail one and text-import, font-extract and lang-scan "
                  "refuse the image. For another round, give the retail disc as <em>Retail disc</em>.",
    "text-coverage": "List every place the game shows text and what the kit does with it, counted from the disc: "
                     "the text it edits, the strings in the executables, and the words drawn into pictures and "
                     "movies (those need picture editing). <em>Verify</em> also checks that unedited exports import "
                     "as no change.",
    "patch": "Replace whole objects in a copy of the disc. One replacement per line: "
             "<code>chain0:cf\\en\\sc1010.sbl=/path/to/new.sbl</code>.",
    "lang-scan": "Translating into an alphabet the game never had (Arabic, Hindi, Georgian, ...): shape the "
                 "translated text tree with a TrueType font, give every glyph a code the USA build can draw, and "
                 "write <code>font0.us</code>, <code>font1.us</code>, <code>glyphset.json</code> and a preview "
                 "sheet. Run it once per script; later scripts take <code>--extend</code> and the earlier fonts. "
                 "See <code>docs/ALPHABETS.md</code>.",
    "lang-encode": "Turn the translated tree into the same tree with the new glyphs written as codes, in the "
                   "order the game draws them (right-to-left lines reordered), wrapped and checked against every "
                   "byte limit. Nothing is written if a glyph or a limit is missing. Then patch the two fonts into "
                   "a copy and run <code>text-import</code> on it.",
    "lang-preview": "Draw one line with the rebuilt font to a PNG, the way the game's print function places "
                    "the cells, to check a translation without the game.",
    "self-test": "Run every module's built-in checks (no disc needed).",
}

# Field hints keyed by option name: label, picker, placeholder.
FIELD_HINTS = {
    "iso": {"label": "Disc image", "pick": "file", "filter": "iso"},
    "out": {"label": "Output", "pick": "dir"},
    "text": {"label": "Text folder", "pick": "dir"},
    "retail": {"label": "Retail disc", "pick": "file", "filter": "iso"},
    "dump": {"label": "Dump folder", "pick": "dir"},
    "path": {"label": "In-game path", "placeholder": "e.g. cf\\sc1010.sb"},
    "set": {"label": "Replacements", "placeholder": "chain0:cf\\en\\sc1010.sbl=/path/to/new.sbl"},
    "glob": {"label": "Only paths", "placeholder": "e.g. model\\char\\pc\\* (optional)"},
    "limit": {"label": "At most", "placeholder": "N objects per kind (optional, for a quick sample)"},
    "font": {"label": "TrueType font", "pick": "file"},
    "glyphset": {"label": "glyphset.json", "pick": "file"},
    "cells": {"label": "Cell sheet (PNG)", "pick": "file"},
    "keys": {"label": "Cell keys (text)", "pick": "file"},
    "font0": {"label": "Earlier font0.us", "pick": "file"},
    "font1": {"label": "Earlier font1.us", "pick": "file"},
    "extend": {"label": "Earlier glyphset.json", "pick": "file"},
    "line": {"label": "Line of text", "placeholder": "the line as you would write it in the tree"},
    "chain": {"label": "Chain", "choices": [["", "All chains"], ["0", "Chain 0 (shared data)"],
                                            ["1", "Chain 1 (Disc 1 movies and voices)"],
                                            ["2", "Chain 2 (Disc 2 movies and voices)"]]},
}

# Commands whose --out is a disc image they write, not a folder.
_WRITES_ISO = re.compile(r"\b(copy of the disc|disc copy|modded|\.iso)\b", re.I)


def _help_of(sub_action, name) -> str:
    for ca in getattr(sub_action, "_choices_actions", []):
        if ca.dest == name or getattr(ca, "metavar", None) == name:
            return ca.help or ""
    return ""


def _kinds_choices() -> Optional[List[str]]:
    try:
        from browse import ALL_KINDS
        return list(ALL_KINDS)
    except Exception:
        return None


def command_schema(parser: Optional[argparse.ArgumentParser] = None) -> List[dict]:
    """One dict per subcommand: name, help, aliases and its fields."""
    parser = parser or cli_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    commands: List[dict] = []
    by_signature: Dict[tuple, dict] = {}
    for name, p in sub.choices.items():
        short = _help_of(sub, name)
        writes_iso = bool(_WRITES_ISO.search(short)) or name in ("patch", "text-import", "exe-import")
        fields = []
        for a in p._actions:
            if isinstance(a, argparse._HelpAction):
                continue
            flag = next((s for s in a.option_strings if s.startswith("--")),
                        a.option_strings[0] if a.option_strings else None)
            if isinstance(a, argparse._StoreTrueAction):
                kind = "flag"
            elif isinstance(a, argparse._StoreFalseAction):
                kind = "flag-off"
            elif isinstance(a, argparse._AppendAction) or a.nargs in ("+", "*"):
                kind = "multi"
            else:
                kind = "value"
            hint = dict(FIELD_HINTS.get(a.dest, {}))
            f = {
                "dest": a.dest,
                "flag": flag,
                "positional": not a.option_strings,
                "kind": kind,
                "required": bool(a.required) if a.option_strings else (a.nargs not in ("?", "*")),
                "help": a.help or "",
                "default": None if a.default in (None, False, argparse.SUPPRESS) else a.default,
                "type": getattr(a.type, "__name__", None) if a.type else None,
                "choices": [str(c) for c in a.choices] if a.choices else None,
                "label": hint.get("label") or a.dest.replace("_", " ").capitalize(),
                "placeholder": hint.get("placeholder", a.help or ""),
            }
            if a.dest == "kinds" and name == "browse":
                f["kind"] = "kinds"
                f["choices"] = _kinds_choices()
                if not f["choices"]:
                    f["kind"] = "value"
            elif hint.get("choices") and not f["choices"]:
                f["select"] = hint["choices"]
            pick = hint.get("pick")
            if a.dest == "out" and writes_iso:
                pick = "save"
                f["label"] = "Output disc"
                f["suggest"] = "modded.iso"
            elif a.dest == "out" and re.search(r"\bPNG to write\b", a.help or ""):
                pick = "save"
                f["label"] = "Output PNG"
                f["suggest"] = "line.png"
            if pick:
                f["pick"] = pick
                if hint.get("filter"):
                    f["filter"] = hint["filter"]
            elif kind in ("value", "multi") and not f.get("select") and not f["choices"] and \
                    re.search(r"\b(file|png|wav|sf2|json|csv|folder|dir)\b", f["help"], re.I):
                f["pick"] = "any"
            fields.append(f)
        signature = (short, tuple((f["dest"], f["kind"]) for f in fields))
        if short and signature in by_signature:        # dump / extract: one card, two names
            by_signature[signature]["aliases"].append(name)
            continue
        cmd = {"name": name, "short": short, "help": COMMAND_HELP.get(name, ""),
               "aliases": [], "fields": fields}
        by_signature[signature] = cmd
        commands.append(cmd)
    return commands


def build_argv(cmd: dict, form: dict) -> List[str]:
    """Turn a submitted card into the argv the CLI gets. Raises ValueError
    with a readable message when something required is missing."""
    def val(key):
        v = form.get(key)
        if isinstance(v, list):
            v = v[0] if v else ""
        return (v or "").strip()

    argv = [*CLI_ARGV, cmd["name"]]
    positional: List[str] = []
    for f in cmd["fields"]:
        key = f["dest"]
        if f["kind"] == "flag":
            if val(key).lower() in ("1", "on", "true", "yes"):
                argv.append(f["flag"])
            continue
        if f["kind"] == "flag-off":
            if val(key).lower() not in ("1", "on", "true", "yes"):
                argv.append(f["flag"])
            continue
        if f["kind"] == "kinds":
            picked = [k for k in f["choices"] if val(f"{key}.{k}").lower() in ("1", "on", "true", "yes")]
            if not picked:
                raise ValueError("Pick at least one kind to convert")
            if len(picked) < len(f["choices"]):
                argv += [f["flag"], ",".join(picked)]
            continue
        if f["kind"] == "multi":
            items = [s.strip() for s in re.split(r"[\n;]", val(key)) if s.strip()]
            if f["required"] and not items:
                raise ValueError(f"{f['label']} is required")
            if f["positional"]:
                positional += items
            else:
                for it in items:
                    argv += [f["flag"], it]
            continue
        v = val(key)
        if not v:
            if f["required"]:
                raise ValueError(f"{f['label']} is required")
            continue
        if f["type"] == "int":
            try:
                int(v, 0)
            except ValueError:
                raise ValueError(f"{f['label']} must be a whole number") from None
        if f["type"] == "float":
            try:
                float(v)
            except ValueError:
                raise ValueError(f"{f['label']} must be a number") from None
        if f["choices"] and v not in f["choices"]:
            raise ValueError(f"{f['label']} must be one of {', '.join(f['choices'])}")
        if f["positional"]:
            positional.append(v)
        else:
            argv += [f["flag"], v]
    return argv + positional


_SCHEMA_CACHE: Optional[List[dict]] = None
_SCHEMA_LOCK = threading.Lock()


def schema() -> List[dict]:
    global _SCHEMA_CACHE
    with _SCHEMA_LOCK:
        if _SCHEMA_CACHE is None:
            _SCHEMA_CACHE = command_schema()
        return _SCHEMA_CACHE


def find_command(name: str) -> Optional[dict]:
    for c in schema():
        if c["name"] == name or name in c["aliases"]:
            return c
    return None


# ---------------------------------------------------------------------------
# Job runner: one subprocess per launched command, lines streamed to clients
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, cmd: List[str], cwd: Optional[Path]):
        self.id = uuid.uuid4().hex[:12]
        self.cmd = cmd
        self.cwd = str(cwd or ROOT)
        self.started_at = time.time()
        self.ended_at: Optional[float] = None
        self.exit_code: Optional[int] = None
        self.lines: List[str] = []
        self.subscribers: List["queue.Queue[Optional[str]]"] = []
        self._lock = threading.Lock()
        self._proc = subprocess.Popen(
            cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self.lines.append(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
        threading.Thread(target=self._pump, daemon=True).start()

    def _broadcast(self, line: Optional[str]) -> None:
        with self._lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip("\r\n")
            self.lines.append(line)
            self._broadcast(line)
        rc = self._proc.wait()
        self.exit_code = rc
        self.ended_at = time.time()
        summary = f"[exit {rc}]"
        self.lines.append(summary)
        self._broadcast(summary)
        self._broadcast(None)

    def subscribe(self) -> "queue.Queue[Optional[str]]":
        q: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=50000)
        with self._lock:
            self.subscribers.append(q)
            for line in self.lines:
                try:
                    q.put_nowait(line)
                except queue.Full:
                    break
            if self.ended_at is not None:
                q.put_nowait(None)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def status(self) -> str:
        if self.exit_code is None:
            return "running"
        return "ok" if self.exit_code == 0 else "err"

    def to_summary(self) -> dict:
        return {"id": self.id, "cmd": self.cmd, "status": self.status(),
                "exit_code": self.exit_code, "started_at": self.started_at,
                "ended_at": self.ended_at}


JOBS: Dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Auto-detect helpers
# ---------------------------------------------------------------------------

def _walk_for_isos(root: Path, max_depth: int, want: int, out: List[Path]) -> None:
    if len(out) >= want or max_depth < 0:
        return
    try:
        entries = list(root.iterdir())
    except (OSError, PermissionError):
        return
    entries.sort(key=lambda p: (not p.is_file(), p.name.lower()))
    for child in entries:
        if len(out) >= want:
            return
        try:
            if child.is_file():
                if child.suffix.lower() == ".iso" and child.stat().st_size > 100 * 1024 * 1024:
                    out.append(child)
            elif child.is_dir() and not child.name.startswith("."):
                _walk_for_isos(child, max_depth - 1, want, out)
        except (OSError, PermissionError):
            continue


def detect_isos() -> List[str]:
    candidates: List[Path] = []
    for root, depth in ((ROOT.parent, 2), (Path.home() / "Downloads", 2),
                        (Path.home() / "Desktop", 3), (Path.home() / "Documents", 3)):
        try:
            if root.is_dir():
                _walk_for_isos(root, depth, want=64, out=candidates)
        except OSError:
            continue
    return list(dict.fromkeys(str(p) for p in candidates))


_XS2 = re.compile(r"episode ii\b|jenseits|xenosaga ?2|slus.?208\.?92|slus.?211\.?33", re.I)


def xs2_discs(isos: List[str]) -> List[str]:
    """The Episode II images among ``isos``, Disc 1 before Disc 2, one of each."""
    picked: Dict[int, str] = {}
    for p in isos:
        name = Path(p).name
        if not _XS2.search(name) or "copy" in name.lower():
            continue
        m = re.search(r"disc\s*([12])", name, re.I)
        disc = int(m.group(1)) if m else 1
        picked.setdefault(disc, p)
    return _one_region([picked[d] for d in sorted(picked)], isos)


def _one_region(pair: List[str], isos: List[str]) -> List[str]:
    """Keep Disc 1 and Disc 2 of one build: when the pair found by file name mixes
    regions (Japan Disc 1 beside USA Disc 2 in one folder), swap Disc 2 for one of
    Disc 1's region if the folder has it, else keep Disc 1 alone. Images that
    cannot be opened are left as they are."""
    if len(pair) < 2:
        return pair
    try:
        import region
        from iso9660 import IsoImage

        def reg(p):
            iso = IsoImage(p)
            try:
                return region.region_of(iso).name
            finally:
                iso.close()

        want = reg(pair[0])
        if reg(pair[1]) == want:
            return pair
        for p in isos:
            name = Path(p).name
            if (p not in pair and _XS2.search(name) and "copy" not in name.lower()
                    and re.search(r"disc\s*2", name, re.I) and reg(p) == want):
                return [pair[0], p]
        print(f"warning: {Path(pair[1]).name} is a different region from {Path(pair[0]).name}; using Disc 1 only",
              file=sys.stderr)
        return pair[:1]
    except Exception:                    # not an image the kit can open: leave the choice alone
        return pair


def _tool(name: str) -> Optional[str]:
    """A helper program: the copy bundled under tools/ first, then PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (RUNTIME_DIR / "tools", ROOT / "packaging" / "tools"):
        p = d / exe
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def _numpy_version() -> str:
    """numpy's version, or "" (the music renderer needs it). The app bundles
    carry it; build.py's bundle self-test fails without it."""
    if FROZEN:
        return "bundled"
    try:
        import numpy
    except ImportError:
        return ""
    return getattr(numpy, "__version__", "installed")


def _module_version(name: str) -> str:
    """A required module's version, or "" when it is missing (fontTools and
    uharfbuzz draw new alphabets; the app bundles carry them)."""
    if FROZEN:
        return "bundled"
    try:
        mod = __import__(name)
    except ImportError:
        return ""
    return str(getattr(mod, "__version__", None) or getattr(mod, "version", None) or "installed")


def detect_deps() -> dict:
    return {
        "python": sys.version.split()[0],
        "cli": str(CLI_TARGET) if CLI_TARGET.exists() else "",
        "ffmpeg": _tool("ffmpeg") or "",
        "numpy": _numpy_version(),
        "fonttools": _module_version("fontTools"),
        "uharfbuzz": _module_version("uharfbuzz"),
    }


def default_out_dir(discs: List[str]) -> str:
    if discs:
        return str(Path(discs[0]).parent / "xs2-out")
    return str(Path.home() / "xs2" / "out")


# ---------------------------------------------------------------------------
# Filesystem browser for the picker
# ---------------------------------------------------------------------------

def list_roots() -> List[str]:
    roots: List[str] = []
    if os.name == "nt":
        for d in string.ascii_uppercase:
            p = Path(f"{d}:\\")
            if p.exists():
                roots.append(str(p))
    else:
        roots += ["/", str(Path.home())]
        for parent in ("/mnt", "/media", "/Volumes"):
            try:
                pp = Path(parent)
                if pp.is_dir():
                    roots += [str(c) for c in sorted(pp.iterdir()) if c.is_dir()]
            except (OSError, PermissionError):
                continue
    return list(dict.fromkeys(roots))


def list_dir(path_str: str, ext_filter: Optional[str] = None) -> dict:
    p = Path(path_str).expanduser() if path_str else Path.home()
    if not p.exists():
        p = Path.home()
    if not p.is_dir():
        p = p.parent
    p = p.resolve()
    entries: List[dict] = []
    err: Optional[str] = None
    try:
        children = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except (OSError, PermissionError) as exc:
        children, err = [], str(exc)
    for child in children:
        try:
            if child.is_symlink() and not child.exists():
                continue
            if child.is_dir():
                if child.name.startswith("."):
                    continue
                entries.append({"name": child.name, "type": "dir", "path": str(child)})
            else:
                if ext_filter and child.suffix.lower().lstrip(".") != ext_filter.lower().lstrip("."):
                    continue
                entries.append({"name": child.name, "type": "file", "path": str(child),
                                "size": child.stat().st_size})
        except (OSError, PermissionError):
            continue
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "entries": entries, "roots": list_roots(), "error": err}


# ---------------------------------------------------------------------------
# Embedded UI. The stylesheet is the Episode I GUI's, unchanged; the cards
# are generated from /schema.
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>XENOSAGA II · Extractor</title>
<style>
/*__XS1_CSS__*/
.card .row .kinds-wrap { display: flex; flex-wrap: wrap; gap: 6px 16px; }
.card .row .kinds-wrap label {
  text-transform: none; letter-spacing: 0; font-size: 13px; color: var(--fg);
  display: flex; align-items: center; gap: 6px;
}
.card .row small.alias { color: var(--muted); }
.card .row .req { color: var(--accent-warm); margin-left: 3px; }
</style>
</head>
<body>
<header>
  <div class="title">
    <h1>
      <span>Xenosaga</span><span class="i"> II</span>
      <span class="sep">·</span><span class="extractor">EXTRACTOR</span>
    </h1>
    <div class="tagline">Jenseits von Gut und Böse · disc unpacker</div>
  </div>
  <div class="actions">
    <button class="ghost" id="btn-refresh">Refresh status</button>
  </div>
</header>
<main>
  <section id="cards"></section>
  <aside id="right">
    <h2>Environment</h2>
    <div id="status-box"></div>
    <h2>Output</h2>
    <pre id="log"></pre>
  </aside>
</main>

<div class="modal" id="fs-modal" hidden>
  <div class="modal-backdrop" data-close></div>
  <div class="modal-window">
    <div class="modal-header">
      <h2 id="fs-title">Select…</h2>
      <button type="button" class="x" data-close>×</button>
    </div>
    <div class="fs-toolbar">
      <button type="button" class="ghost" id="fs-up">↑ Up</button>
      <div class="fs-crumb" id="fs-crumb"></div>
    </div>
    <div class="modal-error" id="fs-error" hidden></div>
    <div class="fs-quick" id="fs-quick" hidden></div>
    <div class="fs-list" id="fs-list"></div>
    <div class="fs-roots" id="fs-roots"></div>
    <div class="modal-footer">
      <div class="sel-path" id="fs-sel">No selection</div>
      <button type="button" class="ghost" data-close>Cancel</button>
      <button type="button" id="fs-select" disabled>Select</button>
    </div>
  </div>
</div>

<script>
const log = document.getElementById('log');
let currentEventSource = null;
const cardEls = {};
let _detectedIsos = [];

function setStatus(name, status) {
  const el = cardEls[name];
  if (!el) return;
  el.classList.remove('run','ok','err');
  if (status) el.classList.add(status);
  const badge = el.querySelector('.badge');
  badge.classList.remove('run','ok','err');
  badge.textContent = (status || 'idle');
  if (status) badge.classList.add(status);
}
function appendLog(line) { log.textContent += line + '\n'; log.scrollTop = log.scrollHeight; }
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB','MB','GB','TB']; let v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 10 ? 0 : 1) + ' ' + units[i];
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}

async function runCommand(name, body) {
  if (currentEventSource) currentEventSource.close();
  log.textContent = '';
  setStatus(name, 'run');
  const res = await fetch('/run/' + encodeURIComponent(name), {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body});
  if (!res.ok) {
    const t = await res.text(); let msg = t;
    try { const j = JSON.parse(t); if (j && j.error) msg = j.error; } catch (_) {}
    setStatus(name, 'err'); appendLog('[error] ' + msg); return;
  }
  const {job_id} = await res.json();
  const es = new EventSource('/stream/' + job_id);
  currentEventSource = es;
  es.onmessage = (e) => {
    if (e.data === '__eof__') {
      es.close();
      fetch('/job/' + job_id).then(r => r.json()).then(j => setStatus(name, j.status === 'ok' ? 'ok' : 'err'));
    } else { appendLog(e.data); }
  };
  es.onerror = () => { es.close(); };
}

// ------------------------------------------------------------------ picker
const fsModal = document.getElementById('fs-modal'), fsTitle = document.getElementById('fs-title');
const fsCrumb = document.getElementById('fs-crumb'), fsList = document.getElementById('fs-list');
const fsRoots = document.getElementById('fs-roots'), fsError = document.getElementById('fs-error');
const fsQuick = document.getElementById('fs-quick'), fsSel = document.getElementById('fs-sel');
const fsSelectBtn = document.getElementById('fs-select'), fsUp = document.getElementById('fs-up');
const fsState = { mode: 'file', filter: null, current: null, parent: null, selected: null, onChoose: null };

function _renderQuickPick() {
  const isos = fsState.filter === 'iso' ? _detectedIsos : [];
  if (!isos.length) { fsQuick.hidden = true; return; }
  fsQuick.hidden = false;
  fsQuick.innerHTML = '<span>Detected:</span>' + isos.map(p =>
    `<a class="qp" data-path="${escapeHtml(p)}" title="${escapeHtml(p)}">${escapeHtml(p.split('/').pop().split('\\').pop())}</a>`).join('');
  fsQuick.querySelectorAll('.qp').forEach(a => {
    a.addEventListener('click', () => {
      fsQuick.querySelectorAll('.qp').forEach(x => x.classList.remove('selected'));
      a.classList.add('selected'); fsState.selected = a.dataset.path;
      fsSel.classList.add('has'); fsSel.textContent = a.dataset.path; fsSelectBtn.disabled = false;
    });
    a.addEventListener('dblclick', () => { fsState.selected = a.dataset.path; chooseAndClose(); });
  });
}
function openPicker({mode, filter, startPath, title, onChoose}) {
  Object.assign(fsState, {mode, filter: filter || null, current: null, parent: null, selected: null, onChoose});
  fsTitle.textContent = title || (mode === 'file' ? 'Select a file' : 'Select a folder');
  fsSel.classList.remove('has'); fsSel.textContent = 'No selection';
  fsSelectBtn.disabled = mode === 'file'; fsError.hidden = true;
  _renderQuickPick(); fsModal.hidden = false; loadDir(startPath || '');
}
function closePicker() { fsModal.hidden = true; fsState.onChoose = null; }
async function loadDir(path) {
  fsError.hidden = true;
  try {
    const qs = new URLSearchParams();
    if (path) qs.set('path', path);
    if (fsState.filter) qs.set('filter', fsState.filter);
    const res = await fetch('/browse-fs?' + qs.toString());
    if (!res.ok) { fsError.hidden = false; fsError.textContent = 'Cannot read directory.'; return; }
    const data = await res.json();
    if (data.error) { fsError.hidden = false; fsError.textContent = data.error; }
    fsState.current = data.path; fsState.parent = data.parent; fsState.selected = null;
    if (fsState.mode !== 'file') {
      fsState.selected = data.path; fsSel.classList.add('has'); fsSel.textContent = data.path; fsSelectBtn.disabled = false;
    } else { fsSel.classList.remove('has'); fsSel.textContent = 'No selection'; fsSelectBtn.disabled = true; }
    fsUp.disabled = !data.parent;
    renderCrumb(data.path); renderList(data.entries); renderRoots(data.roots);
  } catch (err) { fsError.hidden = false; fsError.textContent = 'Error loading directory: ' + err.message; }
}
function renderCrumb(fullPath) {
  const sep = fullPath.indexOf('\\') >= 0 && fullPath.indexOf('/') < 0 ? '\\' : '/';
  let parts;
  if (sep === '\\') { parts = fullPath.split('\\').filter(Boolean); if (parts.length && /^[A-Z]:$/i.test(parts[0])) parts[0] += '\\'; }
  else { parts = ['/'].concat(fullPath.split('/').filter(Boolean)); }
  let acc = '';
  fsCrumb.innerHTML = parts.map((p, i) => {
    if (sep === '/') acc = (i === 0) ? '/' : (acc.endsWith('/') ? acc : acc + '/') + p;
    else acc = (i === 0) ? p : (acc + (acc.endsWith('\\') ? '' : '\\') + p);
    const a = `<a data-path="${escapeHtml(acc)}">${escapeHtml(p)}</a>`;
    return i === parts.length - 1 ? a : a + '<span class="sep">/</span>';
  }).join('');
  fsCrumb.querySelectorAll('a').forEach(a => a.addEventListener('click', () => loadDir(a.dataset.path)));
}
function renderList(entries) {
  if (!entries.length) {
    fsList.innerHTML = `<div class="fs-empty">(empty${fsState.filter ? ' — no .' + fsState.filter + ' files here' : ''})</div>`;
    return;
  }
  fsList.innerHTML = entries.map(e => {
    const icon = e.type === 'dir' ? '📁' : '📄', iconCls = e.type === 'dir' ? 'icon dir' : 'icon';
    const size = e.type === 'file' ? `<span class="size">${fmtSize(e.size)}</span>` : '';
    return `<div class="fs-entry" data-type="${e.type}" data-path="${escapeHtml(e.path)}">
      <span class="${iconCls}">${icon}</span><span class="name">${escapeHtml(e.name)}</span>${size}</div>`;
  }).join('');
  fsList.querySelectorAll('.fs-entry').forEach(el => {
    el.addEventListener('click', () => {
      if (el.dataset.type === 'dir') { loadDir(el.dataset.path); }
      else if (fsState.mode === 'file' || fsState.mode === 'any') {
        fsList.querySelectorAll('.fs-entry.selected').forEach(x => x.classList.remove('selected'));
        el.classList.add('selected'); fsState.selected = el.dataset.path;
        fsSel.classList.add('has'); fsSel.textContent = el.dataset.path; fsSelectBtn.disabled = false;
      }
    });
    el.addEventListener('dblclick', () => {
      if (el.dataset.type === 'dir') loadDir(el.dataset.path);
      else if (fsState.mode === 'file' || fsState.mode === 'any') { fsState.selected = el.dataset.path; chooseAndClose(); }
    });
  });
}
function renderRoots(roots) {
  fsRoots.innerHTML = '<span>Roots:</span>' + roots.map(r => `<a data-path="${escapeHtml(r)}">${escapeHtml(r)}</a>`).join('');
  fsRoots.querySelectorAll('a').forEach(a => a.addEventListener('click', () => loadDir(a.dataset.path)));
}
function chooseAndClose() { if (!fsState.selected) return; const cb = fsState.onChoose; closePicker(); if (cb) cb(fsState.selected); }
fsUp.addEventListener('click', () => { if (fsState.parent) loadDir(fsState.parent); });
fsSelectBtn.addEventListener('click', chooseAndClose);
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closePicker));
document.addEventListener('keydown', (e) => { if (!fsModal.hidden && e.key === 'Escape') closePicker(); });

// ------------------------------------------------------------------ cards from the CLI's parser
function defaultFor(f, d) {
  if (f.default !== null && f.default !== undefined) return String(f.default);
  if (f.dest === 'iso') return f.kind === 'multi' ? (d.discs || []).join('\n') : ((d.discs || [])[0] || '');
  if (f.pick === 'save') return d.default_out ? d.default_out + '/' + (f.suggest || 'modded.iso') : '';
  if (f.dest === 'out') return d.default_out || '';
  if (f.dest === 'text') return d.default_out ? d.default_out + '/text' : '';
  return '';
}
function fieldHtml(f, d) {
  const name = escapeHtml(f.dest), req = f.required ? '<span class="req">*</span>' : '';
  const label = `<label title="${escapeHtml(f.help)}">${escapeHtml(f.label)}${req}</label>`;
  if (f.kind === 'flag' || f.kind === 'flag-off') {
    return `<div class="row">${label}<div class="check-wrap"><input type="checkbox" name="${name}">
      <span>${escapeHtml(f.help)}</span></div></div>`;
  }
  if (f.kind === 'kinds') {
    const boxes = f.choices.map(k => `<label><input type="checkbox" name="${name}.${escapeHtml(k)}" checked> ${escapeHtml(k)}</label>`).join('');
    return `<div class="row">${label}<div class="kinds-wrap">${boxes}</div></div>`;
  }
  const opts = f.select || (f.choices ? [['', '(default)']].concat(f.choices.map(c => [c, c])) : null);
  if (opts) {
    const o = opts.map(([v, l]) => `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`).join('');
    return `<div class="row">${label}<select name="${name}">${o}</select></div>`;
  }
  const value = escapeHtml(defaultFor(f, d)), ph = escapeHtml(f.placeholder || '');
  const input = f.kind === 'multi'
    ? `<textarea name="${name}" placeholder="${ph} (one per line)">${value}</textarea>`
    : `<input type="text" name="${name}" placeholder="${ph}" value="${value}">`;
  if (!f.pick) return `<div class="row">${label}${input}</div>`;
  const btn = {file: 'Browse file', dir: 'Browse folder', save: 'Choose folder', any: 'Browse'}[f.pick] || 'Browse';
  return `<div class="row">${label}<div class="with-pick">${input}
    <button type="button" class="pick" data-pick="${f.pick}" data-pick-filter="${escapeHtml(f.filter || '')}"
      data-pick-for="${name}" data-suggest="${escapeHtml(f.suggest || '')}" data-multi="${f.kind === 'multi' ? 1 : 0}">${btn}</button></div></div>`;
}
function formBody(form) {
  const fd = new FormData(form);
  form.querySelectorAll('input[type=checkbox]').forEach(cb => fd.set(cb.name, cb.checked ? 'on' : ''));
  return new URLSearchParams(fd).toString();
}
function makeCard(step, c, d) {
  const div = document.createElement('div');
  div.className = 'card'; div.dataset.name = c.name;
  const alias = c.aliases.length ? ` <small class="alias">(also: ${c.aliases.map(escapeHtml).join(', ')})</small>` : '';
  const help = c.help || escapeHtml(c.short || '');
  div.innerHTML = `
    <div class="card-head"><span class="step">${step}.</span>
      <h3>${escapeHtml(c.name)}${alias}</h3><span class="badge">idle</span><span class="chev">▼</span></div>
    <div class="card-body">${help ? `<p class="help">${help}</p>` : ''}
      <form data-cmd="${escapeHtml(c.name)}">${c.fields.map(f => fieldHtml(f, d)).join('')}
        <div class="actions"><button type="submit">Run ${escapeHtml(c.name)}</button>
          <button type="button" class="ghost dryrun">Show command</button></div>
      </form></div>`;
  div.querySelector('.card-head').addEventListener('click', () => div.classList.toggle('open'));
  div.querySelectorAll('button.pick').forEach(btn => btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const el = btn.closest('.row').querySelector(`[name="${btn.dataset.pickFor}"]`);
    const multi = btn.dataset.multi === '1', mode = btn.dataset.pick;
    const current = multi ? (el.value.split('\n').filter(Boolean).pop() || '') : el.value;
    openPicker({
      mode: mode === 'save' ? 'dir' : mode, filter: btn.dataset.pickFilter || null,
      startPath: current || undefined,
      title: mode === 'save' ? 'Choose the folder for the new disc image' : undefined,
      onChoose: (p) => {
        if (mode === 'save') p = p.replace(/[\/\\]$/, '') + '/' + (btn.dataset.suggest || 'modded.iso');
        if (multi) el.value = (el.value.trim() ? el.value.trim() + '\n' : '') + p; else el.value = p;
      },
    });
  }));
  const form = div.querySelector('form');
  form.addEventListener('submit', (e) => { e.preventDefault(); runCommand(c.name, formBody(form)); });
  div.querySelector('.dryrun').addEventListener('click', (e) => {
    e.stopPropagation();
    fetch('/preview/' + encodeURIComponent(c.name), {method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: formBody(form)})
      .then(r => r.json()).then(j => {
        log.textContent = '';
        if (j.error) { appendLog('[error] ' + j.error); return; }
        appendLog('# Would run:'); appendLog(j.cmd.map(x => /\s/.test(x) ? `"${x}"` : x).join(' '));
      });
  });
  cardEls[c.name] = div;
  return div;
}

async function loadStatus() {
  const d = await (await fetch('/detect')).json();
  _detectedIsos = d.isos || [];
  const box = document.getElementById('status-box');
  const rows = [
    ['Python', d.deps.python, true],
    ['CLI', d.deps.cli || 'NOT FOUND', !!d.deps.cli],
    ['Discs found', (d.discs || []).length ? d.discs.map(p => p.split('/').pop()).join(' + ') : 'none (use Browse)', (d.discs || []).length > 0],
    ['ffmpeg (movies)', d.deps.ffmpeg || 'not found: movies are skipped', !!d.deps.ffmpeg],
    ['numpy (music WAV)', d.deps.numpy || 'MISSING: python -m pip install -r requirements.txt', !!d.deps.numpy],
    ['fontTools (new alphabets)', d.deps.fonttools || 'MISSING: python -m pip install -r requirements.txt', !!d.deps.fonttools],
    ['uharfbuzz (new alphabets)', d.deps.uharfbuzz || 'MISSING: python -m pip install -r requirements.txt', !!d.deps.uharfbuzz],
  ];
  box.innerHTML = rows.map(([k, v, ok]) =>
    `<div class="row"><span>${k}</span><span class="v ${ok ? 'ok' : 'bad'}" title="${escapeHtml(String(v))}">${escapeHtml(String(v))}</span></div>`).join('');
  return d;
}

(async () => {
  const d = await loadStatus();
  const s = await (await fetch('/schema')).json();
  const cards = document.getElementById('cards');
  if (s.error) { appendLog('[error] could not read the CLI\'s commands: ' + s.error); return; }
  s.commands.forEach((c, i) => cards.appendChild(makeCard(i + 1, c, d)));
  const first = cards.querySelector('[data-name="dump"]') || cards.firstElementChild;
  if (first) first.classList.add('open');
})();
document.getElementById('btn-refresh').addEventListener('click', loadStatus);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Xenosaga2GUI/1.0"

    def log_message(self, fmt, *args):  # noqa: N802
        return

    def _send(self, status, body, content_type="text/plain", extra_headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json")

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            return self._send(200, page(), "text/html; charset=utf-8")
        if path == "/detect":
            isos = detect_isos()
            discs = xs2_discs(isos)
            return self._send_json(200, {"root": str(ROOT), "isos": isos, "discs": discs,
                                         "deps": detect_deps(), "default_out": default_out_dir(discs)})
        if path == "/schema":
            try:
                return self._send_json(200, {"commands": schema()})
            except Exception as exc:  # the page shows it instead of an empty list
                return self._send_json(200, {"error": f"{type(exc).__name__}: {exc}"})
        if path == "/browse-fs":
            params = parse_qs(url.query, keep_blank_values=True)
            return self._send_json(200, list_dir(params.get("path", [""])[0],
                                                 params.get("filter", [""])[0] or None))
        if path.startswith("/job/"):
            with JOBS_LOCK:
                job = JOBS.get(path[len("/job/"):])
            if not job:
                return self._send_json(404, {"error": "no such job"})
            return self._send_json(200, job.to_summary())
        if path.startswith("/stream/"):
            return self._serve_stream(path[len("/stream/"):])
        return self._send(404, "not found")

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        form = parse_qs(body, keep_blank_values=True)
        for prefix, handler in (("/preview/", self._preview), ("/run/", self._run)):
            if url.path.startswith(prefix):
                return handler(unquote(url.path[len(prefix):]), form)
        return self._send(404, "not found")

    def _argv(self, name, form):
        cmd = find_command(name)
        if cmd is None:
            raise ValueError(f"unknown command: {name}")
        return build_argv(cmd, form)

    def _preview(self, name, form):
        try:
            return self._send_json(200, {"cmd": self._argv(name, form)})
        except Exception as exc:
            return self._send_json(400, {"error": str(exc)})

    def _run(self, name, form):
        try:
            argv = self._argv(name, form)
        except Exception as exc:
            return self._send_json(400, {"error": str(exc)})
        try:
            job = Job(argv, cwd=ROOT)
        except OSError as exc:
            shown = " ".join(shlex.quote(c) for c in argv)
            return self._send_json(500, {"error":
                f"could not start the extractor:\n  $ {shown}\n  {type(exc).__name__}: {exc}\n\n"
                f"Expected CLI at: {CLI_TARGET}"
                + ("  (NOT FOUND — rebuild so xeno-cli sits next to the GUI)" if not CLI_TARGET.exists() else "")})
        with JOBS_LOCK:
            JOBS[job.id] = job
        return self._send_json(200, {"job_id": job.id})

    def _serve_stream(self, job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            return self._send(404, "no such job")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = job.subscribe()
        try:
            while True:
                try:
                    line = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if line is None:
                    self.wfile.write(b"data: __eof__\n\n")
                    self.wfile.flush()
                    return
                for chunk in line.replace("\r", "").split("\n"):
                    self.wfile.write(b"data: " + chunk.encode("utf-8") + b"\n")
                self.wfile.write(b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            job.unsubscribe(q)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


_PAGE: Optional[str] = None


def page() -> str:
    global _PAGE
    if _PAGE is None:
        _PAGE = INDEX_HTML.replace("/*__XS1_CSS__*/", XS1_CSS)
    return _PAGE


# ---------------------------------------------------------------------------
# Self-test (no browser, no disc)
# ---------------------------------------------------------------------------

def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    cmds = command_schema()
    names = {c["name"] for c in cmds} | {a for c in cmds for a in c["aliases"]}
    import cli as climod
    real = cli_parser(climod)
    sub = next(a for a in real._actions if isinstance(a, argparse._SubParsersAction))
    check(set(sub.choices) == names, f"every cli.py subcommand has a card ({len(sub.choices)} commands)")
    missing = sorted(n for n in sub.choices if not COMMAND_HELP.get(n) and not any(
        n in c["aliases"] and COMMAND_HELP.get(c["name"]) for c in cmds))
    check(not missing, "every cli.py subcommand's card has its long help text"
          + (f" (missing: {', '.join(missing)})" if missing else ""))
    named = {m for v in COMMAND_HELP.values()
             for m in re.findall(r"\b(?:text|exe|font|lang|texture|model)-[a-z]+\b", v)}
    check(named and named <= set(sub.choices), "every command a card's text names is a cli.py subcommand"
          + (f" (not: {', '.join(sorted(named - set(sub.choices)))})" if named - set(sub.choices) else ""))
    dump = find_command("dump")
    check(dump is not None and "extract" in dump["aliases"], "dump and extract share one card")
    form = {"iso": ["/d/Disc 1.iso\n/d/Disc 2.iso"], "out": ["/o"], "code": ["on"], "glob": [""]}
    argv = build_argv(dump, form)[len(CLI_ARGV):]
    check(argv == ["dump", "--iso", "/d/Disc 1.iso", "--iso", "/d/Disc 2.iso", "--out", "/o", "--code"],
          "dump card -> argv (two discs, a flag, an empty optional left out)")
    try:
        build_argv(dump, {"iso": ["/d/x.iso"]})
        check(False, "a missing required field is refused")
    except ValueError as exc:
        check("required" in str(exc), "a missing required field is refused")
    browse = find_command("browse")
    kinds = next(f for f in browse["fields"] if f["dest"] == "kinds")
    check(kinds["kind"] == "kinds" and "textures" in kinds["choices"], "browse --kinds becomes checkboxes")
    picked = {f"kinds.{k}": ["on"] for k in kinds["choices"][:2]}
    argv = build_argv(browse, {"out": ["/o"], **picked})[len(CLI_ARGV):]
    check(argv == ["browse", "--out", "/o", "--kinds", ",".join(kinds["choices"][:2])], "a subset of kinds")
    patch = find_command("patch")
    out = next(f for f in patch["fields"] if f["dest"] == "out")
    check(out.get("pick") == "save", "patch --out is a disc image to write, not a folder")
    # a command cli.py does not have yet: generic fields still work
    fake = argparse.ArgumentParser()
    fs = fake.add_subparsers(dest="cmd")
    p = fs.add_parser("recolor", help="recolour a texture entry in a copy of the disc")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--hue", type=float, default=0.92)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("entry")
    c = command_schema(fake)[0]
    argv = build_argv(c, {"iso": ["a.iso"], "out": ["b.iso"], "hue": ["0.5"], "dry_run": ["on"],
                          "entry": ["shion00_hair"]})[len(CLI_ARGV):]
    check(argv == ["recolor", "--iso", "a.iso", "--out", "b.iso", "--hue", "0.5", "--dry-run", "shion00_hair"],
          "a new command (options, a float, a flag, a positional) needs no GUI change")
    check(next(f for f in c["fields"] if f["dest"] == "out").get("pick") == "save",
          "its --out is recognised as a disc image from the help text")
    try:
        build_argv(c, {"iso": ["a.iso"], "out": ["b.iso"], "hue": ["pink"], "entry": ["x"]})
        check(False, "a non-number in a float field is refused")
    except ValueError:
        check(True, "a non-number in a float field is refused")
    check(xs2_discs(["/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 2).iso",
                     "/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 1) copy.iso",
                     "/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 1).iso",
                     "/x/Xenosaga Episode I - Der Wille zur Macht (USA).iso"])
          == ["/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 1).iso",
              "/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 2).iso"],
          "disc detection: Disc 1 then Disc 2, copies and other episodes skipped")
    scan = find_command("lang-scan")
    fl = {f["dest"]: f for f in scan["fields"]}
    check(fl["font"].get("pick") == "file" and fl["text"]["kind"] == "multi" and fl["iso"].get("pick") == "file"
          and find_command("lang-encode") and find_command("lang-preview"),
          "lang-scan / lang-encode / lang-preview have cards; the TrueType font is a file picker")
    prev = {f["dest"]: f for f in find_command("lang-preview")["fields"]}
    argv = build_argv(find_command("lang-preview"), {"glyphset": ["/g/glyphset.json"], "line": ["abc"],
                                                     "out": ["/o.png"]})[len(CLI_ARGV):]
    check(prev["out"].get("pick") == "save", "lang-preview's --out is a PNG file to save, not a folder")
    check(prev["line"].get("pick") is None and argv[:5] == ["lang-preview", "--glyphset", "/g/glyphset.json",
                                                            "--text", "abc"],
          "lang-preview's line of text is a text field passed as --text")
    deps = detect_deps()
    check("fonttools" in deps and "uharfbuzz" in deps, "the status panel reports fontTools and uharfbuzz")
    html = page()
    check("__XS1_CSS__" not in html and ".card-head" in html and "/schema" in html, "page assembled")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if not CLI_TARGET.exists():
        if FROZEN:
            _fatal("CLI binary missing", f"Expected {CLI_TARGET.name} next to the GUI.\n\n"
                   "Unzip the whole release folder first; running from inside the zip fails.")
        else:
            _fatal("cli.py missing", f"Expected cli.py next to gui.py at {CLI_TARGET}.")
    try:
        schema()
    except Exception as exc:
        _fatal("Could not read the CLI's commands", f"{type(exc).__name__}: {exc}")
    port = int(os.environ.get("PORT") or _free_port())
    url = f"http://localhost:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        _fatal("Could not start local server", f"Failed to bind to 127.0.0.1:{port} — {exc}.")
    print(f"[gui] {TITLE}")
    print(f"[gui] Open: {url}")
    print("[gui] Press Ctrl+C to stop.", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[gui] stopping")
    finally:
        server.server_close()
    return 0


XS1_CSS = r"""
:root {
  --bg-deep:        #04070d;
  --bg-mid:         #0a1525;
  --bg-panel:       #0c1828;
  --bg-card:        #0e1c30;
  --bg-input:       #060d18;
  --border:         rgba(93, 213, 255, 0.16);
  --border-strong:  rgba(93, 213, 255, 0.38);
  --fg:             #d8e8f5;
  --muted:          #6e8aab;
  --accent:         #5dd5ff;
  --accent-warm:    #ff9d3a;
  --accent-glow:    rgba(93, 213, 255, 0.35);
  --ok:             #62ffa1;
  --warn:           #ffc15d;
  --err:            #ff6e7f;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background:
    radial-gradient(ellipse 70% 50% at 50% 0%, rgba(93, 213, 255, 0.06) 0%, transparent 70%),
    linear-gradient(180deg, var(--bg-deep) 0%, #02040a 100%);
  color: var(--fg);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px; line-height: 1.5;
  overflow: hidden;
}
body::before {
  content: '';
  position: fixed; inset: 0; pointer-events: none; z-index: 1000;
  background: repeating-linear-gradient(180deg,
    transparent 0, transparent 2px,
    rgba(255, 255, 255, 0.012) 2px, rgba(255, 255, 255, 0.012) 3px);
  mix-blend-mode: lighten;
}

header {
  padding: 20px 28px 18px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 24px;
  background: linear-gradient(180deg, rgba(10, 21, 37, 0.92) 0%, rgba(10, 21, 37, 0.55) 100%);
  position: relative;
  backdrop-filter: blur(2px);
}
header::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent) 50%, transparent);
  opacity: 0.5;
}
header .title { display: flex; flex-direction: column; gap: 2px; line-height: 1; }
header h1 {
  margin: 0;
  font-family: "Cormorant Garamond", "Cormorant", "Georgia", serif;
  font-size: 28px; font-weight: 400; letter-spacing: 0.12em;
  text-transform: uppercase;
}
header h1 .i {
  color: var(--accent-warm);
  font-style: italic; font-weight: 500;
  margin: 0 0.15em;
  text-shadow: 0 0 14px rgba(255, 157, 58, 0.5);
}
header h1 .sep {
  color: var(--accent); opacity: 0.7; margin: 0 0.5em;
  font-weight: 300;
}
header h1 .extractor {
  font-family: "Inter", system-ui, sans-serif;
  font-size: 13px; letter-spacing: 0.35em; color: var(--muted);
  font-style: normal; font-weight: 500;
}
header .tagline {
  color: var(--muted); font-style: italic;
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 14px; letter-spacing: 0.04em;
  margin-top: 4px;
}
header .actions { margin-left: auto; display: flex; gap: 8px; }

main {
  display: grid; grid-template-columns: 1fr 480px;
  height: calc(100vh - 82px);
}
#cards {
  padding: 22px 28px 80px; overflow-y: auto;
}
#cards::-webkit-scrollbar { width: 10px; }
#cards::-webkit-scrollbar-thumb { background: rgba(93, 213, 255, 0.18); border-radius: 5px; }
#cards::-webkit-scrollbar-track { background: transparent; }

#right {
  border-left: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(12, 24, 40, 0.85) 0%, rgba(8, 16, 28, 0.95) 100%);
  display: flex; flex-direction: column; min-height: 0;
}
#right h2 {
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 14px; font-weight: 500;
  margin: 14px 16px 6px;
  color: var(--accent);
  text-transform: uppercase; letter-spacing: 0.22em;
  position: relative; padding-left: 14px;
}
#right h2::before {
  content: ''; position: absolute; left: 0; top: 50%; width: 7px; height: 1px;
  background: var(--accent); transform: translateY(-50%);
  box-shadow: 0 0 6px var(--accent-glow);
}

#status-box { padding: 0 16px 14px; }
#status-box .row {
  display: flex; justify-content: space-between;
  padding: 5px 0; font-size: 13px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
#status-box .row:last-child { border-bottom: 0; }
#status-box .row span:first-child {
  color: var(--muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase;
}
#status-box .row .v {
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
  color: var(--muted); max-width: 60%;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#status-box .row .v.ok  { color: var(--ok); }
#status-box .row .v.bad { color: var(--err); }

#log {
  flex: 1; margin: 0; padding: 14px 16px;
  background: #02050b; color: #b8d3eb;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; line-height: 1.55;
  overflow-y: auto; white-space: pre-wrap;
  border-top: 1px solid var(--border);
  min-height: 0;
}
#log::-webkit-scrollbar { width: 8px; }
#log::-webkit-scrollbar-thumb { background: rgba(93, 213, 255, 0.2); border-radius: 4px; }

.card {
  background: linear-gradient(180deg, rgba(14, 28, 48, 0.7) 0%, rgba(10, 21, 37, 0.7) 100%);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px 18px;
  margin-bottom: 12px;
  transition: border-color 0.18s, box-shadow 0.18s;
  position: relative;
}
.card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
  background: transparent; transition: background 0.18s, box-shadow 0.18s;
  border-radius: 6px 0 0 6px;
}
.card.run { border-color: var(--accent); box-shadow: 0 0 22px rgba(93, 213, 255, 0.12); }
.card.run::before { background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); }
.card.ok  { border-color: rgba(98, 255, 161, 0.4); }
.card.ok::before  { background: var(--ok); }
.card.err { border-color: rgba(255, 110, 127, 0.5); }
.card.err::before { background: var(--err); }

.card-head {
  display: flex; align-items: baseline; gap: 14px;
  cursor: pointer; user-select: none;
}
.card-head h3 {
  margin: 0;
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 18px; font-weight: 500;
  letter-spacing: 0.04em;
}
.card-head .step {
  font-family: "Cormorant Garamond", "Georgia", serif;
  color: var(--accent-warm); font-style: italic;
  font-size: 16px; min-width: 26px;
}
.card-head .badge {
  font-size: 10px; padding: 3px 9px; border-radius: 2px;
  background: rgba(110, 138, 171, 0.12);
  color: var(--muted);
  margin-left: auto;
  text-transform: uppercase; letter-spacing: 0.14em; font-weight: 600;
}
.card-head .badge.run { background: rgba(93, 213, 255, 0.18);  color: var(--accent); }
.card-head .badge.ok  { background: rgba(98, 255, 161, 0.18);  color: var(--ok); }
.card-head .badge.err { background: rgba(255, 110, 127, 0.18); color: var(--err); }
.card-head .chev {
  color: var(--muted); transition: transform 0.2s;
  margin-left: 10px; font-size: 12px;
}
.card.open .chev { transform: rotate(180deg); }

.card-body { display: none; margin-top: 14px; }
.card.open .card-body { display: block; }
.card-body p.help {
  margin: 0 0 14px; color: var(--muted); font-size: 13px; line-height: 1.55;
}
.card-body p.help code { color: var(--accent); font-size: 12px; }

.card .row {
  display: grid; grid-template-columns: 130px 1fr; gap: 12px;
  margin-bottom: 9px; align-items: center;
}
.card .row label {
  color: var(--muted); font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.08em;
}
.card .row .with-pick {
  display: grid; grid-template-columns: 1fr auto; gap: 6px;
}
.card .row .check-wrap {
  display: flex; align-items: center; gap: 10px;
}
.card .row .check-wrap span { color: var(--muted); font-size: 12px; }

.card input[type=text], .card textarea, .card select {
  width: 100%; background: var(--bg-input); color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 3px; padding: 8px 10px;
  font: inherit;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 12.5px; transition: border-color 0.15s, box-shadow 0.15s;
}
.card input[type=text]:focus, .card textarea:focus, .card select:focus {
  outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent-glow), 0 0 10px rgba(93, 213, 255, 0.18);
}
.card select option { background: var(--bg-mid); }
.card input[type=checkbox] {
  accent-color: var(--accent); width: 16px; height: 16px;
}
.card textarea { min-height: 60px; resize: vertical; line-height: 1.5; }
.card .actions { margin-top: 14px; display: flex; gap: 8px; }

button {
  background: linear-gradient(180deg, #5dd5ff 0%, #3da8d8 100%);
  color: #02101e; border: 0;
  padding: 8px 16px; border-radius: 3px;
  font: inherit; font-weight: 600; font-size: 12px;
  letter-spacing: 0.1em; text-transform: uppercase;
  cursor: pointer; transition: filter 0.15s, transform 0.05s;
  box-shadow: 0 0 12px rgba(93, 213, 255, 0.25);
}
button:hover { filter: brightness(1.15); }
button:active { transform: translateY(1px); }
button.ghost {
  background: transparent; color: var(--fg);
  border: 1px solid var(--border);
  font-weight: 500; box-shadow: none;
}
button.ghost:hover { background: rgba(93, 213, 255, 0.08); border-color: var(--accent); }
button.pick {
  padding: 7px 12px; font-size: 11px;
  background: rgba(93, 213, 255, 0.12); color: var(--accent);
  border: 1px solid var(--border); box-shadow: none; font-weight: 500;
}
button.pick:hover { background: rgba(93, 213, 255, 0.2); border-color: var(--accent); }
button:disabled { opacity: 0.4; cursor: not-allowed; }

code, kbd {
  background: rgba(93, 213, 255, 0.08); color: var(--accent);
  border-radius: 2px; padding: 1px 5px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
}

/* ------------------------------------------------------------------ */
/* File picker modal                                                   */
/* ------------------------------------------------------------------ */
.modal[hidden] { display: none; }
.modal {
  position: fixed; inset: 0; z-index: 100;
  display: flex; align-items: center; justify-content: center;
}
.modal-backdrop {
  position: absolute; inset: 0;
  background: rgba(2, 5, 11, 0.82);
  backdrop-filter: blur(4px);
  animation: fadeIn 0.15s ease-out;
}
.modal-window {
  position: relative; z-index: 1;
  background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg-mid) 100%);
  border: 1px solid var(--border-strong);
  box-shadow: 0 0 0 1px var(--border), 0 0 80px rgba(93, 213, 255, 0.18), 0 20px 60px rgba(0,0,0,0.5);
  border-radius: 8px;
  width: 760px; max-width: 92vw; height: 580px; max-height: 88vh;
  display: flex; flex-direction: column;
  animation: slideIn 0.18s ease-out;
  overflow: hidden;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes slideIn {
  from { opacity: 0; transform: translateY(-8px) scale(0.985); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
.modal-header {
  padding: 16px 22px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 12px;
  background: linear-gradient(180deg, rgba(93, 213, 255, 0.04), transparent);
}
.modal-header h2 {
  margin: 0;
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 18px; font-weight: 500; letter-spacing: 0.06em;
}
.modal-header .x {
  margin-left: auto; background: transparent; border: 0; color: var(--muted);
  font-size: 22px; cursor: pointer; padding: 0 6px; box-shadow: none;
  text-transform: none; letter-spacing: 0;
}
.modal-header .x:hover { color: var(--err); background: transparent; }
.fs-toolbar {
  padding: 12px 18px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
  background: rgba(2, 5, 11, 0.3);
}
.fs-crumb {
  flex: 1; overflow-x: auto; white-space: nowrap;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
}
.fs-crumb a { color: var(--accent); cursor: pointer; padding: 2px 4px; border-radius: 2px; }
.fs-crumb a:hover { background: rgba(93, 213, 255, 0.12); }
.fs-crumb .sep { color: var(--muted); margin: 0 2px; opacity: 0.6; }
.fs-list { flex: 1; overflow-y: auto; padding: 4px 6px; }
.fs-list::-webkit-scrollbar { width: 10px; }
.fs-list::-webkit-scrollbar-thumb { background: rgba(93, 213, 255, 0.18); border-radius: 5px; }
.fs-entry {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; cursor: pointer; border-radius: 3px; font-size: 13px;
}
.fs-entry:hover { background: rgba(93, 213, 255, 0.08); }
.fs-entry.selected { background: rgba(93, 213, 255, 0.18); outline: 1px solid var(--accent); }
.fs-entry .icon { width: 18px; text-align: center; opacity: 0.85; font-size: 14px; }
.fs-entry .icon.dir { color: var(--accent-warm); }
.fs-entry .name { flex: 1; }
.fs-entry .size {
  margin-left: auto; color: var(--muted); font-size: 11px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.fs-empty { padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
.fs-roots {
  border-top: 1px solid var(--border);
  padding: 8px 18px; display: flex; flex-wrap: wrap; gap: 6px;
  font-size: 11px; align-items: center;
}
.fs-roots span { color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; margin-right: 4px; }
.fs-roots a {
  color: var(--accent); padding: 3px 8px; border-radius: 2px;
  border: 1px solid var(--border); cursor: pointer;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 11px;
}
.fs-roots a:hover { background: rgba(93, 213, 255, 0.12); border-color: var(--accent); }
.modal-footer {
  padding: 14px 18px; border-top: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
  background: rgba(2, 5, 11, 0.3);
}
.modal-footer .sel-path {
  flex: 1; min-width: 0; color: var(--muted); font-size: 12px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.modal-footer .sel-path.has { color: var(--fg); }
.modal-error {
  padding: 8px 18px; color: var(--err); font-size: 12px;
  background: rgba(255, 110, 127, 0.06);
  border-bottom: 1px solid rgba(255, 110, 127, 0.2);
}
.fs-quick {
  padding: 8px 18px; border-bottom: 1px solid var(--border);
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  background: rgba(93, 213, 255, 0.03);
}
.fs-quick span { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; }
.fs-quick a {
  color: var(--fg); padding: 4px 10px; border-radius: 2px;
  border: 1px solid var(--border); cursor: pointer; font-size: 12px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis;
}
.fs-quick a:hover { background: rgba(93, 213, 255, 0.12); border-color: var(--accent); color: var(--accent); }
.fs-quick a.selected { background: rgba(93, 213, 255, 0.18); border-color: var(--accent); color: var(--accent); }
"""

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        import traceback
        _fatal("Unexpected error during startup", f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
