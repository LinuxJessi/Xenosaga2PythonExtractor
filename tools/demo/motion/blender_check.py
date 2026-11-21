"""Blender background check of the demo .glb files (A4):
    Blender -b --factory-startup --python blender_check.py -- A.glb B.glb ...
Imports each file at 30 fps and prints actions, bones and frame ranges."""
import json
import struct
import sys

import bpy

files = sys.argv[sys.argv.index("--") + 1:]
ok_all = True
print("Blender", bpy.app.version_string)
for path in files:
    with open(path, "rb") as fh:
        data = fh.read()
    n = struct.unpack_from("<I", data, 12)[0]
    js = json.loads(data[20:20 + n])
    anims = js.get("animations", [])
    joints = len(js["skins"][0]["joints"]) if js.get("skins") else 0
    names = [nd.get("name", "") for nd in js["nodes"]]
    n_t = sum(1 for nm in names if len(nm) == 3 and nm[0] == "t" and nm[1:].isdigit())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps, sc.render.fps_base = 30, 1.0
    try:
        bpy.ops.import_scene.gltf(filepath=path)
    except Exception as e:                  # noqa: BLE001
        print("FAIL import", path, repr(e))
        ok_all = False
        continue
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    bones = len(arms[0].data.bones) if arms else 0
    t_bones = sum(1 for bn in arms[0].data.bones if bn.name in names
                  and len(bn.name) == 3 and bn.name[0] == "t") if arms else 0
    acts = list(bpy.data.actions)
    print("file %s: %d glTF animations, %d Blender actions; armature bones %d "
          "(%d named t00.., the rest is the 'skeleton' root), transforms %d, "
          "skin joints %d" % (path.rsplit("/", 1)[-1], len(anims), len(acts), bones,
                              t_bones, n_t, joints))
    want = {}
    for a in anims:
        ex = a.get("extras", {})
        want[a["name"]] = (int(ex["end"]) - int(ex["start"]))
    bad = 0
    for act in acts:
        fr = tuple(act.frame_range)
        key = act.name
        match = [nm for nm in want if key == nm or key.startswith(nm)]
        exp = want[match[0]] if match else None
        good = exp is not None and abs(fr[0]) < 1e-3 and abs(fr[1] - exp) < 1e-3
        bad += not good
        print("  action %-34s frames %.2f..%.2f  expected 0..%s  %s" % (
            key, fr[0], fr[1], exp, "ok" if good else "MISMATCH"))
    cond = (len(acts) == len(anims)) and t_bones == n_t and bad == 0
    print("  check: actions == animations %s, t-bones %d == transforms %d, frame ranges %s -> %s" % (
        len(acts) == len(anims), t_bones, n_t, "ok" if not bad else "%d bad" % bad,
        "PASS" if cond else "FAIL"))
    ok_all &= cond
print("ALL PASS" if ok_all else "SOME FAILED")
