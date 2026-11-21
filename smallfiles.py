"""smallfiles.py — the small one-off files of Episode II: packs, the title
images, the memory-card icon, the battle database, and the map/actor tables.

Survey notes and evidence: docs/research/small-files.md. What this module
decodes (2026-11-05, every claim checked on the USA discs):

* **Packs** (``credit.bin``, ``carddata.bin``, ``yajima\\im.bin``,
  ``yajima\\synopsis.bin``, ``yajima\\titlebg2.bin``): ``u32 table_bytes``,
  then ``table_bytes / 4`` member END offsets, counted from the first member;
  members start at 0x40 past the table (the table is padded to a 64-byte
  boundary) and each next member at the next 64-byte boundary, zero-filled.
  ``build_pack`` rebuilds all five byte-identically. Members: credit.bin 6
  JPEGs (the staff-credit images), im.bin 4 JPEGs, synopsis.bin 2 XTX,
  carddata.bin the save's ``icon.sys`` + ``.ico``, titlebg2.bin 190 members
  of the title scene (pxy/txy/XHR models, XAC/FAC motion, XCM cameras).
* **Title logo** (``yajima\\title.bin`` "DISC 1", ``title2.bin`` "DISC 2",
  both ARX-compressed): a raw 512 x 448 RGBA image, alpha 0-0x80 (doubled).
* **Memory-card icon** (inside carddata.bin): ``icon.sys`` (title "Xenosaga
  EPII-00[00:00]", the slot and time filled in when saving) and a PS2 3D
  icon: 36 vertices, one shape, texture type 0x0E (RLE-compressed 128 x 128
  RGB555), decoded exactly (32,768 bytes from the RLE stream).
* **Battle database** (``yamamoto\\batdat.us``): ``u32 count (19), u32 file
  size, count x u32 section offset``. A section with text is records whose
  u16 fields point (from the section start) into a string pool that begins
  at the first record's first string. 1,085 strings in 13 sections: party
  names (0), enemy names (1), Ether skills (6), skills (7), equipment (8),
  items (9), secret keys (10), decoders (11), special attacks (12), E.S.
  attacks and weapons (13, 14), grade letters (17) and battle messages (18,
  "Acquired ", "Stole ", status names). Text a translation has to cover.
  Which u16 fields are string offsets was read from the USA OV01's getters
  (``BATDAT_STRING_FIELDS``): ``batdat_rebuild`` lets a string grow by moving
  the rest of its section down and rewriting exactly those fields.
* ``.id`` (map data): u8 version 3, u8 section count, then u32 section
  offsets; each section starts with u8 0, u8 type. ``.epf``: "EPF " + float
  version + count + records of 0x20 bytes (two u32 then six floats).
  ``.ene``: 48-byte parameter records. ``.dev``: "DEV " + float 0.5 + an
  offset table. ``.fap``: "FAP " + float 1.2 + count + offsets to "FAC "
  clips. Structure only; their meaning is noted in the survey, not decoded.

    python smallfiles.py --self-test
    python smallfiles.py FILE [FILE ...]        # say what each file is
    python smallfiles.py FILE --out DIR          # and unpack/convert it
"""
from __future__ import annotations

import os
import re
import struct
import sys
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ALIGN = 0x40


class SmallFileError(ValueError):
    pass


def _align(n: int, a: int = ALIGN) -> int:
    return (n + a - 1) & ~(a - 1)


def png_bytes(w: int, h: int, rgba: bytes) -> bytes:
    def chunk(t, b):
        return struct.pack(">I", len(b)) + t + b + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------- packs

def pack_members(data: bytes) -> List[Tuple[int, int]]:
    """(start, end) of every member of an offset-table pack; raises if the
    data is not one."""
    if len(data) < 8:
        raise SmallFileError("too short for a pack")
    tb = struct.unpack_from("<I", data, 0)[0]
    if tb == 0 or tb % 4 or 4 + tb > len(data) or tb > 0x10000:
        raise SmallFileError("no offset table")
    ends = struct.unpack_from(f"<{tb // 4}I", data, 4)
    start = _align(4 + tb)
    out, p = [], start
    for e in ends:
        end = start + e
        if end < p or end > len(data):
            raise SmallFileError("member offsets out of order")
        out.append((p, end))
        p = _align(end)
    if p != len(data):
        raise SmallFileError(f"pack ends at {p:#x}, file at {len(data):#x}")
    return out


