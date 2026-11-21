# Fun facts about Xenosaga Episode II

Things learned while taking the discs apart. Almost all of them come from
the retail discs and can be checked with this kit: USA SLUS-20892 /
SLUS-21133, plus the Japanese SLPS-25368 / 25369 and the 2004 demo on
*Xenosaga Freaks* where noted. Each fact names where to look. The few
facts from outside the disc are linked to their source. The longer write-ups
are in [FINDS.md](FINDS.md) (leftovers) and [HISTORY.md](HISTORY.md)
(timeline).

**Contents:** [The people on the disc](#the-people-on-the-disc) ·
[How it was built](#how-it-was-built) · [Words](#words) ·
[Music and sound](#music-and-sound) · [Movies](#movies) ·
[Models and motion](#models-and-motion) · [Under the hood](#under-the-hood) ·
[Left on the disc](#left-on-the-disc) · [Between Episode I and III](#between-episode-i-and-iii) ·
[By the numbers](#by-the-numbers)

## The people on the disc

* **Half the disc is still filed by staff name.** All 1,147 effects live in
  `simajiri\`, the battle in `yamamoto\`, the menus in `yosikawa\`, the
  quests in `iwase\`, field objects in `matumoto\`, and the title and ending
  art in `yajima\`. The engine asks for these folders by name:
  `yosikawa\MainMenu.txd` is a string in the executable.
* **Every scene script signs its writer.** Each `.sb` ends with its source
  path: `nakahara\` wrote 209 scenes, `koji\` 55, `fuji\` 43, `yonesaka\` 23,
  `kuramoto\` 12.
* **Someone signed off on the subtitles.** The sample subtitle file in the
  movie folder ends its header with `;11/21米坂チェック`, "11/21, Yonesaka's
  check" (`movie\en\s010100.txt`).
* **The developers' home folders are still in the code.** The executable asks
  for `host0:/home/xeno/vcd-f/x2/sound/smf/%s.prj` and
  `host0:/home/xeno/work/iwase/jr00.pxy`. In those paths `x2` is the game and
  `x2u` the USA build.
* **One music file leaked a Windows drive letter.** The padding after one
  sample in `107_home.vsf` holds `\DosDevices\R:` in UTF-16, from the PC that
  built the bank.

## How it was built

* **The demo came first, then Japan, then the USA.** Counting changed
  functions puts the January 2004 *Freaks* demo first, then the Japanese
  retail build (version 1.02), then the USA build (version 1.00). The USA
  build is the newest even though its version number is lower.
  ([xs2-analysis JP report](#sources))
* **The two releases are nearly the same program.** After relocations are
  masked, 96% of the Japanese main code is byte-identical to the USA's, built
  with the same compiler (ee-gcc 2.96), Sony SDK 2.7 and CRI middleware
  (`Build:Jan 23 2004`).
* **The USA cutscenes were re-muxed months later.** The Japanese movies
  carry Sofdec mux dates around February 13, 2004; the USA movies mostly
  September 15, 2004, a few as late as November 25.
* **The USA discs were mastered on December 10, 2004:** Disc 1's file system
  was written at 12:57 and Disc 2's at 13:45, about two months before the
  [February 15, 2005](https://en.wikipedia.org/wiki/List_of_Xenosaga_media)
  release (ISO 9660 volume descriptors).
* **Both discs run the same program.** `SLUS_208.92` (Disc 1) and
  `SLUS_211.33` (Disc 2) are byte-identical, and so are the three bigfile
  tables `XENOSAGA.00`-`.02`. The same goes for the two Japanese discs.
* **The USA build added a debug test menu** that the Japanese one lacks:
  17.7 KB of code whose menu lists FONT, PAD, HDD, MEMCARD, RESOURCE, IPU,
  SOUND, CAPTION, NISUI and EDGE (US `0x1e9740`-`0x1edcb0`).
* **Japan kept something the USA dropped:** a `yamamoto\think\` folder of
  116 battle-AI scripts, and the debug scenes `sc6301` and `sc7997`-`sc7999`.

## Words

* **Japanese test lines hide in the English script.** 127 of the 9,699
  English text-bank lines are Japanese placeholders: `11テストメッセージ表示中`
  ("11 test message, displaying", 63 copies) and `12の人です` ("this is person
  12", 64 copies).
* **There is a quest you cannot fail, and the failure text says so.** For
  "Send Him My Love!" the failure message reads: "You cannot fail this
  quest. A flag that cannot exist has come up. xxxxxxxxxx xxxxxxxxxx"
  (`iwase\QuestMes.txt`).
* **The longest string in the game is a recap of Episode I.** Chapter 7 of
  the in-game "Drama Series Ep. I" synopsis is 1,556 characters long, one of
  nine chapters shown in `cf\sc1610`.
* **The most-used line is "Yes | No"**, a choice that appears 295 times.
  Next come the two Japanese placeholders above, then "Target destroyed!"
  (56) and "Use the elevator?" (34).
* **"Okay! Tony! Hit it!" is written out 26 times** in one scene script
  (`sc2510`).
* **"Miltia" is the most-mentioned proper name** in the text, 260 times,
  ahead of Shion (166), MOMO (132) and KOS-MOS (97).
* **The Professor talks the most.** Among field text boxes that name their
  speaker, the Professor has 319 lines, almost three times as many as
  the next speaker, Matthews (109).
* **The USA game cannot print kanji.** The USA font renderer draws bytes from
  0x80 up as Latin-1 letters (through lead byte A9), so the Japanese
  placeholders above show up as accented garbage in the English game. The
  Japanese build reads the same bytes as EUC-JP.
* **The Japanese release keeps its text inside the scripts.** The USA discs
  move all dialogue into separate `.sbl` banks under `en\` folders. The
  Japanese discs and the demo have neither: each `.sb` carries its own EUC-JP
  text.
* **The quest and citizen text is Shift-JIS, the rest is not.** The
  `iwase\*.txt` files and the `#scn` notes use Shift-JIS; the movie
  subtitles use EUC-JP. Two Japanese encodings sit on the same English disc.

## Music and sound

* **The field music runs on a KORG driver that shipped with its symbols.**
  `IOP\KORGIVAG.IRX` ("KORG IOP Sound Driver … test19, (C)2001-2004 KORG
  Inc.") still has 239 named functions, 91 named statics and its full debug
  table. That is how this kit's renderer learned the driver's rules.
* **The field songs are real MIDI files.** No other Xenosaga does this: the
  31 field and menu songs are Standard MIDI files played on KORG sample banks
  (`.vsf` + `.prj`). Each song sets its own reverb from inside the MIDI and
  labels the controllers with text events (`FX1 MODE`, `FX1 DELAY`,
  `FX1 FEEDBACK`).
* **The songs still carry their working titles.** `001_milchia` calls itself
  `001_miltia`, `006_ormus` is `006_olmus`, and both `008_imaginary_territory`
  and `111_moby-dick` say `an_chloe` inside.
* **Second Miltia's theme is song 106, not 002**, and Subconscious Domain is
  109, not 003. Track numbers on the disc don't follow the order you might
  guess. ([MUSIC.md](MUSIC.md))
* **The crash cymbal rings because of one byte.** Byte +12 of a drum key is a
  "hold" flag: the driver skips the note-off and lets the one-shot sample play
  out. 554 music keys hold, including every drum kit. A SoundFont render cuts
  the 0.5 s crash to 0.14 s; the kit's own SPU2 renderer lets it ring.
* **Song 001's echo repeats every 537.7 ms**, from the SPU2's
  `(128 × (delay + 1) − 24) / 24000` rule. A game rip measures 538.
* **Kajiura's cutscene score is never on the disc by itself.** The music
  heard in the movies and real-time events exists only premixed with voice
  and effects. The clean music on the disc is Hosoe and Saso's gameplay
  score: 31 KORG sequences and 7 recorded ADX tracks (battle, boss, fanfare,
  title). ([music-sources.md](research/music-sources.md))
* **The voices are in the centre channel.** In the movies' AC3 5.1 track,
  the centre channel carries about 84% of the speech energy during spoken
  lines, so leaving it out lowers the dialogue by 5.6 to 8 dB.
* **The ADX stereo is its own mix, not a downmix** of the 5.1 track; the AC3
  runs 26 ms behind it.
* **54 voice clips are never called.** One-line clips named by speaker
  (`030920_Albedo_01` … `_Rubedo_09`, `_Negredo_0x`, `040400_margulis_0x`,
  `_pope_0x`, `040700_Juli_0x`, `_MOMO_0x`, `050106_shion_0x`) sit in
  `sound\adx\cfevent`, but no script and no string in the executable names
  them. ([DIALOGUE.md](DIALOGUE.md))
* **The real-time event "voice" tracks are full mixes.** The 88 cutscene
  ADX files hold ambience, music and voice together, and each starts on the
  scene's first `cfe.stKySnd` keyframe (all 78 voiced events check out).

## Movies

* **The boot screens credit Dolby, and they mean it.** `credit.bin` holds
  six boot screens: Namco, the Monolith Software tower ("M.L.S.I."), CRI's
  Sofdec and ADX, KORG, Dolby Digital Pro Logic II, and "Produced by NAMCO".
  CRI's Pro Logic II encoder (`PL2ENC Ver.1.00`) is linked in, and every
  cutscene carries an AC3 5.1 track next to its ADX stereo.
* **Movies are 512 × 448**, taller than Episode III's 512 × 320.
* **44 of the 50 movies have the same video in Japan and the USA**: only
  the audio and the subtitle files differ.
* **The title logo has two versions on the disc**, one reading "DISC 1" and
  one "DISC 2" (`yajima\title.bin`, `title2.bin`), with "© 2001 2004 NAMCO
  LTD."

## Models and motion

* **944 models**, and 562 of them have a skeleton that fits the mesh.
* **About one triangle in thirty is stored facing backwards**, so the kit
  orients faces by their normals and makes materials double-sided.
* **The texture files keep the artists' names:** `shion00_hair`,
  `kosmos00_face`. KOS-MOS's hair is in four different files, each with
  its palette somewhere else, which is why one paint job has to reach all
  four.
* **All 4,340 motion clips decode**, in 550 files, at 30 keys a second.
  Hair, coats and skirts are not animated in them: the game simulates that
  cloth at run time.
* **The skeleton files have no joint names.** Bones are numbers only.
* **The `_grass_col` textures look blank but aren't**: they are a single
  colour, with all of the detail in the alpha channel.
* **The citizen portraits hide their palette in a corner.** The 50 citizen
  portraits and 18 item cards keep their 16-colour palette in the top-right
  16 × 16 corner of the image, and a 1-bit mask file cuts the rounded frame.

## Under the hood

* **Java is gone.** Episode I ran its scenes as Java classes (JDK 1.1).
  Episode II replaced them with Monolith's own 16-bit stack machine (`.sb`),
  which has 46 opcodes. Two of them never occur on the disc, and four have
  byte-identical handlers: the compiler used them only to label which kind of
  literal it was pushing. ([SCRIPTS.md](SCRIPTS.md))
* **Scripts call about 270 named natives** such as `talk.msgDtl`,
  `runtime.mapChange`, `cfe.setMes` and `bat.namePut`, registered by class
  name from tables in the executable.
* **Most of the code is new.** Only about 7% of the game code has a close
  relative in Episode I and 12% in Episode III; 84% matches neither. The
  graphics library from Episode I (`xgl`) still sits intact, in Episode I's
  order.
* **The VU microcode looks ahead, not back.** The vector-unit programs share
  80% with Episode III and nothing with Episode I.
* **The dialogue loaders have fixed rooms.** A scene's text bank must fit in
  0x20000 bytes and a map's in 0x12000, which sets how far a translation can
  grow. ([MODDING.md](MODDING.md))
* **Movie subtitles can barely grow.** Lengthening a subtitle by 52
  characters corrupted the video data in PCSX2 ("Invalid Unpack", 2 FPS).
  Comments and moving the file are free, but the displayed text has almost no
  slack.

## Left on the disc

* **The game can install to a PS2 hard disk.** The title menu sheet has
  INSTALL and UNINSTALL next to NEW GAME, CONTINUE, EXIT and THEATER, and
  the code behind them is real: it formats 256 MB partitions, writes
  `pfs0:/xenosaga.hdd`, and saves into the North American hard disk's shared
  area.
* **It remembers Episode I's save folder** (`/BASLUS-20469Xeno`) next to its
  own (`/BASLUS-20892Xeno2`).
* **The code asks for eleven files the disc doesn't have**: one memory-card
  file per character or E.S. (`yajima\mc\kosmos.bin`, `shion.bin`,
  `r02.bin`…), plus an HDD icon.
* **The save icon is titled in full-width letters:**
  `ＸｅｎｏｓａｇａＥＰＩＩ−００［００：００］` (`carddata.bin`).
* **The menu sheet names are a fixed table** of 24-character slots in the
  executable (SLUS `0x3eae40`).

## Between Episode I and III

* **Episode III was mastered with Episode II's tool.** Every table of
  contents is padded with a repeated signature, `MONOLITHSOFT Xenosaga
  Episode.2`, and Episode III's `X3.00`, `X3.10` and `X3.20` carry the same
  words. One parser in this kit reads both games' tables.
* **The disc is a halfway house:** Episode I's table of contents, ARX
  compression and bitmap fonts, with Episode III's model, texture, skeleton,
  motion and script formats, stored loose rather than in packages.
* **Episode II added name compression** to Episode I's table of contents:
  each name stores only what differs from the one before it.
* **The KORG sound driver appears only in Episode II.** Episode I used
  Procyon sequences and Episode III uses DTPK banks.
* **Sony SDK versions climb one per game:** 2.4, 2.7, 3.0.
* **No symbols survive for the game code.** A byte scan of all five images
  (USA, Japan and the demo) found no symbol tables or map files outside the
  two IOP modules that kept theirs (KORG, and CRI's ADX with 23 source
  files). Episode I *Reloaded*, built in 2003 with the same SDK, is already
  stripped.

## By the numbers

| | |
|---|---|
| Objects on the two USA discs | 9,043 |
| Texture pages decoded | 65,938 (+1,333 inside effect files) |
| Models / with a skeleton | 944 / 562 |
| Motion clips | 4,340 in 550 files |
| Effects | 1,147 (all in `simajiri\`) |
| Dialogue strings | 9,699 in 465 banks |
| Lines in play order | 8,318 from 363 scripts |
| Menu strings | 903 |
| Battle names and descriptions | 1,085 |
| Strings in the executables | 3,498 + 678 + 47 |
| Field and menu songs | 31 |
| Named sound samples | 3,733 from 1,074 KORG banks |
| Cutscenes | 45 movies with subtitles, 80 timed real-time events |
| Event voice files | 145 (91 used by scripts, 54 never named) |

## Sources

Disc facts come from this kit run on the retail discs, and the matching doc
is linked where one exists. The Japanese-versus-USA comparison (build order,
mux dates, debug menu, `think\` scripts, shared movie video) and the
code-similarity and symbol-scan figures come from the author's analysis
workspace for this kit, which holds no game data and is not published; the
numbers can be rebuilt with `region.py` and a byte diff of the two
executables. Release dates: [HISTORY.md](HISTORY.md).
