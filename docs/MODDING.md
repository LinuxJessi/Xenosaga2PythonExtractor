# Modding Xenosaga Episode II with the kit

Everything here writes into a **copy** of your disc image. The original is
never opened for writing. `--out` must name another file: the input itself is
refused, also through a symlink or a hard link. The copy is made and patched
under `<out>.partial` and takes the name `<out>` only after every write has
been read back, so a refused or failed command leaves no half-written image
and an existing `<out>` as it was (`repack.patch_copy`).

Only discs the kit knows are patched: USA SLUS_208.92 / SLUS_211.33, Japan
SLPS_253.68 / SLPS_253.69, the demo SLPS_253.53, and for text also only with
the exact executable the rules were read from (sha256 checked). Any other
serial can be listed, dumped and browsed; every writer refuses it and says
which serial it saw.

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
python cli.py recolor --iso pink.iso --out pink2.iso --xtx 'yajima\titleobj.xtx'    # a second edit: a new file again
```

A texture is named by its `.txy` entry, the names the artists gave them
(`kosmos00_hair_col`, `shion00_face`; `browse` writes them as file names).
`--rule GLOB=MODE` picks entries; `all` sets the hue of every colour of the
entry's palette, `blue` only of its blue colours and of the cool soft edge
where they fade into skin (for faces: the skin stays, a blue fringe changes
and keeps no blue-grey outline). The preset `kosmos-hair` is
`kosmos*_hair_col=all`, `kosmos*_hair_spe=all`, `kosmos*_face=blue`.

**Every copy is found by structure, not by bytes.** The command parses every
standalone `.txy` and every texture page embedded in an effect file
(`embedded.py`), finds each entry the rules name, and edits the palette tile
that entry reads, wherever that tile sits in that file. KOS-MOS's hair is
`kosmos00_hair_col` in `kosmos00.txy` and `kosmos01_hair_col` in `kosmos01`,
`02` and `03`, at four different palette positions, so a byte search for one
palette finds one copy. (Episode III's kit learned the same lesson: costume
variants carry re-baked palettes.) Result for the preset on Disc 1: 12
entries in 4 files, about 600 colours each, all patched in place.

`--xtx PATH` colourises a true-colour `.xtx` sheet instead (no palette;
every visible pixel takes the hue with lifted saturation, so white text
turns pastel). The title menu words live in `yajima\titleobj.xtx`, which is
ARX-compressed; the kit decompresses, edits and recompresses it.

Models live in chain 0, which is on both discs: patch each disc you play.

## Editing textures: `texture-import`

```sh
python cli.py browse --out out/ --kinds textures,xtx,models       # the PNGs and .glb files to edit
cp -R out/browse edited/                                          # edit PNGs in the copy
python cli.py texture-import --iso "Disc 1.iso" --tree edited/ --dry-run
python cli.py texture-import --iso "Disc 1.iso" --tree edited/ --out modded.iso --preview prev/ [--grow]
python cli.py texture-import --iso "Disc 1.iso" --verify-roundtrip  # writes nothing: a self-check on the disc
```

Paint over any PNG `browse` wrote and give the folder back. The command
compares every PNG with what the disc decodes to and writes only the ones
that differ, into a copy of the disc. Nothing else needs to be in the folder:
a tree holding just the three PNGs you edited works the same as the whole
`browse` output. The folder itself is never written to.

**The tree.** `--tree` is a `browse` folder (with `textures_png/`, `xtx/` and
`models/` under it) or a `textures_png` folder on its own; `--textures`,
`--xtx` and `--models` point at those folders separately. Paths and file
names must be the ones `browse` writes: `textures_png/chain0/<path>/<file>.txy/NNN_<name>.png`
for a texture page, `textures_png/_embedded/chain0/<file>/<0xoffset>/NNN_<name>.png`
for a page inside an effect file, `xtx/chain0/<path>.xtx.png` for a UI sheet,
`models/chain0/<path>.glb` for a model. NNN is the entry number. If a file
name does not match the entry it names (a renamed copy, or a tree written by
another kit revision) or a PNG has another size, the whole import stops
before anything is written and lists every such file. Delete or rename
stray PNGs (`notes.png`, `copy of ...`) out of the tree.

**What counts as unchanged.** A PNG is unchanged when every pixel equals the
disc's decode, with one allowance: pixels whose alpha is 0 count as equal
whatever RGB they carry, because paint programs often zero the colour of
transparent pixels. Alpha is compared after snapping to the values the PS2
can show (below).

**The sweep.** The same picture is stored many times on the disc, often under
other names. KOS-MOS's hair is `kosmos00_hair_col` in `kosmos00.txy` and
`kosmos01_hair_col` in `kosmos01.txy`, `kosmos02.txy` and `kosmos03.txy`,
pixel for pixel the same picture with its palette at another place in each
file. By default (`--sweep pixels`) every entry anywhere on the disc
(standalone pages and pages inside effect files) whose picture equals the
edited entry's original picture gets the edit too, each encoded in its own
file's palette. Editing `000_kosmos00_hair_col.png` therefore changes all four
files. Two exceptions keep this from going wrong: a picture of a single
flat colour is only swept to entries of the same name, and every swept group
is printed with its file count and the names in it. Look at that list in a
`--dry-run`: one 32x32 reflection map is stored 1,512 times in 80 files under
many names (`gt01_bodyrf`, `gt03_legrf`, ...), so editing it edits all of
them. `--sweep name` sweeps only to entries of the same name; `--sweep none`
edits only the entries whose PNGs changed. Two PNGs that start from the same
picture but were edited differently are refused under the default sweep.
UI sheets are not swept (each is its own file).

**Re-listed entries.** Some entries are listed twice or three times with the
same rectangle and palette (the duplicate flag at +0x28; `browse` names them
`..._dup1.png`): `kosmos00_ude` is entries 5, 7 and 21. They are one picture
in the file, so editing one changes all; the others are reported as
"ride-along". Editing two of them differently is refused.

**Palettes.** Most textures are 256- or 16-colour pictures, and a palette is
often shared: `kosmos00_ude` and `kosmos00_body` are different pictures that
read the same palette at (208, 80). `--palette auto` (the default) never
changes a colour another texture uses: new colours go into slots no other
texture uses, exactly when they fit (a recolour of a picture with few
colours is lossless), otherwise the edited picture is quantised into those
free slots around the frozen ones. When the other textures use every slot,
the palette cannot change, and the changed pixels take the nearest existing
colour; the command warns, names the other textures and gives the PSNR. On
Disc 1 that happens often: of the 32,114 ways to edit one picture of a shared
256-colour palette, 45.6% leave no free slot and 71.3% fewer than eight
(median one free slot); for shared 16-colour palettes 95.7% leave none.
`--palette joint` re-quantises the edited picture together with the other
textures of the palette into all slots; those textures then change slightly
and are reported as "joint-changed". `--palette keep` never touches a
palette. `--dither fs` applies Floyd-Steinberg to the quantised pixels only
(RGB error, never alpha). Measured on the research cases with the shipped
encoder (whole-picture PSNR after a synthetic edit with hundreds of new
colours): `kosmos00_hair_col` on its own palette 46.1 dB; `kosmos00_ref1`
on a shared palette 39.6 dB (auto, siblings unchanged), 41.2 dB (joint),
28.7 dB (keep); `brealm00_douback` 55.1 dB (auto). The report CSV gives
the PSNR of every changed entry over its changed pixels.

**Alpha.** The PS2 stores alpha as 0-128 (128 = opaque) and the kit shows it
doubled, capped at 255. An edited alpha is stored as min(128, (A + 1) / 2
rounded down), so the alphas that come back exactly are 0, 2, 4, ..., 254 and
255. A pixel or palette colour that does not change keeps its stored bytes
exactly, including the title sheet's raw alpha values above 128.

**Mip levels.** Some map textures carry smaller copies of themselves (entry
word +0x34 numbers them 1-6 under a base marked 0xFFFFFFFF; same name and
palette), and the models' draw state asks the GS for them (see FORMATS.md).
By default (`--mips rebuild`) the smaller levels of an edited base are
rebuilt from the edit with a 2x2 box filter (RGB weighted by alpha) and
written in the same palette pass; `--mips keep` leaves them, and the
command says how many edited entries have levels.

**UI sheets.** Sheets whose palette the kit has solved are written: the 50
citizen portraits and 18 item cards (cut by their frame mask, as `browse`
shows them; pixels the mask hides cannot change and keep their colours),
the three frame masks, `omiya\botan`, and the true-colour title words
`yajima\titleobj.xtx`. The ten scan-palette sheets and two grey sheets are
refused when edited ("palette not solved") and ignored when not. A sheet and
the frame mask that cuts it are not edited in the same run: the sheet's PNG
was cut by the old mask, so the command refuses the pair. Import the mask
alone, run `browse` on the new disc, then edit the sheet.

**Models.** Images inside a `.glb` from `browse` or `model` are named after
their PNG files (`009_kosmos00_mune`), so a model edited in Blender maps back
by name: File > Import > glTF 2.0, paint or replace an image, File > Export >
glTF 2.0 (.glb), then put the file at `models/chain0/<path>.glb` in the tree.
Checked with Blender 5.4.0 on 2026-11-20: a plain import and export with
default settings comes back as 0 changed entries (the images are re-embedded
byte for byte, only their order changes); painting a 32x32 square on one
image changes exactly those 1,024 pixels. A `.NNN` tail Blender adds to a
repeated image name is ignored. Files exported before images were named this
way (entry names, which repeat) are mapped by entry names that are unique
among the entries the model uses. Image order is never used, because Blender
writes images in its own order: an image whose name matches nothing (for
example one replaced in Blender, which takes the new file's name) and two
images that map to the same entry stop the import. Rename such an image to
its PNG file's stem, or export the model again with `browse`.

**Size.** Texture pages never change size (index bytes and palette words are
rewritten in place). Only the three ARX-compressed objects can outgrow their
slot. `yajima\titleobj.xtx` with a gradient painted over its letters
recompresses to 43,944 bytes in a 28,672-byte slot; the command says so
before copying the disc and needs `--grow`, which moves it to the end of the
image (relocation is proven in the game for a subtitle file; for this sheet it
is not yet checked).

**Run it once per disc.** Models, effects and the title sheet live in chain 0,
which is identical on both discs; run the same tree against each disc you
play. The Disc 1 and Disc 2 reports of the demo are identical.

**Checks.** Before writing, every block is encoded and decoded again with the
scalar reference decoder; after writing, and before the copy takes the
`--out` name, every replaced object is read from the new image and decoded: edited, swept, ride-along and joint-changed
entries must show their planned pictures, every other entry its original
one, and blocks inside a file that were not edited must be byte-identical.
If any of this fails, the copy is deleted and nothing is written (an existing
`--out` stays as it was). The report (`--report`, default
`<out>.textures.csv`) is written only after a verified copy is in place, or
on a dry run; it may not name `--out`, `--iso` or any `.iso` file.
`--verify-roundtrip` encodes every texture on a disc with its own picture as
the target and checks the bytes come back identical: on Disc 1, 937
standalone pages, 2,167 embedded pages (69,482 entries) and 73 UI sheets, no
failure, 55 seconds. A no-op run over the whole `browse` output (114,583 PNGs
and model images) takes 26 seconds and writes nothing.

**Not yet seen in the running game.** The demo (KOS-MOS's hair pink all over
in all four of her texture files: the hair, its shine and the hair drawn on
her three face pictures; her arm texture in green, a ring on
her chest texture through a `.glb`, and the title menu words tinted) is
written and verified on disc copies only; the PCSX2 boot check is pending.
`tools/demo/texture-import.sh "Disc 1.iso" out/ demo-textures/` runs it from
a full `dump` in `out/`: paint (a script stands in for the image editor),
dry run, import onto a copy in `demo-textures/discs/`, re-extract, and the
checks (before/after pictures, unrelated objects byte-identical, every copy
of an edited picture changed, an unedited full tree writes nothing).
Also unverified: whether the engine reads palettes through `cbp` or through
palx/paly (they agree for every real entry, so in-place writes are right
either way), how the engine treats the title sheet's alpha values above 128,
and whether edits inside effect files are accepted (no checksum was found or
assumed).

## Text: `text-export` / `text-import`

Banks are rebuilt whole, so lines may grow or shrink inside the bank's slot;
an import with any problem is refused as a whole and nothing is written.

### The tree

`text-export` writes one file per text object and `xs2-text-export.json`,
the manifest. In a block file (`### KEY` lines):