def unpack_pack(data: bytes) -> List[bytes]:
    return [data[a:b] for a, b in pack_members(data)]


def build_pack(members: List[bytes]) -> bytes:
    tb = 4 * len(members)
    start = _align(4 + tb)
    body, ends, p = bytearray(), [], 0
    for m in members:
        body += m
        ends.append(p + len(m))
        p = _align(p + len(m))
        body += bytes(p - len(body))
    head = struct.pack(f"<I{len(ends)}I", tb, *ends)
    return head + bytes(start - len(head)) + bytes(body)


MEMBER_EXT = ((b"\xff\xd8\xff", ".jpg"), (b"XTX\x00", ".xtx"), (b"pxy\x00", ".pxy"), (b"txy\x00", ".txy"),
              (b"XHR ", ".xhr"), (b"XAC ", ".xac"), (b"FAC ", ".fac"), (b"XCM ", ".xcm"),
              (b"PS2D", ".sys"), (b"\x00\x00\x01\x00", ".ico"))


def member_ext(m: bytes) -> str:
    return next((ext for magic, ext in MEMBER_EXT if m.startswith(magic)), ".bin")


# ------------------------------------------------------------- title images

TITLE_W, TITLE_H = 512, 448


def rgba_image(data: bytes, w: int = TITLE_W, h: int = TITLE_H) -> bytes:
    """A raw GS RGBA image (alpha 0..0x80) as straight 8-bit RGBA."""
    if data[:4] == b"ARX\x00":
        import arx
        data = arx.decompress(data)
    if len(data) != w * h * 4:
        raise SmallFileError(f"{len(data)} bytes is not a {w}x{h} RGBA image")
    px = bytearray(data)
    px[3::4] = bytes(min(255, a * 2) for a in px[3::4])
    return bytes(px)


# ------------------------------------------------------- memory-card icon

def icon_sys_title(sys_bytes: bytes) -> str:
    """The save title of an ``icon.sys`` (Shift-JIS, two lines)."""
    if sys_bytes[:4] != b"PS2D":
        raise SmallFileError("not an icon.sys")
    return sys_bytes[0xC0:0xC0 + 68].split(b"\x00")[0].decode("shift_jis", "replace").rstrip()


def ico_texture(ico: bytes) -> Tuple[int, int, bytes]:
    """The 128 x 128 texture of a PS2 3D icon, as RGBA."""
    magic, shapes, ttype, _unk, nverts = struct.unpack_from("<IIIfI", ico, 0)
    if magic != 0x10000:
        raise SmallFileError("not a PS2 icon")
    if not ttype & 4:
        raise SmallFileError("icon without a texture")
    p = 20 + nverts * (shapes * 8 + 16)           # shapes x vertex, normal, uv, colour
    _id, _flen, _speed, _off, nframes = struct.unpack_from("<IIfII", ico, p)
    p += 20
    for _ in range(nframes):
        _shape, nkeys = struct.unpack_from("<II", ico, p)
        p += 8 + nkeys * 8
    if ttype & 8:                                   # RLE-compressed
        size = struct.unpack_from("<I", ico, p)[0]
        src, i, tex = ico[p + 4:p + 4 + size], 0, bytearray()
        while i + 2 <= len(src) and len(tex) < 32768:
            c = struct.unpack_from("<H", src, i)[0]
            i += 2
            if c & 0x8000:
                n = 0x10000 - c
                tex += src[i:i + 2 * n]
                i += 2 * n
            else:
                tex += src[i:i + 2] * c
                i += 2
    else:
        tex = bytearray(ico[p:p + 32768])
    if len(tex) < 32768:
        raise SmallFileError(f"texture short: {len(tex)} of 32768 bytes")
    rgba = bytearray()
    for k in range(0, 32768, 2):
        v = tex[k] | tex[k + 1] << 8
        r, g, b = (v & 31) << 3, ((v >> 5) & 31) << 3, ((v >> 10) & 31) << 3
        rgba += bytes((r | r >> 5, g | g >> 5, b | b >> 5, 255))
    return 128, 128, bytes(rgba)


# --------------------------------------------------------- battle database

