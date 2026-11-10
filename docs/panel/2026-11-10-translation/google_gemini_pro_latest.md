# ~google/gemini-pro-latest

### (a) Code Bugs and Data-Loss Risks

1. **Silent failure when `ref` and `sheets_dir` point to the same directory (`font.py:cmd_rebuild`)**
   If `--ref FONTDIR/font0.us --sheets FONTDIR` is run after an earlier extraction that used a different stem (or if a user renames the `.us` file to `font0_orig.us`), `stem = Path(ref).stem` looks for `font0_orig_sheet0.png`. When none of the 4 PNGs exist, the loop silently skips all of them (`if not p.exists(): continue`), writes back the unedited reference font, and reports success.
   *Evidence:* Run `font-rebuild` with `--ref` named `copy.us`; it returns 0 and prints `(identical to the reference)` without reading any PNGs. It should fail if no sheet PNGs match `stem`.

2. **Comment-stripping eats user dialogue lines starting with `#` (`textpack.py:plan_inline`, `_blocks`, `read_text_file`)**
   In `plan_inline` (`elif cur is not None and not line.startswith("#"):`) and `_blocks` (`elif out and not line.startswith("#"):`), any line inside a text block beginning with `#` is silently dropped on import. If an inline string or menu/battle string contains a real newline (`\x0a` raw, or user-typed multi-line block) where the second line starts with `#` (e.g., `#1` or a Japan `$han` hex escape `#23` at the start of a line), that line vanishes without warning.
   *Evidence:* Export a `.txd`, put a two-line entry where line 2 is `#1`, and run `plan_import`. Line 2 is deleted.

3. **Trailing empty lines inside a multi-line string are stripped (`textpack.py:plan_inline`, `_blocks`)**
   `while len(b) > 1 and b[-1] == "": b.pop()` removes trailing blank lines before the next `###` header, because `export` does not put a blank line between blocks, *except* if a string legitimately ends with `\n` (0x0A). Because `sbl.to_text` leaves 0x0A as a literal newline, a string ending in `0x0A` exports as a block with an empty last line, which `b.pop()` destroys on re-import.
   *Evidence:* Test `txd` or `inline` round-trip on a string ending in `b"text\n"`. `to_text` emits a trailing newline; `_blocks` pops it; the rebuilt string loses its final `0x0A`.

