# ~google/gemini-pro-latest

### (a) Code Bugs and Silent Failures

1. **`pxy.py` (`to_glb`): Degenerate triangles computed but never filtered.**
   In `to_glb`, `keep = (tri[:, 0] != tri[:, 1]) & ...` is computed after welding, but `tri` is never sliced by `keep` (`tri = tri[keep]` is missing). Degenerate triangles are emitted into the GLB and counted in `n_tris`.
   *Evidence:* Inspect lines 581–585 of `pxy.py`; check exported GLBs in glTF Validator for `MESH_PRIMITIVE_DEGENERATE_TRIANGLE`.

2. **`pxy.py` (`skeleton_from`): Overwriting parent `world` matrices corrupts child local transforms.**
   In `skeleton_from`, `world[j + 2] = bind[j]` overwrites the world matrix for used joints, while unused joints keep their `.xhr` `world` matrix. Later in `to_glb`, `local = world[i] @ np.linalg.inv(pw)` computes local transforms mixing `.pxy` bind poses and `.xhr` rest poses across parent-child boundaries. In the 15 files where rest != bind, any unused child of a modified joint (or vice versa) gets a corrupted local transform.
   *Evidence:* Export one of the 15 mismatched files; inspect leaf/helper bones (e.g., physics chains in run E hanging off run B) in Blender for dislocated offsets.

3. **`pxy.py` (`to_glb`): Unskinned batches in partially-skinned nodes ignore `nd.matrix`.**
   If a node contains both skinned and unskinned batches (`node_skinned = True`), the unskinned batches are bound to `root_joint` (which has an identity world transform), and the glTF mesh node is emitted without `nd.matrix`. If `nd.matrix != IDENT`, the unskinned batches are misplaced. (Note: `check()` only flags *skinned* batches under a non-identity node matrix, not unskinned ones sharing that node).
   *Evidence:* Search the 944 files for any node with `nd.matrix != IDENT` that mixes `skin_mode == 0` and `skin_mode != 0` batches.

4. **`pxy.py` (`to_glb`): Vertex colors are clamped at 128 (`1.0`), destroying PS2 overbright modulation.**
   PS2 GS `TEXA`/`RGBAQ` texture modulation uses `0x80 = 1.0` and `0x81..0xFF` for up to `2.0x` brightening. `np.clip(c / 128.0, 0.0, 1.0)` silently clamps anything above 128 to 1.0.
   *Evidence:* Histogram `b.colors` across all 944 files; if values `> 128` exist, clamping alters the lighting/albedo.

5. **`pxy.py` (`to_glb`): `_weld` hashes raw `float32` bit patterns (`np.void`).**
   `-0.0` (`0x80000000`) and `+0.0` (`0x00000000`) will not weld together. Furthermore, before welding, unused joint slots are zeroed (`jm = np.where(w > 0, jm, 0)`), which is good, but if `b.st` has different bit-0 strip-restart flags on otherwise identical UV coordinates (`T`), those vertices will fail to weld because bit 0 of the float mantissa differs by 1 ULP.
   *Evidence:* Check if `raw[n + 1:, 3] & ~1` is masked before converting `b.st` to float in `_decode_mesh` (it is not: `f[n + 1:, 3]` retains the LSB flag in the float).

6. **`pxy.py` (`batch_joints`): Unchecked `255` termination assumption.**
   The comment states "once ended, every later byte must be 255 too", enforced by `ended = np.logical_or.accumulate(~used, axis=1); used &= ~ended`. It does *not* verify that later bytes are actually 255; it silently masks them out. If a slot byte is `0..254` after a `255`, it is silently dropped without raising `PxyError`.
   *Evidence:* Assert `(s[ended] == 255).all()` in `batch_joints` across all 944 files.

7. **`vif.py` (`walk`): Incorrect MPG alignment check.**
   Line 132: `if p & 4 == 0: raise VifError("MPG ... not 8-byte aligned")` works by coincidence because `p` is 4-byte aligned and the MPG payload starts at `p + 4`, so `p & 4 == 0` means `(p + 4) & 7 != 0`. However, on PS2 hardware, VIF `MPG` does *not* require 8-byte alignment in the VIF stream (the VIF shifts and packs 32-bit words into 64-bit VU instructions automatically).
   *Evidence:* Sony EE Users Manual, VIF chapter (MPG command framing).

---

### (b) Triangle Winding and Normals

* **PS2-side mechanisms:**
  1. **Hardware has no backface culling:** The PS2 GS rasterizer draws all triangles two-sided regardless of screen-space winding. Backface culling is purely optional and must be done in software by VU1 microcode (computing a 2D cross product in screen space and setting the ADC bit at bit 15 of $Z$ or $W$).
  2. **Per-item VU cull sign (`Item.cull`):** Your own comment in `Item.__init__` notes that `+0x1C` (`0` = two-sided, `1` vs `2-3` = opposite signs) is sent by `FUN_001ce4c4` to VU1 address `0x3C6`. Whole draws are reversed because the asset was mirrored or authored with opposite winding and the engine flips `Item.cull` (or sets it to `0`) to compensate.
  3. **Mirrored node matrices:** A static node with $\det(\text{node\_matrix}) < 0$ flips geometric winding in world space relative to local space.
