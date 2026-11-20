#!/usr/bin/env python3
"""Episode II motion: `.xap` body clips and `.fap` face clips, decoded and
turned into glTF animations for the `.xhr` skeletons.

    python xap.py --self-test
    python xap.py FILE.xap                         # clip table
    python xap.py FILE.xap --xhr FILE.xhr --slot N --frame F
                                                   # per-transform pose
    python xap.py --sweep DUMPDIR                  # every file on the disc

Everything below was read from SLUS_208.92 (Ghidra output and, where the
two disagreed, the disassembly) and then checked by walking every clip on
the disc (`--sweep`).  Routines are named by address only.

Container (`FUN_00292030` takes the clips)::

    +0x00 "XAP " (body) / "FAP " (face)   +0x04 f32 1.2   +0x08 u32 n
    +0x0C n x u32 clip offsets, ascending, 4-aligned, first = 12 + 4n

A clip runs from its offset to the next distinct offset (or the end of the
file); several slots can share one clip.  A clip whose start frame is
greater than its end frame is not taken (`FUN_00292030` tests start <= end):
on the disc these are 16-byte stubs "XAC ", 1.4, 100.0, -100.0.

Clip header ("XAC " body / "FAC " face): +4 f32 version, +8 f32 start frame,
+0xC f32 end frame.  The clock runs time += speed * global tick and wraps to
start once time >= end (`FUN_002924a0`).  `FUN_002929e0` dispatches on the
version: below 1.4 to `FUN_0028dec0` (body) / `FUN_002900f8` (face), 1.4 to
`FUN_0028f1e0` / `FUN_00290518`.  The version tests compare single floats.

    version 1.4 (0x50 bytes):  count / offset (offsets relative to the clip)
        +0x10 / +0x30  run A  (the first two blocks are the two roots,
                               read by FUN_00293e38 without a count check)
        +0x14 / +0x34  run B
        +0x18 / +0x38  run D  (face; only FAC clips drive it)
        +0x1C / +0x3C  run E  (chains; 8-byte block header)
        +0x20 / +0x40  visibility list (body only, FUN_00290ec0; junk in FAC)
        +0x24 / +0x44  run C
        +0x28..+0x2F, +0x48..+0x4F: exporter junk (fragments of Maya strings)
    version 1.3 / 1.2 (0x30 bytes, FUN_0028dec0): counts +0x10 A, +0x14 B,
        +0x18 D, +0x1C E; offsets +0x20 A, +0x24 B, +0x28 D, +0x2C E.
        Run C stays at rest.  The run-E block header is 8 bytes from 1.3 on
        (FUN_00294338) and 4 bytes below (FUN_002945c8); no clip on the disc
        has run-E blocks below 1.3, so the 4-byte case is covered only by
        the self-test.

Block i of a run drives transform (run base + i) for i < min(clip count,
xhr count); extra blocks are ignored and missing ones leave the transform at
its `.xhr` rest values (each run pointer is re-read from its own offset
field in FUN_0028f1e0).  Transform order is A, B, C, D, E, F as in xhr.py.
Run F is never animated.

Block: u16 mask, u16 flags, (run E from 1.3: one f32 the engine copies to
record +0x2C; meaning unknown, kept in extras), then one key list per set
mask bit in bit order: bits 0-2 T.x T.y T.z, 3-5 R.x R.y R.z (radians), 6-8
S.x S.y S.z.  Keys replace the rest value.  Joint orients are never keyed.
Flags bit 0 sets the record's parent-scale compensation bit
(FUN_002939e8: record +0x44 |= 2); the other flag bits are not read.

Local matrix (row vectors, as xhr.py; built by FUN_0028f1e0)::

    L = S * Rx(R.x) Ry(R.y) Rz(R.z) * Rx(JO.x) Ry(JO.y) Rz(JO.z) * invPS * T

with no JO for the roots, and invPS = diag(1/ps) when the compensation bit
is set, ps being the parent's current scale (the slot FUN_0028dd50 finds,
written each frame; FUN_00293330 skips a zero axis, leaving a stale value
there: this module uses 1.0 and counts it).  A parent in run F has no slot
(the engine asserts "can not found parent scale" and skips the scale).
World = L * world[parent] (FUN_00290ab8).

Face (FUN_00290518, run D only, its own clock): the same order with JO at
+0x18, except that the translation is applied only when the rest T is not
zero and the scale only when the rest S is not (1,1,1) (then the animated
values are used).  The rotation limits are clamped after the matrix is
built, so they change nothing.

Keys (FUN_002935e8, value FUN_00293560, out-angle FUN_00293420, in-angle
FUN_002934c0): u16 header (low 12 bits type, 0x4000 last key, 0x8000 see
below), s16 frame, then
    type 0  8 bytes: f32 value (angles 0)
    type 1  8 bytes: f32 value (segment always linear)
    type 2 12 bytes: f32 angle (in = out), f32 value
    type 3 12 bytes: u16 in-angle, u16 out-angle (x 1e-4), f32 value
    type 4  asserts "XacKeyType_Hermite"; refused
    type 5 16 bytes: f32 in-angle, f32 out-angle, f32 value
Evaluation: t = max(t, first frame); walk the keys; the first k with
frame_k <= t < frame_k+1 picks the segment, a 0x4000 key reached first
returns its own value.  The segment is linear (FUN_00295610) for a type-1
key or a 0x8000 key followed by a key whose whole header is 0x0001, else
Hermite (FUN_00295488 -> FUN_00295260 -> FUN_002955c0) with slopes
sin/cos of key k's out-angle and key k+1's in-angle (5729578.0 when the
cosine is 0) over x = frame / 30.  Keys are 30 per second.

Visibility list (v1.4 body, FUN_00290ec0 with FUN_00295188 per entry):
per entry i32 n, then n pairs (f32 frame, f32 value).  The disassembly of
FUN_00295188 gives: value(t) = the value of the first pair whose frame is
>= t, the walk's "previous frame" starting at 0.0 and the first pair always
taken when nothing later matches; past the last frame, the last value.  Bit
i of the output (pointer named pVisible) is set when value == 0.0.  Which
mesh part an index means is not traced: this module stores the list and the
per-frame bits in the animation extras and hides nothing.

Not reproduced (see docs/MOTION.md): the chain simulation that runs after
the pose (FUN_00297da0), the visibility bits, the run-time factor
act+0xB0..B8 on transform 1's translation (1.0 unless a script sets it;
unverified), and the real-time rate (curves are 30 per second; the game's
speed x tick is unverified).  The calculation is float64; the engine's is
float32.
"""
import json
import math
import os
import struct
import sys
from collections import Counter

import numpy as np

import xhr

__all__ = ["XapError", "Curve", "Block", "Clip", "Xap", "parse",
           "eval_curve_ref", "eval_curve", "pose", "animation_tracks",
           "add_to_glb", "sweep", "self_test"]

KEY_SIZE = {0: 8, 1: 8, 2: 12, 3: 12, 5: 16}
FPS = 30.0
STEEP = 5729578.0                     # FUN_00295260, cosine 0
F32 = lambda v: float(np.float32(v))
V14, V13 = F32(1.4), F32(1.3)
RUN_ORDER = "ABCDEF"


class XapError(ValueError):
    pass


class Curve(object):
    """One channel's keys, as stored.  Arrays: headers (raw u16), types,
    frames (int), ain / aout (radians), values (float64)."""
    __slots__ = ("offset", "headers", "types", "frames", "ain", "aout",
                 "values")

    def __init__(self, offset, headers, frames, ain, aout, values):
        self.offset = offset
        self.headers = np.asarray(headers, dtype=np.int64)
        self.types = self.headers & 0xFFF
        self.frames = np.asarray(frames, dtype=np.int64)
        self.ain = np.asarray(ain, dtype=np.float64)
        self.aout = np.asarray(aout, dtype=np.float64)
        self.values = np.asarray(values, dtype=np.float64)

    def __len__(self):
        return len(self.headers)


class Block(object):
    __slots__ = ("offset", "mask", "flags", "extra", "curves")

    def __init__(self, offset, mask, flags, extra, curves):
        self.offset, self.mask, self.flags = offset, mask, flags
        self.extra, self.curves = extra, curves      # curves: {bit: Curve}


