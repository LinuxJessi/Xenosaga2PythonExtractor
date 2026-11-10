# x-ai/grok-4.3

1. text-import --grow relocation risks corrupt image: patch_iso (called from textpack.py:624) updates TOC/volume size but relocation logic is synthetic only. Evidence: run `minidisc.build` + `patch_iso(..., grow=True)` then boot in PCSX2; check whether relocated .sbl loads or VIF/TOC errors appear.

2. Inline text (Japan) import silently truncates: sb.Script.inline_text + textpack.plan_inline write only if len(new) <= len(old). Evidence: export 17,225 strings, edit one +1 byte, import; confirm original byte lost and no error emitted.

3. euc_jis_2004 encodes unrenderable glyphs: sbl.from_text + font.xs2_cell have no guard for chars outside drawable set (e.g. U+FF5E -> 0xA2B2). Evidence: `xs2_cell(0xA2,0xB2)` returns non-fallback; round-trip test shows encode succeeds but game print drops glyph.

4. Subtitle 64-byte rule is heuristic only: moviesub.check_edit enforces boundary despite work_offset analysis showing it moves with file size. Evidence: craft subtitle whose packed size == next 64-boundary but line length > retail max; observe VIF unpack rate in PCSX2.

5. bank_budget derived solely from decompile: textpack.SCENE_BUFFER/MAP_BUFFER hard-coded from FUN_001eece0 etc. Evidence: single-step those functions in PCSX2 with varying .sbl sizes; confirm actual buffer address and overflow behaviour match 0x20000/0x12000.

6. batdat_replace assumes all string refs are known at build time: smallfiles.known dict built from raw_strings but does not re-validate after each write. Evidence: two overlapping strings where second pointer equals first value; edit first, check second becomes truncated.

7. minidisc._record produces non-canonical ISO9660: root directory records and PVD total fields are hand-built, not round-tripped against real disc. Evidence: compare `minidisc.build(...).read(16*SECTOR)` byte-for-byte with retail SYSTEM.CNF image; any mismatch indicates TOC drift on real patches.

Sound: region.warn_mixed, sbl.CODEC round-trip on corpus, and xs2_cell port of FUN_001979e0 are verified byte-identical on five discs.
