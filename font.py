"""font.py — Episode II's bitmap fonts (``font0.us`` / ``font1.us``, Japan ``font0.tex`` / ``font1.tex``).

The FILE layer is Episode I's (this module started as a copy of
Xenosaga1PythonExtractor/font.py) and is right for Episode II: both regions'
files round-trip to the byte. The ADDRESSING (which cell a character is drawn
from, which metric pair gives its width) is NOT Episode I's; it was read from
the Episode II executables on 2026-11-10 and is described below. Episode I's
addressing is kept only inside the ``Episode1`` class, and the old module-level
names (``cell_of_code``, ``prop_index``, ``ASCII2EUC``, ...) raise ``FontError``
so they cannot be applied to an Episode II font by accident.

File layout (font0 is the dialogue face, font1 the menu face)::

    0x00000  0x20-byte header
    0x00020  block 0  0x3C000 bytes   -> glyph sheets 0 and 1
    0x3C020  0x20-byte header  (identical)
    0x3C040  block 1  0x3C000 bytes   -> glyph sheets 2 and 3
    0x78040  metrics: (left, right) byte pairs, one per cell from cell 0
             USA 384 pairs (file 492,352 bytes); Japan and the demo 256 (492,096)

Each block is a PS2 GS upload of a 320x192 PSMCT32 image which the engine
re-reads as a 640x768 PSMT4 texture: 4 bits per pixel, the low two bits
forming one 4-level sheet and the high two bits another. A sheet is a
32x32 grid of 20x24 cells, so one file carries 4 x 1,024 = 4,096 cells.
The GS swizzle walk is the one worked out by poketony for XS1KOR
(https://github.com/poketony/XS1KOR, ``xeno1font_ex.py``).

Episode II addressing (addresses are USA SLUS_208.92; Japan SLPS_253.68 in
brackets; the demo's EXP.ELF code is Japan's apart from addresses):

* **Code -> cell**: ``xs2_cell(lead, trail)``, a port of ``FUN_001979e0``
  [0x197880]. Rows A1-A8 and AD go to cells below 608 by per-row rules, kanji
  rows B0-CF to ``lead*94 + trail - 0x3D41`` (B0A1 = cell 1024), and lead
  bytes D0 and up are looked up in a list of extra kanji (``FUN_00197988``
  [0x197828]; cell 3989 + index). The list is in the executable, at the VA
  the region profile gives (USA 0x63E870: empty; Japan 0x63A2A0: 87 codes).
  Lead A9 is USA only (cell = trail + 0x100, used for Latin-1 below); the
  Japanese function has no A9 case and returns the fallback cell 0x7F.
* **(a) USA single bytes** (print function ``FUN_00197bb8``, default case at
  0x198660-0x1988dc; width function ``FUN_00195938``):

  - 0x20-0x7F with ``$han1;`` in effect: ``FUN_00198b90`` reads a u16 EUC
    code from a table in the executable (0x63E770 + 2 * byte; ``A`` = A3C1)
    and the code goes through ``xs2_cell`` (``A`` = cell 138). Without
    ``$han``, the byte is drawn from an 8x8 system font, not from a cell.
  - 0x80-0xFF: ``FUN_00194dd0`` accepts only a few two-byte codes (lead A4,
    A6 or AD with any trail; A1 with BD DA DB DF F9 FA FB; A2 with A2 A3 A4
    F6). Any other high byte is ONE character: lead A9, trail = byte - 0x80,
    so cell = byte + 0x80 (0xC9 ``É`` = cell 329). The USA build cannot print
    kanji through this function.

* **(b) Metrics**: the pair index is the cell number in both regions
  (``FUN_00198b38`` [0x1989a8] reads ``font + 0x78040 + 2 * cell``). With
  ``$prop1;`` in effect a cell below the limit is drawn proportionally: the
  glyph image comes from sheet 0, cell + 576 (18 rows down: 0x1b00 at
  0x198894 [0x198704]), ``left - 1`` to ``right + 1``.

  ===============  =====================  ======================
  .                USA                    Japan (and the demo)
  ===============  =====================  ======================
  pairs in file    384                    256
  proportional if  cell < 384 (0x198840)  cell < 192 (0x1986b0)
  advance          right - left + 3       right - left + 4
  ===============  =====================  ======================

  So the Japanese file's pairs 192-255 are never read by this code (they all
  hold (8, 11) and cells 768-831 hold a matching placeholder mark). A pair
  whose left is 0 means "no width": the advance is then the text's ``$xx1``
  value + 1. Everything else is fixed width: cell as is, advance 19.
  Checked against the files: the ink of cell + 576 equals the pair exactly
  for 379 of 384 USA pairs and 255 of 256 Japanese pairs (the rest are blank).
* **Which cells can be drawn**: the Japanese draw function selects block 0 or
  block 1 by bit 1 of cell >> 10 (0x197204). The USA one (``FUN_00197200``,
  0x197380-0x1973fc) builds its texture register from bit 0 only and one
  texture address, and the USA font setup (``FUN_00198bc0``) uploads one
  block; from the code, USA cells 2048-4095 (sheets 2 and 3) are drawn from
  the same place as cells 0-2047. Not seen in the running game.
* Not modelled: the half-size mode (control byte 0x18, ``FUN_00198928``),
  ruby, and the ``$``-codes; this module does not measure whole strings.

Round trip: ``Font.from_bytes(b).to_bytes() == b`` for font0 and font1 of the
USA discs, the Japanese discs and the demo.

Usage:
    python font.py extract font0.us --out DIR      # 4 PNG sheets + metrics.csv
    python font.py rebuild --ref font0.us --sheets DIR --out new.us
    python font.py info font0.us
    python font.py table --iso DISC.iso             # byte -> cell -> metric pair
    python font.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEADER = 0x20
BLOCK = 0x3C000
SHEET_W, SHEET_H = 640, 768
CELL_W, CELL_H = 20, 24
CELLS_PER_ROW = SHEET_W // CELL_W          # 32
CELLS_PER_SHEET = CELLS_PER_ROW * (SHEET_H // CELL_H)   # 1024
METRICS_OFF = 2 * (HEADER + BLOCK)         # 0x78040
FULLWIDTH = (0, 19)                        # metrics pair meaning "not proportional"


class FontError(ValueError):
    pass

# --- GS swizzle tables (PS2 GS PSMCT32 / PSMT4 page/block/column layout) ---
_BLOCK32 = [0, 1, 4, 5, 16, 17, 20, 21, 2, 3, 6, 7, 18, 19, 22, 23,
            8, 9, 12, 13, 24, 25, 28, 29, 10, 11, 14, 15, 26, 27, 30, 31]
_COLWORD32 = [0, 1, 4, 5, 8, 9, 12, 13, 2, 3, 6, 7, 10, 11, 14, 15]
_BLOCK4 = [0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15,
           16, 18, 24, 26, 17, 19, 25, 27, 20, 22, 28, 30, 21, 23, 29, 31]
_COLWORD4 = [
    [0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
     0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
     2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15,
     2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15,
     8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5,
     8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5,
     10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7,
     10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7],
    [8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5,
     8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5,
     10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7,
     10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7,
     0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
     0, 1, 4, 5, 8, 9, 12, 13, 0, 1, 4, 5, 8, 9, 12, 13,
     2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15,
     2, 3, 6, 7, 10, 11, 14, 15, 2, 3, 6, 7, 10, 11, 14, 15],
]
_COLBYTE4 = ([0] * 8 + [2] * 8 + [4] * 8 + [6] * 8) * 2 + \
            ([1] * 8 + [3] * 8 + [5] * 8 + [7] * 8) * 2


def _ct32_word(x: int, y: int, dbw: int = 0x140) -> int:
    page = ((y >> 5) * (dbw >> 6)) + (x >> 6)
    px, py = x & 63, y & 31
    block = _BLOCK32[(px >> 3) + (py >> 3) * 8]
    bx, by = px & 7, py & 7
    return page * 2048 + block * 64 + (by >> 1) * 16 + _COLWORD32[bx + (by & 1) * 8]


def _t4_word(x: int, y: int, dbp: int, dbw: int = 64) -> Tuple[int, int]:
    dbw2 = dbw >> 1
    page = ((y >> 7) * ((dbw2 + 127) >> 7)) + (x >> 7)
    px, py = x & 127, y & 127
    block = _BLOCK4[(px >> 5) + (py >> 4) * 4]
    bx, by = px & 31, py & 15
    col = by >> 2
    cx, cy = bx, by & 3
    word = dbp * 64 + page * 2048 + block * 64 + col * 16 + _COLWORD4[col & 1][cx + cy * 32]
    return word, _COLBYTE4[cx + cy * 32]


_MAP: Optional[Tuple[list, list]] = None


def _layout() -> Tuple[list, list]:
    """Per sheet pixel (row-major 640x768): (byte offset in block, nibble shift).

    Same for both blocks. Built once (~1 s, pure Python) and cached.
    """
    global _MAP
    if _MAP is not None:
        return _MAP
    # GS word address -> block byte offset of that word, for the CT32 upload
    gs_to_byte = {}
    for y in range(192):
        for x in range(320):
            gs_to_byte[_ct32_word(x, y)] = (y * 320 + x) * 4
    offs = [0] * (SHEET_W * SHEET_H)
    shifts = [0] * (SHEET_W * SHEET_H)
    i = 0
    for tile in range(SHEET_H // 128):
        dbp = (tile * SHEET_W) // 4
        for row in range(128):
            for col in range(SHEET_W):
                word, cb = _t4_word(col, row, dbp)
                offs[i] = gs_to_byte[word] + (cb >> 1)
                shifts[i] = 4 if (cb & 1) else 0
                i += 1
    _MAP = (offs, shifts)
    return _MAP


def decode_block(block: bytes) -> Tuple[bytearray, bytearray]:
    """One 0x3C000 block -> two 640x768 sheets of values 0..3 (row-major)."""
    offs, shifts = _layout()
    lo = bytearray(SHEET_W * SHEET_H)
    hi = bytearray(SHEET_W * SHEET_H)
    for i in range(SHEET_W * SHEET_H):
        nib = (block[offs[i]] >> shifts[i]) & 0xF
        lo[i] = nib & 3
        hi[i] = nib >> 2
    return lo, hi


def encode_block(lo: Sequence[int], hi: Sequence[int]) -> bytes:
    offs, shifts = _layout()
    out = bytearray(BLOCK)
    for i in range(SHEET_W * SHEET_H):
        nib = (lo[i] & 3) | ((hi[i] & 3) << 2)
        out[offs[i]] |= nib << shifts[i]
    return bytes(out)


# --- Episode II addressing ----------------------------------------------------

FALLBACK_CELL = 0x7F
EXTRA_KANJI_BASE = 3989          # 0xF95, FUN_00197988
PROP_CELL_SHIFT = 576            # a proportional glyph is drawn from sheet 0, cell + 18 rows
FIXED_ADVANCE = 19


def xs2_cell(lead: int, trail: int, extra: Sequence[Tuple[int, int]] = (), a9: bool = True) -> int:
    """The font cell of a two-byte code: USA ``FUN_001979e0`` (Japan 0x197880).

    ``extra`` is the executable's extra-kanji list as (lead, trail) pairs;
    ``a9`` is False for the Japanese and demo builds, whose function has no
    case for lead 0xA9."""
    lead &= 0xFF
    trail &= 0xFF
    if lead < 0xB0:
        if lead == 0xA1:
            return (trail - 0xA1) & 0xFFFF
        if lead == 0xA2:
            base = {0xA0: 0x5D, 0xE0: 0x6C, 0xF0: 0x75}.get(trail & 0xF0)
            return FALLBACK_CELL if base is None else (trail & 0x0F) + base
        if lead == 0xA3:
            h = trail & 0xE0
            if h == 0xA0:
                return (trail & 0x0F) + 0x80
            if h == 0xC0:
                return (trail & 0x1F) + 0x89
            if h == 0xE0:
                return (trail & 0x1F) + 0xA3
            return FALLBACK_CELL
        if lead == 0xA4:
            return trail + 0x1F
        if lead == 0xA5:
            return trail + 0x7F
        if lead == 0xA6:
            return (trail & 0x1F) + (0x17F if trail < 0xC0 else 0x197)
        if lead == 0xA7:
            return trail + (0x100 if trail > 0xCF else 0x10F)
        if lead == 0xA8:
            return trail + 0x15F
        if lead == 0xA9 and a9:
            return trail + 0x100
        if lead == 0xAD:
            return trail + 0x17F
        return FALLBACK_CELL
    if lead < 0xD0:
        return (lead * 94 + trail - 0x3D41) & 0xFFFF
    for i, pair in enumerate(extra):
        if tuple(pair) == (lead, trail):
            return EXTRA_KANJI_BASE + i
    return FALLBACK_CELL


class Glyph(NamedTuple):
    length: int                 # bytes of text consumed
    kind: str                   # "cell" or "8x8" (the system font: no cell of this file)
    code: int                   # the EUC code looked up (0 for "8x8")
    cell: Optional[int]


# what differs between the builds, with the address that shows it (USA / Japan)
_BUILDS = {
    #        lone high bytes   proportional below     advance = r-l+pad   blocks the draw call reaches
    "us":   dict(latin1=True,  prop_cells=384,        prop_pad=3,         drawable=2048),
    "jp":   dict(latin1=False, prop_cells=192,        prop_pad=4,         drawable=4096),
    "demo": dict(latin1=False, prop_cells=192,        prop_pad=4,         drawable=4096),
}


@dataclass(frozen=True)
class Addressing:
    """Byte -> cell -> metric pair for one build. Make one with ``from_iso`` or
    ``from_executable``: the ASCII table and the extra-kanji list are read from
    the game's executable, at the virtual addresses of the region profile."""
    region: str
    ascii: Tuple[int, ...]                      # EUC codes of bytes 0x20..0x7F
    extra: Tuple[Tuple[int, int], ...]          # the extra-kanji list
    latin1: bool
    prop_cells: int
    prop_pad: int
    drawable: int

    @classmethod
    def from_executable(cls, elf: bytes, profile) -> "Addressing":
        import region
        if profile.name not in _BUILDS:
            raise FontError(f"no Episode II font addressing is known for region {profile.name!r}")
        if profile.ascii_table_va is None or profile.kanji_list_va is None:
            raise FontError(f"the {profile.name} profile does not give the font table addresses")
        try:
            raw = region.read_va(elf, profile.ascii_table_va + 2 * 0x20, 2 * 96)
            ascii_codes = struct.unpack("<96H", raw)
            extra, va = [], profile.kanji_list_va
            while len(extra) <= CELLS_PER_SHEET * 4 - EXTRA_KANJI_BASE:
                a, b = region.read_va(elf, va, 2)
                if a == 0:
                    break
                extra.append((a, b))
                va += 2
            else:
                raise FontError("the extra-kanji list does not end: wrong executable for this profile?")
        except region.ElfError as e:
            raise FontError(f"cannot read the font tables from the executable: {e}")
        if ascii_codes[0] != 0xA1A1 or ascii_codes[ord("A") - 0x20] != 0xA3C1 \
                or any(not (0xA1 <= c >> 8 <= 0xFE and 0xA1 <= c & 0xFF <= 0xFE) for c in ascii_codes):
            raise FontError("the ASCII table is not at the profile's address: wrong executable for this profile?")
        if any(a < 0xD0 for a, _ in extra):
            raise FontError("the extra-kanji list holds a lead byte below 0xD0: wrong executable?")
        return cls(profile.name, tuple(ascii_codes), tuple(extra), **_BUILDS[profile.name])

    @classmethod
    def from_iso(cls, iso) -> "Addressing":
        import region
        _, elf = region.executable(iso)
        return cls.from_executable(elf, region.region_of(iso))

    # -- code -> cell
    def cell(self, lead: int, trail: int) -> int:
        return xs2_cell(lead, trail, self.extra, a9=self.latin1)

    def two_byte(self, lead: int, trail: int) -> bool:
        """Does the print function read (lead, trail) as one two-byte code?
        USA: ``FUN_00194dd0``. Japan: any byte from 0xA0 up leads a pair."""
        if not self.latin1:
            return lead >= 0xA0
        if trail == 0:
            return False
        if lead in (0xA4, 0xA6, 0xAD):
            return True
        if lead == 0xA1:
            return trail in (0xBD, 0xDA, 0xDB, 0xDF, 0xF9, 0xFA, 0xFB)
        if lead == 0xA2:
            return trail in (0xA2, 0xA3, 0xA4, 0xF6)
        return False

    def glyph(self, text: bytes, i: int = 0, han: bool = True) -> Glyph:
        """The character at ``text[i]`` as the print function reads it. ``han``
        is the ``$han1;`` state. Control bytes (< 0x20) and ``$``-codes are the
        caller's business: a control byte raises FontError."""
        b = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else 0
        if b < 0x20:
            raise FontError(f"control byte {b:#04x}: not a character")
        if self.latin1:                                     # FUN_00197bb8, 0x198660-0x1986fc
            if b < 0x80:
                if not han:
                    return Glyph(1, "8x8", 0, None)
                code = self.ascii[b - 0x20]
                return Glyph(1, "cell", code, self.cell(code >> 8, code & 0xFF))
            if self.two_byte(b, nxt):
                return Glyph(2, "cell", b << 8 | nxt, self.cell(b, nxt))
            trail = (b + 0x80) & 0xFF                       # a lone high byte: Latin-1
            return Glyph(1, "cell", 0xA900 | trail, self.cell(0xA9, trail))
        if b >= 0xA0:                                       # Japan 0x198508-0x198570
            return Glyph(2, "cell", b << 8 | nxt, self.cell(b, nxt))
        if not han:
            return Glyph(1, "8x8", 0, None)
        if b >= 0x80:                                       # a signed char below 0x20 at 0x198a68
            return Glyph(1, "cell", 0xA1A1, self.cell(0xA1, 0xA1))
        n = 1
        if b == 0x23:                                       # '#': Japan 0x198a84-0x198b0c
            if nxt == 0x23:
                n, code = 2, self.ascii[0x23 - 0x20]
            else:
                try:
                    v = int(text[i + 1:i + 3].decode("ascii"), 16) if len(text) >= i + 3 else None
                except ValueError:
                    v = None
                if v is None:
                    raise FontError("'#' in Japanese $han text must be '##' or '#' and two hex digits")
                v += 0x20
                n, code = 3, 0xD0A1 + ((v // 94) << 8) + v % 94
        else:
            code = self.ascii[b - 0x20]
        return Glyph(n, "cell", code, self.cell(code >> 8, code & 0xFF))

    # -- cell -> what is drawn
    def metric_slot(self, cell: int, prop: bool = True) -> Optional[int]:
        """The metric pair read for ``cell`` (the cell number itself), or None
        when it is drawn fixed width (``$prop0;``, or a cell at or past the limit)."""
        return cell if prop and 0 <= cell < self.prop_cells else None

    def drawn_cell(self, cell: int, prop: bool = True) -> int:
        """The cell whose image is drawn for ``cell``."""
        return cell + PROP_CELL_SHIFT if self.metric_slot(cell, prop) is not None else cell

    def advance(self, cell: int, metrics: Sequence[Tuple[int, int]], prop: bool = True) -> Optional[int]:
        """Pixels the pen moves at zoom 16. None when the pair's left is 0: the
        width is then the text's own ``$xx1`` value + 1, which this cannot know."""
        slot = self.metric_slot(cell, prop)
        if slot is None:
            return FIXED_ADVANCE
        if slot >= len(metrics):
            raise FontError(f"cell {cell}: the font has {len(metrics)} metric pairs, "
                            f"this build reads pairs below {self.prop_cells}")
        left, right = metrics[slot]
        return None if left == 0 else right - left + self.prop_pad

    def reachable(self, cell: int) -> bool:
        """False for a cell the build's draw call cannot address (USA: sheets 2 and 3)."""
        return 0 <= cell < self.drawable

    def table(self, metrics: Sequence[Tuple[int, int]] = (), han: bool = True, prop: bool = True) -> List[dict]:
        """One row per single byte 0x20..0xFF: what it is read as, its cell, the
        cell drawn, the metric slot, the pair and the advance. A byte that only
        starts a two-byte code (Japan 0xA0 up) has kind "lead"."""
        rows = []
        for b in range(0x20, 0x100):
            row = {"byte": f"{b:02X}", "kind": "", "code": "", "cell": "", "drawn_cell": "",
                   "metric_slot": "", "left": "", "right": "", "advance": ""}
            if not self.latin1 and b >= 0xA0:
                row["kind"] = "lead"
            elif not self.latin1 and han and b == 0x23:
                row["kind"] = "escape"                      # '##' or '#XX'
            else:
                g = self.glyph(bytes((b, 0)), 0, han)
                row["kind"] = g.kind
                if g.cell is not None:
                    slot = self.metric_slot(g.cell, prop)
                    row.update(code=f"{g.code:04X}", cell=g.cell, drawn_cell=self.drawn_cell(g.cell, prop),
                               metric_slot="" if slot is None else slot)
                    if slot is not None and slot < len(metrics):
                        adv = self.advance(g.cell, metrics, prop)
                        row.update(left=metrics[slot][0], right=metrics[slot][1],
                                   advance="$xx1+1" if adv is None else adv)
                    elif slot is None:
                        row["advance"] = FIXED_ADVANCE
            rows.append(row)
        return rows


# --- Episode I addressing: fenced --------------------------------------------
#
# What follows is Xenosaga I's (SLUS_204.69) addressing, kept for reference and
# for comparing the two games. It is WRONG for both Episode II fonts: its kanji
# formula puts B0A1 at cell 752 (Episode II: 1024) and it reserves cells
# 3776-4031 for proportional glyphs, which hold kanji in Episode II. Use
# ``Addressing`` above. The names live only on this class; the old module-level
# names raise FontError (``__getattr__`` below).

class Episode1:
    PROP_CELL_BASE = 3744                      # cell = PROP_CELL_BASE + n, n = 32..287
    PROP_SLOTS = 256

    # xglFontAscii2Euc's table for 0x20..0x7F (0x7F -> space). Punctuation was
    # moved into the USA-only rows 0xD1/0xD2; '\\' lands on the yen glyph as in
    # every JIS font.
    ASCII2EUC = [
        0xA1A1, 0xD1B5, 0xD1C2, 0xD1E5, 0xD2A9, 0xD1E6, 0xD2A8, 0xD1C0,
        0xD1CB, 0xD1CC, 0xD1B4, 0xD1FE, 0xD1BA, 0xD1FD, 0xD1B9, 0xD1D4,
        0xA3B0, 0xA3B1, 0xA3B2, 0xA3B3, 0xA3B4, 0xA3B5, 0xA3B6, 0xA3B7,
        0xA3B8, 0xA3B9, 0xD1BC, 0xD1BB, 0xD1C7, 0xD1FC, 0xD1C8, 0xD1B7,
        0xD1AD, 0xA3C1, 0xA3C2, 0xA3C3, 0xA3C4, 0xA3C5, 0xA3C6, 0xA3C7,
        0xA3C8, 0xA3C9, 0xA3CA, 0xA3CB, 0xA3CC, 0xA3CD, 0xA3CE, 0xA3CF,
        0xA3D0, 0xA3D1, 0xA3D2, 0xA3D3, 0xA3D4, 0xA3D5, 0xA3D6, 0xA3D7,
        0xA3D8, 0xA3D9, 0xA3DA, 0xD1CD, 0xD1A5, 0xD1CE, 0xD1D8, 0xD1D3,
        0xD1DA, 0xA3E1, 0xA3E2, 0xA3E3, 0xA3E4, 0xA3E5, 0xA3E6, 0xA3E7,
        0xA3E8, 0xA3E9, 0xA3EA, 0xA3EB, 0xA3EC, 0xA3ED, 0xA3EE, 0xA3EF,
        0xA3F0, 0xA3F1, 0xA3F2, 0xA3F3, 0xA3F4, 0xA3F5, 0xA3F6, 0xA3F7,
        0xA3F8, 0xA3F9, 0xA3FA, 0xD1CF, 0xD1D1, 0xD1D0, 0xD1E8, 0xA1A1,
    ]
    _ROW3_BASE = (-16, 10, 37, 0)   # xglFontCheckProportional's table for row 3

    # Operand byte count per control byte 0x00..0x1F (xglFontGetSPcodeSize).
    # None = variable: 0x08 takes a flag byte whose set bits add 2,2,3,2,2 bytes
    # (bits 0-4); 0x15 takes a length byte L and L more bytes.
    CONTROL_OPERANDS = [0, 1, 1, 1, 1, 1, 3, 0, None, 0, 0, 0, 3, 1, 5, 3,
                        5, 6, 2, 2, 0, None, 0, 7, 1, 1, 0, 1, 0, 0, 0, 0]
    _CTRL08_BITS = (2, 2, 3, 2, 2, 0, 0, 0)

    @staticmethod
    def control_length(data: bytes, i: int) -> int:
        """Total byte length of the control sequence starting at data[i] (< 0x20)."""
        c = data[i]
        n = Episode1.CONTROL_OPERANDS[c]
        if n is not None:
            return 1 + n
        if c == 0x08:
            flags = data[i + 1] if i + 1 < len(data) else 0
            return 2 + sum(Episode1._CTRL08_BITS[b] for b in range(8) if flags >> b & 1)
        return 2 + (data[i + 1] if i + 1 < len(data) else 0)      # 0x15

    @staticmethod
    def ascii_to_code(ch: int) -> int:
        """ASCII byte (0x20..0x7F) -> EUC code as the engine maps it."""
        return Episode1.ASCII2EUC[ch - 0x20]

    @staticmethod
    def cell_of_code(code: int) -> int:
        """Kanji-path cell for an EUC code (xglFontGetKanjiClutUV)."""
        hi, lo = code >> 8, code & 0xFF
        v = (lo + 94 * hi - 0xA1) & 0xFFFF
        v += 0xC4E2 if hi < 0xAD else (0xC30C if hi < 0xB0 else 0xC250)
        return v & 0xFFFF

    @staticmethod
    def prop_index(code: int) -> int:
        """Proportional slot for an EUC code, or -1 (xglFontCheckProportional)."""
        hi, lo = code >> 8, code & 0xFF
        if hi == 0xA7:
            return {0xA1: 0x40, 0xA3: 0x41, 0xA6: 0x42}.get(lo, -1)
        if hi == 0xA1:
            return {0xA1: 37, 0xA5: 10}.get(lo, -1)
        if hi == 0xA3:
            return Episode1._ROW3_BASE[(lo - 0xA0) >> 5] + (lo & 0x1F)
        if hi < 0xD0:
            return -1
        v = (94 * hi + lo - 0x4D21) & 0xFFFF
        r = (~(v >> 5)) & 7
        if r < 2:
            r ^= 1
        return r * 32 + (v & 0x1F)

    @staticmethod
    def prop_offset(slot: int) -> int:
        """Linear offset n (32..287) from code 0xD0A1 of the code owning ``slot``."""
        r, k = slot >> 5, slot & 0x1F
        r0 = r ^ 1 if r < 2 else r
        return (7 - r0) * 32 + k + 32

    @staticmethod
    def code_of_prop(slot: int) -> int:
        """The USA-row EUC code that selects proportional ``slot`` (inverse of
        prop_index for the 0xD0.. rows)."""
        n = Episode1.prop_offset(slot)
        return ((0xD0 + n // 94) << 8) | (0xA1 + n % 94)

    @staticmethod
    def cell_of_prop(slot: int) -> int:
        """Cell drawn for a proportional slot (the 256-cell block, 3776..4031)."""
        return Episode1.PROP_CELL_BASE + Episode1.prop_offset(slot)

    @staticmethod
    def glyph_cell(code: int) -> int:
        """Cell the dialogue printer draws for ``code`` in proportional mode."""
        p = Episode1.prop_index(code)
        return Episode1.cell_of_prop(p) if p >= 0 else Episode1.cell_of_code(code)

    @staticmethod
    def advance(pair: Tuple[int, int]) -> int:
        """Pixels the printer advances for a proportional slot's (left, right)."""
        left, right = pair
        if left == 0:
            return 20   # engine substitutes its default width; retail uses this
        return right - left + 3        # xglFontGetProportionalSize, mode 0


_EPISODE1_NAMES = ("ASCII2EUC", "CONTROL_OPERANDS", "PROP_CELL_BASE", "PROP_SLOTS", "control_length",
                   "ascii_to_code", "cell_of_code", "prop_index", "prop_offset", "code_of_prop",
                   "cell_of_prop", "glyph_cell", "advance")


def __getattr__(name):                         # module-level: font.cell_of_code and friends
    if name in _EPISODE1_NAMES:
        raise FontError(f"font.{name} is Xenosaga I's addressing and is wrong for Episode II fonts; "
                        f"use font.Addressing (or font.Episode1.{name} if you mean Episode I)")
    raise AttributeError(f"module 'font' has no attribute {name!r}")


# --- the file --------------------------------------------------------------

class Font:
    def __init__(self, headers: List[bytes], sheets: List[bytearray],
                 metrics: List[Tuple[int, int]], tail: bytes = b""):
        self.headers = headers        # two 0x20-byte headers
        self.sheets = sheets          # four 640*768 bytearrays, values 0..3
        self.metrics = metrics        # (left, right) per slot
        self.tail = tail              # bytes after the metrics, if any

    @classmethod
    def from_bytes(cls, data: bytes) -> "Font":
        if len(data) < METRICS_OFF:
            raise FontError(f"font file too short ({len(data)} bytes)")
        headers, sheets = [], []
        for m in range(2):
            base = m * (HEADER + BLOCK)
            headers.append(bytes(data[base:base + HEADER]))
            lo, hi = decode_block(data[base + HEADER:base + HEADER + BLOCK])
            sheets += [lo, hi]
        rest = data[METRICS_OFF:]
        npairs = len(rest) // 2
        metrics = [(rest[2 * i], rest[2 * i + 1]) for i in range(npairs)]
        return cls(headers, sheets, metrics, bytes(rest[npairs * 2:]))

    @classmethod
    def load(cls, path) -> "Font":
        return cls.from_bytes(Path(path).read_bytes())

    def to_bytes(self) -> bytes:
        out = bytearray()
        for m in range(2):
            out += self.headers[m]
            out += encode_block(self.sheets[2 * m], self.sheets[2 * m + 1])
        for left, right in self.metrics:
            out += bytes((left & 0xFF, right & 0xFF))
        out += self.tail
        return bytes(out)

    # cells
    @property
    def cell_count(self) -> int:
        return CELLS_PER_SHEET * len(self.sheets)

    def cell(self, index: int) -> List[List[int]]:
        """24 rows of 20 values (0..3) for cell ``index``."""
        sheet = self.sheets[index // CELLS_PER_SHEET]
        i = index % CELLS_PER_SHEET
        x0, y0 = (i % CELLS_PER_ROW) * CELL_W, (i // CELLS_PER_ROW) * CELL_H
        return [list(sheet[(y0 + r) * SHEET_W + x0:(y0 + r) * SHEET_W + x0 + CELL_W])
                for r in range(CELL_H)]

    def set_cell(self, index: int, rows: Sequence[Sequence[int]]) -> None:
        if len(rows) != CELL_H or any(len(r) != CELL_W for r in rows):
            raise FontError("cell must be 24 rows of 20 values")
        sheet = self.sheets[index // CELLS_PER_SHEET]
        i = index % CELLS_PER_SHEET
        x0, y0 = (i % CELLS_PER_ROW) * CELL_W, (i // CELLS_PER_ROW) * CELL_H
        for r in range(CELL_H):
            sheet[(y0 + r) * SHEET_W + x0:(y0 + r) * SHEET_W + x0 + CELL_W] = \
                bytes(v & 3 for v in rows[r])

    def ink_bounds(self, index: int) -> Optional[Tuple[int, int]]:
        """(leftmost, rightmost) inked columns of a cell, or None if blank."""
        cols = [c for c in range(CELL_W)
                if any(row[c] for row in self.cell(index))]
        return (cols[0], cols[-1]) if cols else None

    def set_metrics(self, slot: int, left: int, right: int) -> None:
        """Change one pair. The table cannot grow: the game reads the whole file into
        a fixed buffer (USA 0x8DD280, set in FUN_00198ff0) and indexes pairs by cell."""
        if not 0 <= slot < len(self.metrics):
            raise FontError(f"metric slot {slot}: this font has {len(self.metrics)} pairs and cannot grow")
        if not (0 <= left <= 255 and 0 <= right <= 255):
            raise FontError(f"metric slot {slot}: left and right are bytes")
        self.metrics[slot] = (left, right)

# --- PNG (8-bit grayscale, stdlib only) -------------------------------------

def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def sheet_to_png(sheet: Sequence[int], width: int = SHEET_W,
                 height: int = SHEET_H) -> bytes:
    levels = bytes((0, 85, 170, 255))
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += bytes(levels[v & 3] for v in sheet[y * width:(y + 1) * width])
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + _png_chunk(b"IEND", b""))


def png_to_gray(data: bytes) -> Tuple[int, int, bytearray]:
    """Decode a non-interlaced 8-bit PNG (gray, gray+alpha, RGB, RGBA, or
    8-bit palette) to (width, height, gray bytes). Enough for edited sheets."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise FontError("not a PNG file")
    pos, idat, plte = 8, bytearray(), b""
    width = height = depth = ctype = 0
    while pos < len(data):
        (ln,) = struct.unpack_from(">I", data, pos)
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            width, height, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            if depth != 8 or inter:
                raise FontError("PNG must be 8-bit, non-interlaced")
        elif tag == b"PLTE":
            plte = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
        pos += 12 + ln
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    stride = width * bpp
    prev = bytearray(stride)
    gray = bytearray(width * height)
    p = 0
    for y in range(height):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        if f == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif f != 0:
            raise FontError(f"bad PNG filter {f}")
        row = y * width
        if ctype == 3:
            for x in range(width):
                i = line[x] * 3
                gray[row + x] = (plte[i] * 299 + plte[i + 1] * 587 + plte[i + 2] * 114) // 1000
        elif bpp == 1:
            gray[row:row + width] = line
        else:
            for x in range(width):
                i = x * bpp
                if bpp == 2:
                    gray[row + x] = line[i]
                else:
                    gray[row + x] = (line[i] * 299 + line[i + 1] * 587 + line[i + 2] * 114) // 1000
        prev = line
    return width, height, gray


def quantize(gray: Sequence[int]) -> bytearray:
    """8-bit gray -> 4 levels (thresholds between the 0/85/170/255 steps)."""
    return bytearray(0 if g <= 42 else 1 if g <= 127 else 2 if g <= 212 else 3
                     for g in gray)


# --- CLI -------------------------------------------------------------------

def write_table(path: Path, addressing: "Addressing", font: Optional["Font"]) -> None:
    """The byte -> cell -> metric table as CSV, for $han1;$prop1; text (what the menus
    and dialogue set) and, in the mode column, for $han1;$prop0; too."""
    fields = ["mode", "byte", "kind", "code", "cell", "drawn_cell", "metric_slot", "left", "right", "advance"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for prop in (True, False):
            for row in addressing.table(font.metrics if font else (), han=True, prop=prop):
                w.writerow(dict(row, mode="han1 prop1" if prop else "han1 prop0"))


def cmd_extract(tex: str, out: str) -> int:
    font = Font.load(tex)
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    stem = Path(tex).stem
    for i, sheet in enumerate(font.sheets):
        (outp / f"{stem}_sheet{i}.png").write_bytes(sheet_to_png(sheet))
    with open(outp / f"{stem}_metrics.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        # slot = the cell number a character maps to; its proportional image is cell + 576
        w.writerow(["slot", "drawn_cell", "left", "right"])
        for slot, (l, r) in enumerate(font.metrics):
            w.writerow([slot, slot + PROP_CELL_SHIFT, l, r])
    print(f"{tex}: 4 sheets + {len(font.metrics)} metrics -> {outp}")
    return 0


def cmd_rebuild(ref: str, sheets_dir: str, out: str, metrics_csv: Optional[str]) -> int:
    data = Path(ref).read_bytes()
    font = Font.from_bytes(data)
    stem = Path(ref).stem
    for i in range(4):
        p = Path(sheets_dir) / f"{stem}_sheet{i}.png"
        if not p.exists():
            continue
        w, h, gray = png_to_gray(p.read_bytes())
        if (w, h) != (SHEET_W, SHEET_H):
            raise FontError(f"{p}: sheet must be {SHEET_W}x{SHEET_H}, got {w}x{h}")
        font.sheets[i] = quantize(gray)
    if metrics_csv:
        with open(metrics_csv, newline="") as fh:
            for row in csv.DictReader(fh):
                font.set_metrics(int(row["slot"]), int(row["left"]), int(row["right"]))
    blob = font.to_bytes()
    if len(blob) != len(data):                      # cannot happen through set_metrics; kept as a guard
        raise FontError(f"rebuilt font is {len(blob)} bytes, the original {len(data)}: the size must not change")
    Path(out).write_bytes(blob)
    print(f"wrote {out}" + (" (identical to the reference)" if blob == data else ""))
    return 0


def cmd_info(tex: str) -> int:
    data = Path(tex).read_bytes()
    font = Font.from_bytes(data)
    ok = font.to_bytes() == data
    used = sum(1 for m in font.metrics if m[0] != 0)
    match = sum(1 for c, m in enumerate(font.metrics) if font.ink_bounds(c + PROP_CELL_SHIFT) == m)
    print(f"{tex}: {len(data)} bytes, {len(font.metrics)} metric pairs ({used} with a width, "
          f"{match} equal to the ink of cell + {PROP_CELL_SHIFT}), round-trip {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


def cmd_table(iso_path: str, out: Optional[str]) -> int:
    import region
    from chains import CHAINS, ChainReader
    from iso9660 import IsoImage
    from toc import parse_toc

    iso = IsoImage(iso_path)
    prof = region.region_of(iso)
    adr = Addressing.from_iso(iso)
    reader = ChainReader(iso, 0)
    byp = {e.path.lower(): e for e in parse_toc(bytes(reader.toc_bytes()), CHAINS[0][0])}
    e = byp[prof.fonts[0]]
    font = Font.from_bytes(reader.read(e.byte_offset, e.size))
    print(f"{prof.name}: {len(adr.extra)} extra kanji, proportional below cell {adr.prop_cells}, "
          f"advance right-left+{adr.prop_pad}, cells below {adr.drawable} reachable")
    if out:
        write_table(Path(out), adr, font)
        print(f"wrote {out}")
    else:
        for row in adr.table(font.metrics):
            if row["kind"] == "cell":
                print(f"  {row['byte']}  code {row['code']}  cell {row['cell']:>4}  drawn {row['drawn_cell']:>4}  "
                      f"pair ({row['left']}, {row['right']})  advance {row['advance']}")
    return 0


def _synthetic_elf(ascii_va: int, list_va: int, extra: Sequence[Tuple[int, int]]) -> bytes:
    """A tiny ELF with one PT_LOAD data segment holding a synthetic ASCII table and
    an extra-kanji list: enough for from_executable."""
    # letters and digits take their JIS row-3 code; every other byte gets a made-up
    # row-1 code (0xA1A1 + (byte - 0x20) % 94). The game's own table is read from its executable.
    codes = [0xA300 | (c + 0x80) if chr(c).isalnum() else 0xA1A1 + (c - 0x20) % 94 for c in range(0x20, 0x80)]
    seg_va, seg_off = min(ascii_va, list_va) & ~0xFFF, 0x1000
    size = max(ascii_va + 0x100, list_va + 2 * len(extra) + 2) - seg_va
    seg = bytearray(size)
    struct.pack_into("<96H", seg, ascii_va + 0x40 - seg_va, *codes)
    for i, (a, b) in enumerate(extra):
        seg[list_va - seg_va + 2 * i:list_va - seg_va + 2 * i + 2] = bytes((a, b))
    head = bytearray(0x34)
    head[:4] = b"\x7fELF"
    struct.pack_into("<I", head, 0x1C, 0x34)
    struct.pack_into("<HH", head, 0x2A, 32, 1)
    ph = struct.pack("<8I", 1, seg_off, seg_va, seg_va, size, size + 0x100, 6, 0x1000)
    return bytes(head) + ph + bytes(seg_off - 0x34 - 32) + bytes(seg)


def self_test() -> int:
    import region
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    # the lookup: values checked against xs2-analysis/jp/text-fonts/xs2cell.py and the JP font
    check(xs2_cell(0xA4, 0xA2) == 193 and xs2_cell(0xB0, 0xA1) == 1024, "A4A2 -> cell 193, B0A1 -> cell 1024")
    check(xs2_cell(0xA5, 0xA2) == 289 and xs2_cell(0xAD, 0xB5) == 564, "A5A2 -> 289, ADB5 -> 564")
    check(xs2_cell(0xA1, 0xA1) == 0 and xs2_cell(0xA3, 0xC1) == 138 and xs2_cell(0xA3, 0xB0) == 128
          and xs2_cell(0xA3, 0xE1) == 164, "row A1 and row A3 (space, A, 0, a)")
    check(xs2_cell(0xA2, 0xA2) == 0x5F and xs2_cell(0xA2, 0xE0) == 0x6C and xs2_cell(0xA2, 0xF6) == 0x7B
          and xs2_cell(0xA2, 0xB0) == FALLBACK_CELL, "row A2: three 16-code groups, the rest falls back")
    check(xs2_cell(0xA6, 0xA1) == 0x180 and xs2_cell(0xA6, 0xC1) == 0x198 and xs2_cell(0xA7, 0xA1) == 0x1B0
          and xs2_cell(0xA7, 0xD1) == 0x1D1 and xs2_cell(0xA8, 0xA1) == 0x200, "rows A6, A7, A8")
    check(xs2_cell(0xAA, 0xA1) == xs2_cell(0xAF, 0xA1) == xs2_cell(0x90, 0xA1) == FALLBACK_CELL,
          "rows without a rule fall back to cell 0x7F")
    check(xs2_cell(0xCF, 0xD3) == 3988, "the last level-1 kanji ends right before the extra list (3989)")
    extra = ((0xD0, 0xBC), (0xD7, 0xFC), (0xF4, 0xA1))
    check(xs2_cell(0xD7, 0xFC, extra) == 3990 and xs2_cell(0xD7, 0xFD, extra) == FALLBACK_CELL
          and xs2_cell(0xD0, 0xBC) == FALLBACK_CELL, "extra kanji: 3989 + index, else fallback")
    check(xs2_cell(0xA9, 0xC0) == 0x1C0 and xs2_cell(0xA9, 0xC0, a9=False) == FALLBACK_CELL,
          "lead A9 is USA only")

    # Episode I's addressing is fenced
    try:
        cell_of_code = getattr(sys.modules[__name__], "cell_of_code")
        check(False, "font.cell_of_code raises")
    except FontError:
        check(True, "font.cell_of_code raises (Episode I addressing is fenced)")
    check(Episode1.cell_of_code(0xB0A1) == 752 != xs2_cell(0xB0, 0xA1), "Episode1.cell_of_code differs (752 vs 1024)")

    # two synthetic executables, at made-up addresses: tables are read through the program headers
    us_prof = region.Region("us", "en", "sbl", ("font0.us", "font1.us"), "", "",
                            ascii_table_va=0x2B1100, kanji_list_va=0x2B1200)
    jp_prof = region.Region("jp", None, "inline", ("font0.tex", "font1.tex"), "", "",
                            ascii_table_va=0x3C2200, kanji_list_va=0x3C2300)
    us = Addressing.from_executable(_synthetic_elf(0x2B1100, 0x2B1200, ()), us_prof)
    jp = Addressing.from_executable(_synthetic_elf(0x3C2200, 0x3C2300, extra), jp_prof)
    check(us.extra == () and jp.extra == extra and us.ascii[1] == 0xA1A2, "tables read from the executable by VA")
    try:
        Addressing.from_executable(_synthetic_elf(0x2B1100, 0x2B1200, ()), jp_prof)
        check(False, "a profile that does not match the executable is refused")
    except FontError:
        check(True, "a profile that does not match the executable is refused")
    try:
        Addressing.from_executable(b"", region.Region("demo", None, "inline", (), "", ""))
        check(False, "a profile without table addresses is refused")
    except FontError:
        check(True, "a profile without table addresses is refused")

    # a synthetic font: every pair is (4, 4 + slot % 12); 384 pairs (USA) and 256 (Japan)
    def blank(n):
        return Font([bytes(HEADER)] * 2, [bytearray(SHEET_W * SHEET_H) for _ in range(4)],
                    [(4, 4 + k % 12) for k in range(n)])

    fu, fj = blank(384), blank(256)
    fu.metrics[0] = fj.metrics[0] = FULLWIDTH

    # USA: byte -> cell -> metric pair
    g = us.glyph(b"A")
    check(g == Glyph(1, "cell", 0xA3C1, 138) and us.metric_slot(138) == 138 and us.drawn_cell(138) == 714
          and us.advance(138, fu.metrics) == (4 + 138 % 12) - 4 + 3, "USA 'A': code A3C1, cell 138, pair 138, image 714")
    check(us.glyph(b"A", han=False) == Glyph(1, "8x8", 0, None), "USA 'A' without $han: the 8x8 system font")
    check(us.glyph(b"\xc9t") == Glyph(1, "cell", 0xA949, 329) and us.metric_slot(329) == 329,
          "USA 0xC9: one Latin-1 byte, cell 329 (= byte + 0x80)")
    check(us.glyph(b"\xa2\xa2>") == Glyph(2, "cell", 0xA2A2, 0x5F), "USA A2A2 (a button symbol) is a two-byte code")
    check(us.glyph(b"\xb0\xa1") == Glyph(1, "cell", 0xA930, 0x130), "USA B0A1 is NOT read as kanji: two Latin-1 bytes")
    check(us.glyph(b"\xa4\xa2").cell == 193 and us.glyph(b"\xa4\x00").length == 1, "USA lead A4 pairs unless the next byte is NUL")
    check(us.advance(0, fu.metrics) is None and us.advance(138, fu.metrics, prop=False) == FIXED_ADVANCE
          and us.drawn_cell(138, prop=False) == 138, "a pair with left 0 defers to $xx1; $prop0 is fixed width")
    check(us.metric_slot(383) == 383 and us.metric_slot(384) is None and us.advance(1024, fu.metrics) == 19,
          "USA: proportional below cell 384")
    check(us.reachable(2047) and not us.reachable(2048), "USA: sheets 2 and 3 are out of the draw call's reach")
    # Japan
    check(jp.glyph(b"\xa4\xa2") == Glyph(2, "cell", 0xA4A2, 193) and jp.glyph(b"\xb0\xa1") == Glyph(2, "cell", 0xB0A1, 1024),
          "Japan: A4A2 -> 193, B0A1 -> 1024, two bytes each")
    check(jp.glyph(b"\xd7\xfc").cell == 3990 and jp.glyph(b"\xa9\xc0").cell == FALLBACK_CELL,
          "Japan: extra kanji from the list; A9 falls back")
    check(jp.glyph(b"A") == Glyph(1, "cell", 0xA3C1, 138) and jp.metric_slot(138) == 138
          and jp.advance(138, fj.metrics) == (4 + 138 % 12) - 4 + 4, "Japan 'A': cell 138, pair 138, advance right-left+4")
    check(jp.metric_slot(191) == 191 and jp.metric_slot(192) is None and jp.drawn_cell(193) == 193
          and jp.advance(193, fj.metrics) == 19, "Japan: proportional below cell 192; kana are fixed width")
    check(jp.glyph(b"##") == Glyph(2, "cell", 0xA1A4, 3) and jp.glyph(b"#10").code == 0xD0A1 + 0x30
          and jp.glyph(b"#40").code == 0xD1A1 + 2, "Japan $han '#': '##' and '#XX' (code D0A1 + XX + 0x20, rows of 94)")
    check(jp.reachable(4095), "Japan: all four sheets reachable")
    rows = us.table(fu.metrics)
    r41 = next(r for r in rows if r["byte"] == "41")
    rc9 = next(r for r in rows if r["byte"] == "C9")
    check(len(rows) == 224 and (r41["cell"], r41["drawn_cell"], r41["metric_slot"], r41["left"], r41["right"]) ==
          (138, 714, 138, 4, 10) and rc9["cell"] == 329, "USA byte table: 224 rows, 'A' and 0xC9")
    rows = jp.table(fj.metrics)
    check(sum(r["kind"] == "lead" for r in rows) == 96 and next(r for r in rows if r["byte"] == "23")["kind"] == "escape",
          "Japan byte table: 96 lead bytes, '#' is an escape")

    # the file layer: build -> parse -> build, PNG round trip, metrics cannot grow
    f = blank(384)
    f.set_cell(138, [[(r + c) % 4 for c in range(CELL_W)] for r in range(CELL_H)])
    f.set_cell(4095, [[3] * CELL_W] * CELL_H)
    blob = f.to_bytes()
    g2 = Font.from_bytes(blob)
    check(len(blob) == METRICS_OFF + 768 and g2.to_bytes() == blob and g2.cell(138) == f.cell(138)
          and g2.metrics == f.metrics, "font build/parse round trip (384 pairs)")
    w, h, gray = png_to_gray(sheet_to_png(g2.sheets[0]))
    check((w, h) == (SHEET_W, SHEET_H) and quantize(gray) == g2.sheets[0], "sheet -> PNG -> sheet is exact")
    try:
        g2.set_metrics(384, 1, 2)
        check(False, "the metric table cannot grow")
    except FontError:
        check(True, "the metric table cannot grow")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--self-test"]:
        return self_test()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pe = sub.add_parser("extract")
    pe.add_argument("tex")
    pe.add_argument("--out", required=True)
    pr = sub.add_parser("rebuild")
    pr.add_argument("--ref", required=True, help="retail font file to start from")
    pr.add_argument("--sheets", required=True, help="directory with edited sheet PNGs")
    pr.add_argument("--metrics", help="edited metrics CSV (optional)")
    pr.add_argument("--out", required=True)
    pi = sub.add_parser("info")
    pi.add_argument("tex")
    pt = sub.add_parser("table", help="byte -> cell -> metric pair, read from a disc's executable and font0")
    pt.add_argument("--iso", required=True)
    pt.add_argument("--out", help="CSV to write (default: print the rows that reach a cell)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "extract":
            return cmd_extract(a.tex, a.out)
        if a.cmd == "rebuild":
            return cmd_rebuild(a.ref, a.sheets, a.out, a.metrics)
        if a.cmd == "table":
            return cmd_table(a.iso, a.out)
        return cmd_info(a.tex)
    except FontError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
