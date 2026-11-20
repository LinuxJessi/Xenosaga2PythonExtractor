# Xenosaga Episode II: disc formats

Byte layouts for everything the kit reads, with how each was confirmed.
"All N files" means the claim was checked on every object of that kind on
the USA discs (Disc 1 SLUS_208.92, Disc 2 SLUS_211.33), 2026-11-05.

## The discs

| | Disc 1 | Disc 2 |
|---|---|---|
| boot ELF | `SLUS_208.92` | `SLUS_211.33`, byte-identical to Disc 1's |
| layer | single (DVD-5), 2,286,192 sectors | single, 2,291,584 sectors |
| bigfiles | `XENOSAGA.00-.02` (chain 0), `.10-.14` (chain 1) | `.00-.02` (chain 0, byte-identical), `.20-.24` (chain 2) |

`OV01.OVL` (the field and script runtime), `OV02.OVL` (memory-card
messages, also linked inside the boot ELF as section `ov02`) and the IOP
modules are identical on both discs.

## The TOC (`XENOSAGA.00`, `.10`, `.20`)

Episode I's binary table of contents with front-coded names. The first byte
is the TOC's own size in sectors; entry sectors count from the TOC file
itself, the data continuing into the sibling bigfiles.

    toc    = [u8 size in sectors] item* [0x00]* filler
    file   = [n]      [code] [literal: n-2]  [u24le sector] [u32le size]
    cfile  = [n|0x40] [code] [literal]       [u24le sector] [u32le csize] [u24le usize]   (ARX)
    dir    = [0x80|n] [u8 pops] [code] [literal: n-3]
    pop    = [0x00]
    filler = "MONOLITHSOFT Xenosaga Episode.2\0", phase-locked to offset % 32

Front coding: `code == 0` means the literal is the whole name plus NUL.
Otherwise `H = code >> 4`, `L = code & 15 = len(literal)`, `p = len(prev) - H - 1`,
`name = prev[:p] + literal + prev[p+L:]`, cut at the first NUL. `prev` is the
previous file name (the first file after a directory is written whole);
directories code against the previous directory at the same depth. A literal
carries a NUL exactly when the name's length changes.

Example, the start of chain 1: `020300_marglis.xap`, then `[08][96]"obj200"`
(H 9, L 6: keep `020300_m`, write `obj200`, keep `.xap`) =
`020300_mobj200.xap`, then `[03][41]"1"` = `020300_mobj201.xap`.

Proof: 9,030 entries (8,605 + 233 + 192); extents tile each chain with no
gap or overlap; chains 1 and 2 end on their last byte; the kit's encoder
rebuilds all three TOCs byte-identically from the parsed items
(`toc.py --iso`). **Episode III's catalogs use the same grammar**: `X3.00`,
`X3.10`, `X3.20` parse to 15,225 entries that agree in name and size with
Lybac's 2006 tables and rebuild byte-identically
(`docs/research/xs3_toc_check.py`); their filler also says "Episode.2".

Three entries are ARX-compressed (`yajima\titleobj.xtx`, `title.bin`,
`title2.bin`): Episode I's coder, recompressed byte-identically by `arx.py`.

## Textures: `.txy` (941 + 2 JPEGs + 4 placeholders)

The GS upload for one model or map plus a named entry table. The same block
Episode III embeds in its packages, standalone here.

    +0x00 "txy\0"   +0x04 u32 bank count   +0x08 u32 size   +0x10 u32 bank tables[count]

