# Models: `.pxy` meshes and `.xhr` skeletons

What the kit knows about Episode II's model files, how each fact was
established, and what is still open. Code: `vif.py` (strict VIF walker),
`pxy.py` (mesh decoder, invariants, GLB export), `xhr.py` (skeleton),
`gltf.py` (GLB writer/reader), `models_sweep.py` (inventory and sweep),
`vuprog.py` (pulls the VU1 programs out of the executable).

Evidence is of three kinds and each statement below says which:

* **code**: read from SLUS_208.92 (sha1 `200605e6...f44e`), either the
  EE functions (named by address, `FUN_<addr>`) or the VU1 microprogram
  ("VU 0x306" is instruction index 0x306 of program 0, see `vuprog.py`);
* **number**: a test run over the disc's files, with the count;
* **image**: looked right in a Blender render. Only two models were
  looked at (`shion00`, `mobj037`).

Disc 1 (USA) has 944 `.pxy`, 564 `.xhr`, 943 `.txy`. Two `.pxy` are
version 0.14, the rest 0.15.

## Reproducing

```sh
python cli.py dump --iso "Disc 1.iso" --out out/            # meshes, skeletons, textures side by side
python models_sweep.py out/dump --inventory                 # the opcode histogram below
python models_sweep.py out/dump --glb --jobs 8              # the sweep numbers below
python pxy.py out/dump/chain0/model/char/pc/shion00.pxy \
       --txy out/dump/chain0/model/char/pc/shion00.txy \
       --xhr out/dump/chain0/model/char/pc/shion00.xhr --glb shion00.glb
python xhr.py out/dump/chain0/model/char/pc/shion00.xhr out/dump/chain0/model/char/pc/shion00.pxy
python vuprog.py SLUS_208.92 vu/                            # prog0.bin .. prog4.bin for a VU disassembler
```

## The VIF stream

**Framing (number + code).** The geometry is a raw VIF1 command stream,
no DMA tags in the file. The draw list cuts it into *draws*
`{offset, qword count}`; in all 944 files the draws tile the region from
the header's stream offset to the draw list exactly, in order.
`FUN_001d0798` sends one draw as `MSCAL <mesh entry>` followed by a DMA
`ref` to `file + offset` for `qword count` qwords.

**Inventory (number).** `models_sweep.py --inventory` walks every draw of
every file with a walker that knows all VIF commands and stops on anything
else (unknown command, reserved address bits, non-zero padding, a stream
that does not end on its boundary). 944 files, 3,580,691 commands, 0
failures:

| count | command |
|---:|---|
| 994,066 | `UNPACK V4-32`, USN, TOPS-relative, unmasked |
| 849,964 | `STCYCL` CL=1 WL=1 |
| 849,964 | `MSCNT` |
| 785,468 | `UNPACK V4-8`, USN, TOPS-relative, unmasked |
| 78,722 | `NOP` (only as 0 to 3 words closing a draw, padding to a qword) |
| 22,507 | `UNPACK V2-32`, USN, TOPS-relative, unmasked |

Nothing else occurs: no `OFFSET`, `BASE`, `ITOP`, `STMOD`, `MSKPATH3`,
`MARK`, `FLUSH*`, `MSCAL`, `MSCALF`, `STMASK`, `STROW`, `STCOL`, `MPG`,
`DIRECT`, no masked or fill-write unpack, no interrupt bit. No UNPACK has
NUM = 0 (the 256 case never arises); the highest address written is
TOPS + 180. The two version 0.14 files (965 batches) use only `V4-32` and
`V4-8`; `V2-32` (two-weight skinning) appears only in 0.15 files. No other
per-version difference was found in the stream.

Every batch is `STCYCL 1,1`, one to four UNPACKs, `MSCNT`: 849,964
batches. The second word of a batch's first row is its kind: 224,873
material batches (1), 574,835 + 50,256 mesh batches (0 and 3).

