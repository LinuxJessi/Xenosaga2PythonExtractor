"""textpack.py — Episode II's text as an editable UTF-8 tree, and back.

``export_text`` writes one file per text object under
``OUT/chainN/<in-game path>``, and ``OUT/xs2-text-export.json``, the manifest
that ties the tree to the disc it came from::

    kind       on the disc                        in the tree       form
    bank       cf\\en\\*.sbl, model\\map\\en\\*.sbl   X.sbl.txt         ### NNNN blocks       (USA)
    inline     cf\\*.sb, model\\map\\*.bin, ...     X.inline.txt      ### @0xOFFSET blocks  (Japan, demo)
    menu       yosikawa\\*.txd                    X.txd.txt         ### NNNN blocks
    battle     yamamoto\\batdat.us | .bin         batdat.us.txt     ### S<sec> @<off> blocks
    subtitle   movie\\en\\*.txt | movie\\*.txt      X.sub.txt         the file itself
    quest      iwase\\*.txt (Shift-JIS)           X.sjis.txt        the file itself

**Block files.** The first line is a ``#`` header. Each ``### KEY`` line opens
one string; the text is the line after it::

    # cf\\en\\sc1010.sbl  chain0  7 strings  stored 284 bytes, slot 2048 bytes
    ### 0004
    [Canaan]What is this thing?

* Inside a block, a line that starts with ``#`` is a COMMENT and is not text
  (the export writes notes and reading hints that way: ``#sjis:`` / ``#euc:``
  for Japanese leftovers, a battle banner, a tutorial command, the slot
  placeholder). To start a line of text with
  ``#``, write ``⟦23⟧``: the export does this itself, so ``#1`` on the disc
  reads ``⟦23⟧1`` in the tree. A ``#`` anywhere else in a line is plain.
* ``⟦XX⟧`` is a raw byte. Line breaks inside a string are the game's own
  code, ``\\n`` typed as backslash + n; a raw 0x0A byte reads ``⟦0A⟧``. If you
  type a real line break inside a block the lines are joined with ``\\n``;
  empty lines at the end of a block are dropped.
* Every block of the export must come back: same keys, same order, none
  twice, none missing, none new. Anything else is refused.
* ``[Speaker]``, ``$zoom13;``, ``$col...;``, ``%p`` are the game's codes: keep them.

**What the characters mean depends on the build** (``textcodec.py``): on the
USA discs a letter like ``é`` is the Latin-1 byte 0xE9, on the Japanese discs
text is EUC-JP. A character typed into a CHANGED string is checked against
the font of the disc being patched and refused, with the reason, when the
game cannot draw it; an explicit ``⟦XX⟧`` escape is never checked.

**The manifest** records the build and, for every exported object, the sha1
of its bytes on the disc. ``import_text`` refuses a tree without one, a disc
of another build, and any object that is not the one the text was exported
from (an already patched image, another revision), unless ``rebase`` is set
(``text-import --rebase``). A tree exported from Disc 1 imports into Disc 2 of
the same build: chain 0 is the same there and each object is still checked.

**What an edit may do**, per kind (``docs/MODDING.md`` has the evidence):

* bank: strings grow and shrink, the bank is rebuilt. It may leave its slot
  only with ``grow`` and never past the room its loader has (``bank_budget``,
  USA only: the rule is read from the USA executable).
* inline: each string in place, same length or shorter.
* battle: in place; on the USA build a longer string moves the rest of its
  section down through the string fields OV01's getters read
  (``smallfiles.batdat_rebuild``), the file within its sectors.
* subtitle: a file in the tree for a movie that has none on the disc is
  refused (``_new_subtitle_files``: the decoder's block has no room for it).
* menu, quest: rebuilt or rewritten; the file may not need more sectors than
  the original (that is what the loader read; the slot could be larger).
  USA MainMenu: a string with the memory-card slot placeholder keeps it where
  FUN_00238038's walk finds it (``slot_problem``). Tutorial.txd: the entries
  FUN_00215f10 runs as commands or skips may not change, and a page of text
  may not become one (``tutorial_role``). USA quest files: typed characters
  go through the reader's pair conversion (``textcodec.UsQuestCodec``).
* bank strings that scripts pass to ``bat.namePut`` (battle banners): marked in
  the export; ``banner_report`` refuses one FUN_002353b8 would measure past its
  end, and notes copies edited unalike and banners wider than any retail one.
* subtitle: ``moviesub.check_edit``; may be relocated with ``grow``.

Nothing is written unless every file passes; the output image is produced by
``repack.patch_copy`` (a complete, verified copy or nothing).

    python textpack.py export DISC.iso OUTDIR
    python textpack.py import DISC.iso OUTDIR MODDED.iso [--grow] [--rebase]
    python textpack.py --self-test
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import region                                                     # noqa: E402
import sbl                                                        # noqa: E402
import textcodec                                                  # noqa: E402
from chains import CHAINS, ChainReader, available_chains          # noqa: E402
from iso9660 import IsoImage, SECTOR                              # noqa: E402
from textcodec import TextError                                   # noqa: E402
from toc import parse_toc                                         # noqa: E402

MANIFEST = "xs2-text-export.json"
FORMAT = 2

SCENE_BUFFER = 0x20000      # the static script buffer at USA 0x3C6DC0 (FUN_001eece0)
MAP_BUFFER = 0x12000        # the block FUN_001df928(0x12000) returns (FUN_001e00f0)


def _round(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def _allocations(entries) -> Dict[str, int]:
    starts = sorted({e.sector for e in entries})
    nxt = {s: n for s, n in zip(starts, starts[1:])}
    out = {}
    for e in entries:
        end = nxt.get(e.sector)
        out[e.path] = ((end - e.sector) * SECTOR if end is not None
                       else (e.size + SECTOR - 1) // SECTOR * SECTOR)
    return out


def bank_budget(bank_path: str, sizes: Dict[str, int], build: str = "us") -> Tuple[Optional[int], str]:
    """(largest .sbl byte size the loader has room for, why) for a bank path on a
    build. ``sizes`` maps lower-case TOC path -> stored size. (None, reason) when
    no loader rule is known: any build but USA (the two rules were read from the
    USA executable), a bank outside the build's language folder, a missing script."""
    if build != "us":
        return None, f"no loader rule has been read for the {build} build"
    low = bank_path.lower()
    head, _, name = low.rpartition("\\")
    parent, _, lang = head.rpartition("\\")
    stem = name.rsplit(".", 1)[0]
    if lang != region.US.lang_dir:
        return None, f"the USA loaders read text banks from '{region.US.lang_dir}\\' only"
    if parent == "cf":
        script, buf, align, who = f"cf\\{stem}.sb", SCENE_BUFFER, SECTOR, "FUN_001eece0"
    elif parent == "model\\map":
        script, buf, align, who = f"model\\map\\{stem}.bin", MAP_BUFFER, 64, "FUN_001e00f0"
    else:
        return None, "no loader is known for a bank at this path"
    if script not in sizes:
        return None, f"its script {script} is not in the table of contents"
    used = _round(sizes[script], align)
    room = max(0, (buf - used) // SECTOR * SECTOR)  # the bank is read in whole sectors
    return room, (f"{who} loads {script} ({sizes[script]} bytes, {used} with rounding) and then this bank "
                  f"into one {buf}-byte buffer")


# ---------------------------------------------------------------- block files

def parse_blocks(text: str, marker: str = "### ") -> List[Tuple[str, str]]:
    """(header after the marker, body) of every block. Comment lines (``#...``) are
    dropped, trailing empty lines too, and the lines left are joined with the
    game's ``\\n``."""
    out: List[Tuple[str, List[str]]] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith(marker):
            out.append((line[len(marker):].strip(), []))
        elif line.startswith("#"):
            continue
        elif out:
            out[-1][1].append(line)
        elif line.strip():
            raise TextError(f"text before the first '{marker.strip()}' block: {line[:40]!r}")
    res = []
    for head, b in out:
        while len(b) > 1 and b[-1] == "":
            b.pop()
        res.append((head, "\\n".join(b)))
    return res


def keyed_bodies(text: str, expected: List[str], key_tokens: int = 1) -> List[str]:
    """The bodies of a block file, in the order of ``expected`` keys. The file must
    hold exactly those keys, each once, in that order."""
    blocks = parse_blocks(text)
    keys = [" ".join(h.split()[:key_tokens]) for h, _ in blocks]
    if keys != expected:
        seen, want = set(), set(expected)
        dup = sorted({k for k in keys if k in seen or seen.add(k)})
        missing = [k for k in expected if k not in set(keys)]
        unknown = sorted(k for k in set(keys) if k not in want)
        why = []
        if dup:
            why.append(f"{len(dup)} key(s) appear twice ({', '.join(dup[:3])})")
        if missing:
            why.append(f"{len(missing)} block(s) are missing ({', '.join(missing[:3])})")
        if unknown:
            why.append(f"{len(unknown)} key(s) are not in the export ({', '.join(unknown[:3])})")
        if not why:
            first = next(i for i, (a, b) in enumerate(zip(keys, expected)) if a != b)
            why.append(f"the blocks are out of order from '### {keys[first]}' (expected '### {expected[first]}')")
        raise TextError("; ".join(why) + ". Every '###' block of the export must be there once, in its order")
    return [b for _, b in blocks]


# -------------------------------------------------------------------- context

class Context:
    """One disc being exported or imported: its build, codecs, and (lazily) the font
    tables and subtitle bounds that the checks on typed text need."""

    def __init__(self, iso: IsoImage, need_executable: bool = False):
        self.iso = iso
        self.profile = (region.require_executable(iso, "text-import") if need_executable
                        else region.region_of(iso))
        self.build = self.profile.name
        self.codec = textcodec.for_build(self.build)
        self.subcodec = textcodec.subtitle_codec(self.build)
        self.sjis = textcodec.SjisCodec()
        self.usquest = textcodec.UsQuestCodec()
        self._adr = self._fonts = self._limits = None

    def _toc(self, chain: int):
        reader = ChainReader(self.iso, chain)
        return reader, parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])

    def addressing(self):
        import font
        if self._adr is None:
            try:
                self._adr = font.Addressing.from_iso(self.iso)
                reader, entries = self._toc(0)
                byp = {e.path.lower(): e for e in entries}
                self._fonts = {n: font.Font.from_bytes(reader.read(byp[n].byte_offset, byp[n].size))
                               for n in self.profile.fonts if n in byp}
            except font.FontError as e:
                raise TextError(f"the font tables of this disc cannot be read, so typed text cannot be "
                                f"checked: {e}")
            if not self._fonts:
                raise TextError("this disc has no font file to check typed text against")
        return self._adr, self._fonts

    def check_typed(self, codec, text: str) -> Optional[str]:
        if all(ch == "" or ord(ch) < 0x80 for ch, _ in textcodec.tokens(text)):
            return None
        adr, fonts = self.addressing()
        return textcodec.check_typed(codec, text, adr, fonts)

    def check_lines(self, codec, old_text: str, new_text: str, skip: Callable[[str], bool] = lambda ln: False
                    ) -> Optional[str]:
        """check_typed over the lines of a whole-file text that are not in the original."""
        old = set(old_text.split("\n"))
        for n, ln in enumerate(new_text.split("\n"), 1):
            if ln in old or skip(ln):
                continue
            why = self.check_typed(codec, ln)
            if why:
                return f"line {n}: {why}"
        return None

    def measure(self):
        """A factory of width functions with the state a subtitle file starts in
        ($bg3;$prop1;$han1;$xx0,24;$xx1,8;$xx2,0; at USA 0x6883F0, the same string in
        the Japanese executable)."""
        adr, fonts = self.addressing()
        metrics = next(iter(fonts.values())).metrics
        return lambda: adr.measurer(metrics, han=True, prop=True, xx1=8).width

    def subtitle_limits(self):
        import moviesub
        if self._limits is None:
            files = []
            for chain in available_chains(self.iso):
                reader, entries = self._toc(chain)
                files += [reader.read(e.byte_offset, e.size) for e in entries
                          if region.is_movie_subtitle(e.path.lower()) and not e.compressed]
            try:
                self._limits = moviesub.limits_of(files, self.measure())
            except moviesub.SubtitleError as e:
                raise TextError(f"a retail subtitle file of this disc does not parse ({e})")
            except ValueError as e:             # a line the width function cannot follow: without the
                # pixel bound the check would be weaker than promised, so subtitles are not editable here
                raise TextError(f"a subtitle line of this disc cannot be measured ({e}); subtitle edits are "
                                f"refused on this image")
        return self._limits


# ---------------------------------------------------------------------- kinds

