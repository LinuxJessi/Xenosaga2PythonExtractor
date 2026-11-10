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
