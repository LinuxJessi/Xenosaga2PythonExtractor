"""Stand-in for a person painting in an image editor: edit PNGs that `cli.py browse` wrote.

usage: paint.py EDITED_DIR BROWSE_DIR [BROWSE_DIR ...]

Reads only files from the BROWSE_DIRs (`cli.py browse` outputs of the retail disc) and writes
the edited copies, at the same relative paths, under EDITED_DIR (a sparse tree):

1. KOS-MOS's hair, all of it pink (hue 0.92, the `recolor` command's default), in three steps:
   textures_png/chain0/model/char/pc/kosmos00.txy/000_kosmos00_hair_col.png   the hair itself
   textures_png/chain0/model/char/pc/kosmos00.txy/001_kosmos00_hair_spe.png   its shine
   the face pictures 024_kosmos00_face (kosmos00.txy), 007_kosmos01_face (kosmos01.txy) and
   009_kosmos03_face (kosmos03.txy): only their blue pixels (the fringe, the sideburns, the
   forehead marks and the brows); skin, lips and eyes stay as they are.
   The two hair pictures are painted once, in kosmos00.txy; `texture-import` carries them to
   her three other files, which hold the same pictures. Each of her three face pictures is a
   different drawing, so each is painted (kosmos02.txy holds a copy of kosmos01's).
2. textures_png/chain0/model/char/pc/kosmos00.txy/005_kosmos00_ude.png
   an outfit texture (arm pieces), recoloured green: hue 0.33, saturation at least 0.5.
3. xtx/chain0/yajima/titleobj.xtx.png  (the title menu words, a UI sheet)
   opaque pixels tinted gold (R, G, B = lum, 0.8 lum, 0.15 lum), alpha kept exactly.
4. models/chain0/model/char/pc/kosmos00.glb  (the browse .glb, as a Blender user would get it)
   image 009_kosmos00_mune given a yellow ring, written back by rewriting the BIN chunk.

Uses the kit's PNG reader/writer (pngread, txy.png_bytes), gltf.read_glb and numpy.
"""
import colorsys, json, shutil, struct, sys
from pathlib import Path
import numpy as np

KIT = Path(__file__).resolve().parents[3]          # the kit: tools/demo/textures/ -> three folders up
sys.path.insert(0, str(KIT))
import gltf, pngread, txy

dst, srcs = Path(sys.argv[1]), [Path(x) for x in sys.argv[2:]]
if dst.exists():
    shutil.rmtree(dst)


def find(rel):
    return next(s / rel for s in srcs if (s / rel).exists())


def load(rel):
    w, h, rgba = pngread.read_png(find(rel).read_bytes())
    return np.frombuffer(rgba, np.uint8).reshape(h, w, 4).copy()


def save(rel, a):
    p = dst / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(txy.png_bytes(a.shape[1], a.shape[0], a.tobytes()))


PC = Path("textures_png/chain0/model/char/pc")
T = PC / "kosmos00.txy"
PINK = 0.92                                         # the hue `cli.py recolor` uses by default


def pink(rel, only_blue):
    """Turn the hue of a picture's visible pixels to PINK, lightness kept. only_blue: just the
    pixels that are not warm (the hair painted on a face and its soft edge), not the skin."""
    a = load(rel)
    f = a.reshape(-1, 4)
    n = 0
    for p in np.nonzero(f[:, 3] > 0)[0]:
        h, l, s = colorsys.rgb_to_hls(*(f[p, :3] / 255.0))
        if only_blue and int(f[p, 2]) < int(f[p, 0]) - 30:     # warm (skin, lips): red far above blue
            continue
        f[p, :3] = [int(round(v * 255)) for v in colorsys.hls_to_rgb(PINK, l, min(1.0, max(s * 1.5, 0.35)))]
        n += 1
    save(rel, a)
    return n


# 1. hair: all pink (the hair, its shine, and the hair painted on her three face pictures)
print("hair_col: pink over", pink(T / "000_kosmos00_hair_col.png", False), "pixels")
print("hair_spe: pink over", pink(T / "001_kosmos00_hair_spe.png", False), "pixels")
for rel in (T / "024_kosmos00_face.png", PC / "kosmos01.txy" / "007_kosmos01_face.png",
            PC / "kosmos03.txy" / "009_kosmos03_face.png"):
    print(f"{rel.stem[4:]}: pink over", pink(rel, True), "blue pixels")
# 2. ude: green
a = load(T / "005_kosmos00_ude.png")
f = a.reshape(-1, 4)
for p in np.nonzero(f[:, 3] > 0)[0]:
    h, l, s = colorsys.rgb_to_hls(*(f[p, :3] / 255.0))
    f[p, :3] = [int(round(v * 255)) for v in colorsys.hls_to_rgb(0.33, l, max(s, 0.5))]
save(T / "005_kosmos00_ude.png", a)
print("ude: green over", int((f[:, 3] > 0).sum()), "pixels")
# 3. titleobj: gold
X = Path("xtx/chain0/yajima/titleobj.xtx.png")
a = load(X)
op = a[..., 3] > 0
lum = a[..., :3].astype(np.float64).mean(-1)
gold = np.stack([lum, 0.8 * lum, 0.15 * lum], -1)
a[op, :3] = np.clip(np.round(gold[op]), 0, 255).astype(np.uint8)
save(X, a)
print("titleobj: gold over", int(op.sum()), "opaque pixels")
# 4. glb: mune image ringed
G = Path("models/chain0/model/char/pc/kosmos00.glb")
js, binc = gltf.read_glb(find(G).read_bytes())
names = [im.get("name") for im in js["images"]]
k = names.index("009_kosmos00_mune")
view = js["bufferViews"][js["images"][k]["bufferView"]]
png = binc[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
w, h, rgba = pngread.read_png(bytes(png))
a = np.frombuffer(rgba, np.uint8).reshape(h, w, 4).copy()
yy, xx = np.mgrid[0:h, 0:w]
ring = (abs(np.hypot(xx - w / 2, yy - h / 2) - w / 4) < 5) & (a[..., 3] > 0)
a[ring, :3] = (255, 230, 0)
blobs = [bytes(binc[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]]) for v in js["bufferViews"]]
blobs[js["images"][k]["bufferView"]] = txy.png_bytes(w, h, a.tobytes())
out = bytearray()
for v, b in zip(js["bufferViews"], blobs):
    out += bytes(-len(out) % 4)
    v["byteOffset"] = len(out)
    v["byteLength"] = len(b)
    out += b
out += bytes(-len(out) % 4)
js["buffers"][0]["byteLength"] = len(out)
j = json.dumps(js, separators=(",", ":")).encode()
j += b" " * (-len(j) % 4)
glb = (struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(j) + 8 + len(out)) + struct.pack("<I4s", len(j), b"JSON") + j
       + struct.pack("<I4s", len(out), b"BIN\0") + bytes(out))
(dst / G).parent.mkdir(parents=True, exist_ok=True)
(dst / G).write_bytes(glb)
print("glb: image", k, names[k], "yellow ring over", int(ring.sum()), "pixels")