class Clip(object):
    def __init__(self, magic, version, start, end, offset, size):
        self.magic, self.version = magic, version
        self.start, self.end = start, end
        self.offset, self.size = offset, size
        self.counts = {}
        self.runs = {k: [] for k in "ABCDE"}
        self.visibility = []
        self.padding = 0

    @property
    def face(self):
        return self.magic == b"FAC "

    @property
    def e_extra(self):
        return [b.extra for b in self.runs["E"] if b.extra is not None]

    def frames(self):
        return np.arange(int(self.start), int(self.end) + 1)


class Xap(object):
    def __init__(self, magic, version, slots, offsets):
        self.magic, self.version = magic, version
        self.slots, self.offsets = slots, offsets

    def clips(self):
        """[(clip, [slots sharing it])] for the non-stub clips."""
        seen = {}
        for i, c in enumerate(self.slots):
            if c is not None:
                seen.setdefault(id(c), (c, []))[1].append(i)
        return sorted(seen.values(), key=lambda v: v[1][0])


# ------------------------------------------------------------------ parse

def _curve(buf, p, end):
    start = p
    hs, fr, ai, ao, vs = [], [], [], [], []
    while True:
        if p + 4 > end:
            raise XapError("curve at %#x runs past the clip" % start)
        h, f = struct.unpack_from("<Hh", buf, p)
        ty = h & 0xFFF
        if ty not in KEY_SIZE:
            raise XapError("key type %d at %#x (the engine knows 0,1,2,3,5)"
                           % (ty, p))
        n = KEY_SIZE[ty]
        if p + n > end:
            raise XapError("curve at %#x runs past the clip" % start)
        if ty in (0, 1):
            v, = struct.unpack_from("<f", buf, p + 4)
            a_in = a_out = 0.0
        elif ty == 2:
            a, v = struct.unpack_from("<2f", buf, p + 4)
            a_in = a_out = a
        elif ty == 3:
            i_, o_ = struct.unpack_from("<2H", buf, p + 4)
            v, = struct.unpack_from("<f", buf, p + 8)
            a_in, a_out = i_ * F32(1e-4), o_ * F32(1e-4)
        else:
            a_in, a_out, v = struct.unpack_from("<3f", buf, p + 4)
        hs.append(h)
        fr.append(f)
        ai.append(a_in)
        ao.append(a_out)
        vs.append(v)
        p += n
        if h & 0x4000:
            return p, Curve(start, hs, fr, ai, ao, vs)


def _block(buf, p, end, extra_f32):
    if p + (8 if extra_f32 else 4) > end:
        raise XapError("block at %#x runs past the clip" % p)
    start = p
    mask, flags = struct.unpack_from("<HH", buf, p)
    if mask & ~0x1FF:
        raise XapError("block at %#x: mask %#x has bits above 8" % (p, mask))
    p += 4
    extra = None
    if extra_f32:
        extra, = struct.unpack_from("<f", buf, p)
        p += 4
    curves = {}
    for bit in range(9):
        if mask >> bit & 1:
            p, curves[bit] = _curve(buf, p, end)
    return p, Block(start, mask, flags, extra, curves)


def _clip(buf, o, end, container):
    magic = bytes(buf[o:o + 4])
    want = b"FAC " if container == b"FAP " else b"XAC "
    if end - o < 16:
        raise XapError("clip at %#x shorter than its header" % o)
    if magic != want:
        raise XapError("clip at %#x: magic %r in a %r file"
                       % (o, magic, container))
    version, start, stop = struct.unpack_from("<3f", buf, o + 4)
    if not start <= stop:
        if end - o != 16:
            raise XapError("clip at %#x is not taken (start > end) but has "
                           "%d bytes" % (o, end - o))
        return None
    c = Clip(magic, version, start, stop, o, end - o)
    if F32(version) >= V14:
        hsize = 0x50
        lay = [("A", 0x10, 0x30), ("B", 0x14, 0x34), ("D", 0x18, 0x38),
               ("E", 0x1C, 0x3C), ("V", 0x20, 0x40), ("C", 0x24, 0x44)]
    else:
        hsize = 0x30
        lay = [("A", 0x10, 0x20), ("B", 0x14, 0x24), ("D", 0x18, 0x28),
               ("E", 0x1C, 0x2C)]
    if end - o < hsize:
        raise XapError("clip at %#x shorter than its %#x-byte header"
                       % (o, hsize))
    if c.face:          # the face path reads run D only
        lay = [r for r in lay if r[0] == "D"]
    spans = []
    for name, co, oo in lay:
        n, = struct.unpack_from("<I", buf, o + co)
        rel, = struct.unpack_from("<I", buf, o + oo)
        c.counts[name] = n
        if not n:
            continue
        if n > 100000 or rel < hsize or o + rel > end:
            raise XapError("clip at %#x: run %s count %d / offset %#x out "
                           "of range" % (o, name, n, rel))
        p = o + rel
        if name == "V":
            for _ in range(n):
                if p + 4 > end:
                    raise XapError("visibility list runs past the clip")
                k, = struct.unpack_from("<i", buf, p)
                if not 0 <= k < 100000 or p + 4 + 8 * k > end:
                    raise XapError("visibility entry at %#x: %d pairs" % (p, k))
                pairs = struct.unpack_from("<%df" % (2 * k), buf, p + 4)
                c.visibility.append([(pairs[2 * i], pairs[2 * i + 1])
                                     for i in range(k)])
                p += 4 + 8 * k
        else:
            extra = name == "E" and F32(version) >= V13
            for _ in range(n):
                p, b = _block(buf, p, end, extra)
                c.runs[name].append(b)
        spans.append((rel, p - o, name))
    if not c.face and c.counts.get("A", 0) < 2:
        raise XapError("clip at %#x: run A has %d blocks, the engine reads "
                       "two roots unconditionally" % (o, c.counts.get("A", 0)))
    spans.sort()
    at = hsize
    for s, e, name in spans:
        if s != at:
            raise XapError("clip at %#x: run %s starts at %#x, expected %#x"
                           % (o, name, s, at))
        at = e
    size = end - o
    if at != size:
        if not (end % 4 == 0 and 0 < size - at <= 3):
            raise XapError("clip at %#x: the blocks end at %#x, the clip at "
                           "%#x" % (o, at, size))
        c.padding = size - at
    return c


def parse(buf):
    buf = bytes(buf)
    if len(buf) < 12 or buf[:4] not in (b"XAP ", b"FAP "):
        raise XapError("not a .xap / .fap (magic)")
    magic = buf[:4]
    version, n = struct.unpack_from("<fI", buf, 4)
    if abs(version - 1.2) > 1e-6:
        raise XapError("container version %g (only 1.2 is known)" % version)
    if 12 + 4 * n > len(buf):
        raise XapError("offset table runs past the file")
    offs = list(struct.unpack_from("<%dI" % n, buf, 12))
    if n and offs[0] != 12 + 4 * n:
        raise XapError("first clip at %#x, expected %#x" % (offs[0],
                                                            12 + 4 * n))
    for a, b in zip(offs, offs[1:]):
        if b < a:
            raise XapError("clip offsets out of order")
    for a in offs:
        if a % 4 or a >= len(buf):
            raise XapError("clip offset %#x misaligned or out of range" % a)
    ends = sorted(set(offs)) + [len(buf)]
    cache = {}
    slots = []
    for a in offs:
        if a not in cache:
            cache[a] = _clip(buf, a, ends[ends.index(a) + 1], magic)
        slots.append(cache[a])
    return Xap(magic, version, slots, offs)


# ------------------------------------------------------------- evaluation

def _linear(f0, f1, y0, y1, t):
    d = float(f1 - f0)
    if d == 0.0:
        d = 1.0
    d = d / FPS
    return y0 + (y1 - y0) * (((t - f0) / FPS) / d)


def _hermite(f0, f1, y0, y1, ao, ai, t):
    # FUN_00295488: control points (x0, x0 + cos ao, x3 - cos ai, x3) and
    # (y0, y0 + sin ao, y3 - sin ai, y3); FUN_00295260 the cubic
    x0, x3 = f0 * (1.0 / FPS), f1 * (1.0 / FPS)
    x1, y1c = x0 + math.cos(ao), y0 + math.sin(ao)
    x2, y2c = x3 - math.cos(ai), y1 - math.sin(ai)
    dx01 = x1 - x0
    s0 = (y1c - y0) / dx01 if dx01 != 0 else STEEP
    dx23 = x3 - x2
    s1 = (y1 - y2c) / dx23 if dx23 != 0 else STEEP
    L, dy = x3 - x0, y1 - y0
    inv = 1.0 / (L * L)
    b = (((dy + dy + dy) - L * s0) - L * s0 - L * s1) * inv
    a = (((L * s0 + L * s1) - dy) - dy) * inv / L
    u = (t - f0) * (1.0 / FPS)
    return u * (u * (u * a + b) + s0) + y0


