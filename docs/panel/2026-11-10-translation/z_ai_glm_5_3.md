# z-ai/glm-5.3

## Findings

### (a) Code bugs and risks

1. **Codec claim is contradicted inside the repo — verify before merge.** JIS X 0213:2004 *remapped* some JIS X 0208 cells; the classic cases are 0xA1C1 (U+301C wave dash vs U+FF5E fullwidth tilde) and 0xA1C2 (U+2225 vs U+2016). If `euc_jis_2004` and `euc_jp` decode 0xA1C1 differently, the headline claim "no code decodes differently" and the self-test `same = all(... 0xA1 ...)` are both false. Evidence: run `bytes((0xA1,0xC1)).decode("euc_jp")` vs `.decode("euc_jis_2004")` in Python. If they differ, every document that says "no existing text changes" is wrong and the round-trip proof (which would pass either way, since decode→encode is stable under both labels) proves nothing about the *meaning* of the text. Refute/settle by that one-liner plus a spot-check that the glyph drawn for 0xA1C1 matches whichever Unicode label the kit emits.

2. **0x8F (plane-2 / SS3) sequences.** The 94×94 comparison explicitly excludes three-byte codes. `euc_jp` reads 0x8F x y as JIS X 0212; `euc_jis_2004` reads the same bytes as X 0213 plane 2 — different characters, and the plane-2 reading always round-trips, so `sbl.to_text` will silently emit plane-2 Unicode for any X 0212 gaiji and `from_text` will happily re-encode it. Worse: a translator who types a character that Python encodes to a plane-2 0x8F sequence produces bytes the game almost certainly cannot draw. Evidence: scan all discs for 0x8F in text; if any exist, add a "no 0x8F in output" check (there is none today).

3. **Row 13 attribution unproven.** "lead 0xAD = ①..Ⅹ" is asserted, not shown. 0xAD is undefined in standard EUC-JP, but vendor variants (cp51932 / NEC-selected rows) also define it with different contents than X 0213 row 13. Cheap test: render the 0xAD codes through `font.py`'s cell lookup and compare the extracted glyphs to circled numerals; that settles both the codec question and the claim that "the game's font draws" them.

4. **Claim/code contradiction in the Latin-1 cell math.** Claim 2 says USA lone high byte → cell = byte + 0x80 via lead 0xA9. The code does `trail = (b + 0x80) & 0xFF; cell = self.cell(0xA9, trail)` and `xs2_cell(0xA9, t) = t + 0x100`, i.e. cell = byte + 0x180. One of the claim, `xs2_cell`'s 0xA9 branch, and `glyph()` is wrong; the `*_bytes.csv` table is only as right as this. Evidence: check one byte (e.g. 0xE9 "é") against the extracted sheet image.

