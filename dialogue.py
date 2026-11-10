#!/usr/bin/env python3
"""dialogue.py — Episode II's dialogue carved out line by line.

Episode I keeps its spoken lines inside Java scene scripts (``evt-export``)
and its voices in streams. Episode II spreads the same material over four
places, and this module walks all of them in the order the game plays them:

* **Scene, field and battle scripts** (``cf\\scNNNN.sb``, ``model\\map\\E2_*.bin``).
  A line is dialogue when its ``.sbl`` string reaches one of the natives that
  show text. Every one of the 13,818 text pushes on the disc reaches a native;
  the dialogue ones are:

  ====================  ==========================================================
  ``talk.msgDtl``       a field text box, ``[Speaker]`` tag first (4, text)
  ``talk.smsg``         a message window: tutorials, "You obtained ..." (text)
  ``talk.menuAddItem``  a choice; the options are one string split by ``\\n``
  ``cfe.setMes``        a real-time event subtitle: (text, start, end, voice, 0),
                        times in 30 fps frames; the call with an empty text names
                        the event's voice track (``cfevent/BE6302/BE6302``)
  ``bat.msgPut``        a line spoken in battle (x, y, text, ..., ...)
  ====================  ==========================================================

  Voice without text is listed too: ``bat.streamPlay`` (battle barks,
  ``sound\\adx\\battle\\*.adx``) and ``runtime.adxplay``/``runtime.adx`` (field
  voice clips, ``sound\\adx\\CF_*.adx``). Resource strings (effect, motion and
  movie names) reach other natives and are left out.

  Lines come out per routine (named entry points, call targets, thread starts)
  in code order. Branches are kept, not flattened: the transcript shows
  ``if``/``else``/``loop``/``case N`` blocks as the compiler laid the code out
  (the conditions themselves are flags and are not named).

* **Movie subtitles** (``movie\\en\\*.txt``, EUC-JP): ``mm:ss:ff`` cues at 30
  frames a second, an empty cue clears, ``$`` codes position the text, ``\\n``
  breaks it, ``[漢字/ルビ]`` is ruby. Written as one ``.srt`` per movie.

* **Voice lines**, with ``--voice`` (needs ffmpeg): every movie cue cut out of
  its ``.sfd``'s ADX stereo track (the full movie mix: voice, music and sound
  together, as the player hears it), and every timed event line cut out of its
  event's ADX voice track (``sound\\adx\\cfevent\\...``) by the script's own frame
  numbers.

Usage::

    python dialogue.py --iso DISC1.iso [--iso DISC2.iso] --out DIR [--voice]
    python dialogue.py --self-test

Output (``DIR``)::

    scripts/<scene>.txt        play-script transcript per scene/battle script
    subtitles/<movie>.srt      movie subtitles
    subtitles/<scene>.srt      timed event lines, relative to the event's voice track
    voice/<movie|track>/NN_<who>.wav   one clip per line (with --voice)
    dialogue.csv               every line: source, scene, order, entry, branch,
                               speaker, text, sbl_id, native, start, end, voice, raw
    voice_check.csv            per clip: duration, RMS, onset energy (with --voice)
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sb    # noqa: E402
import sbl   # noqa: E402

FPS = 30                         # subtitle and event frames (BE6302: frame 600 = 20.0 s of a 20.76 s track)
RATE = 48000                     # ADX tracks and the movies' ADX streams are 48 kHz stereo

DIALOGUE_NATIVES = {
    "talk.msgDtl": "box",
    "talk.smsg": "message",
    "talk.menuAddItem": "choice",
    "cfe.setMes": "event",
    "bat.msgPut": "battle",
}
VOICE_NATIVES = {"bat.streamPlay": "battle voice", "runtime.adxplay": "voice", "runtime.adx": "voice"}

_CODE_RE = re.compile(r"\$[a-z]+[0-9A-Fa-f,]*;")
_RUBY_RE = re.compile(r"\[([^\[\]/]+)/[^\[\]]*\]")
_SPEAKER_RE = re.compile(r"^\[([^\[\]]{1,32})\](.*)$", re.S)
_TIME_RE = re.compile(r"^(\d+):(\d\d):(\d\d)$")


# ---------------------------------------------------------------- the lines

@dataclass
class Line:
    source: str                 # script | event | battle | movie
    scene: str
    order: int
    entry: str = ""
    branch: str = ""
    speaker: str = ""
    text: str = ""
    sbl_id: Optional[int] = None
    native: str = ""
    start: Optional[float] = None
    end: Optional[float] = None
    voice: str = ""             # voice track / ADX the line plays from
    raw: str = ""
    frames: str = ""            # event lines: first-last frame on the event's timeline
    offset: int = 0             # event lines: event frame at which the voice track starts
    word: int = -1              # script code address (words)
    depth: int = 0
    headers: List[str] = field(default_factory=list)   # branch headers opened before this line
    clip: str = ""

    def row(self) -> dict:
        return {"source": self.source, "scene": self.scene, "order": self.order,
                "entry": self.entry, "branch": self.branch, "speaker": self.speaker,
                "text": self.text, "sbl_id": "" if self.sbl_id is None else self.sbl_id,
                "native": self.native,
                "start": "" if self.start is None else f"{self.start:.3f}",
                "end": "" if self.end is None else f"{self.end:.3f}",
                "voice": self.voice, "clip": self.clip, "frames": self.frames, "raw": self.raw}


CSV_FIELDS = ["source", "scene", "order", "entry", "branch", "speaker", "text", "sbl_id",
              "native", "start", "end", "voice", "clip", "frames", "raw"]


def clean(text: str) -> str:
    """Game codes out, ruby to its base text, the game's ``\\n`` to a space."""
    t = _CODE_RE.sub("", text)
    t = _RUBY_RE.sub(r"\1", t)
    t = t.replace("\\n", " ")
    return re.sub(r"[ \t]+", " ", t).strip()