def eval_curve_ref(c, t):
    """Scalar evaluator, a literal transcription of FUN_002935e8."""
    hs, fr, vs = c.headers, c.frames, c.values
    tt = float(fr[0])
    if tt <= t:
        tt = t
    found = False
    res = 0.0
    for k in range(len(hs)):
        h = int(hs[k])
        if not found:
            if h & 0x4000:
                res, found = float(vs[k]), True
            elif fr[k] <= tt < fr[k + 1]:
                found = True
                if (h & 0xFFF) == 1 or ((h & 0x8000) and int(hs[k + 1]) == 1):
                    res = _linear(int(fr[k]), int(fr[k + 1]), float(vs[k]),
                                  float(vs[k + 1]), tt)
                else:
                    res = _hermite(int(fr[k]), int(fr[k + 1]), float(vs[k]),
                                   float(vs[k + 1]), float(c.aout[k]),
                                   float(c.ain[k + 1]), tt)
        if h & 0x4000:
            return res
    raise XapError("curve without a last key")


def _segments(c, tt):
    """Index of the segment each time falls in (-1 = hold the last key)."""
    fr = c.frames
    n = len(fr)
    if n == 1:
        return np.full(len(tt), -1)
    if np.all(np.diff(fr) > 0):
        k = np.searchsorted(fr, tt, side="right") - 1
        k[(k < 0) | (k >= n - 1)] = -1
        return k
    # non-ascending frames: the first k whose [f_k, f_k+1) holds t
    m = (fr[None, :-1] <= tt[:, None]) & (tt[:, None] < fr[None, 1:])
    k = np.argmax(m, axis=1)
    k[~m.any(axis=1)] = -1
    return k


def eval_curve(c, frames):
    """Vectorised FUN_002935e8 over an array of times (frames)."""
    t = np.asarray(frames, dtype=np.float64).reshape(-1)
    tt = np.maximum(t, float(c.frames[0]))
    k = _segments(c, tt)
    out = np.full(len(tt), c.values[-1])
    sel = k >= 0
    if not sel.any():
        return out
    kk = k[sel]
    tk = tt[sel]
    h0 = c.headers[kk]
    h1 = c.headers[kk + 1]
    f0 = c.frames[kk].astype(np.float64)
    f1 = c.frames[kk + 1].astype(np.float64)
    y0, y1 = c.values[kk], c.values[kk + 1]
    lin = ((h0 & 0xFFF) == 1) | (((h0 & 0x8000) != 0) & (h1 == 1))
    res = np.empty(len(kk))
    # linear, FUN_00295610
    d = f1 - f0
    d = np.where(d == 0.0, 1.0, d) / FPS
    res_lin = y0 + (y1 - y0) * (((tk - f0) / FPS) / d)
    # Hermite, same arithmetic as _hermite
    ao, ai = c.aout[kk], c.ain[kk + 1]
    x0, x3 = f0 * (1.0 / FPS), f1 * (1.0 / FPS)
    x1, y1c = x0 + np.cos(ao), y0 + np.sin(ao)
    x2, y2c = x3 - np.cos(ai), y1 - np.sin(ai)
    with np.errstate(divide="ignore", invalid="ignore"):
        dx01 = x1 - x0
        s0 = np.where(dx01 != 0, (y1c - y0) / np.where(dx01 != 0, dx01, 1),
                      STEEP)
        dx23 = x3 - x2
        s1 = np.where(dx23 != 0, (y1 - y2c) / np.where(dx23 != 0, dx23, 1),
                      STEEP)
        L, dy = x3 - x0, y1 - y0
        inv = 1.0 / (L * L)
        b = (((dy + dy + dy) - L * s0) - L * s0 - L * s1) * inv
        a = (((L * s0 + L * s1) - dy) - dy) * inv / L
        u = (tk - f0) * (1.0 / FPS)
        res_h = u * (u * (u * a + b) + s0) + y0
    res = np.where(lin, res_lin, res_h)
    out[sel] = res
    return out


def visibility_value(pairs, t):
    """FUN_00295188 (from its disassembly): the value an entry gives at
    time t, or None when the entry has no pairs (the bit is left as it
    was)."""
    res = None
    prev = 0.0
    for i, (f, v) in enumerate(pairs):
        if prev < t and t <= f:
            res = v
        elif i == 0:
            res = v
        prev = f
    if pairs and prev < t:
        res = pairs[-1][1]
    return res


def visibility_bits(clip, frames):
    """Per frame, the FUN_00290ec0 output word(s) as a Python int: bit i
    set when entry i's value is 0.0 (None-valued entries left clear)."""
    out = []
    for t in frames:
        w = 0
        for i, e in enumerate(clip.visibility):
            v = visibility_value(e, float(t))
            if v is not None and v == 0.0:
                w |= 1 << i
        out.append(w)
    return out


# ------------------------------------------------------------------- pose

def _erows(a):
    """(..., 3) Euler angles -> (..., 3, 3) row-vector Rx Ry Rz (as
    xhr.euler_rows)."""
    a = np.asarray(a, dtype=np.float64)
    cx, sx = np.cos(a[..., 0]), np.sin(a[..., 0])
    cy, sy = np.cos(a[..., 1]), np.sin(a[..., 1])
    cz, sz = np.cos(a[..., 2]), np.sin(a[..., 2])
    z, o = np.zeros_like(cx), np.ones_like(cx)
    rx = np.stack([o, z, z, z, cx, sx, z, -sx, cx], -1).reshape(a.shape[:-1]
                                                              + (3, 3))
    ry = np.stack([cy, z, -sy, z, o, z, sy, z, cy], -1).reshape(a.shape[:-1]
                                                              + (3, 3))
    rz = np.stack([cz, sz, z, -sz, cz, z, z, z, o], -1).reshape(a.shape[:-1]
                                                              + (3, 3))
    return rx @ ry @ rz


def _run_bases(x):
    base, at = {}, 0
    for k in RUN_ORDER:
        base[k] = at
        at += x.counts[k]
    return base


def _order(x):
    """Transform indices with every parent before its children."""
    done, out = set(), []

    def visit(i, depth):
        if i in done:
            return
        if depth > len(x.joints):
            raise XapError("parent cycle at transform %d" % i)
        p = x.joints[i].parent
        if p >= 0:
            visit(p, depth + 1)
        done.add(i)
        out.append(i)
    for j in x.joints:
        visit(j.index, 0)
    return out