def batdat_sections(data: bytes) -> List[Tuple[int, int]]:
    n, total = struct.unpack_from("<II", data, 0)
    if total != len(data) or not 0 < n < 256:
        raise SmallFileError("not a battle database")
    offs = list(struct.unpack_from(f"<{n}I", data, 8))
    return list(zip(offs, offs[1:] + [total]))


def _cstr(seg: bytes, o: int) -> bytes:
    e = seg.find(b"\x00", o)
    return seg[o:e if e >= 0 else len(seg)]


def _is_euc(raw: bytes) -> bool:
    import sbl
    if not any(b > 0x7F for b in raw):
        return False
    try:
        raw.decode(sbl.CODEC)
        return True
    except UnicodeDecodeError:
        return False


def batdat_raw_strings(data: bytes) -> List[Tuple[int, int, bytes]]:
    """(section, offset in section, bytes) for every string the sections point
    to: records whose first two u16 point into the section's string pool, and
    the plain offset list of the last section."""
    out = []
    for si, (a, b) in enumerate(batdat_sections(data)):
        seg = data[a:b]
        if len(seg) < 4:
            continue
        pool = struct.unpack_from("<H", seg, 0)[0]       # the first record's first string
        if not (4 <= pool < len(seg)) or not (0x20 <= seg[pool] < 0x7F or 0xA1 <= seg[pool] <= 0xFE):
            continue                                     # a numbers-only section
        words = struct.unpack_from(f"<{pool // 2}H", seg, 0)
        refs = {w for w in words if pool <= w < len(seg) and seg[w] != 0
                and (w == pool or seg[w - 1] == 0)}
        out += [(si, o, _cstr(seg, o)) for o in sorted(refs)]
    return out


def batdat_strings(data: bytes) -> List[Tuple[int, int, str]]:
    """``batdat_raw_strings`` decoded: ASCII on the USA disc, EUC-JP on the Japanese one."""
    import sbl
    return [(si, o, raw.decode(sbl.CODEC) if _is_euc(raw) else raw.decode("latin1"))
            for si, o, raw in batdat_raw_strings(data)]


def batdat_replace(data: bytes, new: Dict[Tuple[int, int], bytes]) -> bytes:
    """The battle database with some strings replaced IN PLACE. ``new`` maps
    (section, offset in section) -> bytes; a string may keep its length or get
    shorter (the rest is NUL-filled), never longer: no record, offset or size in
    the file changes, so nothing has to be known about the number fields.

    Why not a writer that moves strings: a string is found through u16 fields of
    its section's records, and which fields are offsets is known only by their
    values (sections mix record shapes: 109 records for 197 strings in section 6;
    section 17's "pool" is not text). Moving a string means rewriting every field
    that points at it and no field that merely equals it, and that cannot be told
    apart from the file alone. The file also has little room: 220 bytes on the
    USA disc before its last sector ends, and the game loads it into a static
    buffer (OV01: ``FUN_00a83dc0(path, 0x61d040)``) whose size is not known."""
    known = {(si, o): raw for si, o, raw in batdat_raw_strings(data)}
    secs = batdat_sections(data)
    out = bytearray(data)
    for (si, o), text in new.items():
        old = known.get((si, o))
        if old is None:
            raise SmallFileError(f"section {si} offset {o:#x}: not a string the database points to")
        if b"\x00" in text:
            raise SmallFileError(f"section {si} offset {o:#x}: a string may not contain NUL")
        if len(text) > len(old):
            raise SmallFileError(f"section {si} offset {o:#x}: {len(text)} bytes, the original has "
                                 f"{len(old)}; strings are replaced in place and may not get longer")
        if not text and old:
            raise SmallFileError(f"section {si} offset {o:#x}: a string may not be emptied "
                                 f"(the kit finds a string by its first byte and would lose it)")
        a = secs[si][0] + o
        out[a:a + len(old)] = text + bytes(len(old) - len(text))
    return bytes(out)


