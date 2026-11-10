"""toc.py — Parse (and rebuild) Xenosaga Episode II's binary TOC files.

Episode II keeps Episode I's table of contents (``XENOSAGA.00`` / ``.10`` /
``.20``) and adds one thing: every name is **front-coded** against the
previous name, so a run of ``020300_mobj200.xap``, ``020300_mobj201.xap``
costs one byte of name each. Episode III's ``X3.00`` / ``X3.10`` use exactly
this grammar too (all 13,516 entries agree with Lybac's 2006 tables), and its
filler still says "Episode.2".

Grammar (reverse-engineered 2026-11-05; 9,030 entries over the three TOCs
parse with no desync, extents tile each chain with no gap or overlap, and
``serialize(parse(toc)) == toc`` byte for byte on all three)::

    toc    = [u8 own size in sectors] item* [0x00]* filler
    item   = file | cfile | dir | pop
    file   = [b]      [code] [literal: b-2 bytes]    [u24le sector] [u32le size]
    cfile  = [b|0x40] [code] [literal: (b&0x3f)-2]   [u24le sector] [u32le csize] [u24le usize]
    dir    = [0x80|n] [u8 pops] [code] [literal: n-3 bytes]
    pop    = [0x00]                          (closes one directory level)
    filler = "MONOLITHSOFT Xenosaga Episode.2\\0" repeated, phase-locked to
             (absolute file offset % 32)

Front coding (``code``)::

    code == 0 : the literal is the whole name plus its NUL
    else      : H = code >> 4, L = code & 15 == len(literal)
                p = len(prev) - H - 1
                name = prev[:p] + literal + prev[p + L:]   then cut at the first NUL

``prev`` is the previous *file* name (the first file after a directory is
always written whole); directories front-code against the previous directory
at the same depth. A literal carries a NUL exactly when the new name's length
differs from ``prev``'s, because the engine edits one name buffer in place.

Entry sectors are relative to the start of the TOC file itself: the TOC's
first byte equals its own size in sectors, and the data continues seamlessly
into the sibling bigfiles (see ``chains.py``). ``cfile`` entries are
ARX-compressed (``arx.py``); there are three on the disc.

Usage::

    python toc.py --self-test
    python toc.py --iso DISC.iso            # parse every TOC on the image, report
"""
from __future__ import annotations

import argparse
import struct
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SECTOR = 2048
FILLER = b"MONOLITHSOFT Xenosaga Episode.2\x00"


@dataclass(frozen=True)
class TocEntry:
    source: str          # TOC filename the entry came from
    path: str            # in-game path, backslash-separated
    sector: int          # sector offset relative to the TOC file (chain start)
    size: int            # stored byte length (compressed length for cfiles)
    usize: Optional[int]  # uncompressed length for cfiles, else None
    compressed: bool
    fields_off: int = -1  # TOC byte offset of u24 sector, u32 size[, u24 usize]

    @property
    def byte_offset(self) -> int:
        return self.sector * SECTOR

    @property
    def ext(self) -> str:
        name = self.path.rsplit("\\", 1)[-1]
        return name.rsplit(".", 1)[-1].lower() if "." in name else ""


class TocError(ValueError):
    pass


# --------------------------------------------------------------- front coding

def decode_name(prev: bytes, code: int, literal: bytes) -> bytes:
    """Apply one front-coded edit to ``prev``."""
    if code == 0:
        name = literal
    else:
        h, n = code >> 4, code & 15
        if n != len(literal):
            raise TocError(f"code {code:#04x} says {n} literal bytes, have {len(literal)}")
        p = len(prev) - h - 1
        if p < 0:
            raise TocError(f"code {code:#04x} edits before the start of {prev!r}")
        name = prev[:p] + literal + prev[p + n:]
    cut = name.find(b"\x00")
    return name if cut < 0 else name[:cut]


def encode_name(prev: bytes, name: bytes) -> Tuple[int, bytes]:
    """The (code, literal) Monolith's tool writes for ``name`` after ``prev``."""
    if prev:
        p = 0
        lim = min(len(prev), len(name))
        while p < lim and prev[p] == name[p]:
            p += 1
        if len(name) == len(prev):
            s = 0
            while s < len(name) - p and prev[-1 - s] == name[-1 - s]:
                s += 1
            lit = name[p:len(name) - s]
        else:
            lit = name[p:] + b"\x00"
        h = len(prev) - p - 1
        if lit and 0 <= h <= 15 and len(lit) <= 15:
            return (h << 4) | len(lit), lit
    return 0, name + b"\x00"


# ---------------------------------------------------------------------- parse

@dataclass
class Event:
    """One TOC item, as needed to rebuild the file byte-identically."""
    kind: str                    # "file" | "dir" | "pop"
    name: bytes = b""
    pops: int = 0
    sector: int = 0
    size: int = 0
    usize: Optional[int] = None
    compressed: bool = False
    fields_off: int = -1


