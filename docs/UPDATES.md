# Updates

## 2026-11-20 (round 4): the user's walk-through of text coverage

* **Reading hints stay on one line.** Seven `exe-export` hints for Japanese
  developer strings in SLUS_208.92 / SLUS_211.33 (the ladder label at
  0x006899D0, `メモカテスト` at 0x00688B58 and five more) carried the
  string's raw control bytes: a carriage return or a vertical tab. An editor,
  or a script that reads the file in text mode, ends the comment line there,
  and the rest of the hint becomes a line of its own, which `exe-import`
  reads as the string's new text and refuses. The hints of both exports now
  write every control character as a `⟦XX⟧` escape. Found by exporting both
  USA discs, editing one string of every kind, importing onto copies and
  exporting again; the walk-through itself is not part of the kit.

## 2026-11-20 (round 3): text coverage, the last gaps

* **Memory-card slot number (USA).** Twelve `MainMenu.txd` prompts carry the
  placeholder `£°` (A3 B0) that `FUN_00238038` overwrites with the slot's
  digit. The export notes it above each block; `text-import` refuses an edit
  that drops it or hides it from the game's walk (an accented letter right
  before it).
* **Skill Tutorial commands.** 199 of the 255 `Tutorial.txd` entries are
  commands `FUN_00215f10` runs (`%p`, `%tNN`, `*`, ...) or entries it skips.
  They are marked, and `text-import` refuses a change to them or a page of
  text turned into one.
* **USA quest characters.** The quest reader converts every pair from
  Shift-JIS to EUC-JP (`FUN_0022ec18` -> `FUN_0023ebb8`, ported). The three
  files the USA loaders read now show what the game draws (`―`, `Ω`; other
  pairs as escapes, so the retail `Caf⟦82⟧⟦A0⟧`), and typed USA symbols are
  accepted through the inverse conversion instead of every non-ASCII
  character being refused. A Latin-1 letter is still refused, with the
  reason. Not yet looked at in the running game.
* **Battle banners.** The 416 names (1,363 calls) scripts pass to
  `bat.namePut` are marked in the bank files and listed by `dialogue` as
  `(banner)`; `text-import` notes copies edited unalike and banners wider
  than any retail one, and refuses one whose width walk would run past its
  end. `text-coverage` counts them, and `samp.fontPut`'s digit strings.
* **Japanese leftovers** get a `#sjis:` or `#euc:` reading line in bank and
  menu exports (the 20 EUC test lines and `MainMenu` 0318/0399 had none),
  and Latin-1 lines no longer get a false Shift-JIS hint.
* **Executable**: the field-debug label `ハシゴ` (0x006899D0) is listed by
  `exe-export` (a control code and three kana now count as text in the print
  grammar; on the retail executables that adds this run only).
* **Docs and app**: commands named on this branch only (no `lang-scan`), the
  app self-test checks that every command a card names exists; twelve more
  texture entries with words in `TEX_WORDS`; the language folder and the
  Subtitles switch share one byte (MODDING.md, "Languages").

## 2026-11-20 (later still): text coverage, round 2

* **Battle strings grow (USA).** The string fields of the battle database
  were read from OV01's getters (`FUN_00a9b338`, `FUN_00a9b750` ...
  `FUN_00a9bba8`): sections 0, 1, 6-14 and 18. A longer string is written
  where it is, the rest of its section moves down, exactly those fields are
  moved, the later sections and the header follow, and the result is read
  back field by field (`smallfiles.batdat_rebuild`). The file keeps to its
  sectors (220 spare bytes on the USA disc). Section 17 and the Japanese
  file stay in place. An unedited tree still imports as no change.
* **Subtitles for a movie without any** (`s020310` and four others):
  `text-import` refuses such a file and says why (the movie player keeps
  the cues in the video decoder's block, which has no room when there were
  none). `s020310`'s re-dubbed USA voice track is noted in DIALOGUE.md.