def _channels(x, clip, frames):
    """Per transform and frame, the engine's inputs: T, R, S (applied), the
    scale slot value, JO, compensation flag, and which transforms a block
    drives."""
    frames = np.asarray(frames, dtype=np.float64).reshape(-1)
    n, nf = len(x.joints), len(frames)
    T = np.empty((n, nf, 3))
    R = np.empty((n, nf, 3))
    S = np.empty((n, nf, 3))
    JO = np.zeros((n, 3))
    for j in x.joints:
        T[j.index], R[j.index], S[j.index] = j.t, j.r, j.s
        JO[j.index] = j.jo
    slot = S.copy()
    comp = np.zeros(n, dtype=bool)
    driven = np.zeros(n, dtype=bool)
    base = _run_bases(x)
    face = clip.face
    if face:
        runs = ["D"]
    elif F32(clip.version) >= V14:
        runs = ["A", "B", "C", "E"]
    else:
        runs = ["A", "B", "E"]
    for r in runs:
        for i, b in enumerate(clip.runs[r][:x.counts[r]]):
            ti = base[r] + i
            driven[ti] = True
            comp[ti] = bool(b.flags & 1)
            for bit, cv in b.curves.items():
                v = eval_curve(cv, frames)
                (T, R, S)[bit // 3][ti, :, bit % 3] = v
    slot = S.copy()
    if face:
        for i in range(x.counts["D"]):
            ti = base["D"] + i
            j = x.joints[ti]
            if not np.any(j.t != 0):
                T[ti] = 0.0
            if np.all(j.s == 1.0):
                S[ti] = 1.0
    return dict(T=T, R=R, S=S, slot=slot, JO=JO, comp=comp, driven=driven,
                frames=frames)


def _compose(x, ch, stats=None):
    """Row-vector locals and worlds (nf, n, 4, 4) and the column-form
    3x3 of each local, plus per-transform compensation bookkeeping."""
    T, R, S, slot, JO = ch["T"], ch["R"], ch["S"], ch["slot"], ch["JO"]
    n, nf = T.shape[:2]
    rot = _erows(R) @ _erows(JO)[:, None]                   # (n, nf, 3, 3)
    ps_inv = np.ones((n, nf, 3))
    kinds = [j.kind for j in x.joints]
    info = [None] * n
    for i in range(n):
        if not ch["comp"][i]:
            continue
        p = x.joints[i].parent
        if p < 0:
            continue
        if kinds[p] == "F":
            info[i] = "parent in run F"
            if stats is not None:
                stats["comp_parent_F"] += 1
            continue
        ps = slot[p]
        zero = ps == 0.0
        inv = np.where(zero, 1.0, 1.0 / np.where(zero, 1.0, ps))
        ps_inv[i] = inv
        if stats is not None:
            nonunit = np.any(ps != 1.0, axis=1)
            if nonunit.any():
                stats["comp_nonunit"] += 1
                uni = (ps[:, 0] == ps[:, 1]) & (ps[:, 1] == ps[:, 2])
                if np.all(uni | ~nonunit):
                    stats["comp_uniform"] += 1
                else:
                    stats["comp_nonuniform"] += 1
                if zero.any():
                    stats["comp_zero_axis"] += 1
                if kinds[p] == "D":
                    stats["comp_parent_D"] += 1
                if x.joints[p].index > i:
                    stats["comp_parent_later"] += 1
    m3 = S[..., :, None] * rot * ps_inv[..., None, :]     # S . R . invPS
    loc = np.zeros((nf, n, 4, 4))
    loc[..., :3, :3] = m3.transpose(1, 0, 2, 3)
    loc[..., 3, :3] = T.transpose(1, 0, 2)
    loc[..., 3, 3] = 1.0
    world = np.zeros_like(loc)
    for i in _order(x):
        p = x.joints[i].parent
        world[:, i] = loc[:, i] if p < 0 else loc[:, i] @ world[:, p]
    return loc, world, rot, ps_inv, info


def pose(x, clip, t, face=None):
    """(locals (n,4,4), worlds (n,4,4), trs list) at time t (frames), row
    vectors as xhr.py.  `face` defaults to the clip's own kind."""
    if face is not None and bool(face) != clip.face:
        raise XapError("face=%r for a %r clip" % (face, clip.magic))
    ch = _channels(x, clip, [t])
    loc, world, _, ps_inv, _ = _compose(x, ch)
    trs = [dict(t=ch["T"][i, 0].copy(), r=ch["R"][i, 0].copy(),
                s=ch["S"][i, 0].copy(), jo=ch["JO"][i].copy(),
                comp=bool(ch["comp"][i]), inv_ps=ps_inv[i, 0].copy(),
                driven=bool(ch["driven"][i]))
           for i in range(len(x.joints))]
    return loc[0], world[0], trs


def _quats(m):
    """Vectorised gltf.quat_from_matrix: (..., 3, 3) column-form rotation
    -> (..., 4) xyzw, same branches."""
    m = np.asarray(m, dtype=np.float64)
    sh = m.shape[:-2]
    m = m.reshape(-1, 3, 3)
    q = np.empty((len(m), 4))
    tr = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    c0 = tr > 0
    c1 = ~c0 & (m[:, 0, 0] > m[:, 1, 1]) & (m[:, 0, 0] > m[:, 2, 2])
    c2 = ~c0 & ~c1 & (m[:, 1, 1] > m[:, 2, 2])
    c3 = ~c0 & ~c1 & ~c2
    with np.errstate(invalid="ignore", divide="ignore"):
        s = np.sqrt(np.maximum(tr + 1.0, 0)) * 2
        q0 = np.stack([(m[:, 2, 1] - m[:, 1, 2]) / s, (m[:, 0, 2] - m[:, 2, 0])
                       / s, (m[:, 1, 0] - m[:, 0, 1]) / s, 0.25 * s], -1)
        s = np.sqrt(np.maximum(1.0 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2], 0)) * 2
        q1 = np.stack([0.25 * s, (m[:, 0, 1] + m[:, 1, 0]) / s, (m[:, 0, 2]
                       + m[:, 2, 0]) / s, (m[:, 2, 1] - m[:, 1, 2]) / s], -1)
        s = np.sqrt(np.maximum(1.0 + m[:, 1, 1] - m[:, 0, 0] - m[:, 2, 2], 0)) * 2
        q2 = np.stack([(m[:, 0, 1] + m[:, 1, 0]) / s, 0.25 * s, (m[:, 1, 2]
                       + m[:, 2, 1]) / s, (m[:, 0, 2] - m[:, 2, 0]) / s], -1)
        s = np.sqrt(np.maximum(1.0 + m[:, 2, 2] - m[:, 0, 0] - m[:, 1, 1], 0)) * 2
        q3 = np.stack([(m[:, 0, 2] + m[:, 2, 0]) / s, (m[:, 1, 2] + m[:, 2, 1])
                       / s, 0.25 * s, (m[:, 1, 0] - m[:, 0, 1]) / s], -1)
    for c, v in ((c0, q0), (c1, q1), (c2, q2), (c3, q3)):
        q[c] = v[c]
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    return q.reshape(sh + (4,))


def animation_tracks(x, clip, face=None, stats=None):
    """One entry per transform: times (s), translation (k,3), rotation
    xyzw (k,4), scale (k,3), shear residual, driven flag.  Frames are the
    integers int(start)..int(end); time = (f - int(start)) / 30.

    glTF TRS is written without decomposing the matrix: the column-form
    local is T . (Rrows(R) Rrows(JO))^T . diag(S), so translation = T,
    rotation = that orthonormal matrix, scale = S (exact for zero or
    negative S).  Under compensation with a uniform parent scale k the
    scale is S / k; with a non-uniform one the 3x3 diag(1/ps) Q diag(S) is
    split by a polar decomposition and the shear left over is reported."""
    if face is not None and bool(face) != clip.face:
        raise XapError("face=%r for a %r clip" % (face, clip.magic))
    frames = clip.frames()
    ch = _channels(x, clip, frames)
    loc, world, rot, ps_inv, info = _compose(x, ch, stats)
    times = (frames - int(clip.start)) / FPS
    out = []
    for i in range(len(x.joints)):
        q_col = rot[i].transpose(0, 2, 1)                   # (nf, 3, 3)
        T = ch["T"][i].copy()
        S = ch["S"][i].copy()
        resid = 0.0
        inv = ps_inv[i]
        if np.any(inv != 1.0):
            uni = (inv[:, 0] == inv[:, 1]) & (inv[:, 1] == inv[:, 2])
            S = np.where(uni[:, None], S * inv[:, :1], S)
            nu = ~uni
            if nu.any():
                M = inv[nu][:, :, None] * q_col[nu] * ch["S"][i][nu][:, None, :]
                U, _, Vt = np.linalg.svd(M)
                Rp = U @ Vt
                neg = np.linalg.det(Rp) < 0
                if neg.any():
                    U[neg, :, 2] *= -1
                    Rp = U @ Vt
                s = np.einsum("nji,nji->ni", Rp, M)          # diag(R^T M)
                q_col = q_col.copy()
                q_col[nu] = Rp
                S[nu] = s
                resid = float(np.abs(M - Rp * s[:, None, :]).max())
        q = _quats(q_col)
        for k in range(1, len(q)):
            if np.dot(q[k], q[k - 1]) < 0:
                q[k] = -q[k]
        out.append(dict(times=times, translation=T, rotation=q, scale=S,
                        shear=resid, driven=bool(ch["driven"][i]),
                        world=world[:, i]))
    return out


# -------------------------------------------------------------------- glb

def _const(v):
    return len(v) > 1 and float(np.abs(v - v[0]).max()) < 1e-7


