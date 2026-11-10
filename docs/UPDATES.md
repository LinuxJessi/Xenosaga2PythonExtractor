# Updates

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
