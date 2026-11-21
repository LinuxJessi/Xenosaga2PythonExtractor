"""Front-page preview: KOS-MOS and Shion side by side playing their field clip
slot 1 (motion/<stem>#01, walk-like), EEVEE with the glTF materials (Material
shading), N frames over one loop, transparent film.
    blender -b --factory-startup --python render_motion_preview.py -- OUTDIR N kosmos00.glb shion00.glb
The front page's loop is N = 12 frames, cropped and joined into a GIF at about
106 ms per frame (30 fps)."""
import math
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:]
out, n, paths = args[0], int(args[1]), args[2:]
bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.fps = 30
length = None
for i, path in enumerate(paths):
    stem = os.path.splitext(os.path.basename(path))[0]
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    arm = [o for o in new if o.type == "ARMATURE"][0]
    root = arm
    while root.parent:
        root = root.parent
    # side by side along Blender X (the characters face -Y)
    root.location.x += (i - (len(paths) - 1) / 2) * 0.75
    want = "motion/%s#01" % stem
    act = [a for a in bpy.data.actions
           if a.name == want or a.name.startswith(want + " [")][0]
    arm.animation_data_create()
    arm.animation_data.action = act
    if hasattr(arm.animation_data, "action_slot") and act.slots:
        arm.animation_data.action_slot = act.slots[0]
    end = int(round(act.frame_range[1]))
    length = end if length is None else min(length, end)
    print("clip", stem, act.name, "frames", tuple(act.frame_range))

cam_data = bpy.data.cameras.new("cam")
cam_data.type = "ORTHO"
cam_data.ortho_scale = 2.0
cam = bpy.data.objects.new("cam", cam_data)
sc.collection.objects.link(cam)
cam.location = Vector((1.6, -3.0, 0.88))
d = Vector((0, 0, 0.88)) - cam.location
cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
sc.camera = cam

# soft key + fill + world light so the textures read
for name, rot, energy in (("key", (50, 0, 30), 3.5), ("fill", (70, 0, -120), 1.2)):
    ld = bpy.data.lights.new(name, "SUN")
    ld.energy = energy
    lo = bpy.data.objects.new(name, ld)
    lo.rotation_euler = tuple(math.radians(a) for a in rot)
    sc.collection.objects.link(lo)
world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.6, 0.6, 0.62, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.8
sc.world = world

engines = [e.identifier for e in
           bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
print("engine", sc.render.engine)
try:
    sc.eevee.taa_render_samples = 16
except AttributeError:
    pass
sc.view_settings.view_transform = "Standard"
sc.render.film_transparent = True
sc.render.resolution_x, sc.render.resolution_y = 520, 520
sc.render.image_settings.file_format = "PNG"
sc.render.image_settings.color_mode = "RGBA"
os.makedirs(out, exist_ok=True)
for k in range(n):
    f = int(round(k * length / n))
    sc.frame_set(f)
    sc.render.filepath = os.path.join(out, "frame_%02d_f%02d.png" % (k, f))
    bpy.ops.render.render(write_still=True)
    print("rendered", sc.render.filepath)
