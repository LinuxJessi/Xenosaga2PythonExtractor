# Motion: `.xap` body clips and `.fap` face clips

What the kit knows about Episode II's motion files, how each fact was
established, how the exporter turns them into glTF animations, and what is
still open. Code: `xap.py` (decoder, engine pose, glTF tracks), the animation
helpers in `gltf.py`, and the `model` / `browse` hooks in `cli.py` and
`browse.py`.

Evidence, as in [MODELS.md](MODELS.md):

* **code**: read from SLUS_208.92, routines named by address only
  (`FUN_<addr>`), from the Ghidra output and, where it was unclear, the
  disassembly;
* **number**: a sweep over the disc's files (`python xap.py --sweep DUMP`);
* **image**: contact sheets and Blender renders of three characters.

## Reproducing

```sh
python xap.py --self-test
python xap.py --sweep out/dump                       # every .xap / .fap on the disc
python xap.py out/dump/chain0/motion/shion00.xap     # one file's clip table
python xap.py out/dump/chain0/motion/shion00.xap \
       --xhr out/dump/chain0/model/char/pc/shion00.xhr --slot 1 --frame 10
python cli.py model --iso "Disc 1.iso" --path 'model\char\pc\shion00.pxy' --out shion00.glb
python cli.py model --iso "Disc 1.iso" --path 'model\char\pc\shion00.pxy' --out shion00.glb \
       --motion 'motion\shion00.xap#0,1,2' --motion 'yamamoto\mot\shio00.xap#0,1' --face
```

## Container

`"XAP "` (body) or `"FAP "` (face), f32 1.2, u32 n at +8, then n u32 clip
offsets at +0xC. The first offset is 12 + 4n; offsets ascend and are
4-aligned (number: all 550 files). A clip runs from its offset to the next
distinct offset or to the end of the file, and several slots can share one
clip (they are adjacent). A clip whose start frame is greater than its end
frame is not taken: `FUN_00292030` stores (int)start and (int)end only when
start <= end (code). On the disc those are 16-byte stubs `"XAC "`, 1.4,
100.0, -100.0: empty slots.

Disc: 549 `.xap` and one `.fap` (`motion\face_all.fap`); 8,587 slots, 4,340
real clips, 4,247 stubs (number).

## Clip header

`"XAC "` (body) or `"FAC "` (face), f32 version at +4, f32 start frame at +8,
f32 end frame at +0xC (code: `FUN_00292030`; `FUN_002924a0` advances
time += speed x global tick and wraps to start once time >= end).
`FUN_002929e0` dispatches on the version: below 1.4 to `FUN_0028dec0` (body)
or `FUN_002900f8` (face), 1.4 to `FUN_0028f1e0` or `FUN_00290518` (code). On
the disc: XAC 1.4 4,290 clips, XAC 1.3 27, XAC 1.2 4, FAC 1.4 19 (number).

Version 1.4, 0x50 bytes (code: `FUN_0028f1e0`, `FUN_00290ec0`):

| count | offset | stream |
|---|---|---|
| +0x10 | +0x30 | run A; the first two blocks are the two roots (`FUN_00293e38`, no count check) |
| +0x14 | +0x34 | run B |
| +0x18 | +0x38 | run D, the face joints; only FAC clips drive them |
| +0x1C | +0x3C | run E, the chains; 8-byte block header |
| +0x20 | +0x40 | visibility list (body only; junk in FAC clips) |
| +0x24 | +0x44 | run C |

Offsets are relative to the clip. +0x28..+0x2F and +0x48..+0x4F hold
exporter junk (fragments of Maya strings). Run F is never animated.

Version 1.3 / 1.2, 0x30 bytes (code: `FUN_0028dec0`): counts +0x10 A, +0x14 B,
+0x18 D, +0x1C E; offsets +0x20 A, +0x24 B, +0x28 D, +0x2C E. Run C stays at
rest. The run-E block header is 8 bytes from version 1.3 on (`FUN_00294338`)
and 4 bytes below (`FUN_002945c8`, the test in `FUN_0028dec0` compares
1.3 <= version as single floats). No 1.2 clip on the disc has run-E blocks, so
the 4-byte case is covered by the self-test only.