* the line after `### KEY` is the string; if you type several lines they are
  joined with the game's `\n`; empty lines at the end of a block are dropped;
* **a line that starts with `#` is a comment**, not text. To begin a string
  with `#`, write `⟦23⟧`: the export does so itself. A `#` later in the line
  is plain (the Japanese `#XX` codes in the middle of a line are untouched).
  No retail string starts with `#` (0 of 9,699 + 903 + 1,085 USA strings, 0 of
  17,225 + 902 + 1,085 Japanese, 0 on the demo), and none holds a raw line
  break, so this costs nothing today; it is there for what you type;
* `⟦XX⟧` is a raw byte; a string may not contain `⟦00⟧`;
* every block must come back once, in the export's order. A missing,
  repeated, unknown or moved key is refused (all block kinds: banks, inline
  scripts, menus, battle strings).

The manifest holds the build and the sha1 of every exported object.
`text-import` refuses a tree without it, a disc of another build, and any
object that is not byte for byte the one the text came from, with
`--rebase` as the explicit override. A tree exported from Disc 1 imports into
Disc 2 of the same region (chain 0 is identical; each object is still
checked), which is how both discs get the same dialogue edits.

### What you can type

The two builds read text differently (`textcodec.py`, from the print
functions in `font.py`):

* **USA.** A letter with an accent is one Latin-1 byte: type `é`, the disc
  gets 0xE9, the game draws cell 0xE9 + 0x80 of the font. The font's whole
  0xA0-0xFF block is inked (`¡ ¿ ß ç ñ Ø æ ©` and the accented capitals were
  looked at). The few two-byte codes the USA print function accepts read as
  their symbols: `○ □ ■ △ ☆ ★ × — 「 」 ♪ Ω`. Pairs with lead byte A4 are
  shown as escapes, `⟦A4⟧⟦A2⟧`, because the USA font does not hold kana
  there: A4A1-A4A5 are `ö é è Ä ®` to the eye (the retail text writes `é` as
  A4A2 and `®` as A4A5) and the rest of that row is a placeholder mark.
  Kana and kanji cannot be typed: the USA print function cannot draw them.
* **Japan and the demo.** EUC-JP (Python's `euc_jis_2004`).

Every non-ASCII character you type into a changed string is checked against
the fonts of the disc being patched (`textcodec.check_typed`), and refused
with the reason when

* the build cannot encode it, or it would need an EUC prefix byte (0x8E
  half-width kana, 0x8F plane 2): the game reads one- and two-byte characters;
* the game would read its bytes differently (USA: `¤`, `¦` and the soft
  hyphen always swallow the next byte);
* its code falls to the font's fallback cell 0x7F (Japan: `～` U+FF5E, any
  kanji outside level 1 and the 87 the executable lists), its cell is out of
  the draw call's reach, or its glyph cell is blank in font0 or font1.

