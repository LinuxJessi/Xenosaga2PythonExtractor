"""langpack.py — translate Episode II (USA) into scripts its font never had: shape,
order, wrap and encode text for the game's print function.

Borrowed from Xenosaga1PythonExtractor/langpack.py (2026-10-14): the shaping
(``HBShaper``, and ``SimpleShaper`` with its Arabic presentation-form table for
hand-drawn PNG cells), the bidi run ordering of that encoder, the greedy wrap at
spaces, mirrored brackets, Latin and digit runs kept forward, an all-Latin
paragraph kept left-to-right, and the disc's own Japanese left alone. What is
Episode II's: the glyph codes (``glyphs.py``: single bytes, A4 pairs, AD pairs,
read from the USA executable), the widths (``font.Measurer``, the port of the
game's width function, on the REBUILT font), the text tree (``textpack``'s
text-export tree), the control tokens, the state codes re-emitted for
right-to-left lines, several scripts in one project, and the byte budgets.

The engine draws left to right, one cell per code, with no joining, reordering
or bidi. All of that happens here, at build time; the game only ever sees glyph
codes, written into the tree as ``⟦XX⟧`` escapes, which text-import never checks
against the font. Pipeline (docs/ALPHABETS.md)::

    text-export DISC1 -> TREE                 translate TREE (logical-order UTF-8)
    lang-scan  --iso DISC1 --text TREE ...    -> font0.us font1.us glyphset.json langpack.json
    lang-encode --text TREE --glyphset G      -> TREE-ENC (same tree, glyphs as escapes)
    patch --set chain0:font0.us=... --set chain0:font1.us=...   (into a copy)
    text-import --iso <that copy> --text TREE-ENC --out OUT.iso

What the encoder knows about the text (read from SLUS_208.92 unless marked):

* ``$name...;`` codes have no width. ``$col``, ``$zoom``, ``$shadow``, ``$bg``, ``$alpha``
  and ``$xxN`` set a state that lasts; on a right-to-left line those codes are
  re-emitted in front of each word that needs them, so every word is drawn with
  the state in effect at its logical position, and the logical end state is
  restored after the line.
* A leading ``[Speaker]`` is cut off bytewise by the field window
  (``FUN_00282380``/``FUN_00282338``), so it stays first.
* The two characters ``\\n`` and a real 0x0A are line breaks (``FUN_002870b0``);
  in executable strings (``.exe.txt``) a break is ``⟦0A⟧``.
* ``%d`` and the other ``%`` tokens pass through at their logical place;
  ``&NN`` controller icons are measured as 19 px (an ASSUMPTION, not read).
* The field window wraps a line by itself (``FUN_002865f0`` asks
  ``FUN_00196118`` how many bytes fit and breaks at spaces), so a right-to-left
  bank line has to be pre-wrapped narrower than the window, whose width has
  NOT been read: give ``--width-kind bank=PX`` (a warning says when an RTL
  paragraph is encoded without one). Subtitles (``FUN_001e7a08``) and cfe
  event lines (``FUN_0024b450``) are centred per line and never wrapped by
  the engine; the encoder wraps subtitles at 440 px by default (the widest
  retail line is 454 px on Disc 1, 445 on Disc 2) and never pads them.
* The space is A1A1 (cell 0, pair (0, 19)): its advance is ``$xx1`` + 1, 9 px in
  the subtitle and cfe states (``$xx1,8``, 0x6883F0 and 0x68CD88). The field
  window's ``$xx1`` has not been read; ``--space-px 9`` is assumed for it.

Tree files, by suffix: ``.sbl.txt`` and ``.txd.txt`` block bodies (bank, menu);
``batdat.us.txt`` block bodies, each within its ``max N bytes`` and with the
first byte of a section's first string kept 20-7E or A1-FE; ``.sub.txt`` only
the text after ``time<TAB>`` on cue lines (the frame-0 cue untouched);
``.exe.txt`` block bodies within ``max N bytes``; ``.sjis.txt`` copied (quest
text cannot take new glyphs); everything else copied byte for byte. A body
with no character of a scanned script is passed through unchanged, so an
untouched tree encodes to itself. If any glyph has no slot or any block breaks
its byte budget, nothing is written.

Usage:
    python langpack.py scan    --iso DISC1.iso --text TREE --font F.ttf --script Arab --rtl --out FONTS
    python langpack.py encode  --text TREE --glyphset FONTS/glyphset.json --out TREE-ENC [--width-kind bank=360]
    python langpack.py preview --glyphset FONTS/glyphset.json --text "..." --out line.png
    python langpack.py --self-test
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import font as fontmod                                                    # noqa: E402
import textcodec                                                          # noqa: E402
from font import Font, FontError                                          # noqa: E402
from glyphs import (FIXED, PAIR, PREFER, SINGLE, TIERS, Glyph, GlyphError, GlyphSet,   # noqa: E402
                    Tiers, battle_first_ok, from_png, visible_retail)

MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "{": "}", "}": "{", "<": ">", ">": "<",
          "\u00ab": "\u00bb", "\u00bb": "\u00ab"}         # « » are Bidi_Mirrored too (Arabic quotation marks)
DEFAULT_SPACE_PX = 9            # $xx1,8 + 1 in the subtitle and cfe states; assumed for the window
ICON_PX = 19                    # &NN: an assumption (a fixed-width cell)
SUBTITLE_WIDTH = 440
KINDS = ("bank", "menu", "subtitle", "battle", "exe")
LIMITED_KINDS = ("battle", "exe", "subtitle")
LANGPACK = "langpack.json"
STATE_CODES = ("col", "zoom", "shadow", "bg", "alpha", "xx0", "xx1", "xx2", "xx3")

_CODE = re.compile(r"\$[A-Za-z][^;$\n⟦]{0,40};")
_STATE = re.compile(r"\$(col|zoom|shadow|bg|alpha|xx[0-9])")
_BREAK = re.compile(r"\\n|\n|⟦0[Aa]⟧")
_FMT = re.compile(r"%[A-Za-z]")
_ICON = re.compile(r"&[0-9]{2}")
# '<○>' (also <△> <□> <×>): the field window draws the button picture and neither bracket.
# Seen in PCSX2 on the LANGTEST disc (2026-11-20): an RTL line holding '<○>' came out one
# '<' + '>' narrower than its padded neighbour, and the retail sc1010 #0006 line measures
# as drawn only without the brackets. So the tag is one unit as wide as its symbol.
_BUTTON = re.compile(r"<[○△□×]>")
_ESC = re.compile(r"⟦[0-9A-Fa-f]{2}⟧")
_MAX = re.compile(r"\bmax (\d+) bytes\b")


class LangError(ValueError):
    pass


# --- scripts ---------------------------------------------------------------------

_SCRIPT_RANGES = sorted([
    (0x0600, 0x06FF, "Arab"), (0x0750, 0x077F, "Arab"), (0x08A0, 0x08FF, "Arab"),
    (0xFB50, 0xFDFF, "Arab"), (0xFE70, 0xFEFF, "Arab"),
    (0x0590, 0x05FF, "Hebr"), (0xFB1D, 0xFB4F, "Hebr"),
    (0x0700, 0x074F, "Syrc"), (0x0780, 0x07BF, "Thaa"),
    (0x0900, 0x097F, "Deva"), (0xA8E0, 0xA8FF, "Deva"), (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"), (0x0A80, 0x0AFF, "Gujr"), (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"), (0x0C80, 0x0CFF, "Knda"), (0x0D00, 0x0D7F, "Mlym"),
    (0x0D80, 0x0DFF, "Sinh"), (0x0E00, 0x0E7F, "Thai"),
    (0x1200, 0x139F, "Ethi"), (0x2D80, 0x2DDF, "Ethi"), (0xAB00, 0xAB2F, "Ethi"),
    (0x10A0, 0x10FF, "Geor"), (0x1C90, 0x1CBF, "Geor"), (0x2D00, 0x2D2F, "Geor"),
    (0x0530, 0x058F, "Armn"), (0xFB13, 0xFB17, "Armn"),
    (0x0400, 0x052F, "Cyrl"), (0x1C80, 0x1C8F, "Cyrl"), (0x2DE0, 0x2DFF, "Cyrl"), (0xA640, 0xA69F, "Cyrl"),
    (0x0370, 0x03FF, "Grek"), (0x1F00, 0x1FFF, "Grek"),
])
RTL_SCRIPTS = {"Arab", "Hebr", "Syrc", "Thaa"}


def script_of(ch: str) -> Optional[str]:
    o = ord(ch)
    for lo, hi, tag in _SCRIPT_RANGES:
        if lo <= o <= hi:
            return tag
        if o < lo:
            break
    return None


def _joiner(ch: str) -> bool:
    """Characters that belong to the run before them: combining marks, ZWJ, ZWNJ."""
    return unicodedata.category(ch) in ("Mn", "Mc", "Me") or ch in "\u200c\u200d"


def _is_native(s: str) -> bool:
    """The disc's own Japanese (kana, kanji, full-width forms): left alone."""
    return any(0x3000 <= ord(c) <= 0x30FF or 0x3400 <= ord(c) <= 0x9FFF
               or 0xFF00 <= ord(c) <= 0xFFEF or 0x2500 <= ord(c) <= 0x25FF for c in s)


_US = textcodec.UsCodec()


def _drawable(ch: str) -> bool:
    """Can the USA build draw this character as it is (ASCII, Latin-1, the retail symbol pairs)?"""
    if ord(ch) < 0x80:
        return True
    try:
        _US.encode_char(ch)
        return True
    except textcodec.TextError:
        return False


def _direction(word: str) -> str:
    """R (right-to-left strong), L (left-to-right strong), E (numbers), N."""
    classes = {unicodedata.bidirectional(c) for c in word}
    if classes & {"R", "AL"}:
        return "R"
    if "L" in classes:
        return "L"
    if classes & {"EN", "AN"}:
        return "E"
    return "N"


# --- Arabic presentation forms (the PNG-cell shaper) --------------------------------

