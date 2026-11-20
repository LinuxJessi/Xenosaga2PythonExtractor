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
| quest, citizen and treasure text | `iwase\*.txt` (Shift-JIS) | `X.sjis.txt`, the file itself | only the text between double quotes; the file may not need more sectors than the original |
| battle names and descriptions | `yamamoto\batdat.us` (Japan `batdat.bin`) | `batdat.us.txt`, `### S<section> @<offset> max N bytes` blocks | each string in place: same length or shorter |

An unedited export imports as "no text changed" on every disc, and each
object rebuilt from its unedited text is the original byte for byte: 3 menu
files, 20 + 25 subtitle files, 6 quest files and the battle database on the
USA discs; the same counts on the Japanese discs; 2, 3, 6 and 1 on the demo.
(For the battle database "rebuilt" means every string re-encoded and put back
in place; there is no writer that moves its strings.)

Why these limits, and how sure they are:

* **Menus** are read into static buffers (`FUN_00228570`: `MainMenu.txd` to
  0x6A5BC0 and `Tutorial.txd` to 0x6A9BC0, 16,384 bytes apart;
  `Location.txd` through a pointer in `FUN_0023ad18`). The read is whole
  sectors of the TOC size, so the rule is **no more sectors than the
  original**, whatever the slot is (on the retail discs every slot equals the
  sector-rounded size anyway: 495 of 495 USA text objects, checked on all
  five discs). They are never relocated. Read from code, not seen in game.
* **Quest text**: `FUN_00232100` allocates by the file's TOC size
  (`FUN_002320b0` asks `FUN_0019bc68` for the size and passes it to the
  allocator `FUN_0020b738`), so a larger file might load; that has not been
  tried, and the same "no more sectors" rule is applied. Only the text
  inside `"..."` may change: the rest of the file (record numbers, braces,
  field names, the ruler lines of the Japanese files) is compared with the
  original. The files are Shift-JIS; on a Japanese disc a typed character is
  checked through its JIS code (assuming the game converts one to the other),
  on a USA disc typed non-ASCII is refused (how the USA build reads high
  bytes in these files has not been read).
* **Battle database**: loaded to a static buffer (OV01,
  `FUN_00a83dc0(path, 0x61d040)`) of unknown size, with 220 bytes left in its
  last sector on the USA disc. Its strings are found through 16-bit fields of
  records whose layout is not decoded, so a string is replaced where it is
  and may not get longer (`smallfiles.batdat_replace`). An edit that would
  change which strings the next export finds is refused (the kit recognises
  a text section by the first byte of its first string). Fields that merely
  equal an offset are left alone by design; that they are numbers and not
  pointers into the middle of a string was tested: of the 211 such values on
  the USA disc, 209 are the same number at the same place in the Japanese
  file, whose strings have other lengths (of 1,095 real string offsets, 31).
* **Inline scripts (Japan)**: 470 variable slots in 119 scripts hold a value
  that falls inside a pool string rather than at its start. They look like
  numbers (281 are multiples of ten; 7893, 19456, 2800, 400, 2000 lead the
  list), but a pointer into the middle of a string cannot be ruled out from
  the files, and an in-place edit would show through it.
* **Subtitles**: see "Subtitle limit" below, and the notes under it.

The files of the two whole-file kinds must come back exactly as you want
them on the disc: line ends are kept as they are (LF in the subtitle files,
CRLF in the quest files), and `⟦XX⟧` is a raw byte.

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
is a real 0x0A byte, written `⟦0A⟧`; the title overlay's prompts also carry
the control bytes `⟦19⟧⟦03⟧` and `⟦0C⟧` of the screen that draws them. Keep
those.

**What is listed.** A string the code points at: a 32-bit word in the data
equal to its address, a `lui` followed by an `addiu`, `ori` or byte load with
the same register (in the same block, or after a branch whose other path
shares the `lui`), or an `addiu` from `$gp` (gp read from the executable's
`.reginfo`, 0x006A2DF0; `Next Lv` and `Zone` sit in `.sdata`). It is at least
two bytes of printable ASCII, tab, line feed or bytes from 0x80 up, with an
ASCII letter or digit in it or reading as Japanese (developer strings, marked
in a comment). Control bytes are allowed in a run with at least three ASCII
letters. Bytes inside the font tables the kit reads from the executable are
never listed. On the USA Disc 1 that is 3,441 strings in the boot ELF, 676
in OV01 and 33 in OV02, most of them developer messages, file names and
format strings: leave what you do not recognise alone. A few entries are data
that happens to look like text; the export marks the ones that are visibly a
pointer.

Compared with the Ghidra listing of the boot ELF (1,106 referenced strings),
all but 24 are found; those 24 are error messages (`cvFs... vtbl error`,
`invalid motion_type`, `can not found parent scale`) whose `lui` is on another
path of the code with the register reused in between, which this scan does
not follow. Every string of the Ghidra listings of OV01 and OV02 is found.

**The slot.** A string may use the bytes from its start up to the next
non-zero byte, the next address the code forms or points at, or the start of
a font table, whichever comes first, less one byte for the terminator. That
is the `max N` of its header. `Saving...` has 7 zero bytes after it, so its
slot is 15 bytes of text. The zero bytes after a string are taken to be
padding; a variable that the code reached only through an address below it
would not be seen.

**In place only.** `exe-import` writes each changed string over its own
bytes and fills the rest of the slot with zeros. Nothing moves, no file
changes size, and nothing but the edited strings changes: on Disc 1, two
same-length edits changed 16 bytes of the 4.68 GB image, all inside
`SLUS_208.92`, and its directory record stayed as it was. A string longer
than its slot, a NUL, a changed header or a moved, missing or extra block
is refused, naming the address and the slot, and then nothing is written.
Typed characters are checked against the fonts of the disc being patched, as
in `text-import`; escapes are not checked.

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

Seen in the running game: the edited `Saving...` string (boot executable
0x68B678, in Amharic) on the LANGTEST disc in PCSX2, 2026-11-20
([ALPHABETS.md](ALPHABETS.md#worked-example-the-langtest-disc)). That its
print state sets `$han1` and `$prop1`, so it is drawn from the proportional
font like dialogue, is read from code.

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