def split_speaker(text: str) -> Tuple[str, str]:
    m = _SPEAKER_RE.match(text)
    return (m.group(1).strip(), m.group(2)) if m else ("", text)


# ---------------------------------------------------- native calls of a script

def _value_of(v) -> Tuple[str, object]:
    """(kind, value) of an argument's value instruction."""
    if not isinstance(v, sb.Insn):
        return ("?", None)
    if v.op == 0x06:
        return ("text", v.operand & 0xFFFF)
    if v.op == 0x04:
        return ("int", sb._signed(v.operand))
    if v.op in sb.CONST_OPS:
        return ("int", sb.CONST_OPS[v.op])
    return ("expr", sb.NAMES.get(v.op, "?"))


def native_calls(script: sb.Script) -> Iterator[Tuple[int, str, List[Tuple[str, object]]]]:
    """(word, 'class.function', [(kind, value) per argument]) for every native
    call, from a symbolic walk of the stack: arguments are (value, type)
    pairs then their count (docs/SCRIPTS.md). The stack is reset at every
    label, which loses nothing here: all 13,818 text pushes on the disc are
    attributed to an argument this way."""
    insns = sb.disassemble(script.code)
    labels = {w for _, w in script.entries}
    for i in insns:
        if i.op in sb.CODE_REFS:
            labels.add(i.operand)
        elif i.op == 0x25:
            labels.update([i.default] + [t for _, t in i.table])
    st: list = []
    for i in insns:
        if i.word in labels:
            st = []
        op = i.op
        if op in (0x03, 0x04, 0x06, 0x07, 0x08, 0x09) or op in sb.CONST_OPS:
            st.append(i)
        elif op == 0x01:
            n = 1 if i.operand == 10 else 2
            del st[-n:]
            st.append(i)
        elif op in (0x0A, 0x0B):
            del st[-2:]
        elif op in (0x0C, 0x0D):
            del st[-2:]
            st.append(i)
        elif op in (0x0F, 0x10, 0x16, 0x17, 0x19, 0x21, 0x22, 0x23, 0x24, 0x25):
            if st:
                st.pop()
        elif op == 0x18:
            st.append(st[-1] if st else i)
        elif op in (0x1A, 0x1B, 0x20):
            if st:
                st.pop()
            st.append(i)
        elif op == 0x11:
            st += ["enter"] * 3
        elif op == 0x12:
            while st and st[-1] != "enter":
                st.pop()
            del st[-3:]
            st.append(i)
        elif op == 0x15:
            cls, fn = script.imports[i.operand & 0xFFFF]
            args: List[Tuple[str, object]] = []
            if st and isinstance(st[-1], sb.Insn) and (st[-1].op in sb.CONST_OPS or st[-1].op == 0x04):
                cnt = st.pop()
                n = sb.CONST_OPS.get(cnt.op, cnt.operand)
                if 0 <= n <= 32 and len(st) >= 2 * n:
                    pairs = st[len(st) - 2 * n:]
                    del st[len(st) - 2 * n:]
                    args = [_value_of(pairs[2 * k]) for k in range(n)]
            yield i.word, f"{cls}.{fn}", args
            st.append(i)
        elif op in (0x0E, 0x13, 0x14, 0x1D):
            st = []


# ------------------------------------------------- routines and branch regions

