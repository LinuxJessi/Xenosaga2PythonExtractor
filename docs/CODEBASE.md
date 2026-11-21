# How the code is organized

A tour for someone who has never seen the kit: what each module does, how
they stack, and where to start when you want to change something. Byte
layouts live in [FORMATS.md](FORMATS.md) (and [research/small-files.md](research/small-files.md));
patching and mods in [MODDING.md](MODDING.md). This file is about the code.

## The design rules

The Episode I kit's rules, kept (the first one with three packages added):

1. **Standard library, plus numpy, fontTools and uharfbuzz.** Python 3.9 or
   newer and the three packages of `requirements.txt`: numpy because a
   voice-by-voice model of the SPU2 is too slow in pure Python, fontTools
   and uharfbuzz because shaping Arabic or Devanagari for the font needs
   real font outlines and HarfBuzz. They are required, never optional with
   a worse fallback. Everything else is the standard library; the PNG, WAV
   and SoundFont writers and the glyph rasteriser are written out by hand.
   ffmpeg (movies) is used when found and skipped when not. The app bundles
   carry the three packages and ffmpeg; the launchers install the packages
   into `.venv/` on first run when the Python they find lacks any of them.
2. **Every module stands alone.** `toc.py`, `arx.py`, `sb.py` and the rest
   import only what they need, so `from sb import parse` works in a
   throwaway script, and most double as scripts (`python sb.py FILE.sb`).
3. **Every claim has a self-test.** A module that decodes something has a
   `--self-test` with a synthetic sample; `cli.py self-test` runs them,
   and the release build runs them again from inside the bundle.

## The stack

```
   gui.py ──────────────► cli.py ◄──────────── you, at a terminal
 (cards built from          │
  cli.py's parser)          ├─ dump / list / verify ─► chains.py ─► toc.py      (table of contents)
                            │                          iso9660.py   arx.py      (compression)
                            │                          sniff.py     (manifest kind column)
                            ├─ browse ─► browse.py ─► txy.py  xtx.py  embedded.py   textures
                            │                         font.py sbl.py  sb.py         text, scripts
                            │                         vsf.py  prj.py  midi.py       sound, music
                            │                         songfx.py smallfiles.py       effects, one-offs
                            │                         spu2synth.py                  the PS2's music render
                            ├─ sb ─► sb.py + sbl.py
                            ├─ text-export / text-import ─► textpack.py ─► sbl.py, repack.py
                            ├─ lang-scan / lang-encode / lang-preview ─► langpack.py ─► glyphs.py, font.py, exetext.py
                            ├─ exe-export / exe-import ─► exetext.py ─► repack.py (plain ISO files)
                            ├─ text-coverage ─► coverage.py ─► textpack.py, exetext.py
                            └─ patch / recolor ─► repack.py, recolor.py
```

## The modules

| Module | Role |
|---|---|
| `iso9660.py` | reads the disc image's file system (from the Episode I kit) |
| `chains.py` | the three bigfile chains (`XENOSAGA.0x` shared, `.1x` Disc 1, `.2x` Disc 2) as byte spaces |
| `toc.py` | the table of contents: Episode I's grammar with front-coded names; parse and rebuild |
| `arx.py` | Monolith's ARX compression, both ways (from the Episode I kit) |
| `sniff.py` | names an object's format from its first bytes, or its path for the one-off files |
| `cli.py` | every command; `main()` builds one argparse parser that the GUI reads |
| `browse.py` | maps the decoders over a dump, one function per `--kinds` entry (`BUILDERS`) |
| `txy.py` | texture pages to PNG, one per named entry and bank |
| `embedded.py` | texture pages stored inside effect files |
| `xtx.py` | UI and sprite sheets (Episode I's decoder plus true-colour sheets) |
| `font.py` | the bitmap fonts (from the Episode I kit) |
| `sbl.py` | script text banks: parse, rebuild, edit |
| `sb.py` | the script machine: parse, disassemble, check ([SCRIPTS.md](SCRIPTS.md)) |
| `textpack.py` | dialogue as an editable UTF-8 tree, and back onto a disc copy |
| `textcodec.py` | text bytes <-> editable text per build, and the check on typed characters |
| `glyphs.py` | new glyphs into the USA fonts: the code tiers, allocation, rasterising with fontTools/uharfbuzz (from the Episode I kit, addressing rewritten; [ALPHABETS.md](ALPHABETS.md)) |
| `langpack.py` | shaping, bidi order, wrapping and encoding of a translated tree in a new alphabet (from the Episode I kit) |
| `exetext.py` | the strings the code points at in the boot ELF, OV01 and OV02, as a tree, read with the print function's control-code grammar; written back in place (from the Episode I kit's `ovltext.py`) |
| `coverage.py` | every place the game shows text, counted from the disc, with what the kit does with it; `--verify` round-trips the unedited trees |
| `vsf.py` | KORG sample banks to WAV |
| `prj.py` | KORG projects: keys, programs, SoundFont 2 ([MUSIC.md](MUSIC.md)) |
| `midi.py` | Standard MIDI helpers (channel 10 for General-MIDI players) |
| `songfx.py` | the songs' effect settings, and renders that follow them |
| `spu2synth.py` | the songs as the PS2 plays them: KORG's driver rules on a model of the SPU2 and its reverb (numpy; [MUSIC.md](MUSIC.md)) |
| `smallfiles.py` | packs, title logos, the save icon, the battle database, map/actor tables |
| `repack.py` | writes objects back into a disc copy (same size, or grown and moved); plain ISO files in place (`fs_replacements`) |
| `recolor.py` | hue-shifts named textures in every copy on a disc |
| `gui.py` | the local web GUI |
| `toolfind.py` | finds ffmpeg: the copy bundled under `tools/` first, then PATH |
| `build.py` | builds the double-click bundle with PyInstaller (`packaging/`) |

## The GUI reads the CLI

`gui.py` does not list the commands itself. When it starts it asks
`cli.py` for its parser (`cli.main` is run with `parse_args` swapped for a
hook that hands the parser back) and makes one card per subcommand: an
on/off flag becomes a checkbox, a repeatable option a box with one value
per line, `--iso` a disc-image picker, `--out` a folder picker (or a
disc-image save picker when the command writes a disc copy), `--kinds`
one checkbox per browse kind. So:

* **adding a command** to `cli.py` adds a card, with nothing to change in
  `gui.py`. Friendlier help text for it can go in `gui.py`'s
  `COMMAND_HELP`, a better picker in `FIELD_HINTS`; both are optional.
* `python gui.py --self-test` checks every subcommand has a card and that
  a command it has never seen (options, a float, a flag, a positional)
  turns into the right argv.

## Adding a browse kind

Write `browse_<kind>(out, rows, limit)` (rows are the manifest rows; read
objects from `out/dump/<area>/<path>`, write under `out/browse/<kind>/`),
add it to `BUILDERS` and `ALL_KINDS` in `browse.py`. `smallfiles.browse_small`
is written to that signature already.

## Packaging

`build.py` runs PyInstaller on `packaging/xenosaga2-extractor.spec`, which
bundles every module of the kit (found by listing the folder, so a new
module is never left out), puts `Xenosaga-II-Extractor` (the GUI) and
`xeno-cli` side by side, wraps them in a `.app` on macOS, and then runs both
self-tests from inside the bundle. Output stays in `packaging/dist/`
(ignored by `packaging/.gitignore`). The release workflow
(`.github/workflows/release.yml`) does the same on GitHub's Windows and
macOS machines with Python 3.12, adds a portable ffmpeg, and attaches the
zips to a draft release when a `v*` tag is pushed.
