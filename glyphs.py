"""glyphs.py — put a new script's glyphs into Episode II's USA fonts (font0.us, font1.us).

Borrowed from Xenosaga1PythonExtractor/glyphs.py (2026-10-14). The glyph
bitmaps, ``Glyph``, the ``GlyphSet`` idea and its JSON file, the PNG sheet
input (``from_png``) and ``TTFRenderer`` (fontTools outlines, uharfbuzz
shaping, the kit's own scanline rasteriser, four grey levels) are that
module's, ported unchanged in method. The ADDRESSING is rewritten: Episode I
put glyphs into a 256-slot proportional block and 2,992 kanji cells, and
neither exists in the Episode II USA build. Never use Episode I cell numbers
here.

What the USA build can draw (``font.py`` has the code addresses; read from
SLUS_208.92, sha256 1f978e64..., and measured on the USA discs):

* ``font.us_two_byte`` (``FUN_00194dd0``) is the only reading of high bytes.
  A pair is lead A4, A6 or AD with any non-zero trail, A1 with BD DA DB DF F9
  FA FB, or A2 with A2 A3 A4 F6. Every other byte from 0x80 up is ONE
  character, cell = byte + 0x80 (``font.xs2_cell`` with lead A9).
* With ``$prop1;`` a cell below 384 is drawn from its proportional image at
  cell + 576 (``font.PROP_CELL_SHIFT``) and advances right - left + 3 by its
  metric pair (``font + 0x78040 + 2 * cell``); a pair whose left is 0 has no
  width of its own. A cell from 384 up is drawn as it is and advances 19.
  The UI format strings the code builds all set ``$han1;$prop1;``.
* The engine has one font buffer: ``font1.us`` is loaded at init
  (``FUN_00198ff0``) and draws field dialogue, menus and battle; ``font0.us``
  is swapped in while a movie plays (``FUN_001e7d08``) and between cfe.init
  and cfe.destroy (real-time cutscenes). Cells 0-1023 and all 384 pairs are
  byte-identical in the two retail files, so every glyph goes into both.

So the room is three TIERS of codes, all computed here from an
``font.Addressing`` (``Tiers``), never from a table typed in:

    tier    code                    cell                 drawn
    single  one byte 80-FF except   byte + 0x80          proportional
            the pair leads A1 A2      (256-383)
            A4 A6 AD
    pair    A4 tt, tt A6..E0        tt + 0x1F (197-255)  proportional
    fixed   AD tt, tt 80..C0        tt + 0x17F (511-575) 20 px cell, advance 19

A4 trails E1-FF reach the same cells as the single bytes 80-9E: cells are
allocated, and a cell is written with its cheapest code. AD trails above C0
would land on cells 576 and up, which are the proportional images of cells
0-383, and trails below 0x80 would put ASCII-range bytes such as ``[`` ``$``
``\\`` ``%`` into strings that bytewise parsers scan: both are left out by
design (not tried in the game).

Reserved (never allocated): the cells of the executable's ASCII table, the
font's JIS rows 1-3 (retail symbols, digits and letters, A1xx-A3xx), the
retail symbols A4A1-A4A5, the A6 Greek pairs, the fallback cell 0x7F, and
(``retail_reserve``) every cell that the visible retail text of the disc
reaches. On the USA discs that last scan reserves only the lone byte 0xFC
(the ``A1 FC`` marker that ends 190 tutorial lines), which leaves 122 single,
59 pair and 65 fixed codes (measured 2026-11-20; ``Tiers`` recomputes them).

Allocation (``GlyphSet.allocate``): keys most constrained first, then by
frequency. A glyph used in size-limited text (battle names, executable
strings, subtitle lines) takes a single-byte code first; a glyph that would
be the first byte of a battle section's first string may only take a single
byte A1-FE or a pair (``smallfiles.batdat_raw_strings`` finds a text section
by that byte); every other glyph follows ``prefer``. Ink wider than 18 px can
only go to the fixed tier.

Applying (``GlyphSet.apply``): a proportional glyph's ink goes into the image
at cell + 576 at columns left..right, its metric pair is set, and a centred
copy goes into the cell itself (what ``$prop0;`` text draws); a fixed glyph
is centred in its cell. No other cell or pair is touched. ``verify`` reads
every code back through ``Addressing.glyph`` and ``Measurer``.

``glyphset.json``::

    {"format": 1, "build": "us", "exe_sha256": "...", "source": "...",
     "addressing": {"ascii": [96 codes], "sys_widths": [96 widths]},
     "reserved": [cells],
     "slots": {key: {"tier": "pair", "cell": 228, "bytes": "A4C5", "left": 3, "right": 12}},
     "bitmaps": {key: [24 strings of 20 digits 0-3]},
     "advances": {key: px}}

Keys are Episode I's: ``u:<text>`` for text drawn as one unit, ``g:<font>:<gid>``
for one glyph id, ``c:<font>:<gid>@dx,dy;...`` for a cluster drawn as one cell.

    python glyphs.py --self-test
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import font as fontmod                                          # noqa: E402
from font import CELL_H, CELL_W, FIXED_ADVANCE, Font, PROP_CELL_SHIFT   # noqa: E402

SINGLE, PAIR, FIXED = "single", "pair", "fixed"
TIERS = (SINGLE, PAIR, FIXED)
PAIR_LEAD, FIXED_LEAD = 0xA4, 0xAD
MAX_RIGHT = 18                  # the image is drawn from left - 1 to right + 1; keep right + 1 inside the cell
MAX_PROP_INK = MAX_RIGHT        # ink columns 1..18
PREFER = {SINGLE: (SINGLE, PAIR, FIXED), PAIR: (PAIR, SINGLE, FIXED), FIXED: (FIXED, PAIR, SINGLE)}
FORMAT = 1


class GlyphError(ValueError):
    pass


def battle_first_ok(code: bytes) -> bool:
    """May ``code`` be the first bytes of a battle section's first string?
    ``smallfiles.batdat_raw_strings`` takes a section as text only when that byte
    is 0x20-0x7E or 0xA1-0xFE."""
    return 0x20 <= code[0] < 0x7F or 0xA1 <= code[0] <= 0xFE


# --- tiers --------------------------------------------------------------------

def base_reserved(adr: "fontmod.Addressing") -> Dict[int, str]:
    """Cells never given out whatever the disc's text: cell -> reason."""
    out: Dict[int, str] = {}

    def put(cell, why):
        out.setdefault(cell, why)

    for b, code in enumerate(adr.ascii, 0x20):
        put(adr.cell(code >> 8, code & 0xFF), f"ASCII {chr(b)!r} through the executable's $han table")
    for lead, row in ((0xA1, 1), (0xA2, 2), (0xA3, 3)):
        for t in range(0xA1, 0xFF):
            c = adr.cell(lead, t)
            if c != fontmod.FALLBACK_CELL:
                put(c, f"the font's JIS row {row} (retail symbols, digits and letters)")
    for t in range(0xA1, 0xA6):
        put(adr.cell(0xA4, t), f"retail symbol A4{t:02X}")
    import textcodec
    for piece in textcodec.UsCodec.PAIRS:
        if piece[0] == 0xA6:
            put(adr.cell(0xA6, piece[1]), f"Greek {piece.decode('euc_jis_2004')} (A6{piece[1]:02X})")
    put(fontmod.FALLBACK_CELL, "the fallback cell")
    return out