def _structure(script: sb.Script):
    """Routines (name, start, reachable words) and branch regions.

    A routine is a named entry point, a ``call`` target or a ``pushcode``
    target (a thread or callback); its words are those reachable from its
    start without entering another routine. Regions follow the compiler's
    layout: a forward ``jz``/``jnz`` opens an ``if`` up to its target, a
    ``jmp`` over the target at the end of it an ``else``; a backward jump is a
    ``loop``; a ``switch`` opens one region per case."""
    insns = sb.disassemble(script.code)
    by_word = {i.word: i for i in insns}
    nxt = {a.word: b.word for a, b in zip(insns, insns[1:])}
    prev = {b.word: a.word for a, b in zip(insns, insns[1:])}
    starts: Dict[int, str] = {}
    for name, w in sorted(script.entries, key=lambda e: e[1]):
        starts.setdefault(w, name)
    for i in insns:
        if i.op == 0x12:
            starts.setdefault(i.operand, f"L_{i.operand:04x}")
        elif i.op == 0x07:
            starts.setdefault(i.operand, f"L_{i.operand:04x}")
    owner: Dict[int, str] = {}
    routines = []
    for w0 in sorted(starts):
        name = starts[w0]
        seen, todo = set(), [w0]
        while todo:
            w = todo.pop()
            if w in seen or w not in by_word or (w != w0 and w in starts):
                continue
            seen.add(w)
            i = by_word[w]
            if i.op in (0x13, 0x14, 0x1D):
                continue
            if i.op == 0x0E:
                todo.append(i.operand)
                continue
            if i.op in (0x0F, 0x10):
                todo.append(i.operand)
            if i.op == 0x25:
                todo += [i.default] + [t for _, t in i.table]
                continue
            if w in nxt:
                todo.append(nxt[w])
        for w in seen:
            owner.setdefault(w, name)
        routines.append((name, w0, seen))

    regions: List[Tuple[int, int, str]] = []
    for i in insns:
        if i.op in (0x0E, 0x0F, 0x10):
            t = i.operand
            if t <= i.word:
                regions.append((t, nxt.get(i.word, i.word + 3), "loop"))
            elif i.op in (0x0F, 0x10) and i.word in nxt:
                body0, end = nxt[i.word], t
                last = by_word.get(prev.get(t, -1))
                if last is not None and last.op == 0x0E and last.operand > t and last.word >= body0:
                    regions.append((body0, t, "if"))
                    regions.append((t, last.operand, "else"))
                else:
                    regions.append((body0, end, "if"))
        elif i.op == 0x25:
            cases = sorted({(t, f"case {k}") for k, t in i.table} | {(i.default, "default")})
            join = 0
            for t, _ in cases:
                p = by_word.get(prev.get(t, -1))
                if p is not None and p.op == 0x0E and p.operand > join:
                    join = p.operand
            for k, (t, label) in enumerate(cases):
                end = cases[k + 1][0] if k + 1 < len(cases) else max(join, t + 1)
                if end > t:
                    regions.append((t, end, label))
    return routines, owner, regions


def _path(regions, w: int) -> List[Tuple[int, int, str]]:
    return sorted((r for r in regions if r[0] <= w < r[1]), key=lambda r: (r[0], -r[1]))


# ------------------------------------------------------------ script lines

def script_lines(script: sb.Script, strings: List[bytes], scene: str,
                 battle: bool = False) -> List[Line]:
    """Every dialogue line (and voice-only call) of one script, in routine
    and code order, with its branch path."""
    routines, owner, regions = _structure(script)
    calls = {w: (name, args) for w, name, args in native_calls(script)}
    order_of = {name: k for k, (name, _, _) in enumerate(routines)}
    # the event's voice track starts at its first sound keyframe (cfe.stKySnd's third
    # argument): 0 in most events, 85 in sc8016, 1830 in sc8073 (the second half of a
    # split event, whose frames continue from the first half). Checked against the
    # tracks' speech energy for all 78 voiced events (docs/DIALOGUE.md).
    keys = [args[2][1] for name, args in calls.values()
            if name == "cfe.stKySnd" and len(args) > 2 and args[2][0] == "int"]
    key0 = min(keys) if keys else 0
    out: List[Line] = []
    track = ""
    for w in sorted(calls):
        name, args = calls[w]
        if name not in DIALOGUE_NATIVES and name not in VOICE_NATIVES:
            continue
        texts = [(k, v) for k, (kind, v) in enumerate(args) if kind == "text"]

        def s(idx):
            return sbl.to_text(strings[idx]) if isinstance(idx, int) and idx < len(strings) else ""

        ints = [v if kind == "int" else None for kind, v in args]
        ln = Line(source="battle" if battle else "script", scene=scene, order=0,
                  entry=owner.get(w, ""), native=name, word=w)
        if name == "cfe.setMes":
            raw = s(args[0][1]) if args and args[0][0] == "text" else ""
            voice = s(args[3][1]) if len(args) > 3 and args[3][0] == "text" else ""
            if voice and not raw:
                track = voice                         # names the event's voice track
                continue
            ln.source = "event"
            ln.sbl_id = args[0][1] if args and args[0][0] == "text" else None
            if len(ints) > 2 and ints[1] is not None and ints[2] is not None:
                ln.frames = f"{ints[1]}-{ints[2]}"
                ln.offset = key0
                ln.start, ln.end = (ints[1] - key0) / FPS, (ints[2] - key0) / FPS
            ln.voice = track
        elif name in VOICE_NATIVES:
            if not texts:
                continue
            ln.sbl_id = texts[0][1]
            ln.voice = s(texts[0][1])
            raw = ""
        else:
            if not texts:
                continue
            k, idx = texts[-1] if name != "talk.msgDtl" else texts[0]
            ln.sbl_id = idx
            raw = s(idx)
        if name == "talk.menuAddItem":
            ln.text = " | ".join(clean(o) for o in raw.split("\\n"))
            ln.speaker = "CHOICE"
        elif raw:
            spk, body = split_speaker(raw)
            ln.speaker, ln.text = spk, clean(body)
        ln.raw = raw
        if name == "cfe.setMes" and not ln.text:
            continue                                  # empty clears ("\n" only)
        path = _path(regions, w)
        ln.depth = len(path)
        ln.branch = " > ".join(f"{lab}@{a:04x}" for a, _, lab in path)
        out.append(ln)
    out.sort(key=lambda l: (order_of.get(l.entry, 1 << 30), l.word))
    opened: List[Tuple[int, int, str]] = []
    cur_entry = None
    for n, ln in enumerate(out):
        ln.order = n
        if ln.entry != cur_entry:
            cur_entry, opened = ln.entry, []
        path = _path(regions, ln.word)
        keep = 0
        while keep < min(len(opened), len(path)) and opened[keep] == path[keep]:
            keep += 1
        ln.headers = [("  " * d) + f"{lab}:" for d, (_, _, lab) in enumerate(path) if d >= keep]
        opened = path
    return out


