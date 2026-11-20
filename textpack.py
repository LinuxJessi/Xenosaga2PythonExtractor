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
  (the export writes reading hints that way). To start a line of text with
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
* inline, battle: each string in place, same length or shorter.
* menu, quest: rebuilt or rewritten; the file may not need more sectors than
  the original (that is what the loader read; the slot could be larger).
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


def _sjis_hint(s: bytes) -> str:
    try:
        t = s.decode("shift_jis")
    except UnicodeDecodeError:
        return ""
    return t if any(ord(c) > 0x7F for c in t) else ""


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
        for i, s in enumerate(strings):
            lines.append(f"### {i:04d}")
            t = ctx.codec.to_text(s)
            if "⟦" in t:
                hint = _sjis_hint(s)
                if hint:
                    lines.append("#sjis: " + hint.replace("\n", " "))
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
        lines = [f"# {path}  {len(strings)} strings  (menu text; keep %p, %d and the game's codes)"]
        for i, s in enumerate(strings):
            lines += [f"### {i:04d}", ctx.codec.to_text(s)]
        return "\n".join(lines) + "\n"

    def rebuild(self, ctx, path, data, text):
        old, tail = txd_parse(data)
        keys = [f"{i:04d}" for i in range(len(old))]
        new = [_encode(ctx, ctx.codec, k, o, b) for k, o, b in zip(keys, old, keyed_bodies(text, keys))]
        if new == old:
            return data
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
            blob = smallfiles.batdat_replace(data, new)
        except smallfiles.SmallFileError as e:
            raise TextError(str(e))
        if smallfiles.batdat_raw_strings(blob) != want:
            got = {(si, o) for si, o, _ in smallfiles.batdat_raw_strings(blob)}
            lost = [k for k, (si, o, _) in zip(keys, rows) if (si, o) not in got]
            raise TextError(f"the edit would change which strings the next export finds"
                            + (f" ({len(lost)} would disappear, the first ### {lost[0]})" if lost else "")
                            + ": a section is recognised by the first byte of its first string, which must stay "
                              "a printable ASCII or EUC byte")
        return blob

    def limit(self, ctx, e, data, new, alloc, sizes, grow):
        return None if len(new) == len(data) else "the battle database changed size"


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

    def export(self, ctx, path, data, info):
        return ctx.sjis.to_text(data, file=True)

    def rebuild(self, ctx, path, data, text):
        old_text = ctx.sjis.to_text(data, file=True)
        if text == old_text:
            return data
        if "\r\n" in old_text and "\r" not in text and "\n" in text:
            # an editor saved the file with LF line ends: the game skips CR LF inside a text but copies
            # a lone LF as a line break, so the file's own CR LF are restored, every line alike
            text = text.replace("\n", "\r\n")
        elif "\r\n" in old_text and text.replace("\r\n", "").count("\n"):
            raise TextError("the file mixes CR LF and LF line ends: keep CR LF line ends, as exported (the game "
                            "skips CR LF inside a text and copies a lone LF as a line break)")
        new = ctx.sjis.from_text(text)
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
            old_lines = set(old_text.split("\n"))
            for n, ln in enumerate(text.split("\n"), 1):
                if ln not in old_lines and any(ch and ord(ch) > 0x7F for ch, _ in textcodec.tokens(ln)):
                    raise TextError(f"line {n}: a non-ASCII character in a USA quest file. How the USA build "
                                    f"reads bytes above 0x7F in these files has not been read; write plain "
                                    f"ASCII, or \u27e6XX\u27e7 escapes if you know the bytes")
            layout = QUEST_LAYOUTS.get(path.lower().replace("/", "\\"))
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
    for chain, e, kind, stored, data, alloc, sizes in _objects(iso):
        info = {"chain": chain, "alloc": alloc, "sizes": sizes}
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

def plan_import(iso_path: str, text_dir: str, grow: bool = False, rebase: bool = False
                ) -> Tuple[Dict[Tuple[int, str], bytes], List[str]]:
    """({(chain, path): new bytes}, problems). Raises region.UnknownBuild for a disc
    the kit does not know. With any problem, nothing should be written."""
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
    iso.close()
    if manifest is not None:
        absent = sorted(k for k in manifest.get("objects", {}) if k not in matched and (root / k).exists())
        if absent:                                   # e.g. a Disc 1 tree given Disc 2: its own-chain files
            print(f"note: {len(absent)} text file(s) of this export have no object on this disc and were not "
                  f"applied (e.g. {absent[0]})", file=sys.stderr)
    return changed, problems


def import_text(iso_path: str, text_dir: str, out_iso: str, grow: bool = False, rebase: bool = False) -> int:
    from repack import RepackError, patch_copy, same_file

    if same_file(iso_path, out_iso):
        print(f"refused: --out {out_iso} is the input image itself; the kit never patches its source. "
              f"Give another file name", file=sys.stderr)
        return 1
    try:
        changed, problems = plan_import(iso_path, text_dir, grow=grow, rebase=rebase)
    except region.UnknownBuild as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
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
    pool = b"Medica\x00One ally\x00Revert\x00"
    sec0 = struct.pack("<4H", 16, 23, 7, 0) + struct.pack("<4H", 32, 23, 16, 23) + pool
    bd = struct.pack("<III", 1, 12 + len(sec0), 12) + sec0
    quest = "No.1\r\n{\r\n\tName:\"Sikes\"\r\n}\r\n".encode("shift_jis")
    treasure = "".join(f"No.{i + 1}\r\n{{\r\n\tItem:\"Potion\"\r\n\tLocate:\"Here\"\r\n\tExpl:\"Heals\\nyou.\"\r\n}}\r\n"
                       for i in range(18)).encode("ascii")
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
        "model\\map\\X.bin": b"\x00" * 70004,
        "model\\map\\en\\X.sbl": sbl.build([b"map line"]),
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
        check(names == ["Menu.txd.txt", "Quest.sjis.txt", "Treasure.sjis.txt", "X.sbl.txt", "batdat.us.txt",
                         "carddata.bin.title.txt", "odd.sbl.txt", "s0.sub.txt", "sc0001.sbl.txt"],
              "one tree file per text object, by kind")
        man = json.loads((tree / MANIFEST).read_text(encoding="utf-8"))
        check(man["build"] == "us" and man["serials"] == ["SLUS_208.92"] and len(man["objects"]) == 9
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
        _edit(bt, "Medica", "Medica+")
        check(refused(iso, tree, "may not get longer"), "a longer battle string is refused")
        fresh()
        _edit(bt, "Medica", "⟦01⟧edica")
        check(refused(iso, tree, "disappear"), "a battle edit that would hide a section from the next export is refused")
        fresh()
        t = bt.read_text(encoding="utf-8")
        a, b = t.index("### S00 @0x0010"), t.index("### S00 @0x0017")
        bt.write_text(t[:b] + t[a:b] + t[b:], encoding="utf-8")
        check(refused(iso, tree, "twice"), "a battle block given twice is refused")
        bt.write_text(t[:a] + t[b:], encoding="utf-8")
        check(refused(iso, tree, "missing"), "a missing battle block is refused")
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
        check(refused(iso, tree, "non-ASCII"), "USA quest text: a typed non-ASCII character is refused (reader not read)")
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
