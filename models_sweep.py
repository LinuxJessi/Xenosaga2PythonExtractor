#!/usr/bin/env python3
"""Inventory and sweep of every `.pxy` in a dump (the numbers quoted in
docs/MODELS.md come from this tool).

    python models_sweep.py --self-test
    python models_sweep.py DUMP_DIR --inventory     # VIF opcode histogram
    python models_sweep.py DUMP_DIR                 # decode + invariants
    python models_sweep.py DUMP_DIR --glb           # ... and write every
                                                    # GLB in memory, read it
                                                    # back, check skinning
                                                    # (slow: --jobs 8)

DUMP_DIR is searched recursively for `.pxy`; the `.txy` and `.xhr` of a
model are looked up next to it (same name).  `--json FILE` saves one row
per file.
"""
import collections
import json
import os
import re
import struct
import sys

import numpy as np

import pxy
import vif

C = collections.Counter


def find(root, ext=".pxy"):
    out = []
    for d, _, names in os.walk(root):
        out += [os.path.join(d, n) for n in names if n.lower().endswith(ext)]
    return sorted(out)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- inventory

def draw_ranges(buf):
    """(offset, end) of every draw, after checking that the draw list
    tiles the VIF region exactly."""
    o_draw, = struct.unpack_from("<I", buf, 0x3C)
    o_vif, = struct.unpack_from("<I", buf, 0x4C)
    n_draw, = struct.unpack_from("<I", buf, 0x50)
    ents = sorted(struct.unpack_from("<II", buf, o_draw + i * 0x20)
                  for i in range(n_draw))
    pos = o_vif
    for off, qwc in ents:
        if off != pos:
            raise vif.VifError("draw list does not tile the VIF stream")
        pos = off + qwc * 16
    if pos != o_draw:
        raise vif.VifError("VIF stream does not end at the draw list")
    return [(off, off + qwc * 16) for off, qwc in ents]


def batch_signature(buf, cur):
    """Kind and layout of one batch, addresses and counts in units of n."""
    hx, kind = struct.unpack_from("<II", buf, cur[0].data_off)
    n = hx & 0x7FFF
    parts = []
    for u in cur:
        if kind == 1 or not n:
            parts.append("%s@%d:%d" % (u.fmt, u.addr, u.count))
            continue
        q, r = divmod(u.addr - 1, n) if u.addr else (0, 0)
        addr = "0" if not u.addr else ("%dn+1" % q if not r
                                       else "%dn+1+%d" % (q, r))
        cnt = {n: "n", 2 * n + 1: "2n+1", 1: "1"}.get(u.count, str(u.count))
        if n == 1 and u.count == 1:
            cnt = "n|1"
        parts.append("%s@%s:%s" % (u.fmt, addr, cnt))
    return kind, " ".join(parts)


def inventory(files, out=sys.stdout):
    hist = {}
    by_ver = {}
    kinds = C()
    sigs = C()
    sig_files = collections.defaultdict(set)
    trailing = C()
    stats = dict(files=0, commands=0, failures=0, zero_num_unpacks=0,
                 max_address=0, framing_bad=0)
    for f in files:
        buf = _read(f)
        stats["files"] += 1
        ver = round(struct.unpack_from("<f", buf, 4)[0], 2)
        try:
            for start, end in draw_ranges(buf):
                cur = []
                tail = 0
                for c in vif.walk(buf, start, end):
                    stats["commands"] += 1
                    if c.is_unpack:
                        stats["zero_num_unpacks"] += c.num == 0
                        stats["max_address"] = max(stats["max_address"],
                                                   c.addr + c.count)
                        cur.append(c)
                    elif c.name == "MSCNT":
                        kind, sig = batch_signature(buf, cur)
                        kinds[kind] += 1
                        sigs[(kind, sig if kind != 1 else
                              "V4-32@0:%d" % cur[0].count)] += 1
                        sig_files[(kind, sig)].add(f)
                        cur = []
                    elif c.name == "NOP":
                        tail += 1
                        if cur:
                            raise vif.VifError("NOP inside a batch")
                    elif c.name != "STCYCL":
                        raise vif.VifError("unexpected %s" % c.name)
                if cur:
                    raise vif.VifError("draw ends inside a batch")
                trailing[tail] += 1
                vif.histogram(buf, start, end, hist)
                vif.histogram(buf, start, end, by_ver.setdefault(ver, {}))
        except vif.VifError as e:
            stats["failures"] += 1
            out.write("FAIL %s: %s\n" % (f, e))
    out.write("inventory: %(files)d files, %(commands)d VIF commands, "
              "%(failures)d files failed the strict walk\n" % stats)
    out.write("UNPACKs with NUM = 0 (meaning 256): %d; highest address "
              "written (TOPS-relative, exclusive): %d\n"
              % (stats["zero_num_unpacks"], stats["max_address"]))
    out.write("NOPs closing a draw (padding to a qword): %s\n"
              % sorted(trailing.items()))

    def dump(h, title):
        out.write("%s\n" % title)
        for k, v in sorted(h.items(), key=lambda kv: -kv[1]):
            out.write("  %9d  %s\n" % (v, " ".join(x for x in k if x)))
    dump(hist, "all files:")
    for ver in sorted(by_ver):
        dump(by_ver[ver], "version %.2f:" % ver)
    out.write("batch kinds (header y): %s\n" % sorted(kinds.items()))
    mesh = C()
    for (kind, sig), v in sigs.items():
        mesh[(kind, sig)] += v
    out.write("batch layouts (kind, layout: batches):\n")
    for (kind, sig), v in sorted(mesh.items(), key=lambda kv: -kv[1])[:40]:
        out.write("  %8d  kind %d  %s\n" % (v, kind, sig))
    return stats, hist, kinds


