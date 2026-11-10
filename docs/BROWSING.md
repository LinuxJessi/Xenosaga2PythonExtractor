# A tour of the output

This is what you have after `dump` and `browse`, and where to look first.
The counts are from the USA discs (Disc 1 and Disc 2 given together).

```
OUT/
  manifest.csv   one row per object: area, in-game path, sector, size, sha1, kind
  dump/          the discs, unpacked, as the game addresses them (9,030 objects)
  browse/        the same assets converted to formats your desktop opens
```

Rule of thumb: **`dump/` is for tools, `browse/` is for people.** The
`kind` column of the manifest says what each object is (`sniff.py`); the
formats behind the kinds are in [FORMATS.md](FORMATS.md) and
[research/small-files.md](research/small-files.md).

## `dump/`: three chains and the code

| Area | Comes from | What it holds |
|---|---|---|
| `chain0/` | `XENOSAGA.00-.02`, the same on both discs | the game: 8,605 objects |
| `chain1/` | `XENOSAGA.10-.14`, Disc 1 | Disc 1's movies, cutscene voices and motion: 233 |
| `chain2/` | `XENOSAGA.20-.24`, Disc 2 | Disc 2's: 192 |
| `browse/code/` | the ISO file system (`--code`) | `SLUS_208.92`, `SLUS_211.33` (identical), `OV01.OVL`, `OV02.OVL`, the IOP modules |

ARX-compressed objects (three, all under `yajima\`) are written
decompressed; `verify` recompresses them and checks the sha1.

### `chain0/` by folder

| Folder | Objects | What it is |
|---|---|---|
| `model/` | 2,918 | every model: `char/` (pc, npc, cf_npc, citizen), `enemy/`, `robo/` (E.S.), `mobj/` (map objects), `map/`; each as `.pxy` mesh + `.txy` textures + `.xhr` skeleton, maps with `.id` data and their enemy scripts (`map/E2_*.bin` + `map/en/*.sbl`) |
| `sound/` | 2,708 | `vsf/` and `vsf/battle/` sample banks with their `.prj` projects, `smf/` the 31 songs (`.mid` + `.prj` + `.vsf`), `adx/` streamed music and voices |
| `simajiri/` | 1,147 | effects: 1,022 `.esd`, 125 `.esp` |
| `cf/` | 1,022 | scenes: 352 scripts `.sb` with their text in `cf/en/*.sbl`, 314 `#scn` asset lists (`.txt`), 4 `.ene` tables |
| `yamamoto/` | 320 | battle: `map/` battle maps, `mot/` motions, `stat/stat.xtx` (the battle HUD), `batdat.us` (the battle database) |
| `motion/` | 237 | character motion `.xap` and the face clips `face_all.fap` |
| `iwase/` | 80 | quest and citizen text (`.txt`, Shift-JIS), citizen portraits, door/item images |
| `matumoto/` | 58 | field objects: items, treasure boxes, save points, breakables, and their `.dat` tables |
| `yosikawa/` | 56 | menus: `MainMenu.txd`, `Tutorial.txd`, `Location.txd`, the menu background model, UMN images |
| `yajima/` | 33 | title, ending and game-over art, the synopsis pictures, and the packs below |
| `movie/` | 20 | `cfevent/*.ipu` monitor clips |
| `omiya/` | 2 | two UI sheets |
| (root) | 4 | `font0.us`, `font1.us`, `credit.bin`, `carddata.bin` |

`chain1/` and `chain2/` each hold `movie/` (the `.sfd` cutscenes, `.ipu`
clips, `en/*.txt` subtitles), `sound/adx/cfevent/` (cutscene voice and
music streams), `motion/cfevent/` (cutscene motion) and a 16 MiB file of
zeros, `16m.bin`.

## `browse/`

| Folder | Made by | What you get |
|---|---|---|
| `textures_png/` | `--kinds textures` | every `.txy` page as named PNGs (`shion00_hair`, `kosmos00_face`): 65,938 from 941 files; texture pages found inside effect files under `_embedded/` |
| `xtx/` | `xtx` | the 85 UI sheets |
| `fonts/` | `fonts` | the four glyph sheets of each font + metrics |
| `text/` | `text` | dialogue banks, menus, subtitles and quest text as UTF-8 |
| `scripts/` | `scripts` | the 465 scripts disassembled, dialogue inline ([SCRIPTS.md](SCRIPTS.md)) |
| `audio/` | `audio` | the 3,733 named samples as WAV |
| `music/` | `music` | the 31 songs: MIDI, SoundFont, WAV ([MUSIC.md](MUSIC.md)) |
| `images/` | `images` | the `.jpg` files, and the two `.txy` files that are really JPEGs |
| `movies/` | `movies` | cutscenes, monitor clips and voices through ffmpeg |
| `code/` | `dump --code` | the executables and IOP modules, for disassemblers |

Where to look first: `textures_png/chain0/model/char/pc/` (the party),
`music/001_milchia.wav`, `scripts/chain0/cf/sc1010.sb.asm` next to
`text/chain0/cf/en/sc1010.sbl.txt`, and the cutscenes in `movies/`.

## The small files (`smallfiles.py`)

The one-off files are decoded by `smallfiles.py` (a `browse` kind once it
is wired in; until then run it directly):

```sh
python smallfiles.py OUT/dump/chain0/credit.bin OUT/dump/chain0/carddata.bin \
    OUT/dump/chain0/yajima/title.bin OUT/dump/chain0/yamamoto/batdat.us --out small/
```

| File | Becomes |
|---|---|
| `credit.bin` | the six boot screens as JPEG: Namco, Monolith Software, CRI Sofdec/ADX, KORG, Dolby Pro Logic II, "Produced by NAMCO" |
| `yajima/im.bin` | four character key-art screens (JPEG) |
| `yajima/title.bin`, `title2.bin` | the title logo, "DISC 1" and "DISC 2", as PNG |
| `carddata.bin` | the save's `icon.sys` (its title) and 3D icon, with the icon texture as PNG |
| `yajima/synopsis.bin` | its two XTX sheets |
| `yajima/titlebg2.bin` | the 190 members of the title scene (models, motion, cameras) |
| `yamamoto/batdat.us` | 1,085 strings (names and descriptions of skills, items, equipment, enemies; battle messages) as CSV |
