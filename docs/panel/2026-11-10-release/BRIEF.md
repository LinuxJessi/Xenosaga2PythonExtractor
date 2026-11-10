ROUND 7 (release round): the fixes made after your round-5 review of a PS2 text extraction/patching kit, before it is published. Find what is still wrong; say plainly if a fix is sound.

What changed since round 5 (the author's claims; spot-checked independently: self-tests pass, an unedited export re-imports as "no text changed", a symlink to the input is refused as output, typing "Touché ... señor" into a USA dialogue line and importing changes one object, the re-export equals the edited text, the image differs from the original in three byte ranges only, and the original image is untouched):
1. Writers never patch their input (same file by name, symlink or hard link is refused) and work on <out>.partial, moved into place only after every read-back.
2. One block parser: keys must be exactly the export's, once each, in order; a body line starting with '#' is a comment and a real leading '#' is exported as an escape; NUL refused; after an in-place edit the object is re-scanned and must list the same strings.
3. An unknown disc serial is read-only; executable-dependent readers check the executable's sha256.
4. The export writes a manifest (build, sha1 and size per object); import refuses a different build or a changed object unless --rebase.
5. Per-build text codec: on the USA build a lone byte 0xA0-0xFF is its Latin-1 character and only the EUC pairs the game's print function accepts are pairs; typed characters in changed strings are checked against the font (encodable, not a 0x8E/0x8F sequence, a non-fallback cell, a non-blank glyph in both font files).
6. Subtitles: besides the unexplained 64-byte rule, no line longer in bytes or wider in pixels (font metrics, a port of the game's width function) than the longest retail line on that disc, no more lines per cue than retail, the cue list may not change. Stated as bounds from retail, not proven limits. The line that broke playback in an earlier test was about 52 characters longer than the widest retail line, i.e. wider than the 640-pixel screen.
7. Menus and quest files are capped at the sector count the loader read originally.
8. `browse` exports every 3D model as .glb (a pool of worker processes; a model whose skeleton file is too short is written without it).

Questions: (a) bugs in the code below, above all anything that can corrupt an image, lose text, or leave a partial/incorrect output looking complete; (b) holes in the same-file check or the partial-file publish (crash windows, existing output, permissions, cross-device rename); (c) is the Latin-1/EUC-pair split for the USA build sound as coded, and can a typed character be accepted yet drawn wrong; (d) is binding an import to the export's manifest sound (what can still go wrong with --rebase or a Disc 1 tree applied to Disc 2); (e) anything in the browse/model wiring that fails badly on a damaged dump.

==== git diff (code since round 5) ====
diff --git a/cli.py b/cli.py