Letters the font does not have (Arabic, Devanagari, Georgian, Ethiopic,
Cyrillic, ...) cannot be typed straight into the tree: `lang-scan` draws them
into the fonts first and `lang-encode` writes them as escapes, ordered and
wrapped. See [ALPHABETS.md](ALPHABETS.md).

An explicit `⟦XX⟧` escape is never checked. Unchanged strings are never
checked. The retail text itself passes: retyped through this check, none of
the 587 non-ASCII USA strings and none of the 15,536 Japanese ones would be
refused (one of the demo's 5,055 would: a code with no cell in its font). Not
checked: `$` codes, `%` directives and `[Speaker]` tags you change.

### Battle banners

The scripts pass 416 different names, in 1,363 calls from 113 map banks, to
the native `bat.namePut` (OV01 `FUN_00aa1330`: the fourth argument is
looked up in the bank through `FUN_00a9f238`, the lookup `bat.msgPut` uses,
and handed to `FUN_002355d8`, which draws the box). They are the enemy skill
and action names (`$zoom13;Regenerate Consciousness`, `$zoom13;Point Blank
Rapid-Fire (Strike/Pierce)`), and they are not in `batdat.us`: each bank
that uses a name has its own copy. The export marks every banner block with
`# battle banner (bat.namePut)` and, when the same name is in several banks,
how many. `text-import` then:

* notes (does not refuse) a name whose copies were not all edited alike,
  listing the banks with each text, since each copy is shown in its own
  scene's battles;
* refuses a banner that ends with a one-byte letter from 0x80 up (`é` last):
  the banner's width function `FUN_002353b8` takes every byte from 0x80 up
  together with the next byte, so it would step over the terminator and read
  past the string;
* notes a banner wider than any banner of the disc. `FUN_002355d8` makes the
  box `FUN_002353b8(text) + 0x18` wide with no upper limit; `FUN_002353b8`
  scales each character by `$zoomNN;` over 16. The kit estimates the width
  with the font's metrics (the state the codes `19 03` set for the width
  function is not modelled, so it is an estimate) and compares it with the
  widest retail banner; the screen's own limit was not read.

`samp.fontPut` draws the strings `100`, `802` and `102` (28 calls in 17
scene banks); they look like a debug or template routine (not checked in
the game). Both are counted by `text-coverage`, and `dialogue` lists the
banners as their own kind.

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
carries its own): `yajima\title.bin`, relocated with a changed payload, read
back with its new compressed size in the TOC and in its header and
decompressed to the new payload. A re-export of the patched image differed in
the one edited line only.

Japanese discs: text is inside the scripts and is edited in place, same
length or shorter; `--grow` does not apply to it.

### The other text: menus, subtitles, quests, battle names

`text-export` also writes, and `text-import` reads back:

| Text | On the disc | In the tree | What an edit may do |
|---|---|---|---|
| menus | `yosikawa\MainMenu.txd`, `Tutorial.txd`, `Location.txd` | `X.txd.txt`, `### NNNN` blocks | strings grow and shrink; the file may not need more sectors than the original |
| movie subtitles | `movie\en\*.txt` (Japan `movie\*.txt`) | `X.sub.txt`, the file itself | text only, inside the bounds below; `--grow` may relocate the file |
| quest, citizen and treasure text | `iwase\*.txt` (Shift-JIS) | `X.sjis.txt`, the file itself | only the text between double quotes, each text within its field (below); the file may not need more sectors than the original |
| battle names and descriptions | `yamamoto\batdat.us` (Japan `batdat.bin`) | `batdat.us.txt`, `### S<section> @<offset> max N bytes` blocks | USA: a string may grow, the strings after it in its section move down, the file within its sectors (section 17 in place); Japan: in place, same length or shorter |
| the save's title in the memory-card browser | `carddata.bin` (Japan `carddata.new`), its `icon.sys` | `carddata.bin.title.txt`, one `### title` block | in place, at most 67 bytes, the slot and time characters kept (below); USA only |

An unedited export imports as "no text changed" on every disc, and each
object rebuilt from its unedited text is the original byte for byte: 3 menu
files, 20 + 25 subtitle files, 6 quest files and the battle database on the
USA discs; the same counts on the Japanese discs; 2, 3, 6 and 1 on the demo.
(For the battle database "rebuilt" means every string re-encoded and put back
in place; a string moves only when an edit makes one before it longer.)

Why these limits, and how sure they are:

* **Menus** are read into static buffers (`FUN_00228570`: `MainMenu.txd` to
  0x6A5BC0 and `Tutorial.txd` to 0x6A9BC0, 16,384 bytes apart;
  `Location.txd` through a pointer in `FUN_0023ad18`). The read is whole
  sectors of the TOC size, so the rule is **no more sectors than the
  original**, whatever the slot is (on the retail discs every slot equals the
  sector-rounded size anyway: 495 of 495 USA text objects, checked on all
  five discs). They are never relocated. Read from code, not seen in game.
* **The memory-card slot number** (USA). Twelve `MainMenu.txd` prompts end
  "...in MEMORY CARD slot £°." (blocks 0218, 0539, 0541, 0543-0548, 0551,
  0553, 0559). The `£°` is the bytes A3 B0, a placeholder: before the prompt
  is shown, `FUN_00238038` walks the string the way the print function does
  (a byte from 0x80 up steps two bytes, any other byte one) and at the first
  placeholder it finds (82 4F, A3 B0 or `##`, read from 0x69DA30, 0x69DA38
  and 0x69DA40), or at a digit it wrote before (07 then `1` or `2`), it
  writes 07 and the slot's digit. `FUN_002381a8` does this to blocks 0x21F,
  0x220, 0x224, 0x227 and 0x22F (543, 544, 548, 551, 559) and `FUN_0023b210`
  to 0x22F; whether the other seven are stamped by code not read, or ever
  show `£°` as it is, is not known. The export writes a `#` note above every
  block that holds a placeholder, and `text-import` refuses an edit of such a
  block unless the same walk still finds the same number of placeholders:
  keep `£°` once, where the number belongs, and put a space or another plain
  ASCII character right before it. An accented letter right before it is
  refused, because the walk would read that letter with the `£` as one pair
  and never see the placeholder.
* **The Skill Tutorial** (`Tutorial.txd`, 255 entries) is a script, not a
  list of messages. `FUN_00215f10` runs the entries in order by their first
  byte: `%` is a command picked by the next bytes (`%p`; `%tNN`, which
  counts NN runs of the routine, two digits; `%c+`, `%c-`, `%l0` to `%l2`,
  `%a0` to `%a6`, `%f`, `%m`, `%g`, `%j`, `%P`, `%s`, `%e`, `%r`), `*` calls
  `FUN_00215de8`, an empty entry or one that
  starts with `#` is skipped, and any other entry is a page of text. On the
  USA disc 56 entries are text and 199 are commands or empty. The export
  marks every non-text entry with a `#` note; `text-import` refuses a
  change to one, and refuses a page of text edited to be empty or to start
  with `%`, `*` or `#` (it would then be skipped or run). The same rule is
  applied to the Japanese `Tutorial.txd`, though only the USA function was
  read.
