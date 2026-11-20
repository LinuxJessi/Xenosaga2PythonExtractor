# Survey: the small and one-off files (2026-11-05)

What each of the leftover file types is, how far it is decoded, and the
evidence. Decoders are in `smallfiles.py` (`python smallfiles.py FILE
--out DIR`); kinds in `sniff.py`. Counts are from the USA discs.

## The offset-table pack (5 files)

`credit.bin`, `carddata.bin`, `yajima\im.bin`, `yajima\synopsis.bin`,
`yajima\titlebg2.bin`.

```
+0  u32 table_bytes            (4 x member count: 24, 8, 16, 8, 760)
+4  u32 end[table_bytes / 4]   end of member i, counted from the first member
    zero padding to the next 64-byte boundary: the first member starts there
    each next member starts at the next 64-byte boundary after the previous end
    the file ends at the boundary after the last member
```

Evidence: the JPEG start markers in `credit.bin` sit exactly at 0x40,
0x1780, 0x3540, … (each previous end rounded up to 64); `build_pack`
rebuilds all five files byte-identically from their members.

| File | Members |
|---|---|
| `credit.bin` | 6 JPEG, 512 × 448: the boot screens (Namco; Monolith Software, "M.L.S.I."; CRI Sofdec + ADX; KORG; Dolby Digital Pro Logic II; "Produced by NAMCO") |
| `yajima\im.bin` | 4 JPEG, 512 × 448: character key art (Jr. and Gaignun; an E.S.; Jin; Shion and KOS-MOS) |
| `yajima\synopsis.bin` | 2 XTX; `xtx.py` finds no palette for them (grey only): open |
| `carddata.bin` | `icon.sys` + a PS2 3D icon (below) |
| `yajima\titlebg2.bin` | 190 members of the title scene: 12 `pxy`, 12 `txy`, 11 `XHR `, 71 `XAC `, 71 `FAC `, 13 `XCM ` (the first `pxy` is named `E2_EVE02`) |

`XAC ` (motion curves), `FAC ` (face motion) and `XCM ` (camera motion)
appear here as loose members; `XAC ` / `FAC ` are the clip format of the
`.xap` / `.fap` files, decoded in [../MOTION.md](../MOTION.md) (`xap.py`).
Their names follow the source files in the executable
(`TiXacAnimCurve.euc.c`). The members here are not yet bound to their
skeletons, and `XCM ` is not decoded.

## The title logo: `yajima\title.bin`, `title2.bin`

ARX-compressed; decompressed, exactly 917,504 bytes = 512 × 448 × 4: a raw
GS RGBA image, alpha 0-0x80 (doubled for PNG). The logo with "DISC 1" /
"DISC 2" (the two differ only in a 6 × 9 pixel block, rows 158-166,
columns 436-441) and "© 2001 2004 NAMCO LTD., ALL RIGHTS RESERVED.".

## The save icon (inside `carddata.bin`)

* `icon.sys` (964 bytes, "PS2D"): the save title at +0xC0,
  `ＸｅｎｏｓａｇａＥＰＩＩ−００［００：００］` in Shift-JIS (slot and time filled in
  when saving).
* the icon (7,330 bytes): the standard PS2 3D icon layout, magic
  0x00010000, 1 shape, texture type 0x0E, 36 vertices of
  `shapes x (4 x s16) + normal (4 x s16) + uv (2 x s16) + rgba`, an
  animation header `{1, 1, 1.0, 0, 1 frame}`, one frame `{shape 0, 1 key,
  (0.0, 1.0)}`, then a u32 size (6,406) and the RLE texture: u16 c, if
  bit 15 copy `0x10000 - c` pixels, else repeat the next pixel `c` times.
  It expands to exactly 32,768 bytes, 128 × 128 RGB555: the game's logo.

The executable also asks for `yajima\mc\*.bin` (11 per-character files),
`data\yajima\hddi.bin` and `hdd.res`; none is in the USA table of
contents.

## The battle database: `yamamoto\batdat.us`

```
+0 u32 count (19)   +4 u32 file size   +8 u32 offset[count]   then the sections
```

A section with text is a run of fixed-size records followed by a string
pool; a record's u16 fields hold offsets, from the section start, of its
name and description; the pool begins at the first record's first string.
Section 18 is a plain list of u16 offsets.

