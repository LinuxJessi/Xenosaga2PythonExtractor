# Translating into another alphabet: Arabic, Hindi, Georgian and the rest

The USA build of Episode II draws text one cell at a time, left to right,
from a bitmap font that holds Latin letters and a few symbols. Everything a
different script needs (letters the font never had, joining forms,
right-to-left order, conjuncts) is done by the kit at build time, and the
game only ever sees a stream of glyph codes. Three commands do it:
`lang-scan` draws the glyphs into the fonts, `lang-encode` writes the
translated text as codes, `lang-preview` shows a line without the game.

This is the Episode II counterpart of the Episode I kit's page of the same
purpose (its `docs/SCRIPTS.md`; in this kit `SCRIPTS.md` is about the script
machine). The two modules, `glyphs.py` and `langpack.py`, are borrowed from
the Episode I kit and say so at the top; the addressing, the tiers and the
budgets below are Episode II's own.

**USA build only** (SLUS_208.92 / SLUS_211.33, one executable, sha256
1f978e64c837f9b5...). The Japanese discs read every byte from 0xA0 up as the
lead of a two-byte code, can print kanji and have 192 proportional cells: a
different glyph space, not worked out here. `lang-scan` refuses any other
build and names the serial it saw.

## What is proven and what is not

Proven on disc copies (2026-11-20, the checks at the end of this page): the
fonts the kit writes read back to the byte and change only the cells it
allocated; every code in an imported string decodes, through a port of the
game's own reading, to the cell the encoder meant; `text-import` accepts
the result, including its subtitle checks; Disc 1 and Disc 2 take the same
fonts and chain 0 text.

**Not seen in the running game yet:** lone high bytes in any surface (the
visible retail English never uses them, apart from the `A1 FC` marker at the
end of 190 tutorial lines), A4 pairs from A4A6 up, AD cells, and every
surface's handling of them (field window, menus, battle, movie subtitles,
cfe cutscenes, the save screen). Everything about how the engine draws is
read from the executable. The LANGTEST disc (a sibling work item) is meant to
exercise each tier in each surface; until it has, treat this page as a plan
backed by code reading.

## How the game draws text (read from SLUS_208.92)

* **One print path.** Every visible string goes through the print function
  `FUN_00197bb8` and the width function `FUN_00195938` (`font.Measurer`).
  With `$han1;` ASCII 0x20-0x7F is drawn from font cells through a table in
  the executable; bytes from 0x80 up always are.
* **Which high bytes pair.** `FUN_00194dd0` (`font.us_two_byte`) reads a byte
  as the lead of a two-byte code only for lead A4, A6 or AD (any non-zero
  trail), A1 with BD DA DB DF F9 FA FB, and A2 with A2 A3 A4 F6. Every other
  high byte is ONE character, cell = byte + 0x80.
* **Proportional drawing.** With `$prop1;` a cell below 384 is drawn from its
  proportional image at cell + 576, columns left - 1 to right + 1 of its
  metric pair (`font + 0x78040 + 2 * cell`), and the pen moves
  right - left + 3. A left of 0 means the advance is `$xx1` + 1. Cells from
  384 up are drawn whole and advance 19. Every UI format string the code
  builds sets `$han1;$prop1;` (the menus at 0x6883F0..0x68B810, the subtitle
  state at 0x6883F0, the cfe formatter `FUN_0024b450` at 0x68CD88, the save
  screen `FUN_00237830`, battle `$han1;%s` at 0x68D570).
* **Two faces, one buffer** (new, 2026-11-20). The engine has one font buffer
  (0x8DD280). `FUN_00198ff0` loads `font1.us` at init, so font1 draws field
  dialogue, menus and battle. `font0.us` is swapped in while a movie plays
  (`FUN_001e7d08`) and between cfe.init (`FUN_0024c308`) and cfe.destroy
  (`FUN_0024e738`), the real-time cutscenes. Measured: sheet 0 (cells
  0-1023) and all 384 metric pairs are byte-identical in the two retail
  files. `lang-scan` writes every glyph into both and checks they still agree.
* **Wrapping.** The field message window wraps by itself: `FUN_002865f0` asks
  `FUN_00196118` how many BYTES fit the window width and breaks at spaces;
  3,853 of the 3,886 retail `msgDtl` strings are one line and rely on it.
  Movie subtitles (packer `FUN_001e7a08`) and cfe lines (`FUN_0024b450`) are
  not wrapped; each line is centred. The speaker tag `[...]` is cut off
  bytewise at the start (`FUN_00282380`, `FUN_00282338`). The two characters
  `\n` and a real 0x0A both break a line (`FUN_002870b0`).

