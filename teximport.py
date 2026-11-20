"""teximport.py — edited PNGs back into a copy of the disc (texture import).

Edit PNGs in a copy of the folder ``browse`` wrote and give that folder to
``texture-import``: every PNG that differs from what the disc decodes to is
written back, into a copy of the disc, by ``texenc.py``. A PNG that matches
the disc (under the alpha-0 rule: colours with alpha 0 are equal whatever RGB
they carry) is unchanged and ignored, so a sparse tree holding only the edited
PNGs works the same as the whole tree.

What it reads (paths as ``browse.py`` / ``embedded.py`` write them):

* ``textures_png/<area>/<toc path>/<file>.txy/NNN_<name>[_bBB][_dupD].png``:
  entry NNN of a standalone page. The file name must be exactly what the
  current kit would write for that entry (``txy.entry_filename``), and the
  size must match; otherwise the tree is from another revision and nothing is
  written.
* ``textures_png/_embedded/<area>/<container path>/<0xoffset>/NNN_....png``:
  entry NNN of the txy block at that offset inside an effect file.
* ``xtx/<area>/<path>.xtx.png``: a UI sheet. Sheets whose palette the kit
  has solved are written (parked-corner sheets, their frame masks, the
  portraits and item cards cut by a mask, and the true-colour title words
  ``yajima\\titleobj.xtx``); sheets drawn with a guessed palette (scan, gray)
  are refused when edited and ignored when not.
* ``models/<area>/<path>.glb`` (or .gltf): the images of a model exported by
  ``browse``/``model``, mapped back to the entries of the .txy of the same
  path, by image name or, for files exported before images were named after
  their PNG files, by the order ``pxy.to_glb`` writes them in.

The sweep. The same picture is often stored many times on the disc, under
different names (KOS-MOS's hair is ``kosmos00_hair_col`` in kosmos00.txy and
``kosmos01_hair_col`` in kosmos01, 02 and 03, each with its palette at another
place). By default every entry anywhere on the disc (standalone pages and
embedded blocks, every carrier) whose decoded picture equals an edited entry's
original picture gets the edit too, encoded in its own file's palette context.
Single-colour pictures are swept only to entries of the same name, and every
swept group is printed so mixed-name groups (one 32x32 reflection map is
stored 1,512 times under many names) are visible in a dry run. ``--sweep
name`` keeps copies to the same entry name, ``--sweep none`` edits only what
the PNGs say.

Everything is written through ``repack.patch_copy`` (a complete, read-back
copy or nothing), then every replaced object is read from the new image and
decoded again: edited, swept, ride-along and joint-changed entries must show
their planned pictures, every other entry its original one, and untouched
blocks inside a replaced file must be byte-identical.

    python teximport.py --iso DISC.iso --tree EDITED --out MODDED.iso [--dry-run]
    python teximport.py --iso DISC.iso --verify-roundtrip
    python teximport.py --self-test
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import re
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arx  # noqa: E402
import embedded  # noqa: E402
import pngread  # noqa: E402
import region  # noqa: E402
import texenc  # noqa: E402
import txy  # noqa: E402
import xtx as xtxmod  # noqa: E402
from chains import CHAINS, ChainReader, available_chains  # noqa: E402
from iso9660 import IsoImage  # noqa: E402
from toc import parse_toc  # noqa: E402

CARRIER_EXTS = {"esd", "esp", "bin"}          # recolor.CARRIER_EXTS: files that hold txy blocks
XTX_OK = ("corner", "mask", "corner+mask", "ct32")
ROLES = ("edited", "swept", "ride-along", "joint-changed", "mip")


class ImportError_(ValueError):
    pass


# ======================================================================= disc

class Disc:
    """Every TOC object of an image, readable by (chain, path), ARX objects
    decompressed (what ``repack.read_entry`` returns, without re-parsing the
    TOC for every object)."""

    def __init__(self, iso_path, check: bool = True):
        self.path = str(iso_path)
        self.iso = IsoImage(iso_path)
        if check:
            region.require_known(self.iso, "texture-import")
        self.by_path: Dict[Tuple[int, str], tuple] = {}
        self.order: List[Tuple[int, object]] = []
        for chain in available_chains(self.iso):
            r = ChainReader(self.iso, chain)
            for e in parse_toc(bytes(r.toc_bytes()), CHAINS[chain][0]):
                self.by_path[(chain, e.path.lower())] = (r, e)
                self.order.append((chain, e))

    def entry(self, chain: int, path: str):
        hit = self.by_path.get((chain, path.lower()))
        return hit[1] if hit else None

    def read(self, chain: int, path: str) -> bytes:
        r, e = self.by_path[(chain, path.lower())]
        raw = r.read(e.byte_offset, e.size)
        return arx.decompress(raw) if e.compressed else raw

    def close(self):
        self.iso.close()


def blocks_of(path: str, data: bytes) -> List[Tuple[int, bytes]]:
    """The txy blocks of an object, as browse and recolor find them: a .txy
    file is one block (0 banks = placeholder), anything else is searched with
    ``embedded.iter_blocks``."""
    if path.lower().endswith(".txy"):
        return [(0, data)] if data[:4] == b"txy\x00" else []
    return list(embedded.iter_blocks(data))


def carrier_objects(disc: Disc) -> List[Tuple[int, str]]:
    return [(c, e.path) for c, e in disc.order if e.ext == "txy" or e.ext in CARRIER_EXTS]


# ======================================================================= tree

@dataclass
class Ref:
    kind: str                 # txy, emb, xtx, glb
    chain: int
    path: str                 # TOC path (backslashes), as spelt in the tree
    block_off: int
    entry: int
    fname: str
    file: Path


def resolve_roots(tree=None, textures=None, xtx=None, models=None) -> Dict[str, Optional[Path]]:
    roots: Dict[str, Optional[Path]] = {"textures": None, "xtx": None, "models": None}
    if tree:
        t = Path(tree)
        if not t.is_dir():
            raise ImportError_(f"--tree {t} is not a folder")
        if (t / "chain0").is_dir() or (t / "_embedded").is_dir():
            roots["textures"] = t
        else:
            for k, sub in (("textures", "textures_png"), ("xtx", "xtx"), ("models", "models")):
                if (t / sub).is_dir():
                    roots[k] = t / sub
            if not any(roots.values()):
                raise ImportError_(f"--tree {t} holds no textures_png/, xtx/ or models/ folder "
                                   f"(nor chain0/ or _embedded/ for a textures_png folder itself)")
    for k, v in (("textures", textures), ("xtx", xtx), ("models", models)):
        if v:
            if not Path(v).is_dir():
                raise ImportError_(f"--{k} {v} is not a folder")
            roots[k] = Path(v)
    return roots


_NNN = re.compile(r"^(\d{3,})_.+\.png$")
_OFF = re.compile(r"^0x[0-9a-fA-F]+$")


def _chain_of(area: str) -> Optional[int]:
    m = re.fullmatch(r"chain(\d)", area)
    return int(m.group(1)) if m else None


def parse_texture_path(rel: Tuple[str, ...]) -> Tuple[Optional[Ref], str]:
    """(Ref, "") or (None, problem) for a PNG path relative to textures_png."""
    if not rel[-1].lower().endswith(".png"):
        return None, ""
    m = _NNN.match(rel[-1])
    if rel[0] == "_embedded":
        if len(rel) < 5:
            return None, "not an _embedded/<area>/<container>/<0xoffset>/NNN_name.png path"
        chain = _chain_of(rel[1])
        if chain is None or not _OFF.match(rel[-2]) or not m:
            return None, "not an _embedded/<area>/<container>/<0xoffset>/NNN_name.png path"
        return Ref("emb", chain, "\\".join(rel[2:-2]), int(rel[-2], 16), int(m.group(1)), rel[-1], Path()), ""
    if len(rel) < 3:
        return None, "not a <area>/<path>/<file>.txy/NNN_name.png path"
    chain = _chain_of(rel[0])
    if chain is None or not rel[-2].lower().endswith(".txy") or not m:
        return None, "not a <area>/<path>/<file>.txy/NNN_name.png path"
    return Ref("txy", chain, "\\".join(rel[1:-1]), 0, int(m.group(1)), rel[-1], Path()), ""


def parse_xtx_path(rel: Tuple[str, ...]) -> Tuple[Optional[Ref], str]:
    if not rel[-1].lower().endswith(".png"):
        return None, ""
    chain = _chain_of(rel[0]) if len(rel) >= 2 else None
    if chain is None or not rel[-1].lower().endswith(".xtx.png"):
        return None, "not a <area>/<path>.xtx.png path"
    return Ref("xtx", chain, "\\".join(rel[1:])[:-4], 0, -1, rel[-1], Path()), ""


def parse_model_path(rel: Tuple[str, ...]) -> Tuple[Optional[Ref], str]:
    low = rel[-1].lower()
    if not (low.endswith(".glb") or low.endswith(".gltf")):
        return None, ""
    chain = _chain_of(rel[0]) if len(rel) >= 2 else None
    if chain is None:
        return None, "not a <area>/<path>.glb path"
    stem = "\\".join(rel[1:]).rsplit(".", 1)[0]
    return Ref("glb", chain, stem + ".txy", 0, -1, rel[-1], Path()), ""


def scan_tree(roots) -> Tuple[List[Ref], List[str]]:
    refs, problems = [], []
    for kind, parse in (("textures", parse_texture_path), ("xtx", parse_xtx_path), ("models", parse_model_path)):
        root = roots.get(kind)
        if root is None:
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for f in sorted(files):
                if f.startswith("."):
                    continue
                p = Path(dirpath) / f
                rel = p.relative_to(root).parts
                ref, why = parse(rel)
                if ref is None:
                    if why:
                        problems.append(f"{p}: {why}")
                    continue
                ref.file = p
                refs.append(ref)
    return refs, problems


# ================================================================ edit search

@dataclass
class Edit:
    chain: int
    path: str                 # TOC path as the disc spells it
    block_off: int
    entry: int
    name: str
    w: int
    h: int
    fmt: int
    mip: int
    target: bytes
    orig: bytes
    source: str
    role: str = "edited"
    twin: tuple = ()          # (fmt, x, y, w, h, bank, palx, paly): what the dup flag re-lists


def _twin(e: dict) -> tuple:
    return (e["fmt"], e["x"], e["y"], e["w"], e["h"], e["bank"], e["palx"], e["paly"])


def _png_target(file: Path, w: int, h: int) -> Tuple[Optional[bytes], str]:
    try:
        pw, ph, rgba = pngread.read_png(file.read_bytes())
    except (pngread.PngError, OSError) as e:
        return None, f"{file}: {e}"
    if (pw, ph) != (w, h):
        return None, f"{file}: the PNG is {pw}x{ph}, the texture on the disc is {w}x{h}"
    return rgba, ""


def _txy_job(args):
    """Phase 1 for one object: which of the tree's PNGs differ from the disc."""
    chain, path, refs = args
    disc = _WORKER["disc"]
    out = {"edits": [], "problems": [], "unchanged": 0}
    e = disc.entry(chain, path)
    if e is None:
        out["problems"] += [f"{r.file}: no object {path!r} in chain {chain} on this disc" for r in refs]
        return out
    data = disc.read(chain, e.path)
    blocks = dict(blocks_of(e.path, data))
    decoded: Dict[int, tuple] = {}
    for r in refs:
        if r.block_off not in blocks:
            out["problems"].append(f"{r.file}: {e.path} has no txy block at {r.block_off:#x}")
            continue
        if r.block_off not in decoded:
            t = txy.parse(blocks[r.block_off])
            dec = {en["i"]: (en, rgba) for en, _w, _h, rgba in txy.decode_fast(blocks[r.block_off])}
            decoded[r.block_off] = (t, dec)
        t, dec = decoded[r.block_off]
        multi = len(t["banks"]) > 1
        ent = next((x for x in t["entries"] if x["i"] == r.entry), None)
        if ent is None:
            out["problems"].append(f"{r.file}: {e.path} has no entry {r.entry}")
            continue
        want = txy.entry_filename(ent, multi)
        if want != r.fname:
            out["problems"].append(f"{r.file}: entry {r.entry} of {e.path} would be named {want}")
            continue
        if r.entry not in dec:
            out["problems"].append(f"{r.file}: entry {r.entry} of {e.path} is a placeholder (nothing to edit)")
            continue
        en, orig = dec[r.entry]
        target, why = _png_target(r.file, en["w"], en["h"])
        if target is None:
            out["problems"].append(why)
            continue
        if texenc.same(np.frombuffer(target, np.uint8), np.frombuffer(orig, np.uint8)).all():
            out["unchanged"] += 1
            continue
        bogus = en["bank"] >= len(t["banks"]) or (en["fmt"] in (txy.PSMT8, txy.PSMT4) and en["cbp"] == 0xFFFFFFFF)
        if bogus:
            out["problems"].append(f"{r.file}: entry {r.entry} ({en['name']}) of {e.path} is refused: its bank or "
                                   f"CLUT pointer is 0xFFFFFFFF (FORMATS.md, the bogus E2_ELS01 entries)")
            continue
        out["edits"].append(Edit(chain, e.path, r.block_off, r.entry, en["name"], en["w"], en["h"], en["fmt"],
                                 en.get("mip", 0), target, orig, str(r.file), "edited", _twin(en)))
    return out


