ROUND 5: review a change set to a Python kit that extracts and patches a PS2 game's text (Xenosaga Episode II; USA and Japan discs and a demo) before it is merged and published. The work was done by an automated agent; its claims below were spot-checked independently on real discs (font rebuild byte-identical for both fonts on USA and Japan; an unedited text export re-imports as "no text changed"; the Japan export gives 17,225 inline strings), but most numbers are the agent's own.

WHAT IT CLAIMS
1. Regions: the build is read from SYSTEM.CNF; executable addresses are mapped through ELF program headers. Japan/demo keep dialogue inline in the script files (EUC), USA in separate text banks. Codec chosen: Python `euc_jis_2004` instead of `euc_jp`, because over all 94x94 two-byte codes 6,879 decode identically in both, 1,918 only in euc_jis_2004, none only in euc_jp; on the Japan corpus (15,536 non-ASCII strings) euc_jis_2004 round-trips all, euc_jp fails 73 (all lead byte 0xAD). Inline text import is same-size-or-shorter only.
2. Fonts: glyph cell lookup ported from the game's own function; USA single bytes >= 0x80 are drawn as Latin-1 via an EUC lead byte 0xA9 (cell = byte + 0x80), except a short list of two-byte EUC pairs; USA cannot draw kanji through its print function. Metric pair index = cell number; proportional glyph image at cell+576. font-extract -> font-rebuild is byte-identical on five discs.
3. text-import --grow: the script and its text bank are loaded into one fixed buffer (scenes: 0x20000 bytes, rule round2048(script)+round2048(bank) <= 131072; maps: 0x12000, rule align64(script)+round2048(bank) <= 73728). A grown bank is relocated to the end of the image (TOC sector/size and the volume size updated) only if it still fits that rule; all 465 retail pairs obey the rule. Not yet tested in the running game.
4. More text types: menu string tables (.txd, offset table rebuilt, in-slot only), movie subtitle files, Shift-JIS quest files (in-slot only), a battle string file (each string replaced in place, same length or shorter). Mutation tests on disc copies: 5 edits -> exactly 5 changed objects, no stray byte.
5. Subtitles: an earlier in-game test broke playback when one subtitle line grew by 52 characters. The earlier explanation ("text may grow only to the next 64-byte boundary because the decoder work area starts at align64(packed length)") does NOT match the code: the function that packs the cues returns the offset of the first NUL after the END OF THE FILE, so the work area moves with file size, and in the broken case it did not move at all. The true cause is unknown. The kit still enforces the 64-byte rule because it refuses the one case known to break, and says it is not a guarantee.

QUESTIONS
(a) Find bugs in the code below, especially anything that can write a corrupt disc image, lose data, or report success wrongly (TOC patching, relocation, the offset-table rebuild, in-place string replacement, escapes).
(b) Is the codec reasoning sound? What can go wrong using euc_jis_2004 for a 2004 game's EUC-JP text (e.g. JIS X 0213 mappings vs NEC row 13 / vendor characters at lead 0xAD, U+FF5E vs U+301C style differences, plane-2 0x8F sequences)? What test would settle it?
(c) For the subtitle breakage: given that a single line grew by 52 characters and playback then produced tens of thousands of "VIF invalid unpack" errors at 2 fps, what are the most likely real causes (per-line buffer, a fixed-size cue struct, a draw packet built on the stack, texture/sprite count limits), and what is the cheapest experiment matrix in an emulator to find the limit?
(d) Is keeping an unexplained 64-byte rule the right conservative choice, or should the tool refuse any growth of displayed subtitle text until the cause is known?
(e) The loader-buffer rules in 3 come from reading decompiled code only. What should be checked in the running game before --grow is advertised?

==== git diff (code only; docs and GUI omitted) ====
diff --git a/browse.py b/browse.py
index 0627a9b..18eb352 100644
--- a/browse.py
+++ b/browse.py
@@ -201,7 +201,8 @@ def decode_text_file(b: bytes) -> str:
     """EUC-JP or Shift-JIS, whichever decodes cleanly with fewer half-width
     kana (the mojibake tell); Latin-1 as the last resort."""
     best = None
-    for enc in ("euc_jp", "shift_jis"):
+    import sbl
+    for enc in (sbl.CODEC, "shift_jis"):
         try:
             t = b.decode(enc)
