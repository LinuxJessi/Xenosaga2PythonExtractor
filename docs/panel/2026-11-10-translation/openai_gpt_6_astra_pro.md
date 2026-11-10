# openai/gpt-6-astra-pro

**Do not merge the writable paths as production-safe yet.** The supplied code contains data-loss cases, and the most consequential writer implementation is absent.

### (a) Code and image integrity

1. **TOC patching and relocation cannot be audited from this diff.**  
   `repack.patch_iso`, the TOC writer, `ChainReader`, and `_allocations` are not supplied. Reading relocated data back through the kit’s own reader cannot establish that the game resolves the same LBA.  
   **Required evidence:** review sector-coordinate conversion, TOC field widths/flags, size updates, append alignment, both-endian volume-size fields, and chain-extent handling. Test multiple relocations, nonzero chains, demo paths, and a second patch of an already-relocated image using an independent reader and runtime disc-read tracing.

2. **The importer can overwrite its source image despite promising a copy.**  
   `import_text` skips copying when resolved source and destination are equal, then patches that image. A symlink resolving to the input also takes this path. There is no transaction boundary visible here.  
   **Test/fix:** reject identical files, including aliases; patch a temporary copy, verify it, then atomically publish it. Inject write/readback failures and verify the original and any existing destination remain intact.

3. **“Fits its disc slot” does not prove “fits its runtime buffer.”**  
   `plan_kinds` permits menus and quests to grow anywhere within `alloc`. The claim that the sector-rounded read therefore stays unchanged is false if allocation includes spare sectors or gaps. A 2,048-byte object in a 4,096-byte slot can double its read size.  
   **Test/fix:** construct exactly that case. Until loader capacity is known, cap sector-rounded size at the original read size; for menus, also enforce established per-file buffer limits.

4. **The `.txd` rebuild silently ignores string IDs.**  
   `_txd_rebuild` uses `[b for _, b in _blocks(text)]`; only block count matters. Reordering blocks, duplicating an ID, or replacing an ID with nonsense changes indexed menu strings without refusal.  
   **Test/fix:** swap two complete blocks and duplicate an ID while keeping the count unchanged. Require unique, complete IDs and rebuild by ID, or reject reordered input. The offset arithmetic is sound for the restricted contiguous format accepted by `txd_parse`.

5. **Block syntax can silently delete legitimate text.**  
   `_blocks` and `plan_inline` discard every body line beginning with `#`. This directly conflicts with Japanese renderer syntax such as `##` and `#XX`. A body beginning `### @` can instead become an inline block header. Trailing empty lines are also discarded.  
   **Test/fix:** round-trip strings beginning `#`, `##`, `#20`, and `### @`, plus intentional trailing breaks. Introduce an unambiguous body-escaping convention. `export_kinds` checks reconstruction, but `export_inline` does not.

6. **Inline replacement accepts embedded NUL and unstable empty strings.**  
   `plan_inline` accepts `sbl.from_text("A⟦00⟧B")`, then writes bytes the game will read only as `A`. Emptying a table-only string also removes it from `pool_strings`, so `pointer_slots` may no longer discover it on the next export.  
   **Test/fix:** reject embedded NUL; test emptying both push-referenced and table-only strings through two export/import cycles. Preserve string identities independently of nonempty-content discovery.

7. **Duplicate and missing inline/battle blocks are not validated.**  
   Inline duplicates overwrite entries in `blocks`; battle duplicates have inconsistent semantics because only changed values enter `new`. For example, a changed battle block followed by its original value still applies the earlier change. Missing blocks are silently ignored.  
   **Test/fix:** exercise duplicate IDs with changed→original and original→changed ordering. Reject duplicates; distinguish an explicitly supported partial-edit format from accidental deletion.

