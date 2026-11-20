# Models in Blender

`browse` turns every model on the disc into a `.glb` file. Blender opens those
directly: File ▸ Import ▸ glTF 2.0.

```sh
python cli.py dump   --iso "Disc 1.iso" --out out/
python cli.py browse --out out/ --kinds models      # or plain `browse`, which does everything
```

The files land in `out/browse/models/`, laid out like the disc
(`chain0/model/char/pc/kosmos00.glb`). All 944 models of Disc 1 take about a
minute. `out/browse/models.csv` lists each one: whether it had textures and a
skeleton, its triangle count, and whether every numeric check passed.

One model without dumping the disc first:

```sh
python cli.py model --iso "Disc 1.iso" --path 'model\char\pc\kosmos00.pxy' --out kosmos00.glb
```

`model` adds every motion file with the model's name (`motion\kosmos00.xap`,
and `yamamoto\mot\kosmos00.xap` when there is one); `--motion PATH[#slots]`
picks others by path (`--motion 'yamamoto\mot\kosm00.xap#0,1'`), `--slots`
keeps some slots, `--face` adds the face clips, `--no-motion` leaves them
out. `browse` adds the same-named motion files and lists them in the
`motion` column of `models.csv`.

## What you get

* The mesh, its UVs and its textures (inside the `.glb`, nothing to relink).
* Vertex colours.
* Skin weights and an armature for the 562 models with a usable skeleton file.
  The bones sit on the mesh: select the armature, switch to Pose Mode, rotate a
  bone.

Where to look first: `model/char/pc/` is the party (`shion00`, `kosmos00`,
`jr00`, ...), `model/enemy/` the enemies and bosses, `model/robo/` the E.S.
craft, `model/mobj/` map objects.

## What to expect

* **Animations: one action per motion clip, at 30 fps.** Set the scene to 30
  fps before importing (Output Properties ▸ Frame Rate). In the Dope Sheet,
  switch to the Action Editor and pick a clip (`motion/shion00#01`); the
  names are the disc file and the slot, since the game's own names for the
  slots are not known. Face clips (`model --face`) key only the six face
  joints: push the body action down to the NLA and put the face action on a
  strip above it. Hair, coats and skirts do not swing: the game simulates
  them and the kit does not ([MOTION.md](MOTION.md)).
* **Materials are double-sided and faces follow the normals.** About one
  triangle in thirty is stored facing against its normal, and the game mostly
  draws without culling, so the file order cannot be trusted for facing.
* **Bones have numbers, not names.** The skeleton files carry no joint names.
* **Fifteen skeletons** (some robots and crowd characters, `bos022`, `midle`)
  differ from their mesh's bind pose on a few joints; those may look bent
  until that is understood.
* **Two models** (`tyusya`, `npcr02a`) have a skeleton file too short for the
  mesh, and three skinned ones have none; they are written without an
  armature.
* **One model** (`mobj241`) fails the facing check and is written anyway; the
  CSV says so.
* **Not yet checked against the running game:** whether models are mirrored,
  the colour scale, and which materials should be transparent. If something
  looks wrong in one of those ways, that is a real report: please open an
  issue.

Swept so far: USA Disc 1 (944 models written, one flagged by a check), Japan
Disc 1 (941, one flagged) and the *Xenosaga Freaks* demo (666, five flagged: four
map objects with a skinned batch in a file marked static, one failing the
facing check). USA Disc 2 shares its model data with Disc 1. How the format works, and how
each part was proven: [MODELS.md](MODELS.md).