Bank count: 0 = placeholder (4 files), 1 = characters and props (723),
2-3 = field maps (207), 4/8/23 = skyboxes and animated pages (7). A bank
table is record 0 (`canvas_width` at word 2, strip count word 5, entry count
word 6), the strip records `{data_off, gs_block, w, h, 0, qwords, ?, 4}`, and
the entry table (0x60 each, ending where the first strip's pixels start):

    +0x00 gs addr  +0x04 fmt (0x13 PSMT8, 0x14 PSMT4, 0 CT32)  +0x08 w  +0x0C h
    +0x10 x  +0x14 y  +0x18 cbp  +0x1C flag  +0x20 palx  +0x24 paly
    +0x28 duplicate flag  +0x2C 1024/512  +0x30 BANK INDEX  +0x40 name[32]

Every bank of a file carries the same entry table; only the strips differ.
**`+0x30` names the bank an entry's pixels are in.** The 23-bank skybox
`model\map\E2_EVS02.txy` holds 23 cube faces at the same rect, one per bank;
on the two-bank field map `yamamoto\map\E2_MIL15.txy` 9 of 87 entries live in
bank 1. Decoding each entry against GS memory after banks 0..b are uploaded
fixes 71 of 78 bank-0 entries on that map that came out as noise when all
banks were simply overlaid. Episode III's map packages carry the same word
(70 multi-bank maps in that kit's dump, up to 8 banks), which is a lead for
its residual noisy map entries. Three entries on the disc have bank
0xFFFFFFFF; the kit reads them from the last bank.

Swizzles and CLUT order are Episode III's (`chrtex.py`), PSMT4 included.
Two files have more than 512 entries (599 and 629).

**Embedded pages.** 2,167 more txy blocks sit inside 697 other objects:
`simajiri\esd\*.esd` (1,821 blocks in 592 files), `simajiri\esp\*.esp`
(334 in 104) and `yajima\titlebg2.bin` (12). A block is taken where `txy\0`
is followed by a bank count of 1-32, a total size that fits the container,
0x90 at +0x10 and a table that parses; a `txy\0` preceded by `.` is a texture
*name* ("ef_ring.txy"), 2,348 of those, plus 193 hits with another +0x10.
The 3,547 entries give 1,333 images not written by the standalone pass
(721 duplicate a standalone page, 1,493 an earlier embedded one), written to
`browse/textures_png/_embedded/` with `embedded_textures.csv`.

## UI sheets: `.xtx` (85)

Episode I's XTX unchanged: header `{magic, total, count, 0x10}`, 20-byte
sub-image records `{u16 w, u16 bufw, u16 h, u16 ?, u32 gs_off, u32 size
(qwords), u32 addr}`, a CT32 canvas 256 (`bufw` 4) or 512 (`bufw` 8) wide.
**Parked-corner palette (69 sheets, solved 2026-11-05):** the 50 citizen
portraits, 18 item cards (`iwase\img\*`) and `omiya\botan.xtx` are one
canvas image whose top-right 16x16 tile (x = w-16, y = 0) is the 256-colour
CSM1 palette, with nothing below it. `xtx._parked_corner` recognises exactly
these 69 and colours the art with that tile alone (palette source `corner`).
Episode I's ranking passes picked a wrong tile for 18 of them (grey or
garbled portraits and cards), although the corner tile renders every one
correctly.

**Frame masks (3 sheets):** `ctz_alpha.xtx`, `item_alpha.xtx` and
`door_alpha.xtx` use the same layout with a two-colour corner palette (entry 0
clear, entry 1 opaque white) and art in indices 0/1 only: a rounded rectangle
the size of the pictures beside it (112x127 portraits, 128x143 item cards).
The quest board draws every portrait and card through its directory's mask;
`browse` does the same (`xtx.apply_mask`), which rounds the corners and cuts
the dark margin right of the art. No door pictures ship on the USA disc.

`yajima\titleobj.xtx` has no palette at all and is true colour (the title
menu words: NEW GAME, CONTINUE, INSTALL, UNINSTALL, EXIT, THEATER).

Open: the 11 multi-palette menu sheets (`yosikawa\battle01`, `bg01`,
`ctrl01`, `font`, `g2_00`, `g2_01`, `g2_02`, `obj01`, `seg01`,
`omiya\talk01`, `yamamoto\stat\stat`) still go through Episode I's ranking and
come out grey or garbled. Episode I's 28-byte OVL descriptor tables do not exist in
this game (checked: OV01 names only `stat.xtx`, and its descriptor-shaped
records are noise); the menu code names its sheets through a `char[24]` array
at 0x3eae40, and the palette each sprite uses must be read from that code.

Some `.txy` map textures look odd over a grey background but are right:
`mil13_grass_col` is one flat colour with the grass drawn entirely in alpha.

## Fonts: `font0.us`, `font1.us` (Japan and the demo: `font0.tex`, `font1.tex`)

**The file** has Episode I's container: two 0x20-byte headers, each followed
by a 0x3C000-byte PSMCT32 upload that the engine reads as a 640x768 PSMT4
texture (two 4-level sheets per block, so four sheets of 1,024 20x24 cells),
then (left, right) metric byte pairs from 0x78040: 384 on the USA discs
(492,352 bytes), 256 on the Japanese discs and the demo (492,096 bytes; the
demo's two files are byte-identical to Japan's). `font.py` round-trips all of
them; `font-extract` then `font-rebuild` of the unedited sheets and metrics
gives back the same bytes (checked on USA Disc 1 and 2, Japan Disc 1 and 2
and the demo).

**The addressing is not Episode I's.** (An earlier version of this page and
of `font.py` said "the same layout"; that was true of the container only.)
Read from the executables, 2026-11-10; USA addresses, Japan in brackets:

| Step | Rule | Where |
|---|---|---|
| two-byte code to cell | per-row rules for A1-A8 and AD; kanji B0-CF at `lead*94 + trail - 0x3D41` (B0A1 = 1024, A4A2 = 193); D0 and up through a list of extra kanji (cell 3989 + index) | `FUN_001979e0`, `FUN_00197988` [0x197880, 0x197828] |
| the extra-kanji list | USA 0x63E870, empty; Japan 0x63A2A0, 87 codes; demo 0x639060, the same 87 | data |
| ASCII, with `$han1;` | a u16 code per byte from a table (USA 0x63E770 + 2 * byte), then the row rules; `A` = A3C1 = cell 138 | `FUN_00198b90` [0x198a58] |
| ASCII, without `$han` | an 8x8 system font, not a cell of this file | `FUN_00197bb8` 0x198768 |
| USA bytes 0x80-0xFF | one Latin-1 character each, cell = byte + 0x80 (`É` 0xC9 = cell 329), except the two-byte codes `FUN_00194dd0` lets through (lead A4, A6, AD; a few of A1 and A2). The USA build does not print kanji this way | `FUN_00197bb8` 0x1986c0 |
| Japan bytes 0xA0-0xFF | always the lead of a two-byte code; lead A9 has no rule (fallback cell 0x7F) | [0x198508] |
| metric pair | index = the cell number | `FUN_00198b38` [0x1989a8] |
| proportional (`$prop1;`) | USA cells below 384, Japan below 192: image from sheet 0, cell + 576; advance `right - left + 3` (USA) or `+ 4` (Japan). A pair with left 0 takes the text's `$xx1` width | 0x198840, 0x198b68 [0x1986b0, 0x1989d8] |
| everything else | the cell itself, advance 19 | |

Japan's pairs 192-255 are never read by this code. The ink of cell + 576
equals its pair in 379 of 384 USA pairs and 255 of 256 Japanese ones (the
others are blank cells). All 1,951 two-byte codes of the Japanese text
(322,672 uses in scripts, the battle database and the menus) reach an inked
cell with no fallback, the space aside.

**USA: sheets 2 and 3 look unreachable.** The Japanese draw function picks
the texture block by bit 1 of `cell >> 10` (0x197204) and the Japanese setup
uploads two blocks (0x198bc0, 0x198c1c). The USA draw function
(`FUN_00197200`, 0x197380-0x1973fc) uses one texture address and bit 0 only,
and the USA setup (`FUN_00198bc0`) uploads one block. From the code, a USA
cell from 2048 up is drawn from where cell - 2048 is. Not seen in the
running game.

**Which face draws what** (read from the USA code, 2026-11-20). The engine
has one font buffer (0x8DD280). `FUN_00198ff0` loads `font1.us` at init, so
font1 draws field dialogue, menus and battle text. `font0.us` is loaded in
its place while a movie plays (`FUN_001e7d08`) and between cfe.init
(`FUN_0024c308`) and cfe.destroy (`FUN_0024e738`), the real-time cutscenes.
Measured: sheet 0 (cells 0-1023, every cell the USA build can draw except
the kanji sheet) and all 384 metric pairs are byte-identical in the two USA
files. (The Episode I kit's "font0 is the dialogue face" was carried into
`font.py` and is wrong here.) New glyphs therefore go into both files
([ALPHABETS.md](ALPHABETS.md)).

`font-extract` also writes `fontN_bytes.csv`: for every byte 0x20-0xFF, the
code, cell, drawn cell, metric slot, pair and advance, for this disc's build.
`font.py` does not measure whole strings (the control bytes and `$`-codes
are not modelled).

## Dialogue: `.sbl` (465)

    +0x00 "SBL "  +0x04 1  +0x08 count  +0x0C 1  +0x10 0  +0x14 0x40  +0x18 0x40+4*count
    +0x40 u32 offsets[count] (into the string area), then NUL-terminated strings,
    zero-padded to 4 bytes

All 465 rebuild byte-identically; 9,699 strings. Text is ASCII plus EUC-JP
symbols (`<○>` is a controller button), with codes `[Speaker]`, `\n`,
`$zoom13;`, `$colc0c0c0;`. 127 strings in `cf\en` are Shift-JIS developer
test lines ("11テストメッセージ\n表示中").

`.txd` (`yosikawa\*.txd`, menus): u32 offsets (count = first offset / 4)
then NUL-terminated strings; the game font re-uses some EUC-JP codes for
Latin letters (`D` + 0xA4A1 + `mmerung` is "Dämmerung").