The streams tile every clip exactly, in this order, with no gap and no tail
(number: 4,340 of 4,340 clips; the parser refuses anything else, allowing up
to three bytes of padding before a 4-aligned clip end, which never occurs).
Earlier research called the tail of 31 clips "trailing bytes"; those are the
visibility lists.

## Which block drives which transform

The transform index is the position in A, B, C, D, E, F order, as in
`xhr.py`. Block i of a stream drives transform (run base + i), but only for
i < min(clip count, `.xhr` count): `FUN_0028f1e0` re-reads each stream pointer
from its own offset field, drops it once the clip's count is reached, and
otherwise uses the `.xhr` rest values (code: `FUN_002939e8` with a null
stream copies the record's rest fields). Extra blocks are ignored; missing
blocks leave the transform at rest. Run A is counted with its two roots, so
block k drives transform k. Example: `motion\jr00.xap` has clips with B 6/7
and E 21/18 blocks while `jr00.xhr` has B 6 and E 16; the engine takes the
first 6 and the first 16, and so does the exporter.

## Blocks and channels

u16 mask, u16 flags, then (run E, version 1.3 and later) one f32 that the
engine copies to the record's +0x2C (`FUN_00294338`; meaning unknown; values
0, 0.2, 0.3, 0.7, 1 seen), then one key list per set mask bit, in bit order:

| bits | channel |
|---|---|
| 0-2 | translation x, y, z |
| 3-5 | rotation x, y, z (radians) |
| 6-8 | scale x, y, z |

A clear bit takes the `.xhr` rest value; keys replace the rest value, they
are not offsets. Joint orients are never keyed. Flags bit 0 sets the
record's parent-scale compensation bit (code: `FUN_002939e8`,
record +0x44 |= 2; `FUN_00294338` for run E); the other flag bits are not
read. No mask on the disc has bits above 8 (number; the parser refuses them).

## Local and world matrices

Row vectors, as `xhr.py` (code: the matrix calls in `FUN_0028f1e0`):

    L = S * Rx(R.x) Ry(R.y) Rz(R.z) * Rx(JO.x) Ry(JO.y) Rz(JO.z) * invPS * T

* The roots have no JO.
* The record's +0x40 rotation multiplier is 1.0 in every record on the disc,
  so it is left out.
* invPS = diag(1/ps) when the compensation bit is set, ps being the parent's
  current scale: the slot `FUN_0028dd50` finds, which the pose loop writes
  with this frame's scale, keyed or rest. `FUN_00293330` skips a zero axis and
  leaves whatever an earlier call left there; the exporter uses 1.0 for that
  axis and the sweep counts the cases. A parent in run F has no slot (the
  engine asserts "can not found parent scale" and skips the scale).
* A parent later in index order would give the previous frame's value in the
  engine; the exporter uses the same frame (the sweep counts these among the
  compensated transforms that meet a scale other than 1: none). No
  compensated transform on the disc has a parent in run F.
* World = L * world[parent] (`FUN_00290ab8`).
* Transform 1 (the moving root) has its translation multiplied at run time by
  act+0xB0..B8. The factor is 1.0 unless something sets it (unverified); it is
  left out.

Face clips (`FUN_00290518`, run D only, with the face clock): the same
order with JO at +0x18, plus two quirks the exporter copies: the translation
is applied only when the rest T is not zero (and then the animated T is
used), and the scale only when the rest S is not (1, 1, 1) (then the animated
S). The rotation limits are clamped after the matrix is built, so they change
nothing.

## Keys

Code: `FUN_002935e8`; value `FUN_00293560`, out-angle `FUN_00293420`,
in-angle `FUN_002934c0`. Each key starts with a u16 header (low 12 bits
type, 0x4000 last key of the curve, 0x8000 see below) and an s16 frame.

| type | size | fields |
|---|---|---|
| 0 | 8 | f32 value; angles 0 |
| 1 | 8 | f32 value; the segment it starts is always linear |
| 2 | 12 | f32 angle (in = out), f32 value |
| 3 | 12 | u16 in-angle at +4, u16 out-angle at +6 (x 1e-4), f32 value |
| 4 | | asserts "XacKeyType_Hermite"; never on the disc, refused |
| 5 | 16 | f32 in-angle at +4, f32 out-angle at +8, f32 value |