def _is_filler(data: bytes, pos: int) -> bool:
    rest = data[pos:]
    if not rest:
        return True
    phase = pos % len(FILLER)
    want = (FILLER * (len(rest) // len(FILLER) + 2))[phase:phase + len(rest)]
    return rest == want


def parse_events(data: bytes, source: str = "") -> Tuple[List[Event], int]:
    """The item stream and the offset where the filler starts."""
    if not data:
        raise TocError(f"{source}: empty")
    if data[0] != len(data) // SECTOR:
        raise TocError(f"{source}: header byte {data[0]} != file sector count {len(data) // SECTOR}")
    pos, events = 1, []
    file_prev = b""
    dir_prev: dict[int, bytes] = {}
    depth = 0
    while pos < len(data):
        if _is_filler(data, pos):
            return events, pos
        b = data[pos]
        if b == 0:
            events.append(Event("pop"))
            depth = max(0, depth - 1)
            pos += 1
            continue
        if b & 0x80:
            n = b & 0x7F
            pops, code = data[pos + 1], data[pos + 2]
            literal = data[pos + 3:pos + n]
            depth = max(0, depth - pops)
            name = decode_name(dir_prev.get(depth, b""), code, literal)
            if not name or not all(32 <= c < 127 for c in name):
                raise TocError(f"{source}: bad dir name {name!r} at {pos:#x}")
            dir_prev[depth] = name
            for k in [k for k in dir_prev if k > depth]:
                del dir_prev[k]
            events.append(Event("dir", name=name, pops=pops))
            depth += 1
            file_prev = b""
            pos += n
            continue
        comp = bool(b & 0x40)
        n = b & 0x3F
        code = data[pos + 1]
        literal = data[pos + 2:pos + n]
        name = decode_name(file_prev, code, literal)
        if not name or not all(32 <= c < 127 for c in name):
            raise TocError(f"{source}: bad file name {name!r} at {pos:#x}")
        off = pos + n
        sector = int.from_bytes(data[off:off + 3], "little")
        size = int.from_bytes(data[off + 3:off + 7], "little")
        usize = int.from_bytes(data[off + 7:off + 10], "little") if comp else None
        events.append(Event("file", name=name, sector=sector, size=size, usize=usize,
                            compressed=comp, fields_off=off))
        file_prev = name
        pos = off + (10 if comp else 7)
    return events, pos


def parse_toc(data: bytes, source: str = "") -> List[TocEntry]:
    """Parse a whole TOC file. Raises TocError on any grammar violation."""
    events, _ = parse_events(data, source)
    stack: List[str] = []
    out: List[TocEntry] = []
    for ev in events:
        if ev.kind == "pop":
            if stack:
                stack.pop()
        elif ev.kind == "dir":
            for _ in range(ev.pops):
                if stack:
                    stack.pop()
            stack.append(ev.name.decode("ascii"))
        else:
            out.append(TocEntry(source, "\\".join(stack + [ev.name.decode("ascii")]),
                                ev.sector, ev.size, ev.usize, ev.compressed, ev.fields_off))
    return out


def serialize(events: List[Event], size_sectors: int, filler: bytes = FILLER) -> bytes:
    """Rebuild a TOC from its item stream: the inverse of ``parse_events``.
    Names are front-coded with ``encode_name``; the file is padded with the
    phase-locked filler to ``size_sectors``."""
    out = bytearray([size_sectors & 0xFF])
    file_prev = b""
    dir_prev: dict[int, bytes] = {}
    depth = 0
    for ev in events:
        if ev.kind == "pop":
            out.append(0)
            depth = max(0, depth - 1)
        elif ev.kind == "dir":
            depth = max(0, depth - ev.pops)
            code, lit = encode_name(dir_prev.get(depth, b""), ev.name)
            n = 3 + len(lit)
            if n > 0x7F:
                raise TocError(f"dir name too long: {ev.name!r}")
            out += bytes([0x80 | n, ev.pops, code]) + lit
            dir_prev[depth] = ev.name
            for k in [k for k in dir_prev if k > depth]:
                del dir_prev[k]
            depth += 1
            file_prev = b""
        else:
            code, lit = encode_name(file_prev, ev.name)
            n = 2 + len(lit)
            if n > 0x3F:
                raise TocError(f"file name too long: {ev.name!r}")
            out += bytes([n | (0x40 if ev.compressed else 0), code]) + lit
            out += ev.sector.to_bytes(3, "little") + ev.size.to_bytes(4, "little")
            if ev.compressed:
                out += (ev.usize or 0).to_bytes(3, "little")
            file_prev = ev.name
    total = size_sectors * SECTOR
    if len(out) > total:
        raise TocError(f"TOC needs {len(out)} bytes, {total} available")
    start = len(out)
    rep = filler * (total // len(filler) + 2)
    phase = start % len(filler)
    out += rep[phase:phase + total - start]
    return bytes(out)


# ------------------------------------------------------------------ self-test

def _synthetic() -> Tuple[List[Event], List[str]]:
    ev = [
        Event("file", name=b"font0.us", sector=53, size=492352),
        Event("file", name=b"font1.us", sector=294, size=492352),
        Event("file", name=b"credit.bin", sector=576, size=53248),
        Event("file", name=b"title.bin", sector=602, size=1234, usize=917504, compressed=True),
        Event("dir", name=b"sound"),
        Event("dir", name=b"vsf"),
        Event("file", name=b"SYS_SE.prj", sector=700, size=976),
        Event("file", name=b"SYS_SE.vsf", sector=701, size=80528),
        Event("file", name=b"CFS_SE.prj", sector=741, size=1360),
        Event("file", name=b"CF_DYU.prj", sector=742, size=1360),
        Event("dir", name=b"smf", pops=1),
        Event("file", name=b"020300_marglis.xap", sector=800, size=51920),
        Event("file", name=b"020300_mobj200.xap", sector=826, size=6304),
        Event("file", name=b"020300_mobj201.xap", sector=830, size=5196),
        Event("file", name=b"020300_oruguia.xap", sector=833, size=87568),
        Event("dir", name=b"yajima", pops=2),
        Event("file", name=b"a_very_long_file_name_beyond_fifteen.bin", sector=900, size=1),
        Event("pop"),
    ]
    paths = ["font0.us", "font1.us", "credit.bin", "title.bin",
             "sound\\vsf\\SYS_SE.prj", "sound\\vsf\\SYS_SE.vsf", "sound\\vsf\\CFS_SE.prj",
             "sound\\vsf\\CF_DYU.prj", "sound\\smf\\020300_marglis.xap",
             "sound\\smf\\020300_mobj200.xap", "sound\\smf\\020300_mobj201.xap",
             "sound\\smf\\020300_oruguia.xap", "yajima\\a_very_long_file_name_beyond_fifteen.bin"]
    return ev, paths


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    # codes observed on the retail disc, re-derived by the encoder
    check(encode_name(b"font0.us", b"font1.us") == (0x31, b"1"), "font0.us -> font1.us is code 0x31 '1'")
    check(encode_name(b"SYS_SE.prj", b"SYS_SE.vsf") == (0x23, b"vsf"), "same-length suffix edit 0x23")
    check(encode_name(b"font1.us", b"credit.bin") == (0x7B, b"credit.bin\x00"), "longer name carries its NUL")
    check(encode_name(b"020300_marglis.xap", b"020300_mobj200.xap") == (0x96, b"obj200"),
          "middle edit keeps the suffix (0x96)")
    check(decode_name(b"020300_mobj200.xap", 0x41, b"1") == b"020300_mobj201.xap", "decode 0x41")
    check(decode_name(b"abc", 0, b"xyz\x00") == b"xyz", "code 0 is the whole name")
    ev, paths = _synthetic()
    blob = serialize(ev, 2)
    got = parse_toc(blob, "synthetic")
    check([e.path for e in got] == paths, "synthetic tree parses back to its paths")
    check(serialize(parse_events(blob)[0], 2) == blob, "serialize(parse(x)) == x")
    check(next(e for e in got if e.path == "title.bin").usize == 917504, "cfile usize survives")
    check(_is_filler(blob, len(blob) - 40), "filler is phase-locked")
    try:
        parse_toc(b"\x05" + bytes(100), "bad")
        check(False, "a wrong header byte is refused")
    except TocError:
        check(True, "a wrong header byte is refused")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def _report(iso_path: str) -> int:
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    from chains import CHAINS, ChainReader, available_chains
    from iso9660 import IsoImage

    iso = IsoImage(iso_path)
    rc = 0
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        data = bytes(reader.toc_bytes())
        events, end = parse_events(data, CHAINS[chain][0])
        ents = parse_toc(data, CHAINS[chain][0])
        same = serialize(events, data[0]) == data
        moved = [e for e in ents if e.byte_offset >= reader.total]      # repack grow=True
        tiles = sorted((e for e in ents if e.byte_offset < reader.total), key=lambda e: e.sector)
        gaps = overlaps = 0
        for a, b in zip(tiles, tiles[1:]):
            end_a = a.sector + (a.size + SECTOR - 1) // SECTOR
            gaps += b.sector > end_a
            overlaps += b.sector < end_a
        last = max(e.byte_offset + e.size for e in tiles)
        print(f"chain{chain} {CHAINS[chain][0]}: {len(ents)} entries, "
              f"{sum(e.compressed for e in ents)} ARX, filler at {end:#x}, "
              f"rebuild byte-identical: {same}, gaps {gaps}, overlaps {overlaps}, "
              f"data ends {reader.total - last} bytes before the chain end"
              + (f", {len(moved)} relocated past it ({', '.join(e.path for e in moved)}; "
                 f"their old slots are the gaps)" if moved else ""))
        rc |= (not same) or overlaps or (gaps and not moved)
    return 1 if rc else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--iso")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.iso:
        return _report(a.iso)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