**Double buffer (code).** Each program packet ends with `BASE 0`,
`OFFSET 276`, so TOPS alternates between 0 and 276 at every
`MSCAL`/`MSCNT` and a batch may use addresses 0..275. The programs put the
GIF packet they build at TOP + 183, which is why a mesh batch holds at
most 30 vertices (1 + 6 * 30 = 181; largest seen: 30).

## The microprogram

**Which program (code).** `FUN_001cf468`/`FUN_001cf520` upload one of five
VU1 programs, chosen per model instance, and `FUN_001ccae8` returns its
packet, length and entry points:

| program | `.vudata` VA | mesh entry | chosen when |
|---|---|---|---|
| 0 | 0x673160 | 0x73A | default |
| 1 | 0x677010 | 0x68F | `.pxy` flags bit 0 (unskinned files) |
| 2 | 0x67A890 | 0x592 | instance flag 0x8000 |
| 3 | 0x67D9B0 | 0x765 | renderer global at gp-0x4cdc set |
| 4 | 0x6819C0 | 0x652 | renderer global at gp-0x4cd8 set |

`MSCAL entry` runs two instructions and stops (E bit at the entry); each
`MSCNT` then resumes at entry + 2, handles one batch and jumps back to the
stop. **Program 0 was read instruction by instruction for everything
below.** Programs 1, 3 and 4 were only pattern-checked: they contain the
same instruction sequences for the S/T/restart-flag reads (the
`ILW.w vi12,-1(vi06)` / `ILW.w vi12,808(vi12)` pair occurs 11/6/11/9 times
in programs 0/1/3/4) and the same TOP + 183 packet; program 1 has no
reference to the matrix palette at 552 at all. Program 2 reads no texture
coordinates (it is a shadow or silhouette pass) and was not studied.

**Batch dispatch (code, VU 0x73C-0x748).** `XTOP vi13`, kind = integer y
of row 0. 0 and 3 go to the same mesh routine (0x74A); 1 goes to the
material routine (0x75B); 2 and anything else kick a packet at 813. The
files contain only 0, 1 and 3. What separates 3 from 0 is not known (the
microcode treats them alike; an EE-side use was not found).

**Mesh batch (code).** n = x of row 0 & 0x7FFF (VU 0x74A-0x74F copies
x into the GIF tag at TOP + 183, so bit 15 is EOP).

| address (TOP +) | content | where it is read |
|---|---|---|
| 0 | header: x = n \| 0x8000, y = kind, z = 0, w = flags | 0x73D, 0x74A, 0x1BD |
| 1 .. n | position x, y, z; w = S | `LQI vf02,(vi07++)` 0x306, vi07 = TOP+1 (0x180) |
| 1+n .. 2n | normal x, y, z; w = T, raw bit 0 = restart flag | `LQI vf16,(vi06++)` 0x307, vi06 = vi07+n (0x181) |
| 1+2n | RGBA bytes, n rows if flags bit 0, else 1 row | `LQ vf11,0(vi04)`; `IADD vi04,vi04,vi10` 0x481-0x482, vi04 = TOP+1+2n (0x1A6), vi10 = flags & 1 (0x1A1-0x1A9) |
| 1+3n | a vector per vertex (see below) | vi03 = TOP+1+3n (0x183) |
| 1+4n | skin weights (x, y, z, w) | `LQI vf04,(vi07++)` 0x1D3, vi07 = TOP+1+4n (0x1C6-0x1C8) |
| 1+5n | four integers: palette offsets | `ILW.x/y/z/w vi01,0(vi06)` 0x1D0-0x1F0, vi06 = TOP+1+5n (0x1C9) |