| Section | Strings | What |
|---|---|---|
| 0 | 15 | party names (chaos, KOS-MOS, Shion, …) |
| 1 | 125 | enemy names (Ai Apaec, Deion, Aiakos, …) |
| 6 | 197 | Ether skills and descriptions (Medica: "One ally (EP 2)\nLow HP recovery relative to Ether Attack.") |
| 7 | 167 | skills (HP Mind 10: "HP Recovery +10%.") |
| 8 | 80 | equipment (Auxiliary Armor A) |
| 9 | 80 | items (Med Kit S) |
| 10 | 62 | secret keys (opening Class H skills) |
| 11 | 214 | decoders (Segment Address doors) |
| 12 | 32 | special attacks (Ice Brand, Burst Veil) |
| 13 | 42 | E.S. attacks (Spiral Fist, Tornado Fist) |
| 14 | 14 | E.S. weapons (MINIGUN, MICRO MISSILE, DRAGON BLADE) |
| 17 | 3 | single letters V, S, T |
| 18 | 54 | battle messages ("Acquired ", "Stole ", "Couldn't steal anything!", status names) |

Sections 2-5, 15 and 16 are numbers only. Record sizes differ per section
(12 bytes in section 6, 16 in sections 9 and 12); the number fields are
not named yet. For a translation this file matters: the battle and menu
names of every skill and item are here, not in the `.sbl` banks.

**Writing (2026-11-10).** `smallfiles.batdat_replace` puts new strings back
in place, same length or shorter, NUL-filled; `text-export` / `text-import`
use it. A writer that moves strings was not built: the record shapes are
mixed (section 6 has 109 twelve-byte records for 197 strings, the later ones
with one string each; section 17's "pool" holds 112 byte runs of which the
records name 3), so which u16 fields are offsets is known only from their
values, and a number that happens to equal an offset would be rewritten with
them. The Japanese file is `yamamoto\batdat.bin` (75,992 bytes, the same 19
sections and 1,085 strings, EUC-JP); the demo's has 1,084.

## Map data: `model\map\*.id` (214)

```
+0 u8 version (3)   +1 u8 section count (2-7)   +4 u32 offset[count]
section: u8 0, u8 type (1, 2, 3, 6, 7, …), u8 count, …, then offsets into the section
```

Word 0 is 0x0603 in 111 files, 0x0503 in 76, 0x0703 in 14, 0x0403 in 12,
0x0203 in 1. Each map has one beside its `.pxy`/`.txy`. Section contents
are not decoded.

## `.epf` (249, beside the character, enemy and E.S. models)

```
+0 "EPF "  +4 float version (1.1; 1.0 in 5 files)  +8 u32 count  +0xC u32 0x10
then count x 0x20: u32, u32, float x, y, z, float a, b, c
```

`urtvf00.epf` has 2 records, `shion00.epf` 14. The last three floats are
often ±π/2 (radians), so the records look like points with an orientation
on the model; which joint and what for is open.

## `.ene` (4: `cf\enemy_001.ene`, `enemy001.ene`, `enemy010.ene`, `npc_001.ene`)

3,120 bytes = 65 records of 48 bytes: eight floats (2.0, 1.0, 2.0, 10.0,
0.04, 0.08, 60.0, 5.0 …) and u16 pairs (10, 50). Parameter tables for
field enemies and NPCs (the names say so); fields not named.

## `.dev` (3, Disc 2 `motion\cfevent\050105.dev`, `090305.dev`, `090900.dev`)

`"DEV "`, float 0.5, u32 0, u32 0x181, then offsets 0x190, 0x1F4, 0x258, …
(0x64 apart) to records of floats. Beside the cutscene motions of the same scene numbers; not decoded.

## `.fap` (1: `motion\face_all.fap`)

`"FAP "`, float 1.2, u32 count (19), u32 offsets: 19 `"FAC "` clips
(version float 1.4), the characters' face animations, each keying the six
run-D joints. Decoded: [../MOTION.md](../MOTION.md); `model --face` adds them.

## `.jrd` (2)

Plain text, a C header listing a model's joints in order
(`// definition of row of joint. this version is 0.2`, `#define mobj139_top 0`).
`model\robo\pc_robo\pcr05.jrd` and `model\mobj\mobj139.jrd`.
