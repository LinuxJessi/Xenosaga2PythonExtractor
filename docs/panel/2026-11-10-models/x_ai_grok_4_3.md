# x-ai/grok-4.3

1. triangles() restart/suppression logic risks off-by-one on first restart or n<3: evidence is any batch where computed tris != header count after the 944/944 check, or visual seam on a known strip.

2. _signed(e["w/h"]) + _size_ok tolerance of 1e-3 silently accepts mismatched texture scaling: evidence is a .txy entry where |m.sw*tex_w - signed| is 0.5-0.9 yet still links.

3. skeleton_from forces world[j+2] = invBind[j] for used joints, making 1.14e-6 rest-pose match circular: evidence is identical worst-case error when the same .pxy inv_bind matrices are fed back through the identical path.

4. euler_rows assumes X then Y then Z with row vectors; fitted on one file only: evidence is any of the 15 differing rest-pose skeletons where re-deriving Euler from world matrices yields different angles.

5. Batch header flags &0x1F and skin_mode==2/4/8 path never assert on reserved bits or >4 weights: evidence is a malformed batch that still decodes and produces a glTF without PxyError.

6. No GS register dump path for cull/alpha/TWOPASS/colour scale; all decisions made from .pxy alone: evidence is any draw whose item.cull !=0 or material alpha !=0x1F0003FF that renders differently on real hardware. 

7. UVs written as S/SW, T/TW without possible page/region-repeat or sign flip: evidence is the two batches with |uv|>11k producing visible texture shear once loaded in a game engine.
