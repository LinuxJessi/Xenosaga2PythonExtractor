"""xtx.py — Episode II's UI and sprite sheets (``.xtx``) to PNG.

Episode II's XTX is Episode I's format unchanged (header {magic, total,
count, 0x10}, 20-byte sub-image records {u16 w, u16 bufw, u16 h, u16 ?,
u32 gs_off, u32 size, u32 addr}, CT32 canvas 256 or 512 wide holding
PSMT8 indices and parked CLUT tiles). The decoder below, from
``_png_chunk`` through ``decode_xtx``, is copied verbatim from
Xenosaga1PythonExtractor/browse.py (2026-11-05), including its palette
selection rules and the regression history in the comments. Episode II's
sheets have no paired model file (``lex``), so only the lex-less paths
run here. (Xenosaga3PythonExtractor's xtx_decode mis-reads the 256-wide
canvas, ``bufw`` 4, which is why ``yosikawa\\font.xtx`` came out garbled.)

Episode-II-specific code is at the end: the ``descriptor`` scan of the
boot executable (see FORMATS.md, "UI sheet palettes").

    python xtx.py FILE.xtx OUT.png
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import Iterable, Optional

def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # filter: none
        raw += rgba[y * stride : (y + 1) * stride]
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    path.write_bytes(png_bytes(width, height, rgba))


# ---------------------------------------------------------------------------
# XTX -> PNG
# ---------------------------------------------------------------------------

_ALPHA_SCALE = bytes(min(a * 2, 255) for a in range(256))


def _plausible_clut(pal: Optional[list[bytes]]) -> bool:
    """Reject palette tiles that are clearly not palettes (empty canvas,
    flat fills). Material palette pointers describe *runtime VRAM* slots,
    which usually — but not always — match the tile's position in the file
    canvas; a pointer into unloaded space reads back near-empty."""
    if pal is None:
        return False
    distinct = len({p[:3] for p in pal})
    return distinct >= 16 and any(p[3] for p in pal)


def _clut_at(canvas: bytearray, clen: int, palx: int, paly: int) -> Optional[list[bytes]]:
    """Read a 256-entry CSM1 palette from the CT32 canvas at (palx, paly)."""
    if palx + 16 > clen or paly + 16 > clen:
        return None
    pal = []
    for ey in range(16):
        row = ((paly + ey) * clen + palx) * 4
        for ex in range(16):
            p = canvas[row + ex * 4 : row + ex * 4 + 4]
            pal.append(bytes((p[0], p[1], p[2], min(p[3] * 2, 255))))
    for g in range(8):  # CSM1 storage order -> logical order
        for j in range(8):
            k, m = g * 32 + 8 + j, g * 32 + 16 + j
            pal[k], pal[m] = pal[m], pal[k]
    return pal


def _clut_tile_like(canvas: bytearray, clen: int, px: int, py: int) -> bool:
    """Does the 16x16 tile at (px, py) look like CLUT data? (all raw alpha
    <= 0x80 — GS 7-bit — and a rich colour count)."""
    if px < 0 or py < 0 or px + 16 > clen or py + 16 > clen:
        return False
    distinct = set()
    for ey in range(16):
        row = ((py + ey) * clen + px) * 4
        for ex in range(16):
            r, g, b, a = canvas[row + ex * 4 : row + ex * 4 + 4]
            if a > 0x80:
                return False
            distinct.add((r, g, b))
    return len(distinct) >= 64


def _scan_for_clut(canvas: bytearray, clen: int
                   ) -> Iterable[tuple[list[bytes], tuple[int, int]]]:
    """Yield candidate palette tiles of a canvas with no other palette source.

    Menu/backdrop textures (casino, UI, dev-folder art) park their CLUT as a
    16x16 CT32 tile in an unused corner of the same canvas; the consuming
    code (e.g. OV11.OVL's texture descriptors) addresses it by GS block
    pointer. Standalone we scan for it: raw alpha <= 0x80 throughout and a
    rich colour count, trying the conventional spots first, then all
    16px-aligned tiles bottom-right first, then the finer 8px grid (CBPs
    can address half-block tiles). Yields (palette, (tile_x, tile_y)) in
    preference order; the caller keeps the first that renders sanely.
    """
    spots = [(0, 224), (240, 240), (224, 240), (176, 240), (112, 64), (128, 0)]
    spots += [(x, y) for y in range(clen - 16, -1, -16)
              for x in range(clen - 16, -1, -16)]
    spots += [(x, y) for y in range(clen - 16, -1, -8)
              for x in range(clen - 16, -1, -8) if x % 16 or y % 16]
    for px, py in spots:
        if _clut_tile_like(canvas, clen, px, py):
            yield _clut_at(canvas, clen, px, py), (px, py)


# PSMCT32 block layout within a 64x32-pixel GS page: 8 cols x 4 rows of
# 8x8-pixel blocks, in this (non-raster) order.
_PSMCT32_BLOCKS = (
    (0, 1, 4, 5, 16, 17, 20, 21),
    (2, 3, 6, 7, 18, 19, 22, 23),
    (8, 9, 12, 13, 24, 25, 28, 29),
    (10, 11, 14, 15, 26, 27, 30, 31),
)
_BLOCK_XY = {blk: (c * 8, r * 8)
             for r, row in enumerate(_PSMCT32_BLOCKS)
             for c, blk in enumerate(row)}


def _cbp_to_xy(cbp: int, clen: int) -> tuple[int, int]:
    """CLUT base pointer (GS block address) -> pixel coords in the canvas."""
    pages_per_row = clen // 64
    page, bip = cbp // 32, cbp % 32
    bx, by = _BLOCK_XY[bip]
    return (page % pages_per_row) * 64 + bx, (page // pages_per_row) * 32 + by


def scan_ovl_descriptors(code_dir: Path
                         ) -> dict[str, list[tuple[int, int, int, int, int]]]:
    """Map lowercase .xtx basename -> texture descriptors (u, v, w, h, cbp)
    read from the OV*.OVL overlays that reference the texture by path.

    Each overlay holds 28-byte descriptors {u32 u, v, w-1, h-1, 0, cbp,
    texslot}: a rect in texture pixels plus the GS block address of the
    CLUT that colours it (see FORMATS.md). Descriptor->texture binding is
    runtime state, so every texture an overlay references gets the
    overlay's whole descriptor list; ``decode_xtx`` keeps the descriptor
    groups whose CBP lands on a CLUT tile parked beside the sheet's own
    palette and which render their rects more coherently than it does.
    """
    import re

    index: dict[str, list[tuple[int, int, int, int, int]]] = {}
    if not code_dir.is_dir():
        return index
    for ovl in sorted(code_dir.glob("OV*.OVL")):
        data = ovl.read_bytes()
        names = {m.group(1).lower().rsplit(b"\\", 1)[-1].decode()
                 for m in re.finditer(rb"data\\([\x20-\x7e]{1,96}?\.xtx)", data,
                                      re.IGNORECASE)}
        if not names:
            continue
        descs: list[tuple[int, int, int, int, int]] = []
        for off in range(0, len(data) - 28, 4):
            u, v, w, h, zero, cbp, slot = struct.unpack_from("<7I", data, off)
            if (zero == 0 and 0 < cbp < 0x800 and u < 1024 and v < 1024
                    and 0 < w + 1 <= 1024 and 0 < h + 1 <= 1024 and slot < 32):
                d = (u, v, w + 1, h + 1, cbp)
                if d not in descs:
                    descs.append(d)
        if not descs:
            continue
        for name in names:
            merged = index.setdefault(name, [])
            merged += [d for d in descs if d not in merged]
    return index


def scan_ovl_cluts(code_dir: Path) -> dict[str, list[int]]:
    """Map lowercase .xtx basename -> CBP palette-pointer candidates: the
    distinct CBPs of ``scan_ovl_descriptors``, in first-seen order."""
    index: dict[str, list[int]] = {}
    for name, descs in scan_ovl_descriptors(code_dir).items():
        cbps = index.setdefault(name, [])
        for d in descs:
            if d[4] not in cbps:
                cbps.append(d[4])
    return index


def _region_noise(idx: bytearray, W: int, pal: list[bytes],
                  u0: int, u1: int, v0: int, v1: int) -> tuple[float, float]:
    """(coherence ratio, opaque fraction) of a region rendered under a
    palette. The ratio is the mean L1 RGB distance of horizontally adjacent
    opaque pixels divided by the same for pixels 16 apart (rows sampled).
    Real art is smooth up close but varied at distance — a correct palette
    scores well under 1; a wrong palette renders dither noise, adjacent ~=
    far, ~1 (the exact garbling of GitHub issue #1). Being a ratio it
    cannot be gamed by washed-out low-contrast palettes the way an absolute
    noise measure can. A palette that hides the region (mostly transparent)
    or flattens it (no distance-16 variation) gets ratio inf. Callers
    choosing a BASE palette must also weigh the opaque fraction: transparent
    pixels are excluded from the ratio, so a wrong palette that maps garbled
    regions to alpha 0 would otherwise look spuriously coherent."""
    adj = adjn = far = farn = opaque = count = 0
    for v in range(v0, v1, 2):
        irow = v * W
        row = [pal[idx[irow + u]] for u in range(u0, u1)]
        n = len(row)
        for i, p in enumerate(row):
            count += 1
            if not p[3]:
                continue
            opaque += 1
            if i + 1 < n and row[i + 1][3]:
                q = row[i + 1]
                adj += (abs(p[0] - q[0]) + abs(p[1] - q[1])
                        + abs(p[2] - q[2]))
                adjn += 1
            if i + 16 < n and row[i + 16][3]:
                q = row[i + 16]
                far += (abs(p[0] - q[0]) + abs(p[1] - q[1])
                        + abs(p[2] - q[2]))
                farn += 1
    opq = opaque / count if count else 0.0
    if not count or opq < 0.3 or not adjn or not farn:
        return float("inf"), opq
    fbar = far / farn
    if fbar < 2.0:
        return float("inf"), opq  # flat render carries no information
    return (adj / adjn) / fbar, opq


def _rgba_noise(buf, stride: int, u0: int, u1: int, v0: int, v1: int) -> float:
    """Coherence ratio of a region of an RGBA buffer (same adjacent÷16-apart
    measure as ``_region_noise``, but on already-rendered pixels)."""
    adj = adjn = far = farn = 0
    for v in range(v0, v1, 2):
        row = v * stride
        for u in range(u0, u1):
            o = (row + u) * 4
            if not buf[o + 3]:
                continue
            r, g, b = buf[o], buf[o + 1], buf[o + 2]
            if u + 1 < u1 and buf[o + 7]:
                adj += (abs(r - buf[o + 4]) + abs(g - buf[o + 5])
                        + abs(b - buf[o + 6]))
                adjn += 1
            if u + 16 < u1 and buf[o + 67]:
                far += (abs(r - buf[o + 64]) + abs(g - buf[o + 65])
                        + abs(b - buf[o + 66]))
                farn += 1
    if not adjn or not farn:
        return float("inf")
    fbar = far / farn
    if fbar < 2.0:
        return float("inf")
    return (adj / adjn) / fbar


def _far_contrast(idx: bytearray, W: int, pal: list[bytes],
                  u0: int, u1: int, v0: int, v1: int) -> float:
    """Mean L1 RGB distance between opaque pixels 16 apart (rows sampled)
    under a palette — the absolute contrast the coherence ratio divides
    by. Near zero means the palette renders the region as a flat sheet."""
    far = farn = 0
    for v in range(v0, v1, 4):
        irow = v * W
        for u in range(u0, u1 - 16, 2):
            p = pal[idx[irow + u]]
            q = pal[idx[irow + u + 16]]
            if p[3] and q[3]:
                far += (abs(p[0] - q[0]) + abs(p[1] - q[1])
                        + abs(p[2] - q[2]))
                farn += 1
    return far / farn if farn else 0.0


def _candidate_score(idx: bytearray, W: int, H: int, pal: list[bytes]) -> float:
    """Whole-image score for a BASE-palette candidate: coherence ratio plus
    a stiff penalty for transparency, so a palette cannot win by hiding
    hard-to-explain pixels behind alpha 0. Lower is better; inf = reject."""
    ratio, opq = _region_noise(idx, W, pal, 0, W, 0, H)
    return ratio + max(0.0, 0.95 - opq) * 2.0


def lex_materials(lex: bytes) -> list[tuple[int, int, int, int, int, int]]:
    """(palx, paly, umin, umax, vmin, vmax) per material of a .lex model —
    the mesh-header materials AND the material blocks spliced into the VIF
    vertex streams (``lex.parse_lex``; a mesh switches material mid-stream:
    Cherenkov's jacket mesh draws its sleeves from a second palette that
    only the in-stream block names). True-colour materials (pal 0xFF) are
    skipped — they have no palette. Falls back to the header-only read if
    the stream parse fails."""
    if lex[:4] != b"lex\x00" or len(lex) < 0xB0:
        return []
    try:
        import lex as lexmod
        model = lexmod.parse_lex(lex)
        mats = []
        for m in model.materials:
            if not m.has_texture or m.true_color:
                continue
            pal_hi, pal_lo = m.pal >> 4, m.pal & 0xF
            palx = (pal_hi % 2) * 256 + (pal_lo // 2) * 32 + (m.pal2 >> 7) * 16
            paly = (pal_hi // 2) * 32 + (pal_lo % 2) * 16
            mats.append((palx, paly, m.umin, m.umax, m.vmin, m.vmax))
        if mats:
            return mats
    except Exception:
        pass
    return _lex_header_materials(lex)


def lex_truecolor_rects(lex: bytes) -> list[tuple[int, int, int, int]]:
    """(umin, umax, vmin, vmax) in CANVAS pixels of every true-colour
    (pal 0xFF) material of a .lex — regions stored as raw CT32 on the
    canvas (hair sheets), to be drawn as such instead of through a palette."""
    if lex[:4] != b"lex\x00":
        return []
    try:
        import lex as lexmod
        model = lexmod.parse_lex(lex)
    except Exception:
        return []
    # only the 0xFF UV encoding (canvas-coordinate rects: the character
    # hair sheets); the 0x0A family used by map models gives texel-space
    # rects that mis-scale here (MC_SYU01's skyline became noise), and the
    # 0x7F eyelash sheets are 4bpp, not CT32
    return [(m.umin, m.umax, m.vmin, m.vmax, m.uv_type == 0x7F) for m in model.materials
            if m.true_color and m.has_texture and m.umax > m.umin
            and m.uv_type in (0xFF, 0x7F)]


def _lex_header_materials(lex: bytes) -> list[tuple[int, int, int, int, int, int]]:
    """Header-only material read (fixed-offset mesh headers: palette byte at
    +0x125, UV info at +0x130 — layout from xenotool's lex_file.h)."""
    (nmesh,) = struct.unpack_from("<I", lex, 0x44)
    if not (0 < nmesh <= 4096):
        return []
    mats = []
    for i in range(nmesh):
        off = 0xB0 + 4 * i
        if off + 4 > len(lex):
            break
        (addr,) = struct.unpack_from("<I", lex, off)
        if addr + 0x190 > len(lex):
            continue
        pal2, pal = lex[addr + 0x124], lex[addr + 0x125]
        if pal == 0xFF:
            continue
        t = lex[addr + 0x130]
        b = lex[addr + 0x131 : addr + 0x140]
        if t == 0x00:
            continue
        if t == 0xFF:
            w, h = b[0] & 0xF, b[2] >> 4
            umin = b[1] * 4
            vmin = (b[4] & 0xF) * 64 + (b[3] >> 7) * 32
            umax, vmax = umin + (w + 1) * 16, vmin + (h + 1) * 16
        else:  # 0x0a and friends
            umin = (b[0] & 0x3F) << 4
            vmin = b[2]
            umax = ((b[1] << 2) | (b[0] >> 6)) + 1
            vmax = ((b[4] << 6) | (b[3] >> 2)) + 1
        pal_hi, pal_lo = pal >> 4, pal & 0xF
        palx = (pal_hi % 2) * 256 + (pal_lo // 2) * 32 + (pal2 >> 7) * 16
        paly = (pal_hi // 2) * 32 + (pal_lo % 2) * 16
        mats.append((palx, paly, umin, umax, vmin, vmax))
    return mats


def decode_xtx(data: bytes, lex: bytes | list[bytes] = b"",
               cbp_candidates: Optional[list[int]] = None,
               ovl_rects: Optional[list[tuple[int, int, int, int, int]]] = None,
               ) -> Optional[tuple[int, int, bytes, str]]:
    """(width, height, RGBA, palette_source) of the composed texture, or None.

    ``lex`` — the paired model file, when there is one; its materials say
    which palette tile in the canvas colours which UV region. A LIST of lex
    blobs merges materials from companion models that share the atlas
    (``kosmos_face.lex`` has no ``kosmos_face.xtx`` — its meshes bind
    palettes onto ``kosmos.xtx``'s canvas, e.g. the red eye tile).
    ``cbp_candidates`` — CLUT base pointers from the consuming overlay's
    texture descriptors (``scan_ovl_cluts``); when one of them lands on a
    plausible palette tile it is ground truth and wins over guessing.
    ``ovl_rects`` — the full descriptors (``scan_ovl_descriptors``): for a
    model-less UI sheet they are its materials, each rect painted with
    its own parked palette (card pips vs court cards vs card back).
    palette_source is one of "embedded", "lex", "ovl", "scan", "gray".
    """
    if data[:4] != b"XTX\x00" or len(data) < 0x24:
        return None
    _total, count, hdr_addr = struct.unpack_from("<III", data, 4)
    if not (1 <= count <= 64) or hdr_addr + 20 * count > len(data):
        return None
    subs = []
    for i in range(count):
        base = hdr_addr + 20 * i
        w, bufw, h = struct.unpack_from("<HHH", data, base)
        gs_off, _size, addr = struct.unpack_from("<III", data, base + 8)
        if addr + 32 + w * h * 4 > len(data):
            return None
        subs.append((w, bufw, h, gs_off, addr))
    bufw = subs[0][1] or 8
    clen = {4: 256, 8: 512}.get(bufw)
    if clen is None:
        return None
    canvas = bytearray(clen * clen * 4)  # CT32 canvas, clen x clen pixels
    placed: list[tuple[int, int, int, int]] = []  # (w, h, x0, y0) per sub
    for w, _, h, gs_off, addr in subs:
        px = data[addr + 32 : addr + 32 + w * h * 4]
        # sub-image placement in the canvas: gs_off counts 32 per pixel
        # column and clen*32 per 32-pixel row band — i.e. 4096 per 128-px
        # column step, 65536 (clen 512) / 32768 (clen 256) per 128-row
        # step. Fitted on all 166 multi-sub layouts on the retail disc:
        # the 42 two-part character atlases (andrew, jr1, momo2, virgil1
        # …: second part at 8192/12288 -> x 256/384) only decode under
        # their own .lex palettes with this placement, and the parked
        # 16x16 CLUT tiles at 4096 land at x=128, where the .lex
        # materials point (the old 64-px block formula put them at 64 —
        # the "hama.lex points at (128,0) but the tile is at (64,0)"
        # caveat was this bug, not a VRAM-vs-file mismatch).
        y0 = (gs_off // (clen * 32)) * 32
        x0 = (gs_off % (clen * 32)) // 32
        if x0 + w > clen or y0 + h > clen:
            continue
        for y in range(h):
            dst = ((y0 + y) * clen + x0) * 4
            canvas[dst : dst + w * 4] = px[y * w * 4 : (y + 1) * w * 4]
        placed.append((w, h, x0, y0))
    # 16x16 sub-images are palette tiles: composed into the canvas (materials
    # address them there) but not part of the visible image extent
    pal_tiles = [(x0, y0) for w, h, x0, y0 in placed
                 if w == 16 and h == 16 and count > 1]
    if placed and len(pal_tiles) == len(placed):
        # nothing left as art — a tiny all-16x16 sprite file (e.g. the 1P/2P
        # indicators): keep only tiles that truly read as CLUT data (first
        # one if all do), and let the rest render as image
        looks = [(x0, y0) for x0, y0 in pal_tiles
                 if _clut_tile_like(canvas, clen, x0, y0)]
        pal_tiles = (looks if len(looks) < len(pal_tiles) else looks[:1])
    embedded_pal: Optional[list[bytes]] = None
    if pal_tiles:
        embedded_pal = _clut_at(canvas, clen, *pal_tiles[0])
    max_x = max_y = 0
    for w, h, x0, y0 in placed:
        if (x0, y0) in pal_tiles:
            continue
        max_x = max(max_x, (x0 + w) * 2)
        max_y = max(max_y, (y0 + h) * 2)
    if not max_x or not max_y:
        return None

    # The canvas holds PSMT8 indices swizzled into CT32; unswizzle the
    # cropped region ("unswizzle8", the widely shared PS2 routine).
    W, H, tw = max_x, max_y, clen * 2
    idx = bytearray(W * H)
    for y in range(H):
        block_row = (y & ~0xF) * tw
        swap_selector = (((y + 2) >> 2) & 1) * 4
        col_row = ((((y & ~3) >> 1) + (y & 1)) & 7) * tw * 2
        byte_y = (y >> 1) & 1
        drow = y * W
        for x in range(W):
            idx[drow + x] = canvas[
                block_row + (x & ~0xF) * 2 + col_row
                + ((x + swap_selector) & 7) * 4 + byte_y + ((x >> 2) & 2)]

    lexes = lex if isinstance(lex, list) else ([lex] if lex else [])
    mats = [m for lx in lexes for m in lex_materials(lx)]

    # palette selection: embedded tile > lex material > OVL descriptor CBP >
    # corner scan > grayscale. pal_xy = the chosen tile's canvas coords when
    # we are certain the tile IS a CLUT (so it can be blanked from output).
    pal_xy: Optional[tuple[int, int]] = None
    source = "gray"
    base_pal = embedded_pal
    if base_pal is not None:
        source = "embedded"
    if base_pal is None and mats:
        cand = _clut_at(canvas, clen, mats[0][0], mats[0][1])
        if _plausible_clut(cand):
            base_pal, source = cand, "lex"
    if base_pal is None and cbp_candidates:
        best = 1.2  # only accept OVL candidates that render coherently
        for cbp in cbp_candidates:
            px, py = _cbp_to_xy(cbp, clen)
            if not _clut_tile_like(canvas, clen, px, py):
                continue
            cand = _clut_at(canvas, clen, px, py)
            if not _plausible_clut(cand):
                continue
            s = _candidate_score(idx, W, H, cand)
            if s < best:
                best, base_pal, pal_xy, source = s, cand, (px, py), "ovl"
    if base_pal is None:
        # best of the first couple dozen scan candidates (the true CLUT is
        # near-always parked early in the spot order); first-hit is not
        # enough — a wrong tile can look acceptable while a later one is
        # clearly better, and vice versa
        best, tried = 1.2, 0
        for cand, xy in _scan_for_clut(canvas, clen):
            if not _plausible_clut(cand):
                continue
            s = _candidate_score(idx, W, H, cand)
            if s < best:
                best, base_pal, pal_xy, source = s, cand, xy, "scan"
            tried += 1
            if tried >= 24 or best < 0.45:
                break
    # UI sheets (no model): the artists park the sheet's own CLUT at one of
    # the conventional corner spots, and the sheet often has transparent
    # cut-outs (slot-reel windows, the window frame). The ranking above
    # penalises transparency — right for character atlases, where a wrong
    # palette hides garbage behind alpha 0, wrong here: on the casino slot
    # frame and window sheet it preferred washed-out grey palettes parked
    # for other sub-images. When the first conventional-spot palette
    # renders at least half opaque and, penalty aside, about as coherent
    # as the ranked winner, keep it — the v1.0.0 first-hit behaviour that
    # was right on those sheets. (v1.1.0 required < 95 % opaque, which
    # left the fully opaque casino gallery sketches and the slot sheet
    # with a monotone palette the coherence score prefers — the ratio is
    # biased toward washed-out palettes, see _region_noise.)
    if not lexes and base_pal is not None and source in ("ovl", "scan"):
        for cand, xy in _scan_for_clut(canvas, clen):
            if not _plausible_clut(cand):
                continue
            if cand != base_pal:
                ratio_f, opq_f = _region_noise(idx, W, cand, 0, W, 0, H)
                ratio_b, _ = _region_noise(idx, W, base_pal, 0, W, 0, H)
                # a ranked winner that renders the sheet nearly flat (the
                # window sheet's pick was near-black) is no evidence at all
                flat_b = _far_contrast(idx, W, base_pal, 0, W, 0, H) < 12.0
                if 0.5 <= opq_f and (ratio_f <= ratio_b * 1.25
                                     or flat_b):
                    base_pal, pal_xy, source = cand, xy, "scan"
            break
    base_lut = ([bytes(p) for p in base_pal] if base_pal
                else [bytes((i, i, i, 255)) for i in range(256)])

    out = bytearray(W * H * 4)
    for y in range(H):
        drow = y * W * 4
        irow = y * W
        for x in range(W):
            out[drow + x * 4 : drow + x * 4 + 4] = base_lut[idx[irow + x]]

    def paint(pal, u0, u1, v0, v1):
        for v in range(v0, v1):
            drow = v * W * 4
            irow = v * W
            for u in range(u0, u1):
                out[drow + u * 4 : drow + u * 4 + 4] = pal[idx[irow + u]]

    # per-material regions override the base palette — unless the material's
    # palette renders its own UV rect clearly noisier than the base palette
    # does (material palx/paly are runtime-VRAM slots that only sometimes
    # match the file canvas; a plausible-but-wrong tile used to garble
    # exactly its UV rect — GitHub issue #1)
    painted: list[tuple[int, int, int, int]] = []
    pals: list[list[bytes]] = []
    for palx, paly, umin, umax, vmin, vmax in mats:
        pal = _clut_at(canvas, clen, palx, paly)
        if pal is None or not _plausible_clut(pal):
            continue
        if all(pal != p for p in pals):
            pals.append(pal)
        if pal == base_pal:
            continue
        v0, v1 = max(0, vmin), min(H, vmax)
        u0, u1 = max(0, umin), min(W, umax)
        if v1 <= v0 or u1 <= u0:
            continue
        mn, _ = _region_noise(idx, W, pal, u0, u1, v0, v1)
        bn, _ = (_region_noise(idx, W, base_pal, u0, u1, v0, v1) if base_pal
                 else (mn, 0.0))
        if mn > bn * 1.3:
            continue
        paint(pal, u0, u1, v0, v1)
        painted.append((u0, u1, v0, v1))

    # UI sheets (no model) consumed by an overlay: the overlay's texture
    # descriptors are the sheet's materials — a rect in texture pixels plus
    # the CBP of the CLUT that colours it (the card sheets draw pips, court
    # cards and the card back from three parked palettes; the slot sheet
    # keys nine UI elements to a column of nine). Every texture the
    # overlay references gets the same descriptor list, so a descriptor is
    # trusted only as part of a GROUP (all rects sharing one CBP) that
    # (1) lands on a CLUT tile parked beside the sheet's own palette —
    # chained within 32 px of the base tile, the way artists park a
    # sheet's palette strip — and (2) renders its rects clearly smoother
    # than the base palette does (median adjacent/far ratio <= 0.9 of the
    # base's) without hiding them (median opacity >= half the base's;
    # text strips legitimately turn their background transparent).
    # Measured on OV11: true groups score 0.31-0.85, the same descriptors
    # applied to the wrong sheet 0.99-12 (poker pay table, exchange, BG).
    used_tiles: set[tuple[int, int]] = set()  # accepted groups' CLUT tiles
    if (not lexes and ovl_rects and base_pal is not None
            and pal_xy is not None):
        groups: dict[tuple[int, int], list[tuple[int, int, int, int]]] = {}
        for u, v, w, h, cbp in ovl_rects:
            u0, v0, u1, v1 = u, v, min(W, u + w), min(H, v + h)
            if u0 >= W or v0 >= H or u1 - u0 < 8 or v1 - v0 < 8:
                continue
            tile = _cbp_to_xy(cbp, clen)
            if tile == pal_xy or not _clut_tile_like(canvas, clen, *tile):
                continue
            groups.setdefault(tile, []).append((u0, u1, v0, v1))
        parked = {pal_xy}
        grown = True
        while grown:
            grown = False
            for t in groups:
                if t not in parked and any(abs(t[0] - q[0]) <= 32
                                           and abs(t[1] - q[1]) <= 32
                                           for q in parked):
                    parked.add(t)
                    grown = True
        accepted: list[tuple[int, int, int, int, int, list[bytes]]] = []
        for tile, rects in groups.items():
            if tile not in parked:
                continue
            pal = _clut_at(canvas, clen, *tile)
            if not _plausible_clut(pal) or pal == base_pal:
                continue
            ratios: list[float] = []
            opqs: list[float] = []
            measured = []
            for u0, u1, v0, v1 in rects:
                mn, mo = _region_noise(idx, W, pal, u0, u1, v0, v1)
                bn, bo = _region_noise(idx, W, base_pal, u0, u1, v0, v1)
                measured.append((u0, u1, v0, v1, mn, mo))
                if mn != float("inf") and bn != float("inf") and bn > 0:
                    ratios.append(mn / bn)
                if bo > 0:
                    opqs.append(mo / bo)
            if not ratios:
                continue
            ratios.sort()
            opqs.sort()
            if (ratios[len(ratios) // 2] > 0.9
                    or (opqs and opqs[len(opqs) // 2] < 0.5)):
                continue
            for u0, u1, v0, v1, mn, mo in measured:
                # a rect the palette renders as one flat opaque colour is
                # no evidence either way; leave it to the base palette
                if mn == float("inf") and mo > 0.05:
                    continue
                accepted.append(((u1 - u0) * (v1 - v0), u0, u1, v0, v1, pal))
            used_tiles.add(tile)
            source = "ovl"
        accepted.sort(key=lambda a: -a[0])  # big rects first: specific win
        for _area, u0, u1, v0, v1, pal in accepted:
            paint(pal, u0, u1, v0, v1)
            painted.append((u0, u1, v0, v1))

    # regions no parsed material covers (their materials hide in the VIF
    # vertex streams): per 64px block, if some known palette renders it
    # clearly smoother than the base palette, use it — this un-garbles the
    # issue-#1 char atlases without touching regions base already explains.
    # The candidate pool is the parsed material palettes PLUS every
    # CLUT-looking tile parked in the canvas — map atlases (MC_*.xtx) bind
    # several parked CLUTs through VIF-stream materials we never parse, so
    # the right palette is often in the canvas but absent from ``pals``.
    if base_pal is not None:
        for cand, _xy in _scan_for_clut(canvas, clen):
            if len(pals) >= 14:
                break
            if _plausible_clut(cand) and all(cand != p for p in pals):
                pals.append(cand)
    # Model-bound textures only (a .lex exists), like the raw-CT32 pass: on
    # lex-less UI sheets every rescue this pass made was wrong (grey blobs
    # over the card-tutorial text, a noise strip on the casino menu, an
    # orange tile on the slot machine) — checked 2026-09-24 across all 32
    # lex-less files where it fired.
    if base_pal is not None and len(pals) > 1 and lexes:
        for v0 in range(0, H, 64):
            v1 = min(H, v0 + 64)
            for u0 in range(0, W, 64):
                u1 = min(W, u0 + 64)
                cov = any(u0 >= pu0 and u1 <= pu1 and v0 >= pv0 and v1 <= pv1
                          for pu0, pu1, pv0, pv1 in painted)
                if cov:
                    continue
                bn, _ = _region_noise(idx, W, base_pal, u0, u1, v0, v1)
                if bn == float("inf") or bn < 0.75:  # base looks fine; keep it
                    continue
                best_pal, best_n = None, bn
                for pal in pals:
                    if pal == base_pal:
                        continue
                    n, _ = _region_noise(idx, W, pal, u0, u1, v0, v1)
                    if n < best_n:
                        best_pal, best_n = pal, n
                if best_pal is not None and best_n < bn * 0.5:
                    paint(best_pal, u0, u1, v0, v1)

    # fine-grained rescue with TRUSTED palettes only: the parsed materials'
    # rects don't cover the whole atlas (more materials hide in the VIF
    # vertex streams), but the missing regions are almost always outfit art
    # continuing past a parsed rect — rendered by one of the SAME material
    # palettes. 32px blocks outside every painted rect, visibly imperfect
    # under base (bn > 0.25), repaint when a material palette renders them
    # near-perfectly (n < 0.25 and < 0.6*bn — thresholds fitted on the
    # KOS-MOS atlas: true fixes measured <= 0.16, false repaints >= 0.51).
    if base_pal is not None and mats:
        mat_pals: list[list[bytes]] = []
        for palx, paly, *_ in mats:
            mp = _clut_at(canvas, clen, palx, paly)
            if mp is not None and _plausible_clut(mp) \
                    and all(mp != q for q in mat_pals):
                mat_pals.append(mp)
        for v0 in range(0, H, 32):
            v1 = min(H, v0 + 32)
            for u0 in range(0, W, 32):
                u1 = min(W, u0 + 32)
                if any(u0 >= pu0 and u1 <= pu1 and v0 >= pv0 and v1 <= pv1
                       for pu0, pu1, pv0, pv1 in painted):
                    continue
                bn, _ = _region_noise(idx, W, base_pal, u0, u1, v0, v1)
                if bn <= 0.25 or bn == float("inf"):
                    continue
                best_pal, best_n = None, min(0.25, bn * 0.6)
                for mp in mat_pals:
                    if mp == base_pal:
                        continue
                    n, _ = _region_noise(idx, W, mp, u0, u1, v0, v1)
                    if n < best_n:
                        best_pal, best_n = mp, n
                if best_pal is not None:
                    paint(best_pal, u0, u1, v0, v1)

    # true-colour materials named by the model (pal 0xFF): their rect is a
    # raw CT32 region of the canvas — draw it as such, no heuristic needed
    # (Albedo's white hair rendered half through a skin palette when the
    # per-block noise test below fired on only some of its blocks)
    # — guarded by the same coherence comparison as the block pass: two
    # map atlases (MC_KAS11, MC_SYU01) declare pal-0xFF rects over
    # ordinary paletted art, which reads as noise taken as CT32.
    # Eyelash rects (UV type 0x7F) are drawn unconditionally: the sheet is
    # a mostly transparent dark alpha gradient the lash polygons stretch
    # into strokes, which the coherence/opacity guard would reject.
    truecolor = [r for lx in lexes for r in lex_truecolor_rects(lx)]
    for cu0, cu1, cv0, cv1, is_lash in truecolor:
        cu0, cv0 = max(0, cu0), max(0, cv0)
        cu1, cv1 = min(clen, cu1, W // 2), min(clen, cv1, H // 2)
        if cu1 <= cu0 or cv1 <= cv0:
            continue
        if not is_lash:
            on = _rgba_noise(out, W, cu0 * 2, cu1 * 2, cv0 * 2, cv1 * 2)
            cn = _rgba_noise(canvas, clen, cu0, cu1, cv0, cv1)
            if not (cn < on):
                continue
            # and the region must hold pixels: a rect that is mostly alpha-0
            # as CT32 (MC_SYU01's declared rects) is not a hair sheet
            opaque = sum(1 for y in range(cv0, cv1, 2) for x in range(cu0, cu1, 2)
                         if canvas[((y * clen) + x) * 4 + 3])
            if opaque * 4 < 0.4 * (cu1 - cu0) * (cv1 - cv0):
                continue
        for y in range(cv0 * 2, cv1 * 2):
            drow = y * W * 4
            crow = (y // 2) * clen
            for x in range(cu0 * 2, cu1 * 2):
                o = (crow + x // 2) * 4
                out[drow + x * 4] = canvas[o]
                out[drow + x * 4 + 1] = canvas[o + 1]
                out[drow + x * 4 + 2] = canvas[o + 2]
                out[drow + x * 4 + 3] = _ALPHA_SCALE[canvas[o + 3]]
        painted.append((cu0 * 2, cu1 * 2, cv0 * 2, cv1 * 2))

    # raw CT32 regions: a canvas can mix 8bpp paletted art with TRUE-COLOUR
    # pixel regions (KOS-MOS's hair-strand sheets — issue #1's last holdout).
    # No palette can ever render those. Per 64px block: if the current render
    # still reads as dither noise (ratio >= 0.9; every correctly-paletted
    # block on the KOS-MOS atlas measures below that) but the canvas read
    # directly as CT32 is more coherent, draw the block from the canvas —
    # each canvas pixel covers 2x2 output pixels.
    # Only for model-bound textures (a .lex exists): true-colour regions are
    # a character-model feature, and on the lex-less UI sheets the pass
    # mis-fired on dithered 8bpp art — the casino slot reels (two indices
    # alternating) read as a "coherent" checker when taken as CT32 and were
    # repainted blue/yellow (regression vs the July build, found 2026-09-24).
    for v0 in (range(0, H, 64) if lexes else ()):
        v1 = min(H, v0 + 64)
        for u0 in range(0, W, 64):
            u1 = min(W, u0 + 64)
            on = _rgba_noise(out, W, u0, u1, v0, v1)
            if on < 0.9 or on == float("inf"):
                continue
            cn = _rgba_noise(canvas, clen, u0 // 2, u1 // 2, v0 // 2, v1 // 2)
            if cn >= on:
                continue
            for y in range(v0, v1):
                crow = (y // 2) * clen
                drow = y * W * 4
                for x in range(u0, u1):
                    o = (crow + x // 2) * 4
                    out[drow + x * 4] = canvas[o]
                    out[drow + x * 4 + 1] = canvas[o + 1]
                    out[drow + x * 4 + 2] = canvas[o + 2]
                    out[drow + x * 4 + 3] = _ALPHA_SCALE[canvas[o + 3]]

    # CLUT tiles are palette data, not art: blank the chosen tile plus the
    # connected cluster of palette-looking tiles around it (card sheets etc.
    # park a strip of colour-variant CLUTs together) when it falls inside
    # the visible extent — the "square of noise in the corner" of issue #1
    if pal_xy is not None:
        cluster = {pal_xy}
        frontier = [pal_xy]
        while frontier and len(cluster) <= 16:
            cx, cy = frontier.pop()
            for dx in (-8, 0, 8):
                for dy in (-8, 0, 8):
                    t = (cx + dx, cy + dy)
                    if (t not in cluster
                            and _clut_tile_like(canvas, clen, t[0], t[1])):
                        cluster.add(t)
                        frontier.append(t)
        if len(cluster) > 16:
            # a real palette strip is a handful of tiles; a sprawling
            # "cluster" means the flood fill wandered into colourful art
            # (card faces, object sprites) — blank only the CLUT itself
            cluster = {pal_xy}
        if len(cluster) * 32 * 32 > W * H * 0.06:
            cluster = set()  # parked CLUTs are a sliver of a big canvas;
            # on a small texture the "tile" is likelier art — leave it
        for tx, ty in cluster | used_tiles:
            px0, py0 = tx * 2, ty * 2
            for y in range(max(0, py0), min(H, py0 + 32)):
                drow = y * W * 4
                for x in range(max(0, px0), min(W, px0 + 32)):
                    out[drow + x * 4 : drow + x * 4 + 4] = b"\x00\x00\x00\x00"
    return W, H, bytes(out), source




# ---------------------------------------------------------------- Episode II

def _compose_ct32(data: bytes):
    """(canvas, clen, placed) with the Episode I placement rule."""
    _total, count, hdr_addr = struct.unpack_from("<III", data, 4)
    subs = []
    for i in range(count):
        base = hdr_addr + 20 * i
        w, bufw, h = struct.unpack_from("<HHH", data, base)
        gs_off, _size, addr = struct.unpack_from("<III", data, base + 8)
        subs.append((w, bufw, h, gs_off, addr))
    clen = {4: 256, 8: 512}.get(subs[0][1] or 8)
    if clen is None:
        return None
    canvas = bytearray(clen * clen * 4)
    placed = []
    for w, _, h, gs_off, addr in subs:
        y0 = (gs_off // (clen * 32)) * 32
        x0 = (gs_off % (clen * 32)) // 32
        if x0 + w > clen or y0 + h > clen:
            continue
        px = data[addr + 32:addr + 32 + w * h * 4]
        for y in range(h):
            dst = ((y0 + y) * clen + x0) * 4
            canvas[dst:dst + w * 4] = px[y * w * 4:(y + 1) * w * 4]
        placed.append((w, h, x0, y0))
    return canvas, clen, placed


def _parked_corner(data: bytes):
    """(canvas, clen, w, h) for a sheet that parks its 256-colour palette in
    the top-right 16x16 corner of its single canvas image, with nothing below
    it: the quest board's 50 citizen portraits and 18 item cards
    (``iwase\\\\img\\\\*``) and ``omiya\\\\botan``. None for anything else."""
    if data[:4] != b"XTX\x00":
        return None
    comp = _compose_ct32(data)
    if comp is None:
        return None
    canvas, clen, placed = comp
    if len(placed) != 1:
        return None
    w, h, x0, y0 = placed[0]
    if x0 or y0 or w < 32 or w >= clen:
        return None
    px = w - 16
    if not _clut_tile_like(canvas, clen, px, 0):
        return None
    for y in range(16, h):
        row = (y * clen + px) * 4
        if any(canvas[row:row + 64]):
            return None
    return canvas, clen, w, h


def _decode_parked(canvas: bytearray, clen: int, w: int, h: int) -> tuple:
    """Unswizzle the art left of the parked palette and colour it with that
    palette, nothing else (2026-11-05: the ranking passes chose a wrong tile
    for 18 of these 69 sheets although the corner one renders every one
    correctly). Empty canvas columns between the art and the palette are
    trimmed."""
    art = w - 16
    while art > 0 and not any(canvas[(y * clen + art - 1) * 4:(y * clen + art) * 4]
                              for y in range(h)):
        art -= 1
    W, H, tw = art * 2, h * 2, clen * 2
    pal = _clut_at(canvas, clen, w - 16, 0)
    out = bytearray(W * H * 4)
    for y in range(H):
        block_row = (y & ~0xF) * tw
        swap_selector = (((y + 2) >> 2) & 1) * 4
        col_row = ((((y & ~3) >> 1) + (y & 1)) & 7) * tw * 2
        byte_y = (y >> 1) & 1
        o = y * W * 4
        for x in range(W):
            v = canvas[block_row + (x & ~0xF) * 2 + col_row
                       + ((x + swap_selector) & 7) * 4 + byte_y + ((x >> 2) & 2)]
            out[o + x * 4:o + x * 4 + 4] = pal[v]
    return W, H, bytes(out), "corner"


def _mask_corner(data: bytes):
    """(canvas, clen, w, h) for a frame mask (``*_alpha.xtx``): the parked-
    corner layout with a two-colour palette, entry 0 clear and entry 1 opaque
    white, and art drawn only in indices 0 and 1 (a rounded rectangle the
    size of the portraits, item cards or door pictures beside it)."""
    if data[:4] != b"XTX\x00":
        return None
    comp = _compose_ct32(data)
    if comp is None:
        return None
    canvas, clen, placed = comp
    if len(placed) != 1:
        return None
    w, h, x0, y0 = placed[0]
    if x0 or y0 or w < 32 or w >= clen:
        return None
    pal = _clut_at(canvas, clen, w - 16, 0)
    if pal is None or pal[0][3] != 0 or pal[1] != b"\xff\xff\xff\xff":
        return None
    for y in range(h):
        if any(b > 1 for b in canvas[(y * clen) * 4:(y * clen + w - 16) * 4]):
            return None
        if y >= 16 and any(canvas[(y * clen + w - 16) * 4:(y * clen + w) * 4]):
            return None
    return canvas, clen, w, h


def apply_mask(img: tuple, mask: tuple) -> Optional[tuple]:
    """Cut a decoded sheet with its frame mask, as the game draws it: crop to
    the mask's size and multiply alpha. Both are ``decode`` results; None when
    the sheet is smaller than the mask."""
    W, H, rgba = img[0], img[1], img[2]
    MW, MH, mrgba = mask[0], mask[1], mask[2]
    if W < MW or H < MH:
        return None
    # crop to the mask's visible rectangle (its clear margin is not art)
    xs = [x for x in range(MW) if any(mrgba[(y * MW + x) * 4 + 3] for y in range(MH))]
    ys = [y for y in range(MH) if any(mrgba[(y * MW + x) * 4 + 3] for x in range(MW))]
    if not xs or not ys:
        return None
    x0, x1, y0, y1 = xs[0], xs[-1] + 1, ys[0], ys[-1] + 1
    OW, OH = x1 - x0, y1 - y0
    out = bytearray(OW * OH * 4)
    for y in range(OH):
        for x in range(OW):
            s = ((y + y0) * W + x + x0) * 4
            d = (y * OW + x) * 4
            out[d:d + 3] = rgba[s:s + 3]
            out[d + 3] = rgba[s + 3] * mrgba[((y + y0) * MW + x + x0) * 4 + 3] // 255
    return OW, OH, bytes(out), img[3] + "+mask"


def decode(data: bytes) -> Optional[tuple]:
    """Episode II entry point: frame masks (``_mask_corner``) and sheets with
    a palette parked in the canvas corner (``_parked_corner``) first; then
    Episode I's paletted decoder; a sheet for which it finds no palette at all
    and whose canvas holds no CLUT-like tile is true colour
    (``yajima\\\\titleobj.xtx``, the title menu words), drawn from the canvas
    as CT32 with GS alpha doubled."""
    if data[:4] == b"ARX\x00":
        import arx
        data = arx.decompress(data)
    mask = _mask_corner(data)
    if mask is not None:
        W, H, rgba, _ = _decode_parked(*mask)
        return W, H, rgba, "mask"
    parked = _parked_corner(data)
    if parked is not None:
        return _decode_parked(*parked)
    r = decode_xtx(data)
    if r is not None and r[3] != "gray":
        return r
    comp = _compose_ct32(data) if data[:4] == b"XTX\x00" else None
    if comp is None:
        return r
    canvas, clen, placed = comp
    if not placed:
        return r
    if any(_clut_tile_like(canvas, clen, x, y)
           for y in range(0, clen - 15, 8) for x in range(0, clen - 15, 8)):
        return r
    W = max(x0 + w for w, h, x0, y0 in placed)
    H = max(y0 + h for w, h, x0, y0 in placed)
    out = bytearray(W * H * 4)
    for y in range(H):
        row = canvas[(y * clen) * 4:(y * clen + W) * 4]
        o = y * W * 4
        out[o:o + W * 4] = row
        for x in range(W):
            out[o + x * 4 + 3] = _ALPHA_SCALE[row[x * 4 + 3]]
    return W, H, bytes(out), "ct32"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    r = decode(Path(sys.argv[1]).read_bytes())
    if r is None:
        print("not a decodable XTX")
        return 1
    w, h, rgba, source = r
    write_png(Path(sys.argv[2]), w, h, rgba)
    print(f"{w}x{h} palette from {source} -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