# letter -> (isolated, final, initial, medial); two entries = right-joining only
_ARABIC = {
    0x0621: (0xFE80,), 0x0622: (0xFE81, 0xFE82), 0x0623: (0xFE83, 0xFE84),
    0x0624: (0xFE85, 0xFE86), 0x0625: (0xFE87, 0xFE88),
    0x0626: (0xFE89, 0xFE8A, 0xFE8B, 0xFE8C), 0x0627: (0xFE8D, 0xFE8E),
    0x0628: (0xFE8F, 0xFE90, 0xFE91, 0xFE92), 0x0629: (0xFE93, 0xFE94),
    0x062A: (0xFE95, 0xFE96, 0xFE97, 0xFE98), 0x062B: (0xFE99, 0xFE9A, 0xFE9B, 0xFE9C),
    0x062C: (0xFE9D, 0xFE9E, 0xFE9F, 0xFEA0), 0x062D: (0xFEA1, 0xFEA2, 0xFEA3, 0xFEA4),
    0x062E: (0xFEA5, 0xFEA6, 0xFEA7, 0xFEA8), 0x062F: (0xFEA9, 0xFEAA),
    0x0630: (0xFEAB, 0xFEAC), 0x0631: (0xFEAD, 0xFEAE), 0x0632: (0xFEAF, 0xFEB0),
    0x0633: (0xFEB1, 0xFEB2, 0xFEB3, 0xFEB4), 0x0634: (0xFEB5, 0xFEB6, 0xFEB7, 0xFEB8),
    0x0635: (0xFEB9, 0xFEBA, 0xFEBB, 0xFEBC), 0x0636: (0xFEBD, 0xFEBE, 0xFEBF, 0xFEC0),
    0x0637: (0xFEC1, 0xFEC2, 0xFEC3, 0xFEC4), 0x0638: (0xFEC5, 0xFEC6, 0xFEC7, 0xFEC8),
    0x0639: (0xFEC9, 0xFECA, 0xFECB, 0xFECC), 0x063A: (0xFECD, 0xFECE, 0xFECF, 0xFED0),
    0x0640: (0x0640, 0x0640, 0x0640, 0x0640),
    0x0641: (0xFED1, 0xFED2, 0xFED3, 0xFED4), 0x0642: (0xFED5, 0xFED6, 0xFED7, 0xFED8),
    0x0643: (0xFED9, 0xFEDA, 0xFEDB, 0xFEDC), 0x0644: (0xFEDD, 0xFEDE, 0xFEDF, 0xFEE0),
    0x0645: (0xFEE1, 0xFEE2, 0xFEE3, 0xFEE4), 0x0646: (0xFEE5, 0xFEE6, 0xFEE7, 0xFEE8),
    0x0647: (0xFEE9, 0xFEEA, 0xFEEB, 0xFEEC), 0x0648: (0xFEED, 0xFEEE),
    0x0649: (0xFEEF, 0xFEF0), 0x064A: (0xFEF1, 0xFEF2, 0xFEF3, 0xFEF4),
}
_LAM_ALEF = {0x0622: (0xFEF5, 0xFEF6), 0x0623: (0xFEF7, 0xFEF8),
             0x0625: (0xFEF9, 0xFEFA), 0x0627: (0xFEFB, 0xFEFC)}


def arabic_forms(word: str) -> List[str]:
    """Contextual presentation forms for one word, logical order; marks are dropped."""
    letters = [c for c in word if not (0x064B <= ord(c) <= 0x0652 or ord(c) == 0x0670)]
    out: List[str] = []
    i = 0
    prev_joins = False
    while i < len(letters):
        c = letters[i]
        o = ord(c)
        forms = _ARABIC.get(o)
        if forms is None:
            out.append(c)
            prev_joins = False
            i += 1
            continue
        nxt = ord(letters[i + 1]) if i + 1 < len(letters) else None
        if o == 0x0644 and nxt in _LAM_ALEF:
            iso, fin = _LAM_ALEF[nxt]
            out.append(chr(fin if prev_joins else iso))
            prev_joins = False
            i += 2
            continue
        dual = len(forms) == 4
        next_joins = nxt in _ARABIC
        if prev_joins and dual and next_joins:
            f = forms[3]
        elif prev_joins:
            f = forms[1] if len(forms) > 1 else forms[0]
        elif dual and next_joins:
            f = forms[2]
        else:
            f = forms[0]
        out.append(chr(f))
        prev_joins = dual and next_joins
        i += 1
    return out


# --- shapers ---------------------------------------------------------------------

class SimpleShaper:
    """Hand-drawn PNG cells: one key per character (presentation forms for Arabic)."""
    name = "cells"

    def __init__(self):
        self.notdef: set = set()

    def keys(self, word: str, rtl: bool) -> List[str]:
        if any(0x0600 <= ord(c) <= 0x06FF for c in word):
            seq = [f"u:{f}" for f in arabic_forms(word)]
        else:
            seq = [f"u:{c}" for c in word if unicodedata.category(c) != "Mn"]
        return seq[::-1] if rtl else seq      # visual order, like HarfBuzz

    def glyph(self, key: str):
        return None


class HBShaper:
    """HarfBuzz shaping through glyphs.TTFRenderer; keys name glyph ids."""
    name = "harfbuzz"

    def __init__(self, renderer, script: Optional[str], language: Optional[str], max_cluster_px: int = 18):
        self.r = renderer
        self.script = script
        self.language = language
        self.max_cluster = max_cluster_px
        self._parts: Dict[str, list] = {}
        self._adv: Dict[str, int] = {}
        self._cache: Dict[Tuple[str, bool], List[str]] = {}
        self.notdef: set = set()        # words the font had no glyph for

    def keys(self, word: str, rtl: bool) -> List[str]:
        hit = self._cache.get((word, rtl))
        if hit is not None:
            return hit
        r = self.r
        glyphs = r.shape(word, "rtl" if rtl else "ltr", self.script, self.language)
        if any(g[0] == 0 for g in glyphs):
            self.notdef.add(word)
        groups: List[list] = []
        for g in glyphs:
            if groups and groups[-1][0][1] == g[1]:
                groups[-1].append(g)
            else:
                groups.append([g])
        out = []
        for grp in groups:
            if len(grp) == 1:
                out.append(f"g:{r.name}:{grp[0][0]}")
                continue
            pen = 0.0
            parts = []
            for gid, _, adv, xo, yo in grp:
                parts.append((gid, (pen + xo) * r.scale, yo * r.scale))
                pen += adv
            if pen * r.scale <= self.max_cluster:
                key = "c:%s:%s" % (r.name, ";".join(f"{gid}@{dx:.1f},{dy:.1f}" for gid, dx, dy in parts))
                self._parts[key] = parts
                self._adv[key] = round(pen * r.scale)
                out.append(key)
            else:
                out += [f"g:{r.name}:{gid}" for gid, *_ in grp]
        self._cache[(word, rtl)] = out
        return out

    def glyph(self, key: str) -> Glyph:
        kind, _, rest = key.partition(":")
        if kind == "g":
            return self.r.render_gid(int(rest.split(":")[1]))
        if kind == "c":
            parts = self._parts.get(key)
            if parts is None:
                parts = []
                for p in rest.split(":", 1)[1].split(";"):
                    gid, _, off = p.partition("@")
                    dx, dy = off.split(",")
                    parts.append((int(gid), float(dx), float(dy)))
            return self.r.render_cluster(parts, key, self._adv.get(key))
        return self.r.render_text(rest, key)


def make_shaper(cfg: dict):
    if cfg.get("ttf"):
        from glyphs import TTFRenderer
        r = TTFRenderer(cfg["ttf"], cfg.get("px", 18.0), cfg.get("baseline", 19), cfg.get("face", 0))
        return HBShaper(r, cfg.get("script"), cfg.get("language"))
    return SimpleShaper()


# --- the encoder -------------------------------------------------------------------

class Item:
    __slots__ = ("kind", "text", "keys", "dir", "width", "pre", "state", "level", "verbatim")

    def __init__(self, kind, text="", keys=(), d="N", width=0, pre="", state=None, verbatim=False):
        self.kind, self.text, self.keys, self.dir, self.width = kind, text, list(keys), d, width
        self.verbatim = verbatim            # a control token (<○>, &NN): placed like a neutral, never mirrored
        self.pre = pre                      # control text in front of it (state codes too, as written)
        self.state = dict(state or {})      # state codes in effect when it is drawn
        self.level = 0


