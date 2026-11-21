"""pngread.py — read any ordinary PNG to 8-bit RGBA, standard library + numpy.

The chunk walk and CRC check follow the kit's own ``font.png_to_gray``; the
rest (every colour type and bit depth, palettes with tRNS, all five filters,
Adam7 interlacing) is new here. Paint programs and Blender write PNGs in many
shapes (palette PNGs, 16-bit, gray with alpha, interlaced), and
``texture-import`` must read every one of them back exactly.

    read_png(data) -> (width, height, rgba bytes)

16-bit samples become 8-bit as (v * 255 + 32767) // 65535, which gives a
back exactly when v = a * 257 (how 8-bit images are widened). gAMA, iCCP,
sRGB, cHRM, pHYs, text and time chunks are ignored: the pixels are taken as
stored. Anything else this reader does not understand (an unknown critical
chunk, an animated PNG, a bad CRC, a truncated stream) raises PngError with
the reason.

    python pngread.py --self-test
    python pngread.py FILE.png        # size and the first pixels
"""
from __future__ import annotations

import base64
import struct
import sys
import zlib
from typing import List, Tuple

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
IGNORED = {b"gAMA", b"iCCP", b"sRGB", b"cHRM", b"pHYs", b"tEXt", b"zTXt", b"iTXt", b"tIME",
           b"bKGD", b"sBIT", b"hIST", b"sPLT", b"eXIf"}
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2),
         (0, 1, 1, 2))   # (x0, y0, dx, dy)


class PngError(ValueError):
    pass


def _chunks(data: bytes) -> List[Tuple[bytes, bytes]]:
    if data[:8] != SIGNATURE:
        raise PngError("not a PNG file (signature)")
    pos, out = 8, []
    while True:
        if pos + 12 > len(data):
            raise PngError("PNG cut off inside a chunk header (no IEND)")
        (ln,) = struct.unpack_from(">I", data, pos)
        tag = bytes(data[pos + 4:pos + 8])
        if pos + 12 + ln > len(data):
            raise PngError(f"PNG chunk {tag!r} runs past the end of the file")
        body = bytes(data[pos + 8:pos + 8 + ln])
        if struct.unpack_from(">I", data, pos + 8 + ln)[0] != zlib.crc32(tag + body) & 0xFFFFFFFF:
            raise PngError(f"PNG chunk {tag!r} fails its CRC")
        out.append((tag, body))
        pos += 12 + ln
        if tag == b"IEND":
            return out


def _unfilter(raw: memoryview, pos: int, rows: int, stride: int, bpp: int) -> Tuple[np.ndarray, int]:
    """Undo the per-row filters of one (sub)image: (rows x stride uint8, new pos)."""
    out = np.zeros((rows, stride), np.uint8)
    prev = np.zeros(stride, np.int32)
    for y in range(rows):
        if pos + 1 + stride > len(raw):
            raise PngError("PNG image data is shorter than the image")
        f = raw[pos]
        line = np.frombuffer(raw[pos + 1:pos + 1 + stride], np.uint8).astype(np.int32)
        pos += 1 + stride
        if f == 0:
            cur = line
        elif f == 1:            # Sub: a running sum per byte lane, mod 256
            cur = line.copy()
            for lane in range(min(bpp, stride)):
                cur[lane::bpp] = np.cumsum(line[lane::bpp]) & 0xFF
        elif f == 2:            # Up
            cur = (line + prev) & 0xFF
        elif f == 3:            # Average (each byte needs the one just decoded)
            c, pv = line.tolist(), prev.tolist()
            for i in range(stride):
                a = c[i - bpp] if i >= bpp else 0
                c[i] = (c[i] + ((a + pv[i]) >> 1)) & 0xFF
            cur = np.array(c, np.int32)
        elif f == 4:            # Paeth
            c, pv = line.tolist(), prev.tolist()
            for i in range(stride):
                a = c[i - bpp] if i >= bpp else 0
                b = pv[i]
                cc = pv[i - bpp] if i >= bpp else 0
                p = a + b - cc
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - cc)
                c[i] = (c[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else cc))) & 0xFF
            cur = np.array(c, np.int32)
        else:
            raise PngError(f"unknown PNG filter type {f}")
        out[y] = cur
        prev = cur
    return out, pos


