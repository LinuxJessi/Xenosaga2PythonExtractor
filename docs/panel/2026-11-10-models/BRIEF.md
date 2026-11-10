ROUND 6: review a new decoder for a PS2 game's 3D models (Xenosaga Episode II `.pxy` meshes and `.xhr` skeletons -> glTF .glb) before it is merged and published. Written by an automated agent from the game's VIF1 streams and VU1 microcode; independently spot-checked: three models (two characters, one enemy mech) exported and rendered in Blender look correct, textured, with an armature.

CLAIMS (the agent's numbers, all 944 files of disc 1)
- Framing: raw VIF1, no DMA tags; a draw list {offset, qwords} tiles the stream exactly. Commands seen: STCYCL cl1/wl1, UNPACK V4-32 / V4-8 / V2-32 (all unsigned, TOPS-relative, unmasked), MSCNT, trailing NOP. The walker hard-fails on anything else; 0 failures in 3.58 M commands.
- Mesh batch layout (TOP-relative, n <= 30 vertices): 0 header (x = n|0x8000, y = kind, w = flags); 1.. n positions xyz with S in w; 1+n.. n normals xyz with T in w and a strip-restart flag in the raw low bit; 1+2n RGBA bytes (n rows if flags bit 0 else 1); 1+4n weights; 1+5n four palette bytes per vertex (slot*4, 255 ends the list). Read from VU1 program 0 instruction by instruction; programs 1, 3, 4 only pattern-checked; program 2 not studied.
- Restart flag: a flagged vertex suppresses the triangle ending at itself and at the next vertex; this rule reproduces a triangle count stored in the file header in 944/944 files (5,587,995 triangles).
- Skinning: mode = flags & 0x1E: 0 none, 2 one matrix per vertex, 4/8 weighted up to four. Skin matrix = world[joint] o inverseBind[joint]. Exported skinned GLBs, read back and skinned the glTF way in rest pose, reproduce the static positions to 1.14e-06 worst case over 563 files.
- Gates: normals unit length 8,949,175/8,949,175; weights sum to 1; all indices in range; all vertices inside the draw item's box; every textured material batch finds a texture entry matching GS address, format and size.
- Weak spots the agent reports: only 96.5 % of triangles face the way their vertex normals say, and whole draws are reversed, so the exporter orients triangles by normals and marks materials double-sided; UVs: 30 % in [0,1], 99.8 % in [-8,8], two batches with |uv| up to 11,175 unexplained; 15 of 559 skeletons have a rest pose that differs from the bind pose on some joints (unresolved: other bind pose, or wrong record layout); handedness, colour scale (128 = 1.0), alpha modes and cull modes are not verified; the Euler order of the skeleton records was fitted on one file and then tested on all.

QUESTIONS
(a) Bugs in the code below: anything that would produce a wrong mesh silently (off-by-one in strip assembly, sign extension, wrong matrix order/handedness, column- vs row-major in the glTF inverse bind matrices, UV flip, colour scale), or a crash on malformed input.
(b) The winding result (strips internally consistent but 3.5 % of triangles, and whole draws, opposite to their normals): what PS2-side mechanisms explain that (per-draw cull flip, mirrored instances with negative-determinant node matrices, a sign in the VU clip/cull code, two-sided materials), and how should an exporter decide winding correctly instead of using normals?
(c) Is "the restart rule reproduces the header triangle count in 944/944 files" strong evidence the strip rule is right, or could a wrong rule also reproduce it?
(d) Which of the unverified items (handedness, colour scale, alpha, UV range, the 15 skeletons) can be settled from the files and microcode alone, and how? Which need a GS dump from an emulator?
(e) Is 1.14e-06 rest-pose agreement a real proof of skinning correctness, or is it circular (the same matrices used both ways)? What test would not be circular?

==== vif.py ====
#!/usr/bin/env python3
"""PS2 VIF command stream walker (strict).

The Episode II `.pxy` meshes carry their geometry as a raw VIF1 command