Evaluation at time t, in frames: t = max(t, first key's frame); walk the
keys in order; the first k with frame_k <= t < frame_k+1 picks the segment;
a 0x4000 key reached before a segment matched returns its own value (so a
lone key, or any time past the last key, holds). The segment is linear
(`FUN_00295610`) when key k is type 1, or key k has 0x8000 and the next key's
whole header is 0x0001. Otherwise it is a cubic (`FUN_00295488` ->
`FUN_00295260` -> `FUN_002955c0`) on x = frame / 30 whose end slopes are
sin/cos of key k's out-angle and key k+1's in-angle (5729578.0 when the
cosine is 0). Keys are 30 per second (`DAT_00646338` = 1/30, a literal 30.0
in `FUN_00295610`). Negative frames (202 on the disc) and non-ascending pairs
(74) follow the walk exactly.

A cubic segment into a type-1 key would assert in the engine (the angle
accessors have no type-1 case); the sweep finds none.

## Visibility list

Version 1.4 body clips only (`FUN_00290ec0`, `FUN_00295188`): count at +0x20,
offset at +0x40; per entry an i32 n, then n pairs (f32 frame, f32 value).
Bit i of the output (the pointer is named `pVisible`) is set when entry i's
value is 0.0.

How an entry gives its value at time t was read from the disassembly of
`FUN_00295188`, and it is not the "last pair before t" that the research
notes assumed: walking the pairs with a "previous frame" that starts at 0.0,
a pair whose frame is >= t while the previous one is < t gives its value;
the first pair gives its value when nothing later does; past the last frame
the last value holds. On this disc the difference never matters: all 519
entries in the 31 clips that have a list are one pair, (0.0, 1.0), so every
bit is always clear and nothing is ever hidden (number).

What an index means is not traced. A lead: the entry count equals the
`.pxy` node count of the same-named enemy in every case that could be
checked (`uma002` 16 and 16, `uma005` 20 and 20, `uma011` 20 and 20, `uso002`
3 and 3); `yosikawa\model\menubg.xap` (4 and 10 entries) has no mesh beside it.
The exporter stores the lists and the per-frame bits in the animation's
extras and hides nothing.

## From clip to glTF

`xap.add_to_glb` takes the `.glb` that `pxy.to_glb` wrote with the skeleton
(nodes `t00`..`tNN` under a node named `skeleton`; refused when the mesh was
written without the `.xhr`) and appends one glTF animation per selected
non-stub clip.

* **Names**: the disc path without chain and extension, then the slot:
  `motion/shion00#01`, `yamamoto/mot/kosm00#00`. Slots sharing one clip are
  written once, `#06,07`. Nothing is named "idle" or "walk": the game's names
  for the slots are not known. A clip whose run counts differ from the
  skeleton's carries them after the name, clip/xhr:
  `motion/gno010#03 [counts A30/55]` (see "Clips whose run counts differ").
* **Body clips** key every transform with translation, rotation and scale.
  Transforms the clip does not drive get their `.xhr` rest values as one
  constant key, so each action stands alone in Blender and the engine's
  rest-value fallback is reproduced. Run D and run F stay at rest; run E is
  keyed where the clip keys it, and otherwise at rest.
* **Face clips** key only the run-D nodes: an overlay on top of a body action.
* **No matrix decomposition.** With no compensation, the column-form local is
  T . (Rrows(R) Rrows(JO))^T . diag(S), so the exporter writes translation =
  T, rotation = the quaternion of that orthonormal matrix, scale = S as it is.
  That is exact even for zero or negative scale (keyed scale 0 occurs, for
  example on the `kos_sw03` sword). Under compensation with a uniform parent
  scale k the scale is S / k. With a non-uniform parent scale the 3x3 is
  diag(1/ps) Q diag(S), which glTF cannot hold; the exporter takes the
  rotation of its polar decomposition, scale = diag(R^T M), and records the
  shear left over in the extras (`max_shear_residual`).
* Frames int(start)..int(end), time = (f - int(start)) / 30. Quaternions keep
  their sign from frame to frame. Channels that do not change (within 1e-7)
  get one key; a clip that never moves keeps both ends on one channel so the
  action has the clip's length.
* Every transform is made a joint of the skin (after the mesh's own joints,
  inverse bind = the inverse of its rest world matrix, no vertex uses it), so
  Blender makes a bone of each one.
* **Extras** per animation: source, slots, version, start, end, fps 30, kind,
  the clip's stream counts, the `.xhr` counts, `max_shear_residual`, the
  compensation census when there is any, `visibility` / `visibility_bits`
  when the clip has a list, `e_extra` (the run-E f32s) when it has any,
  `run_counts_differ` ({run: [clip blocks, .xhr transforms]}) when the counts
  of a run the clip drives are not the skeleton's.

Checked (number and image): the self-test builds a skinned `.glb`, adds
synthetic clips, reads the file back, samples it the glTF way and compares
every node's world matrix with `xap.pose` (1e-5). On the demo files
(`shion00`, `kosmos00`, `jr00`, 14 animations; `tools/demo/motion.sh "Disc
1.iso" out/dump demo-motion/` builds them and writes these numbers to
`report.txt`) the read-back world matrices
agree with the engine formula to 5e-7 (3e-7 of the body height), bone lengths
of joints whose T and S are not keyed stay within 3e-8, the field clips loop
exactly, the Khronos glTF validator reports no error, warning or info, and
Blender 5.4 imports every action with the clip's frame range and a bone for
every transform.

## Pairing a model with its motion

`model` without `--motion` and `browse` add every `<dir>\<stem>.xap` on the
disc whose stem is the model's: `motion\` (field) and `yamamoto\mot\`
(battle). 141 stems have both an `.xhr` and an `.xap` (`yamamoto\mot` 119,
`motion` 86, `motion\cfevent` 1, `model\char\pc` 1). The other 331 `.xap`
stems have no same-named skeleton: the cutscene clips
`motion\cfevent\<scene>_<char>`, `motion_ext01`-`29`, the battle `db*` files,
and names that differ from the model's (`kosm00` / `kosmos00`, `shio00` /
`shion00`, `zigg00` / `ziggy00`). They are bound by script or engine tables
that are not traced (the `"?motion/cfevent/%s"` and `"motion_ext%d"`
loaders), so `browse` leaves them out and counts them; `model --motion` takes
any of them by path, on the user's judgement. Face clips are added only on
request (`--face`, or `--motion 'motion\face_all.fap#0'`), since all 19 fit
every skeleton with six run-D joints.

