# Release round (2026-11-10)

The fixes made after the translation review, and the model export wiring, were sent to five
models. Only GPT answered: the gateway returned "402 Payment Required" for the other four.
So this round is one reviewer, not a panel. Its answer is beside this file, unedited.

## Confirmed and fixed

| Finding | What was done |
|---|---|
| 2: an output that is a symlink was removed before the new file was moved in, leaving a gap in which a failure loses it | The unlink is gone; `os.replace` replaces the link itself. |
| 4: exporting into a folder that already holds edited text overwrote the edits | An export never overwrites a file that differs from what it would write; it says how many it kept. Checked on a real disc: an edited line survives a second export. |
| 5: object paths from the table of contents were not checked before becoming file paths; two objects could map to one file | Paths with `..`, empty or drive-like parts are refused; a second object for one file is left out and reported. |
| 6: the USA codec read past the end of a string that ends in a lead byte | A lead byte with nothing after it is one byte. |
| 8: a tree with files for objects the target disc lacks was applied without a word about them | Import says how many files had no object on this disc. Checked: a Disc 1 tree on Disc 2 applies the shared object and reports the 20 Disc 1 subtitle files. |
| 10: an index error when an edit changed the string list without losing a string | Guarded. |
| 13: the pixel-width bound for subtitles was silently dropped if a retail line could not be measured | Subtitle edits are refused on such an image instead. |
| 15, 16: a model was written under its final name directly, a failed decode left an older `.glb` beside it, and only the decoder's own errors were caught | Written as `.partial` and moved into place; a stale file is removed on failure; any error in one model is recorded and the rest go on. All 944 still export. |

## Accepted as limits

- **1, 3: the partial file has a fixed name and is not fsynced.** Two imports to one output at
  once can collide, and a power cut right after "done" can lose the file. The tool is run by
  one person on their own disc copies; not changed.
- **7: a non-blank glyph is not proof it is the right glyph.** The check shows the font can
  draw something at that code; which letter sits there was read by eye for the USA Latin-1
  range and compared by machine for the Japanese codes as a set.
- **9: `--rebase` applies the tree as it is; it does not merge.** Said so in MODDING.md.
- **11, 14: control syntax (`%d`, `$...`, quotes in quest files) is not validated.** A
  translator can still break a format string.
- **12: subtitle width 490-491 px would put a byte 0x0A in a line prefix.** Lines that wide
  are already refused by the retail-width bound (about 450 px on every disc).
- **18: not every writer was shown to the reviewer.** `patch_iso` was exercised on real discs
  instead (single and double relocation read back by an independent reader).

## Not verified by anyone

Nothing in this release was seen in the running game: grown text banks, the subtitle
bounds, the USA Latin-1 route, and whether models are mirrored.
