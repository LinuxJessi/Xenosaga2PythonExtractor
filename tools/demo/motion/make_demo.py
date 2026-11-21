#!/usr/bin/env python3
"""Demo and acceptance numbers for the Episode II motion exporter (xap.py).

Builds three animated .glb files with `cli.py model` (the disc image is
only read), reads them BACK through gltf.read_glb / sample_animation /
node_world_matrices / skinned_positions (the glTF way, independent of
xap.pose's matrix code), and writes:

  <stem>.glb                    the models with their animations
  <stem>_<source>_<slot>.png    contact sheets, 8 frames, front + side
  report.txt / report.json      per-animation checks

Only numpy and the kit are used. ``tools/demo/motion.sh`` runs it.

    python tools/demo/motion/make_demo.py "Disc 1.iso" DUMP OUTDIR

DUMP is a ``cli.py dump`` of the disc (the folder holding chain0/); the files
above are written to OUTDIR.
"""
import json
import os
import subprocess
import sys

import numpy as np

# the kit is this repository: tools/demo/motion/ -> three folders up
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
if len(sys.argv) != 4:
    raise SystemExit("usage: make_demo.py DISC1.iso DUMP OUTDIR")
ISO1 = os.path.abspath(sys.argv[1])
DUMP = os.path.join(os.path.abspath(sys.argv[2]), "chain0")
OUTDIR = os.path.abspath(sys.argv[3])          # where everything is written
os.makedirs(OUTDIR, exist_ok=True)
sys.path.insert(0, KIT)

import gltf  # noqa: E402
import txy   # noqa: E402
import xap   # noqa: E402
import xhr   # noqa: E402

CHARS = [
    ("shion00", [("motion\\shion00.xap", [0, 1, 2]),
                 ("yamamoto\\mot\\shio00.xap", [0, 1]),
                 ("motion\\face_all.fap", [0])]),
    ("kosmos00", [("motion\\kosmos00.xap", [0, 1, 2]),
                  ("yamamoto\\mot\\kosm00.xap", [0, 1])]),
    ("jr00", [("motion\\jr00.xap", [0, 1, 2])]),
]


# ---------------------------------------------------------------- render

