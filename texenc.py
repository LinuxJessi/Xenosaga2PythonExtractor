"""texenc.py — encode edited RGBA pictures back into a txy block (bytes in, bytes out).

The inverse of ``txy.decode``. Given a texture page (one ``.txy`` file or a
txy block inside an effect file) and target pictures for some of its entries,
it writes index bytes, palette words and true-colour pixels where the decoder
reads them, so the block keeps its size and layout and only the bytes that
must change do. No disc I/O happens here; ``teximport.py`` does that.

Rules (FORMATS.md, "Textures", has the evidence):

* Alpha. The decoders show stored alpha a as min(2a, 255). The inverse is
  a = min(0x80, (A + 1) >> 1), so the PNG alphas that survive exactly are
  0, 2, ..., 254 and 255. A target alpha is snapped to that grid first.
* Unchanged means unchanged. Two colours with alpha 0 count as equal whatever
  RGB they carry (editors zero it). Wherever a pixel's target equals its
  original decoded colour it keeps its original index, and a palette slot or
  true-colour pixel whose decoded colour does not change keeps its stored bytes
  exactly (raw alpha above 0x80 included). That makes an unedited picture a
  byte-for-byte no-op even when a palette repeats a colour.
* Shared palettes. A CLUT read by several textures is solved for all of them
  at once (``solve_clut_group``). The default ("auto", the locked scheme):
  slots any unedited texture uses are frozen; new colours go into the slots
  nobody else uses, exactly when they fit, else by quantising the edited
  pictures into the free slots around the frozen colours; when there is no
  free slot at all the palette is kept and changed pixels take the nearest
  existing colour. "keep" always does the last; "joint" re-quantises every
  user together (the other textures then change too, and are reported).
* Twins. Entries with the identical pixel footprint and palette (the dup flag
  at +0x28) are one picture listed twice; editing one changes all, an unedited
  twin rides along, and two twins edited differently are refused.
* Refusals. Entries whose bank or (paletted) CLUT pointer is 0xFFFFFFFF, a
  palette whose words are also index pixels of some entry, a group mixing
  PSMT4 with PSMT8 (palette left as it is: only "keep" runs there), and two
  edits that would write different values into the same byte.

After writing, the new block is decoded with ``txy``'s scalar reference
decoder (not the numpy maps used to find the bytes) and every entry must come
out as planned: edited ones as their expected pictures, every other one
unchanged unless it is a twin or (joint mode) a palette sibling.

    python texenc.py --self-test
"""
from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import txy

W = np.array([0.30, 0.59, 0.11, 0.5])          # distance weights R, G, B, A
SW = np.sqrt(W)
MODES = ("auto", "keep", "joint")
DITHERS = ("none", "fs")


class EncodeError(ValueError):
    pass


# ------------------------------------------------------------------ colour

def a_raw(A):
    """Stored GS alpha for a decoded (PNG) alpha."""
    return np.minimum(0x80, (np.asarray(A, np.int32) + 1) >> 1)


def a_dec(a):
    return np.minimum(np.asarray(a, np.int32) * 2, 255)


def canon(rgba) -> np.ndarray:
    """(N,4) int32: alpha snapped to what the GS can show, and every colour
    with alpha 0 made (0, 0, 0, 0) so all transparent colours compare equal."""
    c = np.asarray(rgba).reshape(-1, 4).astype(np.int32).copy()
    c[:, 3] = a_dec(a_raw(c[:, 3]))
    c[c[:, 3] == 0] = 0
    return c


def pack(c) -> np.ndarray:
    c = np.asarray(c, np.int64).reshape(-1, 4)
    return (c[:, 0] << 24) | (c[:, 1] << 16) | (c[:, 2] << 8) | c[:, 3]


def same(a, b) -> np.ndarray:
    """Per-pixel equality under the alpha-0 rule (inputs raw or canonical)."""
    return (canon(a) == canon(b)).all(1)


def psnr(target, got) -> float:
    a, b = canon(target).astype(np.float64), canon(got).astype(np.float64)
    if a.size == 0:
        return math.inf
    mse = ((a - b) ** 2).mean()
    return math.inf if mse == 0 else 10 * math.log10(255.0 ** 2 / mse)


def nearest(px, pal, allowed=None, chunk=1 << 15) -> np.ndarray:
    """Index of the nearest palette colour (weighted squared distance, both
    sides canonical) for each pixel. ``allowed``: boolean mask of usable slots."""
    px = np.asarray(px, np.float64).reshape(-1, 4)
    pal = np.asarray(pal, np.float64).reshape(-1, 4)
    out = np.empty(len(px), np.int64)
    pw = pal * SW
    pn = (pw * pw).sum(1)
    if allowed is not None:
        pn = np.where(allowed, pn, np.inf)
    for s in range(0, len(px), chunk):
        q = px[s:s + chunk] * SW
        d = (q * q).sum(1)[:, None] - 2 * q @ pw.T + pn[None, :]
        out[s:s + chunk] = d.argmin(1)
    return out


# --------------------------------------------------------------- quantiser
# Median cut over unique colours weighted by pixel count, then weighted
# k-means, around optional fixed centres. Cleaned up from the research
# prototype (scratch texture-import-r2/quant.py, 2026-11-20). No random
# numbers: ties break by order, so a run is repeatable.

def _snap_centres(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.rint(c), 0, 255).astype(np.int32)
    return canon(c)


def _median_cut(cols: np.ndarray, wts: np.ndarray, k: int) -> np.ndarray:
    boxes = [np.arange(len(cols))]
    while len(boxes) < k:
        best, bi, bc = -1.0, -1, 0
        for i, b in enumerate(boxes):
            if len(b) < 2:
                continue
            rng = (cols[b].max(0) - cols[b].min(0)) * SW
            sc = float(rng.max()) * math.sqrt(float(wts[b].sum()) + 1e-9)
            if sc > best:
                best, bi, bc = sc, i, int(rng.argmax())
        if bi < 0 or best <= 0:
            break
        b = boxes.pop(bi)
        o = b[np.argsort(cols[b, bc], kind="stable")]
        cum = np.cumsum(wts[o])
        m = int(np.searchsorted(cum, cum[-1] / 2)) + 1
        m = min(max(m, 1), len(o) - 1)
        boxes += [o[:m], o[m:]]
    out = []
    for b in boxes:
        s = wts[b].sum()
        out.append((cols[b] * wts[b, None]).sum(0) / s if s > 0 else cols[b].mean(0))
    return np.array(out, np.float64).reshape(-1, 4)