Movie subtitles (`movie\en\*.txt`) are EUC-JP with timing lines; the
`#scn` scene lists (`cf\cf*.txt`) and quest text (`iwase\*.txt`) are
Shift-JIS.

## Scripts: `.sb` (465: `cf\sc*.sb` and `model\map\*.bin`)

A stack machine in `OV01.OVL` (Episode I ran Java here). The full instruction
set, literal kinds (ops 03/04/06/07) and argument types are in
[SCRIPTS.md](SCRIPTS.md). Found 2026-11-05:

* opcode handler table at OV01 0xab5fc0 (46 entries), lengths at 0xab5f90;
* natives registered per class with `FUN_00a9cdb0(class_name, table)`, the
  tables being `{char *name, handler}` pairs in the boot ELF's data, e.g.
  "runtime" at 0x3e6e40 (setPlayer, Item, TimeAttack, GetAnalogR,
  progEffect, changeRobo, message, jumpCF, mapChange, Fade, setFlag,
  getFlag, setFog, setItai, setLight, System, mpeg2, adx, ...), "talk"
  at 0x3ec868 (check, reset, smsg, msgDtl, winClose, resume, enableFlags,
  isDispMsg, wait, menuAddItem, ...), the camera class at 0x3e70b8;
* `FUN_00a9d148(class, name)` resolves an import to `class << 16 | index`
  at load; `FUN_00a9e5b8` (opcode 0x15) calls it.

File layout:

    +0x00 "SB  "  +0x04 1  +0x08 0  +0x0C entry count  +0x10 import count
    +0x14 code  +0x18 globals  +0x1C text pool (USA: = entry table; Japan/demo:
    first inline string, see SCRIPTS.md)  +0x20 entry table  +0x24 import table
    +0x28 source name ("nakahara\sc1010": author folder and script)
    entries: {u32 name offset, u32 code word}     e.g. locater, kowaremono, goteinei, mapjump
    imports: {u16 function offset, u16 class offset} into the string pool after the table

Code is 16-bit words; an operand is the 32-bit value in the next two words;
jump targets and entry points count words from the start of the code.

| op | len | name | | op | len | name |
|---|---|---|---|---|---|---|
| 00 | 1 | nop | | 17 | 1 | waitfor2 |
| 01 | 3 | op (1 add 2 sub 3 mul 4 div 5 mod 6 or 7 and 8 shr 9 shl 10 neg 11 eq 12 gt 13 lt 14 ge 15 le 16 ne 17 land 18 lor) | | 18 | 1 | dup |
| 02 | 3 | line (no effect) | | 19 | 1 | drop |
| 03 04 06 07 | 3 | push immediate | | 1A 1B | 1 | load global/local [pop] |
| 05 | 3 | mark (no effect) | | 1C | 1 | yield |
| 08 09 | 3 | load global/local | | 1D | 1 | end |
| 0A 0B | 1 | store global/local | | 1E | 1 | sleep N frames |
| 0C 0D | 1 | store, keep value | | 1F 20 | 1 | send / receive |
| 0E | 3 | jmp | | 21-24 | 1 | inc/dec global/local |
| 0F 10 | 3 | jz / jnz | | 25 | 4n+5 | switch: n, default, n x {key, target} |
| 11 | 1 | enter | | 26 27 | 1 | push 0 |
| 12 | 3 | call | | 28-2D | 1 | push 1..6 |
| 13 14 | 1 | ret / ret value | | | | |
| 15 | 3 | native (import index) | | | | |
| 16 | 1 | waitfor | | | | |

Native arguments are pushed as value then type, then the count:
`push 4; push1; push 0x200004; push3; push 2; native talk.msgDtl` passes
int 4 and text 4 ("[Canaan]What is this thing?"). Type 3 values are
`0x20 << 16 | index into the .sbl`.

Proof: all 465 scripts disassemble to the exact end of their code with every
jump, switch target and entry point on an instruction and every native in
the import table; 2,609,696 instructions, 227 distinct natives (the most
used are the `cfe.stKy*` cutscene keyframe setters and camera calls).

## Sound: KORG `.vsf` / `.prj` / `.mid`