class Encoder:
    """Encodes one body (a string of the tree) at a time.

    ``configs`` is the list from langpack.json; ``widths`` maps a kind to its wrap
    width (0 = no wrap). With ``collect`` set the encoder only gathers glyph keys
    (scan); ``only`` limits that to one config index."""

    def __init__(self, configs: List[dict], glyphset: GlyphSet, adr: "fontmod.Addressing",
                 metrics: Sequence[Tuple[int, int]], rtl: Optional[bool] = None,
                 widths: Optional[Dict[str, int]] = None, align: str = "left",
                 space_px: int = DEFAULT_SPACE_PX, collect: bool = False, only: Optional[int] = None,
                 shapers: Optional[list] = None):
        self.configs = configs
        self.gs = glyphset
        self.adr = adr
        self.metrics = metrics
        self.rtl = rtl
        self.widths = dict(widths or {})
        self.align = align
        self.space_px = space_px
        self.collect = collect
        self.only = only
        self._shapers = list(shapers) if shapers else [None] * len(configs)
        self.counts: Counter = Counter()
        self.kinds_of: Dict[str, set] = defaultdict(set)
        self.script_uses: Counter = Counter()
        self.missing: Counter = Counter()
        self.unknown: Counter = Counter()
        self.typed: Dict[int, Tuple[int, str, str]] = {}    # cell -> (uses, character, where) of typed Latin-1
        self.provisional: Dict[str, int] = {}
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self._first: Optional[list] = None
        self._emitted_any = False
        self.widest: Dict[str, Tuple[int, str]] = {}
        self.kind = "bank"
        self.where = ""
        self._claims = {c.get("script"): i for i, c in enumerate(configs) if c.get("script")}
        self._catchall = next((i for i, c in enumerate(configs) if not c.get("script")), None)

    # configs and shapers
    def shaper(self, i: int):
        if self._shapers[i] is None:
            self._shapers[i] = make_shaper(self.configs[i])
        return self._shapers[i]

    def char_class(self, ch: str):
        """'a' ASCII letter/digit, 'p' ASCII other, 'n' drawable as it is, an int (the
        config that shapes it), or '?' (no config and not drawable)."""
        o = ord(ch)
        if o < 0x80:
            return "a" if ch.isalnum() else "p"
        tag = script_of(ch)
        if tag in self._claims:
            return self._claims[tag]
        if _is_native(ch) or _drawable(ch):
            return "n"
        if self._catchall is not None:
            return self._catchall
        return "?"

    def targets(self, text: str) -> bool:
        """Does ``text`` hold a character this project shapes (or cannot draw at all)?"""
        for ch in _ESC.sub("", text):
            if ord(ch) >= 0x80:
                cls = self.char_class(ch)
                if not isinstance(cls, str) or cls == "?":
                    return True
        return False

    # measuring
    def lit_width(self, text: str) -> int:
        try:
            data = _US.from_text(text)
            return self.adr.measurer(self.metrics, han=True, prop=True, xx1=self.space_px - 1).width(data)
        except (FontError, textcodec.TextError):
            return 0

    def key_width(self, key: str) -> int:
        if key in self.gs.slots:
            return self.gs.advance(key, self.adr.prop_pad)
        return self.provisional.get(key, 10)

    # tokens -> items
    def _runs(self, units: List[str]) -> List[Tuple[object, str]]:
        out: List[Tuple[object, str]] = []
        for u in units:
            if u.startswith("⟦") and len(u) == 4:
                cls = "e"
            elif _joiner(u) and out and not isinstance(out[-1][0], str):
                cls = out[-1][0]
            else:
                cls = self.char_class(u)
            if out and out[-1][0] == cls:
                out[-1] = (cls, out[-1][1] + u)
            else:
                out.append((cls, u))
        return out

    def _word_items(self, word: str, state: dict, pre: str) -> List[Item]:
        units, i = [], 0
        while i < len(word):
            m = _ESC.match(word, i)
            if m:
                units.append(m.group(0))
                i = m.end()
            else:
                units.append(word[i])
                i += 1
        items = []
        for cls, text in self._runs(units):
            if cls == "e":
                bytes_ = _US.from_text(text)
                d = "L" if any(b >= 0x80 for b in bytes_) else "N"
                it = Item("lit", text, d=d, width=self.lit_width(text))
            elif isinstance(cls, int):
                d = _direction(text)
                if self.only is not None and cls != self.only:
                    it = Item("keys", text, d=d)
                else:
                    keys = self.shaper(cls).keys(text, d == "R")
                    self.script_uses[self.configs[cls].get("script") or "*"] += len(keys)
                    if self.collect:
                        self.counts.update(keys)
                        for k in keys:
                            self.kinds_of[k].add(self.kind)
                    w = 0
                    for k in keys:
                        if not self.collect and k not in self.gs.slots:
                            self.missing[k] += 1
                        w += self.key_width(k)
                    it = Item("keys", text, keys, d, w)
            elif cls == "?":
                for ch in text:
                    self.unknown[ch] += 1
                it = Item("lit", text, d=_direction(text), width=0)
            else:
                it = Item("lit", text, d=_direction(text), width=self.lit_width(text))
            it.pre, it.state = pre, dict(state)
            pre = ""
            items.append(it)
        # UBA W5: a currency or percent sign (ET) next to a number takes the number's direction
        for i, it in enumerate(items):
            if it.kind == "lit" and it.text and all(unicodedata.bidirectional(c) == "ET" for c in it.text):
                if any(0 <= j < len(items) and items[j].dir == "E" for j in (i - 1, i + 1)):
                    it.dir = "E"
        return items

    def items(self, text: str, state: dict) -> Tuple[List[Item], dict]:
        """One paragraph (no line breaks) -> items, and the state after it."""
        state = dict(state)
        items: List[Item] = []
        pre = ""
        i, n = 0, len(text)
        word_start = None

        def flush_word(end):
            nonlocal pre, word_start
            if word_start is not None:
                items.extend(self._word_items(text[word_start:end], state, pre))
                pre = ""
                word_start = None

        while i < n:
            m = _CODE.match(text, i) if text[i] == "$" else None
            if m:
                flush_word(i)
                tok = m.group(0)
                s = _STATE.match(tok)
                if s:
                    state[s.group(1)] = tok
                pre += tok
                i = m.end()
                continue
            m = (_FMT.match(text, i) if text[i] == "%" else None)
            if m:
                flush_word(i)
                pre += m.group(0)
                i = m.end()
                continue
            m = (_BUTTON.match(text, i) if text[i] == "<" else None)
            if m:
                flush_word(i)
                it = Item("lit", m.group(0), d="N", width=self.lit_width(m.group(0)[1]), pre=pre, state=state,
                          verbatim=True)
                pre = ""
                items.append(it)
                i = m.end()
                continue
            m = (_ICON.match(text, i) if text[i] == "&" else None)
            if m:
                flush_word(i)
                # its own kind: an icon code is never reversed or mirrored in a right-to-left line
                it = Item("icon", m.group(0), d="N", width=ICON_PX, pre=pre, state=state)
                pre = ""
                items.append(it)
                i = m.end()
                continue
            if text[i] == " ":
                flush_word(i)
                j = i
                while j < n and text[j] == " ":
                    j += 1
                if pre:
                    items.append(Item("ctrl", pre=pre, state=state))
                    pre = ""
                items.append(Item("space", text[i:j], d="N", width=self.space_px * (j - i), state=state))
                i = j
                continue
            if word_start is None:
                word_start = i
            i += 1
        flush_word(n)
        if pre:
            items.append(Item("ctrl", pre=pre, state=state))
        return items, state

    # wrapping
    def _wrap(self, items: List[Item], width: int) -> List[List[Item]]:
        """Greedy fill; breaks only at spaces, so a word and the punctuation or codes
        glued to it travel together. The spaces at a break are dropped."""
        groups, grp = [], []
        for it in items:
            if it.kind == "space":
                if grp:
                    groups.append(grp)
                groups.append([it])
                grp = []
            else:
                grp.append(it)
        if grp:
            groups.append(grp)
        lines, cur, cur_w = [], [], 0
        for g in groups:
            w = sum(it.width for it in g)
            if g[0].kind == "space":
                if cur:
                    cur += g
                    cur_w += w
                continue
            if cur and cur_w + w > width:
                while cur and cur[-1].kind == "space":
                    cur.pop()
                lines.append(cur)
                cur, cur_w = [], 0
            cur += g
            cur_w += w
        while cur and cur[-1].kind == "space":
            cur.pop()
        if cur or not lines:
            lines.append(cur)
        return lines

    # emitting
    def _order(self, items: List[Item], base_rtl: bool) -> List[int]:
        if not base_rtl:                 # a left-to-right paragraph (or --ltr): logical order
            for it in items:
                it.level = 0
            return list(range(len(items)))
        base = 1
        levels: List[Optional[int]] = []
        for it in items:
            if it.dir == "R":
                levels.append(1)
            elif it.dir in ("L", "E"):
                levels.append(2 if base_rtl else 0)
            else:
                levels.append(None)
        for i, lv in enumerate(levels):
            if lv is None:
                prev = next((levels[j] for j in range(i - 1, -1, -1) if levels[j] is not None), base)
                nxt = next((levels[j] for j in range(i + 1, len(levels)) if levels[j] is not None), base)
                levels[i] = prev if prev == nxt else base
        for it, lv in zip(items, levels):
            it.level = lv
        order = list(range(len(items)))
        for lv in (2, 1):               # UBA L2: reverse runs at each level from the highest down to 1
            i = 0
            while i < len(order):
                if levels[order[i]] >= lv:
                    j = i
                    while j < len(order) and levels[order[j]] >= lv:
                        j += 1
                    order[i:j] = order[i:j][::-1]
                    i = j
                else:
                    i += 1
        return order

    def _emit(self, items: List[Item], base_rtl: bool, start: dict, end: dict, pad_to: int = 0
              ) -> List[Tuple[str, str]]:
        """-> atoms ('t', text) / ('k', key) in visual order."""
        order = self._order(items, base_rtl)
        atoms: List[Tuple[str, str]] = []
        if order == list(range(len(items))) and not base_rtl:
            for it in items:
                atoms.append(("t", it.pre))
                atoms += self._render(it, False)
            return [a for a in atoms if a[0] == "k" or a[1]]
        cur = dict(start)
        if items:                        # codes in front of the first logical word stay first
            atoms.append(("t", _strip_state(items[0].pre)))
        if pad_to:
            line_w = sum(it.width for it in items)
            if line_w < pad_to:
                atoms.append(("t", " " * ((pad_to - line_w) // self.space_px)))
        for idx in order:
            it = items[idx]
            if it.kind != "space":          # a space is drawn the same in any state
                for k, v in it.state.items():
                    if cur.get(k) != v:
                        atoms.append(("t", v))
                        cur[k] = v
                for k in cur:
                    if k not in it.state and it.kind in ("lit", "keys", "icon"):
                        self.warnings.append(f"{self.where}: the {k} code cannot be undone for the words before "
                                             f"it (their state before the code is not known); it carries onto them")
            if idx != 0:
                atoms.append(("t", _strip_state(it.pre)))
            atoms += self._render(it, base_rtl)
        for k, v in end.items():
            if cur.get(k) != v:
                atoms.append(("t", v))
        return [a for a in atoms if a[0] == "k" or a[1]]

    def _render(self, it: Item, rtl: bool) -> List[Tuple[str, str]]:
        if it.kind == "icon":                                  # &NN: as written, whatever the direction
            return [("t", it.text)]
        if it.kind in ("space", "lit"):
            s = it.text
            if rtl and it.kind == "lit" and it.level == 1 and not it.verbatim:   # neutral ASCII inside RTL
                s = "".join(MIRROR.get(c, c) for c in s)
                if it.dir == "N" and not _ESC.search(s):
                    s = s[::-1]
            return [("t", s)]
        if it.kind == "keys":
            if not it.keys:                                    # another config's run (scan --script)
                return [("t", it.text)]
            return [("k", k) for k in it.keys]
        return []

    def _atoms_text(self, atoms) -> str:
        out = []
        for kind, v in atoms:
            if kind == "t":
                out.append(v)
            elif v in self.gs.slots:
                out.append("".join(f"⟦{b:02X}⟧" for b in self.gs.code_bytes(v)))
            else:
                out.append("⟦A1⟧⟦A1⟧")                           # no slot: encode refuses anyway
        return "".join(out)

    def _check_atoms(self, atoms) -> Optional[str]:
        """Every glyph code is read by the game where it was written (no lone lead byte
        before it takes its first byte as a trail)."""
        data, spans = bytearray(), []
        for kind, v in atoms:
            if kind == "t":
                try:
                    data += _US.from_text(v)
                except textcodec.TextError:
                    return None                                   # text-import reports it
            elif v in self.gs.slots:
                b = self.gs.code_bytes(v)
                spans.append((len(data), len(b), v))
                data += b
        data = bytes(data)
        starts, i = {}, 0
        while i < len(data):
            n = _US.read(data, i) if data[i] >= 0x80 else 1
            starts[i] = n
            i += n
        for a, n, key in spans:
            if starts.get(a) != n:
                return (f"{self.where}: the glyph {key} would not be read as written (a lone lead byte "
                        f"such as ¡ ¢ ¤ ¦ or the soft hyphen stands in front of it)")
        return None

    def paragraph(self, text: str, state: dict, width: int, rtl_ok: bool, pad: bool) -> Tuple[List[str], dict]:
        items, end_state = self.items(text, state)
        has_r = any(it.dir == "R" for it in items)
        base_rtl = has_r and rtl_ok
        if base_rtl and not width and self.kind == "bank" and not self.collect:
            self.warnings.append(f"{self.where}: a right-to-left paragraph encoded with no --width; the field window "
                                 f"re-wraps lines that are too wide from the left (give --width-kind bank=PX)")
        lines = self._wrap(items, width) if width else [items]
        out, cur = [], dict(state)
        for line in lines:
            end = dict(line[-1].state) if line else dict(cur)
            atoms = self._emit(line, base_rtl, cur, end, width if pad and base_rtl and width else 0)
            if self.collect:
                if not self._emitted_any:
                    self._first = atoms[:1]
            else:
                why = self._check_atoms(atoms)
                if why:
                    self.warnings.append(why)
                    self.errors.append(why)
            text_out = self._atoms_text(atoms)
            w = sum(it.width for it in line)
            if w > self.widest.get(self.kind, (-1, ""))[0]:
                self.widest[self.kind] = (w, f"{self.where}: {text[:50]}")
            out.append(text_out)
            self._emitted_any = self._emitted_any or bool(text_out)
            cur = end
        return out, end_state

    def string(self, body: str, kind: str = "bank", where: str = "") -> str:
        """A whole body (one string of the tree, ``\\n`` as two characters) -> encoded body.
        A body with no character this project shapes comes back unchanged."""
        self.kind, self.where = kind, where
        self._first = None
        self._emitted_any = False
        target = self.targets(body)
        self._note_typed(body, target)
        if not target:
            return body
        width = self.widths.get(kind, 0)
        rtl_ok = self.rtl is not False
        pad = self.align == "right" and kind in ("bank", "menu")
        brk = "⟦0A⟧" if kind == "exe" else "\\n"
        head = ""
        if body.startswith("[") and "]" in body:
            j = body.index("]")
            inner = body[1:j]
            if self.targets(inner):
                lines, _ = self.paragraph(inner, {}, 0, rtl_ok, False)
                inner = "".join(lines)
            head, body = "[" + inner + "]", body[j + 1:]
            self._emitted_any = True
        parts = _BREAK.split(body)
        seps = _BREAK.findall(body)
        state: dict = {}
        out = []
        for p in parts:
            if not p.strip(" ") or not self.targets(p):
                out.append(p)
                if p:
                    state = self.items(p, state)[1]
                    self._emitted_any = True
                continue
            lead = p[:len(p) - len(p.lstrip(" "))]
            tail = p[len(p.rstrip(" ")):]
            core = p.strip(" ")
            self._emitted_any = self._emitted_any or bool(lead)
            lines, state = self.paragraph(core, state, width, rtl_ok, pad)
            out.append(lead + brk.join(lines) + tail)
            self._emitted_any = True
        res = out[0] + "".join(s + t for s, t in zip(seps, out[1:]))
        res = head + res
        if res.startswith("#"):
            res = "⟦23⟧" + res[1:]
        return res

    def _note_typed(self, body: str, target: bool) -> None:
        """Record the cell of every non-ASCII character the tree types that the USA
        build draws as it is (Latin-1 such as « » £ é): it is written as its own byte,
        so its cell must not be given to a new glyph. The disc's own Japanese and the
        ⟦XX⟧ escapes are left out. A body the encoder leaves alone is left out too when
        the retail reserve would skip it (glyphs.visible_retail): text-export shows the
        EUC-JP developer lines as Latin-1 (e.g. 'Í½È÷£±'), and the player never sees them."""
        if not target:
            try:
                if not visible_retail(_US.from_text(body)):
                    return
            except textcodec.TextError:
                pass
        for ch in _ESC.sub("", body):
            if ord(ch) < 0x80 or _is_native(ch):
                continue
            if self.char_class(ch) != "n":
                continue
            for c in {ch, MIRROR.get(ch, ch)}:            # a mirrored « is written as »
                try:
                    data = _US.encode_char(c)
                    cell = self.adr.glyph(data + b"\x00", 0, han=True).cell
                except (textcodec.TextError, FontError):
                    continue
                if cell is None:
                    continue
                n, c0, w0 = self.typed.get(cell, (0, c, self.where))
                self.typed[cell] = (n + 1, c0, w0)

    def typed_conflicts(self) -> List[str]:
        """Typed characters whose cell holds a glyph of the glyphset: the game would draw
        the new glyph in their place."""
        by_cell = {s["cell"]: k for k, s in self.gs.slots.items()}
        out = []
        for cell, (n, ch, where) in sorted(self.typed.items()):
            if cell in by_cell:
                out.append(f"{ch!r} (U+{ord(ch):04X}, typed {n} time(s), e.g. {where}) is written as byte(s) "
                           f"{_US.encode_char(ch).hex().upper()}, whose cell {cell} now holds the glyph "
                           f"{by_cell[cell]}; run lang-scan again on this tree (it reserves typed characters) "
                           f"or replace the character")
        return out

    def first_key(self) -> Optional[str]:
        """After ``string`` in collect mode: the glyph key drawn first, if the encoded
        string would start with a glyph code."""
        f = self._first or []
        return f[0][1] if f and f[0][0] == "k" else None


def _strip_state(pre: str) -> str:
    return "".join(t for t in re.findall(r"\$[A-Za-z][^;$\n⟦]{0,40};|[^$]+|\$", pre) if not _STATE.match(t))


# --- trees -----------------------------------------------------------------------

def tree_kind(path: Path) -> Optional[str]:
    n = path.name.lower()
    if n.endswith(".sbl.txt"):
        return "bank"
    if n.endswith(".txd.txt"):
        return "menu"
    if n.endswith("batdat.us.txt"):
        return "battle"
    if n.endswith(".sub.txt"):
        return "subtitle"
    if n.endswith(".exe.txt"):
        return "exe"
    if n.endswith(".sjis.txt"):
        return "sjis"
    return None


def _read_text(raw: bytes) -> Tuple[str, bool]:
    bom = raw.startswith(b"\xef\xbb\xbf")
    return raw[3:].decode("utf-8") if bom else raw.decode("utf-8"), bom


class Block:
    def __init__(self, header: str, body: str, index: int):
        self.header, self.body, self.index = header, body, index


def transform_blocks(text: str, fn: Callable[[Block], str], marker: str = "### ") -> str:
    """A block file with each body passed through ``fn``; a body ``fn`` returns
    unchanged leaves its lines exactly as they were (textpack.parse_blocks reading)."""
    lines = text.split("\n")
    blocks: List[Tuple[str, List[int]]] = []
    for n, line in enumerate(lines):
        bare = line.rstrip("\r")
        if bare.startswith(marker):
            blocks.append((bare[len(marker):].strip(), []))
        elif bare.startswith("#"):
            continue
        elif blocks:
            blocks[-1][1].append(n)
    drop = set()
    for bi, (head, idx) in enumerate(blocks):
        while len(idx) > 1 and lines[idx[-1]].rstrip("\r") == "":
            idx = idx[:-1]
        body = "\\n".join(lines[i].rstrip("\r") for i in idx)
        new = fn(Block(head, body, bi))
        if new == body:
            continue
        if "\n" in new:
            raise LangError(f"### {head}: an encoded body may not hold a real line break")
        if not idx:
            raise LangError(f"### {head}: a block without a body line")
        cr = "\r" if lines[idx[0]].endswith("\r") else ""
        lines[idx[0]] = new + cr
        drop.update(idx[1:])
    return "\n".join(l for n, l in enumerate(lines) if n not in drop)


_ZERO_TIME = re.compile(r"[0:+]*0[0:]*")


def transform_subtitle(text: str, fn: Callable[[str, int], str]) -> str:
    """Only the text after ``time<TAB>`` on cue lines; ';' lines, the frame-0 cue and
    every other line stay as they are."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        bare = line.rstrip("\r")
        cr = line[len(bare):]
        if bare.startswith(";") or "\t" not in bare:
            out.append(line)
            continue
        time, _, rest = bare.partition("\t")
        if not time.strip() or _ZERO_TIME.fullmatch(time.strip()):
            out.append(line)
            continue
        ws = rest[:len(rest) - len(rest.lstrip(" \t"))]
        body = rest[len(ws):]
        new = fn(body, n)
        out.append(line if new == body else f"{time}\t{ws}{new}{cr}")
    return "\n".join(out)


class TreeResult:
    def __init__(self):
        self.files: Dict[str, bytes] = {}       # relative path -> bytes to write
        self.errors: List[str] = []
        self.budgets: List[Tuple[str, int, int, bool]] = []     # where, encoded bytes, max, changed
        self.sub_lines: List[Tuple[str, int, int]] = []         # where, bytes, px
        self.changed = 0


def _battle_firsts(text: str) -> set:
    firsts, seen = set(), set()
    for bi, (head, _) in enumerate(_blocks_heads(text)):
        sec = head.split()[0] if head else ""
        if sec not in seen:
            seen.add(sec)
            firsts.add(bi)
    return firsts


def _blocks_heads(text: str) -> List[Tuple[str, int]]:
    out = []
    for line in text.split("\n"):
        bare = line.rstrip("\r")
        if bare.startswith("### "):
            out.append((bare[4:].strip(), 0))
    return out


def run_tree(roots: Sequence[Path], enc: Encoder, collect_first: Optional[set] = None) -> TreeResult:
    """Encode (or, in collect mode, scan) every file of the trees. Nothing is written here."""
    res = TreeResult()
    seen_rel = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            raise LangError(f"{root} is not a folder")
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if rel in seen_rel:
                res.errors.append(f"{rel} is in two of the --text trees ({seen_rel[rel]} and {root})")
                continue
            seen_rel[rel] = root
            raw = p.read_bytes()
            kind = tree_kind(p)
            if kind is None or kind == "sjis":
                if kind == "sjis":
                    try:
                        text, _ = _read_text(raw)
                        bad = [ch for ch in _ESC.sub("", text) if ord(ch) >= 0x80 and not _sjis_ok(ch)]
                    except UnicodeDecodeError:
                        bad = []
                    if bad:
                        res.errors.append(f"{rel}: {bad[0]!r} (U+{ord(bad[0]):04X}) in a quest file. The quest "
                                          f"files are Shift-JIS and take no new glyphs (left out of this suite)")
                res.files[rel] = raw
                continue
            try:
                text, bom = _read_text(raw)
            except UnicodeDecodeError as e:
                res.errors.append(f"{rel}: not UTF-8 ({e})")
                continue
            new = _encode_file(rel, kind, text, enc, res, collect_first)
            if new == text:
                res.files[rel] = raw
            else:
                res.changed += 1
                res.files[rel] = (b"\xef\xbb\xbf" if bom else b"") + new.encode("utf-8")
    return res


def _sjis_ok(ch: str) -> bool:
    try:
        ch.encode("shift_jis")
        return True
    except UnicodeEncodeError:
        return False


def _encode_file(rel: str, kind: str, text: str, enc: Encoder, res: TreeResult,
                 collect_first: Optional[set]) -> str:
    if kind == "subtitle":
        def sub(body, n):
            where = f"{rel} line {n}"
            new = enc.string(body, "subtitle", where)
            if new != body and not enc.collect:
                for ln in new.split("\\n"):
                    try:
                        data = _US.from_text(ln)
                    except textcodec.TextError as e:
                        res.errors.append(f"{where}: {e}")
                        continue
                    try:
                        px = enc.adr.measurer(enc.metrics, han=True, prop=True, xx1=8).width(data)
                    except FontError:
                        px = -1
                    res.sub_lines.append((where, len(data), px))
            return new
        return transform_subtitle(text, sub)
    firsts = _battle_firsts(text) if kind == "battle" else set()

    def blk(b: Block) -> str:
        where = f"{rel} ### {b.header}"
        new = enc.string(b.body, kind, where)
        if enc.collect:
            if kind == "battle" and b.index in firsts and collect_first is not None:
                k = enc.first_key()
                if k:
                    collect_first.add(k)
            return b.body
        if kind in ("battle", "exe"):
            m = _MAX.search(b.header)
            if not m:
                res.errors.append(f"{where}: no 'max N bytes' in the block header")
                return new
            limit = int(m.group(1))
            try:
                data = _US.from_text(new)
            except textcodec.TextError as e:
                res.errors.append(f"{where}: {e}")
                return new
            res.budgets.append((where, len(data), limit, new != b.body))
            if new != b.body:
                if len(data) > limit:
                    res.errors.append(f"{where}: {len(data)} bytes encoded, the block holds {limit}")
                if b"\x00" in data:
                    res.errors.append(f"{where}: a NUL byte")
                if kind == "battle" and b.index in firsts and (not data or not battle_first_ok(data)):
                    res.errors.append(f"{where}: the first string of a battle section must start with a byte "
                                      f"20-7E or A1-FE (the kit finds a text section by it); it starts "
                                      f"{data[:1].hex().upper() or 'empty'}")
        return new
    return transform_blocks(text, blk)


# --- configs ----------------------------------------------------------------------

def load_langpack(glyphset_path) -> dict:
    p = Path(glyphset_path).with_name(LANGPACK)
    if not p.exists():
        raise LangError(f"{p} is missing: lang-scan writes it beside glyphset.json")
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("format") != 1 or not isinstance(d.get("configs"), list):
        raise LangError(f"{p}: not a langpack.json of format 1")
    return d


def _encoder_from(glyphset_path, a, collect=False) -> Encoder:
    gs = GlyphSet.load(glyphset_path)
    lp = load_langpack(glyphset_path)
    configs = lp["configs"]
    f0p = Path(glyphset_path).with_name("font0.us")
    if not f0p.exists():
        raise LangError(f"{f0p} is missing: lang-scan writes the rebuilt fonts beside glyphset.json")
    f0 = Font.load(f0p)
    widths = parse_widths(getattr(a, "width", 0), getattr(a, "width_kind", None) or [])
    rtl = a.rtl if a.rtl is not None else (None if any(c.get("rtl") for c in configs) else False)
    return Encoder(configs, gs, gs.addressing(), f0.metrics, rtl=rtl, widths=widths, align=a.align,
                   space_px=a.space_px, collect=collect)


def parse_widths(width: int, kinds: Sequence[str]) -> Dict[str, int]:
    out = {k: (width or 0) for k in KINDS}
    if not width:
        out["subtitle"] = SUBTITLE_WIDTH
    for spec in kinds:
        k, _, v = spec.partition("=")
        if k not in KINDS or not v.isdigit():
            raise LangError(f"--width-kind {spec!r}: want KIND=PX with KIND one of {', '.join(KINDS)}")
        out[k] = int(v)
    return out


# --- commands -----------------------------------------------------------------------

def _placeholder_rank(tiers: Tiers, f: Font):
    """Rank cells for allocation: blank or placeholder-mark cells first, so the
    Latin-1 glyphs of the single tier are given out last."""
    imgs = {}
    for c in tiers.all_codes:
        d = tiers.adr.drawn_cell(c, True)
        imgs[c] = tuple(tuple(r) for r in f.cell(d))
    common = Counter(imgs.values()).most_common(1)[0][0] if imgs else None
    empty = tuple(tuple([0] * fontmod.CELL_W) for _ in range(fontmod.CELL_H))
    return lambda c: 0 if imgs.get(c) in (common, empty) else 1


def cmd_scan(a) -> int:
    from iso9660 import IsoImage
    import region
    from repack import read_entry

    try:
        iso = IsoImage(a.iso)
        try:
            prof = region.require_executable(iso, "lang-scan")
            if prof.name != "us":
                raise LangError(f"lang-scan is written for the USA build (SLUS_208.92 / SLUS_211.33); this disc "
                                f"is {iso.boot_elf_name()} ({prof.name}), whose glyph space is different")
            adr = fontmod.Addressing.from_iso(iso)
            exe_sha = hashlib.sha256(region.executable(iso)[1]).hexdigest()
        finally:
            iso.close()
    except (region.UnknownBuild, FontError) as e:
        raise LangError(str(e))
    if bool(a.font0) != bool(a.font1):
        raise LangError("give both --font0 and --font1 (the previous scan's two files), or neither")
    if a.font0:
        b0, b1 = Path(a.font0).read_bytes(), Path(a.font1).read_bytes()
    else:
        b0, b1 = read_entry(a.iso, 0, "font0.us"), read_entry(a.iso, 0, "font1.us")
    f0, f1 = Font.from_bytes(b0), Font.from_bytes(b1)
    same_before = f0.sheets[0] == f1.sheets[0] and f0.metrics == f1.metrics
    if not same_before:
        print("warning: font0 and font1 differ in sheet 0 or the metrics (they are identical on the retail discs)")
    if not a.font and not a.cells:
        raise LangError("give --font (TrueType/OpenType) or --cells and --keys (a PNG sheet)")
    if a.cells and not a.keys:
        raise LangError("--cells needs --keys (one key or character per cell)")
    configs: List[dict] = []
    gs = GlyphSet(a.font or a.cells)
    if a.extend:
        gs = GlyphSet.load(a.extend)
        gs.source += f" + {a.font or a.cells}"
        configs = list(load_langpack(a.extend)["configs"])
        if gs.exe_sha256 and gs.exe_sha256 != exe_sha:
            raise LangError("the --extend glyphset was made for another executable")
    if a.font:
        cfg = {"ttf": str(Path(a.font).resolve()), "face": a.face, "px": a.px, "baseline": a.baseline,
               "script": a.script, "language": a.language, "rtl": bool(a.rtl), "prefer": a.prefer,
               "tight": bool(a.tight)}
    else:
        cfg = {"cells": str(Path(a.cells).resolve()), "keys": str(Path(a.keys).resolve()), "script": a.script,
               "rtl": bool(a.rtl), "prefer": a.prefer, "tight": bool(a.tight)}
    if any(c.get("script") == cfg["script"] for c in configs):
        raise LangError(f"the --extend project already has a config for script {cfg['script'] or '(any)'}")
    configs.append(cfg)
    only = len(configs) - 1
    gs.bind(adr, exe_sha)
    if a.cells:
        keys = [k for k in Path(a.keys).read_text(encoding="utf-8").split("\n") if k]
        keys = [k if k.startswith(("u:", "g:", "c:")) else f"u:{k}" for k in keys]
        for g in from_png(a.cells, keys, a.cells).glyphs.values():
            gs.add(g)
    else:
        from glyphs import require_shaping
        require_shaping()
    rtl = None if any(c.get("rtl") for c in configs) else False
    enc = Encoder(configs, gs, adr, f0.metrics, rtl=rtl, widths=parse_widths(0, []), collect=True, only=only,
                  space_px=a.space_px)
    roots = [Path(t) for t in a.text]
    firsts: set = set()
    res = run_tree(roots, enc, firsts)
    if res.errors:
        raise LangError("; ".join(res.errors[:6]))
    if enc.unknown:
        chars = ", ".join(f"{c!r} U+{ord(c):04X}" for c, _ in enc.unknown.most_common(8))
        print(f"note: {sum(enc.unknown.values())} character(s) belong to no scanned script and are not in the "
              f"USA font: {chars}. Scan them with another --script (or without --script) before encoding")
    order = [k for k, _ in enc.counts.most_common()]
    shaper = enc.shaper(only)
    new = [k for k in order if k not in gs.glyphs]
    if a.cells and new:
        raise LangError(f"{len(new)} key(s) have no cell in {a.cells}: {new[:8]}")
    for k in new:
        gs.add(shaper.glyph(k))
    # a glyph is drawn whole or not at all: ink outside the 20x24 cell would be lost on the disc
    # (a wide one is drawn narrower by glyphs.TTFRenderer.raster instead; ink above or below stays cut)
    cut = [k for k in new if getattr(gs.glyphs[k], "cut", 0)]
    if cut:
        raise LangError(f"{len(cut)} glyph(s) do not fit the 20x24 cell and would be cut off: "
                        + ", ".join(f"{k} ({gs.glyphs[k].cut} px outside)" for k in cut[:8])
                        + ". Try a smaller --px or another --baseline")
    narrowed = [k for k in new if getattr(gs.glyphs[k], "narrowed", 1.0) < 1.0]
    for k in order:
        g = gs.glyphs[k]
        enc.provisional[k] = fontmod.FIXED_ADVANCE if g.ink_width > 18 else (g.ink_width + 2 if g.bounds else
                                                                              (g.advance_hint or 6))
    limited = {k for k in order if enc.kinds_of[k] & set(LIMITED_KINDS)}
    # the reserve: the base cells, every cell the disc's visible retail text reaches, --keep, minus --reclaim
    t0 = Tiers(adr)
    candidates = set(t0.all_codes) - set(t0.reserved)
    print(f"scanning the visible retail text of {Path(a.iso).name} for cells it uses ...")
    used_by_retail = __import__("glyphs").retail_reserve(a.iso, adr, candidates)
    reserve = {c: f"retail text ({n} uses, e.g. {ex})" for c, (n, ex) in used_by_retail.items()}
    for c in gs.reserved:
        if c in candidates:
            reserve.setdefault(c, "reserved by the --extend glyphset")
    for ch in a.keep or "":
        try:
            g = adr.glyph(_US.encode_char(ch) + b"\x00", 0)
        except textcodec.TextError as e:
            raise LangError(f"--keep {ch!r}: {e}")
        reserve[g.cell] = f"--keep {ch}"
    for spec in (a.reclaim or "").split(","):
        if not spec.strip():
            continue
        try:
            b = int(spec, 16)
        except ValueError:
            raise LangError(f"--reclaim {spec!r}: give lone bytes in hex, e.g. --reclaim FC")
        cell = adr.glyph(bytes((b, 0x20)), 0).cell
        why = reserve.pop(cell, None)
        if why:
            print(f"warning: --reclaim {b:02X} frees cell {cell}; retail strings that use it will draw the new "
                  f"glyph instead: {why}")
    # the cells of non-ASCII characters the tree types as they are (« » £ é ...): their own byte
    # draws them, so a new glyph in that cell would replace them. --reclaim does not free these.
    slot_cells = {s["cell"]: k for k, s in gs.slots.items()}
    clash = []
    for cell, (n, ch, where) in sorted(enc.typed.items()):
        if cell in slot_cells:
            clash.append(f"{ch!r} (U+{ord(ch):04X}, e.g. {where}) and the glyph {slot_cells[cell]} share cell {cell}")
        elif cell in candidates:
            reserve.setdefault(cell, f"typed in the tree: {ch!r} U+{ord(ch):04X} ({n} uses, e.g. {where})")
    if clash:
        raise LangError("characters the tree types sit in cells the --extend glyphset already gave to glyphs: "
                        + "; ".join(clash[:6]) + ". Scan the scripts again from the retail fonts with these "
                        "characters in the tree, or replace them")
    tiers = Tiers(adr, reserve)
    tiers.rank = _placeholder_rank(tiers, f0)
    print("reserved beyond the base cells:")
    if not reserve:
        print("  (none)")
    for c in sorted(reserve):
        if c in tiers.all_codes:
            print(f"  cell {c} (code {tiers.code(c).hex().upper()}): {reserve[c]}")
    before = set(gs.slots)
    try:
        gs.allocate(tiers, order, limited=limited, battle_first=firsts, prefer=cfg["prefer"],
                    prop_only=a.prop_only, tight=bool(cfg.get("tight")))
    except GlyphError as e:
        free = tiers.counts({s["cell"] for s in gs.slots.values()})
        raise LangError(f"{e}. Free cells: " + ", ".join(f"{t} {free[t][1]}" for t in TIERS)
                        + f"; this translation needs {len(order)} distinct glyphs for script "
                          f"{cfg['script'] or '(any)'}. Fewer glyphs: a smaller --px merges nothing, but "
                          f"reducing conjuncts, --prop-only off, or --reclaim can help")
    gs.reserved = sorted(set(gs.reserved) | set(reserve))
    for f in (f0, f1):
        gs.apply(f)
        bad = gs.verify(f, adr)
        if bad:
            raise LangError("the rebuilt font does not read back: " + "; ".join(bad[:5]))
    if same_before and not (f0.sheets[0] == f1.sheets[0] and f0.metrics == f1.metrics):
        raise LangError("font0 and font1 no longer agree in sheet 0 (internal error)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    blobs = {"font0.us": f0.to_bytes(), "font1.us": f1.to_bytes()}
    for name, blob in blobs.items():
        if Font.from_bytes(blob).to_bytes() != blob:
            raise LangError(f"{name} does not round-trip")
        (out / name).write_bytes(blob)
    gs.save(out / "glyphset.json")
    (out / LANGPACK).write_text(json.dumps({"format": 1, "configs": configs, "space_px": a.space_px},
                                           indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    newcells = set()
    for k in set(gs.slots) - before:
        s = gs.slots[k]
        newcells.add(s["cell"])
        if s["tier"] != FIXED:
            newcells.add(s["cell"] + fontmod.PROP_CELL_SHIFT)
    (out / "sheet0_preview.png").write_bytes(__import__("glyphs").sheet_preview(f0, newcells))
    # report
    print(f"scanned {len(roots)} tree(s): {sum(enc.counts.values())} glyph uses, {len(order)} distinct glyphs "
          f"for script {cfg['script'] or '(any)'}; {len(set(gs.slots) - before)} new, {len(gs.slots)} in the glyphset")
    counts = tiers.counts({s["cell"] for s in gs.slots.values()})
    for t in TIERS:
        print(f"  tier {t:<6} used {counts[t][0]:>3}  free {counts[t][1]:>3}")
    by_tier = Counter(gs.slots[k]["tier"] for k in order if k in gs.slots)
    print(f"  script {cfg['script'] or '(any)'}: {len(order)} glyphs ("
          + ", ".join(f"{by_tier[t]} {t}" for t in TIERS) + f"), {len(limited)} in size-limited text, "
          f"{len(firsts)} first in a battle section")
    for k in narrowed:
        print(f"note: {k} is wider than the cell at this --px; drawn at {gs.glyphs[k].narrowed:.0%} of its width "
              f"so none of it is cut off ({gs.slots[k]['tier']} code {gs.slots[k]['bytes']})")
    clipped = [k for k in new if gs.glyphs[k].clipped]
    if clipped:
        print(f"warning: {len(clipped)} glyph(s) touch the edge of the 20x24 cell (nothing of them is cut off, "
              f"but they may touch their neighbours); look at sheet0_preview.png")
    nd = getattr(shaper, "notdef", set())
    if nd:
        print(f"warning: {len(nd)} word(s) use characters this font lacks (drawn as its .notdef): {sorted(nd)[:6]}")
    # the encoded sizes and widths, with the new glyphs
    rep = Encoder(configs, gs, adr, f0.metrics, rtl=rtl, widths=parse_widths(0, []), space_px=a.space_px)
    rr = run_tree(roots, rep)
    for where, n, limit, changed in rr.budgets:
        if changed:
            print(f"  {'OVER ' if n > limit else ''}{where}: {n} of {limit} bytes")
    limits = None
    try:
        import textpack
        ctx = textpack.Context(IsoImage(a.iso), need_executable=True)
        limits = ctx.subtitle_limits()
    except Exception as e:                      # the report goes on without the disc's subtitle bounds
        print(f"note: no subtitle bounds from this disc ({e})")
    for where, n, px in rr.sub_lines:
        over = limits is not None and (n > limits.line_bytes or px > (limits.line_width or 10 ** 6))
        print(f"  {'OVER ' if over else ''}{where}: {n} bytes, {px} px"
              + (f" (this disc's retail lines reach {limits.line_bytes} bytes, {limits.line_width} px)" if limits else ""))
    for kind, (w, where) in sorted(rep.widest.items()):
        print(f"  widest {kind} line: {w} px  {where}")
    for e in rr.errors[:20]:
        print(f"  problem: {e}")
    print(f"wrote {out / 'font0.us'}, font1.us, glyphset.json, {LANGPACK}, sheet0_preview.png")
    print("patch the fonts into a copy of each disc: cli.py patch --iso DISC.iso --out COPY.iso "
          f"--set 'chain0:font0.us={out / 'font0.us'}' --set 'chain0:font1.us={out / 'font1.us'}'")
    return 0


def cmd_encode(a) -> int:
    src, dst = Path(a.text), Path(a.out)
    if dst.resolve() == src.resolve() or src.resolve() in dst.resolve().parents:
        raise LangError(f"--out {dst} is inside the source tree; give another folder")
    enc = _encoder_from(a.glyphset, a)
    res = run_tree([src], enc)
    errors = list(res.errors) + list(enc.errors) + enc.typed_conflicts()
    if enc.missing:
        errors.append(f"{sum(enc.missing.values())} glyph use(s) have no slot ({len(enc.missing)} distinct, e.g. "
                      f"{list(enc.missing)[:4]}); run lang-scan again on this tree")
    if enc.unknown:
        errors.append("characters of no scanned script that the USA font cannot draw: "
                      + ", ".join(f"{c!r} U+{ord(c):04X}" for c, _ in enc.unknown.most_common(8)))
    for w in dict.fromkeys(enc.warnings):
        if w not in errors:
            print(f"warning: {w}", file=sys.stderr)
    if errors:
        for e in errors[:40]:
            print(f"refused: {e}", file=sys.stderr)
        print(f"{len(errors)} problem(s); nothing was written", file=sys.stderr)
        return 1
    for rel, blob in res.files.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
    print(f"encoded {res.changed} changed file(s) of {len(res.files)} -> {dst}")
    for kind, (w, where) in sorted(enc.widest.items()):
        print(f"  widest {kind} line: {w} px  {where}")
    if a.iso:
        import region
        import textpack
        try:
            changed, problems = textpack.plan_import(a.iso, str(dst))
        except region.UnknownBuild as e:
            print(f"dry run refused: {e}", file=sys.stderr)
            return 1
        for p in problems:
            print(f"dry run: {p}", file=sys.stderr)
        print(f"dry run on {a.iso}: {len(changed)} object(s) would change, {len(problems)} problem(s)")
        return 1 if problems else 0
    return 0


def render_line(adr: "fontmod.Addressing", font: Font, data: bytes, scale: int = 2, xx1: int = 8) -> bytes:
    """PNG of one line drawn from the font the way the print function draws
    ``$han1;$prop1;`` text: proportional cells from cell + 576, columns left - 1 to
    right + 1, advance right - left + 3; fixed cells whole, advance 19. Codes are skipped."""
    pen, draws, i = 0, [], 0
    while i < len(data):
        b = data[i]
        if b == 0x24:
            m = _CODE.match(data[i:].decode("latin1"))
            if m:
                i += m.end()
                continue
        if b < 0x20:
            i += 1
            continue
        g = adr.glyph(data + b"\x00", i, han=True)
        i += g.length
        if g.cell is None:
            continue
        slot = adr.metric_slot(g.cell, True)
        if slot is None:
            draws.append((g.cell, pen, 0, fontmod.CELL_W - 1))
            pen += fontmod.FIXED_ADVANCE
            continue
        left, right = font.metrics[slot]
        if left == 0:
            pen += xx1 + 1
            continue
        draws.append((g.cell + fontmod.PROP_CELL_SHIFT, pen - (left - 1), left - 1, right + 1))
        pen += right - left + adr.prop_pad
    W = max(pen + 2, 1)
    img = [[0] * W for _ in range(fontmod.CELL_H)]
    for cell, x0, c0, c1 in draws:
        bm = font.cell(cell)
        for y in range(fontmod.CELL_H):
            for x in range(c0, min(c1, fontmod.CELL_W - 1) + 1):
                if bm[y][x] and 0 <= x0 + x < W:
                    img[y][x0 + x] = max(img[y][x0 + x], bm[y][x])
    flat = bytearray()
    for row in img:
        for _ in range(scale):
            for v in row:
                flat += bytes([v]) * scale
    return fontmod.sheet_to_png(flat, W * scale, fontmod.CELL_H * scale)


def cmd_preview(a) -> int:
    a.width_kind = []
    enc = _encoder_from(a.glyphset, a)
    if a.width:
        enc.widths[a.kind] = a.width
    elif a.kind != "subtitle":
        enc.widths[a.kind] = 0
    face = Font.load(Path(a.glyphset).with_name(f"{a.face}.us"))
    encoded = enc.string(a.line, a.kind, "preview")
    errors = list(enc.errors) + enc.typed_conflicts()
    if enc.missing:
        errors.append(f"no slot for {list(enc.missing)[:4]}: run lang-scan on text that has these words")
    if enc.unknown:
        errors.append("characters of no scanned script: " + "".join(enc.unknown))
    if errors:
        for e in errors:
            print(f"refused: {e}", file=sys.stderr)
        return 1
    print(encoded)
    lines = [_US.from_text(ln) for ln in re.split(r"\\n|⟦0[Aa]⟧", encoded)]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    xx1 = enc.space_px - 1
    pngs = [render_line(enc.adr, face, ln, a.scale, xx1) for ln in lines]
    if len(pngs) == 1:
        out.write_bytes(pngs[0])
        written = [out]
    else:
        written = [out.with_name(f"{out.stem}_{i}{out.suffix}") for i in range(len(pngs))]
        for p, png in zip(written, pngs):
            p.write_bytes(png)
    print(f"preview ({a.face}) -> {', '.join(str(p) for p in written)}")
    return 0


# --- argparse (shared with cli.py) ---------------------------------------------------

def add_scan_args(p) -> None:
    p.add_argument("--iso", required=True, help="the USA disc image the translation is for (addressing, executable "
                   "check, the retail reserve, the fonts unless --font0/--font1)")
    p.add_argument("--text", action="append", required=True, help="the translated text-export folder (repeatable)")
    p.add_argument("--font", help="TrueType/OpenType font file (.ttf, .otf, .ttc) to draw the glyphs from")
    p.add_argument("--face", type=int, default=0, help="face index inside a .ttc collection")
    p.add_argument("--px", type=float, default=18.0, help="em size in pixels (18 puts Latin capitals where retail's are)")
    p.add_argument("--baseline", type=int, default=19, help="baseline row in the 24 px cell (retail: under row 18)")
    p.add_argument("--script", help="ISO 15924 script tag: only words of this script are scanned (Arab, Deva, Geor, ...)")
    p.add_argument("--language", help="BCP-47 language for shaping (ar, hi, ka, ...)")
    p.add_argument("--rtl", action="store_true", help="the script is written right to left")
    p.add_argument("--cells", help="PNG sheet of hand-drawn 20x24 cells instead of --font")
    p.add_argument("--keys", help="text file, one key or character per cell of --cells")
    p.add_argument("--prefer", choices=(SINGLE, PAIR, FIXED), default=SINGLE,
                   help="tier to fill first (size-limited text takes single bytes first anyway)")
    p.add_argument("--keep", help="Latin-1 characters whose cells must stay as they are (e.g. 'éèàç')")
    p.add_argument("--reclaim", help="free these reserved lone bytes, hex, comma-separated (e.g. FC)")
    p.add_argument("--prop-only", action="store_true", help="refuse glyphs too wide for the proportional tiers")
    p.add_argument("--tight", action="store_true", help="ink at left-1..right+1 so joining letters touch "
                   "(the edge columns are read from code, not seen in the game)")
    p.add_argument("--space-px", type=int, default=DEFAULT_SPACE_PX, help="space advance for the width report")
    p.add_argument("--font0", help="start from this font0.us (an earlier scan's), with --font1")
    p.add_argument("--font1", help="start from this font1.us (an earlier scan's), with --font0")
    p.add_argument("--extend", help="glyphset.json of an earlier scan: keep its slots and add this script")
    p.add_argument("--out", required=True, help="folder for font0.us, font1.us, glyphset.json, langpack.json")


def add_encode_args(p) -> None:
    p.add_argument("--text", required=True, help="the translated text-export folder (logical order)")
    p.add_argument("--glyphset", required=True, help="glyphset.json from lang-scan (langpack.json and the fonts beside it)")
    p.add_argument("--out", required=True, help="folder for the encoded tree (text-import reads it)")
    _layout_args(p)
    p.add_argument("--width-kind", action="append", default=[], metavar="KIND=PX",
                   help="wrap width per kind: bank, menu, subtitle, battle, exe (repeatable)")
    p.add_argument("--iso", help="a disc copy with the new fonts patched in: dry-run text-import on the result")


def add_preview_args(p) -> None:
    p.add_argument("--glyphset", required=True, help="glyphset.json from lang-scan")
    p.add_argument("--text", dest="line", required=True, help="the line to draw, as you would write it in the tree")
    p.add_argument("--out", required=True, help="PNG to write (several lines: NAME_0.png, NAME_1.png, ...)")
    _layout_args(p)
    p.add_argument("--scale", type=int, default=2)
    p.add_argument("--face", choices=("font0", "font1"), default="font1",
                   help="font1 draws dialogue, menus and battle; font0 movies and cfe cutscenes")
    p.add_argument("--kind", choices=KINDS, default="bank")


def _layout_args(p) -> None:
    p.add_argument("--width", type=int, default=0, help="wrap width in pixels for every kind (0 = none; subtitles 440)")
    p.add_argument("--align", choices=("left", "right"), default="left", help="right pads RTL lines of banks and menus")
    p.add_argument("--space-px", type=int, default=DEFAULT_SPACE_PX, help="space advance ($xx1 + 1), default 9")
    p.add_argument("--rtl", dest="rtl", action="store_true", default=None,
                   help="order right-to-left paragraphs (the default when a scanned script is RTL)")
    p.add_argument("--ltr", dest="rtl", action="store_false", help="never reorder")


def run(cmd: str, a) -> int:
    try:
        return {"scan": cmd_scan, "encode": cmd_encode, "preview": cmd_preview}[cmd](a)
    except (LangError, GlyphError, FontError, textcodec.TextError) as ex:
        print(f"refused: {ex}", file=sys.stderr)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_scan_args(sub.add_parser("scan"))
    add_encode_args(sub.add_parser("encode"))
    add_preview_args(sub.add_parser("preview"))
    a = ap.parse_args(argv)
    return run(a.cmd, a)


# --- self-test ----------------------------------------------------------------------

def self_test() -> int:
    import tempfile
    from glyphs import blank_font, box, build_test_ttf, synthetic_addressing, TTFRenderer
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    adr = synthetic_addressing()
    f = blank_font()
    for c in range(0, 196):                         # ASCII cells: 6 px wide letters, ink 3..8 -> advance 8
        f.set_metrics(c, 3, 8)
    f.set_metrics(0, 0, 19)                         # the space: (0, 19) as in the retail font
    gs = GlyphSet("test")
    gs.bind(adr)
    heb = "\u05d0\u05d1\u05d2\u05d3\u05d4\u05d5"   # א ב ג ד ה ו
    for i, ch in enumerate(heb):
        gs.add(Glyph(f"u:{ch}", box(2, 2 + i)))     # ink 1..6 px -> advance 3..8
    tiers = Tiers(adr)
    gs.allocate(tiers, [f"u:{c}" for c in heb])
    gs.apply(f)
    cfg = [{"cells": "x", "keys": "y", "script": "Hebr", "rtl": True, "prefer": SINGLE}]

    def enc(**kw):
        return Encoder(cfg, gs, adr, f.metrics, shapers=[SimpleShaper()], **kw)

    def code(ch):
        return "".join(f"⟦{b:02X}⟧" for b in gs.code_bytes(f"u:{ch}"))
    A, B, G, D, H, V = (code(c) for c in heb)
    e = enc()
    out = e.string("\u05d0\u05d1 KOS-MOS \u05d2 3.5 \u05d3")
    check(out == D + " 3.5 " + G + " KOS-MOS " + B + A,
          "RTL: words reversed, 'KOS-MOS' and '3.5' kept forward, each word's letters in visual order")
    check(e.string("(\u05d0\u05d1)") == "(" + B + A + ")", "brackets mirrored around an RTL word")
    check(e.string("[Canaan]\u05d0 \u05d1") == "[Canaan]" + B + " " + A, "a leading [Speaker] stays first")
    s = e.string("\u05d0 $col808080;\u05d1 \u05d2")
    check(s == "$col808080;" + G + " " + B + " " + A and "cannot be undone" in " ".join(e.warnings),
          f"$col808080; re-emitted for the words after it in logical order ({s!r}); the unknown initial colour is reported")
    s2 = e.string("$colc0c0c0;\u05d0 $col808080;\u05d1 \u05d2")
    check(s2 == "$col808080;" + G + " " + B + " $colc0c0c0;" + A + "$col808080;",
          "each word is drawn in its logical colour, and the end state is restored after the line")
    check(e.string("\u05d0\\n\u05d1 \u05d2") == A + "\\n" + G + " " + B, "the two-character \\n splits paragraphs")
    check(e.string("\u05d0⟦0A⟧\u05d1", "exe") == A + "⟦0A⟧" + B, "⟦0A⟧ is a break in executable strings")
    ew = enc(widths={"bank": 30})
    w1 = ew.string("\u05d4\u05d5 \u05d4\u05d5 \u05d4\u05d5")     # 7+8 = 15 px a word, space 9
    check(w1 == V + H + "\\n" + V + H + "\\n" + V + H and ew.widest["bank"][0] == 15,
          f"wrap at --width 30 with Measurer widths: 15 + 9 + 15 > 30 -> one word a line ({w1!r})")
    ew2 = enc(widths={"bank": 40})
    check(ew2.string("\u05d4\u05d5 \u05d4\u05d5") == V + H + " " + V + H, "two words of 15 px and a 9 px space fit 40")
    ep = enc(widths={"bank": 60}, align="right", space_px=5)
    pr = ep.string("\u05d4\u05d5")
    check(pr == " " * ((60 - 15) // 5) + V + H, "right padding counts --space-px spaces")
    pb = ep.string("\u05d4 <\u25cb>")
    check(pb == " " * ((60 - 7 - 5 - ep.lit_width("\u25cb")) // 5) + "<\u25cb> " + H,
          "a '<○>' button tag is one unit as wide as its symbol (the window draws no brackets)")
    for kind in ("bank", "subtitle"):
        ic = e.string("א &12 ב", kind)
        check(ic == B + " &12 " + A and not e.errors,
              f"an &NN icon token inside an RTL line is placed as a neutral but written verbatim ({kind}: {ic!r})")
    check(e.string("א <△> ב") == B + " <△> " + A, "a '<△>' tag is written verbatim, not mirrored")
    check(enc(widths={"subtitle": 60}, align="right").string("\u05d4\u05d5", "subtitle") == V + H,
          "subtitles are never padded")
    check(e.string("Plain English, $zoom13;untouched.") == "Plain English, $zoom13;untouched."
          and e.string("") == "", "a body without the target script comes back unchanged")
    check(enc(rtl=False).string("\u05d0 \u05d1") == A + " " + B, "--ltr keeps logical order")
    check(e.string("KOS-MOS \u05d0") == A + " KOS-MOS", "a Latin name at the start of an RTL line moves to the right")
    check(e.string("\u05d0 &03 \u05d1") == B + " &03 " + A and e.string("Press &01 \u05d0") == A + " &01 Press",
          "a controller icon &NN inside a right-to-left line is written as it is (never reversed)")
    check(e.string("\u05d0 \u00ab\u05d1\u00bb") == "\u00ab" + B + "\u00bb " + A,
          "the Arabic quotation marks \u00ab \u00bb are mirrored like brackets")
    check(e.string("\u05d0 \u00a35.") == ".\u00a35 " + A, "a currency sign travels with its number (UBA W5)")
    et = enc()
    et.string("\u05d0 \u00ab\u05d1\u00bb \u00e9 \u00a3", "bank", "w")
    et.string("Plain caf\u00e9 line, no target script", "bank", "w2")
    et.string("\u65e5\u672c", "bank", "w3")
    et.string("\u00cd\u00bd\u00c8\u00f7", "battle", "w4")      # bytes CD BD C8 F7 = EUC-JP 予備, a developer line
    check(set(et.typed) == {0xAB + 0x80, 0xBB + 0x80, 0xE9 + 0x80, 0xA3 + 0x80},
          f"typed Latin-1 characters are recorded by cell, untouched bodies included, Japanese and EUC-JP "
          f"developer lines shown as Latin-1 left out "
          f"({sorted(et.typed)})")
    g2 = GlyphSet("t2")
    g2.bind(adr)
    for ch in heb[:2]:
        g2.add(Glyph(f"u:{ch}", box(2, 4)))
    g2.slots["u:" + heb[0]] = {"tier": SINGLE, "cell": 0xAB + 0x80, "bytes": "AB", "left": 2, "right": 4}
    g2.slots["u:" + heb[1]] = {"tier": SINGLE, "cell": 0xC0 + 0x80, "bytes": "C0", "left": 2, "right": 4}
    e2 = Encoder(cfg, g2, adr, f.metrics, shapers=[SimpleShaper()])
    e2.string("\u05d0 \u00ab\u05d1\u00bb")
    tc = e2.typed_conflicts()
    check(len(tc) == 1 and "cell 299" in tc[0], "a typed \u00ab whose cell holds a new glyph is a conflict")
    # trees
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        (src / "chain0" / "cf" / "en").mkdir(parents=True)
        (src / "chain0" / "yosikawa").mkdir(parents=True)
        (src / "chain0" / "yamamoto").mkdir(parents=True)
        (src / "chain1" / "movie" / "en").mkdir(parents=True)
        (src / "exe").mkdir(parents=True)
        files = {
            "chain0/cf/en/sc0001.sbl.txt": "# cf\\en\\sc0001.sbl  chain0  2 strings\n### 0000\n[Canaan]What is this?\n"
                                           "### 0001\n#sjis: x\nLine one\\nline two\n\n",
            "chain0/yosikawa/Menu.txd.txt": "# yosikawa\\Menu.txd  2 strings\n### 0000\nItems\n### 0001\n%p left\n",
            "chain0/yamamoto/batdat.us.txt": "# yamamoto\\batdat.us  3 strings\n### S00 @0x0010 max 5 bytes\nchaos\n"
                                            "### S00 @0x0016 max 4 bytes\nJin\n### S01 @0x0004 max 3 bytes\nAbc\n",
            "chain1/movie/en/s0001.sub.txt": ";comment \u3042\n00:00:00\t$ay390;$xx0,22;\n00:01:00\tHello there\n"
                                            "00:02:00\tSecond\\nline\nend\n",
            "exe/SLUS_208.92.exe.txt": "# exetext 1 file=SLUS_208.92 role=boot sha256=00\n### @0x0068b678 max 15 bytes\n"
                                       "Saving...\n",
            "xs2-text-export.json": "{\"format\": 2}\n",
            "chain0/iwase/q.sjis.txt": "\"\u30c6\u30b9\u30c8\"\n",
        }
        for rel, t in files.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(t.encode("utf-8"))
        r = run_tree([src], enc(widths=parse_widths(0, [])))
        check(not r.errors and r.changed == 0 and all(r.files[k] == (src / k).read_bytes() for k in files),
              "an untouched tree (bank, menu, battle, subtitle, exe, quest, manifest) encodes byte-identical")
        tr = Path(td) / "tr"
        import shutil
        shutil.copytree(src, tr)
        (tr / "chain1/movie/en/s0001.sub.txt").write_text(files["chain1/movie/en/s0001.sub.txt"].replace(
            "Hello there", "\u05d0\u05d1 \u05d2"), encoding="utf-8")
        (tr / "chain0/cf/en/sc0001.sbl.txt").write_text(files["chain0/cf/en/sc0001.sbl.txt"].replace(
            "What is this?", "\u05d0\u05d1?"), encoding="utf-8")
        r = run_tree([tr], enc(widths=parse_widths(0, [])))
        sub_old = files["chain1/movie/en/s0001.sub.txt"].split("\n")
        sub_new = r.files["chain1/movie/en/s0001.sub.txt"].decode("utf-8").split("\n")
        diff = [i for i, (x, y) in enumerate(zip(sub_old, sub_new)) if x != y]
        check(not r.errors and diff == [2] and sub_new[2] == "00:01:00\t" + G + " " + B + A,
              f"a subtitle file changes only the cue text ({sub_new[2]!r})")
        bank = r.files["chain0/cf/en/sc0001.sbl.txt"].decode("utf-8")
        check("[Canaan]?" + B + A + "\n" in bank and "Line one\\nline two" in bank and "#sjis: x" in bank,
              "the bank's changed block is encoded, the rest of the file is as it was")
        # budgets and missing glyphs: nothing written, exit 1
        (tr / "chain0/yamamoto/batdat.us.txt").write_text(files["chain0/yamamoto/batdat.us.txt"].replace(
            "Jin", "\u05d0\u05d1\u05d2\u05d3\u05d4"), encoding="utf-8")
        r = run_tree([tr], enc())
        check(any("4 bytes" in x or "holds 4" in x for x in r.errors), "an over-budget battle string is refused")
        (tr / "chain0/yamamoto/batdat.us.txt").write_text(files["chain0/yamamoto/batdat.us.txt"], encoding="utf-8")
        (tr / "exe/SLUS_208.92.exe.txt").write_text(files["exe/SLUS_208.92.exe.txt"].replace(
            "Saving...", "\u05d0" * 16), encoding="utf-8")
        r = run_tree([tr], enc())
        check(any("holds 15" in x for x in r.errors), "an over-budget executable string is refused")
        (tr / "exe/SLUS_208.92.exe.txt").write_text(files["exe/SLUS_208.92.exe.txt"], encoding="utf-8")
        # a section-first battle string: its first byte must be 20-7E or A1-FE
        g80 = GlyphSet("t")
        g80.bind(adr)
        g80.add(Glyph("u:\u05d0", box(2, 4)))
        g80.slots["u:\u05d0"] = {"tier": SINGLE, "cell": 0x81 + 0x80, "bytes": "81", "left": 2, "right": 4}
        (tr / "chain0/yamamoto/batdat.us.txt").write_text(files["chain0/yamamoto/batdat.us.txt"].replace(
            "chaos", "\u05d0"), encoding="utf-8")
        e80 = Encoder(cfg, g80, adr, f.metrics, shapers=[SimpleShaper()])
        r = run_tree([tr], e80)
        check(any("first string of a battle section" in x for x in r.errors),
              "a section's first battle string starting with byte 81 is refused")
        (tr / "chain0/yamamoto/batdat.us.txt").write_text(files["chain0/yamamoto/batdat.us.txt"], encoding="utf-8")
        # the command: missing glyph -> exit 1 and nothing written
        gdir = Path(td) / "g"
        gdir.mkdir()
        gs.save(gdir / "glyphset.json")
        (gdir / "font0.us").write_bytes(f.to_bytes())
        (gdir / LANGPACK).write_text(json.dumps({"format": 1, "configs": cfg}), encoding="utf-8")
        (tr / "chain0/cf/en/sc0001.sbl.txt").write_text(files["chain0/cf/en/sc0001.sbl.txt"].replace(
            "What is this?", "\u05d0\u05e9"), encoding="utf-8")       # ש has no glyph
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(["encode", "--text", str(tr), "--glyphset", str(gdir / "glyphset.json"),
                       "--out", str(Path(td) / "enc")])
        check(rc == 1 and not (Path(td) / "enc").exists() and "no slot" in err.getvalue(),
              "a glyph with no slot: lang-encode writes nothing and exits 1")
        (tr / "chain0/cf/en/sc0001.sbl.txt").write_text(files["chain0/cf/en/sc0001.sbl.txt"].replace(
            "What is this?", "\u05d0\u05d1?"), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(["encode", "--text", str(tr), "--glyphset", str(gdir / "glyphset.json"),
                       "--out", str(Path(td) / "enc"), "--width-kind", "bank=300"])
        check(rc == 0 and (Path(td) / "enc/xs2-text-export.json").read_bytes() == b"{\"format\": 2}\n"
              and "[Canaan]?" + B + A in (Path(td) / "enc/chain0/cf/en/sc0001.sbl.txt").read_text(encoding="utf-8"),
              "lang-encode writes the encoded tree with the manifest copied")
        g2dir = Path(td) / "g2"
        g2dir.mkdir()
        g2.save(g2dir / "glyphset.json")
        (g2dir / "font0.us").write_bytes(f.to_bytes())
        (g2dir / LANGPACK).write_text(json.dumps({"format": 1, "configs": cfg}), encoding="utf-8")
        t2 = Path(td) / "t2"
        shutil.copytree(tr, t2)
        (t2 / "chain0/cf/en/sc0001.sbl.txt").write_text(files["chain0/cf/en/sc0001.sbl.txt"].replace(
            "What is this?", "\u05d0 \u00ab\u05d1\u00bb"), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            rc = main(["encode", "--text", str(t2), "--glyphset", str(g2dir / "glyphset.json"),
                       "--out", str(Path(td) / "enc2"), "--width-kind", "bank=300"])
        check(rc == 1 and not (Path(td) / "enc2").exists() and "U+00AB" in err.getvalue(),
              "lang-encode refuses a typed character whose byte now draws a new glyph, and writes nothing")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = main(["preview", "--glyphset", str(gdir / "glyphset.json"), "--text", "\u05d0\u05d1 Ab",
                       "--out", str(Path(td) / "p.png"), "--face", "font0"])
        png = (Path(td) / "p.png").read_bytes() if (Path(td) / "p.png").exists() else b""
        if png:
            w, h, gray = fontmod.png_to_gray(png)
        check(rc == 0 and png[:4] == b"\x89PNG" and h == 48 and any(gray),
              "lang-preview draws the line from the rebuilt font")
        # scan: the glyph a battle section's first string would start with is found
        bt = Path(td) / "bt"
        (bt / "chain0/yamamoto").mkdir(parents=True)
        (bt / "chain0/yamamoto/batdat.us.txt").write_text(files["chain0/yamamoto/batdat.us.txt"].replace(
            "chaos", "\u05d3\u05d4").replace("Jin", "\u05d5"), encoding="utf-8")
        ecol = Encoder(cfg, GlyphSet("c"), adr, f.metrics, collect=True, shapers=[SimpleShaper()])
        firsts: set = set()
        run_tree([bt], ecol, firsts)
        check(firsts == {"u:\u05d4"} and ecol.kinds_of["u:\u05d5"] == {"battle"},
              f"scan finds the glyph drawn first in a battle section's first string ({firsts})")
        # hand-drawn cells: a PNG sheet and a key list
        sheet = bytearray(40 * 24)
        for y in range(6, 19):
            for x in range(3, 9):
                sheet[y * 40 + x] = 3
            for x in range(25, 28):
                sheet[y * 40 + x] = 2
        (Path(td) / "cells.png").write_bytes(fontmod.sheet_to_png(sheet, 40, 24))
        pg = from_png(Path(td) / "cells.png", ["u:\u05d0", "u:\u05d1"])
        check(pg.glyphs["u:\u05d0"].bounds == (3, 8) and pg.glyphs["u:\u05d1"].bounds == (5, 7)
              and pg.glyphs["u:\u05d1"].bitmap[10][6] == 2, "a PNG sheet of 20x24 cells is read cell by cell")
        try:
            from glyphs import require_shaping
            require_shaping()
        except GlyphError as ex:
            check(False, str(ex))
            print("self-test FAILED")
            return 1
        # TrueType: a font built in code, shaped, rasterised, allocated and encoded
        ttf = Path(td) / "kittest.ttf"
        build_test_ttf(ttf)
        r2 = TTFRenderer(ttf)
        tcfg = [{"ttf": str(ttf), "script": "Hebr", "rtl": True, "prefer": SINGLE}]
        sh = HBShaper(r2, "Hebr", None)
        tg = GlyphSet("ttf")
        tg.bind(adr)
        ec = Encoder(tcfg, tg, adr, f.metrics, collect=True, shapers=[sh])
        ec.string("\u05d0\u05d1\u05d2 \u05d1")
        keys = [k for k, _ in ec.counts.most_common()]
        for k in keys:
            tg.add(sh.glyph(k))
        tg.allocate(Tiers(adr), keys)
        tf = blank_font()
        tg.apply(tf)
        et = Encoder(tcfg, tg, adr, tf.metrics, shapers=[sh])
        enc_t = et.string("\u05d0\u05d1\u05d2 \u05d1")
        tier_wide = tg.slots[f"g:{r2.name}:{r2.hbfont.get_nominal_glyph(0x05D2)}"]["tier"]
        check(len(keys) == 3 and tier_wide == FIXED and enc_t.count("⟦") == 1 + 1 + 1 + 2 and not et.missing
              and tg.verify(tf, adr) == [],
              f"TrueType path: shaped into {len(keys)} keys, the wide glyph fixed, encoded {enc_t!r}")
    # optional checks with system fonts (never copied into the repository)
    sfa = Path("/System/Library/Fonts/SFArabic.ttf")
    if sfa.exists():
        r3 = TTFRenderer(sfa)
        hs = HBShaper(r3, "Arab", "ar")
        words = "\u0645\u0627 \u0647\u0630\u0627 \u0627\u0644\u0634\u064a\u0621".split()
        cells = [hs.keys(w, True) for w in words]
        joined = [r3.gid_name(int(k.split(":")[2])) for k in cells[0] if k.startswith("g:")]
        check([len(c) for c in cells] == [2, 3, 4] and len(set(cells[1])) == 3,
              f"SFArabic: 'ما هذا الشيء' shapes to 2 + 3 + 4 cells: joining forms, and the font's own "
              f"sheen-yeh final ligature ({joined})")
        lamalef = hs.keys("\u0644\u0627", True)
        check(len(lamalef) == 1, f"SFArabic: lam-alef is one ligature glyph ({lamalef})")
    else:
        print("not run: font not found (/System/Library/Fonts/SFArabic.ttf)")
    koh = Path("/System/Library/Fonts/Kohinoor.ttc")
    if koh.exists():
        r4 = TTFRenderer(koh, face_index=0)
        hk = HBShaper(r4, "Deva", "hi")
        k = hk.keys("\u0915\u094d\u0937", False)              # क्ष
        k2 = hk.keys("\u0939\u093f", False)                    # हि: a vowel sign drawn before its consonant
        check(len(k) == 1, f"Kohinoor: the conjunct क्ष is one key ({k})")
        check(len(k2) == 1 and k2[0].startswith("c:"), f"Kohinoor: हि (ha + i-matra, one cluster under 18 px) is one cell key ({k2})")
    else:
        print("not run: font not found (/System/Library/Fonts/Kohinoor.ttc)")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