def _encode(ctx: Context, codec, key: str, old: bytes, body: str) -> bytes:
    """One block's bytes. An unchanged body gives the original bytes back untouched;
    a changed one is encoded, may not hold NUL, and has its typed characters checked."""
    if codec.to_text(old) == body:
        return old
    try:
        new = codec.from_text(body)
    except TextError as e:
        raise TextError(f"### {key}: {e}")
    if b"\x00" in new:
        raise TextError(f"### {key}: a NUL byte (⟦00⟧) inside a string; the game would stop reading there")
    if new == old:
        return old
    why = ctx.check_typed(codec, body)
    if why:
        raise TextError(f"### {key}: {why}. Write ⟦XX⟧ escapes if these bytes are meant")
    return new


def _jp_hint(s: bytes) -> str:
    """A reading hint for bytes that are Japanese developer text in a file of the USA build:
    ``#sjis: ...`` or ``#euc: ...`` (Shift-JIS tried first), when the bytes decode and
    nearly all of what they read as is full-width kana, kanji or full-width signs (so a
    Latin-1 line with accents, and EUC bytes that Shift-JIS would read as half-width kana,
    get no false hint). A '#' line is a comment: an unedited export still imports as no change."""
    if not any(b >= 0x80 for b in s):
        return ""
    for tag, enc in (("sjis", "shift_jis"), ("euc", sbl.CODEC)):
        try:
            t = s.decode(enc)
        except UnicodeDecodeError:
            continue
        jp = sum(0x3040 <= ord(c) <= 0x30FF or 0x4E00 <= ord(c) <= 0x9FFF or 0x3000 <= ord(c) <= 0x303F
                 or 0xFF01 <= ord(c) <= 0xFF5E for c in t)
        if jp >= 2 and 2 * jp >= len(t.replace(" ", "")):
            return f"#{tag}: " + t.replace("\n", " ").replace("\r", " ")
    return ""


# The memory-card slot placeholder (USA SLUS_208.92). FUN_00238038 walks a menu string as the
# print function would (a byte from 0x80 up steps two bytes, any other one, NUL ends it) and at the
# first pair that is one of the three placeholders read gp-relative (82 4F at 0x69DA30, A3 B0 at
# 0x69DA38, '##' at 0x69DA40), or a digit it stamped before (07 then a byte of '12' at 0x69DA48),
# writes 07 and the slot's digit. FUN_002381a8 calls it on MainMenu 0x21F, 0x220, 0x224, 0x227 and
# 0x22F, FUN_0023b210 on 0x22F. The USA MainMenu.txd writes the placeholder as A3 B0 ('£°').
SLOT_PLACEHOLDERS = (b"\x82\x4f", b"\xa3\xb0", b"##")
SLOT_STAMPED = (b"\x071", b"\x072")
SLOT_MENU = "yosikawa\\mainmenu.txd"


def slot_markers(s: bytes) -> List[int]:
    """Offsets of the slot placeholders FUN_00238038 finds in ``s``, walking it as it does
    (it stamps the first; the rest are counted by the same walk)."""
    out, i, n = [], 0, len(s)
    while i < n and s[i]:
        if s[i:i + 2] in SLOT_PLACEHOLDERS or s[i:i + 2] in SLOT_STAMPED:
            out.append(i)
        i += 2 if s[i] >= 0x80 else 1
    return out


def slot_problem(old: bytes, new: bytes, show: Callable[[bytes], str]) -> Optional[str]:
    """Why an edited menu string loses the memory-card slot placeholder of its original, or None."""
    want = slot_markers(old)
    if not want:
        return None
    got = slot_markers(new)
    if len(got) == len(want):
        return None
    mark = show(old[want[0]:want[0] + 2])
    hidden = [k for k in range(len(new) - 1) if new[k:k + 2] in SLOT_PLACEHOLDERS and k not in got]
    if len(got) < len(want) and hidden:
        k = hidden[0]
        return (f"the slot placeholder {mark} is there, but the byte before it ({show(new[k - 1:k])}, 0x{new[k - 1]:02X}) "
                f"is from 0x80 up, so FUN_00238038 reads it with the placeholder's first byte as one pair and never "
                f"finds the placeholder: the game would not write the memory-card slot number. Put a space or "
                f"another plain ASCII character right before {mark}")
    return (f"the original holds {len(want)} memory-card slot placeholder(s) {mark}, the edit {len(got)}: the game "
            f"writes the slot number over the first one (FUN_00238038), so keep {mark} where the number belongs, "
            f"once")


# The Skill Tutorial script (yosikawa\Tutorial.txd, read through FUN_0022d118). FUN_00215f10 runs an
# entry by its first byte: '%' is a command chosen by the next byte(s) (%p, %tNN, %c+, %l0, ...),
# '*' calls FUN_00215de8, an empty entry and one starting with '#' are skipped; any other entry is
# the text the tutorial shows.
TUTORIAL = "yosikawa\\tutorial.txd"


def tutorial_role(s: bytes) -> str:
    if not s:
        return "empty"
    return {0x25: "command", 0x2A: "command", 0x23: "skipped"}.get(s[0], "text")


TUTORIAL_NOTE = {"command": "# tutorial command, not shown (FUN_00215f10 runs it); leave it as is",
                 "empty": "# empty: the tutorial skips it (FUN_00215f10); leave it empty",
                 "skipped": "# starts with '#': the tutorial skips it (FUN_00215f10); leave it as is"}


class Kind:
    name = ""
    relocatable = False
    why_fixed = ""

    def match(self, low: str, head: bytes) -> bool:
        raise NotImplementedError

    def tree_path(self, root: Path, chain: int, path: str) -> Path:
        raise NotImplementedError

    def export(self, ctx: Context, path: str, data: bytes, info: dict) -> str:
        raise NotImplementedError

    def rebuild(self, ctx: Context, path: str, data: bytes, text: str) -> bytes:
        raise NotImplementedError

    def count(self, ctx: Context, data: bytes) -> int:
        return 0

    def limit(self, ctx: Context, e, data: bytes, new: bytes, alloc: int, sizes, grow: bool) -> Optional[str]:
        """Why ``new`` may not replace the object, or None. The default: the file may
        not need more sectors than the original did."""
        if _round(len(new), SECTOR) > _round(len(data), SECTOR) or len(new) > alloc:
            return (f"{len(new)} bytes need {_round(len(new), SECTOR) // SECTOR} sectors, the original "
                    f"({len(data)} bytes) {_round(len(data), SECTOR) // SECTOR}; this file may not grow past "
                    f"the sectors the game read ({self.why_fixed}). Shorten other text in the same file")
        return None


def _tree(root: Path, chain: int, path: str) -> Path:
    parts = path.replace("/", "\\").split("\\")
    if any(p in ("", ".", "..") or ":" in p for p in parts):     # a damaged table of contents must not
        raise TextError(f"object path {path!r} cannot be a file path")   # write outside the export folder
    return root / f"chain{chain}" / Path(*parts)


class Bank(Kind):
    name = "bank"
    relocatable = True

    def match(self, low, head):
        return low.endswith(".sbl") and head[:4] == sbl.MAGIC

    def tree_path(self, root, chain, path):
        return _tree(root, chain, path).with_suffix(".sbl.txt")

    def count(self, ctx, data):
        return len(sbl.parse(data))

    def export(self, ctx, path, data, info):
        strings = sbl.parse(data)
        room, _why = bank_budget(path, info["sizes"], ctx.build)
        lines = [f"# {path}  chain{info['chain']}  {len(strings)} strings  "
                 f"stored {len(data)} bytes, slot {info['alloc']} bytes"
                 + (f", loader room {room} bytes (--grow)" if room is not None else "")]
        banners = info.get("banners", {})
        for i, s in enumerate(strings):
            lines.append(f"### {i:04d}")
            t = ctx.codec.to_text(s)
            if i in banners:
                lines.append("# battle banner (bat.namePut)" + (f"; the same name is in {banners[i]} banks: give every "
                                                              f"copy the same text" if banners[i] > 1 else ""))
            hint = _jp_hint(s) if ctx.build == "us" else ""
            if hint:
                lines.append(hint)
            lines.append(t)
        return "\n".join(lines) + "\n"

    def rebuild(self, ctx, path, data, text):
        old = sbl.parse(data)
        keys = [f"{i:04d}" for i in range(len(old))]
        new = [_encode(ctx, ctx.codec, k, o, b) for k, o, b in zip(keys, old, keyed_bodies(text, keys))]
        if new == old:
            return data
        blob = sbl.build(new, data)
        if sbl.parse(blob) != new:
            raise TextError("the rebuilt bank does not read back as written")
        return blob

    def limit(self, ctx, e, data, new, alloc, sizes, grow):
        room, why = bank_budget(e.path, sizes, ctx.build)
        if room is not None and len(new) > room:
            return (f"rebuilt size {len(new)} is {len(new) - room} bytes past the {room} bytes the game has "
                    f"room for ({why}); shorten the text, relocating cannot help")
        if len(new) > alloc:
            if room is None:
                return f"rebuilt size {len(new)} exceeds its {alloc}-byte slot and cannot be relocated: {why}"
            if not grow:
                return (f"rebuilt size {len(new)} exceeds its {alloc}-byte slot (over by {len(new) - alloc}; "
                        f"--grow relocates it to the end of the image, loader room {room} bytes)")
        return None


class Inline(Kind):
    """Japan and the demo: strings inside the script (``sb.py``). A string is listed
    when a pushtext names it or a variable slot holds its offset ("table")."""
    name = "inline"

    def match(self, low, head):
        import sb
        return low.endswith((".sb", ".bin")) and head[:4] == sb.MAGIC

    def tree_path(self, root, chain, path):
        return _tree(root, chain, path).with_suffix(".inline.txt")

    def _script(self, data):
        import sb
        s = sb.parse(data)
        return s if s.text_kind in ("inline", "mixed") else None

    def count(self, ctx, data):
        s = self._script(data)
        return len(s.text_offsets()) if s else 0

    def export(self, ctx, path, data, info):
        s = self._script(data)
        if s is None:
            return ""
        lines = [f"# {path}  chain{info['chain']}  inline strings (in place: same length or shorter, never longer)"]
        for o, how in s.text_offsets():
            raw = s.inline_text(o)
            lines += [f"### @{o:#x} max {len(raw)} bytes" + ("  table" if how == "table" else ""),
                      ctx.codec.to_text(raw)]
        return "\n".join(lines) + "\n"

    def rebuild(self, ctx, path, data, text):
        import sb
        s = self._script(data)
        if s is None:
            raise TextError("this script has no inline text")
        offs = s.text_offsets()
        keys = [f"@{o:#x}" for o, _ in offs]
        glob_off = struct.unpack_from("<I", data, 0x18)[0]
        out = bytearray(data)
        for (o, how), key, body in zip(offs, keys, keyed_bodies(text, keys)):
            old = s.inline_text(o)
            new = _encode(ctx, ctx.codec, key, old, body)
            if new == old:
                continue
            if len(new) > len(old):
                raise TextError(f"### {key} grows {len(old)} -> {len(new)} bytes; inline strings are replaced "
                                f"in place and may not get longer")
            a = glob_off + o
            out[a:a + len(old)] = new + bytes(len(old) - len(new))
        if bytes(out) == data:
            return data
        again = sb.parse(bytes(out))
        if again.text_offsets() != offs:
            lost = sorted(set(offs) - set(again.text_offsets()))
            raise TextError(f"the edit would make {len(lost)} string(s) disappear from the next export "
                            + (f"(### @{lost[0][0]:#x}: a string that only a table points at may not be emptied)"
                               if lost else "(the script's string list changed)"))
        return bytes(out)

    def limit(self, ctx, e, data, new, alloc, sizes, grow):
        return None if len(new) == len(data) else "the script changed size"


def txd_parse(data: bytes) -> Tuple[List[bytes], bytes]:
    """(strings, tail) of a .txd: u32 offsets (count = first offset / 4), then the
    NUL-terminated strings back to back in table order; ``tail`` is whatever
    follows the last string (zero bytes, or nothing)."""
    if len(data) < 4:
        raise TextError("too short for a .txd")
    first = struct.unpack_from("<I", data, 0)[0]
    n = first // 4
    if first % 4 or not 0 < first <= len(data):
        raise TextError("not a .txd (first offset)")
    offs = struct.unpack_from(f"<{n}I", data, 0)
    strings, pos = [], first
    for o in offs:
        if o != pos or o >= len(data):
            raise TextError(f"not a plain .txd: string at {o:#x}, expected {pos:#x}")
        end = data.find(b"\x00", o)
        if end < 0:
            raise TextError("a .txd string runs off the end")
        strings.append(data[o:end])
        pos = end + 1
    return strings, data[pos:]


def txd_build(strings: List[bytes], tail: bytes = b"") -> bytes:
    offs, body = [], bytearray()
    for s in strings:
        if b"\x00" in s:
            raise TextError("a string may not contain NUL")
        offs.append(4 * len(strings) + len(body))
        body += s + b"\x00"
    return struct.pack(f"<{len(strings)}I", *offs) + bytes(body) + tail


