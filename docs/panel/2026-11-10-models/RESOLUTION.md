# Review round: the model decoder, before it was merged (2026-11-10)

Seven models were sent `vif.py`, `pxy.py`, `xhr.py` and the decoder's own claims. Gemini,
GPT, GLM and Grok answered; their answers are beside this file, unedited. Each point was
checked against the code or the disc.

## Confirmed and fixed

| Finding (who) | What was done |
|---|---|
| The rest-pose skinning check is circular (Gemini e, GPT 1): the exporter sets a used joint's world matrix to the inverse of its inverse-bind matrix, so any joint assignment reproduces the mesh | Replaced as evidence by a test that does not use those matrices (`docs/research/skin_locality.py`). Over the 595,924 skinned vertices of `model/char`: the joint a vertex is mainly bound to (or that joint's child) is the nearest or second-nearest joint for 96.3 % (9.5 % with the assignment shuffled), within the nearest four for 99.7 %; of 1,190,328 mesh edges, 83.1 % join vertices of one joint, 14.7 % a parent and child, 1.0 % siblings, 1.2 % unrelated joints (12.3 % related when shuffled). Joint positions come from the `.xhr` files. |
| T keeps the restart flag in its low bit (Gemini 5, GPT 10) | Cleared before use, so two vertices differing only in the flag weld. |
| Material identity left out the ALPHA register (GPT 12) | Added. |
| Skin modes other than 0, 2, 4, 8 were accepted as "weighted" (GPT 9) | Refused. None occurs: 10,646 / 9,860 / 5,242 batches of modes 4 / 8 / 2 in `model/char`. |
| A palette byte after the 255 that ends a list was ignored (Gemini 6, GPT 9) | Refused. None occurs. |
| Dead code computing degenerate triangles (Gemini 1) | Removed; degenerate triangles are kept on purpose, so the count equals the file's own. |

All 944 models export exactly as before these fixes (same triangle counts, 5,587,995).

## Checked and found harmless

- **Colours above 128 clipped (Gemini 4, GPT 11).** No colour byte above 128 exists in
  `model/char` (0 of 252,968). Not measured on maps and objects.

## Accepted as open, stated in BLENDER.md and MODELS.md

- **Winding (all four).** Faces are oriented by normals. The right source is the per-item
  cull value the engine sends to VU address 0x3C6: trace it through the cull routine and
  export the stored order, double-sided only where that value is 0. Not done.
- **The triangle count does not prove strip parity or which side of a flag is dropped
  (Gemini c, GPT 5).** True. The restart rule was read from the microcode; the count only
  confirms how many triangles a flag removes.
- **Fifteen skeletons, and the Euler order fitted on one file (Gemini d, GPT 8).** The
  loader that builds the matrices should be read instead of fitted.
- **Alpha modes are guessed from group names; region clamp/repeat is exported as plain
  clamp/repeat (GPT 10, 12).**
- **Programs 1, 3 and 4 were pattern-checked only, program 2 not at all (GPT 17).**
- **Handedness and colour scale** need one comparison with the running game.
- **Hostile input (GPT 14, 15).** Truncated headers can raise `struct.error` rather than the
  decoder's own error; NaNs are not rejected explicitly. The disc's files do not trigger
  either.
- **A node that mixes skinned and unskinned batches under a non-identity matrix would lose
  the matrix (Gemini 3, GPT 7).** The sweep found no skinned batch under a non-identity node
  matrix; the mixed case was not searched for separately.