# Which u16 fields of the battle database are string offsets, read from the USA OV01 (2026-11-20). Every
# section is reached through the header: OV01's accessors return ``*(int *)(0x61D048 + 4 * section) +
# 0x61D040`` plus a record index times a record size (FUN_00a9b338 / FUN_00a9b2a8 sections 0 and 1,
# FUN_00a9b490 section 6, ..., FUN_00a9b720 section 18), and the getters below add a signed u16 of the
# record to the section's start and return the string. No other code adds 0x61D040 to anything, so the
# header's section offsets are the only file positions the game reads; offsets inside a section are
# relative to the section. section -> (record size, string-offset fields, the getter that reads them).
BATDAT_STRING_FIELDS: Dict[int, Tuple[int, Tuple[int, ...], str]] = {
    0: (0x5C, (0,), "FUN_00a9b338"),          # party names
    1: (0x5C, (0,), "FUN_00a9b338"),          # enemy names
    6: (0x0C, (0, 2), "FUN_00a9b750"),        # name, description
    7: (0x0C, (0, 2), "FUN_00a9b850"),
    8: (0x10, (0, 2), "FUN_00a9b7d8"),
    9: (0x10, (0, 2), "FUN_00a9b8d8"),
    10: (0x04, (0, 2), "FUN_00a9b950"),
    11: (0x08, (0, 2), "FUN_00a9b9c8"),
    12: (0x10, (0, 2), "FUN_00a9ba40"),
    13: (0x10, (0, 2), "FUN_00a9bab8"),
    14: (0x10, (0, 2), "FUN_00a9bb30"),
    18: (0x02, (0,), "FUN_00a9bba8"),         # battle messages: a plain list of offsets
}
BATDAT_SECTION_ALIGN = 4                       # every section offset of both retail files is a multiple of 4