* **Quest text**: `FUN_00232100` allocates by the file's TOC size
  (`FUN_002320b0` asks `FUN_0019bc68` for the size and passes it to the
  allocator `FUN_0020b738`), so a larger file might load; how much room that
  heap has has not been read, and the same "no more sectors" rule is applied.
  Only the text inside `"..."` may change: the rest of the file (record
  numbers, braces, field names, the ruler lines of the Japanese files) is
  compared with the original. The files are Shift-JIS; on a Japanese disc a
  typed character is checked through its JIS code (assuming the game converts
  one to the other).
* **Quest characters on the USA build.** The copy `FUN_0022ec18` takes a
  byte below 0x80 as it is, and every byte from 0x80 up together with the
  byte after it, whatever that is; it then rewrites the pair in place with
  `FUN_0023ebb8`, which is Shift-JIS to EUC-JP arithmetic (the lead doubled,
  then 0xA0 or 0x20 added when the trail is above 0x9E and the trail plus 2,
  else 0x9F or 0x1F added and the trail plus 0x60 or 0x61). The retail files
  hold three such pairs: `81 5C` in `QuestMes.txt` ("Sidney―my sweet")
  becomes `A1 BD`, the dash; `83 B6` in `Treasure.txt` ("Ω System") becomes
  `A6 B8`, Omega; and `82 A0` in `Citizen.txt` ("Moby Dick's Caf..")
  becomes `A4 A2`, the cell the USA font draws as `é`. So the tree of these
  three files shows what the game draws: a pair whose converted bytes the USA
  print function reads as one of its symbols is that symbol (`―`, `Ω`), any
  other pair is two escapes of the file's bytes (`Caf⟦82⟧⟦A0⟧`). In a
  changed line you may type the USA build's two-byte symbols (`― 【 】 × ☆ ★
  ○ □ ■ △ ♪`, the Greek letters and the rest of row A6), which are written
  as the Shift-JIS pair that converts to them and checked against the font
  like any typed character. A one-byte Latin-1 letter (`é` = 0xE9) cannot be
  written: the reader would pair it with the next character and convert
  both. A kana is refused too, because it converts to a cell of the USA font
  that does not hold that kana. Escapes are written as given, so `⟦82⟧⟦A0⟧`
  still gives the retail `é`. None of this has been looked at in the running
  game: no edited quest text has been seen on screen, and how the USA draw
  path renders a converted pair is read from the code only.
* **Quest fields.** The file is not kept as it is: each loader reads a fixed
  number of records into a zeroed buffer of fixed-size records and copies
  every text into its field (`FUN_0022ee08` for `QuestMes.txt`: 36 records of
  0xB46 bytes, fields `Subject` 31, `Locate` 17, `Client` 17, `MesIPG`,
  `MesSCD`, `MesFLD` 257, `UkunSCD`, `UkunFLD` 1,025 bytes; `FUN_0022f000`
  for `Citizen.txt`: 50 records of 0xD7, `Name` 17, `Sex` 7, `Age` 17,
  `Hobby` 23, `Profile` 151; `FUN_00232160` for `Treasure.txt`: 18 records of
  0xA9, `Item` 21, `Locate` 27, `Expl` 121). The copy, `FUN_0022ec18`, checks
  no length and writes no terminator of its own, so a text may use its field
  less one byte: a longer one would run into the next field. On the USA disc
  several retail texts use exactly that (`Subject` 30 of 31, `Sex` 6 of 7,
  `Hobby` 22 of 23). `text-import` walks each file the way the loader does
  and refuses a text past its field, naming the record and the field. In
  the count, `\n` is one byte, `\"` one, the line ends inside the quotes
  none (the copy skips CR LF), a byte from 0x80 up two. The `...2.txt` files
  (`QuestMes2.txt`, `Citizen2.txt`, `Treasure2.txt`, Japanese placeholder
  text) are exported too, but no USA executable names them.
* **Quest line ends.** The copy skips CR LF inside a text but copies a lone
  LF as a line break, so the files must keep their CR LF. If an editor saved
  the whole file with LF line ends, `text-import` puts the CR LF back on every
  line; a file that mixes the two is refused with "keep CR LF line ends".
* **The memory-card title** is `icon.sys`, member 0 of `carddata.bin`: the
  title at +0xC0 (68 bytes, Shift-JIS, zero-padded) and at +6 the byte
  offset where its second line starts. The tree shows the two lines joined
  by `\n`; on import the offset is set to where your `\n` stands (it cannot
  land inside a character). When the game saves, `FUN_0019dc10` (called from
  `FUN_00239f98` at 0x0023A8C4 with the loaded pack, the slot number + 1 and
  the time) writes into the title: the trail bytes of the slot digits at
  title bytes 27 and 29, the hour's tens as a whole character at 32-33 (a
  full-width space or digit), and the trail bytes of the hour's ones and the
  minutes at 35, 39 and 41. So the characters at bytes 26-29, 32-35 and 38-41
  (`００`, `００`, `００` of `−００［００：００］`) must stay where they are,
  and the text before them must stay 26 bytes; anything else may change, up
  to 67 bytes in all. The pack keeps its size. Only the USA save writer was
  read, so on a Japanese disc the title is exported and edits are refused.
  How the PS2 browser draws half-width characters in a title has not been
  checked.
* **Battle database**: loaded by OV01 (`FUN_00a81a70` calls
  `FUN_00a83dc0(path, 0x61d040)`, which reads the TOC size rounded up to
  whole sectors) into a static buffer at 0x61D040. The next address any
  decompiled code names after it is 0x631140, so the buffer is probably
  larger than the file, but its size was not read; the file keeps to its
  sectors (220 spare bytes on the USA disc). The game reaches every section
  through the header: OV01's accessors return `*(int *)(0x61D048 + 4 *
  section) + 0x61D040` plus a record index times the record size, and no
  other code adds 0x61D040 to anything, so the header's section offsets are
  the only file positions the game reads. The strings are read by getters
  that add a signed 16-bit field of a record to the section's start:
  `FUN_00a9b338` (sections 0 and 1, party and enemy names, 0x5C-byte records,
  field +0), `FUN_00a9b750`, `FUN_00a9b850`, `FUN_00a9b7d8`, `FUN_00a9b8d8`,
  `FUN_00a9b950`, `FUN_00a9b9c8`, `FUN_00a9ba40`, `FUN_00a9bab8`,
  `FUN_00a9bb30` (sections 6-14, records of 12, 12, 16, 16, 4, 8, 16, 16 and
  16 bytes, name +0 and description +2) and `FUN_00a9bba8` (section 18, the
  battle messages, a plain list of offsets). On both retail files every one
  of those sections' records ends exactly where its first string starts, all
  1,150 such fields of the USA file point at a string start or at a NUL (an
  empty text), and every string the export lists in those sections is
  reached by one of them. No other column of those records does that: only
  8 other u16 values in them equal a string's offset, and 7 of the 8 are the
  same number at the same place in the Japanese file, whose strings sit
  elsewhere: numbers. So on the USA disc a string may get longer: it is written
  where it is, the rest of its section moves down, exactly the fields above
  that point past it are moved by the same amount (nothing else in the
  section changes), the section is padded to a multiple of 4 (every section
  offset of both files is one), the later sections move down whole and the
  header's offsets and size follow (`smallfiles.batdat_rebuild`). The result
  is read back the way the getters read it: every field must give its old
  text, or the new one. Refused: a file past its sectors, an offset past
  0x7FFF, and a longer string in section 17 (grade letters), whose fields
  were not read, or one that no getter's field points at. Shorter strings
  stay in place as before; a string may not be emptied, and a section is
  recognised by the first byte of its first string, which must stay a
  printable ASCII or EUC byte. The Japanese file is edited in place only
  (the getters were read in the USA overlay). Not seen in the running game
  yet: no grown battle string has been looked at in PCSX2.
* **Inline scripts (Japan)**: 470 variable slots in 119 scripts hold a value
  that falls inside a pool string rather than at its start. They look like
  numbers (281 are multiples of ten; 7893, 19456, 2800, 400, 2000 lead the
  list), but a pointer into the middle of a string cannot be ruled out from
  the files, and an in-place edit would show through it.
* **Subtitles**: see "Subtitle limit" below, and the notes under it.
* **Battle banners** are bank strings: the name of an enemy's skill or action,
  which a battle shows in a box (`$zoom13;Medica`). See
  "Battle banners" below.
* **Japanese leftovers.** 127 Shift-JIS strings in 64 USA banks and 20
  EUC-JP strings in 10 (`sc1270`, `sc3180`, `sc3200`, `sc4620`, `sc4640`,
  `sc4670`, `sc4690`, `sc4700`, `sc4720`, `sc6110`) are the developers' test
  lines ("11テストメッセージ\n表示中", "12の人です"), and `MainMenu.txd`
  blocks 0318 and 0399 are EUC-JP notes ("↑予備ではなく、空欄で　コンディション予備",
  "以下雑用データ"). The USA codec shows their bytes as Latin-1 letters, so the
  export adds a reading line above each, `#sjis: ...` or `#euc: ...` (a
  comment: it is not imported). The test lines sit behind the results 11 and
  12 of `talk.check` in a template routine; whether a player can reach any of
  them is not known. They round-trip and can be edited like any string.