* **`text-coverage`**: rows for the party-menu labels, the battle pop-ups
  and glyph effect inside effect files, the ANIMA logo, the quest-board
  pictures, and checked negatives (`no text`: door pictures, disc-change
  picture, boot images, `matumoto` textures, UMN banners); the map texture
  survey (OCR, at least 109 files with words, 12 `_us` entries) and the
  movies with drawn words. `--tex-words` lists the texture entries with
  words; `--verify` also checks that they are on the disc and tries the
  battle growth writer on the disc's own file.
* **App**: cards for `dialogue`, `font-extract`, `font-rebuild`, `natives`
  and `recolor`; the `text-import` card lists everything it writes. The app
  self-test checks that every command has its card text.
* **Executable strings**: why none grows, and the one debug string left out
  on purpose (MODDING.md).

## 2026-11-20 (later): text coverage

* **`text-coverage`** (`coverage.py`): every place the game shows text and
  what the kit does with it, counted from the disc; text drawn into pictures
  and movies is listed apart. `--verify` checks that every unedited tree
  imports as no change on each disc given (it does on both USA discs).
* **Executable strings, control codes**: strings are read with the print
  function's grammar (`FUN_00197bb8`; parameter counts agree with the table
  at 0x0063EA40), so the save prompts (`0B 0E 01 01 00 00 00 ...`), the skip
  line, `Start game?` and the memory-card prompt are listed whole. Codes and
  their parameters are escapes in the tree; an edit that ends inside a code,
  puts a zero byte where a character belongs, uses a code the loop never
  passes or leaves a `$code` open is refused. A `lui` on another path of the
  same function is followed to aligned string starts. The HDD installer's
  EUC-JP prompts are listed. Blocks drawn only by the debug print are marked
  `# debug (FUN_00195760 only)`, and blocks whose codes draw ASCII with the
  8x8 system font say so. The tree format is 2; export again.
* **Quest text**: each text must fit its field of the loader's record (read
  from `FUN_0022ee08`, `FUN_0022f000`, `FUN_00232160`); a file an editor saved
  with LF line ends gets its CR LF back, a mixed one is refused.
* **Memory-card title** (`icontitle`, `carddata.bin.title.txt`): edited in
  place; the characters the save writer `FUN_0019dc10` stamps (slot and
  time) stay where they are. USA only.
* Merged from `lang-exetext`: `exe-export` / `exe-import` themselves.

## 2026-11-20: strings in the executables (`exe-export` / `exe-import`)

* **`exetext.py`**, borrowed from the Episode I kit's `ovltext.py`: every
  string the code of the boot executable, `OV01.OVL` and `OV02.OVL` points
  at (pointer words, `lui` pairs, `$gp` addresses), as a block tree with each
  string's slot (`### @0x0068b678 max 15 bytes` for `Saving...`). Written
  back in place only, the rest of the slot zero-filled, nothing moved.
* `exe-import` must be the last step: afterwards the executable is not the
  retail one and `text-import` and `font-extract` refuse the
  image. `--retail` starts again from the retail executables for a second
  round.
* The boot executable carries the title overlay's bytes as a third segment
  (equal to `OV02.OVL`); strings there are listed in both trees and must be
  edited alike.
* `repack.patch_copy` takes `fs_replacements` (plain ISO files, same size).
  Not yet seen in the running game. [MODDING.md](MODDING.md).