class Tiers:
    """The codes the USA print function can reach for new glyphs, per cell."""

    def __init__(self, adr: "fontmod.Addressing", reserved: Optional[Dict[int, str]] = None):
        if not adr.latin1:
            raise GlyphError(f"the {adr.region} build reads every byte from 0xA0 up as a lead byte; glyph "
                             f"allocation is written for the USA build only")
        self.adr = adr
        self.leads = sorted(b for b in range(0x80, 0x100) if any(adr.two_byte(b, t) for t in range(1, 0x100)))
        self.base = base_reserved(adr)
        self.reserved: Dict[int, str] = dict(self.base)
        if reserved:
            for c, why in reserved.items():
                self.reserved.setdefault(c, why)
        codes: Dict[int, List[bytes]] = {}
        images = range(PROP_CELL_SHIFT, PROP_CELL_SHIFT + adr.prop_cells)
        for b in range(0x80, 0x100):                      # single bytes
            if b in self.leads:
                continue
            g = adr.glyph(bytes((b, 0x20)), 0, han=True)
            if g.length == 1 and g.cell is not None and adr.metric_slot(g.cell) is not None:
                codes.setdefault(g.cell, []).append(bytes((b,)))
        for t in range(0x80, 0x100):                      # A4 pairs (proportional) and AD pairs (fixed)
            for lead in (PAIR_LEAD, FIXED_LEAD):
                code = bytes((lead, t))
                g = adr.glyph(code + b"\x00", 0, han=True)
                if g.length != 2 or g.cell is None or not adr.reachable(g.cell):
                    continue
                prop = adr.metric_slot(g.cell) is not None
                if lead == PAIR_LEAD and prop or lead == FIXED_LEAD and not prop and g.cell not in images:
                    codes.setdefault(g.cell, []).append(code)
        self.all_codes = {c: sorted(v, key=len) for c, v in codes.items()}

    def code(self, cell: int) -> bytes:
        return self.all_codes[cell][0]

    def tier_of(self, cell: int) -> str:
        code = self.code(cell)
        if len(code) == 1:
            return SINGLE
        return PAIR if self.adr.metric_slot(cell) is not None else FIXED

    def cells(self, tier: str, include_reserved: bool = False) -> List[int]:
        return sorted(c for c in self.all_codes if self.tier_of(c) == tier
                      and (include_reserved or c not in self.reserved))

    def counts(self, used: Iterable[int] = ()) -> Dict[str, Tuple[int, int]]:
        """tier -> (used, free) among the cells not reserved."""
        used = set(used)
        out = {}
        for t in TIERS:
            cs = self.cells(t)
            out[t] = (sum(1 for c in cs if c in used), sum(1 for c in cs if c not in used))
        return out


def _sjis_reading(s: bytes) -> str:
    """The Shift-JIS reading of ``s`` when it decodes and has a non-ASCII character, else ''.
    This was textpack's ``_sjis_hint`` when the reserve was computed; textpack's hint is now
    stricter (``_jp_hint``), and the reserve keeps this rule so the room stays the same."""
    try:
        t = s.decode("shift_jis")
    except UnicodeDecodeError:
        return ""
    return t if any(ord(c) > 0x7F for c in t) else ""


def visible_retail(s: bytes) -> bool:
    """False for the strings the retail reserve leaves out as developer lines the
    player never sees: those that read as Shift-JIS (``_sjis_reading``) and those that
    decode as EUC-JP with kana or kanji other than the five retail symbols A4A1-A4A5."""
    if _sjis_reading(s):
        return False
    cleaned = s
    for t in range(0xA1, 0xA6):
        cleaned = cleaned.replace(bytes((0xA4, t)), b"")
    try:
        text = cleaned.decode("euc_jis_2004")
    except UnicodeDecodeError:
        return True
    return not any(0x3040 <= ord(c) <= 0x30FF or 0x3400 <= ord(c) <= 0x9FFF for c in text)