The driver is `IOP\KORGIVAG.IRX` ("KORG IOP Sound Driver ... (C)2001-2004
KORG Inc."); it ships with its symbol table, and fine tune (1/64 semitone)
and the per-song effect NRPNs read from its code are in [MUSIC.md](MUSIC.md). Field music is a Standard MIDI File (type 0, 480 PPQN, real
tempo) played through a project's programs; sound effects use the same
banks.

`.vsf` (1,074): `"VSFp"`, u32 0x10, count, sample bytes, two stale words,
then per sample a 0x28-byte record `{u32 ?, u32 ?, u16 rate, u8 root, u8
fine, u32 ?, u32 ?, u32 size, name[16]}` and `size` bytes of SPU ADPCM. All
banks walk to the exact end of file: 6,953 records, 3,220 "-- no sample --"
placeholders (stale memory, not audio), 3,733 real samples. Every real
sample has an end flag: 3,425 one-shots (flag 1, then the usual silent pad
frame) and 308 loops (flag 3 back to the frame flagged 4). Of 4.6 M frames,
6,383 have impossible headers: 6,374 in placeholders and 9 in tail padding
after an end flag (one holds UTF-16 `\DosDevices\R:\...` from the authoring
PC). Decode health: median peak 31,028; 55 samples clip at 1-16 values;
none runs away.

`.prj` (1,074): `PRJp` (name), `KEYp` (count x 0x30), `PRGp` (count x
0x180). A key record is two 16-byte layers and the sample's name:

    +0 u16 sample  +2 u16 SPU ADSR1  +4 u16 SPU ADSR2  +6 volume  +7 pan (64 centre)
    +8 s8 transpose  +9 s8 fine (1/64 semitone)  +10 0xFF = pitch follows the key, 0 = fixed
    +11 choke group  +12 hold (non-zero: note-off ignored, the one-shot plays out)
    +13 bit 0 = SPU2 core (not an on/off flag: a layer sounds when it names a sample)

The sample index matches the same-named `.vsf` sample in 6,921 of 6,962
records (the rest are name typos). A program is name[16], parameters, and
u16 key_map[128] at +0x40 (MIDI key -> key record, 0xFFFF silent).
"Default Program" maps keys one-to-one (groove loops, fixed pitch);
instruments map every key to one record, often with two layers panned
apart. Program N is the song's program change N. The default envelope
0x808F/0xDFE6 is instant attack, full sustain, exponential release.

`prj.py` writes a SoundFont 2 per song (each program also mirrored into bank
128, so a General-MIDI player's channel 10 plays it). The kit's WAV renders
come from `spu2synth.py`, which models the KORG driver and the SPU2, reverb
included: [MUSIC.md](MUSIC.md).

## Movies, voices, clips

Movie subtitles `movie\en\<movie>.txt` (45, EUC-JP, `mm:ss:ff` cues, `;`
comments): the engine compacts the cues into the head of the Sofdec work
block and starts the decoder after them (64-byte aligned), so displayed text
can grow only to the next 64-byte boundary; see MODDING.md.

`.sfd` (50): CRI Sofdec MPEG-1 512x448 (SAR 200:219) with ADX stereo
(0xC0) and AC3 (0xC1): 5.1 in 48 movies, stereo in `s040110` and `s06over`.
`browse --movie-audio ac3|both` keeps the AC3 track bit-exact (and writes it
raw as `<movie>.ac3`). `.ipu` (90): Sony IPU clips, 256x224/256x256,
25 fps. `.adx` (657): CRI ADX. ffmpeg reads all three.

## Models: `.pxy`, `.xhr`

Decoded: [MODELS.md](MODELS.md) has the byte layouts, how each field was
proven (the address in the executable or VU1 program, or the numeric test over
all 944 files) and what is still unknown. In short: a `.pxy` holds material
and node records, a draw list, and a raw VIF1 stream of `STCYCL` + `UNPACK` +
`MSCNT` batches of up to 30 vertices (positions with S in w, normals with T in
w and a strip-restart bit, colour bytes, weights, joint-palette bytes); a
material's texture is named by the TEX0 register inside its batch, not by a
header word; a `.xhr` is six runs of joint transforms with no names.

Earlier notes here were wrong on four points, corrected in MODELS.md: the
header word +0x28 is the record's own index, not a texture index; the "joint
names" in `.xhr` are uninitialised padding; V4-8 unpacks are colours and
palette bytes, while weights are V2-32 or V4-32; and UVs ride in the w of
positions and normals, not in a V2-32 unpack.

`.xap` 1.2 and `.fap` 1.2 (motion and face motion) are offset tables, not
decoded.

## Small side files

Boot screens, title logos, the save icon, `batdat.us` battle strings and the
structure of `.id`, `.epf`, `.ene`, `.dev`, `.fap`, `.jrd`:
[research/small-files.md](research/small-files.md).