5. **Subtitle packer: frame field truncates silently.** `b[q], b[q+1] = state & 0xFF, (state >> 8) & 0xFF` — a cue past frame 65535 wraps with no error. Also the x-offset byte is computed with `width` defaulting to 0 everywhere the kit calls it, so the *returned packed bytes* have bogus offsets (harmless for the size rule, but the function's docstring implies it models the game's output; anyone reusing `pack`'s third return will be misled). Add a `t > 0xFFFF` error or document it.

6. **BOM inconsistency in inline import.** `plan_kinds` reads with `utf-8-sig`, `plan_inline` reads with plain `utf-8`; a BOM saved by an editor makes the first `### @0x…` header unparsed and its block silently ignored (reported only as "not a string this export lists" — i.e. the edit is silently dropped as a *problem*, which is safe, but inconsistent with the other reader). Unify on `utf-8-sig`.

7. **Mixed-region handling only warns.** `dialogue.Disc` proceeds after `warn_mixed` and will merge a Japanese inline-text chain 0 with a USA story chain; the exported dialogue tree is then region-mixed with no per-line marker beyond the warning. If this is intended, the export should record each line's source region; otherwise refuse.

8. **Unknown-serial fallback is a guess.** `region_of` maps an unrecognized disc with no `\EN\` folder to JP and applies inline-text rules. A different regional build (PAL, other demo) would be silently treated as Japan. Refuse instead of guessing.

9. **Relocation assumptions not verified in-diff.** `--grow` moves a bank past the end of the `XENOSAGA.01` bigfile and repoints the TOC. Nothing shown proves the game (or the kit's other tools — dialogue, browse, sniff) reads objects purely by absolute TOC sector rather than by bigfile-relative offset. The minidisc test uses the kit's own readers, which share the assumption; it is not independent evidence. Confirm `ChainReader` and the game both use absolute sectors, and that nothing iterates the bigfile by contiguity.

10. **`minidisc.build` TOC fixed at one sector.** If `toc_events(rows)` serializes past 2048 bytes it silently overwrites the `.01` region; harmless for the shipped tests, but a foot-gun for anyone extending them. Add a length assert.

Otherwise the in-place writers look sound: `batdat_replace` (length-checked, NUL-fill, refuses empty/longer/NUL), `txd_parse`/`txd_build` (strict sequential-offset check, refuse-if-not-byte-identical at export), and the inline import (grow refused, NUL-padded, in-place). The `plan_import`/`plan_kinds` refusal-before-write ordering and the export-then-verify-rebuild guard in `export_kinds` are correct.

### (b) Codec reasoning

Partly sound, partly unproven — see findings 1–3. The code-point-count argument ("0 codes only in euc_jp") is the right kind of evidence but is contradicted by the known JIS2004 remaps unless Python's `euc_jp` happens to agree on 0xA1C1; and the round-trip proof is weak, because any bijection round-trips, including a wrong one. The settling test is not round-trip, it is **glyph agreement**: for every two-byte code that actually appears on the discs, compare the Unicode label the kit emits with the glyph the font draws at the computed cell (all machinery for this already exists in `font.py`). Do that for 0xA1 row symbols and the 73 row-13 strings and the choice is proven; without it, euc_jis_2004 is a plausible but unverified bet. The U+FF5E→A2B2 encode hazard is acknowledged; the 0x8F hazard (finding 2) is not.

### (c) Subtitle breakage

Tens of thousands of VIF "invalid unpack" errors at 2 fps is the signature of a **corrupted or oversized VIF/GIF packet in the subtitle draw path**, not of a buffer overwrite (which would crash or garbage, not a repeating DMA complaint). Most likely, in order:

1. **A per-line draw packet built into a fixed-size staging area** — one line's sprite list/packet is sized for the retail maximum; 52 extra characters overflow the packet mid-tag, and the renderer re-emits the broken tag every frame (hence thousands of errors at 2 fps ≈ retriggering).
2. **A fixed cue/sprite struct count** (text split into N sprites max; overflow writes past the last sprite into the VIF tag).
3. A wrapping loop whose iteration count is computed from a byte that saturates.

A fixed-size cue struct or the decoder work area is *less* likely given the agent's own finding that the work area did not move. The agent's revised reading also fits: displayed text doesn't move the work area, so the break must be in the draw path — which is evidence *for* a per-line/per-cue limit and *against* any total-size rule.

Cheapest matrix (PCSX2, one movie, one cue): vary independently (i) one line's length in single-character steps past the known-broken point — find the exact per-line threshold; (ii) total packed size grown across *many* lines, each under the per-line threshold — does total size matter at all; (iii) number of `\n` segments per cue; (iv) file size grown by comments only (already known safe). If the threshold in (i) is sharp and independent of total size, the 64-byte rule is enforcing the wrong variable entirely.

### (d) The unexplained 64-byte rule

No — it is not the conservative choice, it is the *unrelated* choice. Given the agent's own analysis, the rule permits edits that may still break (one line grown to just under the boundary, if the real limit is per-line and lower) and refuses edits that may be safe. Keeping it while documenting the gap is defensible only as "refuse the one known-broken shape"; the honest conservative policy is: **refuse any growth of displayed subtitle text** (per-line and total) until the emulator matrix in (c) identifies the real limit, then encode that limit. At minimum the tool should also cap per-line growth, which it currently does not.

### (e) Before advertising --grow

The buffer-size rules are static analysis of a decompile, checked only against retail sizes (which obey them trivially) — they have never been exercised. Required before shipping:

1. **In-game load test**, both loaders (scene and map), a relocated bank: text visibly renders from the new location, on both discs.
2. **Worst case**: the scene with least retail room (10,240 B) and the map with least (1,664 B) grown to their computed budgets — the rounding rules (round2048 vs align64 for the script) are exactly the kind of thing a decompile misreads.
3. **Confirm the loader reads the bank by TOC size/sector**, not a cached or hardcoded size — otherwise the appended tail is never read or is read past the buffer.
4. **Confirm nothing else addresses the bigfile by contiguity** (finding 9).
5. **Real hardware or at least a note** that only PCSX2/OPL was tested; sector reads past the original image size are emulator-cheap but media-dependent.
6. Verify the volume-size update path (PVD) is actually consulted by whatever the user loads the image with.

Until 1–4 pass, `--grow` should stay marked experimental, and the help text's "not yet seen in the running game" should be promoted to a hard warning on every relocated object, not a help-string footnote.