8. **Battle-string discovery is too heuristic to support the stated safety guarantee.**  
   `batdat_raw_strings` treats matching values anywhere before the pool as pointers. Its section recognition also depends on the first string’s first byte. `batdat_replace` permits changing that byte to a control byte or a byte outside its recognition ranges, potentially making the entire section disappear on re-export. Same-size replacement avoids offset relocation but does not prove the target is exclusively text or has no interior aliases.  
   **Test/fix:** mutate the first pool string’s leading byte and compare discovered keys before/after. Establish actual record layouts or verify pointer use in-game; investigate any references into string interiors.

9. **Edited imports are not bound to the disc they came from.**  
   Plans match paths and indices/offsets, without an enforced build fingerprint or original-object hash. A stale or different-build tree with compatible paths can overwrite unrelated text successfully.  
   **Test/fix:** import a tree against another disc/build and against an already-modified object. Require matching export metadata and original hashes, with a deliberate override for rebasing.

10. **Unknown builds are assigned executable-specific profiles instead of refused.**  
    `region_of` claims to inspect chain 0 but actually scans `iso.files`, which normally contains ISO files rather than internal TOC paths. Unknown images will commonly default to Japan. `bank_budget` additionally selects USA loader rules solely by path, ignoring build and language directory.  
    **Test/fix:** use an unknown serial with USA chains. Writable operations and executable table access should require a validated supported build; verify budget eligibility explicitly.

11. **ELF validation is incomplete.**  
    `elf_segments` does not check ELF class/endianness, program-header size/bounds, or segment file bounds. `read_va` can return a short slice despite claiming a backed range, leading to uncaught `struct.error`/`ValueError` rather than `ElfError`.  
    **Test/fix:** truncate headers/segments and falsify `p_filesz`. Validate every range against actual file length and normalize failures before interpreting font tables.

12. **The encoding and escape APIs do not validate what the game will consume.**  
    `⟦XX⟧` permits arbitrary bytes; successful encoding is not valid renderer syntax. Imports do not invoke `Addressing`, check truncated pairs, or validate modified `$...`, ruby, quest syntax, and menu format directives. The USA Latin-1 high-byte path is also incompatible with blindly encoding newly typed Western characters as EUC.  
    **Test/fix:** import a USA accented letter, an unsupported kanji, a dangling lead byte, changed `%` directives, and malformed controls. Validate changed strings with the actual build/text-type parser; provide explicit raw-byte escape overrides rather than treating them as safe text.

13. **Font reconstruction identity does not validate edited-font handling.**  
    `cmd_rebuild` silently skips missing sheets; the PNG reader ignores alpha/transparency and CRCs. Transparent black pixels can become visible black glyph pixels, while a wrong directory can produce a successful unchanged rebuild.  
    **Test/fix:** test missing sheets, transparent RGBA/palette images, malformed PNG lengths, and duplicate metric slots. Require explicit partial-sheet mode and define transparency handling. The independently checked byte-identical unedited rebuild is sound evidence only for preservation of those inputs.

### (b) Codec reasoning

14. **The corpus result supports lossless storage conversion, not identification of the game’s character repertoire.**  
    Agreement on shared two-byte mappings and exact corpus round-trips is useful but does not establish that the additional JIS X 0213 mappings correspond to the game’s glyphs. NEC/vendor row 13 overlaps some standardized mappings; that does not validate the entire row or other added rows. The calendar year is not evidence of codec adoption.  
    **Settling test:** enumerate every distinct corpus code, compare candidate Unicode mappings against the actual addressed font glyph, and verify disputed codes through the game’s renderer. Publish the mismatches and create an explicit supported-code map.

15. **The 94×94 comparison excludes important EUC cases.**  
    It does not cover `0x8E` half-width kana or `0x8F` plane-2 sequences. `euc_jp` uses JIS X 0212 for the latter; `euc_jis_2004` uses JIS X 0213 plane 2. The supplied Japanese `glyph` implementation does not consume either prefix as standard EUC would. Unicode aliases such as U+FF5E/U+301C can also encode differently or select unsupported cells; combining mappings introduce normalization hazards.  
    **Test/fix:** inventory these sequences explicitly, test both encode directions and editor normalization, and compare byte consumption in-game. Reject unsupported additions; preserve original bytes independently of Unicode spelling.

