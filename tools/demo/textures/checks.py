"""Checks for the texture-import demo (texture-import.sh runs them; exit status 1 on any FAIL).

  checks.py collect REPORT PREVIEW EDITED OUTDIR --before DIR [DIR ...] --after DIR [DIR ...]
      before/ and after/ PNGs for every row of the report. "before" is the PNG the retail
      browse wrote (what the user started from), "after" is the PNG a fresh dump + browse of
      the modded copy wrote (not the encoder's output). Checks per row: after differs from
      before, after equals the importer's planned preview. Writes contact_sheet.png
      (before | after per row) and compares the re-extracted kosmos00.glb images with the
      re-extracted PNGs.

  checks.py disc RETAIL MODDED REPORT
      1. every TOC object that the report does not list reads back byte-identical (raw, as
         stored) from the modded copy;
      2. every byte range where the images differ lies inside a replaced object's slot, the
         TOC fields of a replaced entry, the PVD volume size, or appended sectors;
      3. duplicates, found independently of the importer: every txy block in every object of
         the retail image (any extension) is decoded, and every entry whose RGBA equals the
         original picture of an edited entry is listed; each must decode differently on the
         modded copy (and the same as its edited source when it is a copy in another file).
"""
import argparse, csv, mmap, shutil, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np

KIT = Path(__file__).resolve().parents[3]          # the kit: tools/demo/textures/ -> three folders up
sys.path.insert(0, str(KIT))
import embedded, pngread, repack, teximport, txy  # noqa: E402
from iso9660 import IsoImage  # noqa: E402
from toc import SECTOR  # noqa: E402

FAILS = []