4. **`moviesub.py:_cue` mishandles `\` at end of cue or `\\` escapes (`moviesub.py:175-185`)**
   When `ch == 0x5C` (`\`), `_cue` unconditionally sets `b[o] = 10; p += 1; c = b[p]` assuming `\n` (2 ASCII bytes `\` and `n`). It does not check that `b[p] == ord('n')`. If a subtitle line contains a literal backslash (or a Shift-JIS/EUC trail byte `0x5C` if someone puts one in), it turns `\x` into a newline `0x0A` and eats the `x`.
   *Evidence:* Check the disassembly of `FUN_001e7610` at the `0x5C` branch to verify whether the game checks for `0x6E` (`'n'`) after `0x5C` or blindly skips one byte.

5. **`moviesub.py:pack` frame-0 overwrite check uses `split(b"\x00")` on mutated buffer (`moviesub.py:205`)**
   When `state > 0` on the first cue, `_cue` has already written the first cue at `out = 0`, potentially overwriting the start of the buffer `b[0..o]` before `state > 0` is checked. If `p0` was small, `_cue`'s own `_put` calls only checked `o + n > p` (where `p` has already advanced to the *end* of the first cue), so `_cue` will happily overwrite the bytes at `p0` (the start of the first cue's timestamp) while parsing the first cue's text. Then at line 207, `q, p = 1, p0` re-parses from `p0`, which now contains corrupted bytes from the first pass!
   *Evidence:* Pass `b"00:01:00\tHello world\nend\n"` (no leading comments, `p0 = 0`, `state = 30 > 0`) to `moviesub.pack()`. On pass 1, `_cue` reads `00:01:00\t` (`p` moves to 9) and writes `\x01\xffHello` to `b[0:7]`, clobbering `00:01:0`. The check `3 + len(...) > p0` catches `p0 = 0`, *except* if `_cue` already corrupted the input stream mid-cue or if `b[2:]` NUL check misfires. Worse: if `p0 = 8` (e.g. `;123456\n00:01:00\tA\nend\n`), `3 + 1 = 4 <= 8` passes the check at line 205, even though pass 1 wrote `b[0..3]` and on pass 2 `q, p = 1, p0` works, *unless* the first cue had a `\n` line break where `b[2:].split(b"\x00")[0]` stops at the end of line 2, undercounting or overcounting the prefix bytes. Instead of running `_cue` at `out = 0` and rewinding `p = p0` after mutating `b[0:]`, run the first-cue probe on a copy or don't mutate `b` when `state != 0`.

6. **`smallfiles.py:batdat_raw_strings` heuristic can miss or falsely match strings (`smallfiles.py:224-226`)**
   It takes `pool = u16_at_0`, interprets the entire `[0 : pool]` region as an array of `u16` words, and treats *every* `u16` whose value `w` satisfies `pool <= w < len(seg)` and `seg[w - 1] == 0` as a string pointer. If a numeric stat field in a record happens to equal the offset of a string in the pool, it adds it to the set `refs` (harmless for reading), *but* if a string is shortened in `batdat_replace` and padded with NULs (`0x00`), any numeric `u16` in the records whose value happens to fall inside that newly NUL-padded tail now satisfies `seg[w - 1] == 0` and `seg[w] != 0` on a subsequent `text-export`!
   *Evidence:* In `batdat`, shorten a string at offset `100` from length 10 to length 2 (so `102..109` become `0x00`). If any stat in the section has integer value `100`, nothing breaks; if a string was shrunk and left non-zero garbage (not here, it zeroes), it would split. However, because `batdat_replace` zeroes the tail, if a string is shortened to the point where a *following* byte was already preceded by NUL, it's fine—Wait: what if two pointers legitimately shared a suffix (e.g., `"All"` and `"One All"`), or what if a string starts with a byte `< 0x20` (like a control code)? Line 222 checks `0x20 <= seg[pool] < 0x7F or 0xA1 <= seg[pool] <= 0xFE`: if the very first string in a section starts with a control byte or `0x8F`, the entire section is skipped as "numbers-only".

---

### (b) Codec Reasoning (`euc_jis_2004` vs `euc_jp`)

1. **The round-trip claim on existing disc bytes is sound, but the *encoding* hazard for translators is real and unguarded in `textpack.py`.**
   *Why:* On PS2 (2004), lead byte `0xAD` is NEC Row 13 (Windows CP932 / `euc-jp-ms`), *not* JIS X 0213:2004. For row 13 (`0xADA1`–`0xADFE`), NEC Row 13 and JIS X 0213 happen to agree on `①`–`⑳` (`0xADA1`–`0xADB4`) and Roman numerals `Ⅰ`–`Ⅹ` (`0xADB5`–`0xADBE`), which is why all 73 strings in the Japan corpus decode cleanly.
   *What goes wrong:*
   - **Row 13 divergence above `0xADDF`:** In NEC Row 13, `0xADE0`–`0xADFC` are kanji (`纊`, `褜`, `鍈`, `銈`...); in `euc_jis_2004`, `0xADE0`–`0xADFC` are lowercase Roman numerals and circled numbers (`ⅰ`..`ⅹ`, `㉑`..`㉟`). If any of those 73 strings on the JP disc uses `0xADE0`+, `euc_jis_2004` silently decodes the wrong character while still round-tripping byte-for-byte!
   - **Fullwidth Tilde (`～` U+FF5E) vs Wave Dash (`〜` U+301C):** `euc_jp` and `euc_jis_2004` both decode `0xA1C1` to `U+301C` (`〜`). When a user types `～` on a Windows IME, they get `U+FF5E`. Under `euc_jp`, encoding `U+FF5E` raises `UnicodeEncodeError` (or maps to `0x8FA2B7`). Under `euc_jis_2004`, `U+FF5E` silently encodes to `0xA2B2` (JIS X 0213 row 2 cell 18)—a code the Episode II font (`xs2_cell`: lead `0xA2`, trail `0xB2` -> `trail & 0xF0 == 0xB0`, which is `None` in `{0xA0, 0xE0, 0xF0}`) maps to `FALLBACK_CELL`! The translator gets a blank/fallback glyph in-game with no error from `text-import`.
   - **3-byte `0x8F` sequences:** `euc_jis_2004` encodes hundreds of common Unicode characters into 3-byte EUC (`0x8F xx yy`), which the game's 2-byte reader (`font.py:glyph`) misreads as two separate glyphs.
   *Test to settle it:*
   1. Dump the exact `(lead, trail)` pairs of all 73 `0xAD` occurrences on the JP discs and verify against the glyph images in `font0.tex` at `cell = trail + 0x17F` (`xs2_cell` for `0xAD`).
   2. In `sbl.from_text` / `text_to_file`, reject any encoded sequence containing `0x8F` or any `(lead, trail)` that `xs2_cell` sends to `FALLBACK_CELL` (unless it was written as an explicit `⟦XX⟧` escape).

---

### (c) Movie Subtitle Breakage ("VIF invalid unpack" at 2 fps)

1. **Most likely causes:**
   - **Per-line / per-cue VU1 or GIF draw packet buffer overflow (Top suspect):** Each character in a subtitle line generates sprite quads (or DMA/VIF unpack tags) into a fixed-size packet buffer (on the scratchpad `0x70000000`, stack, or a static packet ring buffer). Adding 52 characters (~52–104 quads, or ~1.6–3.3 KB of VIF/GIF data) overflows the packet buffer into the adjacent VIF DMA chain or Sofdec MPEG work struct, corrupting VIF command words and causing PCSX2 to spam `VIF invalid unpack`.
   - **Line-buffer overflow during string formatting / ruby expansion (`FUN_001e7610` or the subtitle renderer):** The renderer copies the active line into a small stack/static buffer (e.g., 64 or 128 bytes) to strip `$ruby` tags and compute width (`width(bytes)`). A 52-char increase overflows the stack frame or adjacent BSS globals.
   - **Sofdec allocation header overwrite:** Even if `work_offset` (`align64(L)`) moves with the file size, check whether the outer allocation in `FUN_001e7d08` has a **fixed total size** (so moving `align64(L)` forward pushes the Sofdec work area past the end of the allocated heap block!).

2. **Cheapest emulator experiment matrix (3 runs in PCSX2):**
   - **Run 1 (Isolate file size / `L` from line length):** Take the retail `.txt`, add **+52 bytes of `;` comment** at the top, keep all cues identical. (Tests whether moving `align64(L)` by +64 bytes overflows the outer Sofdec block). *Note: Claim 5 says longer comments were already tested and worked; if so, `align64(L)` moving is NOT the bug.*
   - **Run 2 (Isolate total packed size from single-line length):** Add **+4 characters to 13 different cues** (+52 packed bytes total, no single line longer than retail max).
   - **Run 3 (Hardware breakpoint on the packed cue):** In PCSX2 debugger, place a read breakpoint on the packed cue's bytes during playback to locate the renderer function, and inspect the destination buffer size where it copies/builds the VIF packet for the current line.

---

### (d) Keeping the 64-Byte Subtitle Rule

**No—replace or supplement it with a per-line and total-file guard.**
As proven in `moviesub.py`'s own docstring, `FUN_001e7a08` scans past the `0xFFFF` terminator to the first NUL *after the unpacked file text*, so `packed_size` has **zero effect** on `align64(L)` unless the packed data somehow outgrows the entire source file (impossible since packing shrinks the data). Therefore, the `align64(packed_size)` boundary is physically meaningless—it only rejected the +52 char test by coincidence because `52 > (64 - (a % 64))`. Worse, a user can add +50 characters to a single line *without* crossing the 64-byte boundary if they delete 50 characters from another cue in the same movie, and that will still trigger the VIF crash if the limit is per-line!
*Fix:* Refuse any single line exceeding the maximum line length seen in retail subtitle files (or refuse growing any individual cue's packed length, and refuse growing total `packed_size` at all) until the renderer's buffer size is read from disassembly.

---

### (e) What to Check Before Advertising `--grow`

1. **ChainReader / multi-file LBA math in the game's CD/DVD driver:**
    Chain 0 is split across `XENOSAGA.01`, `XENOSAGA.02`, etc. When `repack.patch_iso` relocates a grown `.sbl` to the "end of the image", check how the game's file system translates a TOC `sector` number into a physical disc LBA. Does the game map TOC sectors linearly past the end of the last split file (`.02`), or does it index an array of split-file sizes/LBAs (e.g., 1 GB chunks) and fail or wrap around if `sector` exceeds the size of the last `.0N` file recorded in the executable or ISO9660 directory?
2. **Heap / static buffer lifetime and clear size:**
   - For scenes (`0x3C6DC0`, `0x20000` bytes): check whether `FUN_001eece0` or the scene teardown zeroes `0x20000` bytes or if anything between `0x3C6DC0 + retail_max` and `0x3E6DC0` is actually used as scratch memory by overlays (`OV01`, etc.) while the scene runs.
   - For maps (`FUN_001df928(0x12000)`): verify that the DMA/CD read function (`FUN_0019bc98`) reads into a temporary ring buffer when unaligned, or if it DMAs whole 2,048-byte sectors *directly* to `block + align64(bin)`. If it DMAs whole 2,048-byte sectors directly to a 64-byte-aligned address (`align64(bin)`), the last sector write can overshoot `0x12000` if `align64(bin)` is not a multiple of 2,048—Wait: `bank_budget` computes `room = (buf - used) // SECTOR * SECTOR`, which ensures `used + round2048(sbl) <= 0x12000`, so the end does not overshoot `0x12000`. However, you must verify that the CD read routine supports destination addresses that are only 64-byte aligned (which it does in retail, since `bin` sizes are only 64-byte aligned).
3. **Required in-game test:**
   Relocate one scene `.sbl` (grown to `room`) and one map `.sbl` (grown to `room`) to the end of the ISO, boot in PCSX2, enter both the scene and the map, trigger the last string in each grown `.sbl`, and verify no memory corruption or CD read hang occurs.