def batdat_fields(data: bytes) -> Dict[Tuple[int, int, int], int]:
    """{(section, record, field offset): string offset} for every field of ``BATDAT_STRING_FIELDS``. A
    section's records run up to its first string (the first record's first field), which on both retail
    files is a whole number of records; a section where it is not (no text there) has no fields, and its
    strings cannot grow."""
    out = {}
    secs = batdat_sections(data)
    for si, (rs, cols, _) in BATDAT_STRING_FIELDS.items():
        if si >= len(secs):
            continue
        a, b = secs[si]
        if b - a < 2:
            continue
        pool = struct.unpack_from("<h", data, a)[0]
        if pool <= 0 or pool % rs or pool >= b - a:
            continue
        for r in range(pool // rs):
            for c in cols:
                out[(si, r, c)] = struct.unpack_from("<h", data, a + r * rs + c)[0]
    return out


def batdat_field_texts(data: bytes) -> Dict[Tuple[int, int, int], bytes]:
    """What each string field reads, the way the getters read it: the section start plus the field,
    up to the NUL."""
    secs = batdat_sections(data)
    return {k: _cstr(data[secs[k[0]][0]:secs[k[0]][1]], v) for k, v in batdat_fields(data).items()}


def batdat_rebuild(data: bytes, new: Dict[Tuple[int, int], bytes], grow: bool = False,
                   max_size: Optional[int] = None) -> bytes:
    """The battle database with some strings replaced. A string that fits its old bytes is replaced in
    place (``batdat_replace``). With ``grow``, a longer string of a section whose string fields are known
    (``BATDAT_STRING_FIELDS``) is written where it is and the rest of its section's strings move down to
    make room: every known field of that section that points past the string's start is moved by the
    same amount, the section is padded to a multiple of 4, the later sections move down whole, and the
    header's section offsets and file size follow. No other byte changes. ``max_size`` is the most the
    file may then hold (the caller's bound: the sectors the game reads).

    Refused, with the reason: a longer string in a section whose fields are not known (17), a string no
    known field points at, a known field pointing inside the old string, an offset past 0x7FFF (the
    getters read a signed u16), and a file over ``max_size``. The result is read back the way the
    getters read it: every field must give its old text, or the new text where it pointed at an edit."""
    known = {(si, o): raw for si, o, raw in batdat_raw_strings(data)}
    fit = {k: v for k, v in new.items() if k not in known or len(v) <= len(known[k])}
    longer = {k: v for k, v in new.items() if k not in fit}
    out = batdat_replace(data, fit)
    if not longer:
        return out
    if not grow:
        batdat_replace(data, longer)                  # raises the in-place message
    secs = batdat_sections(out)
    segs = [bytearray(out[a:b]) for a, b in secs]
    fields = batdat_fields(out)
    for (si, o), text in sorted(longer.items(), key=lambda kv: (kv[0][0], -kv[0][1])):   # last string first
        old = known[(si, o)]
        if b"\x00" in text:
            raise SmallFileError(f"section {si} offset {o:#x}: a string may not contain NUL")
        if si not in BATDAT_STRING_FIELDS:
            raise SmallFileError(f"section {si} offset {o:#x}: {len(text)} bytes, the original has {len(old)}; "
                                 f"which fields of section {si} point at its strings has not been read from the "
                                 f"game, so its strings are replaced in place and may not get longer")
        mine = [k for k, v in fields.items() if k[0] == si and v == o]
        inside = [k for k, v in fields.items() if k[0] == si and o < v < o + len(old)]
        if inside:
            raise SmallFileError(f"section {si} offset {o:#x}: record {inside[0][1]} points inside this string; "
                                 f"it may not get longer")
        if not mine:
            raise SmallFileError(f"section {si} offset {o:#x}: no string field the game reads points at it, so "
                                 f"it is replaced in place and may not get longer")
        delta = len(text) - len(old)
        seg = segs[si]
        seg[o:o + len(old)] = text
        rs = BATDAT_STRING_FIELDS[si][0]
        for k, v in list(fields.items()):
            if k[0] == si and v > o:
                if v + delta > 0x7FFF:
                    raise SmallFileError(f"section {si} offset {o:#x}: the strings after it would move past "
                                         f"0x7FFF, where the game's signed 16-bit offsets end")
                struct.pack_into("<h", seg, k[1] * rs + k[2], v + delta)
                fields[k] = v + delta
    for si in {si for si, _ in longer}:
        segs[si] += bytes(-len(segs[si]) % BATDAT_SECTION_ALIGN)
    head = bytearray(out[:secs[0][0]])
    pos = len(head)
    for i, seg in enumerate(segs):
        struct.pack_into("<I", head, 8 + 4 * i, pos)
        pos += len(seg)
    struct.pack_into("<I", head, 4, pos)
    blob = bytes(head) + b"".join(bytes(sg) for sg in segs)
    if max_size is not None and len(blob) > max_size:
        raise SmallFileError(f"the longer strings make the battle database {len(blob)} bytes; it may hold "
                             f"{max_size} ({len(blob) - max_size} over), the sectors the game reads. Shorten "
                             f"other strings or the edit")
    # read back what the game would read, field by field, against what was asked for
    before = batdat_field_texts(data)
    after = batdat_field_texts(blob)
    for k, v in batdat_fields(data).items():
        expect = new.get((k[0], v), before[k])
        if after.get(k) != expect:
            raise SmallFileError(f"section {k[0]} record {k[1]} field +{k[2]}: would read {after.get(k)!r}, "
                                 f"expected {expect!r}; nothing written")
    return blob


# -------------------------------------------------------- describe anything

def describe(path: str, data: bytes) -> str:
    name = path.replace("/", "\\").lower()
    if data[:4] == b"ARX\x00":
        import arx
        data = arx.decompress(data)
    try:
        if name.endswith(("title.bin", "title2.bin")):
            return f"title logo, raw {TITLE_W}x{TITLE_H} RGBA"
        if name.endswith(("batdat.us", "batdat.bin")):
            secs = batdat_sections(data)
            return f"battle database: {len(secs)} sections, {len(batdat_strings(data))} strings"
        m = pack_members(data)
        kinds: Dict[str, int] = {}
        for a, b in m:
            ext = member_ext(data[a:b])
            kinds[ext] = kinds.get(ext, 0) + 1
        return f"pack of {len(m)}: " + ", ".join(f"{v} {k[1:]}" for k, v in sorted(kinds.items()))
    except SmallFileError:
        pass
    magic = data[:4]
    if magic == b"EPF ":
        n = struct.unpack_from("<I", data, 8)[0]
        return f"EPF v{struct.unpack_from('<f', data, 4)[0]:.1f}: {n} records of 0x20 bytes"
    if magic == b"DEV ":
        return f"DEV v{struct.unpack_from('<f', data, 4)[0]:.1f}, {len(data)} bytes"
    if magic == b"FAP ":
        n = struct.unpack_from("<I", data, 8)[0]
        return f"FAP v{struct.unpack_from('<f', data, 4)[0]:.1f}: {n} FAC clips"
    if name.endswith(".id") and data[0] == 3:
        n = data[1]
        offs = struct.unpack_from(f"<{n}I", data, 4)
        types = [data[o + 1] for o in offs if o + 1 < len(data)]
        return f"map id: {n} sections, types {types}"
    if name.endswith(".ene"):
        return f"parameter table: {len(data) // 48} records of 48 bytes"
    return "unknown"


def extract(path: str, data: bytes, out: Path) -> List[str]:
    """Unpack/convert one file into ``out``; returns the files written."""
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(path.replace("\\", "/")).name
    name = path.replace("/", "\\").lower()
    written = []
    if data[:4] == b"ARX\x00":
        import arx
        data = arx.decompress(data)
    if name.endswith(("title.bin", "title2.bin")):
        p = out / f"{stem}.png"
        p.write_bytes(png_bytes(TITLE_W, TITLE_H, rgba_image(data)))
        return [str(p)]
    if name.endswith(("batdat.us", "batdat.bin")):
        import csv
        p = out / f"{stem}.strings.csv"
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["section", "offset", "text"])
            w.writerows(batdat_strings(data))
        return [str(p)]
    try:
        members = unpack_pack(data)
    except SmallFileError:
        return written
    d = out / stem
    d.mkdir(parents=True, exist_ok=True)
    for i, m in enumerate(members):
        ext = member_ext(m)
        p = d / f"{i:03d}{ext}"
        p.write_bytes(m)
        written.append(str(p))
        if ext == ".sys":
            (d / f"{i:03d}.title.txt").write_text(icon_sys_title(m) + "\n", encoding="utf-8")
        elif ext == ".ico":
            try:
                w, h, rgba = ico_texture(m)
                (d / f"{i:03d}.texture.png").write_bytes(png_bytes(w, h, rgba))
            except SmallFileError:
                pass
        elif ext == ".xtx":
            try:
                import xtx
                r = xtx.decode(m)
                if r:
                    (d / f"{i:03d}.png").write_bytes(png_bytes(r[0], r[1], r[2]))
            except Exception:
                pass
    return written