def verdict(ok, msg):
    print(f"  {'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
        FAILS.append(msg)


def rel_of(row):
    c, p = int(row["chain"]), row["path"]
    parts = p.split("\\")
    if p.lower().endswith(".xtx"):
        return Path("xtx") / f"chain{c}" / Path(*parts[:-1]) / (parts[-1] + ".png"), None
    return None, (c, p, int(row["entry"]))


# ------------------------------------------------------------------ collect

def find(dirs, rel):
    for d in dirs:
        if (Path(d) / rel).exists():
            return Path(d) / rel
    return None


def collect(a):
    out = Path(a.outdir)
    for d in ("before", "after"):
        shutil.rmtree(out / d, ignore_errors=True)
        (out / d).mkdir(parents=True)
    rows = list(csv.DictReader(open(a.report)))
    pairs = []
    for r in rows:
        c, p = int(r["chain"]), r["path"]
        if p.lower().endswith(".xtx"):
            rel, _ = rel_of(r)
            prev = Path(a.preview) / f"chain{c}" / "xtx" / (p.replace("\\", "__") + "__after.png")
            stem = p.replace("\\", "__")
        else:
            # the browse file name of the entry, from the retail block (multi-bank naming)
            blk = Path(*p.split("\\"))
            cand = sorted((find(a.before, Path("textures_png") / f"chain{c}" / blk) or Path("/nonexistent")).glob(f"{int(r['entry']):03d}_*.png"))
            if len(cand) != 1:
                verdict(False, f"{p} entry {r['entry']}: {len(cand)} retail browse PNGs")
                continue
            fn = cand[0].name
            rel = Path("textures_png") / f"chain{c}" / blk / fn
            prev = Path(a.preview) / f"chain{c}" / blk / r["block_off"] / (Path(fn).stem + "__after.png")
            stem = p.replace("\\", "__") + "__" + Path(fn).stem
        b, af = find(a.before, rel), find(a.after, rel)
        if b is None or af is None:
            verdict(False, f"{rel}: before {b}, after {af}")
            continue
        W, H, bb = pngread.read_png(b.read_bytes())
        _, _, ab = pngread.read_png(af.read_bytes())
        planned = pngread.read_png(prev.read_bytes())[2] if prev.exists() else None
        shutil.copyfile(b, out / "before" / f"{stem}.png")
        shutil.copyfile(af, out / "after" / f"{stem}.png")
        verdict(ab != bb and planned == ab,
                f"{stem} ({r['role']}): re-extracted PNG {'changed' if ab != bb else 'UNCHANGED'}, "
                f"{'equals the planned preview' if planned == ab else 'DIFFERS from the planned preview'}")
        pairs.append((W, H, bb, ab))
    if pairs:
        sheet(pairs, out / "contact_sheet.png")
        print(f"  contact sheet: {out / 'contact_sheet.png'}")
    # the model
    G = Path("models/chain0/model/char/pc/kosmos00.glb")
    rx, ed = find(a.after, G), Path(a.edited) / G
    if rx is None:
        verdict(False, "re-extracted kosmos00.glb missing")
        return
    shutil.copyfile(rx, out / "kosmos00.after.glb")
    shutil.copyfile(ed, out / "kosmos00.edited.glb")
    rb = find(a.before, G)
    shutil.copyfile(rb, out / "kosmos00.before.glb")
    ims = {n: b for n, b, _m in teximport.read_gltf_images(rx)}
    old = {n: b for n, b, _m in teximport.read_gltf_images(rb)}
    edi = {n: b for n, b, _m in teximport.read_gltf_images(ed)}
    for n in ("000_kosmos00_hair_col", "024_kosmos00_face", "005_kosmos00_ude", "009_kosmos00_mune"):
        png = find(a.after, Path("textures_png/chain0/model/char/pc/kosmos00.txy") / f"{n}.png")
        x = pngread.read_png(ims[n])[2]
        verdict(x == pngread.read_png(png.read_bytes())[2] and x != pngread.read_png(old[n])[2],
                f"re-extracted kosmos00.glb image {n}: changed from the retail .glb and equal to the re-extracted PNG")
    x, e = pngread.read_png(ims["009_kosmos00_mune"])[2], pngread.read_png(edi["009_kosmos00_mune"])[2]
    print(f"  note: the ring painted into the .glb comes back {'exactly' if x == e else 'palette-quantised (PSNR ' + psnr(x, e) + ' dB vs the edit)'}")


def psnr(x, y):
    a = np.frombuffer(x, np.uint8).astype(np.float64)
    b = np.frombuffer(y, np.uint8).astype(np.float64)
    m = ((a - b) ** 2).mean()
    return "inf" if m == 0 else f"{10 * np.log10(255 ** 2 / m):.2f}"


def sheet(pairs, path):
    rows_img = []
    for W, H, b, a in pairs:
        s = max(1, 128 // max(H, 1)) if H < 128 else 1
        up = lambda x: np.frombuffer(x, np.uint8).reshape(H, W, 4).repeat(s, 0).repeat(s, 1)
        B, A = up(b), up(a)
        rows_img.append(np.concatenate([B, np.zeros((B.shape[0], 4, 4), np.uint8), A], 1))
    Wm = max(r.shape[1] for r in rows_img)
    parts = []
    for r in rows_img:
        parts += [np.concatenate([r, np.zeros((r.shape[0], Wm - r.shape[1], 4), np.uint8)], 1),
                  np.zeros((4, Wm, 4), np.uint8)]
    s = np.concatenate(parts, 0)
    chk = ((np.indices(s.shape[:2]).sum(0) // 8) % 2 * 40 + 60).astype(np.float64)[..., None]
    al = s[..., 3:4] / 255.0
    rgb = (s[..., :3] * al + chk * (1 - al)).astype(np.uint8)
    o = np.concatenate([rgb, np.full(s.shape[:2] + (1,), 255, np.uint8)], -1)
    txy.write_png(path, o.shape[1], o.shape[0], o.tobytes())


# --------------------------------------------------------------------- disc

W = {}


def _init(retail, modded, targets):
    W["a"] = teximport.Disc(retail)
    W["b"] = teximport.Disc(modded)
    W["t"] = targets          # {(w, h): {rgba bytes: label}}


def _job(key):
    c, p = key
    da, db = W["a"], W["b"]
    ra, ea = da.by_path[(c, p.lower())]
    rb, eb = db.by_path[(c, p.lower())]
    raw_a = ra.read(ea.byte_offset, ea.size)
    same = ea.size == eb.size and raw_a == rb.read(eb.byte_offset, eb.size)
    hits = []
    data = teximport.arx.decompress(raw_a) if ea.compressed else raw_a
    blocks = [(0, data)] if data[:4] == b"txy\0" else list(embedded.iter_blocks(data))
    for off, blk in blocks:
        try:
            t = txy.parse(blk)
        except txy.TxyError:
            continue
        if not any((e["w"], e["h"]) in W["t"] for e in t["entries"]):
            continue
        for e, _w, _h, rgba in txy.decode(blk):
            lab = W["t"].get((e["w"], e["h"]), {}).get(rgba)
            if lab:
                hits.append((c, p, off, e["i"], e["name"], lab))
    return key, same, hits


def disc(a):
    rows = list(csv.DictReader(open(a.report)))
    replaced = sorted({(int(r["chain"]), r["path"]) for r in rows}, key=str)
    da, db = teximport.Disc(a.retail), teximport.Disc(a.modded)
    # targets: the original pictures of the entries the user edited
    targets, edited_new = {}, {}
    for r in rows:
        if r["role"] != "edited" or r["path"].lower().endswith(".xtx"):
            continue
        c, p, i = int(r["chain"]), r["path"], int(r["entry"])
        old = {e["i"]: (e, x) for e, _w, _h, x in txy.decode(dict(teximport.blocks_of(p, da.read(c, p)))[int(r["block_off"], 16)])}
        new = {e["i"]: x for e, _w, _h, x in txy.decode(dict(teximport.blocks_of(p, db.read(c, p)))[int(r["block_off"], 16)])}
        e, x = old[i]
        lab = f"{p} entry {i} {e['name']}"
        targets.setdefault((e["w"], e["h"]), {})[x] = lab
        edited_new[lab] = new[i]
    print(f"[1+3] scanning every object of {Path(a.retail).name} ({len(da.order)} TOC objects) ...")
    keys = [(c, e.path) for c, e in da.order]
    diff_objs, hits = [], []
    with ProcessPoolExecutor(initializer=_init, initargs=(a.retail, a.modded, targets)) as ex:
        for key, same, h in ex.map(_job, keys, chunksize=16):
            if not same:
                diff_objs.append(key)
            hits += h
    unlisted = [k for k in diff_objs if k not in replaced]
    print(f"  objects that differ: {len(diff_objs)}; listed in the report: {len(replaced)}")
    for k in diff_objs:
        print(f"    chain{k[0]} {k[1]}{'' if k in replaced else '  <- NOT IN THE REPORT'}")
    verdict(not unlisted and sorted(diff_objs, key=str) == replaced,
            f"{len(keys) - len(diff_objs)} unrelated objects byte-identical; exactly the reported files changed")
    # duplicates
    print(f"  copies of the edited pictures on the retail disc (decoded RGBA equality, independent scan): {len(hits)}")
    for c, p, off, i, name, lab in sorted(hits, key=str):
        blk_new = dict(teximport.blocks_of(p, db.read(c, p))).get(off)
        blk_old = dict(teximport.blocks_of(p, da.read(c, p)))[off]
        newx = {e["i"]: x for e, _w, _h, x in txy.decode(blk_new)}[i] if blk_new else None
        oldx = {e["i"]: x for e, _w, _h, x in txy.decode(blk_old)}[i]
        own = lab.startswith(f"{p} ")
        same_as_src = newx == edited_new[lab]
        verdict(newx is not None and newx != oldx and (own or same_as_src),
                f"chain{c} {p} block {off:#x} entry {i} {name} (copy of {lab}): "
                f"{'changed' if newx != oldx else 'UNCHANGED'}"
                + ("" if own else f", {'same RGBA as the edited source' if same_as_src else 'differs from the edited source'}"))
    # byte ranges
    print("[2] byte ranges")
    A, B = Path(a.retail).stat().st_size, Path(a.modded).stat().st_size
    iso_a = IsoImage(a.retail)
    allowed = []
    for c in sorted({c for c, _ in replaced}):
        w = repack.ChainWriter(iso_a.mm, iso_a, c)
        ents = [e for cc, e in da.order if cc == c]
        toc_base = iso_a.files[w.names[0]].lba * SECTOR
        for c2, p in replaced:
            if c2 != c:
                continue
            e = da.entry(c, p)
            for a0, _s, n in w._spans(e.byte_offset, repack._allocation(e, ents, w.total)):
                allowed.append((a0, a0 + n, f"chain{c} {e.path} (slot)"))
            f = toc_base + e.fields_off
            allowed.append((f, f + 10, f"chain{c} {e.path} (TOC fields)"))
    allowed.append((16 * SECTOR + 80, 16 * SECTOR + 88, "PVD volume size"))
    if B > A:
        allowed.append((A, B, "appended sectors (relocated objects)"))
    ranges = []
    with open(a.retail, "rb") as fa, open(a.modded, "rb") as fb:
        ma = mmap.mmap(fa.fileno(), 0, access=mmap.ACCESS_READ)
        mb = mmap.mmap(fb.fileno(), 0, access=mmap.ACCESS_READ)
        step = 64 << 20
        for s in range(0, A, step):
            x = np.frombuffer(ma[s:s + step], np.uint8)
            y = np.frombuffer(mb[s:s + len(x)], np.uint8)
            d = np.nonzero(x != y)[0]
            if len(d):
                br = np.nonzero(np.diff(d) > 64)[0]
                ranges += [(s + int(p), s + int(q)) for p, q in zip(np.r_[d[0], d[br + 1]], np.r_[d[br], d[-1]] + 1)]
    if B > A:
        ranges.append((A, B))
    bad = 0
    for x, y in ranges:
        lab = [l for s, e, l in allowed if s <= x and y <= e]
        print(f"    diff {x:#012x}-{y:#012x} ({y - x:7d} bytes): {lab[0] if lab else 'OUTSIDE EVERY REPLACED SLOT'}")
        bad += not lab
    print(f"  image size: retail {A}, modded {B}")
    verdict(bad == 0, f"{len(ranges)} differing byte ranges, {bad} outside replaced slots / TOC fields / volume size")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("collect")
    for n in ("report", "preview", "edited", "outdir"):
        c.add_argument(n)
    c.add_argument("--before", nargs="+", required=True)
    c.add_argument("--after", nargs="+", required=True)
    d = sp.add_parser("disc")
    for n in ("retail", "modded", "report"):
        d.add_argument(n)
    a = ap.parse_args()
    {"collect": collect, "disc": disc}[a.cmd](a)
    print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAIL'} ({a.cmd})")
    sys.exit(1 if FAILS else 0)