class Menu(Kind):
    name = "menu"
    why_fixed = "the game reads menu text into static buffers of fixed size"

    def match(self, low, head):
        return low.endswith(".txd")

    def tree_path(self, root, chain, path):
        return _tree(root, chain, path).with_suffix(".txd.txt")

    def count(self, ctx, data):
        return len(txd_parse(data)[0])

    def export(self, ctx, path, data, info):
        strings, tail = txd_parse(data)
        if txd_build(strings, tail) != data or any(tail):
            raise TextError("does not rebuild to the same bytes; left alone")
        low = path.lower().replace("/", "\\")
        tutorial = low == TUTORIAL
        slots = ctx.build == "us" and low == SLOT_MENU
        lines = [f"# {path}  {len(strings)} strings  (menu text; keep %p, %d and the game's codes)"]
        if tutorial:
            lines.append("# The Skill Tutorial's script: FUN_00215f10 runs the entries in order. An entry that starts "
                         "with % or * is a command, an empty one or one starting with # is skipped, every other one "
                         "is a page of text. Edit the text pages only; a page may not be emptied or start with % * #")
        for i, s in enumerate(strings):
            lines.append(f"### {i:04d}")
            if tutorial and tutorial_role(s) != "text":
                lines.append(TUTORIAL_NOTE[tutorial_role(s)])
            if slots and slot_markers(s):
                m = slot_markers(s)[0]
                mark = ctx.codec.to_text(s[m:m + 2])
                lines.append(f"# {mark} = the memory-card slot number, filled in by the game (FUN_00238038); keep it "
                             f"as written, with a plain ASCII character (not an accented letter) right before it")
            hint = _jp_hint(s) if ctx.build == "us" else ""
            if hint:
                lines.append(hint)
            lines.append(ctx.codec.to_text(s))
        return "\n".join(lines) + "\n"

    def rebuild(self, ctx, path, data, text):
        old, tail = txd_parse(data)
        keys = [f"{i:04d}" for i in range(len(old))]
        new = [_encode(ctx, ctx.codec, k, o, b) for k, o, b in zip(keys, old, keyed_bodies(text, keys))]
        if new == old:
            return data
        low = path.lower().replace("/", "\\")
        for k, o, n in zip(keys, old, new):
            if n == o:
                continue
            if low == TUTORIAL:
                if tutorial_role(o) != "text":
                    raise TextError(f"### {k}: this entry is not text: FUN_00215f10 runs it as a tutorial command "
                                    f"({ctx.codec.to_text(o) or 'empty: skipped'}), so changing it changes what the "
                                    f"Skill Tutorial does. Leave it as exported")
                if tutorial_role(n) != "text":
                    raise TextError(f"### {k}: a page of tutorial text may not "
                                    + ("be emptied" if not n else f"start with '{chr(n[0])}'")
                                    + ": FUN_00215f10 would " + ("skip it" if tutorial_role(n) != "command"
                                                                 else "run it as a command") + " instead of showing it")
            if ctx.build == "us" and low == SLOT_MENU:
                why = slot_problem(o, n, ctx.codec.to_text)
                if why:
                    raise TextError(f"### {k}: {why}")
        blob = txd_build(new, tail)
        if txd_parse(blob) != (new, tail):
            raise TextError("the rebuilt menu file does not read back as written")
        return blob


class Battle(Kind):
    name = "battle"
    why_fixed = "the game loads it into a static buffer"

    def match(self, low, head):
        return low in ("yamamoto\\batdat.us", "yamamoto\\batdat.bin")

    def tree_path(self, root, chain, path):
        p = _tree(root, chain, path)
        return p.with_name(p.name + ".txt")            # batdat.us.txt / batdat.bin.txt

    def count(self, ctx, data):
        import smallfiles
        return len(smallfiles.batdat_raw_strings(data))

    def export(self, ctx, path, data, info):
        import smallfiles
        rows = smallfiles.batdat_raw_strings(data)
        if ctx.build == "us":
            room = _round(len(data), SECTOR) - len(data)
            grow = ", ".join(str(si) for si in smallfiles.BATDAT_STRING_FIELDS)
            lines = [f"# {path}  {len(rows)} strings. 'max N' is a string's own bytes. A string of sections {grow} "
                     f"may be longer: the strings after it in its section move down (the file has {room} spare "
                     f"bytes in its last sector). Section 17 is in place only"]
        else:
            lines = [f"# {path}  {len(rows)} strings  (replaced in place: same length or shorter, never longer)"]
        for si, o, raw in rows:
            lines += [f"### S{si:02d} @{o:#06x} max {len(raw)} bytes", ctx.codec.to_text(raw)]
        return "\n".join(lines) + "\n"

    def rebuild(self, ctx, path, data, text):
        import smallfiles
        rows = smallfiles.batdat_raw_strings(data)
        keys = [f"S{si:02d} @{o:#06x}" for si, o, _ in rows]
        new = {}
        want = []
        for (si, o, raw), key, body in zip(rows, keys, keyed_bodies(text, keys, key_tokens=2)):
            b = _encode(ctx, ctx.codec, key, raw, body)
            want.append((si, o, b))
            if b != raw:
                new[(si, o)] = b
        if not new:
            return data
        try:
            blob = smallfiles.batdat_rebuild(data, new, grow=ctx.build == "us")
        except smallfiles.SmallFileError as e:
            raise TextError(str(e))
        if len(blob) != len(data):                     # strings moved: the next export finds the same strings
            if sorted((si, r) for si, _, r in smallfiles.batdat_raw_strings(blob)) != sorted(
                    (si, b) for si, _, b in want):
                raise TextError("the edit would change which strings the next export finds: a string that "
                                "grows must keep its first byte printable, and the strings after it must still be "
                                "found where they move to")
            return blob
        if smallfiles.batdat_raw_strings(blob) != want:
            got = {(si, o) for si, o, _ in smallfiles.batdat_raw_strings(blob)}
            lost = [k for k, (si, o, _) in zip(keys, rows) if (si, o) not in got]
            raise TextError(f"the edit would change which strings the next export finds"
                            + (f" ({len(lost)} would disappear, the first ### {lost[0]})" if lost else "")
                            + ": a section is recognised by the first byte of its first string, which must stay "
                              "a printable ASCII or EUC byte")
        return blob

    def limit(self, ctx, e, data, new, alloc, sizes, grow):
        """In place, or (USA) grown within the sectors the game reads: OV01 FUN_00a81a70 loads the file with
        FUN_00a83dc0 -> FUN_0019c1e0, which reads the TOC size rounded up to whole sectors into the static
        buffer at 0x61D040. The next address any decompiled code names after it is 0x631140, so the
        buffer may well be larger, but its size was not read; the file keeps to its sectors."""
        if len(new) == len(data):
            return None
        if ctx.build != "us":
            return "the battle database changed size"
        if _round(len(new), SECTOR) > _round(len(data), SECTOR) or len(new) > alloc:
            return (f"the longer battle strings make the file {len(new)} bytes; it may hold "
                    f"{min(_round(len(data), SECTOR), alloc)} ({len(new) - min(_round(len(data), SECTOR), alloc)} "
                    f"over): the game reads whole sectors of the file into a static buffer whose size is not "
                    f"known. Shorten other battle strings")
        return None