*Texture coordinates and restart (VU 0x306-0x327).* S = position w + an
offset (`ADDw.x vf04,vf24,vf02w`), T = normal w + an offset (`ADDw.y
vf04,vf24,vf16w`), Q term 1; the offsets (address 958) are a scroll value
times the material's SW/TW and the result is multiplied by a uniform
factor (address 825, written by `FUN_001cec98` as one scalar in x, y, z)
and by Q. So the stored values are final S and T. The integer low half of
the same normal-w word (`ILW.w vi12,-1(vi06)`), bit 0, indexes a two-entry
table at address 808 (`FUN_001cec98` writes 0 and 0x6000 there); the value
is OR-ed into a running word that is doubled for every vertex and stored
as the w of the XYZ row. A flagged vertex therefore sets bit 15 (the GS
"do not draw" bit of XYZF2/XYZ3-style writes) on itself (0xC000) and on
the next vertex (0x8000), and nothing after that: it starts a new strip.

*Skinning (VU 0x1B6-0x226).* mode = flags & 0x1E. 0: no skinning. 2 (VU
0x20F-0x225): one matrix per vertex, `LQ vf12..15, 552+offset`; offset 255
leaves the vertex untouched. Otherwise (VU 0x1CF-0x20C): up to four
influences, `position' = sum weight_i * (M_i * position)`, the list ends
at the first offset 255, weights come from the x, y, z, w of the weight
row in order. Normals get the same treatment with the 3x3 part. There are
real weights; modes seen are 2, 4 (`V2-32` weights) and 8 (`V4-32`
weights). 552 = 0x228 is where `FUN_001cf644` unpacks the skinning
matrices, four rows each, so offset / 4 is a palette slot: with at most 64
joint matrices in the file the slot is the joint matrix index; with more,
the function uploads, for the draw's node, the matrices named by the
node's byte list (1-based) into slots 0, 1, 2...

*The vector at 1+3n* exists in unskinned files only (132,803 batches).
It is unit length and constant over the vertices of a triangle; it looks
like the triangle's face normal, negated relative to the facing rule used
here: for 774,906 of 828,431 triangles (93.5 %) it is within 0.99 of
exactly opposite to the geometric normal. Its use was not traced in
program 1 and it is not exported.

**Material batch (code, VU 0x000-0x11F, called from 0x7C5).** Row 0:
x = shader id (the routine table at 0x782-0x7C0 picks the transform and
lighting routines by it), w = float SW. Row 1: a GIF tag, x = NLOOP |
0x8000, z = 0xE (A+D), w = float TW. Rows 2..: NLOOP register values whose
register numbers the program fills in from a template the EE uploads to
834-839 (`FUN_001cec98`: 6 TEX0_1, 8 CLAMP_1, 0x42 ALPHA_1, ...). Row 2 is
TEX0, row 4 CLAMP. The rest of the batch (17 to 97 rows) carries colours,
SW/TW again and shader parameters; **not decoded**.

## Texture coordinates and textures

**(number)** TEX0 of every textured material batch declares a 1024-wide
page (TBW 16, 1024 x 512 for PSMT8, 1024 x 1024 for PSMT4; 854 batches use
512 x 256 CT32) and a CLAMP window. Its TBP0 minus 0x3800 is the GS
address of an entry of the model's `.txy` (same file name): in the sweep
all 223,175 textured material batches of the 941 files with a readable
`.txy` find an entry at that address with that pixel format, and for all
of them SW * TEX0 width and TW * TEX0 height equal that entry's width and
height. (Several entries can share an address, in other banks or with
other palettes; the linker prefers the one whose size and CLUT address
also agree. Taking the first entry at the address instead leaves 75
batches in 9 map files with the wrong size.) A few placeholder entries
(19 distinct materials in 11 files) have width and height -1 and
SW = TW = -1/1024; their coordinates are exported unscaled. So SW and TW are the texture's share of the page and

    u = S / SW,  v = T / TW        (0..1 across the texture, v down)

Wrapping follows CLAMP's WMS/WMT (2 = region clamp, 3 = region repeat;
both occur). Coordinates outside 0..1 are common under region repeat:
of 8,916,223 textured vertices 30.4 % lie in [0, 1], 92.9 % in [-1, 1],
99.8 % in [-8, 8]; the largest magnitude is 11,175 (two batches of
`mobj037`, unexplained).