def quantise(colours, counts, k: int, fixed=None, rounds: int = 8) -> np.ndarray:
    """At most k new canonical centres for the weighted colours, chosen around
    the ``fixed`` centres (colours that stay in the palette anyway). When the
    distinct colours not already fixed fit in k they are returned exactly."""
    cols = canon(colours)
    cnt = np.asarray(counts, np.float64)
    fixed = canon(fixed) if fixed is not None and len(fixed) else np.zeros((0, 4), np.int32)
    if len(fixed):
        keep = ~np.isin(pack(cols), pack(fixed))
        cols, cnt = cols[keep], cnt[keep]
    if k <= 0 or len(cols) == 0:
        return np.zeros((0, 4), np.int32)
    u, inv = np.unique(pack(cols), return_inverse=True)
    if len(u) <= k:
        first = np.zeros(len(u), np.int64)
        first[inv[::-1]] = np.arange(len(inv))[::-1]
        return cols[first]
    cnt_u = np.zeros(len(u))
    np.add.at(cnt_u, inv, cnt)
    first = np.zeros(len(u), np.int64)
    first[inv[::-1]] = np.arange(len(inv))[::-1]
    cf = cols[first].astype(np.float64)
    wts = cnt_u.copy()
    if len(fixed):                       # spend the free slots where the fixed ones serve worst
        d = ((cf[:, None, :] - fixed[None].astype(np.float64)) ** 2 * W).sum(-1).min(1)
        wts = cnt_u * d
    cent = _median_cut(cf, wts, k)
    nf = len(fixed)
    for _ in range(rounds):
        allc = np.vstack([fixed.astype(np.float64), cent])
        a = nearest(cf, allc) - nf
        live = a >= 0
        s = np.zeros_like(cent)
        n = np.zeros(len(cent))
        np.add.at(s, a[live], cf[live] * cnt_u[live, None])
        np.add.at(n, a[live], cnt_u[live])
        ok = n > 0
        cent[ok] = s[ok] / n[ok, None]
    cent = _snap_centres(cent)
    _, keep = np.unique(pack(cent), return_index=True)
    cent = cent[np.sort(keep)]
    if len(fixed):
        cent = cent[~np.isin(pack(cent), pack(fixed))]
    return cent


def map_pixels(target, pal, allowed=None, dither: str = "none", shape=None, mask=None) -> np.ndarray:
    """Nearest palette index for each target pixel; with dither "fs",
    Floyd-Steinberg over the pixels in ``mask`` only (others are not touched
    and receive no error), RGB error only, never alpha, and no error into or
    out of transparent pixels."""
    tgt = canon(target)
    if dither != "fs" or shape is None:
        return nearest(tgt, pal, allowed)
    w, h = shape
    m = np.ones(len(tgt), bool) if mask is None else np.asarray(mask, bool)
    out = nearest(tgt, pal, allowed)
    err = np.zeros((h, w, 3))
    palf = np.asarray(pal, np.float64)
    pn = ((palf * SW) ** 2).sum(1)
    if allowed is not None:
        pn = np.where(allowed, pn, np.inf)
    pw = palf * SW
    for p in np.nonzero(m)[0]:
        y, x = divmod(int(p), w)
        v = tgt[p].astype(np.float64)
        if v[3] == 0:
            continue
        v[:3] = np.clip(v[:3] + err[y, x], 0, 255)
        q = v * SW
        i = int(((q * q).sum() - 2 * pw @ q + pn).argmin())
        out[p] = i
        e = v[:3] - palf[i, :3]
        for dx, dy, f in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
            xx, yy = x + dx, y + dy
            if 0 <= xx < w and yy < h and m[yy * w + xx] and tgt[yy * w + xx, 3]:
                err[yy, xx] += e * f
    return out


# ------------------------------------------------------- shared-CLUT solver

@dataclass
class User:
    """One picture reading a palette: original indices and colours, the
    target (None = not edited), and pixels whose index cannot change
    (``fixed``: not stored in the file, so they always read what they read)."""
    idx: np.ndarray
    orig: np.ndarray
    target: Optional[np.ndarray] = None
    shape: Optional[Tuple[int, int]] = None
    fixed: Optional[np.ndarray] = None
    name: str = ""


@dataclass
class Solution:
    pal: np.ndarray                     # (K,4) decoded logical palette after the edit
    changed_slots: List[int]
    idx: List[np.ndarray]               # per user, new indices
    expected: List[np.ndarray]          # per user, decoded RGBA after the edit
    action: str                         # unchanged / exact / locked-quantised / keep-fallback / keep / joint
    warnings: List[str] = field(default_factory=list)


def _assign(cent, slots_free, pal_c, nobody) -> Dict[int, np.ndarray]:
    """Give each centre a slot: a slot already holding that colour first (no
    byte change), then slots no picture references, then the rest."""
    out: Dict[int, np.ndarray] = {}
    free = list(slots_free)
    keyp = {}
    for s in free:
        keyp.setdefault(int(pack(pal_c[s:s + 1])[0]), []).append(s)
    rest = []
    for c in cent:
        k = int(pack(c[None])[0])
        if keyp.get(k):
            s = keyp[k].pop(0)
            out[s] = c
            free.remove(s)
        else:
            rest.append(c)
    free.sort(key=lambda s: (s not in nobody, s))
    for c, s in zip(rest, free):
        out[s] = c
    return out


