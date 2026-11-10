# Modding Xenosaga Episode II with the kit

Everything here writes into a **copy** of your disc image. The original is
never opened for writing; each command copies it first when `--out` names
another file, and every write is read back before the command returns.

## How writing works

The game finds every object through the binary TOC (`toc.py`): a sector
and a size per file, at fixed positions inside the TOC file. `repack.py`
(the Episode I kit's, with Episode II's chains) puts a new object back in
one of two ways:

* **In place.** If the new bytes fit the object's slot (the gap to the next
  object, rounded to 2,048-byte sectors), they are written over the old ones
  and the TOC size is updated. Recolours are always this case.
* **Relocated** (`--grow`). If it no longer fits, it is appended after the
  last sector of the image, its TOC sector repointed there, and the ISO's
  volume size bumped. The game reads objects by raw sector, so it follows.
  Every chain on the USA discs is packed solid, so growth is always an
  append. Disc 1 can grow by 17 MB before it stops fitting a single-layer
  DVD (emulators do not care).

ARX-compressed objects are recompressed by the kit's clone of Monolith's
packer; an untouched object recompresses to the same bytes.

## Recolouring textures: `recolor`

```sh
python cli.py recolor --iso "Disc 1.iso" --out pink.iso                     # KOS-MOS's hair, pink
python cli.py recolor --iso "Disc 1.iso" --out x.iso --rule 'shion*_hair=all' --hue 0.08
python cli.py recolor --iso "Disc 1.iso" --dry-run --preview prev/          # before/after PNGs only
python cli.py recolor --iso pink.iso --out pink.iso --xtx 'yajima\titleobj.xtx'
```

A texture is named by its `.txy` entry, the names the artists gave them
(`kosmos00_hair_col`, `shion00_face`; `browse` writes them as file names).
`--rule GLOB=MODE` picks entries; `all` sets the hue of every colour of the
entry's palette, `blue` only of its blue colours (for faces: the skin stays,
a blue fringe changes). The preset `kosmos-hair` is
`kosmos*_hair_col=all`, `kosmos*_hair_spe=all`, `kosmos*_face=blue`.

**Every copy is found by structure, not by bytes.** The command parses every
standalone `.txy` and every texture page embedded in an effect file
(`embedded.py`), finds each entry the rules name, and edits the palette tile
that entry reads, wherever that tile sits in that file. KOS-MOS's hair is
`kosmos00_hair_col` in `kosmos00.txy` and `kosmos01_hair_col` in `kosmos01`,
`02` and `03`, at four different palette positions, so a byte search for one
palette finds one copy. (Episode III's kit learned the same lesson: costume
variants carry re-baked palettes.) Result for the preset on Disc 1: 12
entries in 4 files, about 570 colours each, all patched in place.

`--xtx PATH` colourises a true-colour `.xtx` sheet instead (no palette;
every visible pixel takes the hue with lifted saturation, so white text
turns pastel). The title menu words live in `yajima\titleobj.xtx`, which is
ARX-compressed; the kit decompresses, edits and recompresses it.

Models live in chain 0, which is on both discs: patch each disc you play.

## Text: `text-export` / `text-import`

See the README. Banks are rebuilt whole, so lines may grow or shrink inside
the bank's slot; an import that would not fit is refused as a whole.

### Growing a text bank: `text-import --grow` (not yet seen in game)

**Status: read from the code and exercised on disc images. NOT YET SEEN IN
THE RUNNING GAME.** With `--grow`, a `.sbl` that outgrows its slot is appended
to the image and its TOC entry repointed (the same mechanism as `patch
--grow`, which the game followed for a movie subtitle file, below).

Whether a larger bank is safe to load was read from the USA executable
(SLUS_208.92, 2026-11-10):

* **The size comes from the TOC.** `FUN_0019bc98` (through `FUN_0019c1e0`)
  finds the path in the TOC (`FUN_0019b720`), reads `size` rounded up to
  2,048 bytes from the entry's sector into the address it is given, and
  returns the rounded size. No other copy of a bank's size exists: the
  script finds its strings through the bank's own header
  (`FUN_00a9f238`: count at +0x08, offset table at +0x14, strings at +0x18).