def transcript(scene: str, header: str, lines: List[Line]) -> str:
    """A play-script view of one script's lines."""
    out = [f"# {scene}  {header}".rstrip(), ""]
    cur = None
    track = None
    for ln in lines:
        if ln.entry != cur:
            cur = ln.entry
            out += ["", f"== {cur} =="]
        if ln.source == "event" and ln.voice != track:
            track = ln.voice
            at = f", starts at event frame {ln.offset}" if ln.offset else ""
            out.append(f"   (voice track: {track or 'none'}{at}; times are on the track)")
        out += ["  " + h for h in ln.headers]
        pad = "  " * (ln.depth + 1)
        ref = f"  [#{ln.sbl_id}]" if ln.sbl_id is not None and ln.native not in VOICE_NATIVES else ""
        if ln.native in VOICE_NATIVES:
            out.append(f"{pad}~ voice {ln.voice} ({VOICE_NATIVES[ln.native]})")
        elif ln.native == "talk.menuAddItem":
            out.append(f"{pad}CHOICE: {ln.text}{ref}")
        elif ln.source == "event":
            when = f"[{_clock(ln.start)}-{_clock(ln.end)}] " if ln.start is not None else ""
            who = f"{ln.speaker}: " if ln.speaker else ""
            out.append(f"{pad}{when}{who}{ln.text}{ref}")
        else:
            kind = {"talk.smsg": "(message)", "bat.msgPut": "(battle)"}.get(ln.native, "")
            if ln.speaker:
                out.append(f"{pad}{ln.speaker}{' ' + kind if kind else ''}: {ln.text}{ref}")
            else:
                out.append(f"{pad}{kind or '(narration)'} {ln.text}{ref}")
    return "\n".join(out).replace("\n\n\n", "\n\n") + "\n"


def _clock(t: Optional[float]) -> str:
    if t is None:
        return "?"
    sign = "-" if t < 0 else ""
    m, s = divmod(abs(t), 60)
    return f"{sign}{int(m):02d}:{s:05.2f}"


# ------------------------------------------------------------ movie subtitles

def parse_subtitles(data: bytes) -> List[Tuple[float, float, str]]:
    """(start s, end s, text) cues of a ``movie\\en\\*.txt``; empty cues clear.

    Times are ``mm:ss:ff`` (ff at 30 fps), a frame count, or ``+N`` frames
    after the previous cue. Comment lines start with ``;``; ``end`` closes."""
    text = data.decode("euc_jp", "replace")
    events: List[Tuple[float, str]] = []
    last = 0.0
    for raw in text.splitlines():
        if not raw.strip() or raw.startswith(";"):
            continue
        tm, _, body = raw.partition("\t")
        tm = tm.strip()
        if tm.lower() == "end":
            events.append((last, ""))
            break
        m = _TIME_RE.match(tm)
        if m:
            t = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / FPS
        elif tm.startswith("+") and tm[1:].isdigit():
            t = last + int(tm[1:]) / FPS
        elif tm.isdigit():
            t = int(tm) / FPS
        else:
            continue
        last = t
        events.append((t, body.rstrip("\r")))
    cues = []
    for k, (t, body) in enumerate(events):
        shown = _RUBY_RE.sub(r"\1", _CODE_RE.sub("", body)).strip()
        if not shown:
            continue
        end = events[k + 1][0] if k + 1 < len(events) else t + 3.0
        cues.append((t, end, shown))
    return cues