def add_to_glb(glb_bytes, x, sources):
    """Append one glTF animation per selected non-stub clip.

    sources: [(label, Xap, slots or None, face)].  label is the disc path
    without chain and extension, with forward slashes, e.g.
    "motion/shion00"; slots None = every slot.  Returns (glb bytes,
    [summary dict per animation])."""
    import gltf
    js, _ = gltf.read_glb(glb_bytes)
    nodes = js.get("nodes", [])
    sk = [i for i, nd in enumerate(nodes) if nd.get("name") == "skeleton"]
    if len(sk) != 1:
        raise XapError("the .glb has no skeleton node (mesh written without "
                       "the .xhr)")
    under = {}
    stack = list(nodes[sk[0]].get("children", ()))
    while stack:
        c = stack.pop()
        under[nodes[c].get("name")] = c
        stack.extend(nodes[c].get("children", ()))
    try:
        node_of = [under["t%02d" % j.index] for j in x.joints]
    except KeyError as e:
        raise XapError("the .glb skeleton has no node %s" % e)
    base = _run_bases(x)
    anims, summary = [], []
    for label, xp, slots, face in sources:
        want = None if slots is None else set(slots)
        for clip, sl in xp.clips():
            if want is not None:
                sl = [s for s in sl if s in want]
                if not sl:
                    continue
            if bool(face) != clip.face:
                raise XapError("%s: %s clip given as %s" % (
                    label, clip.magic.decode().strip(),
                    "face" if face else "body"))
            stats = Counter()
            tracks = animation_tracks(x, clip, stats=stats)
            if clip.face:
                keyed = range(base["D"], base["D"] + x.counts["D"])
            else:
                keyed = range(len(x.joints))
            dur = (int(clip.end) - int(clip.start)) / FPS
            channels = []
            for i in keyed:
                tr = tracks[i]
                for path, vals in (("translation", tr["translation"]),
                                   ("rotation", tr["rotation"]),
                                   ("scale", tr["scale"])):
                    if len(vals) == 1 or _const(vals):
                        tm, vv = tr["times"][:1], vals[:1]
                    else:
                        tm, vv = tr["times"], vals
                    channels.append([node_of[i], path, tm, vv, "LINEAR"])
            if dur > 0 and max(float(c[2][-1]) for c in channels) < dur:
                # a clip that never moves: give one channel both ends so
                # the action keeps the clip's length
                c = channels[0]
                c[2] = np.array([0.0, dur])
                c[3] = np.repeat(c[3][:1], 2, axis=0)
            name = "%s#%s" % (label, ",".join("%02d" % s for s in sl))
            shear = max(t["shear"] for t in tracks)
            ex = {"source": label, "slots": sl,
                  "version": round(float(clip.version), 2),
                  "start": float(clip.start), "end": float(clip.end),
                  "fps": 30, "kind": "face" if clip.face else "body",
                  "counts": {k: v for k, v in clip.counts.items()},
                  "xhr_counts": dict(x.counts),
                  "max_shear_residual": shear}
            if clip.visibility:
                ex["visibility"] = [[list(p) for p in e]
                                    for e in clip.visibility]
                ex["visibility_bits"] = visibility_bits(clip, clip.frames())
            if clip.e_extra:
                ex["e_extra"] = clip.e_extra
            if stats:
                ex["compensation"] = dict(stats)
            anims.append({"name": name, "extras": ex,
                          "channels": [tuple(c) for c in channels]})
            summary.append(dict(name=name, clip=clip, tracks=tracks,
                                shear=shear))
    if not anims:
        return glb_bytes, summary
    return gltf.append_animations(glb_bytes, anims), summary


# ------------------------------------------------------------------ sweep

def _stem(path):
    return os.path.splitext(os.path.basename(path))[0].lower()


def sweep(root, verbose=False):
    """Walk every .xap / .fap under root.  Returns a stats dict; 'failures'
    lists what did not parse."""
    files, xhrs = [], {}
    for dp, _, fn in os.walk(root):
        for f in fn:
            lo = f.lower()
            if lo.endswith((".xap", ".fap")):
                files.append(os.path.join(dp, f))
            elif lo.endswith(".xhr"):
                xhrs.setdefault(_stem(f), []).append(os.path.join(dp, f))
    files.sort()
    st = Counter()
    keys = Counter()
    fails = []
    comp = Counter()
    rest_hit = rest_tot = 0
    max_shear = 0.0
    xhr_cache = {}
    for path in files:
        st["files"] += 1
        with open(path, "rb") as fh:
            buf = fh.read()
        try:
            xp = parse(buf)
        except XapError as e:
            fails.append((os.path.relpath(path, root), str(e)))
            continue
        st["slots"] += len(xp.slots)
        st["stubs"] += sum(c is None for c in xp.slots)
        stem = _stem(path)
        sk = None
        if stem in xhrs:
            xp_path = sorted(xhrs[stem])[0]
            if xp_path not in xhr_cache:
                try:
                    with open(xp_path, "rb") as fh:
                        xhr_cache[xp_path] = xhr.parse(fh.read())
                except xhr.XhrError:
                    xhr_cache[xp_path] = None
            sk = xhr_cache[xp_path]
        for c, sl in xp.clips():
            ver = "%s %.1f" % (c.magic.decode().strip(), c.version)
            st["clips"] += 1
            st["clips " + ver] += 1
            st["ok"] += 1
            if len(sl) > 1:
                st["shared slots"] += len(sl) - 1
            if c.padding:
                st["padded clips"] += 1
            if c.visibility:
                st["visibility clips"] += 1
            if not c.face and c.counts.get("A", 0) < 2:
                st["A count < 2"] += 1
            for r, blocks in c.runs.items():
                for b in blocks:
                    if b.flags & 1:
                        st["blocks with flags bit 0"] += 1
                    for cv in b.curves.values():
                        for ty in cv.types:
                            keys[(ver, int(ty))] += 1
                        neg = int((cv.frames < 0).sum())
                        st["negative key frames"] += neg
                        st["non-ascending pairs"] += int(
                            (np.diff(cv.frames) <= 0).sum())
                        h = cv.headers
                        if len(h) > 1:
                            lin8 = ((h[:-1] & 0x8000) != 0) & (h[1:] == 1) \
                                & ((h[:-1] & 0xFFF) != 1)
                            st["0x8000-linear segments"] += int(lin8.sum())
                            herm = ((h[:-1] & 0xFFF) != 1) & ~lin8 & \
                                ((h[1:] & 0xFFF) == 1)
                            st["Hermite-into-type-1 segments"] += int(herm.sum())
            if sk is None:
                continue
            st["clips paired with an .xhr"] += 1
            # A2: first key of T channels, run-A joints >= 2, v1.4
            if not c.face and F32(c.version) >= V14:
                for k, b in enumerate(c.runs["A"][:sk.counts["A"]]):
                    if k < 2:
                        continue
                    for ax in range(3):
                        cv = b.curves.get(ax)
                        if cv is None:
                            continue
                        rest_tot += 1
                        if abs(cv.values[0] - sk.joints[k].t[ax]) < 1e-3:
                            rest_hit += 1
            try:
                tr = animation_tracks(sk, c, stats=comp)
                max_shear = max(max_shear, max(t["shear"] for t in tr))
                bad = sum(int(not np.all(np.isfinite(t["world"])))
                          for t in tr)
                if bad:
                    st["clips with non-finite poses"] += 1
            except XapError as e:
                fails.append((os.path.relpath(path, root), "pose: %s" % e))
    vers = sorted({v for v, _ in keys})
    st = dict(st)
    st["key types"] = {v: {t: keys[(v, t)] for t in sorted(KEY_SIZE)
                           if keys[(v, t)]} for v in vers}
    tot = Counter()
    for (v, t), n in keys.items():
        tot[t] += n
    st["key types, all versions"] = dict(sorted(tot.items()))
    st["type-4 keys"] = 0              # parse refuses them
    st["compensation"] = dict(comp)
    st["max shear residual"] = max_shear
    st["A2 rest T first key"] = [rest_hit, rest_tot]
    st["failures"] = fails
    return st


# --------------------------------------------------------------- self-test