* After review: an address formed from another path's `lui` now ends the slot
  of the string before it (`cdrom0:\` 41 to 15, `pfs1:/Your Saves` 35 to
  23); stores through a register holding a string's address are named, and an
  edit must keep those bytes (`Path No. 01 ...` gets its number in +9..+10);
  an inlined copy of known size caps `max` at the retail length; `%`
  conversions must stay as exported (an added `%s` or `%n` is refused).

## 2026-11-10 (later): fixes from the translation review

Four outside models reviewed the text work; every point and what was done is
in `docs/panel/2026-11-10-translation/RESOLUTION.md`. In short:

* **Writers** never patch their input (also not through a link) and publish a
  complete, verified image or nothing (`repack.patch_copy`). Unknown builds
  are read-only; text needs the exact known executable.
* **The tree** is unambiguous (`⟦23⟧` for a leading `#`), block keys are
  checked (order, duplicates, missing), embedded NULs and edits that would
  hide a string from the next export are refused, and a manifest ties the
  tree to its disc (`--rebase` overrides).
* **`textcodec.py`**: text is read per build. USA `é` is the Latin-1 byte
  0xE9; typed characters are checked against the disc's fonts.
* **Subtitles**: bounds measured on the disc's own retail files (line bytes,
  line width by a port of the game's width function, lines per cue), on top
  of the 64-byte rule. Bounds, not proven limits.
* **`font-rebuild`** refuses to apply nothing, and says what it applied.

## 2026-11-10: models open in Blender

`browse` now writes every `.pxy` model as a `.glb` (`--kinds models`, on by
default): mesh, UVs, textures inside the file, vertex colours, skin weights
and an armature from the `.xhr` skeleton. All 944 models of Disc 1 are
written in about a minute; 943 pass every numeric check (unit normals,
weights summing to one, indices in range, vertices inside their bounds, the
triangle count the file itself records). `model` exports one straight from
the disc. The layout was read from the game's VU1 microcode, not guessed from
the data. Motion is not decoded. [BLENDER.md](BLENDER.md),
[MODELS.md](MODELS.md).

## 2026-11-10: menus, subtitles, quest text and battle names in `text-export` / `text-import`

* Four more kinds of text, each exact both ways on every disc (an unedited
  export imports as no change; see MODDING.md for the table and the limits).
* **`moviesub.py`** packs subtitle cues the way the game does and enforces
  the 64-byte rule. Reading the packer also showed that the recorded
  explanation of that rule does not match the code; the true limit is open
  (MODDING.md, "Subtitle limit").
* **`smallfiles.batdat_replace`**: battle strings, in place.

## 2026-11-10: text banks may grow (`text-import --grow`)

* The two `.sbl` loaders were read: both load script and bank into one
  buffer of fixed size (scenes 0x20000 bytes at 0x3C6DC0, maps 0x12000). A
  bank may be relocated when it outgrows its slot, but never past its
  script's buffer; `text-import` enforces that. Not yet seen in the running
  game. See MODDING.md.
* `minidisc.py` builds a small disc image in code, so `textpack` now has an
  end-to-end self-test.

## 2026-11-10: fonts

* **Episode II addressing in `font.py`** (`Addressing`, `xs2_cell`), read
  from the executables: code to cell, the extra-kanji list, how USA single
  bytes (ASCII through a table, Latin-1 at cell = byte + 0x80) reach cells,
  and that the metric pair is indexed by cell in both regions (384 pairs USA,
  256 Japan with 192 used). The tables come from the disc's executable by
  virtual address. See FORMATS.md, Fonts.
* **Episode I's addressing is fenced** (`font.Episode1`); the old
  module-level names raise.
* **`font-extract` / `font-rebuild`**: sheets as PNG, metrics as CSV, and the
  byte-to-cell table. An unedited round trip is byte-identical on every disc.

## 2026-11-10: regions

* **`region.py`**: the build is read from the boot executable. USA Disc 1
  SLUS_208.92 and Disc 2 SLUS_211.33 (serials read from SYSTEM.CNF; one
  executable on both), Japan SLPS_253.68 / .69, the demo SLPS_253.53. Script
  text, movie subtitles, fonts and the battle database are found by the
  region's layout. Executable addresses go through the ELF program headers
  (`va_to_offset`).
* **Japanese text.** `text-export` on a Japanese disc writes the scripts'
  inline strings: 17,225 in 592 scripts, including 172 that only a pointer
  table reaches (`sb.Script.pointer_slots`). `text-import` writes them back
  in place (same length or shorter).
* **Codec.** The kit's text codec is `euc_jis_2004` (`sbl.CODEC`), chosen by
  measurement: the two codecs never disagree on a code both define; 73
  Japanese strings (JIS row 13: ①, Ⅰ) fail under `euc_jp` and none under
  `euc_jis_2004`; USA output is unchanged byte for byte.
* **Mixed discs.** `list`, `dump`, `natives` and `dialogue` warn when the
  images are different builds; the GUI's disc picker keeps one region.

## 2026-11-05 (night): the *Xenosaga Freaks* demo, native signatures

* **Chains in a directory.** `chains.chain_files` finds a chain's bigfiles
  at the root or in one directory, so the Episode II demo on *Xenosaga
  Freaks* (`EXP\XENOSAGA.00`-`.02`) lists, dumps and patches like a retail
  disc (3,612 entries; 2,655 files byte-identical to retail).
* **The older script form.** The demo's scripts keep their text inline:
  `pushtext` is an offset into the globals (EUC-JP), not an `.sbl` line.
  `sb.py` checks, lists and extracts both forms; all 135 demo scripts and all
  465 retail scripts pass. A zero source-name word is accepted.
* **`natives`**: every native the scripts call, with argument-type
  signatures from the pushed (value, type) pairs; `--out` adds per-script
  listings and the demo's inline strings. See SCRIPTS.md.

## 2026-11-05 (evening): music rendered the way the PS2 plays it

* **`spu2synth.py`**, a new renderer for the 31 KORG songs: the driver's
  rules read from `KORGIVAG.IRX` (hold keys, choke groups, the two SPU2
  cores, 24 voices per core and the steal order, the driver's pitch, volume
  and pan tables, bend and LFO, the 2 ms tick) on a model of the SPU2
  (ADPCM, Gaussian interpolation, exact ADSR, 16-bit clamps) with the
  SPU2's own reverb network and libsd's presets from the disc's
  `LIBSD.IRX`, ECHO/DELAY rewritten from each song's delay and feedback as
  libsd does. `browse --kinds music` renders with it. See MUSIC.md.
* **The crash that cut off**: drum and groove-slice keys *hold* (layer byte
  +12): the driver ignores their note-off, so a 0.5 s cymbal under a
  0.125 s note rings to its end. The SoundFont render released it at the
  note-off.
* **The missing reverb**: the old ECHO waited 403 ms per repeat; libsd's
  rule gives 537.7 ms for 001 Milchia, and a game rip agrees (538 ms). The
  reverb types now use the SPU2's network instead of fluidsynth's.
* Checked against five tracks of a game rip: closer on every measure taken
  (spectral error, tonal balance within 0.3 dB rms, stereo within 0.5 dB,
  held-hit envelopes, the effect type each song names fits best of the
  nine). Numbers in MUSIC.md.
* `prj.py`: byte +13 is the SPU2 core, not an on/off flag (any layer with a
  sample sounds); +11 is the choke group, +12 hold. SoundFont decay and
  release times are now scaled to the SF2 definition (time to −100 dB, about
  2.4× the old values), hold layers play out, choke groups become the
  exclusive class.
* `songfx.py` now only reads the songs' effect settings (libsd's ECHO/DELAY
  timing and feedback formula included); its fluidsynth render is gone.
