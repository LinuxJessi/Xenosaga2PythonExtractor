"""txy.py — Episode II's texture pages (``.txy``) to PNG.

The swizzle, CLUT and PSMT4 routines are copied from
Xenosaga3PythonExtractor/chrtex.py (2026-11-05); the bank handling is new.

A ``.txy`` is what the engine uploads to GS memory for one model or map,
plus a table naming every texture inside it. Episode III embeds the same
block inside its MR packages (``.chr``/``.map``); Episode II ships it as a
file of its own next to the ``.pxy`` mesh. Layout (offsets from the file)::

    +0x00 "txy\\0"
    +0x04 u32 bank count      0 = placeholder, 1 = characters/props,
                              2-3 = field maps, 4/8/23 = skyboxes and animated pages
    +0x08 u32 file size
    +0x10 u32 bank table offsets[count]   (first is always 0x90)

    bank table: record 0 {?, ?, canvas_width, canvas_height(nominal), 0,
                strip_count, entry_count, 0}, then strip_count 0x20-byte
                strip records {data_off, gs_block, width, height, 0, qwords, ?, 4}
                (pixels at data_off + 0x20), then entry_count 0x60-byte entries
                ending right where the first strip's data starts:
        +0x00 gs address   +0x04 fmt (0x13 PSMT8, 0x14 PSMT4, 0 CT32)
        +0x08 w  +0x0C h   +0x10 x  +0x14 y   (in the format's own address space)
        +0x18 cbp  +0x1C flag  +0x20 palx  +0x24 paly  (CLUT tile in the canvas)
        +0x28 duplicate flag   +0x2C 1024 or 512   +0x30 BANK INDEX
        +0x40 name[32]

Every bank carries the same entry table; only the strips differ. The
entry's ``+0x30`` word says which bank its pixels come from: a skybox file
holds 23 faces at the same rect, one per bank. The kit decodes an entry
against the GS state after banks 0..b have been uploaded in order (so a
partial upload keeps the earlier bank's pixels underneath), which is the
same thing as bank b alone wherever bank b covers the rect.

Strips land on the canvas at page = gs_block / 32,
x = (page % (canvas_w/64)) * 64, y = (page // (canvas_w/64)) * 32.

    python txy.py --self-test
    python txy.py FILE.txy OUTDIR        # every entry as PNG
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

PSMCT32, PSMT8, PSMT4 = 0x00, 0x13, 0x14


class TxyError(ValueError):
    pass


# ------------------------------------------------------------------ PNG out

def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def png_bytes(w: int, h: int, rgba: bytes) -> bytes:
    stride = w * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))


def write_png(path, w: int, h: int, rgba: bytes) -> None:
    Path(path).write_bytes(png_bytes(w, h, rgba))


# ------------------------------------------------------------------ parsing

def parse(data: bytes) -> dict:
    """Header, banks (strips) and the entry table of a standalone .txy."""
    if data[:4] != b"txy\x00":
        raise TxyError("not a txy (magic)")
    nbanks = struct.unpack_from("<I", data, 4)[0]
    if nbanks == 0:
        return {"banks": [], "entries": [], "canvas_w": 0}
    if nbanks > 32:
        raise TxyError(f"implausible bank count {nbanks}")
    banks = []
    entries: Optional[list] = None
    for b in range(nbanks):
        tbl = struct.unpack_from("<I", data, 0x10 + 4 * b)[0]
        if not tbl or tbl + 0x40 > len(data):
            raise TxyError(f"bank {b}: table offset {tbl:#x} out of range")
        r0 = struct.unpack_from("<8I", data, tbl)
        cw, nstrips, nent = r0[2], r0[5], r0[6]
        if cw == 0 or cw % 64 or cw > 2048 or nstrips > 64 or nent > 4096:
            raise TxyError(f"bank {b}: implausible table (canvas {cw}, {nstrips} strips, {nent} entries)")
        strips = []
        for k in range(nstrips):
            r = struct.unpack_from("<8I", data, tbl + 0x20 + 0x20 * k)
            if r[2] == 0 or r[3] == 0 or r[0] + 0x20 + r[2] * r[3] * 4 > len(data):
                raise TxyError(f"bank {b}: strip {k} out of range")
            strips.append({"off": r[0], "gs_block": r[1], "w": r[2], "h": r[3]})
        if not strips:
            raise TxyError(f"bank {b}: no strips")
        base = strips[0]["off"] - nent * 0x60
        if base < tbl + 0x20 + 0x20 * nstrips:
            raise TxyError(f"bank {b}: entry table overlaps the strip table")
        ents = []
        for i in range(nent):
            o = base + i * 0x60
            w = struct.unpack_from("<16I", data, o)
            name = data[o + 0x40:o + 0x60].split(b"\x00")[0].decode("latin1")
            ents.append({"i": i, "name": name, "gsaddr": w[0], "fmt": w[1], "w": w[2], "h": w[3],
                         "x": w[4], "y": w[5], "cbp": w[6], "flag": w[7], "palx": w[8],
                         "paly": w[9], "dup": w[10], "bank": w[12], "mip": w[13],
                         "cbp_raw": w[6]})
        if entries is None:
            entries = ents
        banks.append({"table": tbl, "canvas_w": cw, "strips": strips})
    widths = {b["canvas_w"] for b in banks}
    if len(widths) != 1:
        raise TxyError(f"banks disagree on canvas width: {sorted(widths)}")
    return {"banks": banks, "entries": entries or [], "canvas_w": banks[0]["canvas_w"]}


def strip_origin(cw: int, s: dict) -> Tuple[int, int]:
    page = s["gs_block"] // 32
    return (page % (cw // 64)) * 64, (page // (cw // 64)) * 32


def canvas_height(t: dict) -> int:
    cw = t["canvas_w"]
    return max(strip_origin(cw, s)[1] + s["h"] for b in t["banks"] for s in b["strips"])


def compose(data: bytes, t: dict, upto: int) -> Tuple[bytearray, bytearray]:
    """GS memory (as a CT32 canvas) after banks 0..upto are uploaded in
    order, plus a coverage map (1 byte per canvas pixel, set where any
    strip wrote)."""
    cw = t["canvas_w"]
    ch = canvas_height(t)
    canvas = bytearray(cw * ch * 4)
    cover = bytearray(cw * ch)
    for b in t["banks"][:upto + 1]:
        for s in b["strips"]:
            x0, y0 = strip_origin(cw, s)
            src = s["off"] + 0x20
            row = s["w"] * 4
            for y in range(s["h"]):
                d = ((y0 + y) * cw + x0) * 4
                canvas[d:d + row] = data[src + y * row:src + (y + 1) * row]
                c = (y0 + y) * cw + x0
                cover[c:c + s["w"]] = b"\x01" * s["w"]
    return canvas, cover


# --------------------------------------------------------- GS address maps
# (from Xenosaga3PythonExtractor/chrtex.py)

def swizzle8_offset(cw: int, x: int, y: int) -> int:
    """Canvas byte offset holding the PSMT8 index for 2x-space pixel (x,y)."""
    tw = cw * 2
    swap = (((y + 2) >> 2) & 1) * 4
    return ((y & ~0xF) * tw + (x & ~0xF) * 2
            + ((((y & ~3) >> 1) + (y & 1)) & 7) * tw * 2
            + ((x + swap) & 7) * 4 + ((y >> 1) & 1) + ((x >> 2) & 2))


_BLK32 = ((0, 1, 4, 5, 16, 17, 20, 21), (2, 3, 6, 7, 18, 19, 22, 23),
          (8, 9, 12, 13, 24, 25, 28, 29), (10, 11, 14, 15, 26, 27, 30, 31))
_BLK32_XY = {b: (c * 8, r * 8) for r, row in enumerate(_BLK32) for c, b in enumerate(row)}
_BLOCK4 = (0, 2, 8, 10, 1, 3, 9, 11, 4, 6, 12, 14, 5, 7, 13, 15,
           16, 18, 24, 26, 17, 19, 25, 27, 20, 22, 28, 30, 21, 23, 29, 31)
_CW4 = ([0, 1, 2, 3, 4, 5, 6, 7] * 4 + [8, 9, 10, 11, 12, 13, 14, 15] * 4
        + [4, 5, 6, 7, 0, 1, 2, 3] * 4 + [12, 13, 14, 15, 8, 9, 10, 11] * 4)
_CB4 = ([0] * 8 + [2] * 8 + [4] * 8 + [6] * 8) * 2 + ([1] * 8 + [3] * 8 + [5] * 8 + [7] * 8) * 2


def swizzle4_offset(cw: int, x: int, y: int) -> Tuple[int, int]:
    """(canvas byte offset, nibble shift) of PSMT4-space pixel (x, y)."""
    ppr = cw // 64
    page = (y // 128) * ppr + (x // 128)
    px, py = x % 128, y % 128
    block = _BLOCK4[(px // 32) + (py // 16) * 4]
    bx, by = px % 32, py % 16
    col = by // 4
    k = bx + (by % 4) * 32
    word = _CW4[k] ^ (4 if col & 1 else 0)
    nib = _CB4[k]
    bx32, by32 = _BLK32_XY[block]
    cx = (page % ppr) * 64 + bx32 + (word % 8)
    cy = (page // ppr) * 32 + by32 + col * 2 + (word // 8)
    return (cy * cw + cx) * 4 + nib // 2, (nib & 1) * 4


CSM1_SWAP = [(g * 32 + 8 + j, g * 32 + 16 + j) for g in range(8) for j in range(8)]


# ------------------------------------------------- vectorised (numpy) maps
# The scalar maps above are the reference. These are the same arithmetic on
# numpy arrays (first written for the texture-import research, 2026-11-20);
# the self-test checks them against the scalar ones at random points.

def _np():
    import numpy as np
    return np


def swz8_np(cw: int, x, y):
    """swizzle8_offset over arrays of x and y (int64 result)."""
    np = _np()
    x = np.asarray(x, np.int64)
    y = np.asarray(y, np.int64)
    tw = cw * 2
    swap = (((y + 2) >> 2) & 1) * 4
    return ((y & ~0xF) * tw + (x & ~0xF) * 2
            + ((((y & ~3) >> 1) + (y & 1)) & 7) * tw * 2
            + ((x + swap) & 7) * 4 + ((y >> 1) & 1) + ((x >> 2) & 2))


_NP4 = {}


def _np4_tables():
    if not _NP4:
        np = _np()
        _NP4["b4"] = np.array(_BLOCK4, np.int64)
        _NP4["cw4"] = np.array(_CW4, np.int64)
        _NP4["cb4"] = np.array(_CB4, np.int64)
        _NP4["bx"] = np.array([_BLK32_XY[b][0] for b in range(32)], np.int64)
        _NP4["by"] = np.array([_BLK32_XY[b][1] for b in range(32)], np.int64)
    return _NP4


def swz4_np(cw: int, x, y):
    """swizzle4_offset over arrays: (canvas byte offsets, nibble shifts)."""
    np = _np()
    T = _np4_tables()
    x = np.asarray(x, np.int64)
    y = np.asarray(y, np.int64)
    ppr = cw // 64
    page = (y // 128) * ppr + (x // 128)
    px, py = x % 128, y % 128
    block = T["b4"][(px // 32) + (py // 16) * 4]
    bx, by = px % 32, py % 16
    col = by // 4
    k = bx + (by % 4) * 32
    word = T["cw4"][k] ^ np.where(col & 1, 4, 0)
    nib = T["cb4"][k]
    cx = (page % ppr) * 64 + T["bx"][block] + (word % 8)
    cy = (page // ppr) * 32 + T["by"][block] + col * 2 + (word // 8)
    return (cy * cw + cx) * 4 + nib // 2, (nib & 1) * 4


def csm1_positions():
    """pos[i] = storage position (0..255, row-major in the 16x16 tile) of
    logical palette index i, the inverse of CSM1_SWAP."""
    pos = list(range(256))
    for k, m in CSM1_SWAP:
        pos[k], pos[m] = m, k
    return pos


def owner_map(t: dict, upto: int):
    """int64 array [canvas_h, canvas_w]: for every canvas pixel, the offset in
    the block of the 4-byte word the GS holds there after banks 0..upto are
    uploaded, filled in ``compose`` order (later banks and, within a bank,
    later strips win); -1 where no strip covers the pixel."""
    np = _np()
    cw = t["canvas_w"]
    ch = canvas_height(t)
    own = np.full((ch, cw), -1, np.int64)
    for b in t["banks"][:upto + 1]:
        for s in b["strips"]:
            x0, y0 = strip_origin(cw, s)
            ys = np.arange(s["h"], dtype=np.int64)[:, None]
            xs = np.arange(s["w"], dtype=np.int64)[None, :]
            own[y0:y0 + s["h"], x0:x0 + s["w"]] = s["off"] + 0x20 + (ys * s["w"] + xs) * 4
    return own


def canvas_np(data: bytes, own):
    """The composed canvas as a flat uint8 array (what ``compose`` returns),
    built from an owner map."""
    np = _np()
    blk = np.frombuffer(data, np.uint8)
    flat = own.reshape(-1)
    out = np.zeros((flat.size, 4), np.uint8)
    m = flat >= 0
    out[m] = blk[(flat[m][:, None] + np.arange(4)).reshape(-1)].reshape(-1, 4)
    return out.reshape(-1)


def decode_entry_np(canvas, cw: int, ch: int, e: dict):
    """decode_entry on a numpy canvas (``canvas_np``): same result, as bytes,
    or None where decode_entry gives None."""
    np = _np()
    w, h, x0, y0 = e["w"], e["h"], e["x"], e["y"]
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    n = canvas.size
    ys, xs = np.mgrid[0:h, 0:w]
    xs = xs.reshape(-1).astype(np.int64) + x0
    ys = ys.reshape(-1).astype(np.int64) + y0
    if e["fmt"] in (PSMT8, PSMT4):
        if e["fmt"] == PSMT8:
            if e["palx"] + 16 > cw or e["paly"] + 16 > ch:
                return None
            pos = np.array(csm1_positions(), np.int64)
            cells = (e["paly"] + pos // 16) * cw + e["palx"] + pos % 16
        else:
            if e["palx"] + 8 > cw or e["paly"] + 2 > ch:
                return None
            i = np.arange(16, dtype=np.int64)
            cells = (e["paly"] + i // 8) * cw + e["palx"] + i % 8
        pal = canvas.reshape(-1, 4)[cells].copy()
        pal[:, 3] = np.minimum(pal[:, 3].astype(np.int32) * 2, 255).astype(np.uint8)
        if e["fmt"] == PSMT8:
            offs = swz8_np(cw, xs, ys)
            inr = offs < n
            idx = np.zeros(offs.size, np.int64)
            idx[inr] = canvas[offs[inr]]
            return pal[idx].tobytes()
        offs, sh = swz4_np(cw, xs, ys)
        inr = offs < n
        idx = np.zeros(offs.size, np.int64)
        idx[inr] = (canvas[offs[inr]] >> sh[inr]) & 0xF
        out = pal[idx]
        out[~inr] = 0
        return out.tobytes()
    if e["fmt"] == PSMCT32:
        pix = ys * cw + xs
        inr = pix * 4 < n
        out = np.zeros((pix.size, 4), np.uint8)
        out[inr] = canvas.reshape(-1, 4)[pix[inr]]
        out[:, 3] = np.minimum(out[:, 3].astype(np.int32) * 2, 255).astype(np.uint8)
        return out.tobytes()
    return None


def decode_fast(data: bytes) -> Iterator[Tuple[dict, int, int, bytes]]:
    """``decode`` with the numpy maps: the same (entry, w, h, rgba) sequence."""
    t = parse(data)
    if not t["banks"]:
        return
    cw, ch = t["canvas_w"], canvas_height(t)
    cache: dict = {}
    for e in t["entries"]:
        b = min(e["bank"], len(t["banks"]) - 1)
        if b not in cache:
            cache[b] = canvas_np(data, owner_map(t, b))
        rgba = decode_entry_np(cache[b], cw, ch, e)
        if rgba is not None:
            yield e, e["w"], e["h"], rgba


def _px(canvas, off) -> bytes:
    r, g, b, a = canvas[off:off + 4]
    return bytes((r, g, b, min(a * 2, 255)))


def clut256(canvas, cw: int, palx: int, paly: int) -> List[bytes]:
    pal = [_px(canvas, ((paly + ey) * cw + palx + ex) * 4) for ey in range(16) for ex in range(16)]
    for k, m in CSM1_SWAP:
        pal[k], pal[m] = pal[m], pal[k]
    return pal


def clut16(canvas, cw: int, palx: int, paly: int) -> List[bytes]:
    return [_px(canvas, ((paly + ey) * cw + palx + ex) * 4) for ey in range(2) for ex in range(8)]


# ------------------------------------------------------------------ decoding

def decode_entry(canvas: bytearray, cw: int, ch: int, e: dict) -> Optional[bytes]:
    w, h, x0, y0 = e["w"], e["h"], e["x"], e["y"]
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    n = len(canvas)
    if e["fmt"] == PSMT8:
        if e["palx"] + 16 > cw or e["paly"] + 16 > ch:
            return None
        pal = clut256(canvas, cw, e["palx"], e["paly"])
        offs = [swizzle8_offset(cw, x0 + x, y0 + y) for y in range(h) for x in range(w)]
        idx = bytes(canvas[o] if o < n else 0 for o in offs)
        return b"".join(map(pal.__getitem__, idx))
    if e["fmt"] == PSMT4:
        if e["palx"] + 8 > cw or e["paly"] + 2 > ch:
            return None
        pal = clut16(canvas, cw, e["palx"], e["paly"])
        out = []
        for y in range(h):
            for x in range(w):
                o, sh = swizzle4_offset(cw, x0 + x, y0 + y)
                out.append(pal[(canvas[o] >> sh) & 0xF] if o < n else b"\x00\x00\x00\x00")
        return b"".join(out)
    if e["fmt"] == PSMCT32:
        rows = []
        for y in range(h):
            s = ((y0 + y) * cw + x0) * 4
            row = canvas[s:s + w * 4]
            row = row + bytes(w * 4 - len(row))
            rows.append(bytes(c if (i & 3) != 3 else min(c * 2, 255) for i, c in enumerate(row)))
        return b"".join(rows)
    return None


def decode(data: bytes) -> Iterator[Tuple[dict, int, int, bytes]]:
    """Yield (entry, w, h, rgba) for every decodable entry."""
    t = parse(data)
    if not t["banks"]:
        return
    cw, ch = t["canvas_w"], canvas_height(t)
    cache: Dict[int, bytearray] = {}
    for e in t["entries"]:
        b = min(e["bank"], len(t["banks"]) - 1)
        if b not in cache:
            cache[b] = compose(data, t, b)[0]
        rgba = decode_entry(cache[b], cw, ch, e)
        if rgba is not None:
            yield e, e["w"], e["h"], rgba


def entry_filename(e: dict, multi_bank: bool) -> str:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in e["name"]) or "unnamed"
    tag = f"_b{e['bank']:02d}" if multi_bank else ""
    dup = f"_dup{e['dup']}" if e.get("dup") else ""
    return f"{e['i']:03d}_{safe}{tag}{dup}.png"


def decode_to_dir(data: bytes, out_dir) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t = parse(data)
    multi = len(t["banks"]) > 1
    n = 0
    for e, w, h, rgba in decode(data):
        write_png(out / entry_filename(e, multi), w, h, rgba)
        n += 1
    return n


# ------------------------------------------------------------------ self-test

def _build_synthetic() -> Tuple[bytes, List[bytes]]:
    """A 2-bank txy (64-wide canvas, one 64x32 strip per bank) holding a
    16x16 PSMT8 picture per bank at the same rect, each with its own
    palette — the shape of the disc's skybox pages."""
    cw, ch = 64, 32
    images = []
    blobs = []
    for b in range(2):
        canvas = bytearray(cw * ch * 4)
        # palette at (48, 0): logical index i -> colour (i, 255-i, b*100, 0x80)
        logical = [bytes((i, 255 - i, b * 100, 0x80)) for i in range(256)]
        stored = list(logical)
        for k, m in CSM1_SWAP:
            stored[k], stored[m] = stored[m], stored[k]
        for i, px in enumerate(stored):
            o = ((i // 16) * cw + 48 + i % 16) * 4
            canvas[o:o + 4] = px
        idx = bytes(((x * 7 + y * 3 + b * 50) & 0xFF) for y in range(16) for x in range(16))
        for y in range(16):
            for x in range(16):
                canvas[swizzle8_offset(cw, x, y)] = idx[y * 16 + x]
        images.append(b"".join(logical[i][:3] + b"\xff" for i in idx))
        blobs.append(bytes(canvas))
    head = bytearray(0x90)
    head[0:4] = b"txy\x00"
    struct.pack_into("<I", head, 4, 2)
    body = bytearray()
    tables = []
    for b in range(2):
        tbl = 0x90 + len(body)
        tables.append(tbl)
        entry_tbl = tbl + 0x20 + 0x20
        data_off = entry_tbl + 0x60 * 2
        rec = bytearray(0x40 + 0x60 * 2)
        struct.pack_into("<8I", rec, 0, 64, 16, cw, ch, 0, 1, 2, 0)
        struct.pack_into("<8I", rec, 0x20, data_off, 0, cw, ch, 0, 0, 0, 4)
        for i in range(2):
            e = 0x40 + 0x60 * i
            struct.pack_into("<16I", rec, e, 0, PSMT8, 16, 16, 0, 0, 0, 0, 48, 0, 0, 1024, i, 0, 0, 0)
            rec[e + 0x40:e + 0x60] = f"face_{i:02d}".encode().ljust(32, b"\x00")
        body += rec + bytes(0x20) + blobs[b]
    for b, tbl in enumerate(tables):
        struct.pack_into("<I", head, 0x10 + 4 * b, tbl)
    data = bytes(head + body)
    struct.pack_into("<I", head, 8, len(data))
    return bytes(head) + bytes(body), images


def build_block(cw: int, banks: List[List[Tuple[int, int, int, bytes]]],
                entries: List[dict]) -> bytes:
    """A txy block for tests: ``banks`` = per bank a list of strips
    (gs_block, w, h, w*h*4 pixel bytes), ``entries`` = dicts with the parse()
    field names (missing ones are 0, name defaults to tex_NN). Every bank
    carries the same entry table, as on the disc; strip records follow the
    disc's shape {data_off, gs_block, w, h, 0, w*h/4+2, w/8, h/8}."""
    head = bytearray(0x90)
    head[0:4] = b"txy\x00"
    struct.pack_into("<I", head, 4, len(banks))
    body = bytearray()
    ch = max(strip_origin(cw, {"gs_block": g})[1] + h for st in banks for g, _w, h, _p in st)
    for b, strips in enumerate(banks):
        tbl = 0x90 + len(body)
        struct.pack_into("<I", head, 0x10 + 4 * b, tbl)
        ent_off = tbl + 0x20 + 0x20 * len(strips)
        data_off = ent_off + 0x60 * len(entries)
        rec = bytearray(0x20 + 0x20 * len(strips) + 0x60 * len(entries))
        struct.pack_into("<8I", rec, 0, 64, 16, cw, ch, 0, len(strips), len(entries), 0)
        pix = bytearray()
        for k, (g, w, h, px) in enumerate(strips):
            assert len(px) == w * h * 4
            struct.pack_into("<8I", rec, 0x20 + 0x20 * k, data_off + len(pix), g, w, h, 0,
                             w * h // 4 + 2, w // 8, h // 8)
            pix += bytes(0x20) + px
        for i, e in enumerate(entries):
            o = 0x20 + 0x20 * len(strips) + 0x60 * i
            f = [e.get(k, 0) for k in ("gsaddr", "fmt", "w", "h", "x", "y", "cbp", "flag",
                                       "palx", "paly", "dup")]
            struct.pack_into("<16I", rec, o, *f, 1024, e.get("bank", 0), e.get("mip", 0), 0, 0)
            rec[o + 0x40:o + 0x60] = e.get("name", f"tex_{i:02d}").encode().ljust(32, b"\x00")
        body += rec + pix
    struct.pack_into("<I", head, 8, 0x90 + len(body))
    return bytes(head + body)


def _build_synthetic4() -> Tuple[bytes, dict]:
    """One bank, 128-wide canvas, two strips: a 32x32 PSMT4 picture at (0, 0)
    with a 16-colour palette at (64, 0), a 16x16 true-colour entry at (96, 32)
    (raw alpha 0..0x90, so above-0x80 values are present), and a placeholder
    entry with w = h = 0xFFFFFFFF that must decode to nothing. Returns the
    block and {entry name: expected RGBA} computed here, independently of the
    decoders."""
    cw = 128
    canvas = bytearray(cw * 64 * 4)
    pal = [bytes((i * 16, 255 - i * 16, (i * 37) & 0xFF, 0x80 if i else 0)) for i in range(16)]
    for i, c in enumerate(pal):
        o = ((i // 8) * cw + 64 + i % 8) * 4
        canvas[o:o + 4] = c
    want4 = bytearray()
    for y in range(32):
        for x in range(32):
            v = (x // 2 + y * 3) & 0xF
            o, sh = swizzle4_offset(cw, x, y)
            canvas[o] = (canvas[o] & ~(0xF << sh)) | (v << sh)
            want4 += pal[v][:3] + bytes((min(pal[v][3] * 2, 255),))
    want32 = bytearray()
    for y in range(16):
        for x in range(16):
            px = bytes((x * 16, y * 16, 200, (x * 9 + y) % 0x91))
            o = ((32 + y) * cw + 96 + x) * 4
            canvas[o:o + 4] = px
            want32 += px[:3] + bytes((min(px[3] * 2, 255),))
    strips = [(0, cw, 32, bytes(canvas[:cw * 32 * 4])),
              (64, cw, 32, bytes(canvas[cw * 32 * 4:]))]   # page 2 = second page row
    entries = [{"name": "nib", "fmt": PSMT4, "w": 32, "h": 32, "x": 0, "y": 0, "palx": 64, "paly": 0,
                "cbp": 1},
               {"name": "true", "fmt": PSMCT32, "w": 16, "h": 16, "x": 96, "y": 32,
                "cbp": 0xFFFFFFFF},
               {"name": "hole", "fmt": PSMT4, "w": 0xFFFFFFFF, "h": 0xFFFFFFFF}]
    return build_block(cw, [strips], entries), {"nib": bytes(want4), "true": bytes(want32)}


def self_test() -> int:
    data, images = _build_synthetic()
    ok = True
    got = list(decode(data))
    for (e, w, h, rgba), want in zip(got, images):
        good = rgba == want
        print(("ok    " if good else "FAIL  ")
              + f"entry {e['name']} decodes from bank {e['bank']} with that bank's palette")
        ok &= good
    if len(got) != 2:
        ok = False
        print("FAIL  expected two entries")
    try:
        parse(b"txy\x00" + struct.pack("<I", 40) + bytes(0x88))
        ok = False
        print("FAIL  an absurd bank count is refused")
    except TxyError:
        print("ok    an absurd bank count is refused")
    ok &= _self_test_np()
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def _self_test_np() -> bool:
    import random
    np = _np()
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    rnd = random.Random(7)
    agree = True
    for cw in (64, 128, 256, 512, 1024):
        pts = [(rnd.randrange(cw * 2), rnd.randrange(1024)) for _ in range(2000)]
        xs = np.array([p[0] for p in pts])
        ys = np.array([p[1] for p in pts])
        a = swz8_np(cw, xs, ys)
        o4, s4 = swz4_np(cw, xs, ys)
        for k, (x, y) in enumerate(pts):
            if a[k] != swizzle8_offset(cw, x, y) or (o4[k], s4[k]) != swizzle4_offset(cw, x, y):
                agree = False
                break
    check(agree, "numpy PSMT8/PSMT4 maps equal the scalar maps at 2,000 random points, canvas 64-1024")
    data, _ = _build_synthetic()
    check([(e["i"], w, h, r) for e, w, h, r in decode_fast(data)]
          == [(e["i"], w, h, r) for e, w, h, r in decode(data)],
          "decode_fast equals decode on the two-bank PSMT8 block")
    data4, want = _build_synthetic4()
    ref = {e["name"]: r for e, _w, _h, r in decode(data4)}
    fast = {e["name"]: r for e, _w, _h, r in decode_fast(data4)}
    check(ref == want, "decode gives the hand-built PSMT4 and true-colour pictures; the placeholder is skipped")
    check(fast == ref, "decode_fast equals decode on the PSMT4 + true-colour block")
    # owner map: bank 1 re-uploads the top half of bank 0's page
    cw = 64
    b0 = bytes((i * 7) & 0xFF for i in range(cw * 32 * 4))
    b1 = bytes((i * 13 + 5) & 0xFF for i in range(cw * 16 * 4))
    blk = build_block(cw, [[(0, cw, 32, b0)], [(0, cw, 16, b1)]],
                      [{"fmt": PSMCT32, "w": 64, "h": 32, "bank": 1, "cbp": 0xFFFFFFFF}])
    t = parse(blk)
    own = owner_map(t, 1)
    same = canvas_np(blk, own).tobytes() == bytes(compose(blk, t, 1)[0])
    s0, s1 = t["banks"][0]["strips"][0], t["banks"][1]["strips"][0]
    rule = (own[:16] == s1["off"] + 0x20 + 4 * np.arange(16 * cw).reshape(16, cw)).all() and \
        (own[16:] == s0["off"] + 0x20 + 4 * (np.arange(16 * cw).reshape(16, cw) + 16 * cw)).all()
    check(same and rule and (owner_map(t, 0) == s0["off"] + 0x20 + 4 * np.arange(32 * cw).reshape(32, cw)).all(),
          "owner_map follows compose: the later bank owns the rows it re-uploads, bank 0 the rest")
    return ok


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return self_test()
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    n = decode_to_dir(Path(sys.argv[1]).read_bytes(), sys.argv[2])
    print(f"{n} entries -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