class _Pack(object):
    """Hand packer for synthetic files (independent of the parser)."""

    @staticmethod
    def key(ty, frame, value, a_in=0.0, a_out=0.0, last=False, lin8=False):
        h = ty | (0x4000 if last else 0) | (0x8000 if lin8 else 0)
        head = struct.pack("<Hh", h, frame)
        if ty in (0, 1):
            return head + struct.pack("<f", value)
        if ty == 2:
            return head + struct.pack("<ff", a_in, value)
        if ty == 3:
            return head + struct.pack("<HHf", int(a_in), int(a_out), value)
        if ty == 5:
            return head + struct.pack("<fff", a_in, a_out, value)
        return head + struct.pack("<f", value)          # type 4 etc

    @staticmethod
    def curve(keys):
        """keys: [(type, frame, value, a_in, a_out, lin8)]; last flag set
        on the final key."""
        out = b""
        for i, k in enumerate(keys):
            ty, fr, v = k[:3]
            ai = k[3] if len(k) > 3 else 0.0
            ao = k[4] if len(k) > 4 else 0.0
            l8 = k[5] if len(k) > 5 else False
            out += _Pack.key(ty, fr, v, ai, ao, i == len(keys) - 1, l8)
        return out

    @staticmethod
    def block(chans, flags=0, extra=None):
        """chans: {bit: keys}."""
        mask = sum(1 << b for b in chans)
        out = struct.pack("<HH", mask, flags)
        if extra is not None:
            out += struct.pack("<f", extra)
        for b in sorted(chans):
            out += _Pack.curve(chans[b])
        return out

    @staticmethod
    def clip(runs, start, end, version=1.4, magic=b"XAC ", vis=None):
        """runs: {name: [block bytes]}; layout per version."""
        if version >= 1.35:
            order = [("A", 0x10, 0x30), ("B", 0x14, 0x34), ("D", 0x18, 0x38),
                     ("E", 0x1C, 0x3C), ("V", 0x20, 0x40), ("C", 0x24, 0x44)]
            hsize = 0x50
        else:
            order = [("A", 0x10, 0x20), ("B", 0x14, 0x24), ("D", 0x18, 0x28),
                     ("E", 0x1C, 0x2C)]
            hsize = 0x30
        head = bytearray(hsize)
        struct.pack_into("<4sfff", head, 0, magic, version, start, end)
        if hsize == 0x50:
            head[0x28:0x30] = b"|Jseb|Jk"
            head[0x48:0x50] = b"ata_L|\0\0"
        body = b""
        for name, co, oo in order:
            if name == "V":
                items = [struct.pack("<i", len(e)) + b"".join(
                    struct.pack("<ff", f, v) for f, v in e)
                    for e in (vis or [])]
            else:
                items = runs.get(name, [])
            struct.pack_into("<I", head, co, len(items))
            struct.pack_into("<I", head, oo, hsize + len(body))
            body += b"".join(items)
        return bytes(head) + body

    @staticmethod
    def container(clips, magic=b"XAP ", share=None):
        """clips: list of bytes (None = stub); share: {slot: earlier slot}."""
        share = share or {}
        n = len(clips) + len(share)
        offs, blob = [], b""
        pos = 12 + 4 * n
        stub = struct.pack("<4sfff", magic[:1] + b"AC ", 1.4, 100.0, -100.0)
        made = []
        for c in clips:
            data = stub if c is None else c
            data += b"\0" * (-len(data) % 4)
            made.append(pos + len(blob))
            blob += data
        it = iter(made)
        slot_offs = []
        for s in range(n):
            slot_offs.append(made[share[s]] if s in share else next(it))
        return (magic + struct.pack("<fI", 1.2, n)
                + struct.pack("<%dI" % n, *slot_offs) + blob)


def _hand_hermite(f0, f1, y0, y1, s0, s1, t):
    """Cubic Hermite with slopes in value per second, written from the
    textbook basis (not from the module's code)."""
    L = (f1 - f0) / 30.0
    u = (t - f0) / 30.0 / L
    h00 = 2 * u ** 3 - 3 * u ** 2 + 1
    h10 = u ** 3 - 2 * u ** 2 + u
    h01 = -2 * u ** 3 + 3 * u ** 2
    h11 = u ** 3 - u ** 2
    return h00 * y0 + h10 * L * s0 + h01 * y1 + h11 * L * s1


def _expect_error(blob, what):
    try:
        parse(blob)
    except XapError:
        return
    raise AssertionError("parse accepted " + what)


def _curve_from(raw):
    """Parse one packed curve through the module (for evaluator tests)."""
    _, c = _curve(raw, 0, len(raw))
    return c