## Header and tables

All offsets from the file start, little endian. "number" facts were
checked on all 944 files unless a count is given.

| offset | field | evidence |
|---|---|---|
| 0x00 | `"pxy\0"` | |
| 0x04 | f32 version, 0.14 or 0.15 | number |
| 0x08 | u32 flags; bit 0 = unskinned, selects program 1 | code `FUN_001cf520`; number: all 463,218 mesh batches of bit-0 files have skin mode 0 |
| 0x0C | u32 file size | number (944/944) |
| 0x10 | name[32] | |
| 0x30 | u32, equals the `.txy` bank count | number: 937 of 941 |
| 0x34 | u32 node count | number (tables tile) |
| 0x38 | u32 material record count | |
| 0x3C | u32 draw list offset (0x20 per draw) | code `FUN_001d0798` |
| 0x40 | u32 draw item offset (0x60 per item) | code `FUN_001c3a80` |
| 0x44 | u32 group count (always 2) | number |
| 0x48 | u32 draw item count | |
| 0x4C | u32 VIF stream offset | number (draws tile from here) |
| 0x50 | u32 draw count | |
| 0x54 | u32 group table offset (always 0x4B0): `{name[16], index, first item, item count, 0}`, named `non-alpha` and `alpha` | number |
| 0x58 | u32 material table offset (always 0x4F0) | |
| 0x5C | u32 joint matrix count | code: `FUN_00289f48` asserts `numTransform - 2 >= nJointMatrix` |
| 0x60 | u32 node matrix count (= node count) | number |
| 0x64 | u32 offset of the inverse bind matrices (4x4 f32 each) | code `FUN_00289f48`; number, see skeleton |
| 0x68 | u32 offset of the node matrices | code `FUN_001cdc40` |
| 0x6C | u32 node table offset (0x70 per node) | code `FUN_001cf644` |
| 0x70 | u32 offset of a run-time buffer, one identity matrix per joint | number (shion00); role inferred |
| 0x74, 0x78, 0x7C | offsets of optional tables | **unknown** |
| 0x80 | f32[4] bounding sphere | number (shion00 only) |
| 0x90, 0xA0 | f32[3] bounding box min, max | number (shion00 only) |
| 0xB4 | u32 triangle count | number: equals the decoded count in 944/944 |
| 0xBC | u32 | **unknown** (not the vertex count in any reading tried) |
| 0xC0 | u32 offset of a trailing `"WAT "` chunk | **unknown** |

**Draw (0x20):** `{u32 offset, u32 qwords, u32 ?, u32 own index, f32[4]
sphere}`. The third word is unknown (it is not a material record index:
that reading fits 15,139 of 49,793 draws). The sphere is not the draw's
own bound in `shion00` (it grows from draw to draw); unknown.

**Draw item (0x60), code `FUN_001c3a80`/`FUN_001d0798`/`FUN_001cf644`:**
`+0x04` u16 draw index, `+0x10` u16 node index, `+0x14` own index, `+0x18`
flags, `+0x1C` s8 cull mode (sent to VU address 0x3C6 by `FUN_001ce4c4`:
0 = no culling, 1 and 2-3 = cull with opposite signs), `+0x20`/`+0x30`
box min/max, `+0x40` sphere (centre, radius), `+0x50` a second sphere.
Number: every decoded vertex of every draw lies inside its item's box and
sphere (8,949,175 vertices, 0 outside, tolerance 0.1 %). A draw can have
several items (one per group: hair is drawn in both). No draw is used
under two different nodes.

**Node (0x70):** name[0x30], then a zero-terminated list of 1-based joint
matrix indices: the node's matrix palette (code `FUN_001cf644`). The node
matrix (table at 0x68) is applied after skinning (`FUN_001cdc40`:
model x node). All node matrices are the identity in 937 of 942 files that
have nodes; the five others are props under `matumoto/`. No skinned batch
sits under a non-identity node matrix.

