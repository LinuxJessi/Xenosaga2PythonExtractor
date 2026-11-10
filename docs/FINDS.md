# Finds, leftovers, and what the disc says about the people who made it

Everything below is on the retail USA discs (SLUS-20892 / SLUS-21133) and
reproducible from a clean `dump` with this kit. Paths are in-game paths
(`dump/chain0/…`); executable strings are in `SLUS_208.92` unless noted.

## The disc is still organized partly by person

As in Episode I, several developers' working folders went straight onto
the disc, next to the newer asset-type folders (`model\`, `sound\`,
`motion\`, `cf\`). The engine addresses them by name: `yosikawa\MainMenu.txd`
and `matumoto\Item001.pxy` are string literals in the executable.

| Folder | Objects | What it holds |
|---|---|---|
| `simajiri\` (Shimajiri) | 1,147 | every effect: 1,022 `.esd`, 125 `.esp` |
| `yamamoto\` | 320 | battle: battle maps, motions, the HUD sheet, the battle database |
| `iwase\` | 80 | quest and citizen text, citizen portraits |
| `matumoto\` (Matsumoto) | 58 | field objects: items, treasure boxes, save points, breakables |
| `yosikawa\` (Yoshikawa) | 56 | the menus and the UMN screens |
| `yajima\` | 33 | title, ending, game-over and synopsis art |
| `omiya\` | 2 | two UI sheets |

The scripts name their authors too. Each `.sb` ends with its source path,
the writer's folder: `nakahara\` 209 scenes, `koji\` 55, `fuji\` 43,
`yonesaka\` 23, `kuramoto\` 12, `event\` 10; the 113 enemy scripts are
`think008`, `think032`, … And the sample subtitle file kept in the movie
folder ends its header with a sign-off: `;11/21米坂チェック`, "11/21,
Yonesaka's check" (`movie\en\s010100.txt`, Disc 1).

The executable still points at the developers' own machines:
`host0:/home/xeno/vcd-f/x2/sound/smf/%s.prj`, `host0:/home/xeno/work/iwase/jr00.pxy`,
`host0:/home/xeno/vcd/x2/cf/`, and `host0:/home/xeno/vcd-f/x2u/movie/sfd.lst`:
`x2` is the game, `x2u` its USA build.

## Japanese test lines in the English dialogue

127 of the 9,699 lines in the English text banks are Japanese: two
placeholders, repeated across 64 scene banks.

| Line | Copies | Means |
|---|---|---|
| `11テストメッセージ\n表示中` | 63 | "11 test message / displaying" |
| `12の人です` | 64 | "This is person 12" |

## A quest that cannot be failed

The quest text (`iwase\QuestMes.txt`) gives every quest a failure message.
For the letter quest "Send Him My Love!" it reads: "You cannot fail this
quest. A flag that cannot exist has come up. xxxxxxxxxx xxxxxxxxxx".

## The hard-disk install

The title menu sheet (`yajima\titleobj.xtx`, true colour) carries six
words: NEW GAME, CONTINUE, INSTALL, UNINSTALL, EXIT, THEATER. The install
is real code, inherited from Episode I's `xglHdd*` library: the boot
overlay `OV02.OVL` holds the installer's screens ("Start Install",
"Create Partition", "Create Sub Partition %1d", "Format Partition",
"Mount Partition", "Install Complete"); the executable formats partitions
of 256 MB (`%s,256M,PFS`), writes `pfs0:/xenosaga.00` and
`pfs0:/xenosaga.hdd`, and saves to the North American hard disk's shared
area (`hdd0:__common`, `pfs1:/Your Saves`). It also knows Episode I's save
folder name, `/BASLUS-20469Xeno`, beside its own `/BASLUS-20892Xeno2`.

## Files the executable asks for that the disc does not have

The executable names eleven memory-card files, one per character or E.S.
(`yajima\mc\clear.bin`, `chaos.bin`, `jin.bin`, `jr.bin`, `kosmos.bin`,
`momo.bin`, `r02.bin`, `r03.bin`, `r05.bin`, `shion.bin`, `ziggy.bin`),
an HDD icon `data\yajima\hddi.bin` and `hdd.res`. None of them is in the
USA disc's table of contents. The save icon the disc does carry is the one
in `carddata.bin`: an `icon.sys` titled
`ＸｅｎｏｓａｇａＥＰＩＩ−００［００：００］` and a 3D icon whose texture is the
game's logo.

## The boot screens, and Dolby

`credit.bin` is six boot screens: Namco, the Monolith Software tower with
"M.L.S.I.", CRI's Sofdec and ADX, KORG, Dolby Digital Pro Logic II, and
"Produced by NAMCO". The Dolby screen matches the code: CRI's Pro Logic II
encoder is linked in (`PL2ENC Ver.1.00 Build:Jan 23 2004`), and the
cutscenes carry an AC3 5.1 track beside the ADX stereo.

## The music, by KORG, with its symbols

The field music runs on "KORG IOP Sound Driver … version … test19 (C)2001-2004
KORG Inc." (`IOP\KORGIVAG.IRX`), which ships with its full symbol table:
`CcProcReverbDepth`, `InternalSetFxType`, `PitchCalc`, `VoiceOn`… Every song
sets the reverb/echo from inside its MIDI and labels the controllers with
text events (`FX1 MODE`, `FX1 VOL`, `FX1 DELAY`, `FX1 FEEDBACK`).
[MUSIC.md](MUSIC.md) has the details.

Some songs still carry their working titles in their track-name events:
`001_milchia` is `001_miltia`, `006_ormus` is `006_olmus`,
`008_imaginary_territory` is `300_an_chloe`, `111_moby-dick` is
`111_an_chloe`. In `107_home.vsf`, the padding after one sample holds a
Windows path from the authoring PC in UTF-16: `\DosDevices\R:`.

## The script machine has a few unused parts

Of its 46 opcodes, two never occur on the disc (02 and 05, which take an
operand and do nothing the scripts need), nor do the constants 2 and 6. Four
opcodes (03, 04, 06, 07) have byte-identical handlers: the compiler used
them to mark what kind of literal it was pushing. [SCRIPTS.md](SCRIPTS.md).

## Episode III was mastered with Episode II's tool

Every table of contents is padded with a repeated signature. Episode II's
says `MONOLITHSOFT Xenosaga Episode.2`, and so does Episode III's
(`X3.00`, `X3.10`, `X3.20`): the same table format, built by the same tool.

## The title logo

`yajima\title.bin` and `title2.bin` are the logo as a raw 512 × 448 image,
one reading "DISC 1", the other "DISC 2", with "© 2001 2004 NAMCO LTD.,
ALL RIGHTS RESERVED."