def raster(tris, depth, shade, W, H, img, zbuf, color):
    """Flat-shaded triangles (screen coords (m,3,2), depth (m,3), larger
    = nearer) into img/zbuf, vectorised over triangles."""
    if not len(tris):
        return
    lo = np.floor(tris.min(axis=1)).astype(int)
    hi = np.ceil(tris.max(axis=1)).astype(int)
    size = (hi - lo).max(axis=1) + 1
    for kmax in (4, 8, 16, 32, 64, 400):
        sel = (size <= kmax) & (size > kmax // 2 if kmax > 4 else True)
        if not sel.any():
            continue
        t, d, s, l0 = tris[sel], depth[sel], shade[sel], lo[sel]
        gy, gx = np.mgrid[0:kmax, 0:kmax]
        px = l0[:, None, 0] + gx.reshape(-1)[None] + 0.5      # (m, K*K)
        py = l0[:, None, 1] + gy.reshape(-1)[None] + 0.5
        a, b, c = t[:, 0], t[:, 1], t[:, 2]
        den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + \
            (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
        ok = np.abs(den) > 1e-12
        den = np.where(ok, den, 1.0)[:, None]
        w0 = ((b[:, 1] - c[:, 1])[:, None] * (px - c[:, None, 0]) +
              (c[:, 0] - b[:, 0])[:, None] * (py - c[:, None, 1])) / den
        w1 = ((c[:, 1] - a[:, 1])[:, None] * (px - c[:, None, 0]) +
              (a[:, 0] - c[:, 0])[:, None] * (py - c[:, None, 1])) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6) & ok[:, None]
        inside &= (px >= 0) & (px < W) & (py >= 0) & (py < H)
        z = w0 * d[:, None, 0] + w1 * d[:, None, 1] + w2 * d[:, None, 2]
        ii = np.nonzero(inside)
        if not len(ii[0]):
            continue
        idx = (py[ii].astype(int) * W + px[ii].astype(int))
        zz = z[ii]
        ss = np.broadcast_to(s[:, None], inside.shape)[ii]
        order = np.lexsort((-zz, idx))
        idx, zz, ss = idx[order], zz[order], ss[order]
        first = np.unique(idx, return_index=True)[1]
        idx, zz, ss = idx[first], zz[first], ss[first]
        nearer = zz > zbuf[idx]
        idx, zz, ss = idx[nearer], zz[nearer], ss[nearer]
        zbuf[idx] = zz
        img[idx] = (np.asarray(color)[None] * ss[:, None]).clip(0, 255)


def mesh_triangles(js, binc, overrides):
    """World-space triangles of every mesh node for one animation sample,
    skinned the glTF way."""
    world = gltf.node_world_matrices(js, overrides)
    out = []
    for i, nd in enumerate(js["nodes"]):
        if "mesh" not in nd:
            continue
        prims = js["meshes"][nd["mesh"]]["primitives"]
        if "skin" in nd:
            pos = [sk for _, sk in gltf.skinned_positions(js, binc, i,
                                                          overrides)]
        else:
            pos = []
            for pr in prims:
                p = gltf.accessor_array(js, binc,
                                        pr["attributes"]["POSITION"])
                ph = np.c_[p, np.ones(len(p))]
                pos.append((ph @ world[i].T)[:, :3])
        for pr, p in zip(prims, pos):
            ix = gltf.accessor_array(js, binc, pr["indices"]).astype(int)
            out.append(p[ix.reshape(-1, 3)])
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def contact_sheet(js, binc, ai, frames, start, path, cell=(150, 210)):
    W, H = cell
    samples = [np.linspace(frames[0], frames[-1], 8).round().astype(int)
               if len(frames) > 1 else np.repeat(frames[:1], 8)][0]
    tris_all = [mesh_triangles(js, binc, gltf.sample_animation(
        js, binc, ai, (f - start) / 30.0)) for f in samples]
    allp = np.concatenate([t.reshape(-1, 3) for t in tris_all])
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    span = max(hi[1] - lo[1], hi[0] - lo[0], hi[2] - lo[2]) * 1.08
    scale = (H - 10) / span
    sheet = np.full((2 * H, 8 * W, 4), 255, dtype=np.uint8)
    light = np.array([0.35, 0.6, 0.72])
    light /= np.linalg.norm(light)
    for col, tris in enumerate(tris_all):
        n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        ln = np.linalg.norm(n, axis=1)
        n = n / np.where(ln == 0, 1, ln)[:, None]
        shade = 0.3 + 0.7 * np.abs(n @ light)
        for row, (ax_u, ax_d, sign, color) in enumerate(
                ((0, 2, 1.0, (120, 150, 210)), (2, 0, -1.0, (200, 140, 120)))):
            img = np.zeros((W * H, 3))
            img[:] = (246, 246, 246)
            zbuf = np.full(W * H, -np.inf)
            cu = (lo[ax_u] + hi[ax_u]) / 2
            u = (sign * (tris[..., ax_u] - cu)) * scale + W / 2
            v = (H - 5) - (tris[..., 1] - lo[1]) * scale
            d = tris[..., ax_d] * (1.0 if row == 0 else 1.0)
            raster(np.stack([u, v], -1), d, shade, W, H, img, zbuf, color)
            blk = sheet[row * H:(row + 1) * H, col * W:(col + 1) * W]
            blk[..., :3] = img.reshape(H, W, 3).astype(np.uint8)
            blk[:, -1, :3] = 200
            blk[-1, :, :3] = 200
    with open(path, "wb") as fh:
        fh.write(txy.png_bytes(8 * W, 2 * H, sheet.tobytes()))
    return [int(f) for f in samples]


# ----------------------------------------------------------------- checks

def trs_matrix(nd, ov):
    nd = dict(nd, **(ov or {}))
    m = np.eye(4)
    m[:3, :3] = gltf._quat_matrix(nd.get("rotation", (0, 0, 0, 1))) * \
        np.array(nd.get("scale", (1, 1, 1)), dtype=np.float64)
    m[:3, 3] = nd.get("translation", (0, 0, 0))
    return m


def keyed_sets(x, clip):
    """Transforms whose T or S the clip keys (body runs the engine reads)."""
    base, at = {}, 0
    for k in "ABCDEF":
        base[k] = at
        at += x.counts[k]
    runs = ["D"] if clip.face else (["A", "B", "C", "E"]
                                    if xap.F32(clip.version) >= xap.V14
                                    else ["A", "B", "E"])
    T, S, comp = set(), set(), set()
    for r in runs:
        for i, b in enumerate(clip.runs[r][:x.counts[r]]):
            if any(bit in b.curves for bit in (0, 1, 2)):
                T.add(base[r] + i)
            if any(bit in b.curves for bit in (6, 7, 8)):
                S.add(base[r] + i)
            if b.flags & 1:
                comp.add(base[r] + i)
    return T, S, comp


def check_animation(js, binc, ai, x, clip, node_of, height, probs):
    an = js["animations"][ai]
    qdev, nonfinite = 0.0, 0
    for ch in an["channels"]:
        v = gltf.accessor_array(js, binc,
                                an["samplers"][ch["sampler"]]["output"])
        nonfinite += int((~np.isfinite(v)).sum())
        if ch["target"]["path"] == "rotation":
            qdev = max(qdev, float(np.abs(np.linalg.norm(
                v.astype(np.float64), axis=1) - 1).max()))
    frames = clip.frames()
    start = int(clip.start)
    nodes = [node_of[j.name] for j in x.joints]
    parent = [j.parent for j in x.joints]
    P = np.zeros((len(frames), len(nodes), 3))
    err = 0.0
    dset = range(sum(x.counts[k] for k in "ABC"),
                 sum(x.counts[k] for k in "ABCD"))
    for fi, f in enumerate(frames):
        ov = gltf.sample_animation(js, binc, ai, (f - start) / 30.0)
        gw = gltf.node_world_matrices(js, ov)
        loc, w, _ = xap.pose(x, clip, float(f))
        for i, nd in enumerate(nodes):
            P[fi, i] = gw[nd][:3, 3]
        if clip.face:   # overlay: compare the run-D locals
            for i in dset:
                gl = trs_matrix(js["nodes"][nodes[i]], ov.get(nodes[i]))
                err = max(err, float(np.abs(gl - loc[i].T).max()))
        else:
            for i, nd in enumerate(nodes):
                err = max(err, float(np.abs(gw[nd] - w[i].T).max()))
    Tk, Sk, comp = keyed_sets(x, clip)
    ptp_bone = 0.0
    n_bone = 0
    for i, p in enumerate(parent):
        if p < 0 or i in Tk or i in Sk or p in Sk or i in comp:
            continue
        dist = np.linalg.norm(P[:, i] - P[:, p], axis=1)
        ptp_bone = max(ptp_bone, float(np.ptp(dist)))
        n_bone += 1
    step = float(np.linalg.norm(np.diff(P, axis=0), axis=2).max()) \
        if len(frames) > 1 else 0.0
    closure = float(np.linalg.norm(P[-1] - P[0], axis=1).max())
    jptp = np.ptp(P, axis=0)                    # (n, 3)
    top = int(np.argmax(jptp.max(axis=1)))
    shear = an.get("extras", {}).get("max_shear_residual", 0.0)
    return {
        "animation": an["name"], "frames": "%d..%d" % (frames[0], frames[-1]),
        "kind": "face" if clip.face else "body",
        "validate_problems": [p for p in probs
                              if p.startswith("animation %d " % ai)],
        "max_quat_norm_dev": qdev, "nonfinite": nonfinite,
        "joint_world_err": err, "joint_world_err_over_height": err / height,
        "bone_len_ptp": ptp_bone, "bones_checked": n_bone,
        "max_step_over_height": step / height,
        "max_step": step,
        "loop_closure": closure,
        "largest_joint_sweep": {"joint": "t%02d" % top,
                                "axis": "xyz"[int(np.argmax(jptp[top]))],
                                "ptp": float(jptp[top].max())},
        "joint8_ptp_xyz": [float(v) for v in jptp[8]],
        "compensated_transforms": len(comp),
        "max_shear_residual": shear,
    }


def main():
    report = {"iso": ISO1, "characters": []}
    lines = []
    for stem, motions in CHARS:
        out = os.path.join(OUTDIR, stem + ".glb")
        cmd = [sys.executable, os.path.join(KIT, "cli.py"), "model", "--iso",
               ISO1, "--path", "model\\char\\pc\\%s.pxy" % stem, "--out", out]
        for path, slots in motions:
            cmd += ["--motion", "%s#%s" % (path, ",".join(map(str, slots)))]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr)
        status = (r.stdout + r.stderr).strip()
        print(status)
        with open(out, "rb") as fh:
            js, binc = gltf.read_glb(fh.read())
        probs = gltf.validate(js, binc)
        with open(os.path.join(DUMP, "model", "char", "pc", stem + ".xhr"),
                  "rb") as fh:
            x = xhr.parse(fh.read())
        node_of = {}
        sk = [i for i, nd in enumerate(js["nodes"])
              if nd.get("name") == "skeleton"][0]
        stack = list(js["nodes"][sk].get("children", ()))
        while stack:
            c = stack.pop()
            node_of[js["nodes"][c]["name"]] = c
            stack.extend(js["nodes"][c].get("children", ()))
        rest = mesh_triangles(js, binc, {}).reshape(-1, 3)
        height = float(np.ptp(rest[:, 1]))
        jw = x.world_matrices()[:, 3, :3]
        cinfo = {"stem": stem, "glb": out, "status": status,
                 "validate_problems_file": probs, "mesh_height": height,
                 "xhr_joint_height": float(np.ptp(jw[:, 1])),
                 "transforms": len(x.joints), "xhr_counts": x.counts,
                 "animations": []}
        xps = {}
        for ai, an in enumerate(js["animations"]):
            # name: "<disc path>#<slots>" plus, since the run-count flag,
            # an optional " [counts ...]" suffix (xap.add_to_glb)
            label, slots = an["name"].split(" [", 1)[0].split("#")
            slot = int(slots.split(",")[0])
            if label not in xps:
                ext = ".fap" if label.endswith("face_all") else ".xap"
                with open(os.path.join(DUMP, *label.split("/")) + ext,
                          "rb") as fh:
                    xps[label] = xap.parse(fh.read())
            clip = xps[label].slots[slot]
            res = check_animation(js, binc, ai, x, clip, node_of, height,
                                  probs)
            png = os.path.join(OUTDIR, "%s_%s_%s.png" % (
                stem, label.replace("/", "-"), slots.replace(",", "-")))
            res["contact_sheet"] = png
            res["sheet_frames"] = contact_sheet(js, binc, ai, clip.frames(),
                                                int(clip.start), png)
            cinfo["animations"].append(res)
            print("  %-26s %s" % (an["name"], json.dumps(
                {k: (round(v, 6) if isinstance(v, float) else v)
                 for k, v in res.items() if k in (
                     "joint_world_err", "bone_len_ptp", "max_step",
                     "loop_closure", "max_quat_norm_dev")})))
        report["characters"].append(cinfo)
    # field slots 0/1/2: observations only, not names from the game
    for c in report["characters"]:
        field = [a for a in c["animations"]
                 if a["animation"].startswith("motion/%s#" % c["stem"])]
        if len(field) == 3:
            steps = [a["max_step_over_height"] for a in field]
            for a, s in zip(field, steps):
                if s < 0.01:
                    a["observation"] = "nearly static (idle-like)"
                elif s == max(steps[1:]):
                    a["observation"] = "periodic, the larger sweep (run-like)"
                else:
                    a["observation"] = "periodic, the smaller sweep (walk-like)"
    with open(os.path.join(OUTDIR, "report.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    write_text(report)


def write_text(report):
    L = ["Episode II motion demo: read-back checks",
         "(.glb read back through gltf.py; engine pose from xap.pose)", ""]
    for c in report["characters"]:
        L.append("%s  (%d transforms, xhr counts %s)" % (
            c["stem"], c["transforms"], c["xhr_counts"]))
        L.append("  " + c["status"].replace("\n", "\n  "))
        L.append("  mesh height %.4f, xhr joint height %.4f; validate() "
                 "problems in the file: %d" % (
                     c["mesh_height"], c["xhr_joint_height"],
                     len(c["validate_problems_file"])))
        for a in c["animations"]:
            L.append("  %s  [%s, frames %s]" % (a["animation"], a["kind"],
                                                a["frames"]))
            L.append("    validate problems %d, max | |q|-1 | %.2e, NaN/Inf %d"
                     % (len(a["validate_problems"]), a["max_quat_norm_dev"],
                        a["nonfinite"]))
            L.append("    joint-world agreement (glb vs xap.pose) %.2e "
                     "(%.2e x height)" % (a["joint_world_err"],
                                          a["joint_world_err_over_height"]))
            L.append("    bone-length ptp %.2e over %d bones without keyed "
                     "T/S; max step %.4f (%.4f x height); loop closure %.4f"
                     % (a["bone_len_ptp"], a["bones_checked"], a["max_step"],
                        a["max_step_over_height"], a["loop_closure"]))
            s = a["largest_joint_sweep"]
            L.append("    largest joint sweep %s %s ptp %.3f; t08 ptp xyz %s;"
                     " compensated transforms %d, max shear residual %.3g"
                     % (s["joint"], s["axis"], s["ptp"],
                        [round(v, 3) for v in a["joint8_ptp_xyz"]],
                        a["compensated_transforms"], a["max_shear_residual"]))
            if a.get("observation"):
                L.append("    observation: %s (not a name from the game)"
                         % a["observation"])
            L.append("    sheet %s (frames %s)" % (
                os.path.basename(a["contact_sheet"]), a["sheet_frames"]))
        L.append("")
    L += ["Notes:",
          "- yamamoto/mot/shio00 and kosm00 are paired with shion00 and "
          "kosmos00 by their names only (a hypothesis; the battle loaders "
          "are not traced).",
          "- jr00: the clips carry B 6/7 and E 21/18 blocks against the "
          "skeleton's B 6 and E 16; the engine takes blocks by index, and so "
          "does the exporter.",
          "- Contact sheets: top row looks along -Z (X right), bottom row "
          "along +X (Z left); 8 evenly spaced frames; meshes skinned from "
          "the read-back .glb.",
          "- The face clip is compared on the run-D locals (an overlay), the "
          "body clips on every transform's world matrix.",
          "",
          "Open points tested on 2026-11-20 (fixed text, not measured by "
          "this run; see the kit's docs/MOTION.md):",
          "- U1 root scale: in all 13 body clips here t00 and t01 scale is "
          "(1,1,1) in every frame (keyed or rest), so nothing shrinks; the "
          "contact sheets agree. Disc-wide, 198 root-0 scale curves (0.5..10) "
          "and 387 root-1 curves (0..2) are not all 1; none in these clips.",
          "- U2 compensation: no compensated transform in the 13 body clips; "
          "the face clip has 6 (run D, flags bit 0) under a parent scale of "
          "1, so no effect. Disc-wide 48 non-uniform cases in 8 battle clips "
          "(bos009, bos020, uro008, uro009, vma001), shear residual up to "
          "0.58 (sweep.txt).",
          "- U3 visibility: all 519 entries on the disc are one pair (0, 1.0): "
          "nothing is ever hidden. Entry count = .pxy node count for uma002 "
          "(16), uma005 (20), uma011 (20), uso002 (3): a lead, not proof. "
          "FUN_00295188's disassembly gives 'first pair with frame >= t', not "
          "'last pair before t'; on this disc the two never differ.",
          "- U5 rate: glTF at 30 fps (the curve rate); the game's tick is "
          "unverified.",
          "- A4 Blender 5.4.0 Alpha: blender_import.txt (all pass); headless "
          "Workbench renders work: <stem>_blender_slot01_f19.png.",
          "- A5 Khronos glTF validator 2.0.0-dev.3.10 (npm, scratch dir, not "
          "a kit dependency): 0 errors, 0 warnings, 0 infos on all three "
          "files (khronos_validator.txt)."]
    with open(os.path.join(OUTDIR, "report.txt"), "w") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
