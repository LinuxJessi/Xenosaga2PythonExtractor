"""Optional: one Workbench render per demo .glb at the middle frame of the
field clip slot 1 (motion/<stem>#01).
    Blender -b --factory-startup --python blender_render.py -- A.glb ..."""
import math
import os
import sys

import bpy
from mathutils import Vector

for path in sys.argv[sys.argv.index("--") + 1:]:
    stem = os.path.splitext(os.path.basename(path))[0]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = 30
    bpy.ops.import_scene.gltf(filepath=path)
    arm = [o for o in bpy.data.objects if o.type == "ARMATURE"][0]
    # the name may carry a " [counts ...]" suffix (clips whose run counts differ)
    want = "motion/%s#01" % stem
    act = [a for a in bpy.data.actions
           if a.name == want or a.name.startswith(want + " [")][0]
    arm.animation_data_create()
    arm.animation_data.action = act
    if hasattr(arm.animation_data, "action_slot") and act.slots:
        arm.animation_data.action_slot = act.slots[0]
    mid = int(round(act.frame_range[1] / 2))
    sc.frame_set(mid)
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 2.0
    cam = bpy.data.objects.new("cam", cam_data)
    sc.collection.objects.link(cam)
    # glTF +Y up becomes Blender +Z; the character faces glTF +Z = Blender -Y
    cam.location = Vector((2.2, -2.2, 0.85))
    cam.rotation_euler = (math.radians(90), 0, math.radians(45))
    sc.camera = cam
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.display.shading.light = "STUDIO"
    sc.display.shading.color_type = "TEXTURE"
    sc.render.resolution_x, sc.render.resolution_y = 480, 600
    sc.render.filepath = os.path.join(os.path.dirname(path),
                                      "%s_blender_slot01_f%02d.png" % (stem, mid))
    bpy.ops.render.render(write_still=True)
    print("rendered", sc.render.filepath)