def _col_rot(v):
    cx, sx = np.cos(v[0]), np.sin(v[0])
    cy, sy = np.cos(v[1]), np.sin(v[1])
    cz, sz = np.cos(v[2]), np.sin(v[2])
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def self_test():
    import gltf
    P = _Pack
    near = lambda a, b, tol=1e-6: abs(a - b) < tol
    # a. hand-computed curve values ---------------------------------
    c = _curve_from(P.curve([(0, 5, 2.5)]))
    assert all(eval_curve_ref(c, t) == 2.5 for t in (-10, 5, 99))
    c = _curve_from(P.curve([(1, 0, 0.0), (1, 30, 3.0)]))
    assert near(eval_curve_ref(c, 10), 1.0)
    c = _curve_from(P.curve([(1, 10, 1.0), (1, 20, 2.0)]))
    assert eval_curve_ref(c, 0) == 1.0 and eval_curve_ref(c, 50) == 2.0
    assert near(eval_curve_ref(c, 15), 1.5)
    # flat tangents: smoothstep
    c = _curve_from(P.curve([(0, 0, 0.0), (0, 30, 1.0)]))
    assert near(eval_curve_ref(c, 10), 7.0 / 27.0) and \
        near(eval_curve_ref(c, 15), 0.5)
    assert near(7.0 / 27.0, 0.259259, 1e-6)
    # 0x8000 + next header 0x0001: linear; 0x8000 into type 2: Hermite
    c = _curve_from(P.curve([(0, 0, 0.0, 0, 0, True), (1, 30, 1.0),
                             (1, 40, 1.0)]))
    assert c.headers[1] == 1
    assert near(eval_curve_ref(c, 10), 1.0 / 3.0)
    c = _curve_from(P.curve([(0, 0, 0.0, 0, 0, True), (2, 30, 1.0, 0.0)]))
    assert near(eval_curve_ref(c, 10), 7.0 / 27.0)
    # type-2 angle = atan(chord slope) gives the straight line
    slope = (2.0 - 0.5) / (45 / 30.0)
    a = math.atan(slope)
    c = _curve_from(P.curve([(2, 15, 0.5, a), (2, 60, 2.0, a)]))
    for t in (15, 20, 33.3, 59.9):
        assert near(eval_curve_ref(c, t), 0.5 + slope * (t - 15) / 30.0)
    # type 3 (u16 x 1e-4) equals type 5 (f32) for 0.7854
    c3 = _curve_from(P.curve([(3, 0, 0.0, 7854, 7854), (3, 24, 1.0, 7854,
                                                         7854)]))
    c5 = _curve_from(P.curve([(5, 0, 0.0, 0.7854, 0.7854),
                              (5, 24, 1.0, 0.7854, 0.7854)]))
    for t in (3, 12, 20):
        assert near(eval_curve_ref(c3, t), eval_curve_ref(c5, t), 1e-6)
    # type 3 in != out: OUT of key k, IN of key k+1
    c = _curve_from(P.curve([(3, 0, 0.0, 1000, 3000), (3, 30, 1.0, 6000,
                                                        500)]))
    s0, s1 = math.tan(0.3), math.tan(0.6)
    for t in (7, 15, 26):
        want = _hand_hermite(0, 30, 0.0, 1.0, s0, s1, t)
        assert near(eval_curve_ref(c, t), want, 1e-6), (t, want)
    assert not near(eval_curve_ref(c, 15),
                    _hand_hermite(0, 30, 0.0, 1.0, math.tan(0.1),
                                  math.tan(0.05), 15), 1e-3)
    # b. vectorised == reference on random curves and times ----------
    rng = np.random.RandomState(11)
    curves = []
    for trial in range(40):
        nk = rng.randint(1, 9)
        fr = np.cumsum(rng.randint(0, 12, nk)) - rng.randint(0, 20)
        if trial % 5 == 0:
            fr = rng.randint(-10, 60, nk)               # non-ascending
        keys = []
        for i in range(nk):
            ty = int(rng.choice([0, 1, 2, 3, 5]))
            ai = rng.uniform(-1.5, 1.5) if ty in (2, 5) else rng.randint(
                0, 15000)
            ao = rng.uniform(-1.5, 1.5) if ty == 5 else rng.randint(0, 15000)
            l8 = bool(rng.rand() < 0.3)
            keys.append((ty, int(fr[i]), float(rng.uniform(-2, 2)), ai, ao,
                         l8))
        curves.append(_curve_from(P.curve(keys)))
    for cv in curves:
        ts = rng.uniform(-30, 120, 1000)
        ts[:20] = np.arange(-5, 15)
        vec = eval_curve(cv, ts)
        ref = np.array([eval_curve_ref(cv, t) for t in ts])
        ok = np.isclose(vec, ref, rtol=1e-9, atol=1e-6) | \
            ((np.abs(ref) > 1e3) & np.isclose(vec, ref, rtol=1e-9))
        assert ok.all(), (vec[~ok][:3], ref[~ok][:3])
    # synthetic skeleton and clip ------------------------------------
    xdata, spec, vals = xhr._synthetic()
    x = xhr.parse(xdata)
    # transforms: 0,1 roots; 2,3 A; 4 B; 5 C; 6 D; 7,8 E; 9 F
    ra = math.radians
    blocks_a = [
        P.block({}),                                            # root 0
        P.block({0: [(1, 0, 0.5), (1, 20, 1.5)],                # move root T
                 1: [(0, 0, -0.25)]}),
        P.block({3: [(2, 0, ra(10), 0.0), (2, 20, ra(50), 0.0)],
                 5: [(5, 0, 0.3, 0.1, 0.2), (5, 10, -0.2, 0.0, 0.0),
                     (1, 20, 0.4)]}),
        P.block({})]
    blk_b = [P.block({7: [(1, 0, 1.0), (1, 20, 2.0)]})]
    blk_c = [P.block({4: [(0, 0, 0.7)]})]
    blk_e = [P.block({2: [(1, 0, 0.0), (1, 20, 0.5)]}, extra=0.3),
             P.block({}, extra=0.7)]
    blk_d = [P.block({0: [(0, 0, 9.0)]})]
    vis = [[(0.0, 1.0), (10.0, 0.0)], [(5.0, 0.0), (12.0, 2.0)]]
    clip0 = P.clip({"A": blocks_a, "B": blk_b, "C": blk_c, "E": blk_e,
                    "D": blk_d}, 0.0, 20.0, vis=vis)
    clip13 = P.clip({"A": blocks_a[:3], "B": blk_b, "E": blk_e}, 0.0, 20.0,
                    version=1.3)
    old_e = [P.block({2: [(0, 0, 0.1)]}), P.block({})]          # 4-byte E
    clip12 = P.clip({"A": blocks_a[:2], "E": old_e}, 0.0, 10.0, version=1.2)
    data = P.container([clip0, None, clip13, clip12], share={1: 0})
    xp = parse(data)
    assert len(xp.slots) == 5 and xp.slots[2] is None
    assert xp.slots[1] is xp.slots[0]
    assert [sl for _, sl in xp.clips()] == [[0, 1], [3], [4]]
    c0 = xp.slots[0]
    assert c0.counts == dict(A=4, B=1, D=1, E=2, V=2, C=1)
    assert c0.e_extra == [F32(0.3), F32(0.7)]
    assert xp.slots[3].runs["E"][0].extra == F32(0.3)          # v1.3: f32
    assert xp.slots[4].runs["E"][0].extra is None              # v1.2: none
    assert xp.slots[4].runs["E"][0].curves[2].values[0] == F32(0.1)
    for cl, _ in xp.clips():
        for blocks in cl.runs.values():
            for b in blocks:
                for cv in b.curves.values():
                    ts = np.linspace(-5, 25, 301)
                    assert np.allclose(eval_curve(cv, ts), [
                        eval_curve_ref(cv, t) for t in ts], atol=1e-6)
    # c. damaged input ------------------------------------------------
    _expect_error(b"XAQ " + data[4:], "a bad magic")
    bad = bytearray(data)
    o = c0.runs["A"][1].curves[0].offset
    struct.pack_into("<H", bad, o, 4)
    _expect_error(bytes(bad), "key type 4")
    _expect_error(P.container([clip0[:-6]]), "a truncated curve")
    one_a = P.clip({"A": blocks_a[:1]}, 0.0, 10.0)
    _expect_error(P.container([one_a]), "an A count of 1")
    short = bytearray(clip0)
    struct.pack_into("<I", short, 0x1C, 1)                    # E 2 -> 1
    _expect_error(P.container([bytes(short)]), "a walk ending short")
    long_ = bytearray(clip0)
    struct.pack_into("<I", long_, 0x24, 2)                    # C 1 -> 2
    _expect_error(P.container([bytes(long_)]), "a walk past the end")
    _expect_error(P.container([clip0 + b"\0" * 8]), "8 bytes of tail")
    # d. pose against an independent column-vector composition ------
    t = 7.0
    loc, world, trs = pose(x, c0, t)

    def keyed(ti, bit, curve_keys):
        return eval_curve_ref(_curve_from(P.curve(curve_keys)), t)
    T1 = np.array(vals[1][0])
    T1[0] = 0.5 + (1.5 - 0.5) * t / 20
    T1[1] = -0.25
    R2 = np.array(vals[2][2])
    R2[0] = keyed(2, 3, [(2, 0, ra(10), 0.0), (2, 20, ra(50), 0.0)])
    R2[2] = keyed(2, 5, [(5, 0, 0.3, 0.1, 0.2), (5, 10, -0.2, 0.0, 0.0),
                         (1, 20, 0.4)])
    S4 = np.array([1.0, 1.0 + t / 20, 1.0])          # bit 7 = S.y
    R5 = np.array(vals[5][2])
    R5[1] = 0.7
    T7 = np.array(vals[7][0])
    T7[2] = 0.5 * t / 20
    ref = []
    for i, ((kind, par), (tt, jo, rr, ss)) in enumerate(zip(spec, vals)):
        tt, rr, ss = np.array(tt), np.array(rr), np.array(ss)
        if i == 1:
            tt = T1
        if i == 2:
            rr = R2
        if i == 4:
            ss = S4
        if i == 5:
            rr = R5
        if i == 7:
            tt = T7
        m = np.eye(4)
        jo_m = _col_rot(jo) if kind not in ("A0", "F") else np.eye(3)
        m[:3, :3] = jo_m @ _col_rot(rr) @ np.diag(ss)
        m[:3, 3] = tt
        ref.append(m if par < 0 else ref[par] @ m)
    for i in range(len(spec)):
        assert np.abs(world[i].T - ref[i]).max() < 1e-5, i   # f32 storage
    assert np.array_equal(trs[6]["t"], x.joints[6].t)   # D at rest in body
    # v1.3: run C at rest
    _, w13, trs13 = pose(x, xp.slots[3], t)
    assert np.array_equal(trs13[5]["r"], x.joints[5].r)
    # e. compensation -----------------------------------------------
    # child 3 (parent 2) with flags bit 0; parent 2 keyed S
    def comp_clip(ps, flag):
        a = [P.block({}), P.block({}),
             P.block({6: [(0, 0, ps[0])], 7: [(0, 0, ps[1])],
                      8: [(0, 0, ps[2])]}),
             P.block({6: [(0, 0, 0.5)], 7: [(0, 0, 0.75)], 8: [(0, 0, 1.5)]},
                     flags=flag)]
        return parse(P.container([P.clip({"A": a}, 0.0, 4.0)])).slots[0]
    cs = np.array([0.5, 0.75, 1.5])
    for flag in (1, 0):
        cl = comp_clip((2.0, 2.0, 2.0), flag)
        _, w, _ = pose(x, cl, 1.0)
        norms = np.linalg.norm(w[3][:3, :3], axis=1)    # rows = axes
        assert np.allclose(norms, cs * (1 if flag else 2)), (flag, norms)
        rel = w[3][3, :3] - w[2][3, :3]
        tr_parent = np.linalg.norm(rel)
        assert near(tr_parent, 2.0 * np.linalg.norm(x.joints[3].t), 1e-9)
        tracks = animation_tracks(x, cl)
        assert tracks[3]["shear"] == 0.0
        assert np.allclose(tracks[3]["scale"][0], cs / (2.0 if flag else 1))
    cl = comp_clip((2.0, 1.0, 1.0), 1)
    st = Counter()
    tracks = animation_tracks(x, cl, stats=st)
    assert tracks[3]["shear"] > 1e-3 and st["comp_nonuniform"] == 1
    # f. face quirks ---------------------------------------------------
    xd = bytearray(xdata)
    od = x.joints[6].offset
    struct.pack_into("<3f", xd, od + 0xC, 0.0, 0.0, 0.0)        # rest T 0
    xz = xhr.parse(bytes(xd))
    fblk = [P.block({0: [(0, 0, 3.0)], 6: [(0, 0, 2.0)], 4: [(0, 0, 0.4)]})]
    fac = parse(P.container([P.clip({"D": fblk}, 0.0, 5.0, magic=b"FAC ")],
                            magic=b"FAP ")).slots[0]
    _, _, ftrs = pose(xz, fac, 2.0)
    assert np.all(ftrs[6]["t"] == 0.0)                  # keyed T ignored
    assert np.all(ftrs[6]["s"] == 1.0)                  # rest S 1: ignored
    assert ftrs[6]["r"][1] == F32(0.4)
    struct.pack_into("<3f", xd, od + 0xC, 0.1, 0.0, 0.0)
    struct.pack_into("<3f", xd, od + 0x30, 1.0, 1.0, 1.1)
    _, _, ftrs = pose(xhr.parse(bytes(xd)), fac, 2.0)
    assert ftrs[6]["t"][0] == 3.0 and ftrs[6]["s"][0] == 2.0
    assert np.array_equal(ftrs[0]["t"], x.joints[0].t)  # body at rest
    # g. visibility (FUN_00295188 as disassembled) ---------------------
    e = [(0.0, 1.0), (10.0, 0.0)]
    assert visibility_value(e, 0.0) == 1.0              # first pair
    assert visibility_value(e, 5.0) == 0.0              # pair with frame >= t
    assert visibility_value(e, 10.0) == 0.0
    assert visibility_value(e, 15.0) == 0.0             # past the end: last
    e2 = [(5.0, 0.0), (12.0, 2.0)]
    assert visibility_value(e2, 3.0) == 0.0
    assert visibility_value(e2, 8.0) == 2.0
    assert visibility_value([], 3.0) is None
    bits = visibility_bits(c0, [0, 3, 8, 15])
    assert bits == [0b10, 0b11, 0b01, 0b01], bits
    # h. GLB round trip -------------------------------------------------
    import pxy
    pdata = pxy._synthetic(True, n_joint=7)[0]
    pm = pxy.parse(pdata)
    glb = pxy.to_glb(pm, None, x)
    out, summ = add_to_glb(glb, x, [("synthetic/body", xp, None, False),
                                    ("synthetic/face", parse(P.container(
                                        [P.clip({"D": fblk}, 0.0, 5.0,
                                                magic=b"FAC ")],
                                        magic=b"FAP ")), [0], True)])
    js, binc = gltf.read_glb(out)
    probs = gltf.validate(js, binc)
    assert probs == [], probs
    names = [a["name"] for a in js["animations"]]
    assert names == ["synthetic/body#00,01", "synthetic/body#03",
                     "synthetic/body#04", "synthetic/face#00"], names
    assert js["animations"][0]["extras"]["visibility"][0] == [[0.0, 1.0],
                                                              [10.0, 0.0]]
    node_of = {nd["name"]: i for i, nd in enumerate(js["nodes"])}
    for ai, (cl, _) in enumerate(xp.clips()):
        an = js["animations"][ai]
        for f in cl.frames():
            tsec = (f - int(cl.start)) / 30.0
            ov = gltf.sample_animation(js, binc, ai, tsec)
            gw = gltf.node_world_matrices(js, ov)
            _, w, _ = pose(x, cl, float(f))
            for j in x.joints:
                err = np.abs(gw[node_of[j.name]] - w[j.index].T).max()
                assert err < 1e-5, (ai, f, j.name, err)
        # sign continuity and single-key constants
        for ch in an["channels"]:
            smp = an["samplers"][ch["sampler"]]
            v = gltf.accessor_array(js, binc, smp["output"])
            if ch["target"]["path"] == "rotation" and len(v) > 1:
                assert (np.einsum("ij,ij->i", v[1:], v[:-1]) >= 0).all()
    an = js["animations"][0]
    counts = {}
    for ch in an["channels"]:
        n = js["accessors"][an["samplers"][ch["sampler"]]["input"]]["count"]
        counts[(js["nodes"][ch["target"]["node"]]["name"],
                ch["target"]["path"])] = n
    assert counts[("t01", "translation")] == 21
    assert counts[("t09", "rotation")] == 1             # run F: constant
    assert counts[("t03", "scale")] == 1
    assert len(an["channels"]) == 3 * len(x.joints)
    face_an = js["animations"][3]
    assert {js["nodes"][c["target"]["node"]]["name"]
            for c in face_an["channels"]} == {"t06"}
    # the face overlay: sampled D node matches the face pose
    ov = gltf.sample_animation(js, binc, 3, 1.0 / 30)
    assert set(ov) == {node_of["t06"]}
    # a mesh-only .glb is refused
    try:
        add_to_glb(pxy.to_glb(pm, None, None), x, [("s", xp, None, False)])
    except XapError:
        pass
    else:
        raise AssertionError("add_to_glb accepted a .glb without skeleton")
    # face flag must match the clip kind
    try:
        add_to_glb(glb, x, [("s", xp, None, True)])
    except XapError:
        pass
    else:
        raise AssertionError("add_to_glb took body clips as face clips")
    print("xap self-test ok")