# -------------------------------------------------------------------- sweep

def sweep_one(path, make_glb=False):
    buf = _read(path)
    row = dict(file=path, version=round(struct.unpack_from("<f", buf, 4)[0], 2),
               flags=struct.unpack_from("<I", buf, 8)[0], failed=[])
    row["family"] = "static (flags bit 0)" if row["flags"] & 1 else \
        "skin-capable"
    try:
        p = pxy.parse(buf)
    except (pxy.PxyError, vif.VifError) as e:
        row["decode"] = "FAIL: %s" % e
        return row
    row["decode"] = "ok"
    base = path[:-4]
    txy_data = entries = x = None
    if os.path.exists(base + ".txy"):
        import txy
        txy_data = _read(base + ".txy")
        try:
            entries = txy.parse(txy_data)["entries"]
        except txy.TxyError as e:
            row["txy"] = "unreadable (%s)" % e
            txy_data = None
    else:
        row["txy"] = "missing"
    try:
        r = pxy.check(p, entries)
    except pxy.PxyError as e:
        row["decode"] = "FAIL: %s" % e
        return row
    row["check"] = r
    row["failed"] = list(r["failed"])
    row["signature"] = sorted(set(
        "k%d f%02x%s" % (b.kind, b.flags, " face" if b.face is not None
                         else "") for d in p.draws for b in d.batches))
    row["shaders"] = sorted(set(m.shader for d in p.draws
                                for m in d.materials))
    row["skinned"] = any(b.skin_mode for d in p.draws for b in d.batches)
    if row["skinned"]:
        used = pxy.referenced_joints(p)
        row["joint_matrices"] = p.n_joint_matrices
        row["joints_used"] = len(used)
        if os.path.exists(base + ".xhr"):
            import xhr
            try:
                x = xhr.parse(_read(base + ".xhr"))
            except xhr.XhrError as e:
                row["xhr"] = "FAIL: %s" % e
            if x is not None and len(x.joints) < p.n_joint_matrices + 2:
                row["xhr"] = "too few transforms (%d for %d joint matrices)" \
                    % (len(x.joints), p.n_joint_matrices)
                x = None
            elif x is not None:
                w = x.world_matrices()
                n = p.n_joint_matrices
                prod = np.einsum("nij,njk->nik", p.inv_bind, w[2:2 + n])
                e = np.abs(prod - np.eye(4)).reshape(n, -1).max(axis=1)
                row["xhr"] = "ok"
                row["rest_err_used"] = max([float(e[j]) for j in used] or [0])
                row["rest_err_unused"] = max(
                    [float(e[j]) for j in range(n) if j not in used] or [0])
        else:
            row["xhr"] = "none"
    if make_glb and not row["failed"]:
        import gltf
        try:
            blob = pxy.to_glb(p, txy_data, x if row["skinned"] else None)
            js, binc = gltf.read_glb(blob)
            nt = sum(js["accessors"][pr["indices"]]["count"] // 3
                     for m in js.get("meshes", ()) for pr in m["primitives"])
            row["glb"] = "ok" if nt == p.triangle_count else \
                "triangle count %d != %d" % (nt, p.triangle_count)
            worst = 0.0
            for i, nd in enumerate(js["nodes"]):
                if "mesh" in nd and "skin" in nd:
                    for st, sk in gltf.skinned_positions(js, binc, i):
                        if len(st):
                            worst = max(worst, float(np.abs(st - sk).max()))
            if row["skinned"]:
                row["skin_rest_err"] = worst
        except Exception as e:           # report, never hide
            row["glb"] = "FAIL: %r" % (e,)
    return row