def retail_reserve(iso_path: str, adr: "fontmod.Addressing", candidates: Set[int]
                   ) -> Dict[int, Tuple[int, str]]:
    """cell -> (uses, an example) for every candidate cell that the visible retail
    text of the disc reaches: every string of the kinds bank, menu and battle and every
    subtitle cue line, skipping strings that read as Shift-JIS (``_sjis_reading``,
    developer lines) and strings that decode as EUC-JP with kana or kanji other than
    the five retail symbols A4A1-A4A5."""
    import sbl
    import smallfiles
    from iso9660 import IsoImage
    from textpack import _objects, txd_parse

    out: Dict[int, Tuple[int, str]] = {}
    iso = IsoImage(iso_path)
    try:
        for chain, e, kind, stored, data, alloc, sizes in _objects(iso):
            try:
                if kind.name == "bank":
                    strs = sbl.parse(data)
                elif kind.name == "menu":
                    strs = txd_parse(data)[0]
                elif kind.name == "battle":
                    strs = [r[2] for r in smallfiles.batdat_raw_strings(data)]
                elif kind.name == "subtitle":
                    strs = [ln for ln in data.split(b"\n") if not ln.startswith(b";")]
                else:
                    continue
            except Exception:                          # an object the kit cannot read is not text
                continue
            for s in strs:
                if not visible_retail(s):
                    continue
                i = 0
                while i < len(s):
                    if s[i] < 0x80:
                        i += 1
                        continue
                    g = adr.glyph(s + b"\x00", i, han=True)
                    if g.cell in candidates:
                        n, ex = out.get(g.cell, (0, ""))
                        out[g.cell] = (n + 1, ex or f"{kind.name} {e.path}: {s[max(0, i - 30):i + 10]!r}")
                    i += g.length
    finally:
        iso.close()
    return out


# --- bitmaps ---------------------------------------------------------------------

Bitmap = List[List[int]]        # CELL_H rows of CELL_W values 0..3


def blank() -> Bitmap:
    return [[0] * CELL_W for _ in range(CELL_H)]


def ink_bounds(bm: Bitmap) -> Optional[Tuple[int, int]]:
    cols = [x for x in range(CELL_W) if any(row[x] for row in bm)]
    return (cols[0], cols[-1]) if cols else None


def shift_x(bm: Bitmap, dx: int) -> Bitmap:
    out = blank()
    for y in range(CELL_H):
        for x in range(CELL_W):
            if 0 <= x - dx < CELL_W:
                out[y][x] = bm[y][x - dx]
    return out