## The room: three tiers of codes

Glyph sheet 0 has 20x24 cells with four grey levels. The kit computes the
free codes from the executable's reading (`glyphs.Tiers`); these counts were
measured on Disc 1, 2026-11-20:

| Tier | Code | Cell | Drawn | Free |
|---|---|---|---|---|
| single | one byte 80-FF except the pair leads A1 A2 A4 A6 AD, and FC (retail) | byte + 0x80 (256-383) | proportional | 122 |
| pair | A4 A6 .. A4 E0 | trail + 0x1F (197-255) | proportional | 59 |
| fixed | AD 80 .. AD C0 | trail + 0x17F (511-575) | whole 20 px cell, advance 19 | 65 |

246 codes in all, 181 of them proportional. For comparison, Episode I had
161 proportional slots and 2,992 kanji cells; Episode II USA has no kanji
overflow (its print function cannot reach the kanji sheet). Two LANGTEST
scripts with many glyphs fit (Arabic and Hindi together took 85 cells in the
check below); a whole Devanagari translation with many conjuncts may not, and
`lang-scan` says so and writes nothing.

What the cells hold today: 6 single-byte cells (bytes 81 8D 8F 90 9D A0) are a
placeholder dot, the other 116 hold cp1252/Latin-1 glyphs that the visible
retail English does not use; the pair cells and their images are placeholder
or blank; the fixed cells hold two blanks, the tail of the box-drawing row,
circled numbers and Roman numerals. The allocator hands out placeholder and
blank cells first. `--keep 'éèàç'` keeps chosen Latin-1 letters.