The files of the two whole-file kinds must come back exactly as you want
them on the disc: line ends are kept as they are (LF in the subtitle files,
CRLF in the quest files, which `text-import` restores when an editor turned
all of them into LF), and `⟦XX⟧` is a raw byte.

## Executable strings: `exe-export` / `exe-import`

```sh
python cli.py exe-export --iso "Disc 1.iso" --out exe/
python cli.py exe-import --iso "Disc 1.iso" --text exe/ --out modded.iso [--retail "Disc 1.iso"]
```

Most of Episode II's interface words are in the menus and the battle
database, which `text-export` writes. A few displayed strings are still C
literals in the game's code: `Saving...` (0x0068B678, drawn by the save
screen `FUN_00237830` under `$init;$bg0;$zoom13;$han1;$prop1;...`),
`No items to sell.` (0x0068D430), `Total Exp`, `Class Lv`, `Next Lv`,
`Zone`, the disc-change prompts (`Please insert DISC 2 of` ...) and, in the
title overlay, the memory-card prompts. `exe-export` lists them, and every
other string the code points at, from the boot executable (`BOOT.exe.txt`,
whatever its serial, so a Disc 1 tree also fits Disc 2, whose executable is
the same file), `OV01.OVL` and `OV02.OVL`, beside a manifest
`xs2-exe-export.json` with each file's sha256:

```
# exetext 1 file=SLUS_208.92 role=boot sha256=1f978e64...
### @0x0068b678 max 15 bytes
# refs 1 (hilo 0x0023799c)
Saving...
```

The text is in the same form as the rest of the tree (the USA codec:
Latin-1 letters, `⟦XX⟧` for any other byte). A line break in these strings
is a real 0x0A byte, written `⟦0A⟧`.

**Control codes.** The prompts carry the print function's control codes,
and those read some bytes after them as parameters. The save prompt
`Save game data?` is, on the disc,
`0B 0E 01 01 00 00 00 0D 03 19 03 Save game data? ...`: `0E` takes five
parameter bytes, three of them zero. The scan reads such a string the way
`FUN_00197bb8` does: at each character position a zero byte ends the
string, a byte below 0x20 is a control code followed by its parameters, a
byte from 0x80 up is one character or two (`font.us_two_byte`). The
parameter counts are the ones its switch steps over, and the table at
0x0063EA40 that `FUN_00195178` reads gives the same numbers:

| code | parameters | code | parameters |
|---|---|---|---|
| 01-05 | 1 | 0F | 3 |
| 06 | 3 | 10 | 5 |
| 07, 09, 0A, 0B | 0 | 11 | 6 |
| 08 | 1 flags byte, then 2, 2, 3, 2, 2 for flag bits 0-4 | 12, 13 | 2 |
| 0C | 3 (a colour) | 15 | 1 length byte n, then n |
| 0D | 1 | 16 | 4 (an address: a string to print) |
| 0E | 5 | 17 | 7 (an address: a function to call) |
| 18, 19, 1B | 1 | 14, 1A, 1C-1F | never valid: the loop would not move past them |

In the tree a control code and its parameters are always escapes, so a
parameter 0x20 shows as `⟦20⟧` and not as a space you might delete:

```
### @0x00688590 max 79 bytes
⟦0B⟧⟦0E⟧⟦01⟧⟦01⟧⟦00⟧⟦00⟧⟦00⟧⟦0D⟧⟦03⟧⟦19⟧⟦03⟧Save game data?⟦0A⟧⟦0A⟧⟦0C⟧⟦80⟧⟦20⟧⟦20⟧○⟦0C⟧⟦80⟧⟦80⟧⟦80⟧ button: Yes⟦0A⟧⟦0A⟧⟦0C⟧⟦20⟧⟦20⟧⟦80⟧×⟦0C⟧⟦80⟧⟦80⟧⟦80⟧ button: No
```

Reword the text between the codes and keep the codes. `exe-import` reads an
edited string with the same grammar and refuses a zero byte where a
character belongs, a string that ends inside a code's parameters, a code the
loop never moves past, an added or changed 16/17 code (they carry
addresses) and a `$code` without its `;` (the `$` reader, `FUN_001970e0`,
looks the name up in the table at 0x0063EAA0; `$han` and `$prop` are handled
at 0x00196FA0).

**Which font.** ASCII drawn while `$han1` is off comes from an 8x8 system
font, not from `font0.us`/`font1.us` (`FUN_00197bb8`: only with mode bit 4,
which `⟦19⟧` with parameter bit 1 and `$han1;` set, does it look a byte up in
the table that leads to the font file; `⟦0B⟧` clears the bit). A block whose
own codes put ASCII in that state says so in a comment. On the USA Disc 1
that is the seek-error message (`⟦19⟧⟦01⟧A seek error has occured.`) and
debug screens. `font-extract` does not cover that font and its glyphs have
not been read, so typed ASCII there is not checked; Latin-1 letters always
come from the font file, whatever the state, and are checked as usual.

**Debug screens.** `FUN_00195760` draws only when `(DAT_008DAD04 & 3) == 3`,
a debug switch. A block whose every reference forms the address for a call
to it is marked `# debug (FUN_00195760 only)`: the battle config menu of
OV01 (`--BATTLE CONFIG MAIN--`, `PL MUTEKI= %1d`, ...), the memory-card
test and the field debug screens. Translators can skip those.

