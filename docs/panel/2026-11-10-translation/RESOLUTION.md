# Review round: the text and font work, before it was merged (2026-11-10)

Seven models were sent the change set (regions, fonts, `text-import --grow`, the new text
kinds) and its claims. Gemini, GPT, GLM and Grok answered; their answers are beside this
file, unedited, with the first lines of the brief. Each point was checked against the code,
the executables or the discs (USA Disc 1 and 2, Japan Disc 1 and 2, the demo). Nothing here
was seen in the running game.

"Real disc" below means a run on the images; "synthetic" a self-test on an image built in
code (`minidisc.py`); "code" a reading of the game's disassembly.

## Confirmed and fixed

| Finding (who) | What was done, and the evidence |
|---|---|
| A body line starting with `#` is dropped on import; a body starting `### ` becomes a header (Gemini a2, GPT 5) | The tree format now says so and has an escape: a line starting with `#` inside a block is a comment, a string that starts with `#` is written `⟦23⟧...` by the export. Real disc: 0 retail strings start with `#` on any of the five discs (USA 9,699 bank + 903 menu + 1,085 battle; Japan 17,225 inline + 902 + 1,085; demo 3,999 + 823 + 1,084), and 0 hold a raw line break, so nothing was being lost; the fix is for typed text. Mutation, real disc: a USA bank string and a Japanese inline string were changed to start with `#1` / `#23`; the images hold `#1 [Canaan]Qu\xe9 ...` and `#23 ce`, and the re-export shows `⟦23⟧1 ...` / `⟦23⟧23 ce`. Synthetic: strings `#1 ...` and `### 0001` round-trip and are edited. Every kind now re-checks at export that its text rebuilds to the same bytes, inline included. |
| Trailing empty lines of a block are stripped (Gemini a3) | Kept, and documented: a line break in a string is the game's `\n` or `⟦0A⟧`, never a real line end, so no exported block ends in an empty line (0 of every count above). The claimed loss does not occur: `to_text` writes a raw 0x0A as `⟦0A⟧`. |
| `text-import`, `patch`, `recolor` patch the input when `--out` resolves to it (GPT 2) | `repack.patch_copy`: refuses an output that is the input by name, symlink or hard link; works on `<out>.partial`; `os.replace` only after every read-back; removes the partial file on any error. Synthetic: all three aliases refused, a failure injected after the copy leaves an existing output byte-identical and no partial file. Real disc: `--out` a symlink to USA Disc 1 and to Japan Disc 1 refused; the real import produced the image with no partial file left. `recolor --xtx` without `--out` used to patch its input: now refused. `font-rebuild --out` equal to `--ref` refused. |
| `.txd` rebuild ignores block IDs (GPT 4); duplicate and missing inline/battle blocks (GPT 7) | One parser for every block file: the keys must be exactly the export's, once each, in order. Synthetic: swapped, duplicated, missing and unknown keys refused for banks, menus, inline scripts and battle strings. Real disc: a duplicated key refused in a USA bank and in `Location.txd`. Quest and subtitle files have no blocks; their structure is checked instead (quest: everything outside `"..."` must equal the original; subtitle: the same cues at the same frames). |
| Inline replacement accepts an embedded NUL; emptying a table-only string hides it (GPT 6) | NUL refused in every block kind. After an inline edit the script is parsed again and must list the same strings. Real disc (Japan 1): emptying the table-only string at `cf\sc1610.sb` 0x2e3d is refused ("would make 1 string(s) disappear"). |
| Battle edit can change what the next export finds (GPT 8, Gemini a6) | After `batdat_replace` the file is scanned again and must give exactly the expected strings. Real disc (USA 1): changing the first byte of section 0's first string to 0x01 is refused ("15 would disappear"). |
| Imports are not bound to their disc (GPT 9) | `xs2-text-export.json`: build, serials, sha1 and size of every exported object. Refused without `--rebase`: no manifest, another build, an object whose sha1 differs. Real disc: the edited USA Disc 1 tree onto its own patched image: 5 of 5 objects refused, with `--rebase` 0 changes (the text is already there); onto Japan Disc 1: refused; onto USA Disc 2: the 4 chain-0 objects accepted (same build, same sha1), which is the intended way to patch both discs. |
| Unknown serial gets a known build's profile (GPT 10, GLM 8) | `region_of` returns a profile named `unknown` that carries the serial only. `require_known` guards `patch`, `recolor`, `text-import`; `require_executable` (sha256 of the executable) guards the font tables and text import. `bank_budget` takes the build: only USA has loader rules, and only in its `en\` folder. Synthetic: serial SLES_999.99 lists and exports, every writer refuses naming it; a known serial with one byte added to the executable is refused. |
| ELF validation (GPT 11) | Class, byte order, program-header table and every PT_LOAD's file range are checked; `read_va` raises on a short read. Synthetic: 64-bit, big-endian, cut-off headers, cut-off segment, oversized `p_filesz`, a read across a segment end. |
| `font-rebuild` succeeds with no sheet (Gemini a1, GPT 13) | Refuses when no sheet matched and no metrics were given (`--allow-unchanged` asks for it), prints which sheets and how many metric rows were applied and changed, refuses a metric slot given twice. PNG: alpha and `tRNS` count as background, chunk lengths and CRCs are checked. Synthetic. The five-disc byte-identical rebuild still holds (it applies four sheets). |
| "In its slot" is not "what the loader read" (GPT 3) | Menus and quest files may not need more sectors than the original, whatever the slot. Real disc: on all five discs every text object's slot equals its sector-rounded size (USA Disc 1: 495 of 495), so nothing changes there. Synthetic: a menu file with a spare sector in its slot is refused at 2 sectors. Code: quest files are allocated by TOC size (`FUN_002320b0` -> `FUN_0019bc68` -> `FUN_0020b738`), menus go to static buffers; neither was relaxed. |
| USA text is not EUC-JP; typed characters are not checked against the font (GPT 12, Gemini b, GLM 2, Grok 3; raised by the author too) | `textcodec.py`. USA: a lone byte 0xA0-0xFF is its Latin-1 character, the pairs `FUN_00194dd0` accepts are symbols or escapes. Japan: EUC-JP. Typed non-ASCII characters of changed strings are checked with `font.Addressing` and the disc's two fonts: not encodable, 0x8E / 0x8F prefixes, bytes the game would pair differently, fallback cell 0x7F, unreachable cell, blank glyph. Real disc: all 495 / 500 USA and 622 / 627 Japanese and 153 demo objects still export and re-import as no change. Typed and imported on USA Disc 1: `Qué es esto, señor? Été ×2` -> bytes `Qu E9 ... se F1 or ... C9 t E9 A1DF 2`. Refused on real discs: `あ` and `Ā` on USA; `～` (A2B2), a level-2 kanji not in the executable's list (D0A2), half-width kana and a Hangul syllable on Japan. The retail text passes its own check: 0 of 587 non-ASCII USA strings, 0 of 15,536 Japanese, 1 of 5,055 demo strings would be refused if retyped. |
| Subtitle policy: the 64-byte rule alone is not conservative (Gemini d, GPT 18, GLM d, Grok 4) | Kept, and three bounds added from the retail files of the disc being patched: line length in bytes, line width in pixels (a port of the game's width function `FUN_00195938`, `font.Measurer`), lines per cue; and the cue list (count, frames) may not change. USA Disc 1: 51 bytes, 454 px, 2 lines; USA 2: 49, 445, 2; Japan 1: 75, 451, 1; Japan 2: 65, 425, 2; demo: 50, 451, 1. The errors and MODDING.md say these are what the retail game is known to display, not proven limits. Real disc: a 79-byte line, a 3-line cue, a 540-pixel line of 30 bytes and a removed cue are each refused on USA Disc 1; a longer line on Japan Disc 1. |
| Backslash handling in the subtitle packer (Gemini a4, GPT 19) | Code: at 0x1e79b4-0x1e79cc the game stores 0x0A and skips one byte whatever it is; 0x6E does not occur in the function. The port already did the same. Real disc: all 879 backslashes in the 93 retail subtitle files are followed by `n`. An edited file with `\` before anything else is now refused. |
| First-cue overwrite check (Gemini a5) | The check now uses the exact end of what the first pass wrote, not a scan of the buffer. Synthetic: a two-line first cue with 10 bytes above it is refused, with 21 accepted. The example in the finding (8 bytes above a 1-character cue) is accepted correctly: the first pass writes 4 bytes. |
| Frame number truncated to 16 bits; packed x-offset bytes are bogus (GLM 5, GPT 19) | A cue at frame 65535 or later is refused in an edited file. With widths known (imports), the x-offset byte is the real one and a line whose byte would be 0 (510-511 px) is refused. |
| A byte-order mark breaks the first header of an inline file (GLM 6) | Every tree file is read as `utf-8-sig`. Synthetic. |
| A relocated object should warn every time (GLM e) | `text-import` prints a WARNING line per relocated object saying it has not been seen working in the game. |

## Checked and found harmless or wrong

- **"euc_jis_2004 decodes A1C1 / A1C2 differently from euc_jp" (GLM 1).** It does not in
  Python: both give U+301C and U+2016. The earlier table stands: 6,879 two-byte codes
  defined in both, 0 decode differently.
- **"Latin-1 cell = byte + 0x180" (GLM 4).** Misread: the trail is `byte - 0x80`
  (`(byte + 0x80) & 0xFF`), so `xs2_cell(0xA9, trail) = byte + 0x80`. 0xE9 gives cell 361;
  its proportional image, cell 937, is `é`, and 0xC9's is `É` (looked at). The whole
  0xA0-0xFF block has ink; `¡ ¿ ß ç ñ Ø æ ©` are right by eye and the accented capitals
  A share the letter `A`'s shape exactly.
- **"xs2_cell(0xA2, 0xB2) returns non-fallback" (Grok 3).** It returns 0x7F, the fallback.
  The concern behind it (nothing stopped such a character) was right and is fixed above.
- **"Inline import silently truncates" (Grok 2).** A longer string is refused with a message
  and nothing is written (real disc, Japan 1: 14 -> 16 bytes refused).
- **Row 13 above ADDF differs between NEC and JIS X 0213 (Gemini b, GLM 3, GPT 14).** True
  of the standards; the discs do not go there. Real disc: the lead-AD codes used are ADA1-ADA7
  and ADB5-ADB7 on Japan (97 uses), ADB5-ADB7 on the demo. Their font cells show ① to ⑦
  and Ⅰ Ⅱ Ⅲ (looked at).
- **0x8E / 0x8F sequences (GLM 2, GPT 15).** Real disc: no byte 0x8E or 0x8F starts a
  character anywhere in the Japanese or demo text. Typing one is now refused.
- **Glyph agreement instead of round-trip (GLM b, GPT 14).** Done as far as a machine can:
  each of the 1,976 distinct two-byte codes of the Japanese text was rendered from its
  Unicode label with a system font and matched against all 1,976 font cells the game would
  draw. The right cell is the best match for 51 % and among the best five for 73 %
  (chance: 0.05 %); mean correlation 0.74 with its own label, 0.23 with another. The misses
  are shapes that a size-normalising comparison cannot tell apart (bars, dots, `一`). This
  supports the cell formula and the labels together; it does not prove any single code.
- **Battle fields that equal an offset but point inside a string (Grok 6, GPT 8).** Real
  disc: 211 such values on USA; 209 of them are the same number at the same record position
  in the Japanese file, whose strings have different lengths (of the 1,095 real string
  offsets, 31 are). They are numbers.
- **minidisc TOC overflow (GLM 10).** `toc.serialize` already raises (`TOC needs 5540 bytes,
  2048 available`).
- **minidisc is not a canonical ISO (Grok 7).** It is a test fixture read only by the kit;
  every claim about real images rests on runs against the five discs.
- **How the game turns a TOC sector into an LBA (Gemini e1, GLM 9, GPT 1).** Code:
  `FUN_0019b458` reads the entry's 3-byte sector, 4-byte size and (compressed) 3-byte size;
  `FUN_0019b720` computes `LBA = [TOC file LBA] + [entry sector]` (`puVar14[2] +
  puVar14[3]`), one addition, no per-bigfile table. Real disc: an image patched twice
  (a bank relocated, then relocated again, plus a map bank and a chain-1 subtitle file) was
  read with a reader written for the test (ISO9660 by hand, that one addition, plain file
  I/O): all three objects equal their payloads, no extent overlaps another or any ISO file,
  both volume-size fields equal the image size. A relocated subtitle file has played in the
  game (MODDING.md); a relocated bank has not.

## Open

- **`--grow` has not been seen in the running game (all four).** The buffer rules are a
  reading of two loaders. Needed: one scene bank and one map bank relocated at their computed
  room, loaded in the game, with the memory after each buffer watched; both discs. Until
  then every relocated bank prints a warning.
- **The cause of the subtitle failure (all four).** Not found. The suggested experiments
  (one line grown step by step; the same growth spread over many lines; more lines per cue;
  narrow against wide glyphs) are the right ones and need the emulator. The retail widths
  (about 450 px on every disc) and the failed line (several hundred pixels wider) point at
  line width.
- **The Japanese width function's `$`-code handling** was not read; the port assumes the USA
  logic with the Japanese constants. The subtitle loader of the Japanese build was not read
  either.
- **Syntax of what is typed (GPT 12).** `$` codes, `%` directives, `[Speaker]` tags and ruby
  in changed strings are not validated. An explicit `⟦XX⟧` escape is never checked, by design.
- **Pointers into the middle of an inline string (GPT 8 applied to scripts).** 470 variable
  slots in 119 Japanese scripts hold a value that lands inside a pool string. They look like
  numbers (281 are multiples of ten) but this cannot be settled from the files.
- **Quest files on USA**: how high bytes are read is unknown, so typed non-ASCII is refused
  there. On Japan the glyph check assumes Shift-JIS is converted to the same JIS codes.
- **USA sheets 2 and 3** look unreachable from the draw function (code only).
- **Real hardware, and how a loader other than PCSX2 treats sectors past the original image
  (GLM e5).** Not tested.

## Dismissed

- **Mixed-region sets should be refused, not warned (GLM 7).** The brief for this work asked
  for a warning; the text import takes one disc and is bound to it by the manifest.
- **"Refuse any growth of displayed subtitle text" (GPT 18, GLM d).** Not adopted as stated:
  the bounds above allow growth only inside what retail lines already do, which is the
  evidence available. Total growth is still capped by the 64-byte rule.