def solve_clut_group(pal: np.ndarray, users: Sequence[User], mode: str = "auto",
                     dither: str = "none", frozen: Sequence[int] = ()) -> Solution:
    """Solve one palette for all its users. ``pal``: (K,4) decoded logical
    palette; ``frozen``: slots that cannot be written (not stored in the file)."""
    if mode not in MODES:
        raise EncodeError(f"unknown palette mode {mode!r}")
    K = len(pal)
    pal_c = canon(pal)
    frozen = set(int(s) for s in frozen)
    edited = [j for j, u in enumerate(users) if u.target is not None]
    fixedm = []
    for u in users:
        f = np.zeros(len(u.idx), bool) if u.fixed is None else np.asarray(u.fixed, bool)
        fixedm.append(f | (u.idx < 0))
    tgt = {j: canon(users[j].target) for j in edited}
    for j in edited:                     # a pixel that cannot change keeps its original colour
        tgt[j][fixedm[j]] = canon(users[j].orig[fixedm[j]])
    unch = {j: (tgt[j] == canon(users[j].orig)).all(1) | fixedm[j] for j in edited}

    def used(js, sel=None):
        s = set()
        for j in js:
            ii = users[j].idx if sel is None else users[j].idx[sel[j]]
            s |= set(int(v) for v in np.unique(ii) if v >= 0)
        return s

    unedited = [j for j in range(len(users)) if users[j].target is None]
    P_U = used(unedited) | used(edited, {j: fixedm[j] for j in edited}) | frozen
    warnings: List[str] = []

    def finish(new_pal_c, new_idx, action):
        new_pal = np.asarray(pal, np.int32).copy()
        changed = [s for s in range(K) if not (new_pal_c[s] == pal_c[s]).all()]
        for s in changed:
            new_pal[s] = new_pal_c[s]
        exp = []
        for u, ii in zip(users, new_idx):
            e = np.zeros((len(ii), 4), np.int32)
            ok = ii >= 0
            e[ok] = new_pal[ii[ok]]
            e[~ok] = u.orig[~ok]
            exp.append(e.astype(np.uint8))
        if not changed and all((a == u.idx).all() for a, u in zip(new_idx, users)):
            action = "unchanged"
        return Solution(new_pal.astype(np.uint8), changed, new_idx, exp, action, warnings)

    if not edited:
        return finish(pal_c, [u.idx.copy() for u in users], "unchanged")

    if mode == "joint":
        fixed_slots = used(range(len(users)), {j: fixedm[j] for j in range(len(users))}) | frozen
        free = [s for s in range(K) if s not in fixed_slots]
        allt = {j: (tgt[j] if j in tgt else canon(users[j].orig)) for j in range(len(users))}
        stack = np.vstack([allt[j][~fixedm[j]] for j in range(len(users))] + [np.zeros((0, 4), np.int32)])
        fx = pal_c[sorted(fixed_slots)] if fixed_slots else None
        # exact colours already in a free slot need no new slot
        cent = quantise(stack, np.ones(len(stack)), len(free), fx)
        new_c = pal_c.copy()
        for s, c in _assign(cent, free, pal_c, set(free)).items():
            new_c[s] = c
        # unused free slots: keep their colours (they are not referenced after re-indexing)
        new_idx = []
        for j, u in enumerate(users):
            keep = (allt[j] == canon(u.orig)).all(1) & (new_c[np.maximum(u.idx, 0)] == pal_c[np.maximum(u.idx, 0)]).all(1)
            keep |= fixedm[j]
            ii = u.idx.copy()
            need = ~keep
            if need.any():
                ii[need] = map_pixels(allt[j][need], new_c, dither="none")
            new_idx.append(ii)
        return finish(new_c, new_idx, "joint")

    if mode == "keep":
        return _keep(pal_c, users, edited, tgt, unch, fixedm, dither, finish, "keep")

    # ---- auto: the locked scheme
    P_K = used(edited, unch)
    fixed_colour = P_U | P_K
    keys_fixed = {}
    for s in sorted(fixed_colour):
        keys_fixed.setdefault(int(pack(pal_c[s:s + 1])[0]), s)
    need_cols = []
    for j in edited:
        ch = ~unch[j]
        if ch.any():
            need_cols.append(tgt[j][ch])
    need = np.vstack(need_cols) if need_cols else np.zeros((0, 4), np.int32)
    nk = np.unique(pack(need)) if len(need) else np.zeros(0, np.int64)
    N = [k for k in nk.tolist() if k not in keys_fixed]
    F = [s for s in range(K) if s not in fixed_colour]
    referenced = used(range(len(users)))
    if len(N) <= len(F):
        cent = np.array([[(k >> 24) & 255, (k >> 16) & 255, (k >> 8) & 255, k & 255] for k in N],
                        np.int32).reshape(-1, 4)
        new_c = pal_c.copy()
        for s, c in _assign(cent, F, pal_c, set(F) - referenced).items():
            new_c[s] = c
        lut = {}
        for s in sorted(fixed_colour) + sorted(F):
            lut.setdefault(int(pack(new_c[s:s + 1])[0]), s)
        new_idx = []
        for j, u in enumerate(users):
            ii = u.idx.copy()
            if j in tgt:
                ch = ~unch[j]
                if ch.any():
                    ii[ch] = [lut[k] for k in pack(tgt[j][ch]).tolist()]
            new_idx.append(ii)
        return finish(new_c, new_idx, "exact" if N or any((~unch[j]).any() for j in edited) else "unchanged")

    Fp = [s for s in range(K) if s not in P_U]
    if not Fp:
        names = ", ".join(sorted({users[j].name for j in unedited if users[j].name})) or "other textures"
        sol = _keep(pal_c, users, edited, tgt, unch, fixedm, dither, finish, "keep-fallback")
        ps = min((psnr(tgt[j][~unch[j]], sol.expected[j][~unch[j]]) for j in edited if (~unch[j]).any()),
                 default=math.inf)
        sol.warnings.append(
            f"palette full: every slot is used by {names}; changed pixels took the nearest existing "
            f"colour (PSNR {ps:.1f} dB). --palette joint re-quantises them together (they change too)")
        return sol
    stack = np.vstack([tgt[j][~fixedm[j]] for j in edited])
    fx = pal_c[sorted(P_U)] if P_U else None
    cent = quantise(stack, np.ones(len(stack)), len(Fp), fx)
    new_c = pal_c.copy()
    for s, c in _assign(cent, Fp, pal_c, set(Fp) - used(unedited)).items():
        new_c[s] = c
    new_idx = []
    for j, u in enumerate(users):
        ii = u.idx.copy()
        if j in tgt:
            safe = np.maximum(u.idx, 0)
            keep = unch[j] & (new_c[safe] == pal_c[safe]).all(1)
            keep |= fixedm[j]
            need_m = ~keep
            if need_m.any():
                if dither == "fs" and u.shape:
                    full = map_pixels(tgt[j], new_c, None, "fs", u.shape, need_m)
                    ii[need_m] = full[need_m]
                else:
                    ii[need_m] = nearest(tgt[j][need_m], new_c)
        new_idx.append(ii)
    return finish(new_c, new_idx, "locked-quantised")