**The HDD installer.** The title overlay can install the game to a hard
disk. Its menu entry appears only when `FUN_001a6690` (which asks `hdd0:`
for its state) does not report a failure (ov02 `FUN_00a815f0`: entry 2 of
the title menu stays hidden at -1), so a USA player sees it only with a
hard disk attached; that path has not been run. Its screens are in OV02 (and
in the boot executable's resident copy): the step names in English (`Install
Start`, `Create Partition`, ...) and the prompts in EUC-JP (`インストール中です`,
`ハードディスクドライブにアクセスできません。`). The USA print function
cannot draw kanji (`FUN_00194dd0` accepts few two-byte codes), so those
prompts would show as Latin-1 letters; they are listed, marked as Japanese,
and can be replaced in place with text in the USA codec.

**What is listed.** A string the code points at: a 32-bit word in the data
equal to its address, a `lui` followed by an `addiu`, `ori` or byte load with
the same register (in the same block, or after a branch whose other path
shares the `lui`), or an `addiu` from `$gp` (gp read from the executable's
`.reginfo`, 0x006A2DF0; `Next Lv` and `Zone` sit in `.sdata`). It is at least
two bytes of printable ASCII, tab, line feed or bytes from 0x80 up, with an
ASCII letter or digit in it or reading as Japanese (developer strings, marked
in a comment). A run with control codes is read with the print function's
grammar (above) and taken when it has at least three ASCII letters or reads
as EUC-JP Japanese. Bytes inside the font tables the kit reads from the
executable are never listed. On the USA Disc 1 that is 3,497 strings in the
boot ELF, 678 in OV01 and 47 in OV02, most of them developer messages, file
names and format strings: leave what you do not recognise alone. A few
entries are data that happens to look like text; the export marks the ones
that are visibly a pointer.

A `lui` whose register is rewritten on the straight path but reaches the use
through a branch (OV02 0x00A80350 `addiu a1,s1,0x3348` forms the boot
prompt `Start game?` from `lui s1,0xa8` at 0x00A80294, while another arm of
the function rewrites s1 at 0x00A802B4) is also followed, within its
function: such an address makes a string only at an 8-byte-aligned start
after a zero byte, with three ASCII letters or Japanese text. This also finds
the error messages the Ghidra comparison had shown missing (`cvFs... vtbl
error`, `invalid motion_type`, `can not found parent scale`). Every string of
the Ghidra listings of OV01 and OV02 is found, and every row of a visible-text
survey of the three files (174 rows) is a listed string or lies inside one,
apart from rows that are code or pointer bytes.

**The slot.** A string may use the bytes from its start up to the next
non-zero byte, the next address the code forms or points at, or the start of
a font table, whichever comes first, less one byte for the terminator. That
is the `max N` of its header. `Saving...` has 7 zero bytes after it, so its
slot is 15 bytes of text. The zero bytes after a string are taken to be
padding; a variable that the code reached only through an address below it
would not be seen.

Two things can make `max` smaller than the padding, and both are named in a
comment above the line. An address formed after a branch from a `lui` on
another path ends the slot when it lands in the padding (on Disc 1,
`cdrom0:\` at 0x0063F720 stops at 15 bytes because `FUN_001a5ff8` copies 80
bytes from 0x0063F730, and `pfs1:/Your Saves` stops at 23 because 0x00645190
is passed to a function). And when the code copies a string with a size it
knows (an inlined `strcpy`: word and byte loads at fixed offsets up to the
terminator), `max` is that size less the terminator, so the copy keeps it:
`(Player)`, `sc0000.sb` and OV01's `$han1;$prop1;$zoom15,10;...` cannot grow
past their retail length.

**Bytes the code writes.** The scan also follows a register that holds a
string's whole address (`lui` then `addiu`) and sees stores at a fixed offset
from it. Some strings are templates the game fills in before it draws them:
`Path No. 01 achievement level. ...` (0x003EC9A8) gets the path number in
its bytes +9 and +10 (`FUN_0022e9e0`, read from code), `sc0000.sb` a scene
number in +2..+5. Their blocks say "the code writes byte +9 ...", and an edit
must keep the same bytes at those offsets (counting from +0), or the game
writes the digits over the middle of the new text. Eight strings of the boot
ELF are marked; none in the overlays.

**Conversions.** Many of the strings are formats for a printf-style function
that the code calls with a fixed set of arguments (`$han1;%s` at 0x0068D570
goes to `FUN_002aa5d8` with one argument). An extra `%s` or a `%n` would make
it use a garbage pointer. Import compares the `%`-conversions of an edit with
the exported string by the kind of argument each reads and refuses any
change: you may reword around them and change flags or widths, `%d` may
become `%x` (both read a number), but nothing may be added, dropped or
reordered; `%%` is a percent sign and reads nothing. Which strings really go
to a formatter has not been traced, so every string is held to this, and the
blocks with conversions list them in a comment. The game's `$` codes are not
compared (as in `text-import`); keep them.

**In place only.** `exe-import` writes each changed string over its own
bytes and fills the rest of the slot with zeros. Nothing moves, no file
changes size, and nothing but the edited strings changes: on Disc 1, two
same-length edits changed 16 bytes of the 4.68 GB image, all inside
`SLUS_208.92`, and its directory record stayed as it was. A string longer
than its slot, a NUL, a changed header or a moved, missing or extra block
is refused, naming the address and the slot, and so are changed conversions
and moved stamped bytes; then nothing is written.
Typed characters are checked against the fonts of the disc being patched, as
in `text-import`; escapes are not checked.

**Why no executable string grows.** Moving a longer string elsewhere and
pointing its references at it was looked at (2026-11-20) and left out,
because no place to put it is proven free. The boot executable's data
segment has 0x3E83FC file bytes at 0x002B5D80 and runs on as `.bss` to
0x009D5D70: its file part cannot grow without moving the `.bss` that code
addresses directly, and zero bytes inside it may be zero-initialised
variables the code reaches through a base address plus an offset, which the
reference scan cannot see (the limit named in `exetext.py`). `OV01.OVL` and
`OV02.OVL` load at 0x00A80000, and what lies after them in memory, and how
much room there is, has not been read. Until one of these is read from the
code, a translation has to fit each string's slot; the `.txd` menus, the
battle database and the dialogue banks, where most text lives, do grow.

**Left out on purpose.** One EUC-JP string of the boot executable is not
listed: at 0x00688F88, `左右：増減`, `□：１００ＫＢ単位` and `○：作成（時間掛かります）`
joined by blank lines (the controls of a memory-card dummy-file maker that
works in 100 KB steps). Its only reference is `FUN_001eaf88`, which passes
it to the debug print `FUN_00195760` and loads `yajima\mc\*.bin` and
`yajima\thumb.jpg`, files that are on neither USA disc. No call to
`FUN_001eaf88` is in the code; its address sits in a table of function
pointers at 0x002BD3C0 beside the other `yajima` screen functions, and
whether the game ever picks that entry has not been traced. The scan's
Japanese-text rule (half the characters kana or kanji, line breaks counted)
rejects it by one character. The rule is kept as it is: changing it would
change what every export lists, for a debug screen that needs files the
disc does not have.

**One string, two files.** `OV01.OVL` and `OV02.OVL` load at the same
address (0x00A80000), and the boot executable carries a third segment there
whose bytes equal `OV02.OVL`'s: the title overlay as it is at boot. The
strings of that segment are listed in `BOOT.exe.txt` and in
`OV02.OVL.exe.txt`, each marked "the same string is in ..."; an edit must
give both the same text, because which copy a given screen draws from has
not been read. A reference from the boot executable's own code into that
address range cannot say which overlay it means and is not counted.

**Run it last.** After an import the boot executable is no longer the retail
one, so `text-import`, `font-extract`, `recolor` with text and `lang-scan`
refuse the image (they read their tables from the executable and check its
sha256). So: `text-import` first, then `exe-import` on its output. For a
second round on an image that already went through `exe-import`, pass the
retail disc as `--retail`: the executables are taken from it, every edited
string of the tree is applied to them, and the result replaces the ones on
the image. That equals an import into a fresh retail copy (checked byte for
byte on Disc 1). Without `--retail` such an image is refused, because its
files are not the ones the tree was exported from. The tree may hold only
the files it changes (the manifest plus, say, `BOOT.exe.txt`): a file it
leaves out keeps the image's bytes, and with `--retail` it is reset to the
retail bytes too, so an overlay patched in an earlier round does not stay
behind and the twin check still compares against retail text (both cases
are in the self-test). Only the USA build is supported; Japan and the demo
are refused.

**In a new alphabet.** The executable tree goes through `lang-scan` (as a
second `--text`) and `lang-encode` like the text tree; give `lang-encode`
the disc as `--iso` so it runs these checks as a dry run and tells the
untouched strings from typed text ([ALPHABETS.md](ALPHABETS.md)).

Seen in the running game: the edited `Saving...` string (boot executable
0x68B678, in Amharic) on the LANGTEST disc in PCSX2, 2026-11-20
([ALPHABETS.md](ALPHABETS.md#worked-example-the-langtest-disc)). That its
print state sets `$han1` and `$prop1`, so it is drawn from the proportional
font like dialogue, is read from code.

## What is covered: `text-coverage`

```sh
python cli.py text-coverage --iso "Disc 1.iso" --iso "Disc 2.iso" [--verify] [--tsv coverage.tsv]
```

prints every place the game shows text and what the kit does with it,
counted from the discs with the same code the text commands run. On the USA
discs:

| Text | Where | Command | Status |
|---|---|---|---|
| dialogue and scene text | 465 `.sbl` banks, 9,699 strings | text-export / text-import | editable (banks grow within their loader's room) |
| menus, item, skill and location names, help, database, the Skill Tutorial | 3 `.txd` files, 903 strings | text-export / text-import | editable within the file's sectors; the 12 memory-card slot placeholders kept, the 199 tutorial commands unchanged |
| battle banners (enemy skill and action names, `bat.namePut`) | 1,363 calls, 416 names in 113 banks | text-export / text-import (in the banks) | editable; copies edited unalike get a note |
| battle names, descriptions and messages | `batdat.us`, 1,085 strings | text-export / text-import | editable within the file's sectors (section 17 in place) |
| quest, citizen and treasure text | 6 `iwase\*.txt` | text-export / text-import | editable within each field; on the USA build ASCII and the two-byte symbols |
| movie subtitles | 45 files (20 + 25) | text-export / text-import | editable |
| memory-card title | `carddata.bin` | text-export / text-import | in place |
| strings in the executables | 3,498 + 678 + 47 | exe-export / exe-import | in place, run last |
| Japanese developer leftovers | 127 Shift-JIS and 22 EUC-JP strings in 75 bank and menu files | text-export / text-import | editable, with a `#sjis:` / `#euc:` reading; probably debug |
| `samp.fontPut` digit strings | 28 calls, 3 strings | text-export / text-import (in the banks) | editable; probably debug |

**Languages.** The USA build reads its text banks (`cf\en\`, `model\map\en\`)
and subtitles (`movie\en\`) from a language folder picked by
`FUN_001948e8`, which indexes the string `enjpendefriteszhko` two letters at
a time by the byte `DAT_0063f87a`. That byte is also the Subtitles switch
of the options screen: `FUN_0023b210` shows `MainMenu` 0x16D (`OFF`) when it
is 0 and 0x16E (`ON`) otherwise, `FUN_001e7d08` draws subtitle lines only
when it is not 0, and the options code steps it through the table at
0x69D4F0, which holds 0 and 2. Both values read `en`. Both USA discs carry
only `en` folders (352 `cf\en` objects, 45 `movie\en`), so the text above
is all the text the USA build has. The language names `English` to
`Spanish` (`MainMenu` 0367-0371) are exported like any menu string; whether
the game draws them has not been seen. A second language would need a code
change to reach another folder.

Text that is drawn into pictures is listed apart, because it needs picture
editing: the UI sheets (`yosikawa\obj01.xtx`, `seg01`, `g2_00`-`g2_02`,
`ctrl01`, `ctrl_pic`, `battle01`, `bg01`, `font.xtx`, `stat.xtx`,
`talk01.xtx`, `botan.xtx`, `door_alpha.xtx`), the title menu words
(`titleobj.xtx`), the 22 "The Story So Far" pages and `end.jpg`,
`gameov1.jpg`, `mnt_set.jpg`, the staff-credit images in `credit.bin`, the
boot logos `title.bin`/`title2.bin`, the party-menu labels (`yosikawa\model\atk.txy`
`<< COMBATANT >>`, `yosikawa\model\rsv.txy` `<< RESERVE >>`, and
`yosikawa\atk.txy` `<< ATTACKER >>`, a file no executable names, so
whether it is ever shown is not known), the battle pop-ups `Boost Bonus` and
`Stock Bonus` (texture pages inside the effect file `simajiri\esp\default.esp`),
the glyph effect `simajiri\esd\pe820_004.esd` (eight kanji and the word
`CRY`, kept in Japanese by the USA build), the ANIMA logo on the title
backdrop `yajima\titlebg2.bin`, and the shop signs and digits in the
quest-board pictures (`iwase\img\citizen\c01`-`c50.xtx`,
`iwase\img\item\item00`-`item17.xtx`). `browse` writes all of them as
viewable files (the pages inside effect files and the backdrop under
`textures_png/_embedded`). `texture-import` writes edited txy pages,
embedded pages and UI sheets such as the quest-board pictures back
([Editing textures](#editing-textures-texture-import); checked on disc
copies, not yet seen in the running game).

Map and model textures were surveyed on 2026-11-20 by OCR of every decoded
texture: at least 109 `.txy` files carry words. OCR misses small, stylised
and Japanese text, so that is a lower bound. `text-coverage --tex-words`
lists the entries that were looked at by eye, and `--verify` checks on a
USA disc that each is where the list says:

| File | Entry | What is drawn |
|---|---|---|
| `model\map\E2_ELS04.txy` | `moji_us` | SEARCH IN PROGRESS / COMPARTMENT EXTENSION / STANDBY / Locked / Unlocked (the Japanese `moji` ends USABLE / NONUSABLE) |
| `model\map\E2_ELS04.txy` | `attention` | ATTENTION / THIS ELEVATOR CANNOT BE USED NOW |
| `model\map\E2_ELS07.txy`, `E2_MIL03`, `E2_MIL09` | `moji` | SEARCH IN PROGRESS / COMPARTMENT EXTENSION / STANDBY / USABLE / NONUSABLE |
| `model\map\E2_DYU03.txy` | `e_monita_02` | NOW AN ELEVATOR CANNOT BE USED |
| `model\map\E2_MID04.txy` | `E2_MID_04_089_us` | No Entry (Japanese `E2_MID_04_089`: NO THOROUGHFARE) |
| `model\map\E2_KUK11.txy` | `kanban02_01_us` | the FISHMAN CLASSIC shop sign |
| `model\map\E2_UZU01.txy` | `book_kou_us`, `book_hei_us`, `book_bou_us` and their `_01_us` | kanji numerals (the Japanese entries show 甲 乙 丙) |
| `model\map\E2_MID01.txy`, `E2_MID02` | `m01_con0000_us`, `m01_con0000_1_us` | no words (the Japanese entry carries a label) |
| `yosikawa\model\atk.txy`, `rsv.txy` | `dn_atk02`, `dn_rsv02` | `<< COMBATANT >>`, `<< RESERVE >>` (drawn so that it reads RESERUE); Japanese `dn_atk`, `dn_rsv` |
| `model\map\E2_MIL12.txy` | `warning` | WARNING |
| `model\map\E2_KUK11.txy` | `kanbanmoji` | Reel Dude's Fishing Lab. (shop sign) |
| `model\map\E2_KUK02.txy` | `yado_kanban01` | OUR TREASURE / INN.SHOP (sign) |
| `model\map\E2_KUK05.txy` | `kanban01` | IRONMAN (poster) |
| `model\map\E2_ELS05.txy` | `font0001` | MENU, with a line of small print |
| `model\map\CFEVE04.txy` | `koukoku_moji02` | SECOND MILTIA SPACE PORT |
| `model\map\CFEVE03.txy` | `text` | five lines of blocky English words, partly legible |
| `model\map\E2_MIL02.txy` | `poster07` | SECOND MILTIA AEROSPACE INDUSTRY |
| `model\map\E2_MOM11.txy` | `toilet_poster`, `kanban` | SURPRISE & DISCOVERY!! Dogs love GRACE ONE!! / GRACEFUL DOG FOOD; NEW TASTY FLAVOR / VIRGINIAN BRIGHT / refresh yourself!! |
| `model\map\E2_DYU03.txy` | `keijiban4` | DURANDAL, with columns of small print (notice board) |
| `model\map\E2_MIL04.txy` | `nagare_moji03` | INFORMATION SERVICE COUNTER |

A survey of entry names (names with `moji`, `kanban`, `poster`, `sign`,
`text`, `menu` and the like) lists 1,302 entries in 238 files, embedded
blocks included; a name is a hint, not proof that words are drawn.

The 12 `_us` entries in 5 map files are names the Japanese build has
without the suffix, most with another picture: the localizers redrew them.
`dn_atk02` and `dn_rsv02` are USA names too. Checked negatives, so nobody
has to look again: `iwase\img\door\doorimg.dat` (18 door pictures in a
pack of its own, u32 count 0x12 and u32 offsets, which `browse` does not
unpack), `yajima\diskchg.jpg`, the four JPEG in `yajima\im.bin`, the
`matumoto\*.txy` textures (logos only) and the eight `umn?.jpg` banners
carry no words.

The staff roll is drawn into the `EPSTAFF.sfd` movie (looked at frame by
frame, 2026-11-20; `movie\en\EPSTAFF.txt` is the movie's dialogue
subtitles, not the credits). OCR of a frame every 3 seconds of all 50
movies and every 20th frame of all 90 event clips (2026-11-20, a lower
bound) found drawn words in `s010100` (the opening cards "Namco Presents",
"A MONOLITH SOFTWARE INC. Production" and the title), `s010310` (interface
screens: COMMUNICATION, CALL SIGN TO..., SEND, Connection Viewer, Receiving
Device, Waveform:), `s010610` (ROOF GARDEN), `s020520` (a SYSTEM screen),
`s031000` (Unus Mundus Network), `s050400` (DINAH, VECTOR), `s080900`
(CATHERINE, CECILIA), `EP07` (TO BE CONTINUED) and the event clips
`moni_111` (monitor text), `noise_umn` and `noise_umn_l` (the UMN logo).
Re-encoding video is not part of the kit. `s020310` has a USA voice track
that differs from the Japanese one and no subtitle file in either region
(docs/DIALOGUE.md).

`--verify` exports every text and executable string into a temporary
folder and plans an import of the unedited trees onto each disc given: each
plan must change nothing (it does on both USA discs). On the USA executable
it also checks that the seven prompts read with control codes (the four save
prompts, the skip line, `Start game?` and the memory-card prompt) are listed
with their exact ends, that the texture entries above are where the list
says, and it tries the battle database's growth writer on the disc's own
file (unedited it must give the file back; one string per section made a
byte longer must fit and come back from the next export).

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

  **`text-import` enforces this rule** (`moviesub.py`): it packs the cues the
  way `FUN_001e7a08` does and refuses an edit whose packed cues pass the next
  64-byte boundary of the retail file's. The retail room is small: 2 bytes
  for `s010100.txt` (packed 4,926 bytes; the "about 12" and "4,532" above
  were estimates), 2 to 63 bytes across the 45 USA files.

  **The explanation above does not match the code, and the true limit is not
  settled (2026-11-10, code reading only).** `FUN_001e7a08` (disassembly at
  0x1e7a08) does not return the packed size. After the terminator record it
  steps 4 bytes and scans to the next NUL (0x1e7abc-0x1e7ad8), through the
  not-yet-overwritten rest of the file, so its result ends at the first NUL
  after the end of the *file*: on the retail disc that is in the filler of
  the sector's slack, at the next multiple of 32 above the file size (6,752
  for the 6,723-byte `s010100.txt`). The work area therefore starts at
  `align64(6,752) = 6,784`, and it is the file's size, comments included,
  that moves it:

  | Case above | File size | Work area starts at | Result seen |
  |---|---|---|---|
  | retail | 6,723 | 6,784 | plays |
  | relocated, same size | 6,723 | 6,784 | plays |
  | grown with comments | 8,794 | 8,832 (2,048 further) | plays |
  | one line 52 characters longer | 6,775 | 6,784 (unmoved) | broken |

  So a work area moved by 2,048 bytes played, and the broken case did not
  move it at all. The breakage has another cause, not found yet; the length
  of a single line is the obvious suspect (each line is drawn with an
  x-offset byte `~(width / 2)`, which wraps for a line wider than 511 pixels
  and is a NUL, cutting the cue list short, at 510-511). Until someone tries
  it in the game, the 64-byte rule stays: it is the stricter of the two for
  displayed text, and it refuses the case that broke. What it does not
  promise is that every edit it lets through is safe: a short file with 60
  bytes of room would be allowed a much longer line than the one tried.

  The packing is done in place and unchecked. `moviesub.py` also refuses
  files the game's own parser would mangle: a first cue that is not at frame
  0 with too little text above it, runs of very short cue lines, a time not
  followed by a tab or blank, a list not closed by an `end` line, a backslash
  not followed by `n` (the packer makes every backslash a line break and
  drops the byte after it, 0x1e79b4-0x1e79cc; all 879 retail backslashes are
  `\n`), a cue at frame 65535 or later (16-bit field, 0xFFFF ends the list).

  **Bounds from the retail files (added 2026-11-10, after review).** Since
  the 64-byte rule is not the mechanism, `text-import` also keeps an edit
  inside what the retail game is known to display, measured on the disc being
  patched:

  | Disc | longest line (bytes) | widest line (pixels, the game's width function) | most lines in a cue |
  |---|---|---|---|
  | USA Disc 1 (20 files) | 51 | 454 | 2 |
  | USA Disc 2 (25 files) | 49 | 445 | 2 |
  | Japan Disc 1 (20 files) | 75 | 451 | 1 |
  | Japan Disc 2 (25 files) | 65 | 425 | 2 |
  | demo (3 files) | 50 | 451 | 1 |

  The widest line is close to 450 pixels on every disc, in both languages,
  though the byte lengths differ by half: the authors seem to have written to
  a width. No line
  may be longer or wider than these, no cue may have more lines, and the cue
  list itself (how many cues, at which frames) may not change. **These are
  not proven limits of the game.** They say only that text this long has
  been seen to work. They do fit the one failure on record: the line that
  broke playback was 52 characters longer than a retail line, so several
  hundred pixels wider than the 454 the widest retail line reaches, and
  wider than the 640-pixel screen. The width function is a port of
  `FUN_00195938` (`font.Measurer`), started in the state the packer sets
  (`$prop1;$han1;$xx1,8;`); the Japanese function's `$`-code handling was
  not read and is assumed equal.
* **KOS-MOS's hair** is patched in both test discs but she is not on screen in
  the first minutes, so it is not yet checked in the game.