def _samples(rows: np.ndarray, w: int, depth: int, ch: int) -> np.ndarray:
    """Unfiltered rows -> (h, w, ch) samples (uint16 for 16-bit, uint8 otherwise)."""
    h = rows.shape[0]
    if depth == 16:
        return rows.reshape(h, -1).view(">u2").reshape(h, w, ch).astype(np.uint16)
    if depth == 8:
        return rows.reshape(h, w, ch)
    bits = np.unpackbits(rows, axis=1)                  # MSB first, as PNG packs them
    n = w * ch
    bits = bits[:, :n * depth].reshape(h, n, depth)
    vals = np.zeros((h, n), np.uint8)
    for k in range(depth):
        vals = (vals << 1) | bits[:, :, k]
    return vals.reshape(h, w, ch)


def _to8(v: np.ndarray, depth: int) -> np.ndarray:
    if depth == 16:
        return ((v.astype(np.uint32) * 255 + 32767) // 65535).astype(np.uint8)
    if depth == 8:
        return v.astype(np.uint8)
    return (v.astype(np.uint32) * 255 // ((1 << depth) - 1)).astype(np.uint8)


def read_png(data: bytes) -> Tuple[int, int, bytes]:
    """(width, height, RGBA bytes) of a PNG; PngError when it cannot be read."""
    chunks = _chunks(data)
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise PngError("PNG does not start with an IHDR chunk")
    w, h, depth, ctype, comp, filt, inter = struct.unpack(">IIBBBBB", chunks[0][1])
    if ctype not in CHANNELS or depth not in DEPTHS[ctype]:
        raise PngError(f"PNG colour type {ctype} with bit depth {depth} is not valid")
    if comp or filt or inter not in (0, 1):
        raise PngError("PNG uses an unknown compression, filter or interlace method")
    if not (0 < w <= 1 << 16 and 0 < h <= 1 << 16):
        raise PngError(f"PNG size {w}x{h} is out of range")
    plte = trns = None
    idat = bytearray()
    for tag, body in chunks[1:]:
        if tag == b"IDAT":
            idat += body
        elif tag == b"PLTE":
            plte = body
        elif tag == b"tRNS":
            trns = body
        elif tag in (b"acTL", b"fcTL", b"fdAT"):
            raise PngError("animated PNG (APNG) is not supported; save a still image")
        elif tag == b"IEND" or tag in IGNORED:
            continue
        elif tag[0] & 0x20 == 0:                       # critical chunk we do not know
            raise PngError(f"PNG has an unknown critical chunk {tag!r}")
    if ctype == 3 and plte is None:
        raise PngError("palette PNG without a PLTE chunk")
    try:
        raw = memoryview(zlib.decompress(bytes(idat)))
    except zlib.error as e:
        raise PngError(f"PNG image data does not decompress: {e}")
    ch = CHANNELS[ctype]
    bits_px = ch * depth
    bpp = max(1, bits_px // 8)
    samples = np.zeros((h, w, ch), np.uint16 if depth == 16 else np.uint8)
    pos = 0
    passes = ADAM7 if inter else ((0, 0, 1, 1),)
    for x0, y0, dx, dy in passes:
        pw = (w - x0 + dx - 1) // dx if w > x0 else 0
        ph = (h - y0 + dy - 1) // dy if h > y0 else 0
        if pw == 0 or ph == 0:
            continue
        stride = (pw * bits_px + 7) // 8
        rows, pos = _unfilter(raw, pos, ph, stride, bpp)
        samples[y0::dy, x0::dx] = _samples(rows, pw, depth, ch)
    if pos != len(raw):
        raise PngError("PNG image data is longer than the image")
    out = np.empty((h, w, 4), np.uint8)
    if ctype == 3:
        pal = np.frombuffer(plte, np.uint8)
        if len(pal) % 3 or not 0 < len(pal) // 3 <= 256:
            raise PngError("PNG PLTE chunk has a bad length")
        pal = np.concatenate([pal.reshape(-1, 3), np.full((len(pal) // 3, 1), 255, np.uint8)], 1)
        if trns:
            if len(trns) > len(pal):
                raise PngError("PNG tRNS is longer than its palette")
            pal[:len(trns), 3] = np.frombuffer(trns, np.uint8)
        idx = samples[:, :, 0].astype(np.int64)
        if idx.max(initial=0) >= len(pal):
            raise PngError("PNG pixel indexes past the end of its palette")
        return w, h, pal[idx].tobytes()
    v8 = _to8(samples, depth)
    if ctype in (0, 4):
        out[:, :, 0] = out[:, :, 1] = out[:, :, 2] = v8[:, :, 0]
        out[:, :, 3] = v8[:, :, 1] if ctype == 4 else 255
    else:
        out[:, :, :3] = v8[:, :, :3]
        out[:, :, 3] = v8[:, :, 3] if ctype == 6 else 255
    if trns is not None and ctype in (0, 2):
        if ctype == 0:
            if len(trns) != 2:
                raise PngError("PNG tRNS for gray must be 2 bytes")
            key = struct.unpack(">H", trns)[0]
            out[:, :, 3][samples[:, :, 0] == key] = 0
        else:
            if len(trns) != 6:
                raise PngError("PNG tRNS for RGB must be 6 bytes")
            key = struct.unpack(">3H", trns)
            m = (samples[:, :, 0] == key[0]) & (samples[:, :, 1] == key[1]) & (samples[:, :, 2] == key[2])
            out[:, :, 3][m] = 0
    return w, h, out.tobytes()


# ------------------------------------------------------------------ self-test
# A separate, plain-Python encoder: it packs samples with its own loops and
# filters each row with a scalar reference of the PNG filter maths, so the
# reader is checked against code it does not share.

def _ref_filter(kind: int, line: bytes, prev: bytes, bpp: int) -> bytes:
    out = bytearray(len(line))
    for i in range(len(line)):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if kind == 0:
            pred = 0
        elif kind == 1:
            pred = a
        elif kind == 2:
            pred = b
        elif kind == 3:
            pred = (a + b) // 2
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        out[i] = (line[i] - pred) % 256
    return bytes(out)


def _ref_pack(px: List[List[int]], depth: int) -> bytes:
    """Pack one row of sample lists (one list per pixel) at the given depth."""
    flat = [v for p in px for v in p]
    if depth == 16:
        return b"".join(struct.pack(">H", v) for v in flat)
    if depth == 8:
        return bytes(flat)
    out, acc, n = bytearray(), 0, 0
    for v in flat:
        acc = (acc << depth) | v
        n += depth
        if n == 8:
            out.append(acc)
            acc = n = 0
    if n:
        out.append(acc << (8 - n))
    return bytes(out)


def _ref_png(w, h, ctype, depth, samples, filt, interlace=False, plte=b"", trns=None) -> bytes:
    """samples[y][x] = list of channel values at ``depth``."""
    ch = CHANNELS[ctype]
    bpp = max(1, ch * depth // 8)
    passes = ADAM7 if interlace else ((0, 0, 1, 1),)
    raw = bytearray()
    for x0, y0, dx, dy in passes:
        rows = [[samples[y][x] for x in range(x0, w, dx)] for y in range(y0, h, dy)]
        rows = [r for r in rows if r]
        prev = None
        for k, r in enumerate(rows):
            line = _ref_pack(r, depth)
            prev = prev or bytes(len(line))
            kind = filt if filt >= 0 else (k % 5)
            raw.append(kind)
            raw += _ref_filter(kind, line, prev, bpp)
            prev = line

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    out = SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, int(interlace)))
    out += chunk(b"gAMA", struct.pack(">I", 45455))
    if plte:
        out += chunk(b"PLTE", plte)
    if trns is not None:
        out += chunk(b"tRNS", trns)
    z = zlib.compress(bytes(raw))
    out += chunk(b"IDAT", z[:len(z) // 2]) + chunk(b"IDAT", z[len(z) // 2:])
    return out + chunk(b"IEND", b"")


def _ref_cases():
    """(label, png bytes, expected RGBA) for every colour type / depth /
    filter / interlace combination, built with the reference encoder."""
    w, h = 13, 11            # odd sizes: partial bytes at low depths, short Adam7 passes
    cases = []

    def e8(v, d):           # the expected 8-bit value of a d-bit sample
        return (v * 255 + 32767) // 65535 if d == 16 else (v if d == 8 else v * 255 // ((1 << d) - 1))

    for ctype, depths in DEPTHS.items():
        for depth in depths:
            top = (1 << depth) - 1
            for filt, inter in ((0, False), (1, False), (2, False), (3, False), (4, False),
                                (-1, False), (-1, True)):
                samples, want = [], bytearray()
                plte, trns = b"", None
                if ctype == 3:
                    n = 1 << depth
                    pal = [((i * 53) & 0xFF, (i * 101 + 7) & 0xFF, (255 - i * 3) & 0xFF) for i in range(n)]
                    plte = b"".join(bytes(c) for c in pal)
                    trns = bytes((i * 17) & 0xFF for i in range(min(n, 5)))
                for y in range(h):
                    row = []
                    for x in range(w):
                        s = [((x * 7 + y * 13 + c * 29) * (top // 15 + 1) + (x * y)) & top
                             for c in range(CHANNELS[ctype])]
                        row.append(s)
                        if ctype == 3:
                            r, g, b = pal[s[0]]
                            a = trns[s[0]] if s[0] < len(trns) else 255
                            want += bytes((r, g, b, a))
                        elif ctype in (0, 4):
                            v = e8(s[0], depth)
                            want += bytes((v, v, v, e8(s[1], depth) if ctype == 4 else 255))
                        else:
                            want += bytes([e8(c, depth) for c in s[:3]]
                                          + [e8(s[3], depth) if ctype == 6 else 255])
                    samples.append(row)
                if ctype in (0, 2) and filt == 0:          # colour-key transparency
                    key = samples[3][4]
                    trns = struct.pack(">H", key[0]) if ctype == 0 else struct.pack(">3H", *key)
                    want = bytearray(want)
                    for y in range(h):
                        for x in range(w):
                            if samples[y][x] == key:
                                want[(y * w + x) * 4 + 3] = 0
                label = f"type {ctype} depth {depth} " + ("Adam7" if inter else
                                                          f"filter {filt}" if filt >= 0 else "mixed filters")
                cases.append((label, _ref_png(w, h, ctype, depth, samples, filt, inter, plte, trns),
                              bytes(want)))
    return cases


# A 3x2 RGBA PNG (179 bytes) re-encoded by macOS ``sips -s format png`` (ImageIO)
# from a kit-written PNG, 2026-11-20 (it adds sRGB and eXIf chunks); pixels: red, green, blue / white, black, half-transparent grey.
SIPS_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAYAAACddGYaAAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAA"
    "A6ABAAMAAAABAAEAAKACAAQAAAABAAAAA6ADAAQAAAABAAAAAgAAAABqvnfpAAAAHUlEQVQIHWP4z8DwHwwZ/v9nAmIGBkYGhoaGxkYAi7cJ"
    "/ovpXQgAAAAASUVORK5CYII=")
SIPS_PIXELS = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255),
               (255, 255, 255, 255), (0, 0, 0, 255), (128, 128, 128, 128)]


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    cases = _ref_cases()
    bad = []
    for label, png, want in cases:
        try:
            got = read_png(png)
        except PngError as e:
            bad.append(f"{label}: {e}")
            continue
        if got[2] != want:
            bad.append(label)
    check(not bad, f"{len(cases)} PNGs from an independent encoder (types 0/2/3/4/6, depths 1-16, "
                   f"five filters, Adam7, PLTE+tRNS, colour keys) read back exactly"
          + (f"; failed: {', '.join(bad[:6])}" if bad else ""))
    try:
        w, h, rgba = read_png(base64.b64decode(SIPS_PNG))
        px = [tuple(rgba[i:i + 4]) for i in range(0, len(rgba), 4)]
        check((w, h) == (3, 2) and px == SIPS_PIXELS, "a PNG written by macOS sips decodes to its stated pixels")
    except PngError as e:
        check(False, f"a PNG written by macOS sips decodes to its stated pixels ({e})")
    png = bytearray(cases[0][1])
    png[40] ^= 0x01                     # a byte inside the first chunk after IHDR
    try:
        read_png(bytes(png))
        check(False, "a chunk with a wrong CRC is refused")
    except PngError as e:
        check("CRC" in str(e), "a chunk with a wrong CRC is refused")
    try:
        read_png(b"GIF89a" + bytes(40))
        check(False, "a file that is not a PNG is refused")
    except PngError:
        check(True, "a file that is not a PNG is refused")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) != 1:
        print(__doc__)
        return 2
    try:
        w, h, rgba = read_png(open(argv[0], "rb").read())
    except PngError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(f"{w}x{h}, first pixels {[tuple(rgba[i:i + 4]) for i in range(0, min(len(rgba), 16), 4)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
