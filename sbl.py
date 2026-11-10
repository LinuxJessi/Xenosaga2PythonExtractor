"""sbl.py — Episode II's script text banks (``.sbl``): parse, rebuild, edit.

Every line a character speaks in the field, and every map message, lives in
an ``.sbl`` next to the compiled script that shows it: ``cf\\en\\scNNNN.sbl``
for scene scripts (``cf\\scNNNN.sb``), ``model\\map\\en\\*.sbl`` for map
scripts (``model\\map\\*.bin``). The script refers to strings by index.

Layout (all 465 files on the disc, checked 2026-11-05)::

    +0x00 "SBL "   +0x04 u32 1   +0x08 u32 count   +0x0C u32 1 (0 in one file)
    +0x10 u32 0    +0x14 u32 0x40 (offset table)   +0x18 u32 0x40 + 4*count (strings)
    +0x1C .. +0x3F zero
    +0x40 u32 offsets[count], relative to the string area, in order
    strings, NUL-terminated, back to back; the file is zero-padded to 4 bytes

Text is ASCII with in-line codes the game interprets: ``[Speaker]`` at the
start of a line, ``\\n`` (backslash, n) for a line break, ``$zoom13;``,
``$colc0c0c0;``/``$col808080;`` for size and colour, and EUC-JP symbols
for controller buttons (``<\\xa2\\xa2>``). A few files keep Shift-JIS
developer notes. The editable form (``to_text``) shows EUC-JP as text and any
byte run that does not survive a decode/encode round trip as ``⟦XX⟧`` hex,
so ``from_text(to_text(s)) == s`` for every string on the disc.

    python sbl.py --self-test
    python sbl.py FILE.sbl            # print the strings in editable form
"""
from __future__ import annotations

import re
import struct
import sys
from typing import List

MAGIC = b"SBL "
HEADER = 0x40


class SblError(ValueError):
    pass


def parse(data: bytes) -> List[bytes]:
    if data[:4] != MAGIC:
        raise SblError("not an SBL (magic)")
    count, table, strings = (struct.unpack_from("<I", data, 8)[0],
                             struct.unpack_from("<I", data, 0x14)[0],
                             struct.unpack_from("<I", data, 0x18)[0])
    if table != HEADER or strings != HEADER + 4 * count:
        raise SblError(f"unexpected layout: table {table:#x} strings {strings:#x} count {count}")
    out = []
    for o in struct.unpack_from(f"<{count}I", data, table):
        p = strings + o
        end = data.index(b"\x00", p)
        out.append(data[p:end])
    return out


def build(strings: List[bytes], like: bytes = b"") -> bytes:
    """A new .sbl holding ``strings``; header words other than the counts and
    offsets are copied from ``like`` (the original file) when given."""
    head = bytearray(like[:HEADER]) if like[:4] == MAGIC else bytearray(HEADER)
    if not like:
        head[0:4] = MAGIC
        struct.pack_into("<II", head, 4, 1, 0)
        struct.pack_into("<I", head, 0x0C, 1)
    n = len(strings)
    struct.pack_into("<I", head, 8, n)
    struct.pack_into("<III", head, 0x10, 0, HEADER, HEADER + 4 * n)
    offs, body = [], bytearray()
    for s in strings:
        if b"\x00" in s:
            raise SblError("a string may not contain NUL")
        offs.append(len(body))
        body += s + b"\x00"
    out = bytes(head) + struct.pack(f"<{n}I", *offs) + bytes(body)
    return out + bytes(-len(out) % 4)


# ----------------------------------------------------------- editable text

_ESC = re.compile(r"⟦([0-9A-Fa-f]{2})⟧")


def to_text(s: bytes) -> str:
    """Readable, lossless: EUC-JP where it round-trips, ⟦XX⟧ for other bytes."""
    out, i = [], 0
    while i < len(s):
        b = s[i]
        if b < 0x80:
            if b in (0x0A, 0x0D) or b < 0x20:
                out.append(f"⟦{b:02X}⟧")
            else:
                out.append(chr(b))
            i += 1
            continue
        piece = s[i:i + 2] if b != 0x8F else s[i:i + 3]
        try:
            ch = piece.decode("euc_jp")
            if ch.encode("euc_jp") == piece and len(ch) == 1:
                out.append(ch)
                i += len(piece)
                continue
        except UnicodeDecodeError:
            pass
        out.append(f"⟦{b:02X}⟧")
        i += 1
    return "".join(out)


def from_text(t: str) -> bytes:
    out = bytearray()
    pos = 0
    for m in _ESC.finditer(t):
        out += t[pos:m.start()].encode("euc_jp")
        out.append(int(m.group(1), 16))
        pos = m.end()
    out += t[pos:].encode("euc_jp")
    return bytes(out)


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    strings = [b"ce000_062", b"[Canaan]What is this thing?",
               b"Press <\xa2\xa2> to destroy it.\\nNext line", b"\x83\x65\x83\x58\x83\x67 test", b""]
    blob = build(strings)
    check(parse(blob) == strings, "build/parse round trip")
    check(len(blob) % 4 == 0, "padded to 4 bytes")
    check(all(from_text(to_text(s)) == s for s in strings), "editable text is lossless")
    check(to_text(b"<\xa2\xa2>") == "<" + b"\xa2\xa2".decode("euc_jp") + ">", "EUC-JP symbols read as text")
    check("⟦83⟧" in to_text(strings[3]), "Shift-JIS bytes that are not EUC-JP become escapes")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return self_test()
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    data = open(sys.argv[1], "rb").read()
    for i, s in enumerate(parse(data)):
        print(f"### {i:04d}\n{to_text(s)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