SMALL_FILES = ("credit.bin", "carddata.bin", "yajima\\im.bin", "yajima\\synopsis.bin",
               "yajima\\titlebg2.bin", "yajima\\title.bin", "yajima\\title2.bin", "yamamoto\\batdat.us",
               "yamamoto\\batdat.bin", "carddata.new")


def browse_small(out: Path, rows, limit) -> None:
    """``browse`` kind "small": the packs unpacked, title logos and the save
    icon as PNG, the battle database's strings as CSV, and a table saying what
    every .id/.epf/.ene/.dev/.fap is. Same signature as browse.py's kinds."""
    import csv
    base = out / "browse" / "small"
    summary = []
    done = 0
    for r in rows:
        p = r["path"].lower()
        if p in SMALL_FILES or p.endswith((".id", ".epf", ".ene", ".dev", ".fap")):
            src = out / "dump" / r["area"] / Path(*r["path"].split("\\"))
            data = src.read_bytes()
            summary.append({"area": r["area"], "path": r["path"], "what": describe(r["path"], data)})
            if p in SMALL_FILES:
                extract(r["path"], data, base / r["area"] / Path(*r["path"].split("\\")).parent)
                done += 1
                if limit and done >= limit:
                    break
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "small.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["area", "path", "what"])
        w.writeheader()
        w.writerows(summary)
    print(f"small: {done} files unpacked, {len(summary)} described", file=sys.stderr)


# ---------------------------------------------------------------- self-test