def report(rows, out=sys.stdout):
    n = len(rows)
    dec = [r for r in rows if r["decode"] == "ok"]
    good = [r for r in dec if not r["failed"]]
    out.write("sweep: %d files; %d decode; %d decode and pass every "
              "invariant; %d fail\n" % (n, len(dec), len(good), n - len(good)))
    for r in rows:
        if r["decode"] != "ok":
            out.write("  DECODE %s: %s\n" % (r["file"], r["decode"]))
    reasons = C()
    for r in dec:
        for m in r["failed"]:
            reasons[re.sub(r"\d+", "N", m)] += 1
    for m, v in reasons.most_common():
        out.write("  %4d files: %s\n" % (v, m))
    for key in ("version", "family"):
        c = C((r[key], r["decode"] == "ok" and not r["failed"]) for r in rows)
        out.write("by %s: %s\n" % (key, ", ".join(
            "%s %s=%d" % (k[0], "pass" if k[1] else "FAIL", v)
            for k, v in sorted(c.items(), key=str))))
    sg = C()
    for r in dec:
        for s in r["signature"]:
            sg[(s, not r["failed"])] += 1
    out.write("by batch signature (kind, flags, face array) -> files "
              "pass/fail:\n")
    for s in sorted(set(k[0] for k in sg)):
        out.write("  %-14s %4d / %d\n" % (s, sg[(s, True)], sg[(s, False)]))
    sh = C()
    for r in dec:
        for s in r["shaders"]:
            sh[(s, not r["failed"])] += 1
    out.write("by shader id -> files pass/fail: %s\n" % ", ".join(
        "%d: %d/%d" % (s, sh[(s, True)], sh[(s, False)])
        for s in sorted(set(k[0] for k in sh))))
    tot = C()
    uvmax = 0.0
    for r in dec:
        for k, v in r["check"].items():
            if k == "uv_abs_max":
                uvmax = max(uvmax, v)
            elif isinstance(v, (int, float)):
                tot[k] += v
    out.write("totals:\n")
    for k in sorted(tot):
        out.write("  %-28s %d\n" % (k, tot[k]))
    out.write("  %-28s %.1f\n" % ("uv_abs_max", uvmax))
    out.write("textures: %s\n" % dict(C(r.get("txy", "ok") for r in rows)))
    sk = [r for r in dec if r.get("skinned")]
    out.write("skinned files: %d; skeleton: %s\n" % (len(sk), dict(C(
        r.get("xhr", "?").split(" (")[0] for r in sk))))
    wx = [r for r in sk if r.get("xhr") == "ok"]
    out.write("  xhr rest pose == bind pose (|invBind*world - I| < 1e-3) on "
              "every joint a vertex uses: %d of %d\n"
              % (sum(1 for r in wx if r["rest_err_used"] < 1e-3), len(wx)))
    out.write("  ... and on the joints no vertex uses: %d of %d\n"
              % (sum(1 for r in wx if r["rest_err_unused"] < 1e-3), len(wx)))
    for r in wx:
        if r["rest_err_used"] >= 1e-3:
            out.write("    differs: %s (%.3g)\n" % (r["file"],
                                                    r["rest_err_used"]))
    if any("glb" in r for r in rows):
        out.write("glb: %s\n" % dict(C(r.get("glb", "not written")[:40]
                                       for r in rows)))
        se = [r["skin_rest_err"] for r in rows if "skin_rest_err" in r]
        if se:
            out.write("  skinned GLBs read back: rest-pose skinned position "
                      "vs static position, worst over %d files: %.3g\n"
                      % (len(se), max(se)))


# --------------------------------------------------------------- self-test

def self_test():
    import tempfile
    data = pxy._synthetic(True)[0]
    data2 = pxy._synthetic(False)[0]
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "a"))
        for name, blob in (("a/one.pxy", data), ("two.pxy", data2),
                           ("bad.pxy", data[:0x4C] + b"\0\0\0\0"
                            + data[0x50:])):
            with open(os.path.join(d, name), "wb") as f:
                f.write(blob)
        files = find(d)
        assert len(files) == 3

        class Sink(object):
            def __init__(self):
                self.s = ""

            def write(self, t):
                self.s += t
        sink = Sink()
        stats, hist, kinds = inventory(files, sink)
        assert stats["failures"] == 1 and "FAIL" in sink.s
        assert kinds == {1: 4, 0: 2, 3: 2}, kinds
        assert hist[("MSCNT",)] == 8 and hist[("STCYCL", "cl1/wl1")] == 8
        rows = [sweep_one(f, make_glb=True) for f in files]
        assert [r["decode"] == "ok" for r in rows] == [True, False, True]
        assert rows[0]["glb"] == "ok" and rows[0]["skin_rest_err"] < 1e-5
        assert rows[2]["glb"] == "ok" and not rows[2]["skinned"]
        sink = Sink()
        report(rows, sink)
        assert "3 files; 2 decode; 2 decode and pass" in sink.s, sink.s
    print("models_sweep self-test ok")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--inventory", action="store_true")
    ap.add_argument("--glb", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--jobs", type=int, default=1,
                    help="worker processes (texture decoding is slow)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test or not a.dump:
        return self_test()
    files = find(a.dump)
    if a.inventory:
        inventory(files)
        return 0
    if a.jobs > 1:
        import functools
        import multiprocessing
        with multiprocessing.Pool(a.jobs) as pool:
            rows = pool.map(functools.partial(sweep_one, make_glb=a.glb),
                            files, chunksize=4)
    else:
        rows = [sweep_one(f, a.glb) for f in files]
    report(rows)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, default=str, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