def srt(cues: Iterable[Tuple[float, float, str]]) -> str:
    def ts(t):
        ms = int(round(t * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    out = []
    for n, (a, b, text) in enumerate(cues, 1):
        out += [str(n), f"{ts(a)} --> {ts(b)}", text.replace("\\n", "\n"), ""]
    return "\n".join(out)


# ---------------------------------------------------------------- the disc

class Disc:
    """Read objects from one or two disc images through the kit's TOC."""

    def __init__(self, isos: List[str]):
        from chains import CHAINS, ChainReader, available_chains
        from iso9660 import IsoImage
        from toc import parse_toc
        self.chains: Dict[int, Tuple[object, Dict[str, object]]] = {}
        for p in isos:
            iso = IsoImage(p)
            for c in available_chains(iso):
                if c in self.chains:
                    continue
                reader = ChainReader(iso, c)
                entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[c][0])
                self.chains[c] = (reader, {e.path.lower(): e for e in entries})

    def paths(self) -> Iterator[Tuple[int, str]]:
        for c, (_, byp) in sorted(self.chains.items()):
            for e in byp.values():
                yield c, e.path

    def find(self, path: str) -> Optional[Tuple[int, object]]:
        for c, (_, byp) in sorted(self.chains.items()):
            e = byp.get(path.lower())
            if e is not None:
                return c, e
        return None

    def read(self, chain: int, entry) -> bytes:
        reader = self.chains[chain][0]
        data = bytes(reader.read(entry.byte_offset, entry.size))
        if getattr(entry, "compressed", False):
            import arx
            data = arx.decompress(data)
        return data

    def stream(self, chain: int, entry, chunk: int = 8 << 20) -> Iterator[bytes]:
        reader = self.chains[chain][0]
        for part in reader.read_iter(entry.byte_offset, entry.size):
            yield bytes(part)


# ------------------------------------------------------------------- audio

def _decode(ffmpeg: str, fmt: str, chunks: Iterable[bytes], wav_path: Path) -> None:
    """Feed a container to ffmpeg on stdin, keep its first audio stream (the
    ADX one in an .sfd) as 48 kHz stereo 16-bit WAV."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", fmt, "-i", "pipe:0",
           "-map", "0:a:0", "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_s16le", str(wav_path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for c in chunks:
            p.stdin.write(c)
        p.stdin.close()
    except BrokenPipeError:
        pass
    err = p.stderr.read().decode("utf-8", "replace")
    if p.wait() != 0 or not wav_path.exists():
        raise RuntimeError(f"ffmpeg failed: {err.strip()[-300:]}")


def _read_wav(path: Path) -> Tuple[bytes, int, int]:
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes()), w.getnchannels(), w.getframerate()


def _write_clip(path: Path, pcm: bytes, ch: int, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def _rms_db(pcm: bytes) -> float:
    import array
    n = len(pcm) // 2
    if n == 0:
        return -120.0
    vals = array.array("h", pcm[:2 * n])
    if sys.byteorder != "little":
        vals.byteswap()
    sub = vals[::max(1, n // 48000)]           # a sample of the clip is plenty
    ms = sum(v * v for v in sub) / len(sub)
    return 10 * math.log10(ms / (32768.0 ** 2)) if ms > 0 else -120.0


def _slice(pcm: bytes, ch: int, rate: int, a: float, b: float) -> bytes:
    fa = max(0, int(round(a * rate))) * ch * 2
    fb = max(0, int(round(b * rate))) * ch * 2
    return pcm[fa:min(fb, len(pcm))]


def _slug(text: str, n: int = 24) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return (s[:n].rstrip("_") or "line")


def align_offset(pcm: bytes, ch: int, rate: int, lines: List[Line],
                 lo: int = -90) -> Tuple[Optional[int], float, float]:
    """Frames to subtract from the lines' times so that they sit on the
    track's speech: the offset that makes the track loudest inside the lines
    against outside them, with the contrast in dB and the best contrast more
    than a second away (a single sharp peak is the evidence). Validated on all
    78 voiced events: 76 come out within a few frames of their scripted time,
    sc8016 and sc8073 on their first sound keyframe (84 vs 85, 1828 vs 1830)."""
    import array
    vals = array.array("h", pcm[:len(pcm) // 2 * 2])
    if sys.byteorder != "little":
        vals.byteswap()
    hop = rate // FPS * ch
    env = []
    for i in range(0, len(vals) - hop + 1, hop):
        w = vals[i:i + hop:4]
        e = sum(v * v for v in w) / max(1, len(w))
        env.append(0.5 * math.log10(e + 1e-6))
    n = len(env)
    pre = [0.0]
    for x in env:
        pre.append(pre[-1] + x)
    spans = [(int(round(l.start * FPS)), int(round(l.end * FPS))) for l in lines]
    hi = max(0, max(t for _, t in spans) - n) + 600
    res = []
    for o in range(lo, hi):
        si, ci = 0.0, 0
        for a, b in spans:
            a, b = max(0, a - o), min(n, b - o)
            if b > a:
                si += pre[b] - pre[a]
                ci += b - a
        if ci < 30 or n - ci < 30:
            continue
        res.append((20 * (si / ci - (pre[n] - si) / (n - ci)), o))
    if not res:
        return None, 0.0, 0.0
    best = max(res)
    second = max((s for s, o in res if abs(o - best[1]) > FPS), default=-99.0)
    return best[1], best[0], second


def carve(pcm: bytes, ch: int, rate: int, cues: List[Line], folder: Path,
          checks: List[dict], label: str) -> None:
    """Cut one WAV per timed line; record its loudness and whether the
    sound rises at the cue's start."""
    total = len(pcm) / (ch * 2 * rate)
    for n, ln in enumerate(cues, 1):
        if ln.start is None or ln.end is None or ln.end <= ln.start:
            continue
        who = _slug(ln.speaker) if ln.speaker else _slug(ln.text, 20)
        clip = folder / f"{n:02d}_{who}.wav"
        body = _slice(pcm, ch, rate, max(0.0, ln.start), ln.end)
        if not body:
            checks.append({"track": label, "n": n, "clip": "", "start": f"{ln.start:.2f}",
                           "end": f"{ln.end:.2f}", "seconds": 0, "rms_db": "",
                           "onset_db": "", "pre_db": "", "note": f"past the end ({total:.1f} s)"})
            continue
        _write_clip(clip, body, ch, rate)
        ln.clip = str(clip)
        a = max(0.0, ln.start)
        onset = _rms_db(_slice(pcm, ch, rate, a, min(ln.end, a + 0.6)))
        pre = _rms_db(_slice(pcm, ch, rate, max(0.0, a - 0.6), a))
        checks.append({"track": label, "n": n, "clip": clip.name, "start": f"{ln.start:.2f}",
                       "end": f"{ln.end:.2f}", "seconds": f"{ln.end - ln.start:.2f}",
                       "rms_db": f"{_rms_db(body):.1f}", "onset_db": f"{onset:.1f}",
                       "pre_db": f"{pre:.1f}",
                       "note": "" if ln.end <= total + 0.05 else f"ends past the track ({total:.1f} s)"})


# -------------------------------------------------------------------- run

def _scene_scripts(disc: Disc) -> Iterator[Tuple[str, str, bool, int, object, Optional[Tuple[int, object]]]]:
    for c, path in disc.paths():
        low = path.lower()
        is_scene = low.startswith("cf\\") and low.endswith(".sb") and low.count("\\") == 1
        is_battle = low.startswith("model\\map\\") and low.endswith(".bin") and low.count("\\") == 2
        if not (is_scene or is_battle):
            continue
        head, _, name = path.rpartition("\\")
        stem = name.rsplit(".", 1)[0]
        bank = disc.find(f"{head}\\en\\{stem}.sbl")
        hit = disc.find(path)
        yield stem, path, is_battle, hit[0], hit[1], bank


def run(isos: List[str], out: Path, voice: bool = False, only: Optional[List[str]] = None) -> int:
    disc = Disc(isos)
    out.mkdir(parents=True, exist_ok=True)
    (out / "scripts").mkdir(exist_ok=True)
    (out / "subtitles").mkdir(exist_ok=True)
    all_lines: List[Line] = []
    events: Dict[str, List[Line]] = {}
    written: List[Tuple[str, str, List[Line]]] = []
    n_scripts = 0
    for stem, path, battle, chain, entry, bank in sorted(_scene_scripts(disc), key=lambda t: (t[0], t[1])):
        if only and not any(o.lower() in stem.lower() for o in only):
            continue
        data = disc.read(chain, entry)
        if data[:4] != sb.MAGIC:
            continue
        script = sb.parse(data)
        strings = sbl.parse(disc.read(*bank)) if bank else []
        lines = script_lines(script, strings, stem, battle=battle)
        n_scripts += 1
        if not lines:
            continue
        head = f"{path} ({script.source})" + (f", lines from {bank[1].path}" if bank else "")
        written.append((stem, head, lines))
        all_lines += lines
        ev = [l for l in lines if l.source == "event" and l.start is not None]
        if ev:
            events[stem] = ev

    movies: Dict[str, Tuple[int, object, List[Line]]] = {}
    for c, path in disc.paths():
        low = path.lower()
        if low.startswith("movie\\en\\") and low.endswith(".txt"):
            stem = path.rsplit("\\", 1)[1][:-4]
            if only and not any(o.lower() in stem.lower() for o in only):
                continue
            hit = disc.find(path)
            cues = parse_subtitles(disc.read(*hit))
            (out / "subtitles" / f"{stem}.srt").write_text(srt(cues), encoding="utf-8")
            lines = [Line(source="movie", scene=stem, order=n, speaker="", text=clean(t),
                          native="movie\\en", start=a, end=b, raw=t,
                          voice=f"movie\\{stem}.sfd")
                     for n, (a, b, t) in enumerate(cues)]
            mv = disc.find(f"movie\\{stem}.sfd")
            movies[stem] = (mv[0], mv[1], lines) if mv else (None, None, lines)
            all_lines += lines

    checks: List[dict] = []
    durations: Dict[str, float] = {}
    if voice:
        from toolfind import find_tool
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            print("voice: ffmpeg not found; skipped", file=sys.stderr)
        else:
            with tempfile.TemporaryDirectory(dir=out) as tmp:
                wav = Path(tmp) / "track.wav"
                for stem, (c, e, lines) in sorted(movies.items()):
                    if e is None or not lines:
                        continue
                    _decode(ffmpeg, "mpeg", disc.stream(c, e), wav)
                    pcm, ch, rate = _read_wav(wav)
                    durations[stem] = len(pcm) / (ch * 2 * rate)
                    carve(pcm, ch, rate, lines, out / "voice" / stem, checks, stem)
                tracks: Dict[str, List[Line]] = {}
                for stem, ev in events.items():
                    for l in ev:
                        if l.voice:
                            tracks.setdefault(l.voice, []).append(l)
                for track, ev in sorted(tracks.items()):
                    rel = "sound\\adx\\" + track.replace("/", "\\") + ".adx"
                    hit = disc.find(rel)
                    if hit is None:
                        checks.append({"track": track, "n": 0, "clip": "", "note": f"{rel} not on these discs"})
                        continue
                    _decode(ffmpeg, "adx", [disc.read(*hit)], wav)
                    pcm, ch, rate = _read_wav(wav)
                    name = track.rsplit("/", 1)[-1]
                    durations[name] = total = len(pcm) / (ch * 2 * rate)
                    if max(l.end for l in ev) > total + 0.1:
                        # the lines run past the track: the scene's frames start elsewhere
                        # and it sets no sound keyframe; find the track's start frame from
                        # where its speech is (sc8074)
                        off, best, second = align_offset(pcm, ch, rate, ev)
                        if off is not None and best >= 6 and best - second >= 3:
                            for l in ev:
                                l.start -= off / FPS
                                l.end -= off / FPS
                                l.offset += off
                            note = (f"lines ran past the track; aligned by speech energy: track starts at "
                                    f"event frame {ev[0].offset} (contrast {best:.1f} dB, next best {second:.1f} dB)")
                        else:
                            note = "lines run past the track and no clear alignment was found"
                        checks.append({"track": name, "n": 0, "clip": "", "note": note})
                    carve(pcm, ch, rate, ev, out / "voice" / name, checks, name)
            with open(out / "voice_check.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["track", "n", "clip", "start", "end", "seconds",
                                                  "rms_db", "onset_db", "pre_db", "note"])
                w.writeheader()
                for r in checks:
                    w.writerow(r)

    for stem, head, lines in written:
        (out / "scripts" / f"{stem}.txt").write_text(transcript(stem, head, lines), encoding="utf-8")
    for stem, ev in events.items():
        (out / "subtitles" / f"{stem}.srt").write_text(
            srt((max(0.0, l.start), l.end, (l.speaker + ": " if l.speaker else "") + l.text) for l in ev),
            encoding="utf-8")

    with open(out / "dialogue.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for ln in all_lines:
            w.writerow(ln.row())
    kinds: Dict[str, int] = {}
    for ln in all_lines:
        kinds[ln.native] = kinds.get(ln.native, 0) + 1
    clips = sum(1 for r in checks if r.get("clip"))
    print(f"dialogue: {n_scripts} scripts, {len(set(l.scene for l in all_lines if l.source != 'movie'))} "
          f"with lines, {len(movies)} movies; {len(all_lines)} rows "
          + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
          + (f"; {clips} voice clips" if voice else ""), file=sys.stderr)
    return 0


# --------------------------------------------------------------- self-test

def _assemble(words: List[int]) -> bytes:
    return struct.pack(f"<{len(words)}H", *words)


def _op(op: int, operand: Optional[int] = None) -> List[int]:
    return [op] if operand is None else [op, operand & 0xFFFF, (operand >> 16) & 0xFFFF]


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    # subtitles: times, clears, codes, ruby, relative frames, end
    sub = ("; comment\n00:00:00\t$ay390;$xx0,22;\n00:01:15\tHello [世界/せかい]\\nthere\n"
           "00:03:00\t\n+30\tAgain\n150\t$zoom13;Five\nend\n").encode("euc_jp")
    cues = parse_subtitles(sub)
    check([round(a, 2) for a, _, _ in cues] == [1.5, 4.0, 5.0], "subtitle times (mm:ss:ff, +N, frames)")
    check(round(cues[0][1], 2) == 3.0 and cues[0][2] == "Hello 世界\\nthere", "an empty cue ends the line; ruby kept as its base")
    check(cues[2][2] == "Five", "$ codes dropped")
    s = srt(cues[:1])
    check("00:00:01,500 --> 00:00:03,000" in s and "Hello 世界\nthere" in s, "SRT timing and line break")

    # a tiny script: entry -> msgDtl, then if (flag) { smsg } else { choice }, an event track + timed line
    imports = [("talk", "msgDtl"), ("talk", "smsg"), ("talk", "menuAddItem"), ("cfe", "setMes"),
               ("cfe", "stKySnd")]
    strings = [b"[Canaan]What is this thing?", b"[chaos]A save point.", b"Yes\\nNo",
               b"", b"cfevent/BE6302/BE6302", b"Long time no see!"]
    code: List[int] = []

    def emit(*ops):
        for o in ops:
            code.extend(o)

    T = lambda k: (0x20 << 16) | k                      # noqa: E731
    emit(_op(0x04, 4), _op(0x28), _op(0x06, T(0)), _op(0x2A), _op(0x04, 2), _op(0x15, 0), _op(0x19))
    emit(_op(0x08, 0))                                   # ldg g0
    jz_at = len(code)
    emit(_op(0x0F, 0))                                   # jz else (patched)
    emit(_op(0x06, T(1)), _op(0x2A), _op(0x28), _op(0x15, 1), _op(0x19))
    jmp_at = len(code)
    emit(_op(0x0E, 0))                                   # jmp end (patched)
    else_w = len(code)
    emit(_op(0x26), _op(0x28), _op(0x06, T(2)), _op(0x2A), _op(0x29), _op(0x15, 2), _op(0x19))
    end_w = len(code)
    # the event's first sound keyframe: stKySnd(0, 0, 30, 1, 0) -> the voice track starts at frame 30
    emit(_op(0x26), _op(0x28), _op(0x26), _op(0x28), _op(0x04, 30), _op(0x28), _op(0x28), _op(0x28),
         _op(0x26), _op(0x28), _op(0x2C), _op(0x15, 4), _op(0x19))
    # event: setMes('', 0, 1, track, 0) then setMes(text, 90, 195, '', 0)
    emit(_op(0x06, T(3)), _op(0x2A), _op(0x26), _op(0x28), _op(0x28), _op(0x28),
         _op(0x06, T(4)), _op(0x2A), _op(0x26), _op(0x28), _op(0x2C), _op(0x15, 3), _op(0x19))
    emit(_op(0x06, T(5)), _op(0x2A), _op(0x04, 90), _op(0x28), _op(0x04, 195), _op(0x28),
         _op(0x06, T(3)), _op(0x2A), _op(0x26), _op(0x28), _op(0x2C), _op(0x15, 3), _op(0x19))
    emit(_op(0x13))
    code[jz_at + 1:jz_at + 3] = [else_w & 0xFFFF, else_w >> 16]
    code[jmp_at + 1:jmp_at + 3] = [end_w & 0xFFFF, end_w >> 16]
    script = sb.Script(_assemble(code), [("goteinei", 0)], imports, b"\0" * 4, "test\\sc0000")
    lines = script_lines(script, strings, "sc0000")
    got = [(l.native, l.speaker, l.text, l.sbl_id) for l in lines]
    check(got[0] == ("talk.msgDtl", "Canaan", "What is this thing?", 0), "box line, speaker split, .sbl id")
    check(got[1] == ("talk.smsg", "chaos", "A save point.", 1) and lines[1].branch.startswith("if@"),
          "message inside the if-branch")
    check(got[2] == ("talk.menuAddItem", "CHOICE", "Yes | No", 2) and lines[2].branch.startswith("else@"),
          "choice inside the else-branch")
    ev = [l for l in lines if l.source == "event"]
    check(len(ev) == 1 and ev[0].voice == "cfevent/BE6302/BE6302" and (ev[0].start, ev[0].end) == (2.0, 5.5)
          and ev[0].frames == "90-195", "event line: voice track from the empty call, 30 fps, from the first sound keyframe")
    tx = transcript("sc0000", "", lines)
    check("if:" in tx and "else:" in tx and "CHOICE: Yes | No" in tx and "[00:02.00-00:05.50]" in tx,
          "transcript shows the branches, the choice and the event timing")

    # audio slicing and loudness
    rate, ch = 1000, 2
    pcm = b"".join(struct.pack("<hh", 0, 0) for _ in range(1000)) + \
        b"".join(struct.pack("<hh", 8000, -8000) for _ in range(1000))
    cut = _slice(pcm, ch, rate, 1.0, 1.5)
    check(len(cut) == 500 * 4 and _rms_db(cut) > -13 and _rms_db(_slice(pcm, ch, rate, 0, 0.5)) < -100,
          "clip slicing and RMS")
    check(clean("$col7AF9FB;$zoom17;There's\\nnothing!$colc0c0c0;") == "There's nothing!", "battle codes cleaned")

    # alignment: speech at 2-4, 8-10, 14-15 s; the lines say 2 s later -> 60 frames
    rate = 3000
    seed = 12345
    frames = []
    for i in range(20 * rate):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        t = i / rate
        amp = 8000 if (2 <= t < 4 or 8 <= t < 10 or 14 <= t < 15) else 40
        v = (seed % (2 * amp + 1)) - amp
        frames.append(struct.pack("<hh", v, v))
    pcm = b"".join(frames)
    lines = [Line("event", "t", k, start=a, end=b) for k, (a, b) in enumerate([(4, 6), (10, 12), (16, 17)])]
    off, best, second = align_offset(pcm, 2, rate, lines)
    check(off is not None and abs(off - 60) <= 1 and best - second > 3, f"speech-energy alignment finds the offset ({off} frames)")
    print("self-test passed" if ok else "SELF-TEST FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", action="append", help="a disc image (give both discs for every movie)")
    ap.add_argument("--out", help="output directory")
    ap.add_argument("--voice", action="store_true", help="also cut one WAV per timed line (needs ffmpeg)")
    ap.add_argument("--only", action="append", help="only scenes/movies whose name contains this")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.iso or not a.out:
        ap.error("--iso and --out are required")
    return run(a.iso, Path(a.out), voice=a.voice, only=a.only)


if __name__ == "__main__":
    sys.exit(main())