# -------------------------------------------------------------------- CLI

def _print_table(path, xp):
    print("%s: %s %.1f, %d slots, %d stubs" % (
        path, xp.magic.decode().strip(), xp.version, len(xp.slots),
        sum(c is None for c in xp.slots)))
    for c, sl in xp.clips():
        cnt = " ".join("%s%d" % (k, v) for k, v in c.counts.items())
        print("  #%-10s %s %.1f  frames %g..%g  %s%s%s" % (
            ",".join("%02d" % s for s in sl), c.magic.decode().strip(),
            c.version, c.start, c.end, cnt,
            "  visibility %d" % len(c.visibility) if c.visibility else "",
            "  E f32 %s" % sorted(set(round(v, 3) for v in c.e_extra))
            if c.e_extra else ""))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] == "--self-test":
        return self_test()
    if argv[0] == "--sweep":
        st = sweep(argv[1])
        fails = st.pop("failures")
        for k, v in st.items():
            print("%-34s %s" % (k, json.dumps(v) if isinstance(v, (dict, list))
                                else v))
        h, n = st["A2 rest T first key"]
        print("A2 ratio: %d / %d = %.4f" % (h, n, h / float(n or 1)))
        for f in fails[:40]:
            print("FAIL", *f)
        print("%d failures" % len(fails))
        return 1 if fails or st.get("Hermite-into-type-1 segments") \
            or st.get("A count < 2") else 0
    path = argv[0]
    with open(path, "rb") as fh:
        xp = parse(fh.read())
    if "--xhr" not in argv:
        _print_table(path, xp)
        return 0
    with open(argv[argv.index("--xhr") + 1], "rb") as fh:
        x = xhr.parse(fh.read())
    slot = int(argv[argv.index("--slot") + 1]) if "--slot" in argv else 0
    frame = float(argv[argv.index("--frame") + 1]) if "--frame" in argv \
        else 0.0
    clip = xp.slots[slot]
    if clip is None:
        print("slot %d is an empty stub" % slot)
        return 1
    _, world, trs = pose(x, clip, frame)
    print("slot %d frame %g (%s %.1f, %g..%g)" % (slot, frame,
                                                  clip.magic.decode().strip(),
                                                  clip.version, clip.start,
                                                  clip.end))
    for j, tr in zip(x.joints, trs):
        print("  %s %-2s %s T %s R %s S %s%s  world %s" % (
            j.name, j.kind, "*" if tr["driven"] else " ",
            np.round(tr["t"], 4).tolist(), np.round(tr["r"], 4).tolist(),
            np.round(tr["s"], 4).tolist(),
            " comp 1/ps %s" % np.round(tr["inv_ps"], 4).tolist()
            if tr["comp"] else "",
            np.round(world[j.index][3, :3], 4).tolist()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