**Material record (0x60):** name[0x20] (Maya names), `+0x20` flags,
`+0x24` shader id, `+0x28` high half = the record's own index (52,761 of
52,819 records), two floats at `+0x44`/`+0x4C` that look like SW/TW. How a
record is tied to the material batches of the stream is **unknown**; the
exporter does not use this table.

## Triangles

**(code + number)** From the restart rule above: within a batch, triangle
(k-2, k-1, k) is drawn for every k >= 2 unless vertex k or k-1 is flagged.
The first two vertices of a batch are never flagged (0 of 625,091
batches). The count this gives equals the header's triangle count in
944 of 944 files (5,587,995 triangles).

**Facing.** The cull routine (VU 0x131-0x14E) resets its sign at a flagged
vertex and flips it every vertex, so facing alternates from the strip's
first vertex, not from the batch's. With "k minus strip start even =
counter-clockwise", 1,440,390 of 1,445,222 strips of two or more
triangles have all their triangles facing the same way relative to the
vertex normals. But which way a whole strip faces is not reliable:
5,390,649 of 5,583,792 non-degenerate triangles (96.5 %) agree with their vertex
normals and whole draws disagree (the head of `boy100`: every triangle).
52,609 of 53,193 draw items have cull mode 0, so the engine draws them
two-sided and never notices. The exporter therefore turns every triangle
to face along its vertex normals and marks materials double-sided. Whether
the cull modes 1 and 2 map to this rule with opposite signs was not
established.

## Skeleton (`.xhr`)

Layout: see the docstring of `xhr.py`. Evidence:

* **code**: `FUN_00290ab8` walks six runs of records (sizes 0x40 for the
  first two records, then 0x50, 0x50, 0x50, 0x70, a variable size from
  `FUN_00297da0`, 0x40), takes each record's first word as the parent
  index and multiplies local by parent matrix. `FUN_00289f48` builds the
  skinning matrix of joint matrix j from transform j + 2 and the mesh's
  inverse bind matrix j.
* **number**: all 564 files parse with the runs tiling exactly. The
  positions of translation, the two rotation triples and scale inside each
  record type, the Euler order (X then Y then Z on row vectors) and the
  order of the two rotations were fitted on `shion00` against the inverse
  bind matrices and then tested everywhere: of 559 skinned meshes with a
  usable skeleton, in 544 the rest pose reproduces the bind pose
  (`|invBind * world - I| < 1e-3`) on every joint that a vertex uses. In
  the other 15 (robots, some crowd characters, `bos022`) some joints of
  the first and fourth run differ: there the mesh was bound in another
  pose than the skeleton's rest values, or the layout is wrong for those
  records; not resolved.
* The run-F records and the two root records are not skinned to, so their
  layout (one rotation, no second triple) is **not verified**.
* There are **no joint names**. The gaps between the fields hold leftover
  exporter memory (Maya UI paths like `|Jseb|Jkata_L|`,
  `actionOptionsForm|saveAsOption`), different in every record and
  unrelated to the joint.