def _keep(pal_c, users, edited, tgt, unch, fixedm, dither, finish, action):
    new_idx = []
    for j, u in enumerate(users):
        ii = u.idx.copy()
        if j in tgt:
            ch = ~unch[j]
            if ch.any():
                if dither == "fs" and u.shape:
                    full = map_pixels(tgt[j], pal_c, None, "fs", u.shape, ch)
                    ii[ch] = full[ch]
                else:
                    ii[ch] = nearest(tgt[j][ch], pal_c)
        new_idx.append(ii)
    return finish(pal_c.copy(), new_idx, action)


# ------------------------------------------------------------ block analysis

@dataclass
class EntryInfo:
    e: dict
    fmt: int
    w: int
    h: int
    bank: int                         # bank the GS state is taken after
    addr: np.ndarray                  # (N,) byte offset in the block of each pixel's index/word, -1 = not stored
    shift: Optional[np.ndarray]       # PSMT4 nibble shift
    idx: Optional[np.ndarray]         # original indices (paletted)
    clut: Optional[np.ndarray]        # (K,) word offsets of logical slots, -1 = not stored
    pal: Optional[np.ndarray]         # (K,4) decoded logical palette
    orig: np.ndarray                  # (N,4) decoded RGBA, equal to txy.decode's
    reads: np.ndarray = None          # sorted unique bytes the decode reads
    bogus: str = ""

    def nibbles(self) -> np.ndarray:
        """Nibble addresses (byte*2 + high) the pixels occupy (paletted)."""
        if self.fmt == txy.PSMT4:
            return np.where(self.addr >= 0, self.addr * 2 + self.shift // 4, -1)
        return self.addr


def analyse(block: bytes, t: Optional[dict] = None) -> Tuple[dict, Dict[int, EntryInfo]]:
    """Parse a block and resolve, for every decodable entry, where each pixel
    and palette slot lives in the block."""
    t = t or txy.parse(block)
    infos: Dict[int, EntryInfo] = {}
    if not t["banks"]:
        return t, infos
    nb = len(t["banks"])
    cw, ch = t["canvas_w"], txy.canvas_height(t)
    owns, canv = {}, {}
    blk = np.frombuffer(block, np.uint8)
    pos = np.array(txy.csm1_positions(), np.int64)
    for e in t["entries"]:
        w, h = e["w"], e["h"]
        if not (0 < w <= 4096 and 0 < h <= 4096) or e["fmt"] not in (txy.PSMT8, txy.PSMT4, txy.PSMCT32):
            continue
        b = min(e["bank"], nb - 1)
        if b not in owns:
            owns[b] = txy.owner_map(t, b).reshape(-1)
            canv[b] = txy.canvas_np(block, owns[b].reshape(ch, cw))
        own, cv = owns[b], canv[b]
        n = cv.size
        ys, xs = np.mgrid[0:h, 0:w]
        xs = xs.reshape(-1).astype(np.int64) + e["x"]
        ys = ys.reshape(-1).astype(np.int64) + e["y"]
        bogus = ""
        if e["bank"] >= nb:
            bogus = f"bank {e['bank']:#x} is not one of the file's {nb} banks"
        if e["fmt"] == txy.PSMCT32:
            pix = ys * cw + xs
            inr = pix * 4 < n
            addr = np.full(pix.size, -1, np.int64)
            addr[inr] = own[pix[inr]]
            orig = np.zeros((pix.size, 4), np.uint8)
            orig[inr] = cv.reshape(-1, 4)[pix[inr]]
            orig[:, 3] = a_dec(orig[:, 3])
            reads = addr[addr >= 0]
            reads = np.unique((reads[:, None] + np.arange(4)).reshape(-1))
            infos[e["i"]] = EntryInfo(e, e["fmt"], w, h, b, addr, None, None, None, None, orig, reads, bogus)
            continue
        if e["fmt"] == txy.PSMT8:
            if e["palx"] + 16 > cw or e["paly"] + 16 > ch:
                continue
            cells = (e["paly"] + pos // 16) * cw + e["palx"] + pos % 16
            offs = txy.swz8_np(cw, xs, ys)
            sh = None
        else:
            if e["palx"] + 8 > cw or e["paly"] + 2 > ch:
                continue
            i16 = np.arange(16, dtype=np.int64)
            cells = (e["paly"] + i16 // 8) * cw + e["palx"] + i16 % 8
            offs, sh = txy.swz4_np(cw, xs, ys)
        if e["cbp"] == 0xFFFFFFFF and not bogus:
            bogus = "its CLUT pointer is 0xFFFFFFFF"
        clut = own[cells]
        pal = cv.reshape(-1, 4)[cells].copy()
        pal[:, 3] = a_dec(pal[:, 3])
        inr = offs < n
        cov = np.zeros(offs.size, bool)
        cov[inr] = own[offs[inr] // 4] >= 0
        addr = np.full(offs.size, -1, np.int64)
        addr[cov] = own[offs[cov] // 4] + offs[cov] % 4
        idx = np.zeros(offs.size, np.int64)
        idx[inr] = cv[offs[inr]]
        if sh is not None:
            idx[inr] = (idx[inr] >> sh[inr]) & 0xF
            idx[~inr] = -1                  # outside the canvas: transparent black, no slot
        orig = np.zeros((offs.size, 4), np.uint8)
        orig[idx >= 0] = pal[idx[idx >= 0]]
        cr = clut[clut >= 0]
        reads = np.unique(np.concatenate([addr[addr >= 0], (cr[:, None] + np.arange(4)).reshape(-1)]))
        infos[e["i"]] = EntryInfo(e, e["fmt"], w, h, b, addr, sh, idx, clut, pal.astype(np.uint8), orig,
                                  reads, bogus)
    return t, infos


def _class_key(inf: EntryInfo) -> bytes:
    parts = [struct.pack("<iii", inf.fmt, inf.w, inf.h), inf.addr.tobytes()]
    if inf.shift is not None:
        parts.append(inf.shift.tobytes())
    if inf.clut is not None:
        parts.append(inf.clut.tobytes())
    return b"".join(parts)


@dataclass
class BlockResult:
    block: bytes
    expected: Dict[int, bytes]          # entry -> decoded RGBA after the edit (every changed or edited entry)
    roles: Dict[int, str]               # entry -> edited / ride-along / joint-changed
    rows: List[dict]
    warnings: List[str]


def _canon_bytes(rgba) -> np.ndarray:
    return canon(np.frombuffer(bytes(rgba), np.uint8) if isinstance(rgba, (bytes, bytearray)) else rgba)


def encode_block(block: bytes, edits: Dict[int, object], mode: str = "auto", dither: str = "none",
                 t: Optional[dict] = None, force: bool = False, info=None) -> BlockResult:
    """Write ``edits`` {entry index: target RGBA (bytes or (N,4) array)} into
    a copy of ``block``. ``force`` keeps an edit whose target equals the
    original (the round-trip check), instead of skipping it."""
    if mode not in MODES or dither not in DITHERS:
        raise EncodeError(f"unknown mode {mode!r} or dither {dither!r}")
    t, infos = info if info is not None else analyse(block, t)
    names = {i: inf.e["name"] for i, inf in infos.items()}
    tg: Dict[int, np.ndarray] = {}
    for i, rgba in edits.items():
        if i not in infos:
            raise EncodeError(f"entry {i} is not a decodable picture (a placeholder or out of range)")
        inf = infos[i]
        a = np.frombuffer(bytes(rgba), np.uint8).reshape(-1, 4) if isinstance(rgba, (bytes, bytearray)) \
            else np.asarray(rgba, np.uint8).reshape(-1, 4)
        if len(a) != inf.w * inf.h:
            raise EncodeError(f"entry {i} ({names[i]}): target has {len(a)} pixels, the texture {inf.w}x{inf.h}")
        if not force and same(a, inf.orig).all():
            continue
        if inf.bogus:
            raise EncodeError(f"entry {i} ({names[i]}) is refused: {inf.bogus} (FORMATS.md, the bogus "
                              f"E2_ELS01 entries)")
        tg[i] = a
    # twins: identical footprint and palette words
    classes: Dict[bytes, List[int]] = {}
    for i, inf in infos.items():
        classes.setdefault(_class_key(inf), []).append(i)
    cls_of = {}
    members: List[List[int]] = []
    for mem in classes.values():
        for i in mem:
            cls_of[i] = len(members)
        members.append(sorted(mem))
    ctarget: Dict[int, np.ndarray] = {}
    for i, a in tg.items():
        c = cls_of[i]
        if c in ctarget and not (canon(ctarget[c]) == canon(a)).all():
            j = next(k for k in members[c] if k in tg and k != i)
            raise EncodeError(f"entries {j} ({names[j]}) and {i} ({names[i]}) are the same pixels in the file "
                              f"(a re-listed entry, dup flag) but were edited differently; edit one of them")
        ctarget[c] = a
    rep = {c: infos[mem[0]] for c, mem in enumerate(members)}
    # bytes that are index pixels of some entry
    pix_bytes = np.unique(np.concatenate([inf.addr[inf.addr >= 0] if inf.fmt != txy.PSMCT32 else
                                          (inf.addr[inf.addr >= 0][:, None] + np.arange(4)).reshape(-1)
                                          for inf in rep.values()] + [np.zeros(0, np.int64)]))
    # palette groups: classes whose CLUT words overlap, merged
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    word_owner: Dict[int, int] = {}
    pal_classes = [c for c, inf in rep.items() if inf.clut is not None]
    for c in pal_classes:
        parent[c] = c
    for c in pal_classes:
        for wo in rep[c].clut[rep[c].clut >= 0].tolist():
            if wo in word_owner:
                a, b = find(word_owner[wo]), find(c)
                if a != b:
                    parent[b] = a
            else:
                word_owner[wo] = c
    groups: Dict[int, List[int]] = {}
    for c in pal_classes:
        groups.setdefault(find(c), []).append(c)

    buf = np.frombuffer(block, np.uint8).copy()
    nib_val = np.full(len(block) * 2, -1, np.int16)
    nib_who = np.full(len(block) * 2, -1, np.int32)

    def claim(c, nibs, vals):
        nibs = np.asarray(nibs, np.int64)
        vals = np.asarray(vals, np.int16)
        o = np.argsort(nibs, kind="stable")
        sn, sv = nibs[o], vals[o]
        dupe = (sn[1:] == sn[:-1]) & (sv[1:] != sv[:-1])
        if dupe.any():
            raise EncodeError(f"entry {members[c][0]} ({names[members[c][0]]}) reads one stored pixel at two "
                              f"places and the edit gives them different colours")
        old = nib_val[nibs]
        clash = (old >= 0) & (old != vals)
        if clash.any():
            other = members[int(nib_who[nibs[clash][0]])][0]
            me = members[c][0]
            raise EncodeError(f"entries {other} ({names[other]}) and {me} ({names[me]}) overlap in the file and "
                              f"the edits write different values into the same bytes")
        nib_val[nibs] = vals
        nib_who[nibs] = c

    expected_c: Dict[int, np.ndarray] = {}
    actions: Dict[int, str] = {}
    slots_changed: Dict[int, int] = {}
    warnings: List[str] = []
    joint_changed: set = set()
    written_by: Dict[int, np.ndarray] = {}

    for g, cls in groups.items():
        if not any(c in ctarget for c in cls):
            continue
        fmts = {rep[c].fmt for c in cls}
        cluts = {rep[c].clut.tobytes() for c in cls}
        words = rep[cls[0]].clut
        wbytes = (words[words >= 0][:, None] + np.arange(4)).reshape(-1)
        gmode = mode
        why = ""
        if len(fmts) > 1:
            why = "the palette is read by both 16- and 256-colour textures"
        elif len(cluts) > 1:
            why = "palettes overlap without lining up"
        elif np.isin(wbytes, pix_bytes).any():
            why = "the palette words are also index pixels of a texture"
        if why:
            gmode = "keep"
        users = []
        for c in cls:
            inf = rep[c]
            users.append(User(inf.idx, inf.orig, ctarget.get(c), (inf.w, inf.h), inf.addr < 0,
                              names[members[c][0]]))
        frozen = [s for s in range(len(words)) if words[s] < 0]
        sol = solve_clut_group(rep[cls[0]].pal, users, gmode, dither, frozen)
        if why and any(c in ctarget for c in cls):
            warnings.append(f"{', '.join(names[members[c][0]] for c in cls if c in ctarget)}: palette kept "
                            f"({why}); changed pixels took the nearest existing colour")
        warnings += sol.warnings
        # palette words
        for s in sol.changed_slots:
            wo = int(words[s])
            if wo < 0:
                raise EncodeError("internal: a palette slot that is not stored was changed")
            r, gg, b, A = (int(v) for v in sol.pal[s])
            word = bytes((r, gg, b, int(a_raw(A))))
            claim(cls[0], np.arange(wo * 2, wo * 2 + 8),
                  [v for byte in word for v in (byte & 15, byte >> 4)])
            buf[wo:wo + 4] = np.frombuffer(word, np.uint8)
        action = "keep-fallback" if why else sol.action
        for j, c in enumerate(cls):
            inf = rep[c]
            ii = sol.idx[j]
            reindexed = not (ii == inf.idx).all()
            if c not in ctarget:
                if not reindexed and (sol.expected[j] == inf.orig).all():
                    continue                     # an untouched sibling
                joint_changed.add(c)
            if c in ctarget or reindexed:
                st = inf.addr >= 0
                a, v = inf.addr[st], ii[st].astype(np.int64)
                if inf.fmt == txy.PSMT8:
                    claim(c, np.concatenate([a * 2, a * 2 + 1]), np.concatenate([v & 15, v >> 4]))
                    buf[a] = v
                else:
                    s4 = inf.shift[st]
                    claim(c, a * 2 + s4 // 4, v)
                    for sh in (0, 4):            # one nibble position per pass: no lost updates
                        m = s4 == sh
                        buf[a[m]] = (buf[a[m]] & (0xF0 >> sh)) | (v[m] << sh)
                written_by[c] = np.unique(a)
            expected_c[c] = sol.expected[j]
            actions[c] = action
            slots_changed[c] = len(sol.changed_slots)

    # true colour
    for c, a in ctarget.items():
        inf = rep[c]
        if inf.fmt != txy.PSMCT32:
            continue
        tc = canon(a)
        oc = canon(inf.orig)
        st = inf.addr >= 0
        changed = ~(tc == oc).all(1) & st
        exp = inf.orig.astype(np.int32).copy()
        rawb = np.frombuffer(block, np.uint8)
        words = np.zeros((len(a), 4), np.int64)
        words[st] = rawb[(inf.addr[st][:, None] + np.arange(4)).reshape(-1)].reshape(-1, 4)
        new = words.copy()
        new[changed, :3] = a[changed, :3]
        keep_a = changed & (tc[:, 3] == oc[:, 3])
        new[changed & ~keep_a, 3] = a_raw(a[changed & ~keep_a, 3])
        exp[changed, :3] = a[changed, :3]
        exp[changed, 3] = a_dec(new[changed, 3])
        ad = inf.addr[st]
        nv = new[st]
        nibs = (ad[:, None] * 2 + np.arange(8)).reshape(-1)
        vals = np.stack([nv & 15, nv >> 4], -1).reshape(-1)
        claim(c, nibs, vals)
        buf[(ad[:, None] + np.arange(4)).reshape(-1)] = nv.reshape(-1)
        written_by[c] = np.unique((ad[:, None] + np.arange(4)).reshape(-1))
        expected_c[c] = exp.astype(np.uint8)
        actions[c] = "true colour"
        slots_changed[c] = 0

    new_block = buf.tobytes()
    # ---- check with the scalar reference decoder
    old_arr = np.frombuffer(block, np.uint8)
    new_arr = np.frombuffer(new_block, np.uint8)
    diff = np.nonzero(old_arr != new_arr)[0]
    nb = len(t["banks"])
    cw, ch = t["canvas_w"], txy.canvas_height(t)
    canvases = {}
    expected: Dict[int, bytes] = {}
    roles: Dict[int, str] = {}
    rows: List[dict] = []
    for i, inf in infos.items():
        c = cls_of[i]
        hit = bool(len(diff)) and bool(np.isin(diff, inf.reads, assume_unique=True).any())
        if hit:
            b = inf.bank
            if b not in canvases:
                canvases[b] = txy.compose(new_block, t, b)[0]
            got = txy.decode_entry(canvases[b], cw, ch, inf.e)
            got = np.frombuffer(got, np.uint8).reshape(-1, 4)
        else:
            got = inf.orig
        changed_px = ~(got == inf.orig).all(1)
        if c in expected_c:
            want = expected_c[c]
            if not (got == want).all():
                raise EncodeError(f"internal check failed: entry {i} ({names[i]}) does not decode to the planned "
                                  f"picture after writing (please report this)")
            if c in ctarget:
                role = "edited" if i in tg else "ride-along"
            elif c in joint_changed:
                role = "joint-changed"
            else:
                role = "ride-along"
            if changed_px.any() or i in tg:
                expected[i] = want.tobytes()
                roles[i] = role
        elif changed_px.any():
            culprits = [members[k][0] for k, wb in written_by.items() if np.isin(wb, inf.reads).any()]
            if culprits:
                k = culprits[0]
                raise EncodeError(f"the edit of entry {k} ({names[k]}) would also change entry {i} ({names[i]}), "
                                  f"which shares stored bytes with it (overlapping footprints); edit both to match "
                                  f"or leave this one alone")
            raise EncodeError(f"internal check failed: entry {i} ({names[i]}) changed although nothing it reads "
                              f"was edited (please report this)")
        if changed_px.any():
            src = ctarget.get(c)
            ps = psnr(src[changed_px], got[changed_px]) if src is not None else math.inf
            rows.append({"block_entry": i, "name": names[i], "role": roles.get(i, ""),
                         "palette": actions.get(c, "joint" if c in joint_changed else ""),
                         "slots_changed": slots_changed.get(c, 0), "pixels_changed": int(changed_px.sum()),
                         "psnr": ps, "bytes_changed": int(np.isin(inf.reads, diff, assume_unique=True).sum())})
    return BlockResult(new_block, expected, roles, rows, warnings)


# ------------------------------------------------------------------ self-test

def _pal_block(cw, pictures, palettes, extra_entries=()):
    """A one-bank block: ``palettes`` = {(palx, paly): list of 256 or 16
    logical (r,g,b,a_raw)}, ``pictures`` = list of entry dicts with an
    ``_idx`` (h*w index list) placed at (x, y) in their format's space."""
    ch = 64
    canvas = bytearray(cw * ch * 4)
    pos = txy.csm1_positions()
    for (px, py), cols in palettes.items():
        for i, c in enumerate(cols):
            if len(cols) == 256:
                s = pos[i]
                o = ((py + s // 16) * cw + px + s % 16) * 4
            else:
                o = ((py + i // 8) * cw + px + i % 8) * 4
            canvas[o:o + 4] = bytes(c)
    ents = []
    for e in pictures:
        e = dict(e)
        idx = e.pop("_idx", None)
        if idx is not None:
            for k, v in enumerate(idx):
                x, y = e["x"] + k % e["w"], e["y"] + k // e["w"]
                if e["fmt"] == txy.PSMT8:
                    canvas[txy.swizzle8_offset(cw, x, y)] = v
                else:
                    o, sh = txy.swizzle4_offset(cw, x, y)
                    canvas[o] = (canvas[o] & (0xF0 >> sh)) | (v << sh)
        e.setdefault("cbp", 1)
        ents.append(e)
    ents += list(extra_entries)
    strips = [(0, cw, 32, bytes(canvas[:cw * 32 * 4])), (cw // 64 * 32, cw, 32, bytes(canvas[cw * 32 * 4:]))]
    return txy.build_block(cw, [strips], ents)


def _decoded(block):
    return {e["i"]: r for e, _w, _h, r in txy.decode(block)}


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    # 1. unchanged targets are byte-identical no-ops (forced through the edit path)
    for label, blk in (("two-bank PSMT8", txy._build_synthetic()[0]),
                       ("PSMT4 + true colour (raw alpha up to 0x90)", txy._build_synthetic4()[0])):
        d = _decoded(blk)
        r = encode_block(blk, d, force=True)
        check(r.block == blk and not r.rows, f"unchanged targets give a byte-identical block ({label})")
    # a palette that repeats colours, and pixels using both copies; alpha-0 slots with RGB
    cols = [(i, (i * 3) & 255, 200 - i % 200, 0x80) for i in range(256)]
    cols[7] = cols[3]
    cols[9] = (250, 10, 10, 0)
    cols[10] = (10, 250, 10, 0)
    cols[11] = (12, 34, 56, 0x90)
    idx_a = [(x * 5 + y) % 16 for y in range(16) for x in range(16)]
    blk = _pal_block(64, [{"name": "a", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 48,
                           "paly": 0, "_idx": idx_a}], {(48, 0): cols})
    d = _decoded(blk)
    r = encode_block(blk, d, force=True)
    check(r.block == blk, "a palette with a repeated colour that pixels use both copies of stays byte-identical")
    zeroed = bytearray(d[0])
    for k in range(0, len(zeroed), 4):
        if zeroed[k + 3] == 0:
            zeroed[k:k + 3] = b"\x00\x00\x00"
    r = encode_block(blk, {0: bytes(zeroed)})
    check(r.block == blk and not r.rows, "a target with RGB zeroed under alpha 0 is a no-op")
    # edit some pixels of slot 1 to a new colour; slot 11 (raw 0x90) stays used and unchanged
    tgt = bytearray(d[0])
    for k in range(256):
        if idx_a[k] == 1:
            tgt[k * 4:k * 4 + 4] = bytes((1, 2, 3, 255))
    r = encode_block(blk, {0: bytes(tgt)})
    s = txy.csm1_positions()[11]
    o11 = r.block.index(bytes((12, 34, 56, 0x90)))
    check(_decoded(r.block)[0] == bytes(tgt) and blk.count(bytes((12, 34, 56, 0x90))) == 1 and o11 > 0,
          "an exact edit lands, and a raw-alpha 0x90 slot that does not change keeps 0x90")
    del s
    # 4. 300 new colours on a single-user CLUT; another entry with its own CLUT
    grad_idx = [k % 64 for k in range(64 * 32)]
    cols2 = [(k, k, k, 0x80) for k in range(256)]
    blk = _pal_block(128, [
        {"name": "grad", "fmt": txy.PSMT8, "w": 64, "h": 32, "x": 0, "y": 0, "palx": 112, "paly": 0,
         "_idx": grad_idx},
        {"name": "other", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 128, "y": 0, "palx": 112, "paly": 16,
         "_idx": [(k * 3) % 256 for k in range(256)]}], {(112, 0): cols2, (112, 16): cols})
    d = _decoded(blk)
    g = np.zeros((32, 64, 4), np.uint8)
    yy, xx = np.mgrid[0:32, 0:64]
    g[..., 0] = (xx * 4) % 256
    g[..., 1] = (yy * 8) % 256
    g[..., 2] = ((xx + yy) * 3) % 256
    g[..., 3] = 255
    ncol = len(np.unique(g.reshape(-1, 4), axis=0))
    r = encode_block(blk, {0: g.tobytes()})
    got = np.frombuffer(_decoded(r.block)[0], np.uint8)
    ps = psnr(g.reshape(-1, 4), got.reshape(-1, 4))
    check(ncol > 256 and ps >= 35 and _decoded(r.block)[1] == d[1],
          f"an edit with {ncol} colours on a one-user palette: PSNR {ps:.1f} dB (>= 35), the other entry untouched")
    # 5/6. a shared CLUT: auto keeps the sibling, joint changes and reports it
    pal_sh = [(k, 255 - k, (k * 7) & 255, 0x80) for k in range(200)] + [(0, 0, 0, 0)] * 56
    blk = _pal_block(128, [
        {"name": "ude", "fmt": txy.PSMT8, "w": 32, "h": 32, "x": 0, "y": 0, "palx": 112, "paly": 0,
         "_idx": [k % 100 for k in range(1024)]},
        {"name": "body", "fmt": txy.PSMT8, "w": 32, "h": 32, "x": 32, "y": 0, "palx": 112, "paly": 0,
         "_idx": [100 + k % 100 for k in range(1024)]}], {(112, 0): pal_sh})
    d = _decoded(blk)
    edit = np.frombuffer(d[0], np.uint8).reshape(-1, 4).copy()
    edit[:, :3] = edit[:, [1, 2, 0]]
    edit[::3, 0] = np.arange(len(edit[::3])) % 256
    r = encode_block(blk, {0: edit.tobytes()})
    check(_decoded(r.block)[1] == d[1] and r.roles.get(0) == "edited" and 1 not in r.roles,
          "a shared palette in auto mode: the sibling's decode is identical")
    rj = encode_block(blk, {0: edit.tobytes()}, mode="joint")
    check(_decoded(rj.block)[1] != d[1] and rj.roles.get(1) == "joint-changed",
          "joint mode changes the sibling and reports it as joint-changed")
    # 7. two twins (dup) edited differently are refused
    twin = {"fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 48, "paly": 0}
    blk = _pal_block(64, [dict(twin, name="t", _idx=idx_a), dict(twin, name="t", dup=1)], {(48, 0): cols})
    d = _decoded(blk)
    e1 = bytearray(d[0])
    e1[0:4] = bytes((9, 9, 9, 255))
    e2 = bytearray(d[0])
    e2[4:8] = bytes((8, 8, 8, 255))
    try:
        encode_block(blk, {0: bytes(e1), 1: bytes(e2)})
        check(False, "two re-listed (dup) entries edited differently are refused")
    except EncodeError:
        check(True, "two re-listed (dup) entries edited differently are refused")
    r = encode_block(blk, {0: bytes(e1)})
    check(r.roles.get(1) == "ride-along" and _decoded(r.block)[1] == bytes(e1),
          "an unedited twin rides along with the edited one")
    # 8. PSMT4: entries sharing bytes (low nibbles rows 0-1, high nibbles rows 2-3)
    p16 = [(k * 16, 128, 255 - k * 16, 0x80) for k in range(16)]
    blk = _pal_block(128, [
        {"name": "lo", "fmt": txy.PSMT4, "w": 32, "h": 2, "x": 0, "y": 0, "palx": 64, "paly": 0,
         "_idx": [k % 16 for k in range(64)]},
        {"name": "hi", "fmt": txy.PSMT4, "w": 32, "h": 2, "x": 0, "y": 2, "palx": 64, "paly": 0,
         "_idx": [(k * 5) % 16 for k in range(64)]}], {(64, 0): p16})
    d = _decoded(blk)
    _t, inf = analyse(blk)
    shared_bytes = np.intersect1d(inf[0].addr, inf[1].addr).size
    e = np.frombuffer(d[0], np.uint8).reshape(-1, 4).copy()
    e[:] = e[::-1]
    r = encode_block(blk, {0: e.tobytes()}, mode="keep")
    check(shared_bytes > 0 and _decoded(r.block)[1] == d[1] and _decoded(r.block)[0] == e.tobytes(),
          f"PSMT4 nibble writes leave the other nibble of {shared_bytes} shared bytes intact")
    # 9. an exact-fit recolour (hue shift, <= 256 colours, one user) is lossless
    blk = _pal_block(64, [{"name": "h", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 48,
                           "paly": 0, "_idx": list(range(256))}],
                     {(48, 0): [(k, (k * 2) & 255, 255 - k, 0x80) for k in range(256)]})
    d = _decoded(blk)
    e = np.frombuffer(d[0], np.uint8).reshape(-1, 4).copy()
    e[:, :3] = e[:, [2, 0, 1]]
    r = encode_block(blk, {0: e.tobytes()})
    check(_decoded(r.block)[0] == e.tobytes() and r.rows and r.rows[0]["psnr"] == math.inf,
          "an exact-fit recolour of a 256-colour picture on its own palette is lossless")
    # true colour: RGB-only edit keeps raw alpha above 0x80, an alpha edit writes the snapped value
    blk4, _ = txy._build_synthetic4()
    d = _decoded(blk4)
    e = np.frombuffer(d[1], np.uint8).reshape(-1, 4).copy()
    vis = e[:, 3] > 0                     # colour under alpha 0 is not an edit (alpha-0 rule)
    e[vis, 0] = 255 - e[vis, 0]
    e[5, 3] = 100
    r = encode_block(blk4, {1: e.tobytes()})
    _t, inf = analyse(r.block)
    raw = np.frombuffer(r.block, np.uint8)[inf[1].addr + 3]
    raw0 = np.frombuffer(blk4, np.uint8)[inf[1].addr + 3]
    keepm = np.ones(len(raw), bool)
    keepm[5] = False
    check((raw[keepm] == raw0[keepm]).all() and raw[5] == 50 and _decoded(r.block)[1] == e.tobytes()
          and _decoded(r.block)[0] == d[0], "true colour: RGB edits keep raw alpha (0x90 too); alpha 100 is stored as 50")
    # bogus entries are refused
    blk = _pal_block(64, [{"name": "bad", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 48,
                           "paly": 0, "cbp": 0xFFFFFFFF, "_idx": idx_a}], {(48, 0): cols})
    try:
        encode_block(blk, {0: bytes(len(_decoded(blk)[0]))})
        check(False, "an entry whose CLUT pointer is 0xFFFFFFFF is refused")
    except EncodeError:
        check(True, "an entry whose CLUT pointer is 0xFFFFFFFF is refused")
    # quantiser: deterministic, fixed centres kept out
    cols_q = np.array([[x, y, 0, 255] for x in range(0, 256, 8) for y in range(0, 256, 8)])
    a = quantise(cols_q, np.ones(len(cols_q)), 40, cols_q[:5])
    b = quantise(cols_q, np.ones(len(cols_q)), 40, cols_q[:5])
    check(len(a) <= 40 and (a == b).all() and not np.isin(pack(a), pack(canon(cols_q[:5]))).any(),
          "the quantiser is deterministic and never returns a fixed colour as a new one")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(self_test() if sys.argv[1:] == ["--self-test"] else (print(__doc__) or 2))