* **How the exporter should decide winding:**
  Do **not** flip individual triangles by vertex normals (which breaks smooth tangent spaces and creates cracks along hard/skewed normal seams). Instead:
  1. Disassemble VU1 programs 0–4 where address `0x3C6` (`Item.cull`) is read.
  2. Determine the exact strip parity rule and sign flip applied by the microcode for `cull == 1`, `2`, and `3`.
  3. Apply that deterministic per-item winding rule to the strip indices, and only set `doubleSided: True` on the glTF material when `Item.cull == 0`.

---

### (c) Strip Restart Rule and Header Triangle Count

* **Not a complete proof of the strip rule:**
  Reproducing `p.triangle_count` in 944/944 files proves **only the cardinality** of the suppression rule (that each flagged vertex beyond index 1 reduces the triangle count of a batch of length $n$ by 2, and that no restarts occur at $k = n-1$ or $k=0,1$).
* **What could still be wrong:**
  1. **Alternation parity:** It is completely blind to whether even/odd triangles wind `(k-2, k-1, k)` vs `(k-1, k-2, k)`, or whether parity resets at each restart `start[k]` vs continuing globally across the batch `k & 1` (many VU1 microprograms just toggle a register every vertex without resetting on ADC).
  2. **Off-by-one shift:** If the flag on vertex $k$ actually suppresses $(k-1, k)$ instead of $(k, k+1)$, the total count is identical so long as the flag never lands on index 1 or $n-1$.
* *Evidence to settle:* Check `strips_one_facing` under global parity (`k & 1`) vs strip-reset parity (`(k - start[k]) & 1`), and verify the exact `IADDIU` / `IBEQ` loop in VU1 microcode that writes bit 15 of the GIF vertex kick.

---

### (d) Settling Unverified Items

* **Settled from files and EE/VU1 code alone:**
  1. **The 15 mismatched skeletons & Euler order:** Disassemble `FUN_00290ab8` (and its callees in `SLUS_208.92`) that build the local matrices from the `.xhr` records. That code contains the exact sin/cos calls, matrix multiplication order (`euler_rows`), and record offsets, eliminating any need for statistical fitting and immediately showing why the 15 skeletons diverge.
  2. **Extreme UVs (`|uv| <= 11,175`):** Check `st.wms` and `st.wmt` (from the `CLAMP` register) and `st.textured` on those two batches. If `tbw == 0` or the shader ID in VU1 ignores `ST` (e.g., environment mapping or untextured flat shading), the `w` components of pos/nrm contain uninitialized exporter garbage or non-UV attributes. Also check if `CLAMP` mode is `2` (`REGION_CLAMP`) or `3` (`REGION_REPEAT`), where UV coordinates are scaled/offset against `MINU/MAXU/MINV/MAXV` in bits 4..43 of `CLAMP`.
  3. **Cull modes:** Settled by tracing `0x3C6` in the VU1 microprograms (see (b)).
  4. **Handedness:** Settled by inspecting the view/projection matrix setup in the EE executable (`SLUS_208.92`) and checking whether a known asymmetric character model (e.g., asymmetrical costume details or text on textures) is mirrored in Blender.
* **Requires a GS dump (PCSX2) or full shader/GIF trace:**
  1. **Colour scale (`128 = 1.0`) and Alpha modes (`ALPHA_1`, `TEST_1`):** While the GIF register block in the material batch (`q[2..2+nreg]`) contains the raw GS register writes (`TEX0_1`, `CLAMP_1`, `ALPHA_1`, `TEST_1`, `TEXA`), confirming how multi-pass shaders and framebuffer alpha (`FBA`) interact in practice is fastest and least error-prone with a single PCSX2 GS dump.

---

### (e) Circularity of the 1.14e-06 Rest-Pose Skinning Test

* **It is completely circular.**
  In `skeleton_from`, for every used joint $j$, you explicitly set `world[j + 2] = np.linalg.inv(p.inv_bind[j])` and export `ibm = p.inv_bind[j].T`. When reading the GLB back in rest pose, glTF computes:
  $$\text{jointMatrix}_j = \text{worldNode}_j \cdot \text{IBM}_j = \text{invBind}_j^{-1} \cdot \text{invBind}_j = I$$
  Because $\sum_k w_k = 1$ (and you normalized `w = w / ws` in `to_glb`), the linear blend $\sum_k w_k (I \cdot v)$ equals $v$ for **any** invertible matrix and **any** joint assignment. Even if `batch_joints` mapped every vertex to the wrong bone, or if `inv_bind` were random invertible matrices, this test would still pass to `float32` precision (`~1e-6`).
* **Non-circular tests:**
  1. **`.xhr` vs `.pxy` agreement without overwriting:** Measure `max |p.inv_bind[j] @ x.world_matrices()[j + 2] - I|` across all used joints *before* `skeleton_from` overwrites `world` (handled by `xhr.bind_error`, which confirms the `.xhr` hierarchy and joint index $+2$ offset, though it still does not test vertex-to-joint palette mapping).
  2. **Non-trivial pose continuity test:** Apply a non-identity rotation to a single joint (or load an actual `.xan` animation frame), skin the mesh, and verify that triangle edge lengths across joint boundaries do not explode (which catches wrong palette-to-joint mappings in `batch_joints` when `n_j > 64`).