### Clips whose run counts differ

A matching name does not make a clip fit. How the reader applies a clip is
read from the executable (block i of a run drives transform run base + i
while both have one, `FUN_0028f1e0`), but nothing read so far shows which
clip the game plays on which skeleton, so a clip with other block counts may
have been made for another model. 189 of the 2,506 stem-paired clips have a
block count, in a run their decoder reads (A B C E for a 1.4 body clip, A B E
below, D for a face clip), that is not the skeleton's: run A in 52, run B in
118, run E in 71, in 13 models (number, `--sweep`).

Some of them are plainly for another skeleton. `motion\gno010.xap` slots 0-2
have 55 run-A blocks like `gno010.xhr`, slots 3-4 have 30, and those two
clips are byte for byte slots 3-4 of `motion\uma003.xap`, whose skeleton has
15; `motion\uma013.xap` has two other 30-block clips beside one of 22, its
skeleton's count. Others fit
as far as anything can be checked: `jin_saya` (3 blocks for 4 transforms) and
`gno003` (36 for 34) pass the rest check below on every channel, and every
field and battle clip of `jr00` has more run-E (chain) blocks than its
skeleton (21 or 18 for 16). The rest check splits the same way: 0.913 of the
channels agree in clips whose counts match, 0.529 in clips whose counts
differ (number). The count alone does not decide it, so nothing is dropped
silently and nothing is passed off as known:

* `model` and `browse` write such a clip with the counts in its animation
  name and in the `run_counts_differ` extras; `model` prints a line for each
  and counts them in its summary, and `models.csv` lists them per file in the
  `motion` column (`#03 A30/55`) and counts them in `motion_counts_differ`.
* `model --matched-only` leaves them out and prints what it left out.

## Sweep numbers (USA Disc 1 dump, chains 0-2)

    files 550 (549 .xap, 1 .fap); slots 8,587; stubs 4,247
    clips 4,340, all decoded: XAC 1.4 4,290, XAC 1.3 27, XAC 1.2 4, FAC 1.4 19
    key types, all versions: type 0 767,900  type 1 1,752,783  type 2 2,493,051
                             type 3 4,529    type 5 131        type 4 0
      (XAC 1.4: 764,261 / 1,748,839 / 2,481,097 / 4,512 / 131;
       XAC 1.3: 3,005 / 3,268 / 8,467 / 17 / 0; XAC 1.2: 262 / 676 / 1,230;
       FAC 1.4: 372 type 0, 2,257 type 2)
    0x8000-linear segments 270,342; cubic segments into a type-1 key 0
    clips with a visibility list 31; clips with fewer than two run-A blocks 0
    negative key frames 202; non-ascending key pairs 74
    blocks with flags bit 0: 41,606

The all-version key totals equal the first research round's; the second
round's equal XAC 1.4 plus FAC 1.4, so it counted version 1.4 only.

Over the 2,506 clips whose stem matches an `.xhr` (the only ones with a known
skeleton): 191 compensated transforms meet a parent scale other than 1 (143
uniform, 48 non-uniform, 93 with a zero axis); the non-uniform ones are in 8
clips of 5 battle files (`bos009`, `bos020`, `uro008`, `uro009`, `vma001`),
with a shear residual up to 0.58. None is in the demo clips. Whether the shear
is visible in the game is not known.

Rest check (not circular; pins the order of the translation bits): over the
version-1.4 clips of those pairs whose run counts are the skeleton's, the
first key of each translation channel on a run-A joint (index 2 and up)
equals the `.xhr` rest translation within 1e-3 in 52,368 of 57,351 channels
(0.913). The misses are hips and rescaled bosses. In the clips whose counts
differ it is 315 of 596 (0.529). The field clips of the party key only rotations plus the roots'
T/R/S, so this check comes from the battle and cutscene files.

## Not reproduced

* **Chain simulation.** Hair, coats and skirts in run E are simulated after
  the pose (`FUN_00297da0`); the exporter writes their keyed or rest values
  only, so coat tails stay stiff.
* **Visibility.** Stored in the extras, not applied (it hides nothing on this
  disc anyway).
* **The act+0xB0..B8 factor** on the moving root's translation.
* **Real-time rate.** The curves are 30 keys per second and the glTF uses 30
  fps; how fast the game advances a clip (speed x global tick) is unverified.
  A PINE watch of act+0x18 while a clip plays would settle it.

## Unverified

* What a visibility index means (lead above: the `.pxy` node index).
* What the run-E f32 means.
* The real-time playback rate.
* Which clips the game plays on which skeleton, so whether the 189
  same-named clips whose run counts differ were made for that model.
* How the `.xap` files without a same-named `.xhr` are bound to models; the
  demo's battle pairings `shio00` -> `shion00` and `kosm00` -> `kosmos00` rest
  on the names only.
* Whether the shear from compensation under a non-uniform parent scale is
  visible in the game.
* XAC +0x28 / +0x48 and the FAC visibility fields are taken to be junk because
  no reader touches them.
* A lead only: the Episode III executable carries the same `XAP`/`XAC`
  strings; whether this reader decodes its files is untested.

## In Blender

Set the scene to 30 fps before importing (Output Properties ▸ Frame Rate),
then File ▸ Import ▸ glTF 2.0. Each clip is an action on the armature: in the
Dope Sheet switch to the Action Editor and pick one (`motion/shion00#01`).
A face clip keys only the six face joints: push the body action to the NLA
(Push Down) and add the face action on a strip above it. Bones are `t00`..
`tNN`; `t01` is the moving root.