* **numpy is required** (`requirements.txt`), since most people will use the
  app bundles, which carry it. The launchers install it into `.venv/` on
  first run when the Python they find lacks it; `build.py` refuses to build
  without it; `cli.py self-test` now fails, instead of skipping, a module that
  cannot be imported (so a bundle missing numpy fails its build). fluidsynth
  is no longer used; the GUI's status panel shows numpy in its place.
* `dump` always copies the two IOP modules the music render reads
  (`KORGIVAG.IRX`, `LIBSD.IRX`), so music no longer needs `--code`, and
  `browse` stops with a clear message if they are missing. Run on its own,
  `python spu2synth.py SONG.mid ...` finds them there and warns instead of
  rendering without the reverb.

## 2026-11-05 (later): embedded textures, recolour, AC3, first in-game checks

* **Embedded texture pages**: 2,167 txy blocks inside 697 effect files
  (`.esd`, `.esp`, the title backdrop `.bin`) decoded into
  `textures_png/_embedded/`, deduplicated by image sha1 against the standalone
  pass and each other: 3,547 entries, 1,333 new images, provenance in
  `embedded_textures.csv`. Standalone pages moved to `textures_png/` (the
  Episode I kit's name).
* **`recolor`**: hue-shift named texture entries in every copy, found by
  structure (entry names, palette tiles) rather than bytes. Preset
  `kosmos-hair`: 12 entries in 4 files. `--xtx` colourises a true-colour
  sheet (the title menu).
* **Movies**: `browse --movie-audio ac3|both` keeps the AC3 5.1 track (48
  movies; 2 are AC3 stereo) bit-exact, also as a raw `.ac3`.
* **`patch --grow` / `--relocate`** and a TOC report that lists relocated
  objects.
* **Checked in PCSX2**: pink title menu; a relocated subtitle file (same size,
  and grown past its slot) read from past the end of the original image. Found
  the subtitle limit described in MODDING.md: lengthening the displayed text
  of `s010100.txt` by 52 characters broke the first movie.

## 2026-11-05: first version

Started the same day the discs were first looked at, on the USA Disc 1 and
Disc 2 images.

* **Disc and TOC.** The table of contents is Episode I's with front-coded
  names; parsed and rebuilt byte-identically on all three TOCs (9,030
  entries). The same parser reads Episode III's catalogs (15,225 entries,
  all agreeing with Lybac's tables). `dump` + `verify`: 9,030 objects plus
  the code files, 0 problems, ARX objects recompressed byte-identically.
* **Textures.** `.txy` pages decode with Episode III's swizzles plus a new
  rule: each entry's word +0x30 names its bank. Skyboxes (23 faces in one
  file) and two-pass field maps come out right; overlaying all banks, as
  the Episode III kit does, garbled 71 of 78 bank-0 entries on one map.
  65,938 PNGs from 941 files in under a minute on 8 cores.
