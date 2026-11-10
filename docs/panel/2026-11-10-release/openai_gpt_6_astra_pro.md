# openai/gpt-6-astra-pro

**Release blockers remain.** The spot checks establish one successful edit path, not the advertised failure guarantees.

1. **The fixed `.partial` name is unsafe under concurrency and path races.**  
   `patch_copy` checks, unlinks, creates, patches and publishes the temporary file through separate pathname operations. Two invocations can delete each other’s work or publish a file they did not verify. A symlink inserted before `copyfile` can redirect its write—including to the input. `same_file` also fails open on `OSError`.  
   **Evidence/test:** synchronize two writers at copy/verification boundaries; inject a symlink after the temporary-file check. Use an exclusively created unique temporary file and retain its identity throughout processing.

2. **Existing destination symlinks are destroyed before publication succeeds.**  
   `dst.unlink()` followed by `os.replace()` creates a gap. An exception, crash or rename failure in that gap loses the previous output link, contradicting “an existing output is left exactly as it was.” `os.replace` ordinarily replaces the link itself without this unlink.  
   **Evidence/test:** make `os.replace` fail immediately after `dst.unlink`; verify the original destination still exists. It currently will not.

3. **Publication is not crash-durable; replacement and permission policies are unstated.**  
   Read-back is not `fsync`. There is no file or directory synchronization, so power loss can invalidate a supposedly completed publication. Existing regular outputs are silently replaced; `copyfile` does not preserve their permissions or the source’s permissions. Creating the temporary file beside the destination normally avoids cross-device rename—sound under stable directory paths.  
   **Evidence/test:** inject disk-full, permission and rename failures; inspect output modes; perform crash-recovery tests around rename. Explicitly document overwrite behavior.

4. **Text export can erase translations and leave a mixed-generation tree.**  
   Exporting into an existing same-build tree unconditionally overwrites its text files, merges old manifest records, and writes the manifest last without a transaction. Exporting Disc 2 into an edited Disc 1 tree can therefore erase shared-object edits. A crash can leave only some files refreshed. Skipped objects still allow exit status zero.  
   **Evidence/test:** edit an exported bank, export again into that directory, and interrupt midway through another export. Require explicit replacement/merge policy and a completion report.

5. **Export paths are neither contained nor guaranteed unique.**  
   `_tree` accepts TOC path components without rejecting `..`, absolute paths or symlinked output directories. Damaged or crafted TOCs can direct writes outside the tree. Separately, `Inline.tree_path` maps both `x.sb` and `x.bin` to `x.inline.txt`, allowing one export and manifest record to overwrite another.  
   **Evidence/test:** construct those two script names and traversal paths; assert unique destinations contained within the export root before writing anything.

6. **The USA codec crashes on truncated unconditional pairs.**  
   `UsCodec.read(b"\xA4", 0)` returns two using the synthetic zero lookahead; `_decode` then accesses nonexistent `s[i + 1]`. The same applies to terminal `A6` and `AD`. Thus “exact for any bytes” is false, and damaged text can abort export rather than become escapes.  
   **Evidence/test:** round-trip every one-byte input, then all two-byte inputs and truncated retail strings. An incomplete pair must export its existing byte without indexing beyond the buffer.

7. **The USA split is plausible; “drawn as typed” is not established by this checker.**  
   Conditional on `font.us_two_byte` matching the executable, the pair/Latin-1 dispatch and neighboring-byte checks are sound in principle. However, ink only proves a cell is nonblank—not that it depicts the requested character. `han=True` is forced, command state is not interpreted, and `Context.addressing` accepts just one font if the other is missing. Patched fonts can pass while depicting the wrong character.  
   **Evidence/test:** remove either font; substitute a different nonblank glyph; test supported characters under actual `$han`/font/proportional states. Compare screenshots or validated glyph mappings, not just re-exported text.

8. **Manifest binding is sound only for the objects actually matched.**  
   Checking target-object hashes supports importing byte-identical shared objects from Disc 1 onto Disc 2 without requiring identical disc serials. But import iterates target objects, silently skips missing tree files, and never accounts for unused tree/manifest entries. A Disc-1-only edited object can be ignored while import reports success.  
   **Evidence/test:** edit a source-only object, remove another exported file, then import into Disc 2. Require explicit applied/unchanged/missing/unmatched accounting.

9. **`--rebase` is a blind snapshot application, not a merge.**  
   It discards even build/format provenance. Matching keys or string counts do not establish semantic correspondence: indices can refer to different dialogue, offsets to different strings, and another build uses another codec. Unedited text from the old export also overwrites changes already present on the target.  
   **Evidence/test:** change one target string independently, edit a different exported string, and rebase; check whether the independent edit survives. Retain build checks by default and require base-aware conflict handling for actual rebasing.

