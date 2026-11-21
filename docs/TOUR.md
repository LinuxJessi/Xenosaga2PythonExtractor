# A tour of the Xenosaga Episode II extractor

This is the short, look-at-it version. Every section shows one thing the kit
does, the command that does it, and what comes out, using real output from the
USA discs. The reference pages ([FORMATS](FORMATS.md), [MODELS](MODELS.md),
[MUSIC](MUSIC.md), [MODDING](MODDING.md), ...) say how each format works and how
it was proven; this page only shows you the results.

The kit ships no game data. You need your own copy of the discs, and everything
below was produced from them.

**Contents:** [Setup](#setup) · [Models](#models) · [Animation](#animation) ·
[Textures](#textures) · [Menus and images](#menus-and-images) · [Fonts](#fonts) ·
[Dialogue](#dialogue) · [Scripts](#scripts) ·
[Sound effects and music](#sound-effects-and-music) · [Movies](#movies) ·
[Small files](#small-files) · [Changing the game](#changing-the-game) ·
[Another alphabet](#another-alphabet) · [Text coverage](#text-coverage) ·
[The window](#the-window) · [Reproducing the demos](#reproducing-the-demos)

## Setup

```sh
python -m pip install -r requirements.txt      # once: numpy, fontTools, uharfbuzz
python cli.py dump    --iso "Disc 1.iso" --iso "Disc 2.iso" --out out/ --code
python cli.py verify  --out out/
python cli.py browse  --out out/
```

`dump` unpacks the discs into `out/dump/` and writes `out/manifest.csv`, one row
per object on the disc. `browse` turns the dump into files your computer opens
and writes the result to `out/browse/`.

A row of `manifest.csv`:

```
area,path,sector,size,compressed,usize,sha1,kind,disc,bigfile,bigfile_offset
chain0,font0.us,53,492352,0,,6ec1aaed7987b9aa4f30007ac48826a1613b9f90,font,1,XENOSAGA.01,0
```

A run over both USA discs lists 9,043 objects. `chain0` is the part both discs share;
`chain1` and `chain2` are specific to Disc 1 and Disc 2.

What `browse` writes, by folder:

| Folder | What is in it | Files (both USA discs) |
|---|---|---|
| `models/` | every model as `.glb` | 944 |
| `textures_png/` | every texture page as PNG | 67,271 |
| `xtx/` | the UI sheets | 85 |
| `images/` | full-screen JPEGs (synopsis, game over, map pictures) | 36 |
| `fonts/` | font sheets and metrics | 10 |
| `text/` | dialogue, menus and quest text, as editable text | 833 |
| `scripts/` | every scene script, disassembled | 465 |
| `audio/` | sound effects as WAV | 3,733 |
| `music/` | the 31 songs: MIDI, SoundFont and rendered WAV | 124 |
| `movies/` | cutscenes and monitor clips as MP4, `.adx` audio as WAV | 140 MP4, 657 WAV |
| `small/` | boot screens, title logos, battle strings, an index of side files | 212 |

Pick parts with `browse --kinds models,text,music` and so on; see
[BROWSING.md](BROWSING.md).

## Models

Every model on the disc becomes a `.glb` that Blender opens directly:
mesh, UVs, textures inside the file, vertex colours, skin weights and, for 562
of them, an armature whose bones sit on the mesh.

```sh
python cli.py browse --out out/ --kinds models          # all of them, about a minute
python cli.py model  --iso "Disc 1.iso" --path 'model\char\pc\kosmos00.pxy' --out kosmos00.glb   # just one
```

`out/browse/models.csv` says what each one has:

```
file,skeleton,textures,triangles,checks,status
model\char\pc\kosmos00.pxy,kosmos00.xhr,kosmos00.txy,2990,all pass,ok
```

Where to look first: `model/char/pc/` is the party, `model/char/npc/` the
people, `model/enemy/` the enemies and bosses, `model/robo/` the E.S. craft and
`model/map/` the places.

<table>
<tr>
<td><img src="images/gallery/kosmos00.jpg" width="150" alt="kosmos00"><br><code>char/pc/kosmos00</code></td>
<td><img src="images/gallery/shion00.jpg" width="150" alt="shion00"><br><code>char/pc/shion00</code></td>
<td><img src="images/gallery/gaignun00.jpg" width="150" alt="gaignun00"><br><code>char/npc/gaignun00</code></td>
<td><img src="images/gallery/pope00.jpg" width="150" alt="pope00"><br><code>char/npc/pope00</code></td>
</tr>
</table>

<img src="images/gallery/E2_KUK06.jpg" width="480" alt="E2_KUK06"> <img src="images/gallery/E2_MIL12.jpg" width="480" alt="E2_MIL12">

*`map/E2_KUK06` and `map/E2_MIL12`. More pictures are on the [front page](../README.md#models).*

Things to know before you open one:

* Open the `.glb` with File ▸ Import ▸ glTF 2.0, switch the viewport to
  **Material Preview** (the third shading ball) or it looks grey, and hide the
  **Bones** overlay if the importer's bone shapes look like balls. The steps are
  in [BLENDER.md](BLENDER.md).
* The figures here stand in the bind pose. The motion clips come with each
  model as animations ([next section](#animation)).
* Maps include their sky domes. If a map looks boxed in or tiny, hide the
  biggest objects; `docs/gallery/render_gallery.py` shows one way.

## Animation

The motion files (`.xap` for the body, `.fap` for the face) are decoded, and
each clip becomes one glTF animation on the model's armature, at 30 keys per
second. `browse` adds the clips of the same name to every model (142 of the
944 get motion); `model` takes more by path:

```sh
python cli.py model --iso "Disc 1.iso" --path 'model\char\pc\shion00.pxy' --out shion00.glb \
       --motion 'motion\shion00.xap#0,1,2' --motion 'yamamoto\mot\shio00.xap#0,1' --face
```

<img src="images/front/motion-kosmos-shion-walk.gif" width="300" alt="KOS-MOS and Shion playing clip 1 of their field motion files">

*KOS-MOS and Shion playing slot 1 of their field motion files
(`motion/kosmos00#01`, `motion/shion00#01`), rendered in Blender from the
kit's `.glb` files. The kit calls this clip walk-like; the game's names for
the slots are not known.*

In Blender, set the scene to 30 fps before importing, then pick a clip in the
Dope Sheet's Action Editor. The names are the disc file and the slot
(`motion/shion00#01`). All 4,340 clips on the disc decode. Hair, coats and
skirts stay stiff, because the game simulates them and the kit does not, and
none of this has been compared with the running game. A clip whose block
counts differ from the skeleton's may belong to another model; its name ends
in the counts (`[counts A30/55]`). Details in [MOTION.md](MOTION.md).

## Textures

Every texture page on the disc is a PNG, named the way the artists named it.

```
textures_png/chain0/model/char/pc/kosmos00.txy/
    000_kosmos00_hair_col.png     011_kosmos00_body.png
    005_kosmos00_ude.png          017_kosmos00_asi.png
    023_kosmos00_eye.png          024_kosmos00_face.png   ...
```

<img src="images/tour/textures_kosmos.png" alt="KOS-MOS hair, body, face, eye, arm and leg textures">

*From the left: `hair_col`, `body`, `face`, `eye`, `ude` (arm) and `asi` (leg) of
`kosmos00`, enlarged without smoothing. The textures are small and palette-based,
which is why they look soft.*

Skybox faces and animated pages come out one file per bank, and 1,333 more
textures come out of the pages embedded inside effect files
(`textures_png/_embedded/`).

## Menus and images

The `.xtx` sheets are the game's user-interface art: portraits, icons, the title
menu words. Full-screen pictures come out as JPEGs.

<img src="images/tour/ui_sheets.png" alt="Two citizen portraits, a dialogue-box sheet and the title menu words">

*`xtx/chain0/iwase/img/citizen/c01` and `c02` (citizen portraits), `omiya/talk01`
(interface pieces) and `yajima/titleobj` (the title menu: NEW GAME, CONTINUE,
INSTALL, ...).*

<img src="images/tour/synopsis.jpg" width="320" alt="The Story So Far screen"> <img src="images/tour/gameover.jpg" width="320" alt="Game over screen">

*`images/chain0/yajima/synopsis/01.jpg` ("The Story So Far") and `gameov1.jpg`.*

## Fonts

```sh
python cli.py font-extract --iso "Disc 1.iso" --out font/
```

You get each font file, its four glyph sheets as PNG, a table of proportional
widths, and the byte-to-cell table that says which character each byte draws.

<img src="images/tour/font_sheet.png" width="360" alt="font0 glyph sheet 0">

*`font/font0_sheet0.png`, the first of four sheets of the main font.*

```
slot,drawn_cell,left,right
0,576,0,19
1,577,2,6
2,578,3,8
```

*`font0_metrics.csv`: for each glyph slot, the cell it draws and a left and a
right metric. `font0_bytes.csv` maps each byte to its cell.
`browse --kinds fonts` writes the same sheets and metrics under `fonts/`, named
`font0_us.sheet0.png` and `font0_us.metrics.csv`.*
`font-rebuild` goes the other way, building a font file from edited sheets
([MODDING.md](MODDING.md)).

## Dialogue

There are three views of the same words, from raw to readable.

**1. Text banks, as the disc stores them.** `browse` and `text-export` write one
file per bank, with `### NNNN` markers you keep when you edit. This is the start
of `text/chain0/cf/en/sc1410.sbl.txt`:

```
### 0003
[Pitt]My son always asked me about work, so I finally got permission for him to look around.
### 0004
[Pitt]I would think a kid his age would enjoy going to a toy store a lot more than a lab.
### 0005
[Pitt]Please save my son!
```

Menus, quest text and citizen profiles come out the same way. `yosikawa/MainMenu.txd.txt`
starts with the party's names, and `iwase/Citizen.txt` holds the citizens:

```
No.1
{
	Name:"Sikes"
	Sex:"Male"
	Age:"28 years old"
	Hobby:"Visiting hot springs"
	Profile:"
A nice man who works for Sincere Deliveries. He\n
strives day and night to hand deliver sincerity\n
throughout Miltia."
}
```

**2. Every spoken line, in play order.**

```sh
python cli.py dialogue --iso "Disc 1.iso" --iso "Disc 2.iso" --out lines/ --voice
```

This writes `dialogue.csv`: speaker, text, which script and branch the line
belongs to, and the native call that shows it (columns trimmed below).

```
source,scene,order,speaker,text,native
script,sc1410,0,Pitt,"My son always asked me about work, so I finally got permission for him to look around.",talk.smsg
script,sc1410,1,Pitt,I would think a kid his age would enjoy going to a toy store a lot more than a lab.,talk.smsg
script,sc1410,2,Pitt,Please save my son!,talk.smsg
```

The full file also has the branch each line sits on (`if@05f0 > case 11@05f6 > ...`),
so you can tell which lines appear together. There are 8,318 lines from 363
scripts: text boxes, messages, choices, timed event lines and battle callouts.

**3. Movie subtitles and voices.** The 45 cutscenes and 80 real-time events come
out as `.srt` files, and with `--voice` each timed line is cut from the movie's
soundtrack as its own WAV:

```
1
00:00:04,000 --> 00:00:06,467
I see.

2
00:00:06,467 --> 00:00:09,467
So this is the Y-Data.
```

*`subtitles/EP07.srt`.* The matching clips are `voice/EP07/01_I_see.wav`,
`02_So_this_is_the_Y_Dat.wav` and so on. Play a few in the
[audio table on the front page](../README.md#audio-samples). More in
[DIALOGUE.md](DIALOGUE.md).

## Scripts

Every scene and map script is a small program for the game's own script machine.
The kit disassembles all 465 of them, names every call into the game, and puts
each spoken line next to the instruction that shows it.

```sh
python cli.py sb --iso "Disc 1.iso" --path 'cf\sc1410.sb'     # one script
python cli.py natives --iso "Disc 1.iso" --iso "Disc 2.iso" --out natives/   # every native call
```

A piece of `scripts/chain0/cf/sc1410.sb.asm`: if variable `g23` is zero, say the
first Pitt line, and otherwise say the second.

```
  0610  push0
  0611  op        eq
  0614  jz        L_062c
  0617  pushtext  text#3    ; [Pitt]My son always asked me about work, so I finally got permission f
  061a  push3     ; type text
  061b  push      1
  061e  native    talk.smsg
  0621  drop
  ...
L_062c:
  062c  pushtext  text#4    ; [Pitt]I would think a kid his age would enjoy going to a toy store a l
```

Each script starts with the list of natives it calls (`runtime.mapChange`,
`runtime.getFlag`, `talk.smsg`, `camera.SetCamPos`, ...), which is a quick way to
see what a scene does. `natives` collects them all with argument types and call
counts. The machine is described in [SCRIPTS.md](SCRIPTS.md).

## Sound effects and music

**Sound effects.** 3,733 named samples from 1,074 sound banks, as WAV with their
loop points. `audio.csv` lists them:

```
index,name,rate,root,fine,loop,file,samples,peak,clipped,dc,bank
1,CFS00001.wav,22050,60,0,,001_CFS00001.wav,13020,8244,0,-2.2890168970814133,sound\vsf\SYS_SE.vsf
```

**Music.** The field and menu music is not recorded audio. The game plays 31
MIDI songs through a KORG sound driver on the PS2's sound chip, so the kit gives
you three things per song:

| File | What it is |
|---|---|
| `001_milchia.mid` | the song as stored (480 ticks per beat) |
| `001_milchia.sf2` | a SoundFont built from the song's own samples |
| `001_milchia.wav` | a render that follows the driver's rules and the sound chip's reverb |

```
song,mid,ppqn,ticks,sf2,wav,fx,seconds
001_milchia,001_milchia.mid,480,107760,001_milchia.sf2,001_milchia.wav,ECHO (return 90/127),118.1
```

Short clips of both are in [`samples/`](samples/) and listed in the
[front-page audio table](../README.md#audio-samples): five songs, two
sound effects and three event voice lines. How the renderer works and how it was
checked is in [MUSIC.md](MUSIC.md), which also says where it still differs from
the game.

## Movies

```sh
python cli.py browse --out out/ --kinds movies
```

`.sfd` cutscenes become MP4 at 512×448 with their stereo audio
(`--movie-audio ac3` or `both` keeps the 5.1 track); the `.ipu` monitor clips
are smaller (256×224, 128×112, 128×128, one 256×256). The `.adx` audio files
become WAV. In my run 139 of the 140 movie files converted (50 cutscenes, 89
monitor clips); the MP4 of `iwase\avitest.ipu` came out empty. `s010100`, the
opening, runs about eight and a half minutes.

<img src="images/tour/movie_s010100.jpg" width="320" alt="A frame from the opening movie">

*A frame from `movies/chain1/movie/s010100.sfd.mp4`.*

## Small files

`browse --kinds small` unpacks the little files that do not fit anywhere else:
boot screens, the title logos, the save icon, and the battle database's strings.
`small.csv` says what every side file is:

```
area,path,what
chain0,yajima\title.bin,"title logo, raw 512x448 RGBA"
chain0,carddata.bin,"pack of 2: 1 ico, 1 sys"
```

`batdat.us.strings.csv` has the 1,085 battle strings, starting with the party:

```
section,offset,text
0,1380,chaos
0,1386,KOS-MOS
0,1394,Shion
0,1400,Jin
```

## Changing the game

Everything above reads the discs. These commands write a changed copy of a disc.
They never write over the original.

### Recolour a texture

```sh
python cli.py recolor --iso "Disc 1.iso" --dry-run --preview prev/      # look first; writes only PNGs
python cli.py recolor --iso "Disc 1.iso" --out pink.iso                 # KOS-MOS's hair, pink
```

`--dry-run --preview` writes a before and after PNG for every texture it would
change and touches no disc. On Disc 1 it finds 12 entries in 4 files, about 570
colours each:

<img src="images/tour/recolor_preview.png" alt="KOS-MOS hair and face textures, blue before and pink after">

*`kosmos00_hair_col` and `kosmos00_face`, before (blue) and after (pink).*

The kit finds every copy of a texture by structure rather than by bytes, because
the game stores KOS-MOS's hair in several files at different offsets.

### Paint textures and put them back

```sh
cp -R out/browse edited/                                            # paint PNGs in the copy
python cli.py texture-import --iso "Disc 1.iso" --tree edited/ --dry-run
python cli.py texture-import --iso "Disc 1.iso" --tree edited/ --out modded.iso --preview prev/
```

Paint over any PNG `browse` wrote: a texture page, a UI sheet, or an image
inside a model's `.glb` after a trip through Blender. `texture-import` writes
only the pictures that changed, into every copy of the same picture on the
disc (KOS-MOS's hair is in four files), keeps shared palettes intact for the
other textures, and decodes every write again from the new image before the
copy gets its name. The `--dry-run` lists each swept group, so you see what
one edit reaches.

<img src="images/front/texture-kosmos-full.jpg" alt="KOS-MOS from the retail disc and from the modded copy, front and back">

<img src="images/front/texture-title-menu.png" width="560" alt="The title menu sheet, retail and tinted gold">

*Re-extracted from the modded copy of Disc 1 and rendered in Blender: the hair
band, the green arm texture and a ring painted on her chest texture through a
`.glb`; and the title menu sheet tinted gold. Checked on the disc copy (every
unrelated object byte-identical); not yet booted in PCSX2. The rules are in
[MODDING.md](MODDING.md#editing-textures-texture-import).*

### Edit the text

```sh
python cli.py text-export --iso "Disc 1.iso" --out text/
# edit text/..., keeping the "### NNNN" lines and codes such as [Pitt], \n and $zoom13;
python cli.py text-import --iso "Disc 1.iso" --text text/ --out modded.iso
```

`text-import` says which limit an edit broke: menu and quest text may not need
more disc sectors than the original, each quest text must fit its field,
battle strings may grow within the file's sectors (the rest of their section
moves down), subtitles must stay inside what the retail subtitles reach, and
dialogue banks can grow with `--grow`. It refuses a tree that came from another
disc, never writes over its input, and gives you a complete image or none. The
rules, and what was checked in an emulator, are in [MODDING.md](MODDING.md).

### Strings in the game's code

A few strings the game shows live in the executables, not in text files:
`Saving...`, the memory-card and disc-change prompts, `Total Exp`.

```sh
python cli.py exe-export --iso "Disc 1.iso" --out exe/
python cli.py exe-import --iso modded.iso --text exe/ --out final.iso     # run last
```

```
### @0x0068b678 max 15 bytes
Saving...
```

Each string is rewritten in place, at most the `max N` bytes its header gives.
Run `exe-import` last: afterwards the executable is no longer the retail one
and the other text commands refuse the image
([MODDING.md](MODDING.md#executable-strings-exe-export--exe-import)).

### Replace any file

```sh
python cli.py patch --iso "Disc 1.iso" --out modded.iso --set 'chain0:cf\en\sc1010.sbl=new.sbl' [--grow]
```

## Another alphabet

The USA font has Latin letters only, so the kit draws a new script's glyphs
into it. Translate the exported tree as you would type it, then:

```sh
python cli.py lang-scan   --iso "Disc 1.iso" --text tr/ --font NotoNaskhArabic.ttf \
    --script Arab --language ar --rtl --out fonts/
python cli.py lang-encode --text tr/ --glyphset fonts/glyphset.json --out tr-enc/ --width-kind bank=410
python cli.py lang-preview --glyphset fonts/glyphset.json --text "[Canaan]ما هذا الشيء؟" --out line.png
```

`lang-scan` shapes every string with HarfBuzz, gives each glyph a free cell
and draws it into both fonts; `lang-encode` writes the tree back with the
glyphs as codes, right-to-left lines reordered and wrapped, every byte limit
checked; `patch` (the two fonts) and `text-import` put it on a copy of each
disc. The room is 246 codes.

<img src="images/front/lang-new-cells.png" width="420" alt="The 144 glyph cells drawn for the LANGTEST disc">

*The 144 cells `lang-scan` drew for the LANGTEST test disc: Arabic,
Devanagari, Georgian and Ethiopic.*

<img src="images/front/lang-preview-lines.png" width="470" alt="lang-preview output for four lines">

*`lang-preview` of four of its lines, drawn with the rebuilt font.*

That disc was played in PCSX2. Arabic in the field box, Hindi in movie
subtitles and real-time cutscenes, and Amharic on the save screen were drawn
by the game:

<img src="images/ingame-xs2-arabic-box.png" width="320" alt="Arabic in the field box"> <img src="images/ingame-xs2-hindi-movie.png" width="320" alt="Hindi subtitle in the opening movie">

The field menu's party names drew blank, and the battle screen was not
reached. The whole procedure, the captures and what failed:
[ALPHABETS.md](ALPHABETS.md).

## Text coverage

```sh
python cli.py text-coverage --iso "Disc 1.iso" --iso "Disc 2.iso" --verify
```

lists every place the game shows text and what the kit does with it, counted
from your discs. The start of its output:

```
[editable] dialogue and scene text (USA: one bank per scene or map): 465 file(s), 9699 string(s)
[editable] menus, item/skill/location names, help text, database, the Skill Tutorial: 3 file(s), 903 string(s)
[editable] battle names and descriptions: party, enemies, skills, items, equipment, messages: 1 file(s), 1085 string(s)
[editable] quest, citizen and treasure text: 6 file(s)
[editable] movie subtitles: 45 file(s)
[in place] the save's title in the PS2 memory-card browser: 1 file(s), 1 string(s)
[editable] battle banners: the enemy skill and action names a battle shows (bat.namePut): 1363 call(s), 416 distinct string(s) in 113 bank(s)
[in place] strings in SLUS_208.92 (boot ELF): 3498 strings
```

*(One line per kind shown; the real output adds where each lives and its
limits, then the words drawn into pictures and movies.)* `--verify` exports
every text and executable string and checks that the unedited trees import
as no change on each disc. The full table is in
[MODDING.md](MODDING.md#what-is-covered-text-coverage).

## The window

Prefer clicking to typing? `python gui.py`, or double-click `launch.command` on a
Mac (`launch.bat` on Windows, `launch.sh` on Linux). It opens a local page with
one card per command, 22 in all, in sections by goal: **Start here** (1 Unpack
the discs, 2 Check the unpacked files, 3 Convert to files you can open), then
Look at one thing, Change pictures, Change text, Fonts and whole files,
Translate into a new alphabet, and For developers. A closed card shows a plain
title, a one-line summary and the command's name; an open one says what it
needs and what it makes, and after a run where the output is, with a button
that shows it in Finder or Explorer. Cards that edit the game write a new disc
image and are marked "new disc copy"; the original is never changed. The cards
are built from the command line itself, so new commands appear on their own
(in an "Other commands" section until they get their own text).

<img src="images/front/gui-window.png" alt="The kit's window with the dump card open">

*The disc paths in this screenshot are example names.*

## Reproducing the demos

The demos on this page and on the front page are scripts in `tools/`, each
taking the disc images and output folders as arguments:

```sh
tools/langtest/langtest.sh "Disc 1.iso" langtest/                 # the LANGTEST disc, built and checked
tools/demo/motion.sh "Disc 1.iso" out/dump/ demo-motion/          # the motion exports and their checks
tools/demo/texture-import.sh "Disc 1.iso" out/ demo-textures/      # the texture demo on a disc copy
```

What each needs is at the top of the script and in [CODEBASE.md](CODEBASE.md).
The Blender scripts behind the pictures are in [`gallery/`](gallery/):
`render_gallery.py` (the gallery), `render_motion_preview.py` (the walking
loop) and `render_bind_pose.py` (the texture before and after).