def quest_skeleton(text: str) -> Optional[str]:
    """A quest file with the contents of every "..." removed: the record numbers,
    braces, field names and ruler lines. None when the quotes do not pair up."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        out.append(c)
        i += 1
        if c == '"':
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            if i >= n:
                return None
            out.append('"')
            i += 1
    return "".join(out)


# The USA loaders (FUN_0022ee08 QuestMes, FUN_0022f000 Citizen, FUN_00232160 Treasure) read a fixed
# number of records into a zeroed buffer of fixed-size records (0x195D8 = 36 x 0xB46, 0x29FE =
# 50 x 0xD7, 0xBE2 = 18 x 0xA9) with FUN_0022ec18, which finds each field by name, then copies the
# quoted text with no length check and no terminator of its own: a field holds its size minus one
# byte, the rest is the zeroed buffer's NUL. In the copy, CR LF is skipped, \n is one byte (0x0A), \"
# is a quote, any other backslash pair is dropped, a byte from 0x80 up takes two bytes (FUN_0023ebb8
# converts the pair in place), and a lone LF is copied as a line break.
QUEST_LAYOUTS = {
    "iwase\\questmes.txt": (0x24, 0xB46, (("Subject", 0x000), ("Locate", 0x01F), ("Client", 0x030),
                                         ("MesIPG", 0x041), ("MesSCD", 0x142), ("MesFLD", 0x243),
                                         ("UkunSCD", 0x344), ("UkunFLD", 0x745))),
    "iwase\\citizen.txt": (50, 0xD7, (("Name", 0x00), ("Sex", 0x11), ("Age", 0x18), ("Hobby", 0x29),
                                      ("Profile", 0x40))),
    "iwase\\treasure.txt": (0x12, 0xA9, (("Item", 0x00), ("Locate", 0x15), ("Expl", 0x30))),
}


def _quest_name_match(pat: bytes, d: bytes, p: int) -> bool:
    """FUN_0024f480: the pattern ('Name:%s\\n') matches when the text agrees up to its '%'."""
    i, n = 0, len(d)
    while True:
        c2 = pat[i]
        if c2 == 0x0A:
            return p < n and d[p] == 0x0A
        if p >= n or d[p] != c2:
            return c2 == 0x25
        p += 1
        i += 1


def quest_fields(data: bytes, layout) -> List[Tuple[int, str, int, int]]:
    """(record, field, bytes the game copies, field size) for every field the loader reads,
    walking the file as FUN_0022ec18 does. TextError when a field cannot be found."""
    nrec, rsize, fields = layout
    out, p, n = [], 0, len(data)
    for r in range(nrec):
        for k, (name, off) in enumerate(fields):
            size = (fields[k + 1][1] if k + 1 < len(fields) else rsize) - off
            pat = (name + ":%s\n").encode()
            while True:
                while p < n and data[p] != pat[0]:
                    p += 1
                if p >= n:
                    raise TextError(f"record {r + 1}: the game would not find its field {name}: and read past "
                                    f"the end of the file")
                if _quest_name_match(pat, data, p):
                    break
                p += 1
            while p < n and data[p] != 0x22:
                p += 1
            p += 1
            length = 0
            while True:
                if p >= n:
                    raise TextError(f"record {r + 1} {name}: the text has no closing quote")
                c = data[p]
                if c == 0x22:
                    p += 1
                    break
                if c == 0x0D and p + 1 < n and data[p + 1] == 0x0A:
                    p += 2
                elif c == 0x5C:
                    length += p + 1 < n and data[p + 1] in (0x22, 0x6E)
                    p += 2
                elif c < 0x80:
                    length += 1
                    p += 1
                else:
                    length += 2
                    p += 2
            out.append((r, name, length, size))
    return out


class Quest(Kind):
    name = "quest"
    why_fixed = ("its loader allocates by the file's size from the game's heap, whose room has not been read; "
                 "each text also has a fixed field size")

    def match(self, low, head):
        low = low.replace("/", "\\")
        return low.startswith("iwase\\") and low.endswith(".txt") and low.count("\\") == 1

    def tree_path(self, root, chain, path):
        return _tree(root, chain, path).with_suffix(".sjis.txt")

    @staticmethod
    def _layout(path):
        return QUEST_LAYOUTS.get(path.lower().replace("/", "\\"))

    def _codec(self, ctx, path):
        """USA: the three files its loaders read, as the game draws them (UsQuestCodec); every other
        quest file (Japan, and the USA build's unloaded Japanese placeholders) as plain Shift-JIS."""
        return ctx.usquest if ctx.build == "us" and self._layout(path) is not None else ctx.sjis

    def export(self, ctx, path, data, info):
        return self._codec(ctx, path).to_text(data, file=True)

    def rebuild(self, ctx, path, data, text):
        codec = self._codec(ctx, path)
        old_text = codec.to_text(data, file=True)
        if text == old_text:
            return data
        if "\r\n" in old_text and "\r" not in text and "\n" in text:
            # an editor saved the file with LF line ends: the game skips CR LF inside a text but copies
            # a lone LF as a line break, so the file's own CR LF are restored, every line alike
            text = text.replace("\n", "\r\n")
        elif "\r\n" in old_text and text.replace("\r\n", "").count("\n"):
            raise TextError("the file mixes CR LF and LF line ends: keep CR LF line ends, as exported (the game "
                            "skips CR LF inside a text and copies a lone LF as a line break)")
        try:
            new = codec.from_text(text)
        except TextError as e:
            for n, ln in enumerate(text.split("\n"), 1):
                try:
                    codec.from_text(ln)
                except TextError as e2:
                    raise TextError(f"line {n}: {e2}")
            raise e
        if new == data:
            return data
        if b"\x00" in new:
            raise TextError("a NUL byte in the file")
        a, b = quest_skeleton(old_text), quest_skeleton(text)
        if a is not None and a != b:
            raise TextError("the file's structure changed: only the text between double quotes may be edited "
                            "(record numbers, braces, field names and every line outside the quotes must stay; "
                            "a quote inside a text is written \\\")")
        if ctx.build == "us":
            layout = self._layout(path)
            old_lines = set(old_text.split("\n"))
            for n, ln in enumerate(text.split("\n"), 1):
                if ln in old_lines:
                    continue
                typed = [ch for ch, _ in textcodec.tokens(ln) if ch and ord(ch) > 0x7F]
                if typed and layout is None:
                    raise TextError(f"line {n}: a non-ASCII character in a quest file no USA loader reads (the "
                                    f"loaders read QuestMes.txt, Citizen.txt and Treasure.txt), so what the game "
                                    f"would make of it is not known; write plain ASCII, or \u27e6XX\u27e7 escapes "
                                    f"if you know the bytes")
                for ch in typed:
                    why = codec.typed_problem(ch)
                    if why is None:      # a symbol pair: its glyph in the USA font, as in any other text
                        why = ctx.check_typed(ctx.codec, codec.drawn(codec.encode_char(ch)))
                    if why:
                        raise TextError(f"line {n}: {why}")
            if layout is not None:
                old_f = quest_fields(data, layout)
                for (r, name, length, size), (_, _, was, _) in zip(quest_fields(new, layout), old_f):
                    if length > size - 1 and length != was:
                        raise TextError(f"record {r + 1} {name}: {length} bytes, but the game copies it into a "
                                        f"{size}-byte field (at most {size - 1} and the terminator; \\n counts one "
                                        f"byte, the line ends inside the quotes none); shorten it by "
                                        f"{length - size + 1}")
        else:
            # the file is Shift-JIS; the glyph check assumes the game maps each character to its JIS code
            why = ctx.check_lines(textcodec.EucCodec(), old_text, text)
            if why:
                raise TextError(why)
        return new


class Subtitle(Kind):
    name = "subtitle"
    relocatable = True

    def match(self, low, head):
        return region.is_movie_subtitle(low)

    def tree_path(self, root, chain, path):
        return _tree(root, chain, path).with_suffix(".sub.txt")

    def export(self, ctx, path, data, info):
        return ctx.subcodec.to_text(data, file=True)

    def rebuild(self, ctx, path, data, text):
        import moviesub
        old_text = ctx.subcodec.to_text(data, file=True)
        if text == old_text:
            return data
        new = ctx.subcodec.from_text(text)
        if new == data:
            return data
        why = ctx.check_lines(ctx.codec, old_text, text, skip=lambda ln: ln.startswith(";"))
        if why:
            raise TextError(why)
        why = moviesub.check_edit(data, new, ctx.subtitle_limits(), ctx.measure())
        if why:
            raise TextError(why)
        return new

    def limit(self, ctx, e, data, new, alloc, sizes, grow):
        if len(new) > alloc and not grow:
            return f"{len(new)} bytes exceeds its {alloc}-byte slot (over by {len(new) - alloc}; --grow relocates it)"
        return None


ICON_TITLE_AT, ICON_TITLE_LEN = 0xC0, 68          # icon.sys: the title field, Shift-JIS, NUL-padded
ICON_BREAK_AT = 6                                 # icon.sys: u16, the byte offset where line 2 starts
# FUN_0019dc10 (USA), called from FUN_00239f98 at 0x0023A8C4 with the loaded carddata.bin, the slot
# number + 1 and the save time, writes these bytes of the title when it saves (offsets in the
# title; the buffer has the pack's 0x40-byte table first, icon.sys at 0x40): the trail bytes of
# the slot digits (+0x1B, +0x1D), the hour's tens as a whole pair (+0x20/+0x21: 81 40 or 82 4F+d),
# the trail bytes of the hour's ones and the minutes (+0x23, +0x27, +0x29). The leads before
# them stay as loaded, so the whole pairs +0x1A..+0x1D, +0x20..+0x23 and +0x26..+0x29 are kept.
ICON_STAMPED = tuple(range(0x1A, 0x1E)) + tuple(range(0x20, 0x24)) + tuple(range(0x26, 0x2A))


class IconTitle(Kind):
    """The save's title in the PS2 memory-card browser: ``icon.sys`` (member 0 of
    carddata.bin; Japan carddata.new), Shift-JIS at +0xC0, two lines split at the u16 byte
    offset at +6. Rewritten in place: the pack keeps its size."""
    name = "icontitle"
    why_fixed = "the pack is rewritten in place"

    def match(self, low, head):
        return low in ("carddata.bin", "carddata.new")

    def tree_path(self, root, chain, path):
        p = _tree(root, chain, path)
        return p.with_name(p.name + ".title.txt")

    def count(self, ctx, data):
        return 1

    @staticmethod
    def _sys(data: bytes) -> Tuple[int, bytes]:
        import smallfiles
        try:
            start, end = smallfiles.pack_members(data)[0]
        except smallfiles.SmallFileError as e:
            raise TextError(f"not a pack: {e}")
        sys_ = data[start:end]
        if sys_[:4] != b"PS2D" or len(sys_) < ICON_TITLE_AT + ICON_TITLE_LEN:
            raise TextError("member 0 is not an icon.sys")
        return start, sys_

    def _lines(self, ctx, sys_: bytes) -> Tuple[bytes, int]:
        field = sys_[ICON_TITLE_AT:ICON_TITLE_AT + ICON_TITLE_LEN]
        title = field.split(b"\0")[0]
        if any(field[len(title):]):
            raise TextError("bytes after the title's terminator; left alone")
        brk = struct.unpack_from("<H", sys_, ICON_BREAK_AT)[0]
        if not 0 < brk <= len(title):
            raise TextError(f"the line-break offset {brk} is outside the title")
        return title, brk

    def _show(self, ctx, title: bytes, brk: int) -> str:
        a, b = ctx.sjis.to_text(title[:brk]), ctx.sjis.to_text(title[brk:])
        if ctx.sjis.from_text(a) != title[:brk]:
            raise TextError("the line break falls inside a character; left alone")
        return a + "\\n" + b

    def export(self, ctx, path, data, info):
        _, sys_ = self._sys(data)
        title, brk = self._lines(ctx, sys_)
        shown = self._show(ctx, title, brk)
        return "\n".join([
            f"# {path}  the save's title in the PS2 memory-card browser (icon.sys, Shift-JIS)",
            f"# Two lines, split where \\n stands. At most {ICON_TITLE_LEN - 1} bytes in all (2 per full-width "
            f"character, 1 per half-width).",
            "# The game writes the save slot and time into the title when it saves (FUN_0019dc10): keep the "
            "characters at bytes",
            "# 26-29 (the slot, '\uff10\uff10'), 32-35 and 38-41 (the time) where they are, so the text before them "
            "must stay 26 bytes",
            "# (13 full-width characters, as exported).",
            "### title", shown]) + "\n"

    def rebuild(self, ctx, path, data, text):
        start, sys_ = self._sys(data)
        title, brk = self._lines(ctx, sys_)
        (body,) = keyed_bodies(text, ["title"])
        if body == self._show(ctx, title, brk):
            return data
        parts = body.split("\\n")
        if len(parts) != 2:
            raise TextError(f"### title: write exactly one \\n (the line break of the memory-card browser); "
                            f"found {len(parts) - 1}")
        if "\\" in body.replace("\\n", ""):
            raise TextError("### title: a backslash other than the \\n line break")
        try:
            a, b = ctx.sjis.from_text(parts[0]), ctx.sjis.from_text(parts[1])
        except TextError as e:
            raise TextError(f"### title: {e}")
        new = a + b
        if b"\0" in new:
            raise TextError("### title: a NUL byte (\u27e600\u27e7) inside the title")
        if len(new) > ICON_TITLE_LEN - 1:
            raise TextError(f"### title: {len(new)} bytes, but the field holds {ICON_TITLE_LEN - 1} and its "
                            f"terminator; shorten it by {len(new) - ICON_TITLE_LEN + 1}")
        if not a:
            raise TextError("### title: the first line is empty")
        if ctx.build != "us":
            raise TextError("### title: the save writer was read in the USA executable only (FUN_0019dc10); "
                            "on this build which bytes it stamps has not been read")
        moved = [k for k in ICON_STAMPED if k >= len(new) or new[k] != title[k]]
        if moved:
            raise TextError(f"### title: the game writes the slot and time into bytes {ICON_STAMPED[0]}-"
                            f"{ICON_STAMPED[3]}, {ICON_STAMPED[4]}-{ICON_STAMPED[7]} and {ICON_STAMPED[8]}-"
                            f"{ICON_STAMPED[11]} of the title when it saves; the edit changes or moves what sits "
                            f"there (byte {moved[0]}). Keep the text before them 26 bytes and those characters "
                            f"as exported")
        blob = bytearray(data)
        o = start + ICON_TITLE_AT
        blob[o:o + ICON_TITLE_LEN] = new + bytes(ICON_TITLE_LEN - len(new))
        struct.pack_into("<H", blob, start + ICON_BREAK_AT, len(a))
        out = bytes(blob)
        import smallfiles
        if smallfiles.build_pack(smallfiles.unpack_pack(out)) != out:
            raise TextError("the rewritten pack does not rebuild to itself")
        _, sys2 = self._sys(out)
        if self._lines(ctx, sys2) != (new, len(a)):
            raise TextError("the rewritten title does not read back as written")
        return out

    def limit(self, ctx, e, data, new, alloc, sizes, grow):
        return None if len(new) == len(data) else "the memory-card pack changed size"


KINDS: Tuple[Kind, ...] = (Bank(), Inline(), Menu(), Battle(), Quest(), Subtitle(), IconTitle())


# ------------------------------------------------------------- battle banners

BANNER = "bat.namePut"


def banner_uses(iso_path: str) -> List[Tuple[int, str, int, bytes]]:
    """(chain, bank path, .sbl index, original bytes) of every string a script passes to
    ``bat.namePut`` (the battle banners: OV01 FUN_00aa1330 resolves the 4th argument with
    FUN_00a9f238 and calls FUN_002355d8). Empty on a build without string banks."""
    import dialogue
    return dialogue.native_text_uses(dialogue.Disc([iso_path]), (BANNER,))[BANNER]


def banner_overrun(s: bytes) -> bool:
    """True when FUN_002353b8, which measures a banner, would step over the string's terminator:
    it takes every byte from 0x80 up together with the next byte, so a one-byte Latin-1 letter
    right before the end pairs with the NUL and the walk reads on past the string."""
    i, n = 0, len(s)
    while i < n:
        if s[i] >= 0x80:
            if i + 1 >= n:
                return True
            i += 2
        else:
            i += 1
    return False


def banner_width(measure: Callable[[bytes], int], s: bytes) -> int:
    """The banner box's width as FUN_002355d8 sizes it: FUN_002353b8(text) + 0x18. FUN_002353b8
    starts at zoom 16, skips a $code to its ';' ($init sets zoom 16, $zoomN; and $zoomNN; set N),
    ignores bytes below 0x20 and adds, for every character (a byte from 0x80 up with the next
    byte, any other byte alone), the width function's value for it times zoom / 16.
    ``measure(piece)`` stands in for that width function (FUN_00195938, which it calls with the
    piece after the codes 19 03; those codes' state is not modelled, so this is an estimate)."""
    zoom, w, i, n, skip = 16, 0, 0, len(s), False
    while i < n and s[i]:
        b = s[i]
        if b >= 0x80:
            w += measure(s[i:i + 2]) * zoom >> 4
            i += 2
            continue
        if skip:
            skip = b != 0x3B
        elif b == 0x24:
            skip = True
            if s[i + 1:i + 5] == b"init":
                zoom = 16
            elif s[i + 1:i + 5] == b"zoom":
                d5 = s[i + 5] if i + 5 < n else 0
                d6 = s[i + 6] if i + 6 < n else 0
                zoom = d5 - 0x30 if d6 in (0x2C, 0x3B, 0) else d5 * 10 + d6 - 0x210
        elif b > 0x1F:
            w += measure(bytes((b,))) * zoom >> 4
        i += 1
    return w + 0x18


def banner_report(ctx: Context, uses, banks: Dict[Tuple[int, str], Tuple[List[bytes], List[bytes]]],
                  problems: List[str], notes: List[str]) -> set:
    """Battle banners after an import is planned. ``banks`` maps (chain, lower-case bank path) to
    (original strings, new strings). A refusal for a banner whose width walk would run off its end;
    a note (not a refusal) for a name whose copies in different banks were not edited alike, and
    for a banner wider than any the retail disc shows (the width has no clamp in FUN_002355d8).
    Returns the (chain, bank path) of the banks refused."""
    copies: Dict[bytes, Dict[Tuple[int, str, int], bytes]] = {}
    for chain, path, idx, orig in uses:
        pair = banks.get((chain, path.lower()))
        if pair is None or idx >= len(pair[1]):
            continue
        copies.setdefault(orig, {})[(chain, path, idx)] = pair[1][idx]
    changed = {(o, k): n for o, cs in copies.items() for k, n in cs.items() if n != o}
    refused = set()
    if not changed:
        return refused
    for (o, (chain, path, idx)), n in sorted(changed.items(), key=lambda kv: kv[0][1]):
        if banner_overrun(n):
            refused.add((chain, path))
            problems.append(f"{path}: ### {idx:04d} is a battle banner (bat.namePut) and ends with a one-byte "
                            f"letter from 0x80 up ({ctx.codec.to_text(n[-1:])}): FUN_002353b8, which measures the "
                            f"banner, takes such a byte with the next one and would step over the terminator. End "
                            f"the banner with a plain ASCII character")
    for o, cs in sorted(copies.items()):
        texts = set(cs.values())
        if len(cs) > 1 and len(texts) > 1:
            groups: Dict[bytes, List[str]] = {}
            for (chain, path, idx), n in sorted(cs.items()):
                groups.setdefault(n, []).append(f"{path.rsplit(chr(92), 1)[-1]} #{idx:04d}")
            desc = "; ".join(("unchanged" if n == o else repr(ctx.codec.to_text(n))) + f" in {len(where)} "
                             f"({', '.join(where[:3])}{', ...' if len(where) > 3 else ''})"
                             for n, where in groups.items())
            notes.append(f"the battle banner {ctx.codec.to_text(o)!r} has {len(cs)} copies that now differ: "
                         f"{desc}. Each copy is shown by its own scene's battles; give them the same text unless "
                         f"you mean otherwise")
    try:
        adr, fonts = ctx.addressing()
        metrics = next(iter(fonts.values())).metrics

        def measure(piece: bytes) -> int:
            return adr.measurer(metrics, han=True, prop=True).width(piece)
        widest = max((banner_width(measure, o), o) for o in copies)
        for (o, (chain, path, idx)), n in sorted(changed.items(), key=lambda kv: kv[0][1]):
            w = banner_width(measure, n)
            if w > widest[0]:
                notes.append(f"{path}: ### {idx:04d}, the battle banner {ctx.codec.to_text(n)!r}, is about {w} "
                             f"pixels wide by the font's metrics, wider than any banner of this disc ({widest[0]}, "
                             f"{ctx.codec.to_text(widest[1])!r}). FUN_002355d8 sizes the box to the text with no "
                             f"limit; the screen's limit was not read, so check it in the game")
    except (TextError, ValueError) as e:
        notes.append(f"the widths of edited battle banners were not estimated: {e}")
    return refused


def _objects(iso: IsoImage):
    """(chain, entry, kind, bytes as stored, bytes decompressed, slot, {path: size}) for
    every text object of the image."""
    import arx
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        alloc = _allocations(entries)
        sizes = {e.path.lower(): e.size for e in entries}
        for e in entries:
            low = e.path.lower()
            if not low.endswith((".sbl", ".sb", ".bin", ".txd", ".us", ".txt", ".new")):
                continue
            head = reader.read(e.byte_offset, min(4, e.size))
            kind = next((k for k in KINDS if k.match(low, head)), None)
            if kind is None:
                continue
            stored = reader.read(e.byte_offset, e.size)
            data = arx.decompress(stored) if e.compressed else stored
            yield chain, e, kind, stored, data, alloc[e.path], sizes


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


# --------------------------------------------------------------------- export

def export_text(iso_path: str, out_dir: str) -> int:
    iso = IsoImage(iso_path)
    ctx = Context(iso)
    root = Path(out_dir)
    serial = iso.boot_elf_name() or "?"
    mpath = root / MANIFEST
    manifest = {"format": FORMAT, "build": ctx.build, "serials": [], "objects": {}}
    if mpath.exists():
        try:
            old = json.loads(mpath.read_text(encoding="utf-8"))
        except ValueError:
            old = None
        if not old or old.get("format") != FORMAT or old.get("build") != ctx.build:
            print(f"refused: {root} already holds an export of another build or format "
                  f"({(old or {}).get('build')}); export into an empty folder", file=sys.stderr)
            return 1
        manifest = old
    if ctx.build == "unknown":
        print(f"note: {serial} is not a build the kit knows; the text is exported as EUC-JP for reading. "
              f"text-import will refuse this disc", file=sys.stderr)
    done: Dict[str, List[int]] = {}
    written: Dict[Path, str] = {}
    kept: List[str] = []
    banners: Dict[Tuple[int, str], Dict[int, int]] = {}
    if ctx.build == "us":
        uses = banner_uses(iso_path)
        spread: Dict[bytes, set] = {}
        for chain, path, idx, orig in uses:
            spread.setdefault(orig, set()).add((chain, path.lower()))
        for chain, path, idx, orig in uses:
            banners.setdefault((chain, path.lower()), {})[idx] = len(spread[orig])
    for chain, e, kind, stored, data, alloc, sizes in _objects(iso):
        info = {"chain": chain, "alloc": alloc, "sizes": sizes, "banners": banners.get((chain, e.path.lower()), {})}
        try:
            text = kind.export(ctx, e.path, data, info)
            if not text:
                continue
            if kind.rebuild(ctx, e.path, data, text) != data:
                raise TextError("does not rebuild to the same bytes")
        except (TextError, ValueError) as ex:
            print(f"text-export: {e.path} left out: {ex}", file=sys.stderr)
            continue
        dest = kind.tree_path(root, chain, e.path)
        if dest in written:                          # two objects of one disc mapping to one file
            print(f"text-export: {e.path} left out: its file {dest.name} is already used by "
                  f"{written[dest]}", file=sys.stderr)
            continue
        written[dest] = e.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            with open(dest, encoding="utf-8-sig", newline="") as fh:
                have = fh.read()
            if have != text:                         # someone's edits: an export never overwrites them
                kept.append(_rel(root, dest))
                continue
        with open(dest, "w", encoding="utf-8", newline="") as fh:     # line ends exactly as written here
            fh.write(text)
        manifest["objects"][_rel(root, dest)] = {"chain": chain, "path": e.path, "kind": kind.name,
                                                 "size": len(stored), "sha1": hashlib.sha1(stored).hexdigest()}
        n = done.setdefault(kind.name, [0, 0])
        n[0] += 1
        n[1] += kind.count(ctx, data)
    if serial not in manifest["serials"]:
        manifest["serials"].append(serial)
    root.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for name, (files, strings) in sorted(done.items()):
        print(f"exported {files} {name} file(s)" + (f", {strings} strings" if strings else "") + f" to {root}",
              file=sys.stderr)
    if kept:
        print(f"kept {len(kept)} file(s) in {root} that differ from this disc's text (edited, or from another "
              f"export) and did not overwrite them, e.g. {kept[0]}. Export into an empty folder for a fresh copy",
              file=sys.stderr)
    if not done and not kept:
        print(f"no text objects found on {iso_path}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------- import

def plan_import(iso_path: str, text_dir: str, grow: bool = False, rebase: bool = False,
                notes: Optional[List[str]] = None) -> Tuple[Dict[Tuple[int, str], bytes], List[str]]:
    """({(chain, path): new bytes}, problems). Raises region.UnknownBuild for a disc
    the kit does not know. With any problem, nothing should be written. ``notes`` collects
    remarks that do not stop the import (battle banners edited unalike, wide banners)."""
    notes = [] if notes is None else notes
    banks: Dict[Tuple[int, str], Tuple[List[bytes], List[bytes]]] = {}
    iso = IsoImage(iso_path)
    ctx = Context(iso, need_executable=True)
    root = Path(text_dir)
    matched = set()
    changed: Dict[Tuple[int, str], bytes] = {}
    problems: List[str] = []
    manifest = None
    if not rebase:
        mpath = root / MANIFEST
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}, [f"{mpath} is missing or unreadable: this tree cannot be tied to a disc. Export again, "
                        f"or pass --rebase to import it onto this disc as it is"]
        if manifest.get("format") != FORMAT:
            return {}, [f"{mpath} is of format {manifest.get('format')}, this kit writes {FORMAT}; export again"]
        if manifest.get("build") != ctx.build:
            return {}, [f"this text was exported from a {manifest.get('build')} disc "
                        f"({', '.join(manifest.get('serials', []))}); the disc given is {ctx.build} "
                        f"({iso.boot_elf_name()}). --rebase overrides"]
    for chain, e, kind, stored, data, alloc, sizes in _objects(iso):
        src = kind.tree_path(root, chain, e.path)
        if not src.exists():
            continue
        matched.add(_rel(root, src))
        if manifest is not None:
            rec = manifest["objects"].get(_rel(root, src))
            sha = hashlib.sha1(stored).hexdigest()
            if rec is None:
                problems.append(f"{e.path}: {src.name} is not in the export's manifest")
                continue
            if rec.get("sha1") != sha:
                problems.append(f"{e.path}: the object on this disc is not the one this text was exported from "
                                f"(sha1 {sha[:12]}, exported {str(rec.get('sha1'))[:12]}): an already patched "
                                f"image or another revision. Export from this disc, or pass --rebase to apply "
                                f"the text to it as it is")
                continue
        with open(src, encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
        try:
            new = kind.rebuild(ctx, e.path, data, text)
        except TextError as ex:
            problems.append(f"{e.path}: {ex}")
            continue
        if kind.name == "bank":
            old_s = sbl.parse(data)
            banks[(chain, e.path.lower())] = (old_s, old_s if new == data else sbl.parse(new))
        if new == data:
            continue
        if e.compressed:
            problems.append(f"{e.path}: stored compressed; text-import does not rewrite compressed text")
            continue
        why = kind.limit(ctx, e, data, new, alloc, sizes, grow)
        if why:
            problems.append(f"{e.path}: {why}")
            continue
        changed[(chain, e.path)] = new
    problems += _new_subtitle_files(root, matched, available_chains(iso), manifest)
    if any(o != n for o, n in banks.values()):
        for key in banner_report(ctx, banner_uses(iso_path), banks, problems, notes):
            changed.pop(key, None)
    iso.close()
    if manifest is not None:
        absent = sorted(k for k in manifest.get("objects", {}) if k not in matched and (root / k).exists())
        if absent:                                   # e.g. a Disc 1 tree given Disc 2: its own-chain files
            print(f"note: {len(absent)} text file(s) of this export have no object on this disc and were not "
                  f"applied (e.g. {absent[0]})", file=sys.stderr)
    return changed, problems


def _new_subtitle_files(root: Path, matched, chains, manifest) -> List[str]:
    """A subtitle file in the tree for a movie that has none on this disc (s020310 and four others) is
    refused: the movie player FUN_001e7d08 reads <movie>.txt through the TOC into the start of the block
    it allocates for the video decoder, and starts the decoder's work area right after the cues, at
    align64 of their length; when the file is missing (FUN_0019c1e0 returns -1) that length is 0 and the
    work area starts at the block's start. The block is sized for the decoder alone (an overrun of 64
    bytes broke playback in the running game, docs/MODDING.md), so such a movie has no room for cues."""
    out = []
    objects = (manifest or {}).get("objects", {})
    for f in sorted(root.glob("chain*/**/*.sub.txt")):
        rel = _rel(root, f)
        try:
            chain = int(rel.split("/", 1)[0][5:])
        except ValueError:
            continue
        if rel in matched or rel in objects or chain not in chains:
            continue
        out.append(f"{rel}: this disc has no subtitle file for that movie, and text-import does not add one. The "
                   f"movie player keeps a movie's cues at the start of the video decoder's memory block and starts "
                   f"the decoder right after them; with no subtitle file there is no room for any cue "
                   f"(FUN_001e7d08, docs/DIALOGUE.md)")
    return out


def import_text(iso_path: str, text_dir: str, out_iso: str, grow: bool = False, rebase: bool = False) -> int:
    from repack import RepackError, patch_copy, same_file

    if same_file(iso_path, out_iso):
        print(f"refused: --out {out_iso} is the input image itself; the kit never patches its source. "
              f"Give another file name", file=sys.stderr)
        return 1
    notes: List[str] = []
    try:
        changed, problems = plan_import(iso_path, text_dir, grow=grow, rebase=rebase, notes=notes)
    except region.UnknownBuild as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    for n in notes:
        print("note: " + n, file=sys.stderr)
    for p in problems:
        print("refused: " + p, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s); nothing was written", file=sys.stderr)
        return 1
    if not changed:
        print("no text changed; nothing written", file=sys.stderr)
        return 0
    try:
        reports = patch_copy(iso_path, out_iso, changed, grow=grow)
    except (RepackError, region.UnknownBuild) as e:
        print(f"refused: {e}; nothing was written", file=sys.stderr)
        return 1
    for r in reports:
        if r.relocated_lba is not None:
            print(f"WARNING: chain{r.chain} {r.path} was RELOCATED to the end of the image (sector "
                  f"{r.relocated_lba}). Its loader's room was read from the code; a relocated text bank has "
                  f"NOT been seen working in the running game. Test it before relying on it", file=sys.stderr)
    print(f"patched {len(changed)} text file(s) into {out_iso}", file=sys.stderr)
    return 0


# ------------------------------------------------------------------ self-test

def _edit(path: Path, old: str, new: str) -> None:
    with open(path, encoding="utf-8", newline="") as fh:
        t = fh.read()
    if old not in t:
        raise AssertionError(f"{old!r} not in {path}")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(t.replace(old, new, 1))


def _edit_block(path: Path, index: int, new_text: str, marker: str = "### ") -> None:
    """Replace the text of block ``index`` (0-based) of an exported file (tests)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    heads = [k for k, ln in enumerate(lines) if ln.startswith(marker)]
    k = heads[index]
    end = heads[index + 1] if index + 1 < len(heads) else len(lines) - 1
    body = [ln for ln in lines[k + 1:end] if ln.startswith("#")] + [new_text]
    path.write_text("\n".join(lines[:k + 1] + body + lines[end:]), encoding="utf-8")


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    import minidisc
    import smallfiles

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    def quiet(fn, *a, **k):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    def plan(iso, tree, **k):
        return plan_import(str(iso), str(tree), **k)

    def refused(iso, tree, needle, **k):
        ch, pr = plan(iso, tree, **k)
        return not ch and len(pr) == 1 and needle in pr[0]

    # ---- pieces
    sizes = {"cf\\sc0001.sb": 3000, "model\\map\\x.bin": 70004}
    check(bank_budget("cf\\en\\sc0001.sbl", sizes)[0] == SCENE_BUFFER - 4096, "scene bank: 0x20000 less the script's sectors")
    check(bank_budget("model\\map\\en\\X.sbl", sizes)[0] == 2048, "map bank: 0x12000 less the script rounded to 64, whole sectors")
    check(bank_budget("yamamoto\\think\\jp\\think001.sbl", sizes)[0] is None and bank_budget("cf\\en\\sc9999.sbl", sizes)[0] is None
          and bank_budget("cf\\fr\\sc0001.sbl", sizes)[0] is None, "no budget without a known loader, script or language folder")
    check(bank_budget("cf\\en\\sc0001.sbl", sizes, "jp")[0] is None and bank_budget("cf\\en\\sc0001.sbl", sizes, "unknown")[0] is None,
          "the loader rules are the USA build's: no budget for another build")
    check(parse_blocks("# h\n### 0000\n#hint\nA\n\n### 0001\nB\nC\n\n\n") == [("0000", "A"), ("0001", "B\\nC")],
          "blocks: comments dropped, trailing blank lines dropped, typed lines joined with \\n")
    for bad, why in (("### 0001\nb\n### 0000\na\n", "out of order"), ("### 0000\na\n### 0000\nb\n", "twice"),
                     ("### 0000\na\n", "missing"), ("### 0000\na\n### 0001\nb\n### 0002\nc\n", "not in the export")):
        try:
            keyed_bodies(bad, ["0000", "0001"])
            check(False, f"block keys: {why}")
        except TextError as e:
            check(why in str(e), f"block keys refused: {why}")
    menu = [b"New Game", b"Load", b"%p", b""]
    check(txd_parse(txd_build(menu, b"\x00\x00")) == (menu, b"\x00\x00"), ".txd build/parse round trip, tail kept")
    check(quest_skeleton('No.1\n{\n\tName:"a \\" b"\n\tP:"\nx\n"\n}\n') == 'No.1\n{\n\tName:""\n\tP:""\n}\n'
          and quest_skeleton('Name:"open') is None, "quest skeleton: what is outside the quotes")

    # ---- a USA mini disc: executable with font tables, two fonts, every kind of text
    blank_cells = [0xF1 + 0x80, 0xF1 + 0x80 + 576]                   # ñ has no glyph in this font
    font_us = minidisc.synthetic_font(384, blank_cells)
    elf_us = minidisc.synthetic_executable("us")
    scene = [b"[A]One.", b"#1 starts with a hash", b"<\xa2\xa2> Caf\xe9 \xa4\xa5", b"", b"### 0001"]
    subs = (b";" + b"-" * 60 + b"\n0\t$ay390;\n00:01:00\tHello there\n00:02:00\tTwo\\nlines of it\n00:03:00\t\nend\n")
    # the battle database with the retail numbering: 19 sections, the text in section 6 (12-byte records,
    # name +0 and description +2 read by OV01 FUN_00a9b750; +4 and +6 are numbers equal to string offsets)
    pool = b"Medica\x00One ally\x00Revert\x00\x00"
    sec6 = struct.pack("<6H", 24, 31, 7, 0, 0, 0) + struct.pack("<6H", 40, 31, 24, 31, 0, 0) + pool
    bsecs = [struct.pack("<4H", 1, 2, 3, 4)] * 19
    bsecs[6] = sec6
    boffs = [84 + sum(len(x) for x in bsecs[:i]) for i in range(19)]
    bd = struct.pack("<II", 19, 84 + sum(len(x) for x in bsecs)) + struct.pack("<19I", *boffs) + b"".join(bsecs)
    quest = "No.1\r\n{\r\n\tName:\"Sikes\"\r\n}\r\n".encode("shift_jis")
    treasure = "".join(f"No.{i + 1}\r\n{{\r\n\tItem:\"Potion\"\r\n\tLocate:\"Here\"\r\n\tExpl:\"Heals\\nyou.\"\r\n}}\r\n"
                       for i in range(18)).encode("ascii")
    treasure = treasure.replace(b'Locate:"Here"', b'Locate:"\x83\xb6 System"', 1)     # the retail pair (Omega)
    # MainMenu.txd: entries 0218 and 0543 with their retail bytes (the slot placeholder A3 B0), one without it,
    # the EUC developer note of entry 0318
    slot218 = (b"Checking memory card (8MB) (for PlayStation\xa4\xa52) in\\nMEMORY CARD slot \xa3\xb0.")
    slot543 = (b"No memory card (8MB) (for PlayStation\xa4\xa52) found\\nin MEMORY CARD slot \xa3\xb0.")
    note318 = "↑予備ではなく、空欄で　コンディション予備".encode(sbl.CODEC)
    mainmenu = [b"Saving...", slot218, slot543, note318]
    # Tutorial.txd: the first entries of the retail Skill Tutorial
    tutorial = [b"Skill Tutorial", b"%p", b"*", b"You can customize the abilities of your characters", b"%t45", b"",
                b"%c+"]
    # two map scripts whose banks share a battle banner (bat.namePut, 4th of 5 arguments) and a test line
    def op(o, v=None):
        return struct.pack("<H", o) if v is None else struct.pack("<HHH", o, v & 0xFFFF, (v >> 16) & 0xFFFF)
    name_put = (op(0x04, 1) + op(0x28) + op(0x04, 2) + op(0x28) + op(0x04, 3) + op(0x28) + op(0x06, 0x200001)
                + op(0x2A) + op(0x04, 5) + op(0x28) + op(0x04, 5) + op(0x15, 0) + op(0x19) + op(0x13))
    import sb as _sb
    map_script = _sb.build_script(name_put, [("bat", "namePut")]).ljust(70004, b"\x00")
    euc_line = "11テストメッセージ\\n表示中".encode(sbl.CODEC)
    title = "ＸｅｎｏｓａｇａＥＰＩＩ−００［００：００］".encode("shift_jis")
    icon_sys = bytearray(964)
    icon_sys[:4] = b"PS2D"
    struct.pack_into("<H", icon_sys, ICON_BREAK_AT, 30)
    icon_sys[ICON_TITLE_AT:ICON_TITLE_AT + len(title)] = title
    import smallfiles
    carddata = smallfiles.build_pack([bytes(icon_sys), b"\x00\x00\x01\x00" + bytes(200)])
    files = {
        "font0.us": font_us, "font1.us": font_us,
        "cf\\sc0001.sb": b"\x00" * 3000,                           # not parsed: only its size matters here
        "cf\\en\\sc0001.sbl": sbl.build(scene),
        "model\\map\\X.bin": map_script,
        "model\\map\\en\\X.sbl": sbl.build([b"map line", b"$zoom13;Medica", euc_line]),
        "model\\map\\Y.bin": map_script,
        "model\\map\\en\\Y.sbl": sbl.build([b"other map", b"$zoom13;Medica"]),
        "yosikawa\\MainMenu.txd": txd_build(mainmenu),
        "yosikawa\\Tutorial.txd": txd_build(tutorial),
        "other\\en\\odd.sbl": sbl.build([b"odd"]),
        "yosikawa\\Menu.txd": txd_build(menu),
        "movie\\en\\s0.txt": subs,
        "iwase\\Quest.txt": quest,
        "iwase\\Treasure.txt": treasure,
        "carddata.bin": carddata,
        "yamamoto\\batdat.us": bd,
        "zz\\end.bin": b"E" * 100,
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        iso = minidisc.build(td / "mini.iso", files, elf=elf_us, slack={"yosikawa\\Menu.txd": 1})
        tree = td / "text"

        def fresh():
            import shutil
            if tree.exists():
                shutil.rmtree(tree)
            quiet(export_text, str(iso), str(tree))

        fresh()
        names = sorted(p.name for p in tree.rglob("*.txt"))
        check(names == ["MainMenu.txd.txt", "Menu.txd.txt", "Quest.sjis.txt", "Treasure.sjis.txt", "Tutorial.txd.txt",
                         "X.sbl.txt", "Y.sbl.txt", "batdat.us.txt", "carddata.bin.title.txt", "odd.sbl.txt",
                         "s0.sub.txt", "sc0001.sbl.txt"],
              "one tree file per text object, by kind")
        man = json.loads((tree / MANIFEST).read_text(encoding="utf-8"))
        check(man["build"] == "us" and man["serials"] == ["SLUS_208.92"] and len(man["objects"]) == 12
              and man["objects"]["chain0/cf/en/sc0001.sbl.txt"]["sha1"] == hashlib.sha1(files["cf\\en\\sc0001.sbl"]).hexdigest(),
              "the manifest records the build and each object's sha1")
        check(plan(iso, tree) == ({}, []), "an unedited export imports as no change")
        bank = Bank().tree_path(tree, 0, "cf\\en\\sc0001.sbl")
        body = bank.read_text(encoding="utf-8")
        check("\n⟦23⟧1 starts with a hash\n" in body and "\n⟦23⟧## 0001\n" in body and "<□> Café ⟦A4⟧⟦A5⟧" in body,
              "USA export: a leading # is ⟦23⟧, é is é, the game's pairs are symbols, A4 pairs are escapes")
        # mutation of the hash-leading string, and typing a new one
        _edit(bank, "⟦23⟧1 starts with a hash", "⟦23⟧2 starts with a hash")
        _edit(bank, "[A]One.", "⟦23⟧A typed hash; Été")
        ch, pr = plan(iso, tree)
        got = sbl.parse(ch[(0, "cf\\en\\sc0001.sbl")]) if ch else []
        check(not pr and got == [b"#A typed hash; \xc9t\xe9", b"#2 starts with a hash", scene[2], b"", b"### 0001"],
              "strings that start with # round-trip and can be edited; É and é are typed as Latin-1")
        fresh()
        _edit(bank, "[A]One.", "#[A]One.")
        ch, pr = plan(iso, tree)
        check(ch and sbl.parse(ch[(0, "cf\\en\\sc0001.sbl")])[0] == b"" and not pr,
              "a line typed with a bare # is a comment: the block is then empty text (documented)")
        # typed characters against the font
        fresh()
        _edit(bank, "[A]One.", "Señor")
        check(refused(iso, tree, "has no glyph"), "USA: a typed character whose glyph cell is blank is refused")
        _edit(bank, "Señor", "Se⟦F1⟧or")
        ch, pr = plan(iso, tree)
        check(not pr and sbl.parse(ch[(0, "cf\\en\\sc0001.sbl")])[0] == b"Se\xf1or", "... and accepted as an explicit escape")
        fresh()
        _edit(bank, "[A]One.", "あ")
        check(refused(iso, tree, "cannot be written on the USA build"), "USA: kana are refused")
        fresh()
        _edit(bank, "[A]One.", "One¤x")
        check(refused(iso, tree, "would not read"), "USA: ¤ before another byte would pair with it: refused")
        fresh()
        _edit(bank, "[A]One.", "A⟦00⟧B")
        check(refused(iso, tree, "NUL"), "an embedded NUL is refused")
        # block identity
        fresh()
        t = bank.read_text(encoding="utf-8")
        a, b = t.index("### 0000"), t.index("### 0001")
        c = t.index("### 0002")
        bank.write_text(t[:a] + t[b:c] + t[a:b] + t[c:], encoding="utf-8")
        check(refused(iso, tree, "out of order"), "bank blocks swapped: refused")
        bank.write_text(t.replace("### 0001", "### 0000"), encoding="utf-8")
        check(refused(iso, tree, "twice"), "a bank key twice: refused")
        bank.write_text(t[:b] + t[c:], encoding="utf-8")
        check(refused(iso, tree, "missing"), "a bank block removed: refused")
        fresh()
        m = Menu().tree_path(tree, 0, "yosikawa\\Menu.txd")
        t = m.read_text(encoding="utf-8")
        a, b, c = t.index("### 0000"), t.index("### 0001"), t.index("### 0002")
        m.write_text(t[:a] + t[b:c] + t[a:b] + t[c:], encoding="utf-8")
        check(refused(iso, tree, "out of order"), "menu blocks swapped (same count): refused")
        m.write_text(t.replace("### 0001", "### 0003", 1), encoding="utf-8")
        check(refused(iso, tree, "twice"), "a menu ID duplicated: refused")
        # the manifest binds the tree to the disc
        fresh()
        _edit(bank, "[A]One.", "[A]Two.")
        out1 = td / "out1.iso"
        check(quiet(import_text, str(iso), str(tree), str(out1)) == 0 and sbl.parse(minidisc.read(out1, "cf\\en\\sc0001.sbl"))[0] == b"[A]Two.",
              "an edit is imported into a new image")
        check(quiet(import_text, str(iso), str(tree), str(iso)) == 1 and iso.read_bytes() == minidisc.build(td / "again.iso", files, elf=elf_us, slack={"yosikawa\\Menu.txd": 1}).read_bytes(),
              "text-import refuses to write over its input")
        _edit(bank, "[A]Two.", "[A]Three.")
        check(refused(out1, tree, "not the one this text was exported from"), "importing onto an already patched image is refused")
        ch, pr = plan(out1, tree, rebase=True)
        check(not pr and sbl.parse(ch[(0, "cf\\en\\sc0001.sbl")])[0] == b"[A]Three.", "... unless --rebase is given")
        (tree / MANIFEST).rename(tree / "gone.json")
        check(refused(iso, tree, "missing or unreadable"), "a tree without its manifest is refused")
        (tree / "gone.json").rename(tree / MANIFEST)
        jp_like = minidisc.build(td / "jp.iso", files, boot="SLPS_253.68", elf=minidisc.synthetic_executable("jp"))
        check(refused(jp_like, tree, "exported from a us disc"), "a tree from another build is refused")
        unk = minidisc.build(td / "unk.iso", files, boot="SLES_999.99", elf=elf_us)
        try:
            plan(unk, tree)
            check(False, "an unknown build is not imported into")
        except region.UnknownBuild as e:
            check("SLES_999.99" in str(e), "an unknown build is refused, naming its serial")
        check(quiet(export_text, str(unk), str(td / "unk_text")) == 0 and (td / "unk_text" / MANIFEST).exists(),
              "... but its text can still be exported for reading")
        hacked = minidisc.build(td / "hack.iso", files, elf=elf_us + b"\x00")
        try:
            plan(hacked, tree)
            check(False, "a changed executable under a known serial is refused")
        except region.UnknownBuild as e:
            check("not the executable" in str(e), "a changed executable under a known serial is refused")
        # growth: bank budget, relocation, and the sector cap of menus and quests
        fresh()
        _edit(bank, "[A]One.", "[A]" + "long " * 500)
        check(refused(iso, tree, "--grow"), "a bank past its slot is refused without --grow")
        out = td / "grown.iso"
        rc = quiet(import_text, str(iso), str(tree), str(out), grow=True)
        check(rc == 0 and sbl.parse(minidisc.read(out, "cf\\en\\sc0001.sbl"))[0] == b"[A]" + b"long " * 500
              and out.stat().st_size == iso.stat().st_size + 2 * SECTOR
              and all(minidisc.read(out, p) == d for p, d in files.items() if p != "cf\\en\\sc0001.sbl"),
              "--grow relocates it; the image gains its sectors; every other object is unchanged")
        fresh()
        _edit(Bank().tree_path(tree, 0, "model\\map\\en\\X.sbl"), "map line", "x" * 3000)
        check(refused(iso, tree, "relocating cannot help", grow=True), "a bank past the loader's buffer is refused even with --grow")
        fresh()
        _edit(Bank().tree_path(tree, 0, "other\\en\\odd.sbl"), "\nodd\n", "\n" + "y" * 3000 + "\n")
        check(refused(iso, tree, "no loader is known", grow=True), "a bank with no known loader may not leave its slot")
        fresh()
        _edit(m, "Load", "L" * 2100)                   # the menu's slot has a spare sector; the loader read one
        check(refused(iso, tree, "need 2 sectors", grow=True), "a menu file may not need more sectors than the original, spare slot or not")
        fresh()
        _edit(m, "Load", "Load Game")
        ch, pr = plan(iso, tree)
        check(not pr and txd_parse(ch[(0, "yosikawa\\Menu.txd")])[0] == [b"New Game", b"Load Game", b"%p", b""], "a menu string grows inside the file's sector")
        # battle database
        fresh()
        bt = Battle().tree_path(tree, 0, "yamamoto\\batdat.us")
        _edit(bt, "One ally", "One")
        ch, pr = plan(iso, tree)
        check(not pr and ch[(0, "yamamoto\\batdat.us")] == bd.replace(b"One ally", b"One\x00\x00\x00\x00\x00"), "a battle string is shortened in place")
        fresh()
        _edit(bt, "Medica", "Medica+")
        ch, pr = plan(iso, tree)
        import smallfiles
        got = ch.get((0, "yamamoto\\batdat.us"))
        check(not pr and got is not None and len(got) > len(bd)
              and [r for _, _, r in smallfiles.batdat_raw_strings(got)] == [b"Medica+", b"One ally", b"Revert"]
              and smallfiles.batdat_field_texts(got)[(6, 1, 0)] == b"Revert",
              "a longer battle string (USA) grows: the strings after it in its section move down")
        fresh()
        _edit(bt, "Medica", "M" * 3000)
        check(refused(iso, tree, "Shorten other battle strings"), "a battle database past its sectors is refused")
        fresh()
        _edit(bt, "Medica", "⟦01⟧edica")
        check(refused(iso, tree, "disappear"), "a battle edit that would hide a section from the next export is refused")
        fresh()
        t = bt.read_text(encoding="utf-8")
        a, b = t.index("### S06 @0x0018"), t.index("### S06 @0x001f")
        bt.write_text(t[:b] + t[a:b] + t[b:], encoding="utf-8")
        check(refused(iso, tree, "twice"), "a battle block given twice is refused")
        bt.write_text(t[:a] + t[b:], encoding="utf-8")
        check(refused(iso, tree, "missing"), "a missing battle block is refused")
        # a subtitle file for a movie that has none on the disc
        fresh()
        extra = tree / "chain0" / "movie" / "en" / "s020310.sub.txt"
        extra.write_text("00:01:00 Hello.\n00:03:00\nend\n", encoding="utf-8")
        check(refused(iso, tree, "does not add one"), "a subtitle file for a movie with none on the disc is refused")
        extra.unlink()
        # quest text
        fresh()
        q = Quest().tree_path(tree, 0, "iwase\\Quest.txt")
        _edit(q, "Sikes", "Sykes")
        ch, pr = plan(iso, tree)
        check(not pr and ch[(0, "iwase\\Quest.txt")] == quest.replace(b"Sikes", b"Sykes"), "a quest text edit")
        _edit(q, "No.1", "No.2")
        check(refused(iso, tree, "structure changed"), "a quest file's structure outside the quotes may not change")
        fresh()
        _edit(q, "Sikes", "Ｓikes")
        check(refused(iso, tree, "no USA loader reads"), "USA: a typed non-ASCII character in a quest file no USA "
                                                         "loader reads is refused")
        fresh()
        with open(q, encoding="utf-8", newline="") as fh:
            qt = fh.read()
        with open(q, "w", encoding="utf-8", newline="") as fh:       # an editor that writes LF line ends
            fh.write(qt.replace("Sikes", "Sykes").replace("\r\n", "\n"))
        ch, pr = plan(iso, tree)
        check(not pr and ch[(0, "iwase\\Quest.txt")] == quest.replace(b"Sikes", b"Sykes"),
              "a quest file saved with LF line ends gets its CR LF back (a lone LF would be a line break in game)")
        with open(q, "w", encoding="utf-8", newline="") as fh:
            fh.write(qt.replace("\r\n", "\n", 1))
        check(refused(iso, tree, "keep CR LF"), "mixed CR LF and LF line ends are refused, saying to keep CR LF")
        # the field sizes of the USA loaders (Treasure: Item 21, Locate 27, Expl 121 bytes)
        fresh()
        tq = Quest().tree_path(tree, 0, "iwase\\Treasure.txt")
        _edit(tq, 'Item:"Potion"', 'Item:"' + "P" * 20 + '"')
        _edit(tq, 'Expl:"Heals\\nyou."', 'Expl:"' + "e\\n" * 60 + '"')     # \n is one byte: 120
        ch, pr = plan(iso, tree)
        check(not pr and (0, "iwase\\Treasure.txt") in ch, "a field of exactly its size minus one is accepted "
                                                          "(\\n counts one byte)")
        fresh()
        _edit(tq, 'Item:"Potion"', 'Item:"' + "P" * 21 + '"')
        check(refused(iso, tree, "record 1 Item: 21 bytes, but the game copies it into a 21-byte field"),
              "a field past its size in the loader's record is refused, record and field named")
        fresh()
        _edit(tq, 'Locate:"Here"', 'Locate:"Here\r\nand there"')
        ch, pr = plan(iso, tree)
        check(not pr, "CR LF inside a text is skipped by the loader and not counted")
        # USA quest text through the reader's pair conversion (FUN_0022ec18 -> FUN_0023ebb8)
        fresh()
        check('Locate:"Ω System"' in tq.read_text(encoding="utf-8"), "USA quest export: the retail pair 83 B6 reads "
                                                                       "as Ω, what the game draws after the conversion")
        _edit(tq, 'Locate:"Ω System"', 'Locate:"☆ System ―"')
        ch, pr = plan(iso, tree)
        check(not pr and ch.get((0, "iwase\\Treasure.txt"), b"").count(b'Locate:"\x81\x99 System \x81\x5c"') == 1,
              "typed symbols are written as the Shift-JIS pairs the reader converts to them (☆ 81 99, ― 81 5C)")
        fresh()
        _edit(tq, 'Locate:"Ω System"', 'Locate:"Système"')
        check(refused(iso, tree, "one Latin-1 byte"), "a typed Latin-1 letter in a USA quest file is refused, the pair "
                                                       "rule explained")
        fresh()
        _edit(tq, 'Locate:"Ω System"', 'Locate:"あ System"')
        check(refused(iso, tree, "A4 A2"), "a kana, which the reader turns into another cell, is refused")
        fresh()
        _edit(tq, 'Locate:"Ω System"', 'Locate:"Caf⟦82⟧⟦A0⟧ System"')
        ch, pr = plan(iso, tree)
        check(not pr and b'Locate:"Caf\x82\xa0 System"' in ch.get((0, "iwase\\Treasure.txt"), b""),
              "an escape pair is written as given (never checked)")
        # the memory-card slot placeholder in MainMenu.txd (FUN_00238038)
        fresh()
        mm = Menu().tree_path(tree, 0, "yosikawa\\MainMenu.txd")
        body = mm.read_text(encoding="utf-8")
        check(body.count("# £° = the memory-card slot number") == 2 and "#euc: ↑予備ではなく" in body,
              "MainMenu export: a note above the two strings with the slot placeholder, an EUC reading of the note")
        _edit(mm, "MEMORY CARD slot £°.\n### 0002", "MEMORY CARD slot.\n### 0002")
        check(refused(iso, tree, "slot placeholder"), "an edit that drops the slot placeholder is refused")
        fresh()
        _edit(mm, "MEMORY CARD slot £°.\n### 0002", "MEMORY CARD slot é£°.\n### 0002")
        check(refused(iso, tree, "never finds the placeholder"), "é right before the placeholder hides it from the "
                                                                  "game's walk: refused")
        fresh()
        _edit(mm, "MEMORY CARD slot £°.\n### 0002", "carte mémoire, fente £°.\n### 0002")
        ch, pr = plan(iso, tree)
        new = txd_parse(ch.get((0, "yosikawa\\MainMenu.txd"), txd_build([b""])))[0]
        check(not pr and len(new) == 4 and new[1].endswith(b"m\xe9moire, fente \xa3\xb0.") and slot_markers(new[1]),
              "a reworded line that keeps the placeholder is accepted")
        check(slot_markers(b"slot \x07\x31.") == [5] and slot_markers(b"\xe9\xa3\xb0") == [] and
              slot_markers(slot218) == [len(slot218) - 3], "the walk finds a stamped digit, not a placeholder after "
                                                          "a high byte, and the retail placeholder")
        # the Skill Tutorial's commands (FUN_00215f10)
        fresh()
        tu = Menu().tree_path(tree, 0, "yosikawa\\Tutorial.txd")
        body = tu.read_text(encoding="utf-8")
        check(body.count("# tutorial command, not shown") == 4 and "# empty: the tutorial skips it" in body,
              "Tutorial export: commands and the empty entry are marked")
        _edit_block(tu, 1, "%P")
        check(refused(iso, tree, "runs it as a tutorial command"), "a changed tutorial command is refused")
        fresh()
        _edit_block(tu, 5, "now text")
        check(refused(iso, tree, "runs it as a tutorial command"), "text typed into an empty (skipped) entry is refused")
        fresh()
        _edit_block(tu, 3, "Vous pouvez personnaliser")
        ch, pr = plan(iso, tree)
        check(not pr and txd_parse(ch[(0, "yosikawa\\Tutorial.txd")])[0][3] == b"Vous pouvez personnaliser",
              "a page of tutorial text is edited")
        for bad, why in (("%Vous", "run it as a command"), ("", "be emptied"), ("⟦23⟧Vous", "skip it")):
            fresh()
            _edit_block(tu, 3, bad)
            check(refused(iso, tree, why), f"a page edited to {bad or 'nothing'!r} is refused ({why})")
        # Japanese developer text and battle banners in the banks
        fresh()
        xb = Bank().tree_path(tree, 0, "model\\map\\en\\X.sbl")
        body = xb.read_text(encoding="utf-8")
        check("#euc: 11テストメッセージ\\n表示中" in body and "#sjis" not in body.split("### 0002")[0],
              "an EUC test line gets an #euc: reading; Latin-1 text gets none")
        check(body.count("# battle banner (bat.namePut); the same name is in 2 banks") == 1,
              "a banner is marked, with the number of banks that hold the same name")
        _edit(xb, "$zoom13;Medica", "$zoom13;Medica+")
        notes = []
        ch, pr = plan_import(str(iso), str(tree), notes=notes)
        check(not pr and len(ch) == 1 and any("have 2 copies" in n or "2 copies that now differ" in n for n in notes),
              "a banner edited in one bank only is imported, with a note naming the copy left unchanged")
        _edit(Bank().tree_path(tree, 0, "model\\map\\en\\Y.sbl"), "$zoom13;Medica", "$zoom13;Medica+")
        notes = []
        ch, pr = plan_import(str(iso), str(tree), notes=notes)
        check(not pr and len(ch) == 2 and not any("copies" in n for n in notes), "edited alike in both banks: no note")
        fresh()
        _edit(xb, "$zoom13;Medica", "$zoom13;Medicé")
        check(refused(iso, tree, "step over the terminator"), "a banner that ends with a one-byte Latin-1 letter is "
                                                              "refused (FUN_002353b8)")
        fresh()
        _edit(xb, "$zoom13;Medica", "$zoom13;Medica of the far too long banner text")
        notes = []
        plan_import(str(iso), str(tree), notes=notes)
        check(any("wider than any banner" in n for n in notes), "a banner wider than any on the disc gets a note")
        check(banner_width(lambda p: 10, b"$zoom8;ab$init;c") == 10 * 8 // 16 * 2 + 10 + 0x18
              and banner_width(lambda p: 10 * len(p), b"\xa4\xa2x") == 20 + 10 + 0x18 and banner_overrun(b"ab\xe9")
              and not banner_overrun(b"ab\xe9c"), "the banner width walk: $zoom scales, $init resets, a high byte "
                                                    "takes two; a trailing high byte runs over")
        # the memory-card title (icon.sys in carddata.bin)
        fresh()
        it = IconTitle().tree_path(tree, 0, "carddata.bin")
        body = it.read_text(encoding="utf-8")
        check("\nＸｅｎｏｓａｇａＥＰＩＩ−００\\n［００：００］\n" in body, "the title is exported with its line break as \\n")
        _edit(it, "ＸｅｎｏｓａｇａＥＰＩＩ−００\\n", "ＸｅｎｏｓａｇａＥｐ．ＩＩ００\\n")
        ch, pr = plan(iso, tree)
        new = ch.get((0, "carddata.bin"), b"")
        s0 = smallfiles.pack_members(carddata)[0][0]
        want = "ＸｅｎｏｓａｇａＥｐ．ＩＩ００［００：００］".encode("shift_jis")
        check(not pr and len(new) == len(carddata) and new[s0 + ICON_TITLE_AT:s0 + ICON_TITLE_AT + 68] == want + bytes(68 - len(want))
              and new[s0:s0 + 6] == carddata[s0:s0 + 6] and new[s0 + 8:s0 + ICON_TITLE_AT] == carddata[s0 + 8:s0 + ICON_TITLE_AT],
              "an edited title is written in place, the rest of icon.sys unchanged")
        fresh()
        _edit(it, "ＥＰＩＩ−００\\n［", "ＥＰＩＩ−０\\n０［")
        ch, pr = plan(iso, tree)
        new = ch.get((0, "carddata.bin"), b"")
        check(not pr and struct.unpack_from("<H", new, s0 + ICON_BREAK_AT)[0] == 28, "the line break moves to where \\n stands")
        fresh()
        _edit(it, "ＸｅｎｏｓａｇａＥＰＩＩ", "ＸｅｎｏｓａｇａＥｐｉｓｏｄｅＩＩ")
        check(refused(iso, tree, "writes the slot and time"), "an edit that moves the slot and time digits is refused")
        fresh()
        _edit(it, "\\n［", "［")
        check(refused(iso, tree, "exactly one \\n"), "a title without its line break is refused")
        fresh()
        _edit(it, "］", "］" + "Ａ" * 12)
        check(refused(iso, tree, "the field holds 67"), "a title past its 68-byte field is refused")
        # subtitles
        fresh()
        sp = Subtitle().tree_path(tree, 0, "movie\\en\\s0.txt")
        _edit(sp, "Hello there", "Hello tehre")
        ch, pr = plan(iso, tree)
        check(not pr and ch[(0, "movie\\en\\s0.txt")] == subs.replace(b"Hello there", b"Hello tehre"), "a subtitle edit inside every bound")
        _edit(sp, "Hello tehre", "Hello there, this line is longer than any other")
        check(refused(iso, tree, "longest retail line"), "a subtitle line longer than the disc's longest is refused")
        fresh()
        _edit(sp, "Two\\nlines of it", "Two\\nlines\\nof it")
        check(refused(iso, tree, "3 lines"), "a subtitle cue with more lines than the disc's maximum is refused")
        fresh()
        _edit(sp, "00:03:00\t\n", "")
        check(refused(iso, tree, "cue list changed"), "a removed subtitle cue is refused")
        fresh()
        _edit(sp, "Hello there", "Hello thére")
        ch, pr = plan(iso, tree)
        check(not pr and b"Hello th\xe9re" in ch[(0, "movie\\en\\s0.txt")], "USA subtitle: é typed into a cue line is the Latin-1 byte")
        _edit(sp, "Hello thére", "Hello thñre")
        check(refused(iso, tree, "has no glyph"), "USA subtitle: a typed character with no glyph is refused")
        fresh()
        _edit(sp, ";" + "-" * 60, ";" + "-" * 3000)
        check(refused(iso, tree, "--grow") and plan(iso, tree, grow=True)[1] == [], "a subtitle file past its slot needs --grow")

    # ---- a Japanese mini disc: inline text
    import sb
    extra = ((0xD0, 0xBC),)
    font_jp = minidisc.synthetic_font(256)
    elf_jp = minidisc.synthetic_executable("jp", extra)
    pool = "あいう".encode(sbl.CODEC) + b"\x00\x00" + b"#23 hash\x00\x00\x00\x00" + "かな".encode(sbl.CODEC) + b"\x00\x00\x00\x00"
    g = struct.pack("<4I", 7, 16 + 8, 16 + 20, 0) + pool        # slot 1 -> "#23 hash" (also pushed), slot 2 -> "かな" (table only)
    code = (struct.pack("<HHH", 0x06, 16, 0) + struct.pack("<H", 0x2A)
            + struct.pack("<HHH", 0x06, 24, 0) + struct.pack("<H", 0x2A) + struct.pack("<H", 0x1D))
    ent = struct.pack("<II", 8, 0) + b"main\x00\x00\x00\x00"
    imp = struct.pack("<HH", 8, 0) + b"runtime\x00setFlag\x00"
    glob_off = 0x40 + len(code)
    ent_off = glob_off + len(g)
    imp_off = ent_off + len(ent)
    head = sb.MAGIC + struct.pack("<10I", 1, 0, 1, 1, 0x40, glob_off, glob_off + 16, ent_off, imp_off, imp_off + len(imp))
    script = head.ljust(0x40, b"\x00") + code + g + ent + imp + b"test\\x\x00"
    jfiles = {"font0.tex": font_jp, "font1.tex": font_jp, "cf\\sc0001.sb": script, "zz\\end.bin": b"E"}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        iso = minidisc.build(td / "jp.iso", jfiles, boot="SLPS_253.68", elf=elf_jp)
        tree = td / "text"

        def fresh():
            import shutil
            if tree.exists():
                shutil.rmtree(tree)
            quiet(export_text, str(iso), str(tree))

        fresh()
        f = Inline().tree_path(tree, 0, "cf\\sc0001.sb")
        t = f.read_text(encoding="utf-8")
        check("### @0x10 max 6 bytes\nあいう\n" in t and "### @0x18 max 8 bytes\n⟦23⟧23 hash\n" in t
              and "### @0x24 max 4 bytes  table\nかな\n" in t, "Japan export: inline strings, a leading # as ⟦23⟧, a table string")
        check(plan(iso, tree) == ({}, []), "the unedited Japanese tree imports as no change")
        _edit(f, "⟦23⟧23 hash", "⟦23⟧24 hash")
        _edit(f, "あいう", "あう")
        ch, pr = plan(iso, tree)
        new = sb.parse(ch[(0, "cf\\sc0001.sb")]) if ch else None
        check(not pr and new is not None and new.inline_text(24) == b"#24 hash" and new.inline_text(16) == "あう".encode(sbl.CODEC)
              and len(ch[(0, "cf\\sc0001.sb")]) == len(script), "an inline string that starts with # is edited; another is shortened in place")
        fresh()
        _edit(f, "あいう", "あいうえ")
        check(refused(iso, tree, "may not get longer"), "a longer inline string is refused")
        fresh()
        _edit(f, "かな\n", "\n")
        check(refused(iso, tree, "disappear"), "emptying a string that only a table points at is refused")
        fresh()
        _edit(f, "あいう", "あ⟦00⟧う")
        check(refused(iso, tree, "NUL"), "an embedded NUL in an inline string is refused")
        fresh()
        _edit(f, "あいう", "あ～")
        check(refused(iso, tree, "fallback cell"), "Japan: ～ (U+FF5E) has no glyph: refused")
        fresh()
        _edit(f, "あいう", "あｶ")
        check(refused(iso, tree, "half-width"), "Japan: half-width kana (0x8E) refused")
        fresh()
        t = f.read_text(encoding="utf-8")
        f.write_text(t.replace("### @0x18", "### @0x10"), encoding="utf-8")
        check(refused(iso, tree, "twice"), "an inline key twice is refused")
        f.write_text(t[:t.index("### @0x24")], encoding="utf-8")
        check(refused(iso, tree, "missing"), "a missing inline block is refused")
        f.write_text("﻿" + t.replace("あいう", "あう"), encoding="utf-8")
        check(plan(iso, tree)[1] == [] and len(plan(iso, tree)[0]) == 1, "a byte-order mark at the top of a tree file is accepted")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    flags = {f for f in a if f in ("--grow", "--rebase")}
    a = [x for x in a if x not in flags]
    if len(a) == 3 and a[0] == "export":
        return export_text(a[1], a[2])
    if len(a) == 4 and a[0] == "import":
        return import_text(a[1], a[2], a[3], grow="--grow" in flags, rebase="--rebase" in flags)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