10. **Inline read-back verifies addresses, not intended string contents.**  
    `Inline.rebuild` checks only `again.text_offsets() == offs`. If two references address overlapping strings or suffixes, editing one can alter another without changing that list. The error branch also assumes `lost` is nonempty; an added-only discovery change produces `IndexError`.  
    **Evidence/test:** construct overlapping referenced strings and compare every reread string with its intended replacement or unchanged original. Test discovery changes that add entries without removing any.

11. **Quest structure validation runs before escape decoding.**  
    Putting `⟦22⟧` inside a quoted body passes `quest_skeleton(text)` but emits a literal quote, potentially breaking the quest syntax. Raw backslashes and newlines have similar issues. If the original skeleton returns `None`, structural checking is disabled altogether. The Japanese glyph check additionally assumes an unread Shift-JIS conversion path.  
    **Evidence/test:** replace `Name:"A"` with `Name:"A⟦22⟧B"`; validate the emitted bytes with the game-equivalent parser. Raw-byte permission must not silently waive structural constraints.

12. **Subtitle line extraction confuses prefix bytes with newline delimiters.**  
    `cues.lines()` splits the complete prefixed text on `0x0A`. A width of **490 or 491 pixels** produces x-offset byte `~245 & 0xFF == 0x0A`; this is a prefix byte, not a line break. The resulting line count, text bytes and width checks are wrong.  
    **Evidence/test:** call `cues` with a width function returning 490 for a normal line. Parse each two-byte prefix structurally before searching its text for separators.

13. **Subtitle “retail bounds” can silently become weaker or cease being retail.**  
    `subtitle_limits` drops pixel limits on `ValueError`. It also derives limits from the image being patched, including previously modified subtitle files. Fresh exports of successive patched images can ratchet these supposed retail maxima upward. Independent byte/width/line-count maxima remain a heuristic, not proof that every combination works.  
    **Evidence/test:** induce a width-parser failure and inspect the enforced limits; repeat export/edit/import against patched images. Pin baseline limits to validated retail data and refuse—or prominently report—unmeasured widths.

14. **Control-language safety is still mostly an instruction to the translator.**  
    Block checking permits changes to `%p`, `%d`, `$...` and speaker/control syntax; ASCII-only strings bypass glyph checking entirely. File-size and string-identity checks cannot detect malformed formatter arguments or interpreter commands. Whether these cause runtime faults depends on the actual consumers.  
    **Evidence/test:** mutate/remove format placeholders and command terminators; trace each consumer or exercise those strings in-game. Either validate the relevant grammar or explicitly classify these edits as unchecked.

15. **Model exports can look complete when incomplete or stale.**  
    `_one_model` writes directly to the final `.glb`. Disk-full or interruption can leave a truncated final-named file. A subsequent failed decode does not remove or distinguish an older successful `.glb`. Numeric-check failures are deliberately published, so “written” cannot mean valid.  
    **Evidence/test:** fail `write_bytes` halfway; rerun against a damaged mesh with an existing output; validate the resulting GLB and report. Use per-model temporary publication and explicit run/completion metadata.

16. **Damaged-model containment and skeleton fallback are insufficiently demonstrated.**  
    Outer handling excludes `OSError`, `IndexError`, `struct.error` and other possible parser failures unless every decoder wraps them. The skeleton retry catches **any** `PxyError`, not specifically insufficient transforms; removing a skeleton does not prove the remaining geometry is correctly positioned. The pool implementation is omitted, so worker-crash, timeout and resource-exhaustion behavior cannot be reviewed.  
    **Evidence/test:** truncated/random PXY/TXY/XHR inputs, huge counts, worker termination, and short skeletons checked against a reference render. Inspect the complete pool code and decoder exception contracts.

17. **Sound, narrowly:** exact ordered block-key checking, exported leading-`#` escaping, changed-string NUL refusal, and the original-sector cap implement their stated policies; the supplied tests support these, not general runtime safety.

18. **The universal writer/read-back claim remains unproven from this material.**  
    `patch_iso`, recolor writers, font output, TOC validation and final image verification are not shown. `patch_copy` itself checks only the serial, despite describing reliance on build-specific loader rules.  
    **Evidence needed:** audit every writer entry point, executable-dependent rule call, extent/overlap check and metadata read-back. The successful three-range image diff does not establish those failure-path guarantees.