* **The destination is a buffer of fixed size, shared with the script.**

  | | scenes | maps |
  |---|---|---|
  | loader | `FUN_001eece0` | `FUN_001e00f0` |
  | script | `cf\scNNNN.sb` at 0x3C6DC0 | `model\map\X.bin` at the block from `FUN_001df928(0x12000)` |
  | bank | `cf\en\scNNNN.sbl` at 0x3C6DC0 + script size rounded up to 2,048 | `model\map\en\X.sbl` at the block + script size rounded up to 64 |
  | buffer | 0x20000 bytes (the script-native tables start at 0x3E6DC0) | 0x12000 bytes |
  | rule | `round2048(sb) + round2048(sbl) <= 131,072` | `align64(bin) + round2048(sbl) <= 73,728` |
  | retail | 352 pairs, least room 10,240 bytes (`sc8014`) | 113 pairs, least room 1,664 bytes (`E2_EXB06`) |

  So a bank may grow, but only inside its script's buffer. Past that the
  read runs over whatever follows (for scenes, the table of script natives).
  `text-import` computes the room for every bank (it is printed in the header
  of each exported file as `loader room N bytes`) and **refuses a bank past
  it with or without `--grow`**. `E2_EXB06`'s bank, for one, cannot leave
  its 2,048-byte sector at all.
* A bank anywhere else (none on the retail discs) has no known loader and is
  not relocated. OV01's `FUN_00a81a98` would load `yamamoto\think\thinkNNN.sb`
  and `yamamoto\think\jp\thinkNNN.sbl` into one 0x12000-byte block; the USA
  discs carry no such files.
* The map rule is read from the branch taken when `DAT_0069e1bc` is 0 (the
  id passed to `FUN_001e0610` is not negative). The other branch loads into
  space measured from the TOC sizes.

What `patch_iso` rewrites for a relocated object, checked on a copy of USA
Disc 1 (one bank grown from 284 to 2,984 bytes): the TOC entry's sector (3
bytes) and size (4 bytes), the ISO volume size (both byte orders), and two
new sectors at the end of the image. Nothing else in the image differed, the
other 8,604 TOC entries were unchanged, and for an ARX-compressed object the
entry's compressed and uncompressed sizes are both rewritten (the ARX header
carries its own). A re-export of the patched image differed in the one edited
line only.

Japanese discs: text is inside the scripts and is edited in place, same
length or shorter; `--grow` does not apply to it.

## Any file: `patch`

```sh
python cli.py patch --iso "Disc 1.iso" --out t.iso --set 'chain1:movie\en\s010100.txt=my.txt' [--grow]
```

Movie subtitles are plain EUC-JP text in `movie\en\<movie>.txt` with
`mm:ss:ff` timings (frames at 30 per second); lines starting with `;` are
comments, `\n` breaks a line, `$ay390;` sets the height. Disc 1's first
story movie is `movie\s010100.sfd` (the news report), its subtitles
`movie\en\s010100.txt` (in chain 1, so `chain1:`).

## Checked in the running game (PCSX2 2.9, 2026-11-05)

* **Title menu recolour** (`--xtx yajima\titleobj.xtx`, ARX recompressed):
  NEW GAME / CONTINUE drawn pink on the title screen.
* **Relocation works.** A subtitle file appended past the original end of
  Disc 1 (LBA 2,286,192) with its TOC sector repointed played in the first
  movie: once relocated at its old size (`--relocate`), once grown with
  comment lines to 8,794 bytes in an 8,192-byte slot (`--grow`). Movie at
  full speed, every line on time, no emulator errors.
* **Subtitle limit (important).** The movie player reads `<movie>.txt` into
  the start of the block it allocates for the Sofdec decoder
  (`FUN_001e7d08` in the boot ELF: `FUN_001e1830(FUN_001a07b8(...))`),
  compacts the cues in place (comments dropped, `FUN_001e7a08`), and starts
  the decoder's work area right after them, rounded up to 64 bytes
  (`FUN_001a0840(..., buf + align64(len), ...)`). The block is sized for the
  decoder alone, so **the displayed text may not grow past the next 64-byte
  boundary** of the retail cue data. For `s010100.txt` that headroom is
  about 12 bytes (cues ≈ 4,532 B): adding 52 characters to one line moved the
  work area 64 bytes and overran the block; the movie crawled at 2 frames per
  second and PCSX2 logged tens of thousands of "Vif: Invalid Unpack" errors.
  Comment lines are free; equal-length or shorter edits are safe. Translations
  that lengthen subtitles need the engine changed (or the decoder block
  enlarged), not just the text file.
* **KOS-MOS's hair** is patched in both test discs but she is not on screen in
  the first minutes, so it is not yet checked in the game.
