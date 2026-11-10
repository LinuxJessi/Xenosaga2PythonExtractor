# openai/gpt-6-astra-pro

**Do not merge as a verified decoder yet.** The supplied code contains silent fidelity changes, and several headline tests validate internal consistency rather than the game’s behavior. Findings below distinguish definite code behavior from unresolved interpretation.

1. **[a,e — Definite] Rest-pose skinning agreement is largely circular.**  
   `skeleton_from()` replaces every referenced joint’s world matrix with `inverse(inv_bind)`. Consequently, `world × inverseBind = I` by construction. Incorrect joint assignments and many incorrect weight distributions still reproduce the original positions.
   **Evidence required:** Compare exported deformation against independently executed game skinning at multiple **non-bind poses**, including noncommuting joint rotations/translations, rigid vertices, four-influence vertices, and palette remapping. Compare normals too. The current test establishes, at most, round-trip consistency.

2. **[a — Definite] Winding is altered without establishing the source front face.**  
   `to_glb()` reverses each triangle according to averaged shading normals. Those normals need not identify the intended front side, particularly on thin surfaces, hard edges, or authored two-sided geometry. Double-sided rendering does not make the change harmless: back-face normal handling and shading can change.
   **Evidence required:** Preserve source indices in a diagnostic export and compare ordered triangles and culling decisions with VU output. Remove normal-based correction from the faithful export path.

3. **[b — Unresolved] The code already identifies a likely per-item culling input but ignores it.**  
   `Item.cull` documents opposite cull signs passed through `FUN_001ce4c4` to VU address `0x3C6`; export never uses it. PS2 GS has no ordinary fixed-function back-face-cull switch: any such culling is implemented upstream, commonly in VU code.
   **Evidence required:** Trace that value through every relevant VU variant into signed-area/clip tests and emitted GIF vertex order/ADC decisions. Export winding from that ordering plus the established front-face convention; export two-sidedness from actual draw state.

4. **[b — Qualification] Mirroring can reverse rendered facing, but cannot explain this raw-space statistic by itself.**  
   `check()` compares untransformed `b.pos` with untransformed `b.nrm`. Negative-determinant node matrices are absent from that calculation. They can explain later screen-space reversal, not the reported raw-space disagreement unless reflection is already baked into one attribute set.
   **Evidence required:** Correlate disagreement by draw with `Item.cull`, node determinant, batch kind, and VU program. Include view/projection and viewport orientation when interpreting screen-space cull signs. Apply reflection corrections exactly once, considering whether the transform is retained or baked.

5. **[a,c — Unresolved] Triangle counts do not prove restart placement or parity.**  
   Suppressing two triangles around each isolated restart can match the total even if the suppressed triangles are shifted. Reversing every triangle preserves the count. Resetting strip parity incorrectly also preserves it.
   `triangles()` is internally consistent **if** a flag means “new strip begins at this vertex,” but a GS ADC drawing kick is not automatically that semantic.
   **Evidence required:** Trace the flag through VU emission. Add odd-index restarts, adjacent flags, terminal flags, short batches, and batch-boundary cases. The synthetic test restarts only at index 4, so it cannot distinguish global from reset parity. The 944-file match strongly supports the omission count, not the complete topology rule.

6. **[a — Unresolved] Matrix algebra is locally consistent; byte serialization is not reviewable here.**  
   Row-vector `world = local @ parent`, `local = world @ inverse(parent)`, and conversion to column-vector matrices by transpose are sound. Row-vector skinning should use `inverseBind @ world`; the equivalent column-vector product is `world.T @ inverseBind.T`.
   However, `gltf.py` is absent. Passing `m.T` does not prove the MAT4 accessor is written column-major.
   **Evidence required:** Inspect actual GLB bytes with an independent reader using an asymmetric rotation-plus-translation matrix. Check glTF matrix translation entries 12–14 and run the Khronos validator. A writer and reader sharing the same packing mistake can pass a self-test.

7. **[a — Definite risk] Mixed static/skinned files lose static node transforms.**  
   File-wide `skinned` causes **all** batches to receive skin attributes and sets every exported mesh node’s `node_skinned = True`. The skinned node branch omits `nd.matrix`; unskinned batches instead bind to an identity dummy joint. Thus an otherwise static node’s nonidentity matrix is dropped.
   **Evidence required:** A fixture containing one skinned draw and one unskinned draw under a translated/rotated node. Preserve the latter’s transformation by baking it correctly or exporting it separately.

8. **[a,e — Unresolved] Skeleton correction can hide layout errors and introduce lossy TRS decomposition.**  
   Referenced joints are forced to bind worlds, while other joints retain `.xhr` worlds. Recomputed locals may differ from the authored hierarchy and may contain shear, which ordinary glTF TRS cannot represent. `trs_from_matrix()` is unavailable for review.
   **Evidence required:** Reconstruct every exported local from its emitted TRS and measure residuals, including negative/nonuniform scales. Investigate the 15 discrepancies by run type, parent, and record offset using the engine’s transform evaluator. Do not classify them as alternate poses merely because replacement makes the mesh stationary.

9. **[a — Definite] Unsupported skin modes and malformed influence lists are accepted.**  
   `_decode_mesh()` accepts modes 6, 10, …, 30 as generic weighted modes despite claiming only 0/2/4/8. `batch_joints()` says subsequent bytes must be 255 after a terminator, but silently ignores them instead. The `n_joint_matrices <= 64` direct-index rule also lacks evidence in this submission.
   **Evidence required:** Reject unsupported modes and verify the intended terminator policy. Trace palette upload/lookup in the engine, especially around 64/65 matrices. Mutation tests should exercise these cases.