def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    members = [b"\xff\xd8\xff\xe0" + bytes(70), b"XTX\x00" + bytes(10), b"PS2D" + bytes(300)]
    blob = build_pack(members)
    check(unpack_pack(blob) == members, "pack round trip")
    check(len(blob) % ALIGN == 0 and struct.unpack_from("<I", blob, 0)[0] == 12, "pack layout")
    try:
        pack_members(blob[:-ALIGN])
        check(False, "a truncated pack is refused")
    except SmallFileError:
        check(True, "a truncated pack is refused")
    img = bytes([10, 20, 30, 0x80]) * (TITLE_W * TITLE_H)
    rgba = rgba_image(img)
    check(rgba[:4] == bytes([10, 20, 30, 255]), "title image alpha doubled")
    # a 1-vertex icon with an RLE texture: 16384 copies of one colour
    vert = bytes(8) + bytes(8) + bytes(4) + bytes(4)
    ico = struct.pack("<IIIfI", 0x10000, 1, 0x0E, 0.0, 1) + vert
    ico += struct.pack("<IIfII", 1, 1, 1.0, 0, 1) + struct.pack("<II", 0, 1) + struct.pack("<ff", 0, 1)
    rle = struct.pack("<HH", 0x4000, 0x7C1F)          # 16384 x (r 31, g 0, b 31)
    ico += struct.pack("<I", len(rle)) + rle
    w, h, tex = ico_texture(ico)
    check((w, h) == (128, 128) and tex[:4] == bytes((255, 0, 255, 255)), "icon RLE texture")
    sec = struct.pack("<HHHH", 9, 17, 0, 0) + b"\x00Med Kit\x00Heal.\x00\x00\x00"
    db = struct.pack("<II", 1, 12 + len(sec)) + struct.pack("<I", 12) + sec
    check(batdat_strings(db) == [(0, 9, "Med Kit"), (0, 17, "Heal.")], "battle database strings")
    # the battle database: a text section (two 8-byte records, a pool) and a numbers-only one
    pool = b"Medica\x00One ally\x00\x00Revert\x00All\x00\x00\x00"
    recs = struct.pack("<4H", 16, 23, 7, 0) + struct.pack("<4H", 33, 40, 16, 23)
    sec0, sec1 = recs + pool, struct.pack("<4H", 2, 0, 500, 9)
    head = struct.pack("<II", 2, 16 + len(sec0) + len(sec1)) + struct.pack("<II", 16, 16 + len(sec0))
    bd = head + sec0 + sec1
    raw = batdat_raw_strings(bd)
    check(raw == [(0, 16, b"Medica"), (0, 23, b"One ally"), (0, 33, b"Revert"), (0, 40, b"All")],
          "battle database: the strings the records point to")
    check(batdat_replace(bd, {}) == bd and batdat_replace(bd, {(0, 16): b"Medica"}) == bd,
          "batdat_replace with unchanged text is the identity")
    nb = batdat_replace(bd, {(0, 23): b"One"})
    check(len(nb) == len(bd) and batdat_raw_strings(nb)[1] == (0, 23, b"One")
          and [x for i, x in enumerate(batdat_raw_strings(nb)) if i != 1] == [raw[0], raw[2], raw[3]]
          and sum(a != b for a, b in zip(nb, bd)) == 5, "a shorter string is NUL-filled in place; nothing else moves")
    for bad, why in (({(0, 16): b"Medica!"}, "longer"), ({(0, 17): b"x"}, "not a string"), ({(0, 16): b""}, "emptied")):
        try:
            batdat_replace(bd, bad)
            check(False, f"batdat_replace refuses: {why}")
        except SmallFileError:
            check(True, f"batdat_replace refuses: {why}")
    # strings that grow: a database with the retail section numbering (19 sections), section 6 with
    # 12-byte records (name +0, description +2, a number +4 that equals a string offset), section 18 a
    # list of offsets, section 17 text whose fields are not known, the rest numbers
    def sec6():
        pool = b"Fire\x00Burns.\x00Ice\x00\x00Freezes.\x00\x00"
        base = 3 * 12
        rec = (struct.pack("<3h3H", base, base + 5, base + 12, 0, 0, 0)
               + struct.pack("<3h3H", base + 12, base + 17, base + 5, 0, 0, 0)
               + struct.pack("<3h3H", base + 12, base + 15, 9, 0, 0, 0))      # empty description: a NUL
        return rec + pool + bytes(-len(rec + pool) % 4)
    sec18 = struct.pack("<2h", 4, 10) + b"Stole\x00Got\x00\x00\x00"
    sec17 = struct.pack("<2h", 4, 6) + b"A\x00B\x00\x00\x00"
    sections = [struct.pack("<4H", 1, 2, 3, 4)] * 19
    sections[6], sections[17], sections[18] = sec6(), sec17, sec18
    hlen = 8 + 4 * 19
    offs, pos = [], hlen
    for sg in sections:
        offs.append(pos)
        pos += len(sg)
    g = struct.pack("<II", 19, pos) + struct.pack("<19I", *offs) + b"".join(sections)
    check(batdat_rebuild(g, {}, grow=True) == g and batdat_rebuild(g, {(6, 41): b"Burns."}, grow=True) == g,
          "batdat_rebuild of unedited text is the file itself")
    gt = batdat_field_texts(g)
    g2 = batdat_rebuild(g, {(6, 41): b"Burns badly."}, grow=True)
    s6 = batdat_sections(g2)[6]
    seg = g2[s6[0]:s6[1]]
    rec0 = struct.unpack_from("<3h", seg, 0)
    ft = batdat_field_texts(g2)
    check(rec0[1] == 41 and seg[41:].startswith(b"Burns badly.\x00Ice\x00\x00Freezes.\x00")
          and struct.unpack_from("<h", seg, 12)[0] == 54 and (s6[1] - s6[0]) % 4 == 0,
          "a longer string stays where it is; the section's later strings move down")
    check(rec0[2] == 48, "a number field (+4) that equals a string's offset is left alone")
    check(ft[(6, 0, 2)] == b"Burns badly." and all(ft[k] == gt[k] for k in gt if k != (6, 0, 2)),
          "every field the getters read gives the old text but the one edited (read back from the result)")
    check(struct.unpack_from("<h", seg, 12 + 4)[0] == 41 and struct.unpack_from("<h", seg, 24 + 4)[0] == 9,
          "a number that equals the moved string's old offset is left alone")
    check(sorted(r for _, _, r in batdat_raw_strings(g2)) == sorted(
          [b"Fire", b"Burns badly.", b"Ice", b"Freezes.", b"Stole", b"Got", b"A", b"B"]),
          "the next export finds the same strings, the edited one at its new place")
    o_old, o_new = batdat_sections(g), batdat_sections(g2)
    grown = len(g2) - len(g)
    check(grown % 4 == 0 and struct.unpack_from("<I", g2, 4)[0] == len(g2)
          and all(o_new[i][0] == o_old[i][0] for i in range(7))
          and all(o_new[i][0] == o_old[i][0] + grown for i in range(7, 19))
          and all(g2[o_new[i][0]:o_new[i][1]] == g[o_old[i][0]:o_old[i][1]] for i in range(19) if i != 6),
          "the later sections move down whole; the header's offsets and size follow; no other section changes")
    g3 = batdat_rebuild(g, {(18, 10): b"Received", (6, 41): b"Burn"}, grow=True)
    check(batdat_field_texts(g3)[(18, 1, 0)] == b"Received" and batdat_field_texts(g3)[(6, 0, 2)] == b"Burn"
          and len(g3) - len(g) == 8, "a battle message grows in the offset list; a shorter edit stays in place")
    g4 = batdat_rebuild(g, {(6, 48): b"Icicle"}, grow=True)
    check(batdat_field_texts(g4)[(6, 2, 2)] == b"" and batdat_field_texts(g4)[(6, 1, 2)] == b"Freezes.",
          "an empty description at a string's NUL stays empty when that string grows")
    gin = bytearray(g)
    struct.pack_into("<h", gin, batdat_sections(g)[6][0] + 24 + 2, 49)
    for bad, why, kw, src in (({(6, 48): b"Icicle"}, "a field points inside the string", {"grow": True}, bytes(gin)),
                              ({(6, 41): b"Burns badly."}, "in place without grow", {}, g),
                              ({(17, 6): b"BB"}, "a section whose fields are not known (17)", {"grow": True}, g),
                              ({(6, 41): b"Burns badly."}, "over the size bound", {"grow": True,
                                                                                 "max_size": len(g) + 4}, g)):
        try:
            batdat_rebuild(src, bad, **kw)
            check(False, f"batdat_rebuild refuses: {why}")
        except SmallFileError:
            check(True, f"batdat_rebuild refuses: {why}")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if not a:
        print(__doc__)
        return 2
    out = None
    if "--out" in a:
        i = a.index("--out")
        out = Path(a[i + 1])
        a = a[:i] + a[i + 2:]
    for f in a:
        data = open(f, "rb").read()
        print(f"{f}: {describe(f, data)}")
        if out:
            for w in extract(f, data, out):
                print(f"  wrote {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
