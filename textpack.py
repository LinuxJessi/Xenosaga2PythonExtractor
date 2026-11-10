"""textpack.py — Episode II's text as an editable UTF-8 tree, and back.

Dialogue banks are described first; menus, movie subtitles, quest text and the
battle database's strings follow the same export / import path and are
described at "the other text files" below. On a Japanese disc the dialogue is
inside the scripts ("inline text" below).

``export_text`` writes one ``.txt`` per ``.sbl`` text bank (field and scene
dialogue, map messages, quest prompts) under ``OUT/chainN/<in-game path>.txt``::

    # cf\\en\\sc1010.sbl  chain0  7 strings  stored 284 bytes, slot 2048 bytes
    ### 0004
    [Canaan]What is this thing?
    ### 0005
    #sjis: テストメッセージ      <- a reading hint, ignored on import
    11⟦83⟧e⟦83⟧X...

Each ``### NNNN`` block is one string, on one line. ``[Speaker]``, ``\\n``
(the game's own line-break code, typed as backslash + n), ``$zoom13;`` and
``$col...;`` are the game's codes: keep them. ``⟦XX⟧`` is a raw byte the
editor cannot show; keep it too. If you do type a real line break inside a
block, it is joined with the game's ``\\n`` on import.

``import_text`` re-encodes every changed file (EUC-JP), rebuilds the
``.sbl`` (strings may grow or shrink; offsets are recomputed) and writes the
result into a COPY of the disc with ``repack.patch_iso``, which reads every
write back. A bank that outgrows its slot on the disc is refused unless
``grow`` is set (``text-import --grow``); it is then appended to the image
and its TOC entry repointed.

How far a bank may grow (read from the USA code, 2026-11-10; NOT YET SEEN IN
THE RUNNING GAME). Both loaders read a file by its TOC size, rounded up to
2,048-byte sectors (``FUN_0019bc98``), into a buffer of fixed size, script
first and bank right after it:

* scenes, ``FUN_001eece0``: ``cf\\scNNNN.sb`` goes to a static buffer at
  0x3C6DC0 and ``cf\\en\\scNNNN.sbl`` to that address + the script's
  sector-rounded size. The buffer is 0x20000 bytes (the next object, the
  script-native tables, starts at 0x3E6DC0), so
  ``round2048(sb) + round2048(sbl) <= 131,072``.
* maps, ``FUN_001e00f0``: ``model\\map\\X.bin`` goes to a block of 0x12000
  bytes allocated once (``FUN_001df928(0x12000)``) and
  ``model\\map\\en\\X.sbl`` to that block + the script's size rounded up
  to 64, so ``align64(bin) + round2048(sbl) <= 73,728``.

Every retail pair obeys both rules (352 scenes, least room 10,240 bytes; 113
maps, least room 1,664 bytes). ``bank_budget`` applies them and the import
refuses a bank past its budget whether or not ``grow`` is set: past it the
load overwrites what follows the buffer. A bank at any other path has no
known loader and may not leave its slot.

    python textpack.py export DISC.iso OUTDIR
    python textpack.py import DISC.iso OUTDIR MODDED.iso [--grow]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import region
import sbl
from chains import CHAINS, ChainReader, available_chains
from iso9660 import IsoImage, SECTOR
from toc import parse_toc


def _allocations(entries) -> Dict[str, int]:
    starts = sorted({e.sector for e in entries})
    nxt = {s: n for s, n in zip(starts, starts[1:])}
    out = {}
    for e in entries:
        end = nxt.get(e.sector)
        out[e.path] = ((end - e.sector) * SECTOR if end is not None
                       else (e.size + SECTOR - 1) // SECTOR * SECTOR)
    return out


SCENE_BUFFER = 0x20000      # the static script buffer at USA 0x3C6DC0 (FUN_001eece0)
MAP_BUFFER = 0x12000        # the block FUN_001df928(0x12000) returns (FUN_001e00f0)


def _round(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def bank_budget(bank_path: str, sizes: Dict[str, int]) -> Tuple[Optional[int], str]:
    """(largest .sbl byte size the loader has room for, why) for a bank path.
    ``sizes`` maps lower-case TOC path -> stored size. (None, reason) when no
    loader is known for the path or its script is missing."""
    low = bank_path.lower()
    head, _, name = low.rpartition("\\")
    parent, _, lang = head.rpartition("\\")
    stem = name.rsplit(".", 1)[0]
    if parent == "cf":
        script, buf, align, who = f"cf\\{stem}.sb", SCENE_BUFFER, SECTOR, "FUN_001eece0"
    elif parent == "model\\map":
        script, buf, align, who = f"model\\map\\{stem}.bin", MAP_BUFFER, 64, "FUN_001e00f0"
    else:
        return None, "no loader is known for a bank at this path"
    if script not in sizes:
        return None, f"its script {script} is not in the table of contents"
    used = _round(sizes[script], align)
    room = (buf - used) // SECTOR * SECTOR          # the bank is read in whole sectors
    return room, (f"{who} loads {script} ({sizes[script]} bytes, {used} with rounding) and then this bank "
                  f"into one {buf}-byte buffer")


def _sbl_entries(iso: IsoImage):
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        alloc = _allocations(entries)
        sizes = {e.path.lower(): e.size for e in entries}
        for e in entries:
            if e.ext == "sbl":
                yield chain, reader, e, alloc[e.path], sizes


# ------------------------------------------------ inline text (Japan, the demo)
#
# The Japanese discs have no .sbl: a pushtext operand is the byte offset of a
# NUL-terminated EUC-JP string in the script's own globals (sb.py). Export
# writes OUT/chainN/<script>.inline.txt with one ``### @0xOFFSET`` block per
# string; import re-encodes and writes each string back IN PLACE, so a string
# may shrink (NUL-padded) but never grow: every other pushtext and every global
# slot keep their offsets, and the .sb keeps its size.
#
# A string is listed when a pushtext names it or when a variable slot holds its
# offset (``sb.Script.pointer_slots``; the block header then says "table").
# Japan Disc 1: 17,053 + 172 = 17,225 strings in 592 scripts, which is every
# string of every text pool. A rebuild that lets strings grow would have to
# move the pool and rewrite both the pushtext operands and those slots; the kit
# does not offer one.

def _inline_entries(iso: IsoImage):
    import sb
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        for e in entries:
            if e.ext not in ("sb", "bin") or e.compressed:
                continue
            data = reader.read(e.byte_offset, e.size)
            if data[:4] != sb.MAGIC:
                continue
            s = sb.parse(data)
            if s.text_kind in ("inline", "mixed"):
                yield chain, e, data, s


def _inline_path(root: Path, chain: int, path: str) -> Path:
    return root / f"chain{chain}" / Path(*path.split("\\")).with_suffix(".inline.txt")


def export_inline(iso: IsoImage, root: Path) -> Tuple[int, int]:
    import sb
    n_files = n_strings = 0
    for chain, e, data, s in _inline_entries(iso):
        lines = [f"# {e.path}  chain{chain}  inline strings (grow refused; shrink is NUL-padded)"]
        for o, how in s.text_offsets():
            raw = s.inline_text(o)
            lines += [f"### @{o:#x} max {len(raw)} bytes" + ("  table" if how == "table" else ""),
                      sbl.to_text(raw)]
            n_strings += 1
        dest = _inline_path(root, chain, e.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n_files += 1
    return n_files, n_strings


def plan_inline(iso: IsoImage, root: Path, changed, problems) -> None:
    import struct
    for chain, e, data, s in _inline_entries(iso):
        src = _inline_path(root, chain, e.path)
        if not src.exists():
            continue
        glob_off = struct.unpack_from("<I", data, 0x18)[0]
        known = {o for o, _ in s.text_offsets()}
        out = bytearray(data)
        cur = None
        blocks = {}
        for line in src.read_text(encoding="utf-8").split("\n"):
            if line.startswith("### @"):
                cur = int(line[5:].split()[0], 16)
                blocks[cur] = []
            elif cur is not None and not line.startswith("#"):
                blocks[cur].append(line)
        for o, b in blocks.items():
            while len(b) > 1 and b[-1] == "":
                b.pop()
            old = s.inline_text(o) if o in known else None
            if old is None:
                problems.append(f"{e.path}: @{o:#x} is not a string this script's export lists")
                continue
            new = sbl.from_text("\\n".join(b))
            if new == old:
                continue
            if len(new) > len(old):
                problems.append(f"{e.path}: @{o:#x} grows {len(old)} -> {len(new)} bytes (in-place only)")
                continue
            a = glob_off + o
            out[a:a + len(old)] = new + b"\x00" * (len(old) - len(new))
        if bytes(out) != data:
            changed[(chain, e.path)] = bytes(out)


# ------------------------------------------------------ the other text files
#
# Four more kinds of text, each with its own file in the tree:
#
#   yosikawa\*.txd                 menus      X.txd.txt      ### NNNN blocks, like a bank
#   movie\en\*.txt, movie\*.txt    subtitles  X.sub.txt      the file itself, as UTF-8
#   iwase\*.txt                    quests     X.sjis.txt     the file itself (Shift-JIS), as UTF-8
#   yamamoto\batdat.us|.bin        battle db  batdat.XX.txt  ### S<section> @<offset> max N bytes
#
# Every kind is exact both ways: rebuilding from an unedited export gives the
# original bytes (checked for every such object on USA Disc 1 and 2, Japan
# Disc 1 and 2 and the demo), so an untouched file is never written. What may
# change, and why:
#
#   .txd       strings may grow and shrink (the offset table is rebuilt); the
#              file must stay inside its slot. The game reads these into static
#              buffers (FUN_00228570: MainMenu.txd to 0x6A5BC0, Tutorial.txd
#              right after it at 0x6A9BC0, so 16,384 bytes for the first); a
#              slot is whole sectors and the read is too, so staying in the slot
#              leaves the read unchanged. Never relocated.
#   subtitles  the 64-byte rule of moviesub.py; --grow may relocate the file
#              (a relocated, comment-grown subtitle file played in the running
#              game, docs/MODDING.md).
#   quests     must stay inside its slot; never relocated (loader not read).
#   battle db  each string in place, same length or shorter
#              (smallfiles.batdat_replace says why).

class TextError(ValueError):
    pass


_SJIS_LEAD = set(range(0x81, 0xA0)) | set(range(0xE0, 0xFD))


def file_to_text(data: bytes, codec: str) -> str:
    """A whole text file as editable text, lossless: line ends and tabs as they
    are, characters where they decode and re-encode to the same bytes, ⟦XX⟧ for
    every other byte. ``codec`` is the kit codec (EUC-JP) or "shift_jis"."""
    sjis = codec == "shift_jis"
    out, i, n = [], 0, len(data)
    while i < n:
        b = data[i]
        if b < 0x80:
            out.append(chr(b) if b >= 0x20 and b != 0x7F or b in (9, 10, 13) else f"⟦{b:02X}⟧")
            i += 1
            continue
        if sjis:
            size = 2 if b in _SJIS_LEAD else 1
        else:
            size = 3 if b == 0x8F else 2
        piece = data[i:i + size]
        try:
            ch = piece.decode(codec)
            if len(ch) == 1 and ch.encode(codec) == piece and ch not in "⟦⟧":
                out.append(ch)
                i += size
                continue
        except UnicodeDecodeError:
            pass
        out.append(f"⟦{b:02X}⟧")
        i += 1
    return "".join(out)


def text_to_file(text: str, codec: str) -> bytes:
    out, pos = bytearray(), 0
    try:
        for m in sbl._ESC.finditer(text):
            out += text[pos:m.start()].encode(codec)
            out.append(int(m.group(1), 16))
            pos = m.end()
        out += text[pos:].encode(codec)
    except UnicodeEncodeError as e:
        bad = e.object[e.start:e.start + 1]
        raise TextError(f"{bad!r} (U+{ord(bad):04X}) cannot be written in {codec}, near "
                        f"{e.object[max(0, e.start - 8):e.start + 8]!r}")
    return bytes(out)


def txd_parse(data: bytes) -> Tuple[List[bytes], bytes]:
    """(strings, tail) of a .txd: u32 offsets (count = first offset / 4), then the
    NUL-terminated strings back to back in table order; ``tail`` is whatever
    follows the last string (zero bytes, or nothing)."""
    import struct
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
    import struct
    offs, body = [], bytearray()
    for s in strings:
        if b"\x00" in s:
            raise TextError("a string may not contain NUL")
        offs.append(4 * len(strings) + len(body))
        body += s + b"\x00"
    return struct.pack(f"<{len(strings)}I", *offs) + bytes(body) + tail


def _blocks(text: str, marker: str = "### ") -> List[Tuple[str, bytes]]:
    """(header after the marker, encoded text) of every block of a block file."""
    out: List[Tuple[str, List[str]]] = []
    for line in text.split("\n"):
        if line.startswith(marker):
            out.append((line[len(marker):].strip(), []))
        elif out and not line.startswith("#"):
            out[-1][1].append(line.rstrip("\r"))
    res = []
    for head, b in out:
        while len(b) > 1 and b[-1] == "":
            b.pop()
        try:
            res.append((head, sbl.from_text("\\n".join(b))))
        except UnicodeEncodeError as e:
            bad = e.object[e.start:e.start + 1]
            raise TextError(f"block {head}: {bad!r} (U+{ord(bad):04X}) is not in the game's character set "
                            f"({sbl.CODEC})")
    return res


def _txd_export(path: str, data: bytes) -> str:
    strings, tail = txd_parse(data)
    if txd_build(strings, tail) != data or any(tail):
        raise TextError("does not rebuild to the same bytes; left alone")
    lines = [f"# {path}  {len(strings)} strings  (menu text; keep %p, %d and the game's codes)"]
    for i, s in enumerate(strings):
        lines += [f"### {i:04d}", sbl.to_text(s)]
    return "\n".join(lines) + "\n"


def _txd_rebuild(path: str, data: bytes, text: str) -> bytes:
    strings, tail = txd_parse(data)
    new = [b for _, b in _blocks(text)]
    if len(new) != len(strings):
        raise TextError(f"{len(new)} strings, the disc has {len(strings)} (do not add or remove ### blocks)")
    return data if new == strings else txd_build(new, tail)


def _sub_rebuild(path: str, data: bytes, text: str) -> bytes:
    import moviesub
    new = text_to_file(text, sbl.CODEC)
    if new != data:
        why = moviesub.check_edit(data, new)
        if why:
            raise TextError(why)
    return new


def _batdat_export(path: str, data: bytes) -> str:
    import smallfiles
    rows = smallfiles.batdat_raw_strings(data)
    lines = [f"# {path}  {len(rows)} strings  (replaced in place: same length or shorter, never longer)"]
    for si, o, raw in rows:
        lines += [f"### S{si:02d} @{o:#06x} max {len(raw)} bytes", sbl.to_text(raw)]
    return "\n".join(lines) + "\n"


def _batdat_rebuild(path: str, data: bytes, text: str) -> bytes:
    import smallfiles
    known = {(si, o): raw for si, o, raw in smallfiles.batdat_raw_strings(data)}
    new = {}
    for head, raw in _blocks(text):
        try:
            sec, off = head.split()[:2]
            key = (int(sec[1:]), int(off[1:], 16))
        except (ValueError, IndexError):
            raise TextError(f"bad block header '### {head}'")
        if key not in known:
            raise TextError(f"### {head}: not a string of this database")
        if raw != known[key]:
            new[key] = raw
    try:
        return smallfiles.batdat_replace(data, new) if new else data
    except smallfiles.SmallFileError as e:
        raise TextError(str(e))


class Kind:
    def __init__(self, name, suffix, match, export, rebuild, relocatable, why_fixed=""):
        self.name, self.suffix, self.match = name, suffix, match
        self.export, self.rebuild = export, rebuild
        self.relocatable, self.why_fixed = relocatable, why_fixed


def _is_quest(low: str) -> bool:
    return low.replace("/", "\\").startswith("iwase\\") and low.endswith(".txt") and low.count("\\") + low.count("/") == 1


KINDS = (
    Kind("menu", ".txd.txt", lambda low: low.endswith(".txd"), _txd_export, _txd_rebuild, False,
         "the game reads menu text into static buffers of fixed size"),
    Kind("subtitle", ".sub.txt", region.is_movie_subtitle,
         lambda path, data: file_to_text(data, sbl.CODEC), _sub_rebuild, True),
    Kind("quest", ".sjis.txt", _is_quest,
         lambda path, data: file_to_text(data, "shift_jis"),
         lambda path, data, text: text_to_file(text, "shift_jis"), False,
         "its loader has not been read"),
    Kind("battle", ".txt", lambda low: low in ("yamamoto\\batdat.us", "yamamoto\\batdat.bin"),
         _batdat_export, _batdat_rebuild, False, "the game loads it into a static buffer"),
)


def _kind_of(path: str):
    low = path.lower()
    for k in KINDS:
        if k.match(low):
            return k
    return None


def _kind_path(root: Path, chain: int, path: str, kind: Kind) -> Path:
    p = root / f"chain{chain}" / Path(*path.replace("/", "\\").split("\\"))
    if kind.name == "battle":
        return p.with_name(p.name + ".txt")            # batdat.us.txt / batdat.bin.txt
    return p.with_suffix(kind.suffix)


def _kind_entries(iso: IsoImage):
    import arx
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        alloc = _allocations(entries)
        for e in entries:
            kind = _kind_of(e.path)
            if kind is None:
                continue
            data = reader.read(e.byte_offset, e.size)
            if e.compressed:
                data = arx.decompress(data)
            yield chain, e, kind, data, alloc[e.path]


def export_kinds(iso: IsoImage, root: Path) -> Dict[str, int]:
    """Write the menu, subtitle, quest and battle text; {kind: files written}. A file
    that would not rebuild to its own bytes is reported and left out."""
    done: Dict[str, int] = {}
    for chain, e, kind, data, alloc in _kind_entries(iso):
        try:
            text = kind.export(e.path, data)
            if kind.rebuild(e.path, data, text) != data:
                raise TextError("does not rebuild to the same bytes")
        except TextError as ex:
            print(f"text-export: {e.path} left out: {ex}", file=sys.stderr)
            continue
        dest = _kind_path(root, chain, e.path, kind)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="") as fh:     # line ends exactly as in the game's file
            fh.write(text)
        done[kind.name] = done.get(kind.name, 0) + 1
    return done


def plan_kinds(iso: IsoImage, root: Path, changed, problems, grow: bool = False) -> None:
    for chain, e, kind, data, alloc in _kind_entries(iso):
        src = _kind_path(root, chain, e.path, kind)
        if not src.exists():
            continue
        with open(src, encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
        try:
            new = kind.rebuild(e.path, data, text)
        except TextError as ex:
            problems.append(f"{e.path}: {ex}")
            continue
        if new == data:
            continue
        if e.compressed:
            problems.append(f"{e.path}: stored compressed; text-import does not rewrite compressed text")
            continue
        if len(new) > alloc:
            if not kind.relocatable:
                problems.append(f"{e.path}: {len(new)} bytes, {len(new) - alloc} more than its {alloc}-byte "
                                f"slot; this file is not relocated ({kind.why_fixed}). Shorten other text "
                                f"in the same file")
                continue
            if not grow:
                problems.append(f"{e.path}: {len(new)} bytes exceeds its {alloc}-byte slot "
                                f"(over by {len(new) - alloc}; --grow relocates it)")
                continue
        changed[(chain, e.path)] = new


def _sjis_hint(s: bytes) -> str:
    try:
        t = s.decode("shift_jis")
    except UnicodeDecodeError:
        return ""
    return t if any(ord(c) > 0x7F for c in t) else ""


def _out_path(root: Path, chain: int, path: str) -> Path:
    return root / f"chain{chain}" / Path(*path.split("\\")).with_suffix(".sbl.txt")


def export_text(iso_path: str, out_dir: str) -> int:
    iso = IsoImage(iso_path)
    root = Path(out_dir)
    n_files = n_strings = 0
    for chain, reader, e, alloc, sizes in _sbl_entries(iso):
        data = reader.read(e.byte_offset, e.size)
        strings = sbl.parse(data)
        room, _why = bank_budget(e.path, sizes)
        lines = [f"# {e.path}  chain{chain}  {len(strings)} strings  "
                 f"stored {e.size} bytes, slot {alloc} bytes"
                 + (f", loader room {room} bytes (--grow)" if room is not None else "")]
        for i, s in enumerate(strings):
            lines.append(f"### {i:04d}")
            t = sbl.to_text(s)
            if "⟦" in t:
                hint = _sjis_hint(s)
                if hint:
                    lines.append("#sjis: " + hint.replace("\n", " "))
            lines.append(t)
        dest = _out_path(root, chain, e.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n_files += 1
        n_strings += len(strings)
    print(f"exported {n_strings} strings from {n_files} .sbl files to {root}", file=sys.stderr)
    fi, si = export_inline(iso, root)
    if fi:
        print(f"exported {si} inline strings from {fi} .sb scripts (Japan/demo layout) to {root}", file=sys.stderr)
    done = export_kinds(iso, root)
    if done:
        print("exported " + ", ".join(f"{n} {k} file(s)" for k, n in sorted(done.items())) + f" to {root}",
              file=sys.stderr)
    return 0


def read_text_file(path: Path) -> List[bytes]:
    blocks: List[List[str]] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("### "):
            blocks.append([])
        elif blocks and not line.startswith("#"):
            blocks[-1].append(line)
    out = []
    for b in blocks:
        while len(b) > 1 and b[-1] == "":
            b.pop()
        out.append(sbl.from_text("\\n".join(b)))
    return out


def plan_import(iso_path: str, text_dir: str,
                grow: bool = False) -> Tuple[Dict[Tuple[int, str], bytes], List[str]]:
    iso = IsoImage(iso_path)
    root = Path(text_dir)
    changed: Dict[Tuple[int, str], bytes] = {}
    problems: List[str] = []
    for chain, reader, e, alloc, sizes in _sbl_entries(iso):
        src = _out_path(root, chain, e.path)
        if not src.exists():
            continue
        data = reader.read(e.byte_offset, e.size)
        old = sbl.parse(data)
        new = read_text_file(src)
        if len(new) != len(old):
            problems.append(f"{e.path}: {len(new)} strings, the disc has {len(old)} (do not add or remove ### blocks)")
            continue
        if new == old:
            continue
        blob = sbl.build(new, data)
        room, why = bank_budget(e.path, sizes)
        if room is not None and len(blob) > room:
            problems.append(f"{e.path}: rebuilt size {len(blob)} is {len(blob) - room} bytes past the "
                            f"{room} bytes the game has room for ({why}); shorten the text, "
                            f"relocating cannot help")
            continue
        if len(blob) > alloc:
            if room is None:
                problems.append(f"{e.path}: rebuilt size {len(blob)} exceeds its {alloc}-byte slot and "
                                f"cannot be relocated: {why}")
                continue
            if not grow:
                problems.append(f"{e.path}: rebuilt size {len(blob)} exceeds its {alloc}-byte slot "
                                f"(over by {len(blob) - alloc}; --grow relocates it to the end of the "
                                f"image, loader room {room} bytes)")
                continue
        changed[(chain, e.path)] = blob
    plan_inline(iso, root, changed, problems)
    plan_kinds(iso, root, changed, problems, grow=grow)
    return changed, problems


def import_text(iso_path: str, text_dir: str, out_iso: str, grow: bool = False) -> int:
    from repack import patch_iso

    changed, problems = plan_import(iso_path, text_dir, grow=grow)
    for p in problems:
        print("refused: " + p, file=sys.stderr)
    if problems:
        return 1
    if not changed:
        print("no text changed; nothing written", file=sys.stderr)
        return 0
    if Path(out_iso).resolve() != Path(iso_path).resolve():
        print(f"copying {iso_path} -> {out_iso} ...", file=sys.stderr)
        shutil.copyfile(iso_path, out_iso)
    reports = patch_iso(out_iso, changed, grow=grow)
    moved = [r for r in reports if r.relocated_lba is not None]
    print(f"patched {len(changed)} text file(s) into {out_iso}"
          + (f"; {len(moved)} relocated to the end of the image (not yet seen in the running game)"
             if moved else ""), file=sys.stderr)
    return 0


def _edit_block(path: Path, index: int, new_text: str, marker: str = "### ") -> None:
    """Replace the text of block ``index`` (0-based) of an exported file (self-tests)."""
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

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    def quiet(fn, *a, **k):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    # the loader budgets
    sizes = {"cf\\sc0001.sb": 3000, "model\\map\\x.bin": 70004}
    check(bank_budget("cf\\en\\sc0001.sbl", sizes)[0] == SCENE_BUFFER - 4096, "scene bank: 0x20000 less the script's sectors")
    check(bank_budget("model\\map\\en\\X.sbl", sizes)[0] == 2048, "map bank: 0x12000 less the script rounded to 64, whole sectors")
    check(bank_budget("yamamoto\\think\\jp\\think001.sbl", sizes)[0] is None
          and bank_budget("cf\\en\\sc9999.sbl", sizes)[0] is None, "no budget without a known loader or script")

    scene = [b"[A]One.", b"[B]Two, a longer line.", b"<\xa2\xa2> three"]
    files = {
        "cf\\sc0001.sb": b"\x00" * 3000,                       # not parsed: only its size matters here
        "cf\\en\\sc0001.sbl": sbl.build(scene),
        "model\\map\\X.bin": b"\x00" * 70004,
        "model\\map\\en\\X.sbl": sbl.build([b"map line"]),
        "other\\en\\odd.sbl": sbl.build([b"odd"]),
        "zz\\end.bin": b"E" * 100,
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        iso = minidisc.build(td / "mini.iso", files)
        tree = td / "text"
        quiet(export_text, str(iso), str(tree))
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and not pr, "an unedited export imports as no change")
        f = _out_path(tree, 0, "cf\\en\\sc0001.sbl")
        check("loader room 126976" in f.read_text(encoding="utf-8").split("\n")[0], "the export header states the loader room")
        _edit_block(f, 1, "[B]Two!")
        ch, pr = plan_import(str(iso), str(tree))
        check(list(ch) == [(0, "cf\\en\\sc0001.sbl")] and not pr
              and sbl.parse(ch[(0, "cf\\en\\sc0001.sbl")]) == [scene[0], b"[B]Two!", scene[2]], "one edited line changes one bank")
        _edit_block(f, 1, "[B]" + "long " * 500)                # 2.5 KB: past the one-sector slot
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and len(pr) == 1 and "--grow" in pr[0], "a bank past its slot is refused without --grow")
        out = td / "grown.iso"
        rc = quiet(import_text, str(iso), str(tree), str(out), grow=True)
        got = sbl.parse(minidisc.read(out, "cf\\en\\sc0001.sbl"))
        check(rc == 0 and got[1] == b"[B]" + b"long " * 500 and got[0] == scene[0]
              and out.stat().st_size == iso.stat().st_size + 2 * SECTOR, "--grow relocates it; the image gains its sectors")
        check(all(minidisc.read(out, p) == d for p, d in files.items() if p != "cf\\en\\sc0001.sbl"),
              "every other object is unchanged")
        tree2 = td / "text2"
        quiet(export_text, str(out), str(tree2))
        ch, pr = plan_import(str(out), str(tree2))
        check(not ch and not pr, "the grown image exports and re-imports as no change")
        # the map bank has 2,048 bytes of loader room: more is refused even with --grow
        quiet(export_text, str(iso), str(tree))
        m = _out_path(tree, 0, "model\\map\\en\\X.sbl")
        _edit_block(m, 0, "x" * 3000)
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(not ch and len(pr) == 1 and "FUN_001e00f0" in pr[0] and "relocating cannot help" in pr[0],
              "a bank past the loader's buffer is refused even with --grow")
        _edit_block(m, 0, "map line")
        o = _out_path(tree, 0, "other\\en\\odd.sbl")
        _edit_block(o, 0, "y" * 3000)
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(not ch and len(pr) == 1 and "no loader is known" in pr[0], "a bank with no known loader may not leave its slot")
        _edit_block(o, 0, "odd")
        _edit_block(f, 0, "[A]One.")
        (tree / "chain0" / "cf" / "en" / "sc0001.sbl.txt").write_text(
            f.read_text(encoding="utf-8").replace("### 0002", "### 0002\n### 0003"), encoding="utf-8")
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and any("do not add or remove" in p for p in pr), "an added block is refused")

    # the other kinds: menus (.txd), movie subtitles, quest text (Shift-JIS), the battle database
    import struct
    check(file_to_text(b"a\tb\r\n\x01\xa4\xa2\xff", sbl.CODEC) == "a\tb\r\n⟦01⟧あ⟦FF⟧"
          and text_to_file("a\tb\r\n⟦01⟧あ⟦FF⟧", sbl.CODEC) == b"a\tb\r\n\x01\xa4\xa2\xff", "whole-file text is lossless (EUC-JP)")
    sj = "Name:\"テスト\"\r\nｶﾅ\x00".encode("shift_jis") + b"\xfd"
    check(text_to_file(file_to_text(sj, "shift_jis"), "shift_jis") == sj and "テスト" in file_to_text(sj, "shift_jis"),
          "whole-file text is lossless (Shift-JIS)")
    menu = [b"New Game", b"Load", b"%p", b""]
    check(txd_parse(txd_build(menu, b"\x00\x00")) == (menu, b"\x00\x00"), ".txd build/parse round trip, tail kept")
    subs = (b";" + b"-" * 60 + b"\n0\t$ay390;\n00:01:00\tHello there\n00:02:00\t\nend\n")
    pool = b"Medica\x00One ally\x00Revert\x00"
    sec0 = struct.pack("<4H", 16, 23, 7, 0) + struct.pack("<4H", 32, 23, 16, 23) + pool
    bd = struct.pack("<III", 1, 12 + len(sec0), 12) + sec0
    quest = "No.1\r\n{\r\n\tName:\"テスト\"\r\n}\r\n".encode("shift_jis")
    files = {"yosikawa\\Menu.txd": txd_build(menu), "movie\\en\\s0.txt": subs,
             "iwase\\Quest.txt": quest, "yamamoto\\batdat.us": bd, "zz\\end.bin": b"E" * 10}
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        iso = minidisc.build(td / "mini.iso", files)
        tree = td / "text"
        quiet(export_text, str(iso), str(tree))
        names = sorted(p.name for p in tree.rglob("*.txt"))
        check(names == ["Menu.txd.txt", "Quest.sjis.txt", "batdat.us.txt", "s0.sub.txt"], "one tree file per object, by kind")
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and not pr, "unedited menus, subtitles, quests and battle text import as no change")

        def edit(rel, old_, new_):
            f = tree / "chain0" / rel
            with open(f, encoding="utf-8", newline="") as fh:
                t = fh.read()
            assert old_ in t
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(t.replace(old_, new_, 1))

        edit("yosikawa/Menu.txd.txt", "Load", "Load Game")
        edit("movie/en/s0.sub.txt", "Hello there", "Hello, there")
        edit("iwase/Quest.sjis.txt", "テスト", "テスト２")
        edit("yamamoto/batdat.us.txt", "One ally", "One")
        ch, pr = plan_import(str(iso), str(tree))
        check(len(ch) == 4 and not pr, "one edit in each changes four objects")
        out = td / "out.iso"
        quiet(import_text, str(iso), str(tree), str(out))
        check(txd_parse(minidisc.read(out, "yosikawa\\Menu.txd"))[0] == [b"New Game", b"Load Game", b"%p", b""]
              and minidisc.read(out, "movie\\en\\s0.txt") == subs.replace(b"Hello there", b"Hello, there")
              and minidisc.read(out, "iwase\\Quest.txt") == quest.replace("テスト".encode("shift_jis"), "テスト２".encode("shift_jis"))
              and minidisc.read(out, "yamamoto\\batdat.us") == bd.replace(b"One ally", b"One\x00\x00\x00\x00\x00")
              and minidisc.read(out, "zz\\end.bin") == b"E" * 10 and out.stat().st_size == iso.stat().st_size,
              "the image holds exactly the four edits")
        quiet(export_text, str(iso), str(tree))
        edit("movie/en/s0.sub.txt", "Hello there", "Hello there" + "!" * 64)
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(not ch and len(pr) == 1 and "64-byte boundary" in pr[0], "subtitle text past the 64-byte boundary is refused")
        quiet(export_text, str(iso), str(tree))
        edit("movie/en/s0.sub.txt", ";" + "-" * 60, ";" + "-" * 3000)
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and len(pr) == 1 and "--grow" in pr[0], "a subtitle file past its slot needs --grow")
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(len(ch) == 1 and not pr, "... and with --grow a longer comment is accepted (comments are not counted)")
        quiet(export_text, str(iso), str(tree))
        edit("yamamoto/batdat.us.txt", "Medica", "Medica+")
        edit("yosikawa/Menu.txd.txt", "Load", "L" * 3000)
        edit("iwase/Quest.sjis.txt", "テスト", "漢" * 1500)
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(not ch and len(pr) == 3 and sum("not relocated" in p for p in pr) == 2
              and sum("may not get longer" in p for p in pr) == 1,
              "a longer battle string and a menu or quest file past its slot are refused, --grow or not")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if len(a) == 3 and a[0] == "export":
        return export_text(a[1], a[2])
    grow = "--grow" in a
    a = [x for x in a if x != "--grow"]
    if len(a) == 4 and a[0] == "import":
        return import_text(a[1], a[2], a[3], grow=grow)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