### (c) Subtitle failure

16. **“VIF invalid unpack” indicates corrupted command input, not a diagnosed subtitle-size limit.**  
    The leading hypotheses are a line/cue staging-buffer overflow or overflow of a glyph draw/DMA packet, possibly stack-resident. A fixed cue structure is plausible if text is copied into it. Sprite/glyph-count or packet-capacity limits are more directly connected than a texture-count limit when growth merely repeats existing glyphs. The error flood and 2 fps may be consequences of logging after the first corruption.  
    **Evidence:** break on the first malformed VIF submission, capture the DMA chain and its producer, and watch writes around subtitle staging storage, stack frames, and packet buffers. Determine where the first out-of-bounds write occurs.

17. **Use a small controlled matrix, then binary-search the failure threshold.**  
    Start each run before subtitle loading; avoid savestates containing an already-loaded old file. Keep timing and control codes unchanged, and compensate with comments where possible to hold file length/work-area placement constant.

    | Experiment | What it distinguishes |
    |---|---|
    | Original, +1, +26, +52 plain repeated glyphs; binary-search first failure | Reproducibility and threshold |
    | Same added text on one line, split with `\n`, then split across non-overlapping cues | Per-line versus per-cue/packet limits |
    | Same byte count using narrow/wide glyphs and different glyph counts | Byte capacity versus pixel width versus glyph count |
    | Repeated glyph versus many distinct glyphs | Packet count versus glyph/texture caching |
    | Comment-only padding across 64-byte and sector boundaries | Raw-file/work-area placement |
    | Grow one line while shortening another by the same amount | Per-line limit versus total packed size |

    **Record:** encoded bytes, rendered glyphs, pixel width, per-cue size, packed total, actual work pointer, and first failing write—not merely whether playback appears slow.

### (d) Subtitle policy

18. **The unexplained 64-byte rule is not a conservative safety bound.**  
    It can allow a line already near its real limit to grow, or allow large per-line growth offset by shortening another line. It also admits arbitrary comment growth despite comments moving the hypothesized decoder work-area start. Rejecting one known failure does not validate accepted cases.  
    **Policy:** default to refusing displayed-text growth pending diagnosis—at least per line and per cue, not merely in aggregate. Keep any broader editing behind an explicitly unsafe experimental option. Equal-size edits still need syntax/resource checks; comment growth needs a separate decoder-memory bound.

19. **The subtitle simulator’s size-only checks cannot establish rendering safety.**  
    `check_edit` uses `width=0`, never checks the real work-area bound, and accepts times that are later truncated into 16-bit cue fields. The parser also treats backslash as a line-break introducer without validating the next byte as `n`.  
    **Test/fix:** compare packed bytes, returned `L`, and actual sector slack against the running game for edited cases, not just retail fixtures. Reject unsupported escapes, embedded NUL, invalid time ranges/sentinel collisions, and malformed ruby/control syntax.

### (e) Before advertising `--grow`

20. **The loader equations need runtime boundary and lifecycle verification.**  
    For each supported scene/map loader and build, trace the actual allocation, script and bank destinations, TOC size, sector read length, LBA, and subsequent pointer fixups. Check whether decompression, temporary workspace, simultaneous loads, or later processing require additional space. For maps, specifically verify how the sector-rounded script read interacts with placing the bank at `align64(script_size)`.  
    **Required tests:** same-size relocation first; then growth across sector boundaries and up to the proposed budget, including repeated scene/map transitions, reloads, and disc changes. Watch memory immediately beyond the proposed buffer throughout loading and use. A deliberately over-budget laboratory case can test the boundary hypothesis, but must remain tool-refused. Retail pairs fitting an inequality establishes consistency, not capacity or relocation support.