**Unused inverse bind matrices.** In 24 of the 559 files some inverse bind
matrices do not match the transform at their index, and in every such case
no vertex uses that joint (in `shion00`: the last 15 of 79, which match
the simulated run's records in another order). The exporter keeps the
`.xhr` rest matrix for a joint no vertex uses.

**Export.** One glTF node per transform (`t00`...), parents from the
`.xhr`; the world matrix of a joint a vertex uses is the inverse of the
mesh's inverse bind matrix, so the rest pose reproduces the mesh whatever
the `.xhr` says. Proof by number: every skinned GLB written by the sweep
is read back and skinned the glTF way in its rest pose
(`sum w * jointWorld * inverseBind * p`); the worst difference from the
stored positions over 563 files is 1.14e-06 (model units, metres).

## Sweep

`models_sweep.py out/dump --glb` (Disc 1, 944 files):

* 944 decode (no structural surprise anywhere).
* 943 decode and pass every invariant; 1 fails: `model/mobj/mobj241.pxy`,
  where 8 of its 10 strips face one way (below the 90 % gate; 79 of its 82
  triangles agree with their normals).
* By version: 0.14: 2 of 2 pass; 0.15: 941 of 942.
* By program family: flags bit 0 (program 1): 315 of 315; others: 628 of
  629. Programs 2, 3 and 4 are chosen at run time, so files cannot be
  grouped by them.
* By batch signature (kind, flags, face vector): every one of the 20
  signatures that occur passes in all its files, except kind 0 and kind 3
  with flags 0x05, each failing in that same one file (189/1 and 55/1).
* By shader id (files pass/fail): 0: 301/0, 1: 110/0, 2: 4/0, 3: 164/0,
  5: 285/0, 6: 44/0, 7: 268/1, 8: 293/0, 10: 360/0.
* Totals: 625,091 mesh batches, 8,949,175 strip vertices, 5,587,995
  triangles; 0 non-unit normals; 2,462,256 skinned vertices, 0 weight rows
  off 1 (12 rigid vertices of `gno021` carry the "no matrix" byte and are
  left unskinned, as the microcode does); 0 vertices outside box or
  sphere; 0 mesh batches before a material batch.
* Textures: 941 `.txy` read; 2 are JPEGs under a `.txy` name and 1 model
  has none (those three export untextured).
* Skinned files: 564. Skeleton usable for 559; 3 have no `.xhr`
  (`ukun00` under `iwase/`, `gno021`, `mobj138`) and 2 have an `.xhr` with
  fewer transforms than the mesh has joint matrices (`tyusya`, `npcr02a`);
  those five export with one unparented node per joint matrix.
* GLB: 943 written and read back with the header's triangle count (the
  failing file is not written by the sweep).

A file counts as passing only if it decodes without any structural
surprise *and* passes: triangle count = header; every vertex inside its
item's box and sphere; weight rows sum to 1; normals unit length (1 %
allowed to miss); strips face one way (10 % allowed to miss); no skinned
batch in a file flagged unskinned or under a moved node; every textured
material batch finds a `.txy` entry at its address with its size.

## What is verified how

| | by code | by number | by image |
|---|---|---|---|
| stream framing, batch structure | yes | 944 files | |
| position, normal, S/T, restart flag, colour count, weights, palette bytes | program 0 | counts tile; boxes; triangle counts | 2 models |
| same layout under programs 1, 3, 4 | pattern only | 315 bit-0 files pass the same gates | 1 model (`mobj037`) |
| u = S/SW, v = T/TW, texture by TBP0 | TEX0/CLAMP roles | sizes match the `.txy` | 2 models |
| palette slot -> joint matrix | yes | indices in range everywhere | 1 model |
| skeleton hierarchy and bind pose | run structure | 544/559 | 1 model (armature imports) |
| colour scale (128 = 1.0), alpha modes | no | no | 2 models |
| handedness of the coordinate system | no | no | text on textures not checked |

## Open

* Material batches beyond TEX0/CLAMP (shader parameters, scroll, the 43-
  to 97-row variants); what the shader ids 0-10 mean.
* The link from the header's material records to the stream; draw word 2;
  header 0xBC, 0x74-0x7C, the `WAT ` chunk; batch kind 3 versus 0.
* Texture size disagreements listed in the sweep.
* The vector at 1+3n and the cull modes.
* The 15 skeletons whose rest pose is not the bind pose; run-F layout.
* Alpha: the exporter marks a draw that is only in the `alpha` group
  BLEND, one in both groups MASK, others OPAQUE. This is a presentation
  choice, not read from the engine's blend setup.
* Motion (`.xap`), face motion (`.fap`), the tables after run E in the
  `.xhr` (collision shapes and limits for the simulated chains).
* Disc 2 and the Japanese discs were not swept.