def centred(bm: Bitmap) -> Bitmap:
    b = ink_bounds(bm)
    if b is None:
        return bm
    return shift_x(bm, (CELL_W - (b[1] - b[0] + 1)) // 2 - b[0])


class Glyph:
    def __init__(self, key: str, bitmap: Bitmap, advance_hint: Optional[int] = None):
        self.key = key
        self.bitmap = bitmap
        self.bounds = ink_bounds(bitmap)
        self.advance_hint = advance_hint    # for blank glyphs (spaces), from the shaper
        self.narrowed = 1.0                 # < 1: drawn narrower than the font so it fits the cell
        self.cut = 0                        # ink pixels (of the unclipped drawing) outside the cell

    @property
    def ink_width(self) -> int:
        return self.bounds[1] - self.bounds[0] + 1 if self.bounds else 0

    @property
    def clipped(self) -> bool:
        """Ink on the cell's outer edge: part of the glyph may have been cut off."""
        bm = self.bitmap
        return bool(any(bm[0]) or any(bm[-1]) or self.bounds and (self.bounds[0] == 0 and self.bounds[1] == CELL_W - 1))


# --- allocation ------------------------------------------------------------------

class GlyphSet:
    def __init__(self, source: str = ""):
        self.source = source
        self.glyphs: Dict[str, Glyph] = {}
        self.slots: Dict[str, dict] = {}     # key -> {tier, cell, bytes, left, right[, tight]}
        self.reserved: List[int] = []
        self.exe_sha256 = ""
        self.ascii: Tuple[int, ...] = ()
        self.sys_widths: Tuple[int, ...] = ()

    def add(self, g: Glyph) -> None:
        self.glyphs[g.key] = g

    def bind(self, adr: "fontmod.Addressing", exe_sha256: str = "") -> None:
        """Record the build's ASCII and width tables so encode and preview need no disc."""
        self.ascii, self.sys_widths = tuple(adr.ascii), tuple(adr.sys_widths)
        self.exe_sha256 = exe_sha256 or self.exe_sha256

    def addressing(self) -> "fontmod.Addressing":
        if len(self.ascii) != 96:
            raise GlyphError("this glyphset holds no ASCII table (made without a disc?)")
        return fontmod.Addressing("us", tuple(self.ascii), (), sys_widths=tuple(self.sys_widths), **fontmod._BUILDS["us"])

    def allocate(self, tiers: Tiers, order: Sequence[str], limited: Iterable[str] = (),
                 battle_first: Iterable[str] = (), prefer: str = SINGLE, prop_only: bool = False,
                 tight: bool = False) -> None:
        """Give every key in ``order`` (most frequent first) a cell. ``limited`` keys
        appear in size-limited text and take a single-byte code first; ``battle_first``
        keys may only take a code whose first byte a battle section may start with.
        Keys already placed keep their place. All refusals are collected and raised
        together."""
        limited, battle_first = set(limited), set(battle_first)
        if prefer not in PREFER:
            raise GlyphError(f"prefer must be one of {', '.join(PREFER)}")
        used = {s["cell"] for s in self.slots.values()}
        clash = sorted(c for c in used if c in tiers.reserved)
        if clash:
            raise GlyphError(f"cells {clash[:8]} of the existing glyphset are now reserved "
                             f"({tiers.reserved[clash[0]]}); use --reclaim or start a new glyphset")
        font_rank = getattr(tiers, "rank", None) or (lambda c: 0)
        free = {t: sorted((c for c in tiers.cells(t) if c not in used), key=lambda c: (font_rank(c), c))
                for t in TIERS}
        new = [k for k in order if k not in self.slots]
        for k in new:
            if k not in self.glyphs:
                raise GlyphError(f"no bitmap for key {k!r}")

        def rank(k):
            g = self.glyphs[k]
            return (0 if k in battle_first else 1 if g.ink_width > MAX_PROP_INK else 2 if k in limited else 3)
        refused = []
        for k in [k for r in range(4) for k in new if rank(k) == r]:
            g = self.glyphs[k]
            if g.ink_width > MAX_PROP_INK:
                if prop_only:
                    refused.append(f"{k}: ink {g.ink_width} px wide, the proportional tiers hold 18 (--prop-only)")
                    continue
                want = [FIXED]
            else:
                want = list(PREFER[prefer])
                if k in limited:
                    want = [SINGLE] + [t for t in want if t != SINGLE]
                if prop_only:
                    want = [t for t in want if t != FIXED]
            cell = None
            for t in want:
                pool = free[t]
                for c in pool:
                    if k in battle_first and not battle_first_ok(tiers.code(c)):
                        continue
                    cell = c
                    break
                if cell is not None:
                    pool.remove(cell)
                    break
            if cell is None:
                refused.append(f"{k}: no free cell in tier(s) {', '.join(want)}")
                continue
            tier = tiers.tier_of(cell)
            slot = {"tier": tier, "cell": cell, "bytes": tiers.code(cell).hex().upper()}
            if tier == FIXED:
                slot.update(left=0, right=FIXED_ADVANCE)
            else:
                left, right, t = self._prop_metrics(g, tight)
                slot.update(left=left, right=right)
                if t:
                    slot["tight"] = True
            self.slots[k] = slot
        if refused:
            raise GlyphError(f"{len(refused)} glyph(s) got no cell: " + "; ".join(refused[:12])
                             + (" ..." if len(refused) > 12 else ""))

    @staticmethod
    def _prop_metrics(g: Glyph, tight: bool = False) -> Tuple[int, int, bool]:
        """(left, right, tight) of a proportional glyph. The engine reads a left of 0 as
        "no width", so left is at least 1; the image is drawn from left - 1 to right + 1,
        so right is at most 18. Normally the ink sits at left..right, as in every retail
        pair (advance = ink + 2). ``tight`` puts the ink at left - 1..right + 1 instead
        (advance = ink width, so joining letters touch); that edge reading is from the
        code and has not been seen in the game."""
        if g.bounds is None:
            adv = g.advance_hint if g.advance_hint else 6
            right = max(1, min(MAX_RIGHT, adv - 2))
            return 1, right, False
        w = g.ink_width
        if tight and w >= 3:
            s = max(0, min(g.bounds[0], CELL_W - w))
            return s + 1, s + w - 2, True
        left = max(1, min(g.bounds[0], MAX_RIGHT - w + 1))
        return left, left + w - 1, False

    def advance(self, key: str, prop_pad: int = 3) -> int:
        s = self.slots[key]
        return FIXED_ADVANCE if s["tier"] == FIXED else s["right"] - s["left"] + prop_pad

    def code_bytes(self, key: str) -> bytes:
        return bytes.fromhex(self.slots[key]["bytes"])

    # applying
    def apply(self, font: Font, keys: Optional[Iterable[str]] = None) -> None:
        """Draw placed glyphs into ``font``: proportional ones into cell + 576 at their
        metric columns (pair set) and centred into the cell; fixed ones centred."""
        for key in (self.slots if keys is None else keys):
            s = self.slots[key]
            g = self.glyphs[key]
            bm = g.bitmap
            cell = s["cell"]
            if s["tier"] == FIXED:
                font.set_cell(cell, centred(bm))
                continue
            x = s["left"] - 1 if s.get("tight") else s["left"]
            image = shift_x(bm, x - g.bounds[0]) if g.bounds else blank()
            font.set_cell(cell + PROP_CELL_SHIFT, image)
            font.set_metrics(cell, s["left"], s["right"])
            font.set_cell(cell, centred(bm))

    def verify(self, font: Font, adr: "fontmod.Addressing") -> List[str]:
        """Problems (empty = good): every code reads back to its cell through the print
        function's reading, the drawn cells hold the glyph, and the width function gives
        the advance the encoder assumed."""
        bad = []
        for key, s in self.slots.items():
            code = self.code_bytes(key)
            g = adr.glyph(code + b"\x00", 0, han=True)
            if g.length != len(code) or g.cell != s["cell"]:
                bad.append(f"{key}: {s['bytes']} reads as {g.length} byte(s), cell {g.cell}, not cell {s['cell']}")
                continue
            prop = s["tier"] != FIXED
            if (adr.metric_slot(s["cell"]) is not None) != prop:
                bad.append(f"{key}: cell {s['cell']} is not drawn the way its tier {s['tier']} says")
            glyph = self.glyphs.get(key)
            if glyph is not None and glyph.bounds is not None:
                for p in (True, False):
                    c = adr.drawn_cell(s["cell"], p)
                    if font.ink_bounds(c) is None:
                        bad.append(f"{key}: cell {c} ({'$prop1' if p else '$prop0'}) is blank")
                if prop:
                    ib = font.ink_bounds(s["cell"] + PROP_CELL_SHIFT)
                    lo, hi = (s["left"] - 1, s["right"] + 1) if s.get("tight") else (s["left"], s["right"])
                    if ib is None or ib[0] < lo or ib[1] > hi:
                        bad.append(f"{key}: the image's ink {ib} is outside columns {lo}..{hi}")
            if prop and font.metrics[s["cell"]] != (s["left"], s["right"]):
                bad.append(f"{key}: pair {font.metrics[s['cell']]} in the font, ({s['left']}, {s['right']}) in the glyphset")
            w = adr.measurer(font.metrics, han=True, prop=True, xx1=8).width(code)
            if w != self.advance(key, adr.prop_pad):
                bad.append(f"{key}: the width function gives {w}, the glyphset {self.advance(key, adr.prop_pad)}")
        return bad

    # persistence
    def to_json(self) -> str:
        return json.dumps({
            "format": FORMAT, "build": "us", "exe_sha256": self.exe_sha256, "source": self.source,
            "addressing": {"ascii": list(self.ascii), "sys_widths": list(self.sys_widths)},
            "reserved": sorted(self.reserved),
            "slots": self.slots,
            "bitmaps": {k: ["".join(str(v) for v in row) for row in g.bitmap] for k, g in self.glyphs.items()},
            "advances": {k: g.advance_hint for k, g in self.glyphs.items() if g.advance_hint is not None},
        }, ensure_ascii=False, indent=0, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "GlyphSet":
        d = json.loads(text)
        if d.get("format") != FORMAT or d.get("build") != "us":
            raise GlyphError(f"not a glyphset of format {FORMAT} for the USA build "
                             f"(format {d.get('format')}, build {d.get('build')})")
        gs = cls(d.get("source", ""))
        gs.exe_sha256 = d.get("exe_sha256", "")
        ad = d.get("addressing", {})
        gs.ascii, gs.sys_widths = tuple(ad.get("ascii", ())), tuple(ad.get("sys_widths", ()))
        gs.reserved = list(d.get("reserved", []))
        adv = d.get("advances", {})
        for k, rows in d.get("bitmaps", {}).items():
            gs.add(Glyph(k, [[int(ch) for ch in row] for row in rows], adv.get(k)))
        for k, v in d.get("slots", {}).items():
            if v.get("tier") not in TIERS or k not in gs.glyphs:
                raise GlyphError(f"glyphset slot {k!r} is damaged (tier {v.get('tier')}, bitmap "
                                 f"{'present' if k in gs.glyphs else 'missing'})")
            gs.slots[k] = dict(v)
        return gs

    def save(self, path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "GlyphSet":
        try:
            return cls.from_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            if isinstance(e, GlyphError):
                raise
            raise GlyphError(f"{path}: {e}")


# --- PNG cells -------------------------------------------------------------------

def from_png(png_path, keys: Iterable[str], source: str = "") -> GlyphSet:
    """Cells read left-to-right, top-to-bottom from a 20x24-grid PNG."""
    w, h, gray = fontmod.png_to_gray(Path(png_path).read_bytes())
    if w % CELL_W or h % CELL_H:
        raise GlyphError(f"sheet {w}x{h} is not a multiple of {CELL_W}x{CELL_H}")
    q = fontmod.quantize(gray)
    per_row = w // CELL_W
    gs = GlyphSet(source or str(png_path))
    for i, key in enumerate(keys):
        cx, cy = (i % per_row) * CELL_W, (i // per_row) * CELL_H
        if cy + CELL_H > h:
            raise GlyphError("more keys than cells in the sheet")
        bm = [list(q[(cy + r) * w + cx:(cy + r) * w + cx + CELL_W]) for r in range(CELL_H)]
        gs.add(Glyph(key, bm))
    return gs


# --- TrueType rendering --------------------------------------------------------------

def require_shaping():
    """fontTools and uharfbuzz: required by the kit (requirements.txt)."""
    try:
        import fontTools                     # noqa: F401
        import uharfbuzz                     # noqa: F401
    except ImportError as ex:
        raise GlyphError(f"{ex.name} is missing; the kit requires fontTools and uharfbuzz: "
                         f"python -m pip install -r requirements.txt") from ex


class TTFRenderer:
    """Shape text with HarfBuzz and rasterise glyph outlines into cells.

    ``px`` is the em size in pixels, ``baseline`` the cell row the baseline sits
    on. The defaults (18, 19) put a Latin capital on rows 6-18, where the retail
    capitals are (measured on font1.us: 'A' inks rows 6-18, 'x' rows 11-18)."""

    def __init__(self, path, px: float = 18.0, baseline: int = 19,
                 face_index: int = 0, features: Optional[dict] = None,
                 supersample: int = 4):
        require_shaping()
        from fontTools.ttLib import TTCollection, TTFont
        import uharfbuzz as hb
        self.hb = hb
        p = Path(path)
        data = p.read_bytes()
        if data[:4] == b"ttcf":
            self.tt = TTCollection(str(p)).fonts[face_index]
        else:
            self.tt = TTFont(str(p))
        self.upem = self.tt["head"].unitsPerEm
        self.glyph_set = self.tt.getGlyphSet()
        self.glyph_order = self.tt.getGlyphOrder()
        face = hb.Face(data, face_index)
        self.hbfont = hb.Font(face)
        self.hbfont.scale = (self.upem, self.upem)
        self.px = px
        self.baseline = baseline
        # one cell per glyph: keep required ligatures (lam-alef, conjuncts)
        # but not the optional wide ones a 20 px cell cannot hold
        self.features = {"liga": False, "dlig": False, "calt": False} if features is None else features
        self.ss = supersample
        self.name = p.stem.replace(":", "_") + (f"-{face_index}" if face_index else "")
        self.scale = px / self.upem
        self._contours: Dict[int, list] = {}

    # shaping
    def shape(self, text: str, direction: str = "ltr", script: Optional[str] = None,
              language: Optional[str] = None):
        """-> list of (gid, cluster, x_advance, x_offset, y_offset) in font units."""
        hb = self.hb
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        buf.direction = direction
        if script:
            buf.script = script
        if language:
            buf.language = language
        hb.shape(self.hbfont, buf, self.features)
        return [(i.codepoint, i.cluster, p.x_advance, p.x_offset, p.y_offset)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]

    def gid_name(self, gid: int) -> str:
        return self.glyph_order[gid]

    def advance_px(self, gid: int) -> float:
        return self.hbfont.get_glyph_h_advance(gid) * self.scale

    # outlines
    def contours(self, gid: int) -> List[List[Tuple[float, float]]]:
        """Flattened closed contours in font units."""
        if gid in self._contours:
            return self._contours[gid]
        from fontTools.pens.recordingPen import DecomposingRecordingPen
        pen = DecomposingRecordingPen(self.glyph_set)   # flattens TrueType components
        self.glyph_set[self.gid_name(gid)].draw(pen)
        out: List[List[Tuple[float, float]]] = []
        cur: List[Tuple[float, float]] = []
        last = (0.0, 0.0)
        for op, args in pen.value:
            if op == "moveTo":
                if cur:
                    out.append(cur)
                cur = [args[0]]
                last = args[0]
            elif op == "lineTo":
                cur.append(args[0])
                last = args[0]
            elif op == "qCurveTo":
                pts = list(args)
                if pts[-1] is None:          # TrueType implied on-curve start
                    pts = pts[:-1]
                ctrls = pts[:-1]
                end = pts[-1]
                p0 = last
                for i, c in enumerate(ctrls):
                    nxt = ctrls[i + 1] if i + 1 < len(ctrls) else end
                    p2 = nxt if i + 1 == len(ctrls) else ((c[0] + nxt[0]) / 2, (c[1] + nxt[1]) / 2)
                    for t in range(1, 9):
                        u = t / 8
                        x = (1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * c[0] + u * u * p2[0]
                        y = (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * c[1] + u * u * p2[1]
                        cur.append((x, y))
                    p0 = p2
                last = end
            elif op == "curveTo":
                c1, c2, end = args[-3], args[-2], args[-1]
                p0 = last
                for t in range(1, 9):
                    u = t / 8
                    x = ((1 - u) ** 3 * p0[0] + 3 * (1 - u) ** 2 * u * c1[0]
                         + 3 * (1 - u) * u * u * c2[0] + u ** 3 * end[0])
                    y = ((1 - u) ** 3 * p0[1] + 3 * (1 - u) ** 2 * u * c1[1]
                         + 3 * (1 - u) * u * u * c2[1] + u ** 3 * end[1])
                    cur.append((x, y))
                last = end
            elif op in ("closePath", "endPath"):
                if cur:
                    out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        self._contours[gid] = out
        return out

    # rasterising
    def raster(self, placements: Sequence[Tuple[int, float, float]],
               x_origin: Optional[float] = None) -> Bitmap:
        """Draw glyphs (gid, x_px, y_px offsets from the pen) into one cell.
        Even-odd scanline fill at ``supersample`` x resolution, then averaged
        to the four levels the font supports. With no ``x_origin`` the leftmost
        ink is put at column 1 (the ink is moved to its metric columns later),
        and ink wider than the 19 columns from there to the cell's edge (a wide
        ligature such as SF Arabic's sheen+yeh final, 25 px at 18 px/em) is
        drawn narrower, so nothing is cut off; ``last_fit`` is that factor.
        ``last_cut`` counts the ink pixels that still fall outside the cell
        (above or below it, or sideways with an ``x_origin``)."""
        sx = 1.0
        xmin = 0.0
        if x_origin is None:
            xs = [dx + x * self.scale for gid, dx, dy in placements for c in self.contours(gid) for x, _ in c]
            xmin = min(xs) if xs else 0.0
            extent = (max(xs) - xmin) if xs else 0.0
            room = CELL_W - 1.0 - 0.05          # columns 1..19, the edge itself left free
            if extent > room:
                sx = room / extent
            x_origin = 1.0
        self.last_fit = sx
        ss = self.ss
        W, H = CELL_W * ss, CELL_H * ss
        # a canvas with room around the cell, to count what falls outside it
        mx, my = W, H // 2
        BW, BH = W + 2 * mx, H + 2 * my
        cov = [[0] * BW for _ in range(BH)]
        edges = []
        for gid, dx, dy in placements:
            for contour in self.contours(gid):
                pts = [((x_origin + sx * (dx + x * self.scale - xmin)) * ss + mx,
                        (self.baseline - dy - y * self.scale) * ss + my) for x, y in contour]
                for i in range(len(pts)):
                    x0, y0 = pts[i]
                    x1, y1 = pts[(i + 1) % len(pts)]
                    if y0 != y1:
                        edges.append((x0, y0, x1, y1))
        for sy in range(BH):
            yc = sy + 0.5
            xs = []
            for x0, y0, x1, y1 in edges:
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                a, b = int(math.floor(xs[i] + 0.5)), int(math.floor(xs[i + 1] + 0.5))
                for px in range(max(a, 0), min(b, BW)):
                    cov[sy][px] = 1
        bm = blank()
        n = ss * ss
        cut = 0
        for y in range(BH // ss):
            rows = cov[y * ss:(y + 1) * ss]
            if not any(any(r) for r in rows):
                continue
            col = [sum(r[i] for r in rows) for i in range(BW)]
            for x in range(BW // ss):
                s = sum(col[x * ss:(x + 1) * ss])
                level = min(3, (s * 4 + n // 2) // n) if s else 0
                cx, cy = x - mx // ss, y - my // ss
                if 0 <= cx < CELL_W and 0 <= cy < CELL_H:
                    bm[cy][cx] = level
                elif level:
                    cut += 1
        self.last_cut = cut
        return bm

    def _glyph(self, key: str, bm: Bitmap, advance: Optional[int]) -> Glyph:
        g = Glyph(key, bm, advance)
        g.narrowed, g.cut = self.last_fit, self.last_cut
        return g

    def render_gid(self, gid: int) -> Glyph:
        bm = self.raster([(gid, 0.0, 0.0)])
        return self._glyph(f"g:{self.name}:{gid}", bm, round(self.advance_px(gid)))

    def render_cluster(self, parts: Sequence[Tuple[int, float, float]], key: str,
                       advance: Optional[int] = None) -> Glyph:
        return self._glyph(key, self.raster(parts), advance)

    def render_text(self, text: str, key: str, direction: str = "ltr",
                    script: Optional[str] = None) -> Glyph:
        """Shape ``text`` as one unit and draw it into one cell."""
        pen = 0.0
        parts = []
        for gid, _, adv, xo, yo in self.shape(text, direction, script):
            parts.append((gid, (pen + xo) * self.scale, yo * self.scale))
            pen += adv
        return self._glyph(key, self.raster(parts), round(pen * self.scale))


# --- sheet preview ------------------------------------------------------------------

def sheet_preview(font: Font, cells: Iterable[int], sheet: int = 0) -> bytes:
    """RGB PNG of one sheet with the given cells (and, for proportional cells, their
    images) outlined in red."""
    import struct
    import zlib
    cells = set(cells)
    W, H = fontmod.SHEET_W, fontmod.SHEET_H
    levels = (0, 85, 170, 255)
    px = bytearray()
    sh = font.sheets[sheet]
    for v in sh:
        g = levels[v & 3]
        px += bytes((g, g, g))
    for c in cells:
        if c // fontmod.CELLS_PER_SHEET != sheet:
            continue
        i = c % fontmod.CELLS_PER_SHEET
        x0, y0 = (i % fontmod.CELLS_PER_ROW) * CELL_W, (i // fontmod.CELLS_PER_ROW) * CELL_H
        for x in range(x0, x0 + CELL_W):
            for y in (y0, y0 + CELL_H - 1):
                px[(y * W + x) * 3:(y * W + x) * 3 + 3] = b"\xe0\x30\x30"
        for y in range(y0, y0 + CELL_H):
            for x in (x0, x0 + CELL_W - 1):
                px[(y * W + x) * 3:(y * W + x) * 3 + 3] = b"\xe0\x30\x30"
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        raw += px[y * W * 3:(y + 1) * W * 3]
    return (b"\x89PNG\r\n\x1a\n"
            + fontmod._png_chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + fontmod._png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + fontmod._png_chunk(b"IEND", b""))


# --- self-test -----------------------------------------------------------------------

def synthetic_addressing() -> "fontmod.Addressing":
    """A USA Addressing from a synthetic executable (as textcodec's self-test builds it)."""
    import region
    return fontmod.Addressing.from_executable(
        fontmod._synthetic_elf(0x2B1100, 0x2B1200, ()),
        region.Region("us", "en", "sbl", (), "", "", ascii_table_va=0x2B1100, kanji_list_va=0x2B1200))


def blank_font(pairs: int = 384) -> Font:
    return Font([bytes(fontmod.HEADER)] * 2, [bytearray(fontmod.SHEET_W * fontmod.SHEET_H) for _ in range(4)],
                [(0, 19)] * pairs)


def box(x0: int, x1: int, y0: int = 6, y1: int = 18) -> Bitmap:
    bm = blank()
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            bm[y][x] = 3
    return bm


def changed_cells(a: Font, b: Font) -> Set[int]:
    out = set()
    for s in range(4):
        if a.sheets[s] == b.sheets[s]:
            continue
        sa, sb = a.sheets[s], b.sheets[s]
        for p in range(len(sa)):
            if sa[p] != sb[p]:
                y, x = divmod(p, fontmod.SHEET_W)
                out.add(s * fontmod.CELLS_PER_SHEET + (y // CELL_H) * fontmod.CELLS_PER_ROW + x // CELL_W)
    return out


def build_test_ttf(path) -> Dict[str, str]:
    """A TrueType font built in code: a square, a triangle, a wide bar (21.6 px at
    18 px/em, wider than a cell) and a tall bar (36 px, taller than a cell) mapped
    to four Hebrew letters, plus a space. -> {letter: glyph name}."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    def poly(points):
        pen = TTGlyphPen(None)
        if points:
            pen.moveTo(points[0])
            for p in points[1:]:
                pen.lineTo(p)
            pen.closePath()
        return pen.glyph()
    upem = 1000
    shapes = {".notdef": [(50, 0), (50, 700), (450, 700), (450, 0)], "space": [],
              "sq": [(100, 0), (100, 600), (600, 600), (600, 0)],
              "tri": [(100, 0), (350, 700), (600, 0)],
              "wide": [(50, 0), (50, 300), (1250, 300), (1250, 0)],
              "tall": [(100, -600), (100, 1400), (300, 1400), (300, -600)]}
    adv = {".notdef": 500, "space": 400, "sq": 700, "tri": 700, "wide": 1300, "tall": 400}
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(list(shapes))
    fb.setupCharacterMap({0x20: "space", 0x05D0: "sq", 0x05D1: "tri", 0x05D2: "wide", 0x05D3: "tall"})
    fb.setupGlyf({n: poly(p) for n, p in shapes.items()})
    fb.setupHorizontalMetrics({n: (adv[n], 50) for n in shapes})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "KitTest", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))
    return {"א": "sq", "ב": "tri", "ג": "wide", "ד": "tall"}


def self_test() -> int:
    import tempfile
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    adr = synthetic_addressing()
    t = Tiers(adr)
    n = {k: len(t.cells(k)) for k in TIERS}
    check(t.leads == [0xA1, 0xA2, 0xA4, 0xA6, 0xAD], f"pair leads read from us_two_byte: {[f'{b:02X}' for b in t.leads]}")
    check(n == {SINGLE: 123, PAIR: 59, FIXED: 65}, f"tiers before the retail reserve: {n} (123/59/65)")
    t2 = Tiers(adr, {0xFC + 0x80: "retail A1 FC marker"})
    n2 = {k: len(t2.cells(k)) for k in TIERS}
    check(n2 == {SINGLE: 122, PAIR: 59, FIXED: 65}, f"with the lone byte FC reserved: {n2} (122/59/65)")
    every = all(adr.glyph(t.code(c) + b"\x00", 0).cell == c and adr.glyph(t.code(c) + b"\x00", 0).length == len(t.code(c))
                for c in t.all_codes)
    check(every, f"every one of {len(t.all_codes)} codes reads back to its cell through Addressing.glyph")
    c80 = adr.glyph(b"\x80\x00", 0).cell
    check(adr.glyph(b"\xa4\xe1", 0).cell == c80 == 256 and t.code(c80) == b"\x80",
          "A4 E1 and byte 0x80 reach the same cell (256); the cheapest code, the single byte, is the one used")
    check(all(not (len(c) == 1 and c[0] in (0xA1, 0xA2, 0xA4, 0xA6, 0xAD)) for v in t.all_codes.values() for c in v),
          "no code uses A1 A2 A4 A6 AD as a lone byte")
    check(min(t.cells(PAIR)) == 197 and max(t.cells(PAIR)) == 255 and t.code(197) == b"\xa4\xa6"
          and t.cells(FIXED)[0] == 511 and t.cells(FIXED)[-1] == 575 and t.code(575) == b"\xad\xc0",
          "pair tier = A4A6..A4E0 (cells 197-255), fixed tier = AD80..ADC0 (cells 511-575)")
    check(all(c < PROP_CELL_SHIFT for c in t.cells(FIXED)), "no fixed cell is a proportional image (576+)")

    # allocation and apply on a blank font
    gs = GlyphSet("test")
    gs.bind(adr, "0" * 64)
    gs.add(Glyph("u:a", box(3, 9)))                       # 7 px
    gs.add(Glyph("u:b", box(0, 4)))                       # starts at column 0: moved to 1
    gs.add(Glyph("u:w", box(0, 19)))                      # 20 px: fixed only
    gs.add(Glyph("u:s", blank(), advance_hint=9))         # a blank glyph
    gs.add(Glyph("u:f", box(5, 8)))                       # a battle section's first glyph
    gs.add(Glyph("u:l", box(2, 6)))                       # size-limited
    t3 = Tiers(adr)
    gs.allocate(t3, ["u:a", "u:b", "u:w", "u:s", "u:l", "u:f"], limited={"u:l", "u:f"}, battle_first={"u:f"},
                prefer=PAIR)
    sl = gs.slots
    check(sl["u:w"]["tier"] == FIXED and sl["u:a"]["tier"] == PAIR and sl["u:l"]["tier"] == SINGLE,
          "a 20 px glyph goes to fixed, prefer=pair is followed, a size-limited glyph gets a single byte")
    check(sl["u:f"]["tier"] == SINGLE and battle_first_ok(gs.code_bytes("u:f")) and gs.code_bytes("u:f")[0] >= 0xA1,
          f"a battle section-first glyph never gets 80-A0 (got {sl['u:f']['bytes']})")
    check(sl["u:b"]["left"] == 1 and sl["u:b"]["right"] == 5 and sl["u:s"]["left"] == 1 and sl["u:s"]["right"] == 7,
          "ink at column 0 moves to left 1; a blank glyph gets left 1 and the shaper's advance (9 = 7 - 1 + 3)")
    try:
        g2 = GlyphSet()
        g2.add(Glyph("u:w", box(0, 19)))
        g2.allocate(Tiers(adr), ["u:w"], prop_only=True)
        check(False, "--prop-only refuses a glyph wider than 18 px")
    except GlyphError as e:
        check("prop-only" in str(e), "--prop-only refuses a glyph wider than 18 px")
    base = blank_font()
    f = blank_font()
    gs.apply(f)
    want = set()
    for s in sl.values():
        want.add(s["cell"])
        if s["tier"] != FIXED:
            want.add(s["cell"] + PROP_CELL_SHIFT)
    got = changed_cells(base, f)
    pairs = {i for i in range(384) if base.metrics[i] != f.metrics[i]}
    check(got <= want and pairs == {s["cell"] for s in sl.values() if s["tier"] != FIXED},
          f"apply changes only the allocated cells, their +576 images and their pairs ({len(got)} cells, {len(pairs)} pairs)")
    check(gs.verify(f, adr) == [], "verify passes: codes, drawn cells and advances")
    m = adr.measurer(f.metrics, han=True, prop=True, xx1=8)
    check(m.width(gs.code_bytes("u:a")) == 7 + 2 and m.width(gs.code_bytes("u:w")) == 19,
          "Measurer: proportional advance = right - left + 3 (ink 7 -> 9), fixed advance 19")
    f2 = blank_font()
    f2.set_metrics(sl["u:a"]["cell"], 1, 1)
    check(any("width function" in p or "pair" in p for p in gs.verify(f2, adr)), "verify notices a font without the glyphs")
    # persistence and extend
    back = GlyphSet.from_json(gs.to_json())
    check(back.slots == gs.slots and back.glyphs["u:a"].bitmap == gs.glyphs["u:a"].bitmap
          and back.addressing() == adr and back.glyphs["u:s"].advance_hint == 9, "glyphset.json round trip")
    back.add(Glyph("u:z", box(4, 10)))
    back.allocate(Tiers(adr), ["u:z", "u:a"])
    check(all(back.slots[k] == gs.slots[k] for k in gs.slots) and "u:z" in back.slots
          and back.slots["u:z"]["cell"] not in {s["cell"] for s in gs.slots.values()},
          "extending keeps every existing slot and takes a free cell")
    # TrueType path: a font built in code
    try:
        require_shaping()
    except GlyphError as e:
        check(False, str(e))
        print("self-test FAILED")
        return 1
    with tempfile.TemporaryDirectory() as td:
        ttf = Path(td) / "kittest.ttf"
        build_test_ttf(ttf)
        r = TTFRenderer(ttf)
        shaped = r.shape("אבג", "rtl")
        gids = [g[0] for g in shaped]
        names = [r.gid_name(g) for g in gids]
        check(names == ["wide", "tri", "sq"], f"HarfBuzz shapes right-to-left into visual order: {names}")
        gl = {r.gid_name(g): r.render_gid(g) for g in gids}
        check(gl["sq"].ink_width in (9, 10) and gl["sq"].bounds[0] == 1 and gl["wide"].ink_width > 18,
              f"rasterised: square {gl['sq'].ink_width} px from column 1, bar {gl['wide'].ink_width} px")
        wb = gl["wide"]
        check(wb.narrowed < 1.0 and wb.cut == 0 and wb.bounds[0] == 1 and wb.bounds[1] <= CELL_W - 1
              and gl["sq"].narrowed == 1.0 and gl["sq"].cut == 0,
              f"a glyph wider than the cell (21.6 px) is drawn at {wb.narrowed:.0%} of its width into columns "
              f"{wb.bounds[0]}..{wb.bounds[1]}, nothing cut; the square is drawn as it is")
        cut_wide = r.raster([(gids[0], 0.0, 0.0)], x_origin=1.0)
        check(r.last_cut > 0 and ink_bounds(cut_wide)[1] == CELL_W - 1,
              f"with a fixed origin the same bar would lose {r.last_cut} px past the cell's edge (counted)")
        tall = r.render_gid(r.shape("ד")[0][0])
        check(tall.cut > 0 and tall.clipped, f"a glyph taller than the cell is counted as cut ({tall.cut} px outside)")
        rows = [y for y in range(CELL_H) if any(gl["sq"].bitmap[y])]
        check(rows[-1] == 18, f"baseline 19: ink ends on row 18 like retail capitals (rows {rows[0]}-{rows[-1]})")
        tg = GlyphSet(r.name)
        tg.bind(adr)
        for g in gl.values():
            tg.add(g)
        tg.allocate(Tiers(adr), [g.key for g in gl.values()])
        tf = blank_font()
        tg.apply(tf)
        check(tg.slots[gl["wide"].key]["tier"] == FIXED and tg.slots[gl["sq"].key]["tier"] == SINGLE
              and tg.verify(tf, adr) == [], "TrueType glyphs allocated (bar -> fixed), applied and verified")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print(__doc__)
