"""blender -b --factory-startup --python render_bind_pose.py -- OUTDIR A.glb B.glb ...
Bind-pose Workbench renders (front and back, textured) of each .glb."""
import math
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
outdir, paths = args[0], args[1:]
for path in paths:
    stem = os.path.splitext(os.path.basename(path))[0]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    bpy.ops.import_scene.gltf(filepath=path)
    for o in bpy.data.objects:
        if o.animation_data:
            o.animation_data.action = None
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    ctr = (lo + hi) / 2
    size = max(hi.z - lo.z, hi.x - lo.x) * 1.08
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = size
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "TEXTURE"
    sc.render.resolution_x, sc.render.resolution_y = 600, 800
    sc.render.film_transparent = False
    sc.world = bpy.data.worlds.new("w") if sc.world is None else sc.world
    for view, sign in (("front", -1), ("back", 1)):
        d = 4.0 * size
        cam.location = Vector((ctr.x, ctr.y + sign * d, ctr.z))
        cam.rotation_euler = (math.radians(90), 0, 0 if sign < 0 else math.radians(180))
        sc.render.filepath = os.path.join(outdir, f"{stem}.{view}.png")
        bpy.ops.render.render(write_still=True)