A4 trails E1-FF reach the same cells as the single bytes 80-9E; the kit
allocates cells and writes each with its cheapest code (the single byte).
Left out by design, not tried: AD trails above C0 (they land on cells 576 and
up, the proportional images), and A4/AD trails below 0x80 (they would put
ASCII-range bytes such as `[` `]` `$` `\` `%` `&` into strings that bytewise
parsers scan).

**Reserved, never allocated** (`lang-scan` prints the list with reasons): the
cells of the ASCII table, the font's JIS rows 1-3 (retail symbols, digits,
letters), A4A1-A4A5 (retail `é` and `®` among them), the A6 Greek pairs, the
fallback cell 0x7F, and every cell the visible retail text of the `--iso`
disc reaches. "Visible" means every string of the banks, menus and battle
database and every subtitle cue line, skipping Shift-JIS developer lines
(`textpack._sjis_hint`) and strings that decode as EUC-JP with kana or kanji
(the five retail symbols A4A1-A4A5 aside). On the USA discs that reserves the
lone byte FC only (the `A1 FC` marker, e.g. `model\map\en\E2_KML03.sbl`).
`--reclaim FC` frees it and names the retail strings that would change look.

**Which glyph gets which tier.** Glyphs that appear in size-limited text
(battle names, executable strings, subtitle lines) take single-byte codes
first. A glyph that would be the very first byte of a battle section's first
string may only take a single byte A1-FE or a pair, because the kit finds a
text section of `batdat.us` by that byte. Everything else follows `--prefer`
(single, pair or fixed first). Ink wider than 18 px can only go to the fixed
tier; `--prop-only` refuses such glyphs instead.

## Order of operations

```sh
# 1. export, then translate the tree (logical-order UTF-8, as you type it)
python cli.py text-export --iso "Disc 1.iso" --out tr/

# 2. one scan per script; later scans extend the first
python cli.py lang-scan --iso "Disc 1.iso" --text tr/ --font NotoNaskhArabic.ttf \
    --script Arab --language ar --rtl --prefer pair --out fonts-ar/
python cli.py lang-scan --iso "Disc 1.iso" --text tr/ --font Kohinoor.ttc --face 0 \
    --script Deva --language hi --extend fonts-ar/glyphset.json \
    --font0 fonts-ar/font0.us --font1 fonts-ar/font1.us --out fonts/

# 3. encode
python cli.py lang-encode --text tr/ --glyphset fonts/glyphset.json --out tr-enc/ --width-kind bank=360

# 4. the fonts into a copy of each disc (chain 0 is on both), then the text
python cli.py patch --iso "Disc 1.iso" --out fonts1.iso \
    --set 'chain0:font0.us=fonts/font0.us' --set 'chain0:font1.us=fonts/font1.us'
python cli.py text-import --iso fonts1.iso --text tr-enc/ --out final1.iso
python cli.py patch --iso "Disc 2.iso" --out fonts2.iso \
    --set 'chain0:font0.us=fonts/font0.us' --set 'chain0:font1.us=fonts/font1.us'
python cli.py text-import --iso fonts2.iso --text tr-enc/ --out final2.iso

# 5. executable strings last (exe-import, a sibling work item)
```

`text-import` goes onto the copy with the new fonts because its subtitle
check measures lines with the disc's own font metrics. Executable strings go
last: a patched executable no longer matches the sha256 that text-import,
font-extract and lang-scan check, so those commands refuse that disc.

`lang-encode --iso fonts1.iso` runs `text-import`'s checks on the encoded
tree as a dry run.

## lang-scan

`lang-scan` shapes every string of the tree with HarfBuzz (through
`glyphs.TTFRenderer`), collects the glyphs the translation uses (joined
forms, ligatures, conjuncts, marks drawn together with their base when the
cluster fits 18 px), gives each a cell and draws it from the TrueType font
into both faces. It writes:

* `font0.us`, `font1.us`: the rebuilt fonts, verified (every code read back
  through the game's reading, both cells inked, the width function giving
  the advance the encoder uses) and identical to each other in sheet 0;
* `glyphset.json`: which glyph has which code; keep it with the project so
  later runs keep the same codes;
* `langpack.json`: the shaping settings, one entry per script;
* `sheet0_preview.png`: sheet 0 with this run's new cells outlined.

It prints the tier use, the glyph count per script, words whose characters
the font lacks (drawn as its `.notdef`), every changed battle and executable
string with its encoded size against its `max N bytes`, every changed
subtitle line in bytes and pixels against what the disc's retail lines
reach, and the widest line of each kind.

Glyph size: `--px 18 --baseline 19` put a Latin capital on rows 6-18 and the
baseline under row 18, where the retail capitals are (measured on
`font1.us`: `A` inks rows 6-18, `x` rows 11-18, descenders reach row 22).
Devanagari vowel signs below the line touch the bottom of the cell at these
settings (the scan warns about glyphs on the cell edge); `--px 16
--baseline 17` gives them room. Look at the preview sheet.

Spacing: a proportional glyph's ink goes to columns left..right of its
pair, so there are two blank columns between glyphs, as between retail
letters. Joining scripts (Arabic) then show a small gap inside words.
`--tight` puts the ink at left - 1..right + 1 instead, so joined letters
touch; that relies on the draw range read from the code (left - 1 to
right + 1) and has not been seen in the game.

`--script` names the ISO 15924 script whose words this run collects (Arab,
Hebr, Syrc, Thaa, Deva, Beng, Guru, Gujr, Taml, Telu, Knda, Mlym, Sinh,
Thai, Ethi, Geor, Armn, Cyrl, Grek); without it the run takes every
character the USA font cannot draw as it is. Greek is drawn from the retail
A6 cells (fixed width) unless a run claims `Grek`.

Hand-drawn cells instead of a TrueType font: `--cells sheet.png --keys
keys.txt`, one 20x24 cell per key in reading order, a key per line
(`u:ب`, or just the character). Arabic is then shaped from Unicode's
presentation forms.

## lang-encode

`lang-encode` reads the translated tree and writes the same tree with every
glyph as `⟦XX⟧` escapes (single byte `⟦E9⟧`, pairs `⟦A4⟧⟦C3⟧`). A string with
no character of a scanned script is copied as it is, so an untouched tree
encodes to itself byte for byte.

* **Order.** Right-to-left paragraphs are put in visual order by the Unicode
  rule for runs: words reversed, Latin and digit runs (`KOS-MOS`, `3.5`) kept
  forward, brackets mirrored, an all-Latin paragraph left alone. `--ltr`
  never reorders.
* **Codes.** `$...;` codes have no width. On a reordered line, the codes that
  set a lasting state (`$col`, `$zoom`, `$shadow`, `$bg`, `$alpha`, `$xxN`) are
  written again in front of each word that needs them, so each word is
  drawn in the state of its logical position, and the logical end state is
  restored after the line. A word before the first such code in logical
  order would need the state from before the string, which the kit cannot
  know; the encoder warns and the code carries onto it. Other codes in front
  of the first word stay at the front. A leading `[Speaker]` stays first.
  `%d`, `%s` and the other `%` tokens pass through; `&NN` icons count 19 px
  (an assumption).
* **Wrapping** at spaces to `--width` or `--width-kind KIND=PX` (kinds bank,
  menu, subtitle, battle, exe), measured with the port of the game's width
  function on the rebuilt font. Subtitles wrap at 440 px unless told
  otherwise (the widest retail line is 454 px on Disc 1, 445 on Disc 2);
  other kinds do not wrap unless told. The field window re-wraps a line
  that is too wide, from the left, which scrambles a right-to-left line, so
  bank lines in a right-to-left script need `--width-kind bank=PX` below the
  window width. **That width has not been read**; the encoder warns when an
  RTL bank paragraph is encoded without a width. The LANGTEST measures it.
* **Space.** The space is A1A1, cell 0, pair (0, 19): it advances `$xx1` + 1,
  9 px in the subtitle and cfe states (`$xx1,8`). The field window's `$xx1`
  has not been read; `--space-px 9` is assumed. `--align right` pads RTL
  lines of banks and menus with spaces; subtitles and cfe lines are centred
  by the engine and never padded.

Per kind:

| Tree file | What changes | Limit checked by lang-encode |
|---|---|---|
| `cf\en\*.sbl.txt`, `model\map\en\*.sbl.txt` (banks) | block bodies | text-import checks the loader room (at least 1,664 bytes) |
| `yosikawa\*.txd.txt` (menus) | block bodies | text-import: the file may not need more sectors |
| `movie\en\*.sub.txt` (subtitles) | only the text after `time<TAB>` on cue lines; the frame-0 cue and `;` lines untouched | text-import: the disc's retail line bytes (51 Disc 1, 49 Disc 2) and pixels (454 / 445), the cue list, the 64-byte packing rule (s010100 has 2 bytes of room) |
| `yamamoto\batdat.us.txt` (battle) | block bodies | `max N bytes` per string; a section's first string starts with a byte 20-7E or A1-FE |
| `*.exe.txt` (executable strings, sibling work item) | block bodies, `⟦0A⟧` breaks | `max N bytes` per string |
| `iwase\*.sjis.txt` (quest) | nothing: Shift-JIS, no new glyphs | refused if it holds a character Shift-JIS cannot write |

If any glyph has no slot, any character belongs to no scanned script and is
not in the USA font, or any string breaks its byte limit, `lang-encode`
writes nothing and exits 1 with the list.

Cfe event lines are bank strings drawn with font0 and centred; movie
subtitles are drawn by the engine with font0 like any other text, so no burn
tool is needed (Episode I burned them into the video).

## lang-preview

```sh
python cli.py lang-preview --glyphset fonts/glyphset.json --text "[Canaan]ما هذا الشيء؟" --out line.png
python cli.py lang-preview --glyphset fonts/glyphset.json --kind subtitle --face font0 \
    --text "आज सुबह भोर से पहले\nभड़की हिंसा की" --out sub.png
```

The line is encoded exactly as `lang-encode` would and drawn from the
rebuilt font the way the print function places cells: ASCII through the
executable's table, proportional cells from cell + 576 with their pair's
advance, fixed cells whole with advance 19, codes skipped. Several lines
give `NAME_0.png`, `NAME_1.png`, ...

## Checked on disc copies, 2026-11-20

* `cli.py self-test` passes, including the GUI and the two new modules.
* Disc 1 exported; `lang-scan` on the untouched tree finds 0 glyphs and
  writes fonts byte-identical to the retail ones; `lang-encode` of the
  untouched tree gives a byte-identical tree (`diff -r`).
* A tree with `cf\en\sc1010.sbl` blocks 0004-0006 in Arabic and the
  00:14:15, 00:16:20 and 00:19:14 cues of `movie\en\s010100.txt` in Hindi:
  SF Arabic `--prefer pair` (45 glyphs: 44 pair, 1 fixed), then Kohinoor
  face 0 with `--extend` (40 glyphs: 37 single, 3 fixed; all in size-limited
  subtitle text), then `lang-encode --width-kind bank=360` (widest bank line
  346 px, widest subtitle line 329 px).
* The fonts patched into a Disc 1 copy and the encoded tree imported with
  `text-import`, subtitle checks passed, no `--rebase`. Re-exported, every
  changed string decoded through `Addressing.glyph` gives exactly the
  encoder's 127 glyph cells in order.
* The imported fonts differ from retail only in the allocated cells, their
  +576 images and their pairs (166 cells, 81 pairs per face), round-trip
  through `Font`, and font0 = font1 in sheet 0 and the pairs.
* The same fonts and chain 0 text imported into a Disc 2 copy.
* `lang-preview` of each changed line read correctly (Arabic right to left,
  joined forms with the two-column gap; Devanagari conjuncts and vowel signs
  in place).

Not yet: any of it in PCSX2.