* **UI sheets.** Episode I's XTX decoder copied unchanged; true-colour
  sheets (title menu words, frame masks) drawn as such. Two multi-palette
  menu sheets remain.
* **Text.** `.sbl` banks rebuild byte-identically (465/465) and round-trip
  through editable UTF-8; `text-export` / `text-import` patch a copy of the
  disc (a same-size edit changed exactly 2 bytes of the image; a longer line
  updated the TOC size).
* **Scripts.** The `.sb` VM found in OV01 (46 opcodes, native registry) and
  disassembled: all 465 scripts structurally clean, 2.6 M instructions,
  dialogue lines shown inline.
* **Sound and music.** KORG `.vsf` banks to WAV (3,733 samples, decode
  health checked), `.prj` projects parsed (1,074/1,074) and turned into
  SoundFonts; all 31 songs rendered through fluidsynth.
* **Movies.** `.sfd`, `.ipu`, `.adx` through ffmpeg.

## 2026-11-05 (evening): UI sheet palettes, wiring

* **Portraits and item cards fixed.** 18 of the 69 quest-board portraits,
  item cards and button icons decoded grey or garbled: Episode I's palette
  ranking picked a wrong tile. These sheets park their palette in the
  canvas's top-right corner; `xtx.py` now recognises that layout and uses
  that tile alone (palette source `corner`), 69/69 correct. The three
  `*_alpha.xtx` frame masks are decoded as masks and applied to the pictures
  in their directory, as the game draws them: rounded corners, no dark strip
  (palette `corner+mask` in `xtx.csv`).
* **Wired in:** the new modules' self-tests (`sniff`, `songfx`, `smallfiles`,
  `toolfind`, `gui`) run under `cli.py self-test`; `browse` finds a bundled
  ffmpeg/fluidsynth first; `--kinds small`; music renders follow each song's
  effect settings.
* Still open: 11 multi-palette menu sheets (FORMATS.md, UI sheets).