10. **[a,d — Definite/unresolved] UV extraction retains the embedded flag and approximates GS addressing.**  
    `b.st` reads T directly as float without clearing its flag bit. This is normally a one-ULP error, not an explanation for UVs of 11,175. More importantly, region-clamp and region-repeat are mapped to ordinary clamp/repeat while their MIN/MAX fields are ignored. Region-repeat’s bitwise addressing is not generally ordinary wrapping.
    **Evidence required:** Follow S/T/Q generation and `PRIM.FST`, decode all CLAMP fields, and derive the effective texel mapping. Explain the two outlier batches against that mapping. Clear the metadata bit if the VU does so. Test UV orientation with an asymmetric numbered texture; no universal “PS2 requires V flip” rule is justified.

11. **[a,d — Definite] Colour export irreversibly clips possible modulation above unity.**  
    `np.clip(c / 128.0, 0, 1)` maps every byte from 128 through 255 to 1. Even if 128 is neutral modulation, this discards brightness above neutral before texture multiplication. RGB modulation and alpha/blend-factor interpretation also need not share one export rule.
    **Evidence required:** Trace RGBA construction, lighting, TEX0 texture function/component control, and blending. Compare representative 64/128/192/255 values through the complete pipeline. Bake or explicitly document unsupported overbright behavior rather than silently clipping it.

12. **[a,d — Definite] Alpha behavior is guessed, and material deduplication omits relevant state.**  
    `_alpha_mode()` uses group names, not GS alpha-test/blend state. `MaterialState.key` omits even the parsed `alpha`, allowing otherwise identical states with different ALPHA registers to merge. TEX1 and further shader parameters are ignored.
    **Evidence required:** Decode actual GIF register identities and relevant `PRIM/PRMODE`, `TEST`, `ALPHA`, and texture state. Include every export-affecting field in material identity. Map only representable behavior to OPAQUE/MASK/BLEND; additive and other GS equations may require baking or an explicit approximation.

13. **[a — Definite] Texture linkage is a best-effort choice, not a strict match.**  
    `link_textures()` accepts the first address/format candidate even when dimensions or CLUT disagree. Equal candidates are silently resolved by input order. `check()` detects dimension mismatch but not palette mismatch. Material deduplication also does not include the resolved entry.
    **Evidence required:** Require a unique valid address/format/size/CLUT match, including relevant CLUT interpretation and upload lifetime where addresses are reused. Test duplicate-address textures with distinct palettes. Missing or ambiguous matches must stop faithful export.

14. **[a — Definite] Numeric gates can pass NaNs, and failed gates do not block writing.**  
    Comparisons such as `abs(length - 1) > tolerance`, bounds tests, and negative-weight checks do not reliably reject NaNs. Normal normalization and weight renormalization can conceal bad input. `main()` writes the GLB even when `r["failed"]` is nonempty; `to_glb()` performs no prerequisite check.
    **Evidence required:** Explicit finite-value checks for all attributes and matrices; affine/invertibility checks where required; mandatory validation before output. Inject NaNs, infinities, zero/singular matrices, and invalid weights. Separate “faithful export” from an explicitly requested repair mode.

15. **[a — Definite] Malformed input can escape the advertised error handling.**  
    Examples:
    - `vif.walk()` does not enforce `0 <= start <= end <= len(buf)`; negative offsets and oversized ends can produce wrong reads or `struct.error`.
    - Magic-only `.pxy`/`.xhr` inputs reach unchecked header reads.
    - Many XHR records are read before their full spans are checked.
    - The final variable-length E record can extend beyond EOF without rejection when F is absent.
    - Recursive parent resolution can hit Python’s recursion limit before its cycle guard.
    
    **Evidence required:** Preflight all spans and counts, validate full E extents, use iterative cycle detection, and fuzz truncations/offsets/counts. Expected failures should consistently be domain exceptions, not `IndexError`, `struct.error`, or `RecursionError`.

16. **[a — Scope] The VIF framing result does not prove execution-state correctness.**  
    `_decode_draw()` resets its assumed state per draw; it does not execute VIF/VU memory, TOPS swapping, or microprogram continuation. Also, standalone `vif.walk()` accepts many commands outside the claimed corpus subset; the subset restriction lives in `_decode_draw()`. Zero UNPACK padding is an exporter/corpus convention, not a general VIF validity requirement.
    **Evidence required:** Document and verify engine initialization, resident matrices/constants, selected program, and MSCNT entry/continuation behavior for each draw class. Differential-test executed VU memory and packets, not just stream boundaries.

17. **[a — Coverage] Programs 1/3/4 remain unproved; program 2 is unreviewed.**  
    Similar instruction patterns do not establish equivalent addressing, flag interpretation, skinning, UV generation, or culling. Three Blender spot-checks cannot cover those branches.
    **Evidence required:** Publish a coverage inventory mapping files/draws to microprogram variants and relevant modes. Execute each used variant against decoder predictions. If program 2 is unused, establish that from program-selection logic rather than its absence in sampled renders.

18. **[d — Evidence boundary] A GS dump is useful, but it cannot settle everything.**  
    - **Files plus VU/engine code:** Can settle record layout, flag propagation, matrix indexing/order, Euler evaluation, UV arithmetic, and constant colour scaling—provided the relevant CPU setup code and runtime inputs are understood.
    - **Runtime GIF/GS capture:** Resolves inherited texture, CLUT, sampler, alpha-test, blend, and emitted primitive state when assets omit them. Capture state history, not merely a screenshot.
    - **VU/EE memory or execution trace:** Needed for authoritative joint matrices, palette uploads, cull inputs, and CPU-side skeleton evaluation. A final GS dump contains already transformed output and cannot uniquely identify those causes.
    
    **Acceptance evidence:** An independent dynamic-pose comparison, ordered primitive comparison, and representative state captures covering the unresolved variants—not another inverse-bind cancellation test.