# ----------------------------------------------------------------- xtx sheets

def _xtx_subs(data: bytes):
    _total, count, hdr = struct.unpack_from("<III", data, 4)
    subs = []
    for i in range(count):
        b = hdr + 20 * i
        w, bufw, h = struct.unpack_from("<HHH", data, b)
        gs_off, _size, addr = struct.unpack_from("<III", data, b + 8)
        subs.append((w, bufw, h, gs_off, addr))
    return subs


def _xtx_placed(data: bytes):
    """[(w, h, x0, y0, addr)] in placement order, by xtx._compose_ct32's rule
    (a sub that does not fit the canvas is skipped)."""
    subs = _xtx_subs(data)
    clen = {4: 256, 8: 512}.get(subs[0][1] or 8)
    out = []
    for w, _b, h, gs_off, addr in subs:
        y0 = (gs_off // (clen * 32)) * 32
        x0 = (gs_off % (clen * 32)) // 32
        if x0 + w > clen or y0 + h > clen:
            continue
        out.append((w, h, x0, y0, addr))
    return clen, out


def mask_for(disc: Disc, chain: int, path: str) -> Optional[str]:
    """The frame mask browse cuts this sheet with: the last ``*_alpha.xtx``
    in the same directory, in TOC order (browse.browse_xtx's rule)."""
    if path.lower().endswith("_alpha.xtx"):
        return None
    d = path.rpartition("\\")[0].lower()
    found = None
    for c, e in disc.order:
        if c == chain and e.path.lower().endswith(".xtx"):
            ed, _, name = e.path.rpartition("\\")
            if ed.lower() == d and name.lower().endswith("_alpha.xtx"):
                found = e.path
    return found


def xtx_view(data: bytes, mask: Optional[bytes]) -> Tuple[Optional[tuple], str]:
    """(browse's decode of a sheet, kind): kind is corner, mask, corner+mask,
    ct32, or the decoder's palette source for sheets the kit cannot write."""
    r = xtxmod.decode(data)
    if r is None:
        return None, "undecodable"
    kind = r[3]
    if kind == "corner" and mask is not None:
        m = xtxmod.decode(mask)
        if m is not None and m[3] == "mask":
            cut = xtxmod.apply_mask(r, m)
            if cut is not None:
                return cut, "corner+mask"
    return r, kind


def _corner_layout(data: bytes):
    """File offsets behind a parked-corner sheet: per pixel of the decoded
    picture (index byte), per logical palette slot (word), plus the bytes of
    the art area the picture does not reach."""
    raw = arx.decompress(data) if data[:4] == b"ARX\x00" else data
    comp = xtxmod._compose_ct32(raw)
    canvas, clen, placed = comp
    clen2, subs = _xtx_placed(raw)
    if len(subs) != 1 or clen2 != clen:
        raise ImportError_("sheet layout is not a single parked-corner image")
    w, h, x0, y0, addr = subs[0]
    W, H, rgba, _src = xtxmod._decode_parked(canvas, clen, w, h)
    ys, xs = np.mgrid[0:H, 0:W]
    o = txy.swz8_np(clen, xs.reshape(-1), ys.reshape(-1))
    p = o // 4
    cx, cy = p % clen, p // clen
    inside = (cx < w) & (cy < h)
    in_pal = (cx >= w - 16) & (cy < 16) & inside
    if in_pal.any():
        raise ImportError_("a picture pixel's index lies inside the sheet's palette tile")
    # rows past an odd-height image (iwase\img\door\door_alpha.xtx is 33 rows) read the
    # empty canvas: index 0, not stored, so they cannot change
    pix = np.where(inside, addr + 32 + (cy * w + cx) * 4 + o % 4, -1)
    pos = np.array(txy.csm1_positions(), np.int64)
    pal_words = addr + 32 + ((pos // 16) * w + (w - 16) + pos % 16) * 4
    art = np.zeros((h, w), bool)
    art[:, :w - 16] = True
    art_bytes = (addr + 32 + (np.nonzero(art.reshape(-1))[0][:, None] * 4 + np.arange(4))).reshape(-1)
    rest = np.setdiff1d(art_bytes, pix[pix >= 0])
    return raw, W, H, rgba, pix, pal_words, rest


def encode_corner(data: bytes, target: bytes, mode: str = "auto", dither: str = "none", keep=None):
    """(new sheet bytes, expected full picture, solution) for a parked-corner
    sheet: the picture is the one user of its palette that can change; the art
    bytes the picture does not show, and the pixels in ``keep`` (hidden by a
    frame mask), keep their colours like an unedited sibling would."""
    raw, W, H, orig, pix, pal_words, rest = _corner_layout(data)
    arr = np.frombuffer(raw, np.uint8)
    stored = pix >= 0
    idx = np.zeros(len(pix), np.int64)
    idx[stored] = arr[pix[stored]]
    words = arr[(pal_words[:, None] + np.arange(4)).reshape(-1)].reshape(-1, 4)
    pal = words.astype(np.int32).copy()
    pal[:, 3] = texenc.a_dec(pal[:, 3])
    origa = np.frombuffer(orig, np.uint8).reshape(-1, 4)
    if not (pal[idx] == origa).all():
        raise ImportError_("internal: the sheet's palette does not reproduce its decode")
    tg = np.frombuffer(target, np.uint8).reshape(-1, 4)
    keep = np.zeros(len(idx), bool) if keep is None else np.asarray(keep, bool).reshape(-1).copy()
    keep |= ~stored
    ed = ~keep
    users = [texenc.User(idx[ed], origa[ed], tg[ed], None, None, "sheet")]
    if keep.any():
        users.append(texenc.User(idx[keep], origa[keep], None, None, None, "pixels the frame mask hides"))
    if dither == "fs" and not keep.any():
        users[0].shape = (W, H)
    if len(rest):
        ri = arr[rest].astype(np.int64)
        users.append(texenc.User(ri, pal[ri].astype(np.uint8), None, None, None, "unshown art bytes"))
    sol = texenc.solve_clut_group(pal.astype(np.uint8), users, "auto" if mode == "joint" else mode, dither)
    buf = arr.copy()
    for s in sol.changed_slots:
        r, g, b, A = (int(v) for v in sol.pal[s])
        buf[pal_words[s]:pal_words[s] + 4] = (r, g, b, int(texenc.a_raw(A)))
    new_idx = idx.copy()
    new_idx[ed] = sol.idx[0]
    buf[pix[stored]] = new_idx[stored]
    exp = origa.copy()
    exp[ed] = sol.expected[0]
    if keep.any():
        exp[keep] = sol.expected[1]
    return buf.tobytes(), exp.tobytes(), sol


def unmask_target(sheet: bytes, mask: bytes, png: bytes) -> Tuple[bytes, int]:
    """A corner+mask PNG back to a full-sheet target: inside the mask's crop,
    pixels where the mask is opaque take the PNG's colour, the rest keep the
    original. Returns (full target, number of changed pixels ignored because
    the mask hides them, full-sheet mask of the pixels the PNG cannot reach)."""
    full = xtxmod.decode(sheet)
    m = xtxmod.decode(mask)
    W, H, rgba = full[0], full[1], full[2]
    MW, MH, mrgba = m[0], m[1], m[2]
    ma = np.frombuffer(mrgba, np.uint8).reshape(MH, MW, 4)[:, :, 3]
    if not np.isin(ma, (0, 255)).all():
        raise ImportError_("the frame mask has partial alpha; the kit cannot invert it")
    cols = np.nonzero(ma.any(0))[0]
    rows = np.nonzero(ma.any(1))[0]
    x0, x1, y0, y1 = cols[0], cols[-1] + 1, rows[0], rows[-1] + 1
    OW, OH = x1 - x0, y1 - y0
    masked = np.frombuffer(xtxmod.apply_mask(full, m)[2], np.uint8).reshape(-1, 4)
    pngp = np.frombuffer(png, np.uint8).reshape(-1, 4)
    if len(pngp) != OW * OH:
        raise ImportError_(f"the PNG has {len(pngp)} pixels, the cut sheet {OW}x{OH}")
    changed = ~texenc.same(pngp, masked)
    tgt = np.frombuffer(rgba, np.uint8).reshape(H, W, 4).copy()
    sub = ma[y0:y1, x0:x1].reshape(-1)
    take = changed & (sub == 255)
    yy, xx = np.divmod(np.nonzero(take)[0], OW)
    tgt[yy + y0, xx + x0] = pngp[take]
    keep = np.ones((H, W), bool)
    keep[y0:y1, x0:x1] = (sub != 255).reshape(OH, OW)
    return tgt.tobytes(), int((changed & (sub == 0)).sum()), keep


def encode_ct32(data: bytes, target: bytes):
    """(new sheet bytes, expected picture, warnings) for a true-colour sheet.
    A changed pixel whose PNG alpha equals the shown original keeps its stored
    alpha byte (this sheet stores full 8-bit alpha; how the engine reads values
    above 0x80 is unverified); otherwise alpha is stored as min(0x80, (A+1)>>1)."""
    raw = arx.decompress(data) if data[:4] == b"ARX\x00" else data
    r = xtxmod.decode(raw)
    W, H, orig = r[0], r[1], r[2]
    clen, placed = _xtx_placed(raw)
    own = np.full((H, W), -1, np.int64)
    for w, h, x0, y0, addr in placed:              # later subs win, as _compose_ct32 paints them
        ys, xs = np.mgrid[0:h, 0:w]
        sel = (y0 + ys < H) & (x0 + xs < W)
        own[(y0 + ys)[sel], (x0 + xs)[sel]] = addr + 32 + (ys[sel] * w + xs[sel]) * 4
    own = own.reshape(-1)
    tg = np.frombuffer(target, np.uint8).reshape(-1, 4)
    og = np.frombuffer(orig, np.uint8).reshape(-1, 4)
    arr = np.frombuffer(raw, np.uint8)
    buf = arr.copy()
    st = own >= 0
    warns = []
    changed = ~texenc.same(tg, og)
    lost = int((changed & ~st).sum())
    if lost:
        warns.append(f"{lost} changed pixels lie where no sub-image is stored; left as they were")
    ch = changed & st
    words = arr[(own[st][:, None] + np.arange(4)).reshape(-1)].reshape(-1, 4).astype(np.int64)
    new = words.copy()
    chs = ch[st]
    new[chs, :3] = tg[ch, :3]
    sa = texenc.canon(tg[ch])[:, 3]
    keep_a = sa == texenc.canon(og[ch])[:, 3]
    na = new[chs, 3]
    na[~keep_a] = texenc.a_raw(tg[ch][~keep_a, 3])
    new[chs, 3] = na
    if (~keep_a).any():
        warns.append(f"{int((~keep_a).sum())} pixels have new alpha, stored as min(0x80, (A+1)>>1); this sheet "
                     f"otherwise stores 8-bit alpha (how the engine reads it is unverified)")
    buf[(own[st][:, None] + np.arange(4)).reshape(-1)] = new.reshape(-1).astype(np.uint8)
    exp = og.astype(np.int32).copy()
    e_st = exp[st]
    e_st[chs, :3] = new[chs, :3]
    e_st[chs, 3] = texenc.a_dec(new[chs, 3])
    exp[st] = e_st
    return buf.tobytes(), exp.astype(np.uint8).tobytes(), warns


def _xtx_job(args):
    """Phase 1 + encode for one sheet: (path, payload or None, report row, problems)."""
    chain, path, file, mode, dither, force_target = args
    disc = _WORKER["disc"]
    out = {"problems": [], "warnings": [], "replace": None, "row": None, "unchanged": 0, "kind": ""}
    e = disc.entry(chain, path)
    if e is None:
        out["problems"].append(f"{file}: no sheet {path!r} in chain {chain} on this disc")
        return out
    data = disc.read(chain, e.path)
    mpath = mask_for(disc, chain, e.path)
    mask = disc.read(chain, mpath) if mpath else None
    view, kind = xtx_view(data, mask)
    out["kind"] = kind
    if view is None:
        out["problems"].append(f"{file}: the kit cannot decode {e.path}")
        return out
    W, H, shown = view[0], view[1], view[2]
    if force_target:
        png = shown
    else:
        png, why = _png_target(Path(file), W, H)
        if png is None:
            out["problems"].append(why)
            return out
        if texenc.same(np.frombuffer(png, np.uint8), np.frombuffer(shown, np.uint8)).all():
            out["unchanged"] = 1
            return out
    if kind not in XTX_OK:
        if force_target:
            return out
        out["problems"].append(f"{file}: {e.path} is refused: its palette is {kind}, not solved "
                               f"(FORMATS.md, UI sheet palettes)")
        return out
    try:
        if kind == "ct32":
            new, exp, warns = encode_ct32(data, png)
            out["warnings"] += [f"{e.path}: {w}" for w in warns]
            action = "true colour"
        else:
            target, keep = png, None
            if kind == "corner+mask":
                target, ignored, keep = unmask_target(data, mask, png)
                if ignored:
                    out["warnings"].append(f"{e.path}: {ignored} changed pixels lie where the frame mask hides "
                                           f"them; ignored")
            new, exp, sol = encode_corner(data, target, mode, dither, keep)
            out["warnings"] += [f"{e.path}: {w}" for w in sol.warnings]
            action = sol.action
        check, kind2 = xtx_view(new, mask)
        want = exp
        if kind == "corner+mask":
            full = xtxmod.decode(new)
            want = xtxmod.apply_mask((full[0], full[1], exp, "corner"), xtxmod.decode(mask))[2]
        slots = 0 if kind == "ct32" else len(sol.changed_slots)
        if check is None or kind2 != kind or check[2] != want:
            raise ImportError_("internal check failed: the rebuilt sheet does not decode to the plan (please report)")
        raw = arx.decompress(data) if data[:4] == b"ARX\x00" else data
        if new != raw:
            got = np.frombuffer(check[2], np.uint8).reshape(-1, 4)
            pc = ~(got == np.frombuffer(shown, np.uint8).reshape(-1, 4)).all(1)
            out["replace"] = ((chain, e.path), new)
            out["row"] = {"chain": chain, "path": e.path, "block_off": "", "entry": "", "name": e.path.rsplit("\\", 1)[-1],
                          "role": "edited", "source": str(file), "palette": action,
                          "slots_changed": slots,
                          "pixels_changed": int(pc.sum()),
                          "psnr": texenc.psnr(np.frombuffer(png, np.uint8).reshape(-1, 4)[pc], got[pc]),
                          "bytes_changed": int((np.frombuffer(new, np.uint8) != np.frombuffer(raw, np.uint8)).sum()),
                          "_before": (W, H, shown), "_after": (W, H, check[2]), "_kind": kind}
        out["identical"] = new == raw
    except (ImportError_, texenc.EncodeError) as err:
        out["problems"].append(f"{file}: {e.path}: {err}")
    return out


# ------------------------------------------------------------------ glb files

def read_gltf_images(file: Path) -> List[Tuple[Optional[str], bytes, str]]:
    """[(image name, encoded bytes, mime)] of a .glb or .gltf."""
    import base64
    import json

    import gltf
    data = file.read_bytes()
    if data[:4] == b"glTF":
        js, binc = gltf.read_glb(data)
        bufs = {0: binc}
    else:
        js = json.loads(data.decode("utf-8"))
        bufs = {}
        for k, b in enumerate(js.get("buffers", [])):
            uri = b.get("uri", "")
            if uri.startswith("data:"):
                bufs[k] = base64.b64decode(uri.split(",", 1)[1])
            elif uri:
                bufs[k] = (file.parent / uri).read_bytes()
    out = []
    for img in js.get("images", []):
        mime = img.get("mimeType", "")
        if "bufferView" in img:
            v = js["bufferViews"][img["bufferView"]]
            b = bufs[v.get("buffer", 0)]
            blob = bytes(b[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]])
        else:
            uri = img.get("uri", "")
            if uri.startswith("data:"):
                mime = mime or uri[5:].split(";", 1)[0]
                blob = base64.b64decode(uri.split(",", 1)[1])
            else:
                blob = (file.parent / uri).read_bytes()
                mime = mime or ("image/png" if uri.lower().endswith(".png") else "")
        out.append((img.get("name"), blob, mime or ("image/png" if blob[:8] == pngread.SIGNATURE else "")))
    return out


def map_glb_images(names: List[Optional[str]], t: dict, decoded: Dict[int, tuple], used: Optional[set]):
    """Image k -> entry index, by (1) the PNG-file stem the export names images
    after, (2) an entry name unique among the used entries, (3) pxy.to_glb's
    order (image k = k-th of sorted(used and decoded)), accepted only when the
    counts match. Returns ({k: entry}, [unmapped k])."""
    multi = len(t["banks"]) > 1
    by_stem = {Path(txy.entry_filename(e, multi)).stem: e["i"] for e in t["entries"] if e["i"] in decoded}
    mapping, left = {}, []
    uniq = {}
    if used is not None:
        cnt: Dict[str, List[int]] = {}
        for i in sorted(used & set(decoded)):
            cnt.setdefault(t["entries"][i]["name"], []).append(i)
        uniq = {n: v[0] for n, v in cnt.items() if len(v) == 1}
    for k, nm in enumerate(names):
        if nm in by_stem:
            mapping[k] = by_stem[nm]
        elif nm in uniq:
            mapping[k] = uniq[nm]
        else:
            left.append(k)
    if left and used is not None:
        order = sorted(used & set(decoded))
        if len(order) == len(names):
            for k in list(left):
                mapping[k] = order[k]
            left = []
    return mapping, left


def _glb_job(args):
    chain, txy_path, file = args
    disc = _WORKER["disc"]
    out = {"edits": [], "problems": [], "unchanged": 0}
    try:
        images = read_gltf_images(Path(file))
    except Exception as err:                         # a damaged file is a problem, not a crash
        out["problems"].append(f"{file}: cannot read ({type(err).__name__}: {err})")
        return out
    if not images:                                   # exported untextured: nothing to bring back
        return out
    e = disc.entry(chain, txy_path)
    if e is None:
        out["problems"].append(f"{file}: no texture page {txy_path!r} beside this model on the disc")
        return out
    data = disc.read(chain, e.path)
    try:
        t = txy.parse(data)
    except txy.TxyError as err:
        out["problems"].append(f"{file}: {e.path}: {err}")
        return out
    decoded = {en["i"]: (en, rgba) for en, _w, _h, rgba in txy.decode_fast(data)}
    names = [nm for nm, _b, _m in images]
    multi = len(t["banks"]) > 1
    stems = {Path(txy.entry_filename(x, multi)).stem for x in t["entries"]}
    used = None
    if not all(nm in stems for nm in names):
        pe = disc.entry(chain, txy_path[:-4] + ".pxy")
        if pe is not None:
            import pxy
            try:
                p = pxy.parse(disc.read(chain, pe.path))
                pxy.link_textures(p, t["entries"])
                used = {m.txy_entry["i"] for d in p.draws for m in d.materials if m.txy_entry is not None}
            except Exception as err:
                out["problems"].append(f"{file}: the model {pe.path} does not parse ({err})")
                return out
    mapping, left = map_glb_images(names, t, decoded, used)
    for k in left:
        out["problems"].append(f"{file}: image {k} ({names[k]!r}) matches no entry of {e.path}")
    for k, i in sorted(mapping.items()):
        nm, blob, mime = images[k]
        en, orig = decoded[i]
        if mime != "image/png":
            out["problems"].append(f"{file}: image {k} ({nm!r}) is {mime or 'not a PNG'}; export PNG images")
            continue
        try:
            pw, ph, rgba = pngread.read_png(blob)
        except pngread.PngError as err:
            out["problems"].append(f"{file}: image {k} ({nm!r}): {err}")
            continue
        if (pw, ph) != (en["w"], en["h"]):
            out["problems"].append(f"{file}: image {k} ({nm!r}) is {pw}x{ph}, entry {i} is {en['w']}x{en['h']}")
            continue
        if texenc.same(np.frombuffer(rgba, np.uint8), np.frombuffer(orig, np.uint8)).all():
            out["unchanged"] += 1
            continue
        if en["bank"] >= len(t["banks"]) or (en["fmt"] in (txy.PSMT8, txy.PSMT4) and en["cbp"] == 0xFFFFFFFF):
            out["problems"].append(f"{file}: image {k} maps to entry {i} ({en['name']}), refused (bogus entry)")
            continue
        out["edits"].append(Edit(chain, e.path, 0, i, en["name"], en["w"], en["h"], en["fmt"], en.get("mip", 0),
                                 rgba, orig, f"{file}#image{k}({nm})", "edited", _twin(en)))
    return out


# ====================================================================== sweep

def _sweep_job(args):
    """Entries of one object whose decoded picture is one of the wanted ones."""
    chain, path = args
    wanted = _WORKER["wanted"]
    disc = _WORKER["disc"]
    data = disc.read(chain, path)
    hits = []
    for off, block in blocks_of(path, data):
        try:
            t = txy.parse(block)
        except txy.TxyError:
            continue
        if not t["banks"] or not any((e["w"], e["h"]) in wanted for e in t["entries"]):
            continue
        cw, ch = t["canvas_w"], txy.canvas_height(t)
        cache = {}
        for e in t["entries"]:
            shas = wanted.get((e["w"], e["h"]))
            if not shas or e["bank"] >= len(t["banks"]) or \
                    (e["fmt"] in (txy.PSMT8, txy.PSMT4) and e["cbp"] == 0xFFFFFFFF):
                continue
            b = min(e["bank"], len(t["banks"]) - 1)
            if b not in cache:
                cache[b] = txy.canvas_np(block, txy.owner_map(t, b))
            rgba = txy.decode_entry_np(cache[b], cw, ch, e)
            if rgba is None:
                continue
            sha = hashlib.sha1(rgba).hexdigest()
            if sha in shas:
                hits.append((chain, path, off, e["i"], e["name"], sha, e["fmt"], e["w"], e["h"], e.get("mip", 0),
                             _twin(e)))
    return hits


def box_down(rgba: np.ndarray, w: int, h: int) -> Tuple[np.ndarray, int, int]:
    """One mip level down: 2x2 box filter, RGB weighted by alpha."""
    a = rgba.reshape(h, w, 4).astype(np.float64)
    nw, nh = max(1, w // 2), max(1, h // 2)
    a = a[:nh * 2 if h > 1 else 1, :nw * 2 if w > 1 else 1]
    if h > 1:
        a = a.reshape(nh, 2, a.shape[1], 4).transpose(0, 2, 1, 3).reshape(nh, a.shape[1], 2, 4)
        a = a.reshape(nh, a.shape[1], 2, 4)
    else:
        a = a.reshape(1, a.shape[1], 1, 4)
    # a: (nh, W2, ky, 4); now fold x
    if w > 1:
        a = a.reshape(nh, nw, 2, a.shape[2], 4).reshape(nh, nw, -1, 4)
    else:
        a = a.reshape(nh, 1, -1, 4)
    al = a[..., 3]
    s = al.sum(-1)
    rgb = np.where(s[..., None] > 0, (a[..., :3] * al[..., None]).sum(-2) / np.maximum(s, 1e-9)[..., None],
                   a[..., :3].mean(-2))
    out = np.concatenate([rgb, al.mean(-1)[..., None]], -1)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8).reshape(-1, 4), nw, nh


# ======================================================================= main

_WORKER: dict = {}


def _is_path(iso) -> bool:
    return isinstance(iso, (str, Path))


def _init(iso_path, wanted=None):
    _WORKER["disc"] = Disc(iso_path, check=False) if _is_path(iso_path) else iso_path
    _WORKER["wanted"] = wanted or {}


def _pool_map(fn, jobs, iso_path, wanted=None, workers=None):
    """Map over jobs in worker processes (each opens the image once)."""
    jobs = list(jobs)
    if len(jobs) <= 2 or workers == 1 or not _is_path(iso_path):
        _init(iso_path, wanted)
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count() or 4, initializer=_init,
                             initargs=(iso_path, wanted)) as ex:
        return list(ex.map(fn, jobs, chunksize=max(1, min(16, len(jobs) // 64 or 1))))


def _encode_obj_job(args):
    """Encode every block of one object that has edits: (key, new object
    bytes, per-block results)."""
    chain, path, per_block, mode, dither, force = args
    disc = _WORKER["disc"]
    data = disc.read(chain, path)
    blocks = dict(blocks_of(path, data))
    buf = bytearray(data)
    res = {}
    for off, edits in sorted(per_block.items()):
        block = blocks[off]
        bmode = mode[off] if isinstance(mode, dict) else mode
        try:
            r = texenc.encode_block(block, edits, bmode, dither, force=force)
        except texenc.EncodeError as err:
            return {"key": (chain, path), "error": f"{path} block {off:#x}: {err}"}
        buf[off:off + len(block)] = r.block
        res[off] = {"expected": r.expected, "roles": r.roles, "rows": r.rows, "warnings": r.warnings,
                    "identical": r.block == block}
    return {"key": (chain, path), "data": bytes(buf), "orig_same": bytes(buf) == data, "blocks": res}


def _roundtrip_job(args):
    chain, path = args
    disc = _WORKER["disc"]
    data = disc.read(chain, path)
    out = {"blocks": 0, "entries": 0, "placeholders": 0, "bogus": 0, "fail": [], "std": 0, "emb": 0}
    for off, block in blocks_of(path, data):
        try:
            t, infos = texenc.analyse(block)
        except txy.TxyError as err:
            out["fail"].append(f"{path} {off:#x}: {err}")
            continue
        if not t["banks"]:
            out["placeholders"] += 1
            continue
        edits = {i: inf.orig for i, inf in infos.items() if not inf.bogus}
        out["bogus"] += sum(1 for inf in infos.values() if inf.bogus)
        try:
            r = texenc.encode_block(block, edits, force=True, t=t, info=(t, infos))
            if r.block != block:
                out["fail"].append(f"{path} {off:#x}: bytes differ")
        except texenc.EncodeError as err:
            out["fail"].append(f"{path} {off:#x}: {err}")
            continue
        out["blocks"] += 1
        out["entries"] += len(edits)
        out["std" if path.lower().endswith(".txy") else "emb"] += 1
    return out


def _fmt_psnr(v) -> str:
    return "inf" if v == math.inf else f"{v:.2f}"


def verify_roundtrip(iso_path: str) -> int:
    t0 = time.time()
    disc = Disc(iso_path)
    objs = carrier_objects(disc)
    sheets = [(c, e.path) for c, e in disc.order if e.path.lower().endswith(".xtx")]
    disc.close()
    print(f"round trip: {len(objs)} texture pages and carriers, {len(sheets)} UI sheets; every entry is forced "
          f"through the edit path with its own picture as the target")
    res = _pool_map(_roundtrip_job, objs, iso_path)
    tot = {k: 0 for k in ("blocks", "entries", "placeholders", "bogus", "std", "emb")}
    fails = []
    for r in res:
        for k in tot:
            tot[k] += r[k]
        fails += r["fail"]
    print(f"txy: {tot['std']} standalone + {tot['emb']} embedded blocks byte-identical "
          f"({tot['entries']} entries), {tot['placeholders']} placeholders skipped, "
          f"{tot['bogus']} bogus entries left out (refused for editing), {len(fails)} failures")
    xres = _pool_map(_xtx_job, [(c, p, "", "auto", "none", True) for c, p in sheets], iso_path)
    kinds: Dict[str, int] = {}
    xok = 0
    for (c, p), r in zip(sheets, xres):
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        if r["kind"] in XTX_OK:
            if r["problems"] or not r.get("identical"):
                fails.append(f"{p}: " + ("; ".join(r["problems"]) or "bytes differ"))
            else:
                xok += 1
    print(f"xtx: {xok} importable sheets byte-identical ("
          + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()) if k in XTX_OK) + "); not importable: "
          + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()) if k not in XTX_OK))
    for f in fails:
        print(f"FAIL  {f}")
    print(f"{len(fails)} failures, {time.time() - t0:.0f} s")
    return 1 if fails else 0


def _key(e: Edit):
    return (e.chain, e.path.lower(), e.block_off, e.entry)


def run(iso, out=None, tree=None, textures=None, xtx=None, models=None, palette="auto", dither="none",
        sweep="pixels", mips="rebuild", grow=False, dry_run=False, preview=None, report=None,
        verify_roundtrip_=False, workers=None, _plan=None) -> int:
    if verify_roundtrip_:
        try:
            return verify_roundtrip(iso)
        except region.UnknownBuild as err:
            print(f"refused: {err}", file=sys.stderr)
            return 1
    t0 = time.time()
    if palette not in texenc.MODES or dither not in texenc.DITHERS or sweep not in ("pixels", "name", "none") \
            or mips not in ("keep", "rebuild"):
        print("bad option value", file=sys.stderr)
        return 2
    if not out and not dry_run:
        print("--out is required (or --dry-run)", file=sys.stderr)
        return 2
    try:
        roots = resolve_roots(tree, textures, xtx, models)
        disc = Disc(iso) if _is_path(iso) else iso
    except (ImportError_, region.UnknownBuild) as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    refs, problems = scan_tree(roots)
    # spell paths as the disc does and group by object
    by_obj: Dict[Tuple[int, str], List[Ref]] = {}
    xtx_refs, glb_refs = [], []
    for r in refs:
        if r.kind in ("txy", "emb"):
            e = disc.entry(r.chain, r.path)
            if e is None:
                problems.append(f"{r.file}: no object {r.path!r} in chain {r.chain} on this disc")
                continue
            by_obj.setdefault((r.chain, e.path), []).append(r)
        elif r.kind == "xtx":
            xtx_refs.append(r)
        else:
            glb_refs.append(r)
    n_png = sum(len(v) for v in by_obj.values())
    print(f"tree: {n_png} texture PNGs in {len(by_obj)} files, {len(xtx_refs)} UI sheets, {len(glb_refs)} models")
    jobs = [(c, p, v) for (c, p), v in sorted(by_obj.items())]
    res = _pool_map(_txy_job, jobs, iso, workers=workers)
    edits: Dict[tuple, Edit] = {}
    unchanged = 0
    for r in res:
        problems += r["problems"]
        unchanged += r["unchanged"]
        for e in r["edits"]:
            edits[_key(e)] = e
    gres = _pool_map(_glb_job, [(r.chain, r.path, str(r.file)) for r in glb_refs], iso, workers=workers)
    for r in gres:
        problems += r["problems"]
        unchanged += r["unchanged"]
        for e in r["edits"]:
            k = _key(e)
            if k in edits and not texenc.same(np.frombuffer(edits[k].target, np.uint8),
                                              np.frombuffer(e.target, np.uint8)).all():
                problems.append(f"entry {e.entry} ({e.name}) of {e.path} is edited differently in "
                                f"{edits[k].source} and {e.source}")
                continue
            edits.setdefault(k, e)
    xres = _pool_map(_xtx_job, [(r.chain, r.path, str(r.file), palette, dither, False) for r in xtx_refs], iso,
                     workers=workers)
    xtx_repl, xtx_rows, warnings = {}, [], []
    for r in xres:
        problems += r["problems"]
        warnings += r["warnings"]
        unchanged += r["unchanged"]
        if r["replace"]:
            xtx_repl[r["replace"][0]] = r["replace"][1]
            xtx_rows.append(r["row"])
    if problems:
        for p in problems:
            print(f"problem: {p}", file=sys.stderr)
        print(f"{len(problems)} problems; nothing written (a PNG from another kit revision, a wrong size, or "
              f"an edit the kit refuses)", file=sys.stderr)
        disc.close()
        return 1
    print(f"edits: {len(edits)} texture entries and {len(xtx_repl)} UI sheets differ from the disc "
          f"({unchanged} PNGs unchanged)")

    # ---- mips
    mip_note = []
    if edits:
        add = {}
        for k, e in list(edits.items()):
            if e.mip != 0xFFFFFFFF:
                continue
            data = disc.read(e.chain, e.path)
            block = dict(blocks_of(e.path, data))[e.block_off]
            t = txy.parse(block)
            base = t["entries"][e.entry]
            sib = [x for x in t["entries"] if x["name"] == base["name"] and x["fmt"] == base["fmt"]
                   and (x["palx"], x["paly"]) == (base["palx"], base["paly"]) and 1 <= x.get("mip", 0) <= 6]
            if not sib:
                continue
            if mips == "keep":
                mip_note.append(e.name)
                continue
            dec = {en["i"]: rgba for en, _w, _h, rgba in txy.decode_fast(block)}
            for x in sib:
                L = x["mip"]
                img, w, h = np.frombuffer(e.target, np.uint8).reshape(-1, 4), e.w, e.h
                for _ in range(L):
                    img, w, h = box_down(img, w, h)
                if (w, h) != (x["w"], x["h"]) or x["i"] not in dec:
                    continue
                kk = (e.chain, e.path.lower(), e.block_off, x["i"])
                if kk not in edits:
                    add[kk] = Edit(e.chain, e.path, e.block_off, x["i"], x["name"], x["w"], x["h"], x["fmt"], L,
                                   img.tobytes(), dec[x["i"]], f"mip {L} of {e.source}", "mip", _twin(x))
        edits.update(add)
        if mip_note:
            print(f"note: {len(mip_note)} edited entries have +0x34 mip siblings "
                  f"({', '.join(sorted(set(mip_note))[:6])}); pass --mips rebuild to rebuild them")

    # ---- sweep
    copies: Dict[tuple, Edit] = {}
    copy_src: Dict[tuple, tuple] = {}
    if edits and sweep != "none":
        groups: Dict[tuple, List[tuple]] = {}
        for k, e in edits.items():
            groups.setdefault(_sweep_group(e, sweep), []).append(k)
        for gk, ks in groups.items():
            t0_ = np.frombuffer(edits[ks[0]].target, np.uint8)
            other = [k for k in ks[1:] if not texenc.same(t0_, np.frombuffer(edits[k].target, np.uint8)).all()]
            if other:
                a, b = ks[0], other[0]
                problems.append(f"{edits[a].source} and {edits[b].source} start from the same picture but were "
                                f"edited differently (the sweep would write both onto every copy); use "
                                f"--sweep none to let each stand alone")
        if problems:
            for p in problems:
                print(f"problem: {p}", file=sys.stderr)
            disc.close()
            return 1
        wanted: Dict[Tuple[int, int], set] = {}
        for k, e in edits.items():
            wanted.setdefault((e.w, e.h), set()).add(hashlib.sha1(e.orig).hexdigest())
        objs = carrier_objects(disc)
        hits = [h for r in _pool_map(_sweep_job, objs, iso, wanted, workers=workers) for h in r]
        by_sha: Dict[str, List[tuple]] = {}
        for k, e in edits.items():
            by_sha.setdefault(hashlib.sha1(e.orig).hexdigest(), []).append(k)
        twins_of = {(e.chain, e.path.lower(), e.block_off, e.twin) for e in edits.values()}
        for chain, path, off, i, name, sha, fmt, w, h, mip, tw in hits:
            kk = (chain, path.lower(), off, i)
            if (chain, path.lower(), off, tw) in twins_of:
                continue                     # a re-listed twin of an edit: it rides along in the encoder
            srcs = [s for s in by_sha.get(sha, []) if edits[s].w == w and edits[s].h == h]
            if not srcs or kk in edits:
                continue
            srcs = [s for s in srcs if not (_flat(edits[s].orig) or sweep == "name") or edits[s].name == name]
            if not srcs:
                continue
            s = srcs[0]
            copies[kk] = Edit(chain, path, off, i, name, w, h, fmt, mip, edits[s].target, edits[s].orig,
                              f"swept from {edits[s].source}", "swept")
            copy_src[kk] = s
        for s in sorted({v for v in copy_src.values()}, key=lambda k: edits[k].source):
            cs = [k for k, v in copy_src.items() if v == s]
            files = {(k[0], k[1]) for k in cs}
            names = sorted({copies[k].name for k in cs})
            print(f"sweep: {edits[s].name} ({edits[s].path} entry {edits[s].entry}): {len(cs)} copies in "
                  f"{len(files)} files; names: {', '.join(names[:8])}{' ...' if len(names) > 8 else ''}")
    disc.close()

    # ---- plan: phase A (blocks with edits), phase B (blocks with only copies)
    copy_mode = "keep" if palette == "keep" else "auto"
    blocks_a: Dict[Tuple[int, str], Dict[int, Dict[int, bytes]]] = {}
    for k, e in edits.items():
        blocks_a.setdefault((e.chain, e.path), {}).setdefault(e.block_off, {})[e.entry] = e.target
    for k, e in copies.items():
        objb = blocks_a.get((e.chain, e.path))
        if objb is not None and e.block_off in objb:
            objb[e.block_off][e.entry] = e.target
    results: Dict[Tuple[int, str], dict] = {}
    errors = []
    ja = [(c, p, pb, palette, dither, False) for (c, p), pb in sorted(blocks_a.items())]
    for r in _pool_map(_encode_obj_job, ja, iso, workers=workers):
        if "error" in r:
            errors.append(r["error"])
        else:
            results[r["key"]] = r
    if errors:
        for er in errors:
            print(f"refused: {er}", file=sys.stderr)
        return 1
    Q: Dict[tuple, bytes] = {}
    for k, e in edits.items():
        r = results[(e.chain, e.path)]["blocks"][e.block_off]
        Q[k] = r["expected"].get(e.entry, e.orig)
    blocks_b: Dict[Tuple[int, str], Dict[int, Dict[int, bytes]]] = {}
    for kk, e in copies.items():
        if (e.chain, e.path) in results and e.block_off in results[(e.chain, e.path)]["blocks"]:
            continue
        e.target = Q[copy_src[kk]]
        blocks_b.setdefault((e.chain, e.path), {}).setdefault(e.block_off, {})[e.entry] = e.target
    # an object can have phase-A blocks and phase-B blocks: encode B on top of A's result
    jb = [(c, p, pb, copy_mode, dither, False) for (c, p), pb in sorted(blocks_b.items()) if (c, p) not in results]
    for r in _pool_map(_encode_obj_job, jb, iso, workers=workers):
        if "error" in r:
            errors.append(r["error"])
        else:
            results[r["key"]] = r
    for (c, p), pb in sorted(blocks_b.items()):
        if (c, p) in results and any(off not in results[(c, p)]["blocks"] for off in pb):
            data = results[(c, p)]["data"]
            blocks = dict(blocks_of(p, data))
            buf = bytearray(data)
            for off, ed in pb.items():
                try:
                    r = texenc.encode_block(blocks[off], ed, copy_mode, dither)
                except texenc.EncodeError as err:
                    errors.append(f"{p} block {off:#x}: {err}")
                    continue
                buf[off:off + len(blocks[off])] = r.block
                results[(c, p)]["blocks"][off] = {"expected": r.expected, "roles": r.roles, "rows": r.rows,
                                                  "warnings": r.warnings, "identical": r.block == blocks[off]}
            results[(c, p)]["data"] = bytes(buf)
    if errors:
        for er in errors:
            print(f"refused: {er}", file=sys.stderr)
        return 1

    # ---- report rows
    rows = []
    role_of = {}
    for k, e in edits.items():
        role_of[k] = (e.role, e.source)
    for k, e in copies.items():
        role_of[k] = ("swept", e.source)
    for (c, p), r in sorted(results.items()):
        for off, br in sorted(r["blocks"].items()):
            warnings += [f"{p} block {off:#x}: {w}" for w in br["warnings"]]
            for row in br["rows"]:
                kk = (c, p.lower(), off, row["block_entry"])
                role, src = role_of.get(kk, (row["role"], ""))
                if row["role"] in ("ride-along", "joint-changed"):
                    role, src = row["role"], ""
                rows.append({"chain": c, "path": p, "block_off": f"{off:#x}", "entry": row["block_entry"],
                             "name": row["name"], "role": role, "source": src, "palette": row["palette"],
                             "slots_changed": row["slots_changed"], "pixels_changed": row["pixels_changed"],
                             "psnr": row["psnr"], "bytes_changed": row["bytes_changed"]})
    rows += [{k: v for k, v in r.items() if not k.startswith("_")} for r in xtx_rows]
    for w in warnings:
        print(f"warning: {w}")
    replacements = {k: r["data"] for k, r in results.items() if not r["orig_same"]}
    replacements.update(xtx_repl)
    by_role: Dict[str, int] = {}
    for r in rows:
        by_role[r["role"]] = by_role.get(r["role"], 0) + 1
    print(f"plan: {len(rows)} changed entries (" + ", ".join(f"{v} {k}" for k, v in sorted(by_role.items()))
          + f") in {len(replacements)} files")
    worst = sorted((r for r in rows if r["psnr"] != math.inf), key=lambda r: r["psnr"])[:5]
    for r in worst:
        print(f"  quantised: {r['path']} entry {r['entry']} {r['name']} ({r['role']}, {r['palette']}): "
              f"PSNR {_fmt_psnr(r['psnr'])} dB over {r['pixels_changed']} pixels")
    if _plan is not None:
        _plan.update(replacements=replacements, rows=rows, results=results)
    if not replacements:
        print("no texture changed; nothing written")
        return 0
    rep_path = report or (str(out) + ".textures.csv" if out else None)
    too_big = _overflow(iso, replacements) if _is_path(iso) else []
    for path, new, alloc in too_big:
        print(f"{'note' if grow else 'refused'}: {path} recompresses to {new} bytes, its slot holds {alloc}"
              + (" (moved to the end of the image: --grow)" if grow else "; pass --grow to move it to the end "
                 "of the image (not yet seen in the running game)"), file=sys.stdout if grow else sys.stderr)
    if too_big and not grow and not dry_run:
        return 1
    if preview:
        _write_preview(Path(preview), iso, results, xtx_rows)
    if dry_run:
        if rep_path:
            _write_report(rep_path, rows)
        print(f"dry run: nothing written ({time.time() - t0:.0f} s)")
        return 0
    from repack import RepackError, patch_copy
    try:
        patch_copy(iso, out, replacements, grow=grow)
    except (RepackError, region.UnknownBuild) as err:
        print(f"refused: {err}", file=sys.stderr)
        return 1
    bad = verify_output(iso, out, results, xtx_rows, replacements)
    if rep_path:
        _write_report(rep_path, rows)
        print(f"report: {rep_path}")
    if bad:
        for b in bad:
            print(f"VERIFY FAILED: {b}", file=sys.stderr)
        return 1
    print(f"verified {len(replacements)} replaced files in {out} ({time.time() - t0:.0f} s)")
    return 0


def _overflow(iso, replacements) -> List[Tuple[str, int, int]]:
    """Objects that would not fit their slot (ARX objects are recompressed
    first), found on the source image before anything is copied."""
    import repack
    img = IsoImage(iso)
    try:
        plan, _extra = repack._plan(img, replacements)
    finally:
        img.close()
    return [(path, len(v[0]), v[3]) for (c, path), v in plan.items() if not v[4]]


def _sweep_group(e: Edit, sweep: str) -> tuple:
    """Sources in one group must agree on their target: same original picture,
    and for single-colour pictures (or --sweep name) the same entry name."""
    sha = hashlib.sha1(e.orig).hexdigest()
    return (sha, e.name) if _flat(e.orig) or sweep == "name" else (sha,)


def _flat(rgba: bytes) -> bool:
    return rgba == rgba[:4] * (len(rgba) // 4)


def verify_output(iso, out_iso, results, xtx_rows, replacements) -> List[str]:
    """Read every replaced object from the written image and decode it again:
    planned entries show their planned pictures, every other entry its
    original one, untouched blocks are byte-identical."""
    from repack import read_entry
    bad = []
    src = Disc(iso, check=False)
    new = Disc(out_iso, check=False)
    xrow = {(r["chain"], r["path"]): r for r in xtx_rows}
    try:
        for (c, p), payload in replacements.items():
            got = new.read(c, p)
            if got != payload:
                bad.append(f"{p}: the object on the new image differs from the planned bytes")
                continue
            old = src.read(c, p)
            if (c, p) in xrow:
                view, _k = xtx_view(got, new.read(c, mask_for(new, c, p)) if mask_for(new, c, p) else None)
                if view is None or view[2] != xrow[(c, p)]["_after"][2]:
                    bad.append(f"{p}: the sheet does not decode as planned")
                continue
            r = results[(c, p)]
            oldb = dict(blocks_of(p, old))
            newb = dict(blocks_of(p, got))
            if set(oldb) != set(newb):
                bad.append(f"{p}: the txy blocks moved")
                continue
            for off, block in newb.items():
                br = r["blocks"].get(off)
                if br is None:
                    if block != oldb[off]:
                        bad.append(f"{p} block {off:#x} was not planned to change but did")
                    continue
                before = {en["i"]: rgba for en, _w, _h, rgba in txy.decode_fast(oldb[off])}
                for en, _w, _h, rgba in txy.decode_fast(block):
                    want = br["expected"].get(en["i"], before.get(en["i"]))
                    if rgba != want:
                        bad.append(f"{p} block {off:#x} entry {en['i']} ({en['name']}) does not decode as planned")
        if replacements:                       # and once through the kit's own reader
            (c, p), payload = next(iter(replacements.items()))
            if read_entry(out_iso, c, p) != payload:
                bad.append(f"{p}: repack.read_entry disagrees with the planned bytes")
    finally:
        src.close()
        new.close()
    return bad


def _write_report(path, rows):
    keys = ["chain", "path", "block_off", "entry", "name", "role", "source", "palette", "slots_changed",
            "pixels_changed", "psnr", "bytes_changed"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["psnr"] = _fmt_psnr(r["psnr"]) if isinstance(r["psnr"], float) else r["psnr"]
            w.writerow({k: r.get(k, "") for k in keys})


def _write_preview(root: Path, iso, results, xtx_rows):
    _init(iso)
    disc = _WORKER["disc"]
    for (c, p), r in sorted(results.items()):
        old = disc.read(c, p)
        oldb = dict(blocks_of(p, old))
        newb = dict(blocks_of(p, r["data"]))
        for off, br in r["blocks"].items():
            changed = set(br["expected"])
            if not changed:
                continue
            d = root / f"chain{c}" / Path(*p.split("\\")) / f"{off:#x}"
            d.mkdir(parents=True, exist_ok=True)
            t = txy.parse(oldb[off])
            multi = len(t["banks"]) > 1
            before = {en["i"]: (en, rgba) for en, _w, _h, rgba in txy.decode_fast(oldb[off])}
            after = {en["i"]: rgba for en, _w, _h, rgba in txy.decode_fast(newb[off])}
            for i in sorted(changed):
                en, rgba = before[i]
                if after[i] == rgba:
                    continue
                stem = Path(txy.entry_filename(en, multi)).stem
                txy.write_png(d / f"{stem}__before.png", en["w"], en["h"], rgba)
                txy.write_png(d / f"{stem}__after.png", en["w"], en["h"], after[i])
    for row in xtx_rows:
        d = root / f"chain{row['chain']}" / "xtx"
        d.mkdir(parents=True, exist_ok=True)
        stem = row["path"].replace("\\", "__")
        for tag in ("before", "after"):
            W, H, rgba = row[f"_{tag}"]
            txy.write_png(d / f"{stem}__{tag}.png", W, H, rgba)


# ================================================================== self-test

class _FakeDisc:
    """An in-memory stand-in for Disc: {(chain, path): bytes}."""

    def __init__(self, objects):
        self.objects = {(c, p.lower()): (p, d) for (c, p), d in objects.items()}

        class E:
            def __init__(s, p):
                s.path = p
                s.ext = p.rsplit(".", 1)[-1].lower()
        self.order = [(c, E(p)) for (c, _pl), (p, _d) in self.objects.items()]
        self._E = E

    def entry(self, chain, path):
        hit = self.objects.get((chain, path.lower()))
        return self._E(hit[0]) if hit else None

    def read(self, chain, path):
        return self.objects[(chain, path.lower())][1]

    def close(self):
        pass


def _self_test_plan(tmp: Path) -> List[Tuple[bool, str]]:
    """Run the edit search, sweep and encode phases on a fake disc and tree."""
    out = []
    pal = [(k, (k * 3) & 255, 255 - k, 0x80) for k in range(64)] + [(0, 0, 0, 0)] * 192
    pal2 = [(255 - k, k, (k * 5) & 255, 0x80) for k in range(64)] + [(9, 9, 9, 0x80)] * 192
    idx = [(x + y * 3) % 64 for y in range(16) for x in range(16)]
    flat_idx = [5] * 256
    blk_a = texenc._pal_block(128, [
        {"name": "hair", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 112, "paly": 0, "_idx": idx},
        {"name": "flat", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 16, "y": 0, "palx": 112, "paly": 0,
         "_idx": flat_idx}], {(112, 0): pal})
    # the same pictures in another block under other names and another CLUT position/order
    perm = list(range(64))[::-1]
    pal_b = [pal[perm[k]] for k in range(64)] + [(0, 0, 0, 0)] * 192
    inv = {perm[k]: k for k in range(64)}
    blk_b = texenc._pal_block(128, [
        {"name": "hair_copy", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 32, "y": 16, "palx": 96, "paly": 16,
         "_idx": [inv[v] for v in idx]},
        {"name": "flat_other", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 96, "paly": 16,
         "_idx": [inv[5]] * 256},
        {"name": "flat", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 16, "y": 0, "palx": 96, "paly": 16,
         "_idx": [inv[5]] * 256}], {(96, 16): pal_b})
    del pal2
    carrier = b"\x00" * 64 + b"ef.txy\x00" + b"\x11" * 9 + blk_b + b"\x22" * 32
    objects = {(0, "model\\a.txy"): blk_a, (0, "simajiri\\esd\\e.esd"): carrier}
    fake = _FakeDisc(objects)
    _WORKER["disc"] = fake
    off_b = carrier.index(blk_b)
    # tree: unchanged PNGs give no edits
    dec_a = {e["i"]: r for e, _w, _h, r in txy.decode(blk_a)}
    root = tmp / "browse" / "textures_png"
    d = root / "chain0" / "model" / "a.txy"
    d.mkdir(parents=True)
    txy.write_png(d / "000_hair.png", 16, 16, dec_a[0])
    txy.write_png(d / "001_flat.png", 16, 16, dec_a[1])
    refs, probs = scan_tree(resolve_roots(tmp / "browse"))
    r = _txy_job((0, "model\\a.txy", refs))
    out.append((not probs and not r["edits"] and r["unchanged"] == 2, "an unchanged tree finds no edits"))
    # edit both
    hair = np.frombuffer(dec_a[0], np.uint8).reshape(-1, 4).copy()
    hair[:, :3] = 255 - hair[:, :3]
    flat = np.frombuffer(dec_a[1], np.uint8).reshape(-1, 4).copy()
    flat[:, :3] = (200, 10, 10)
    txy.write_png(d / "000_hair.png", 16, 16, hair.tobytes())
    txy.write_png(d / "001_flat.png", 16, 16, flat.tobytes())
    refs, probs = scan_tree(resolve_roots(tmp / "browse"))
    r = _txy_job((0, "model\\a.txy", refs))
    ed = {e.entry: e for e in r["edits"]}
    out.append((set(ed) == {0, 1}, "both edited PNGs are found"))
    wanted = {}
    for e in ed.values():
        wanted.setdefault((e.w, e.h), set()).add(hashlib.sha1(e.orig).hexdigest())
    _WORKER["wanted"] = wanted
    hits = [h for o in carrier_objects(fake) for h in _sweep_job(o)]
    found = {(h[1], h[3], h[4]) for h in hits}
    out.append((("simajiri\\esd\\e.esd", 0, "hair_copy") in found and ("simajiri\\esd\\e.esd", 1, "flat_other") in found,
                "the sweep finds the copies by pixels, under other names, inside a carrier"))
    # the real planner on the fake disc (dry run)
    import contextlib
    import io
    plan: dict = {}
    with contextlib.redirect_stdout(io.StringIO()):
        rc = run(fake, tree=str(tmp / "browse"), dry_run=True, _plan=plan)
    ra = texenc.encode_block(blk_a, {0: hair.tobytes(), 1: flat.tobytes()})
    new_c = plan.get("replacements", {}).get((0, "simajiri\\esd\\e.esd"))
    good = rc == 0 and new_c is not None and len(new_c) == len(carrier) and \
        (0, "model\\a.txy") in plan["replacements"]
    if good:
        dec_b2 = {e["i"]: x for e, _w, _h, x in txy.decode(new_c[off_b:off_b + len(blk_b)])}
        dec_b = {e["i"]: x for e, _w, _h, x in txy.decode(blk_b)}
        roles = {(r["path"], r["entry"]): r["role"] for r in plan["rows"]}
        good = dec_b2[0] == ra.expected[0] and dec_b2[1] == dec_b[1] and dec_b2[2] == ra.expected[1] \
            and roles.get(("simajiri\\esd\\e.esd", 0)) == "swept" and new_c[:off_b] == carrier[:off_b]
    out.append((good, "the planner sweeps the copy into the carrier (same result as the source), the flat "
                      "picture only under its own name, and leaves the carrier's other bytes alone"))
    plan = {}
    txy.write_png(d / "000_hair.png", 16, 16, dec_a[0])
    txy.write_png(d / "001_flat.png", 16, 16, dec_a[1])
    with contextlib.redirect_stdout(io.StringIO()):
        rc = run(fake, tree=str(tmp / "browse"), dry_run=True, _plan=plan)
    out.append((rc == 0 and plan.get("replacements") == {}, "an unchanged tree plans zero replacements"))
    # mip levels: an edited chain base rebuilds its +0x34 levels (same name, same palette)
    pal_m = [(k * 4, 255 - k * 4, 128, 0x80) for k in range(64)] + [(0, 0, 0, 0)] * 192
    blk_m = texenc._pal_block(128, [
        {"name": "rock", "fmt": txy.PSMT8, "w": 16, "h": 16, "x": 0, "y": 0, "palx": 112, "paly": 0,
         "mip": 0xFFFFFFFF, "_idx": [(x * 4) % 64 for y in range(16) for x in range(16)]},
        {"name": "rock", "fmt": txy.PSMT8, "w": 8, "h": 8, "x": 16, "y": 0, "palx": 112, "paly": 0, "mip": 1,
         "_idx": [(x * 8) % 64 for y in range(8) for x in range(8)]}], {(112, 0): pal_m})
    fake_m = _FakeDisc({(0, "model\\m.txy"): blk_m})
    dm = {e["i"]: r for e, _w, _h, r in txy.decode(blk_m)}
    dmi = tmp / "mip" / "textures_png" / "chain0" / "model" / "m.txy"
    dmi.mkdir(parents=True)
    rock = np.frombuffer(dm[0], np.uint8).reshape(-1, 4).copy()
    rock[:, :3] = rock[:, [2, 1, 0]]
    txy.write_png(dmi / "000_rock.png", 16, 16, rock.tobytes())
    plan = {}
    with contextlib.redirect_stdout(io.StringIO()):
        rc = run(fake_m, tree=str(tmp / "mip"), dry_run=True, _plan=plan)
    roles = {r["entry"]: r["role"] for r in plan.get("rows", [])}
    lvl, _w, _h = box_down(rock, 16, 16)
    newm = plan.get("replacements", {}).get((0, "model\\m.txy"), b"")
    got1 = {e["i"]: r for e, _w2, _h2, r in txy.decode(newm)}.get(1) if newm else None
    out.append((rc == 0 and roles == {0: "edited", 1: "mip"} and got1 is not None
                and texenc.psnr(lvl, np.frombuffer(got1, np.uint8).reshape(-1, 4)) > 30,
                "an edited mip-chain base rebuilds its level-1 entry from the edit (box filter)"))
    _WORKER["disc"] = fake
    # path parsing
    p1, _ = parse_texture_path(("chain0", "model", "map", "x.txy", "007_sky_b4294967295_dup1.png"))
    p2, _ = parse_texture_path(("_embedded", "chain0", "simajiri", "esd", "e.esd", f"{off_b:#x}", "003_ring.png"))
    p3, _ = parse_xtx_path(("chain0", "yajima", "titleobj.xtx.png"))
    p4, _ = parse_model_path(("chain0", "model", "char", "pc", "kosmos00.glb"))
    _bad, why = parse_texture_path(("chain0", "model", "x.txy", "notes.png"))
    out.append((p1.entry == 7 and p1.path == "model\\map\\x.txy" and p2.block_off == off_b and p2.entry == 3
                and p2.path == "simajiri\\esd\\e.esd" and p3.path == "yajima\\titleobj.xtx"
                and p4.path == "model\\char\\pc\\kosmos00.txy" and why,
                "tree paths parse: standalone (_b4294967295), embedded, xtx, glb; a stray PNG is a problem"))
    # a wrong file name is a problem (tree from another revision)
    (d / "000_hair.png").rename(d / "000_hairy.png")
    refs, probs = scan_tree(resolve_roots(tmp / "browse"))
    r = _txy_job((0, "model\\a.txy", refs))
    out.append((any("would be named" in p for p in r["problems"]), "a PNG whose name does not match its entry is refused"))
    return out


def _self_test_glb() -> List[Tuple[bool, str]]:
    import gltf
    out = []
    blk, _ = txy._build_synthetic()
    t = txy.parse(blk)
    dec = {e["i"]: (e, r) for e, _w, _h, r in txy.decode(blk)}
    names = [Path(txy.entry_filename(t["entries"][i], True)).stem for i in (0, 1)]
    m, left = map_glb_images(names, t, dec, None)
    out.append((m == {0: 0, 1: 1} and not left, "glb images map back by PNG-file stem"))
    m, left = map_glb_images(["face_01"], t, dec, {0, 1})
    out.append((m == {0: 1} and not left, "an image named after an entry name unique among the used entries maps"))
    m, left = map_glb_images(["x", "y"], t, dec, {0, 1})
    out.append((m == {0: 0, 1: 1} and not left, "unnamed images map by pxy.to_glb's order when the counts match"))
    m, left = map_glb_images(["x"], t, dec, {0, 1})
    out.append((left == [0], "an unmatched image with a different count stays unmapped (a problem)"))
    g = gltf.GlbBuilder("test")
    for i in (0, 1):
        g.add_image(txy.png_bytes(16, 16, dec[i][1]), names[i])
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "m.glb"
        f.write_bytes(g.to_bytes())
        imgs = read_gltf_images(f)
    ok = [nm for nm, _b, _m in imgs] == names and pngread.read_png(imgs[1][1])[2] == dec[1][1]
    out.append((ok, "a .glb written with gltf.GlbBuilder reads back with its image names and pixels"))
    return out


def _self_test_xtx() -> List[Tuple[bool, str]]:
    """A synthetic parked-corner sheet and frame mask: the corner+mask inverse
    keeps the masked-out pixels as they were."""
    out = []
    clen, w, h = 256, 48, 32                 # one 48x32 CT32 image: art 32 wide + 16-wide palette column
    canvas = bytearray(w * h * 4)
    pos = txy.csm1_positions()
    cols = [((k * 7) & 255, (k * 13) & 255, (k * 29) & 255, 0x80) for k in range(256)]
    for i, c in enumerate(cols):
        s = pos[i]
        o = ((s // 16) * w + w - 16 + s % 16) * 4
        canvas[o:o + 4] = bytes(c)
    for y in range(h * 2):
        for x in range(64):
            v = (x * 3 + y * 5) % 200 + 1
            o = txy.swizzle8_offset(clen, x, y)
            p = o // 4
            cx, cy = p % clen, p // clen
            canvas[(cy * w + cx) * 4 + o % 4] = v

    def sheet(px):
        hdr = struct.pack("<4sIII", b"XTX\x00", 0, 1, 0x10)
        rec = struct.pack("<HHHHIII", w, 4, h, 0, 0, w * h * 4, 0x30)
        body = hdr + rec
        body += bytes(0x30 - len(body)) + bytes(32) + px
        return body[:4] + struct.pack("<I", len(body)) + body[8:]
    data = sheet(bytes(canvas))
    r = xtxmod.decode(data)
    out.append((r is not None and r[3] == "corner", "a synthetic parked-corner sheet decodes as corner"))
    # mask: index 1 inside a rounded box, palette 0 clear / 1 white
    mcan = bytearray(w * h * 4)
    mcols = [(0, 0, 0, 0), (255, 255, 255, 0x80)] + [(0, 0, 0, 0)] * 254
    for i, c in enumerate(mcols):
        s = pos[i]
        o = ((s // 16) * w + w - 16 + s % 16) * 4
        mcan[o:o + 4] = bytes(c)
    for y in range(h * 2):
        for x in range(64):
            v = 1 if (4 <= x < 60 and 4 <= y < 60 and not (x < 8 and y < 8)) else 0
            o = txy.swizzle8_offset(clen, x, y)
            p = o // 4
            cx, cy = p % clen, p // clen
            mcan[(cy * w + cx) * 4 + o % 4] = v
    mask = sheet(bytes(mcan))
    view, kind = xtx_view(data, mask)
    out.append((kind == "corner+mask", "with its frame mask the sheet is corner+mask, as browse writes it"))
    png = np.frombuffer(view[2], np.uint8).reshape(-1, 4).copy()
    png[:, :3] = 255 - png[:, :3]            # paint everything, including pixels the mask hides
    png[:, 3] = 255
    full, ignored, keep = unmask_target(data, mask, png.tobytes())
    new, exp, sol = encode_corner(data, full, keep=keep)
    after, _k = xtx_view(new, mask)
    m = xtxmod.decode(mask)
    orig_full = np.frombuffer(r[2], np.uint8).reshape(r[1], r[0], 4)
    new_full = np.frombuffer(xtxmod.decode(new)[2], np.uint8).reshape(r[1], r[0], 4)
    ma = np.frombuffer(m[2], np.uint8).reshape(m[1], m[0], 4)[:, :, 3]
    hidden = np.zeros((r[1], r[0]), bool)
    hidden[:m[1], :m[0]] = ma == 0
    out.append((ignored > 0 and (new_full[hidden] == orig_full[hidden]).all()
                and after[2] == xtxmod.apply_mask((r[0], r[1], exp, "corner"), m)[2],
                f"corner+mask inverse: the edit lands, the {int(hidden.sum())} masked-out pixels stay original"))
    new2, _e, _s = encode_corner(data, r[2])
    out.append((new2 == data, "an unchanged corner sheet re-encodes byte-identical"))
    return out


def self_test() -> int:
    import tempfile
    ok = True
    checks = []
    with tempfile.TemporaryDirectory() as td:
        checks += _self_test_plan(Path(td))
    checks += _self_test_glb()
    checks += _self_test_xtx()
    a = np.zeros((4, 4, 4), np.uint8)
    a[:, :, 3] = 255
    a[0:2, 0:2, 0] = 200
    a[0, 0, 3] = 0
    b, w2, h2 = box_down(a.reshape(-1, 4), 4, 4)
    checks.append(((w2, h2) == (2, 2) and tuple(b[0]) == (200, 0, 0, 191) and tuple(b[3]) == (0, 0, 0, 255),
                   "the mip box filter weights RGB by alpha"))
    _WORKER.clear()
    for good, msg in checks:
        print(("ok    " if good else "FAIL  ") + msg)
        ok &= bool(good)
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--iso", required=True)
    p.add_argument("--out", help="the modded copy to write (required unless --dry-run or --verify-roundtrip)")
    p.add_argument("--tree", help="the edited browse folder (or its textures_png folder); only changed PNGs are written")
    p.add_argument("--textures", help="a textures_png folder (overrides the one under --tree)")
    p.add_argument("--xtx", help="an xtx folder of UI sheet PNGs (overrides the one under --tree)")
    p.add_argument("--models", help="a models folder of .glb files (overrides the one under --tree)")
    p.add_argument("--palette", choices=texenc.MODES, default="auto",
                   help="shared palettes: auto keeps the other textures' colours, keep never changes a palette, "
                        "joint re-quantises every texture of the palette together")
    p.add_argument("--dither", choices=texenc.DITHERS, default="none", help="Floyd-Steinberg on quantised pixels")
    p.add_argument("--sweep", choices=("pixels", "name", "none"), default="pixels",
                   help="also edit every other copy of the same pixels on the disc (pixels), only copies with the "
                        "same entry name (name), or nothing else (none)")
    p.add_argument("--mips", choices=("keep", "rebuild"), default="rebuild",
                   help="smaller mip levels (+0x34) of an edited entry: rebuild them from the edit (default; the "
                        "models' draw state asks the GS for those levels) or leave them as they are (keep)")
    p.add_argument("--grow", action="store_true",
                   help="let a compressed object that outgrows its slot move to the end of the image "
                        "(yajima\\titleobj.xtx; not yet seen in the running game)")
    p.add_argument("--dry-run", action="store_true", help="plan and print, write nothing")
    p.add_argument("--preview", help="folder for before/after PNGs of every changed entry")
    p.add_argument("--report", help="CSV of every changed entry (default <out>.textures.csv)")
    p.add_argument("--verify-roundtrip", action="store_true",
                   help="write nothing: re-encode every texture on the disc with its own picture and check the "
                        "bytes come back identical")


def run_args(a) -> int:
    return run(a.iso, a.out, a.tree, a.textures, a.xtx, a.models, a.palette, a.dither, a.sweep, a.mips, a.grow,
               a.dry_run, a.preview, a.report, a.verify_roundtrip)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    ap = argparse.ArgumentParser(description="write edited PNGs (textures, UI sheets, model .glb images) back into "
                                             "a copy of the disc")
    add_arguments(ap)
    return run_args(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
