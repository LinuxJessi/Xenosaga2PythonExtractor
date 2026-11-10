"""textcodec.py — bytes of game text <-> editable text, per build, and what the font can draw.

The two builds read text bytes differently (``font.py``, from the print
functions), so one codec cannot serve both:

* **Japan and the demo**: every byte from 0xA0 up leads a two-byte EUC-JP
  code. ``EucCodec`` is the kit codec of ``sbl.py`` (Python's
  ``euc_jis_2004``, chosen there on evidence).
* **USA**: the print function accepts only a short list of two-byte codes
  (``font.us_two_byte``: lead A4, A6 or AD with any trail; A1 with BD DA DB DF
  F9 FA FB; A2 with A2 A3 A4 F6). Every other byte from 0x80 up is ONE
  character, drawn from the font's Latin-1 block (cell = byte + 0x80; 0xE9 is
  ``é``). ``UsCodec`` follows that reading:

  - a byte 0xA0-0xFF that is not part of a pair is its Latin-1 character, so a
    translator types ``é`` and the disc gets 0xE9;
  - a pair with lead A1 or A2 (and a pair with lead A6 that is valid EUC) is
    its EUC character: ``○ □ ■ △ ☆ ★ × — 「 」 ♪``, Greek. The USA font's glyphs
    for these agree with the labels (looked at, 2026-11-10);
  - ``×`` belongs to its pair (A1DF, which the retail text uses); the lone
    byte 0xD7 is written ``⟦D7⟧``;
  - a pair with lead A4 or AD is written as two escapes, ``⟦A4⟧⟦A2⟧``. In the
    USA font these cells do not hold kana: A4A1-A4A5 are ``ö é è Ä ®`` to the
    eye and the rest of the row a placeholder mark, so an EUC label would
    mislead. (The retail text uses A4A2 for ``é`` and A4A5 for ``®``.)
  - bytes 0x80-0x9F are escapes.

Both codecs are exact: ``from_text(to_text(b)) == b`` for any bytes.
``⟦XX⟧`` is a raw byte, in either direction, and is never checked: writing
one says "I know what this byte does".

``check_typed`` is the check on what a translator TYPED (every non-ASCII
character of a changed string that is not an escape). It refuses

* a character the build cannot encode, or that encodes to anything but the
  one- or two-byte form the print function reads (so EUC plane 2, 0x8F, and
  half-width kana, 0x8E, are refused on Japan);
* a character whose bytes the game would read differently from how they were
  written (on USA, ``¤`` 0xA4 followed by any byte is taken as a pair);
* a character whose code falls to the font's fallback cell 0x7F, whose cell
  the build's draw call cannot reach, or whose glyph cell is blank in font0
  or font1 of that disc (the spaces excepted).

    python textcodec.py --self-test
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import font                      # noqa: E402
import sbl                       # noqa: E402

_ESC = re.compile(r"⟦([0-9A-Fa-f]{2})⟧")
_KEEP_IN_FILES = (9, 10, 13)
_SJIS_LEAD = set(range(0x81, 0xA0)) | set(range(0xE0, 0xFD))
SPACES = ("　", " ", " ")


class TextError(ValueError):
    pass


def _esc(b: int) -> str:
    return f"⟦{b:02X}⟧"


def tokens(text: str) -> List[Tuple[str, Optional[int]]]:
    """The text as (character, None) and ("", raw byte) items, in order."""
    out, pos = [], 0
    for m in _ESC.finditer(text):
        out += [(ch, None) for ch in text[pos:m.start()]]
        out.append(("", int(m.group(1), 16)))
        pos = m.end()
    out += [(ch, None) for ch in text[pos:]]
    return out


class Codec:
    """One build's reading of text bytes. ``file=True`` is for a whole text file:
    tabs and line ends stay as they are; without it they are escapes and a
    leading ``#`` is written ``⟦23⟧`` (in a block file a line starting with ``#``
    is a comment)."""
    name = ""

    def _decode(self, s: bytes, file: bool) -> str:
        raise NotImplementedError

    def encode_char(self, ch: str) -> bytes:
        raise NotImplementedError

    def read(self, s: bytes, i: int) -> int:
        """How many bytes the game's print function takes as one character at s[i]."""
        raise NotImplementedError

    def to_text(self, s: bytes, file: bool = False) -> str:
        t = self._decode(s, file)
        if not file and t.startswith("#"):
            t = "⟦23⟧" + t[1:]
        return t

    def spans(self, text: str) -> Tuple[bytes, List[Tuple[int, int, str]]]:
        """(bytes, [(start, end, typed character)]) with a span for every typed
        non-ASCII character. TextError names a character that cannot be written."""
        out, spans = bytearray(), []
        for ch, raw in tokens(text):
            if raw is not None:
                out.append(raw)
            elif ord(ch) < 0x80:
                out.append(ord(ch))
            else:
                b = self.encode_char(ch)
                spans.append((len(out), len(out) + len(b), ch))
                out += b
        return bytes(out), spans

    def from_text(self, text: str) -> bytes:
        return self.spans(text)[0]


class EucCodec(Codec):
    name = "euc"

    def __init__(self, codec: str = sbl.CODEC):
        self.codec = codec

    def _decode(self, s: bytes, file: bool) -> str:
        out, i, n = [], 0, len(s)
        while i < n:
            b = s[i]
            if b < 0x80:
                keep = b >= 0x20 or (file and b in _KEEP_IN_FILES)
                out.append(chr(b) if keep else _esc(b))
                i += 1
                continue
            size = 3 if b == 0x8F else 2
            piece = s[i:i + size]
            try:
                ch = piece.decode(self.codec)
                if len(ch) == 1 and ch.encode(self.codec) == piece and ch not in "⟦⟧":
                    out.append(ch)
                    i += size
                    continue
            except UnicodeDecodeError:
                pass
            out.append(_esc(b))
            i += 1
        return "".join(out)

    def encode_char(self, ch: str) -> bytes:
        try:
            return ch.encode(self.codec)
        except UnicodeEncodeError:
            raise TextError(f"{ch!r} (U+{ord(ch):04X}) is not in the game's character set (EUC-JP)")

    def read(self, s: bytes, i: int) -> int:
        return 2 if s[i] >= 0xA0 else 1                  # Japan 0x198508: any byte from 0xA0 leads a pair


class SjisCodec(EucCodec):
    """Whole Shift-JIS files (the quest text). Not a build's print-function reading:
    how the game turns these bytes into glyphs has not been read."""
    name = "sjis"

    def __init__(self):
        super().__init__("shift_jis")

    def _decode(self, s: bytes, file: bool) -> str:
        out, i, n = [], 0, len(s)
        while i < n:
            b = s[i]
            if b < 0x80:
                keep = b >= 0x20 and b != 0x7F or (file and b in _KEEP_IN_FILES)
                out.append(chr(b) if keep else _esc(b))
                i += 1
                continue
            size = 2 if b in _SJIS_LEAD else 1
            piece = s[i:i + size]
            try:
                ch = piece.decode("shift_jis")
                if len(ch) == 1 and ch.encode("shift_jis") == piece and ch not in "⟦⟧":
                    out.append(ch)
                    i += size
                    continue
            except UnicodeDecodeError:
                pass
            out.append(_esc(b))
            i += 1
        return "".join(out)

    def encode_char(self, ch: str) -> bytes:
        try:
            return ch.encode("shift_jis")
        except UnicodeEncodeError:
            raise TextError(f"{ch!r} (U+{ord(ch):04X}) cannot be written in Shift-JIS")


def _us_pairs() -> Dict[bytes, str]:
    """USA: the pairs shown as a character. Leads A1 and A2 from the game's list,
    lead A6 where the pair is valid EUC."""
    out = {}
    cand = [(0xA1, t) for t in (0xBD, 0xDA, 0xDB, 0xDF, 0xF9, 0xFA, 0xFB)]
    cand += [(0xA2, t) for t in (0xA2, 0xA3, 0xA4, 0xF6)]
    cand += [(0xA6, t) for t in range(0xA1, 0xFF)]
    for a, b in cand:
        piece = bytes((a, b))
        try:
            ch = piece.decode(sbl.CODEC)
        except UnicodeDecodeError:
            continue
        if len(ch) == 1 and ch.encode(sbl.CODEC) == piece:
            out[piece] = ch
    return out


class UsCodec(Codec):
    name = "us"
    PAIRS = _us_pairs()
    CHARS = {ch: piece for piece, ch in PAIRS.items()}

    def read(self, s: bytes, i: int) -> int:
        if i + 1 >= len(s):                      # a lead byte with nothing after it is one byte
            return 1
        return 2 if s[i] >= 0x80 and font.us_two_byte(s[i], s[i + 1]) else 1

    def _decode(self, s: bytes, file: bool) -> str:
        out, i, n = [], 0, len(s)
        while i < n:
            b = s[i]
            if b < 0x80:
                keep = b >= 0x20 or (file and b in _KEEP_IN_FILES)
                out.append(chr(b) if keep else _esc(b))
                i += 1
            elif self.read(s, i) == 2:
                ch = self.PAIRS.get(s[i:i + 2])
                out.append(ch if ch else _esc(b) + _esc(s[i + 1]))
                i += 2
            else:
                ch = chr(b)
                out.append(ch if b >= 0xA0 and ch not in self.CHARS else _esc(b))
                i += 1
        return "".join(out)

    def encode_char(self, ch: str) -> bytes:
        if ch in self.CHARS:
            return self.CHARS[ch]
        if 0xA0 <= ord(ch) <= 0xFF:
            return bytes((ord(ch),))
        raise TextError(f"{ch!r} (U+{ord(ch):04X}) cannot be written on the USA build: its print function "
                        f"reads Latin-1 bytes and a few symbol pairs, not Japanese text")


class UsSubtitleCodec(UsCodec):
    """A USA subtitle file: cue lines as the print function reads them, comment lines
    (``;``, the developers' notes, never drawn) as EUC-JP."""
    name = "us-subtitle"
    _euc = EucCodec()

    def _lines(self, s: bytes) -> List[bytes]:
        return re.findall(rb"[^\n]*\n|[^\n]+$", s)

    def to_text(self, s: bytes, file: bool = True) -> str:
        return "".join(self._euc._decode(ln, True) if ln.startswith(b";") else UsCodec._decode(self, ln, True)
                       for ln in self._lines(s))

    def spans(self, text: str):
        out, spans = bytearray(), []
        for ln in re.findall(r"[^\n]*\n|[^\n]+$", text):
            if ln.startswith(";"):
                out += self._euc.from_text(ln)
            else:
                b, sp = UsCodec.spans(self, ln)
                spans += [(a + len(out), e + len(out), ch) for a, e, ch in sp]
                out += b
        return bytes(out), spans


def for_build(build: str) -> Codec:
    return UsCodec() if build == "us" else EucCodec()


def subtitle_codec(build: str) -> Codec:
    return UsSubtitleCodec() if build == "us" else EucCodec()


def check_typed(codec: Codec, text: str, addressing, fonts: Dict[str, "font.Font"]) -> Optional[str]:
    """None when every typed non-ASCII character of ``text`` will be drawn as typed on
    this build; else the reason, naming the first character that will not."""
    data, spans = codec.spans(text)
    # where the game's own reading starts each character
    starts, i = {}, 0
    while i < len(data):
        n = codec.read(data, i) if data[i] >= 0x80 else 1
        starts[i] = n
        i += n
    for a, e, ch in spans:
        size = e - a
        what = f"{ch!r} (U+{ord(ch):04X}, bytes {data[a:e].hex().upper()})"
        if size > 2 or data[a] in (0x8E, 0x8F) and not addressing.latin1:
            kind = "a 3-byte plane-2 sequence (prefix 0x8F)" if size > 2 else "half-width kana (prefix 0x8E)"
            return (f"{what} is {kind} in EUC-JP; the game reads plain one- and two-byte characters only")
        if starts.get(a) != size:
            return (f"{what}: the game would not read these bytes as written (a byte beside it pairs with "
                    f"it, or splits it). On the USA build ¤ ¦ and the soft hyphen always take the next byte")
        g = addressing.glyph(data + b"\x00", a, han=True)
        if g.length != size or g.cell is None:
            return f"{what}: the print function reads {g.length} byte(s) here, not {size}"
        if g.cell == font.FALLBACK_CELL:
            return f"{what} has no glyph: its code falls to the font's fallback cell 0x7F"
        if not addressing.reachable(g.cell):
            return f"{what} maps to cell {g.cell}, which this build's draw call cannot reach"
        if ch in SPACES:
            continue
        for name, f in fonts.items():
            for prop in (True, False):
                cell = addressing.drawn_cell(g.cell, prop)
                cache = f.__dict__.setdefault("_inked", {})        # the font is not edited while checking
                if cell not in cache:
                    cache[cell] = f.ink_bounds(cell) is not None
                if not cache[cell]:
                    return (f"{what} has no glyph: cell {cell} of {name} is blank"
                            f"{' (the proportional image)' if prop and cell != g.cell else ''}")
    return None


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    us, euc, sj = UsCodec(), EucCodec(), SjisCodec()
    every = bytes(range(256)) * 2 + bytes(range(255, -1, -1)) + b"\xa4\xa2\xa1\xdf\xa1\xa1\xa2\xf6\xad\xa1"
    check(all(c.from_text(c.to_text(every)) == every and c.from_text(c.to_text(every, file=True)) == every
              for c in (us, euc, sj, UsSubtitleCodec())), "every codec is exact on arbitrary bytes, block and file form")
    check(us.to_text(b"Caf\xe9 \xc9t\xe9") == "Café Été" and us.from_text("Café Été") == b"Caf\xe9 \xc9t\xe9",
          "USA: a lone high byte is its Latin-1 character (é = 0xE9)")
    check(us.to_text(b"<\xa2\xa2> \xa1\xdf2 \xa2\xf6") == "<□> ×2 ♪" and us.from_text("<□> ×2 ♪") == b"<\xa2\xa2> \xa1\xdf2 \xa2\xf6",
          "USA: the game's symbol pairs are their characters; × is the pair A1DF")
    check(us.to_text(b"\xd7") == "⟦D7⟧" and us.to_text(b"Pok\xa4\xa2mon\xa4\xa5") == "Pok⟦A4⟧⟦A2⟧mon⟦A4⟧⟦A5⟧",
          "USA: the lone byte D7 and the A4 pairs are escapes")
    check(us.to_text(b"\x85\x9f\xa1\xa1") == "⟦85⟧⟦9F⟧¡¡" and us.to_text(b"\xa6\xb8") == "Ω", "USA: 0x80-0x9F escapes; A1A1 is two ¡; A6B8 is Ω")
    try:
        us.from_text("あ")
        check(False, "USA: kana cannot be typed")
    except TextError:
        check(True, "USA: kana cannot be typed (the USA print function has no kana)")
    check(euc.to_text(b"\xa4\xa2\xad\xa1") == "あ①" and euc.from_text("あ①") == b"\xa4\xa2\xad\xa1", "Japan: EUC-JP with row 13")
    check(us.to_text(b"#1 x") == "⟦23⟧1 x" and euc.to_text(b"### 0001") == "⟦23⟧## 0001" and us.from_text("⟦23⟧1 x") == b"#1 x"
          and us.to_text(b"a#b") == "a#b", "a body that starts with # is written with ⟦23⟧; elsewhere # is plain")
    check(us.to_text(b"a\nb\tc") == "a⟦0A⟧b⟦09⟧c" and us.to_text(b"a\nb\tc", file=True) == "a\nb\tc"
          and us.to_text(b"#x", file=True) == "#x", "file form keeps line ends and tabs, and leaves # alone")
    sub = ";\xa5\xc6\xa5\xb9\xa5\xc8 caf\xe9\n00:01:00\tCaf\xe9 \xa2\xf6\nend\n".encode("latin1")
    t = UsSubtitleCodec().to_text(sub)
    check(t == ";テスト caf⟦E9⟧\n00:01:00\tCafé ♪\nend\n" and UsSubtitleCodec().from_text(t) == sub,
          "USA subtitle file: comment lines read as EUC-JP, cue lines as the print function reads them")
    check(sj.to_text("ｶﾅ漢".encode("shift_jis"), file=True) == "ｶﾅ漢", "Shift-JIS files")

    # check_typed against two synthetic builds and a font with ink only where told
    import region
    extra = ((0xD0, 0xBC),)
    us_adr = font.Addressing.from_executable(
        font._synthetic_elf(0x2B1100, 0x2B1200, ()),
        region.Region("us", "en", "sbl", (), "", "", ascii_table_va=0x2B1100, kanji_list_va=0x2B1200))
    jp_adr = font.Addressing.from_executable(
        font._synthetic_elf(0x3C2200, 0x3C2300, extra),
        region.Region("jp", None, "inline", (), "", "", ascii_table_va=0x3C2200, kanji_list_va=0x3C2300))

    def blank(n, inked):
        f = font.Font([bytes(font.HEADER)] * 2, [bytearray(font.SHEET_W * font.SHEET_H) for _ in range(4)],
                      [(4, 9)] * n)
        for c in inked:
            f.set_cell(c, [[3] * font.CELL_W] * font.CELL_H)
        return f

    fu = {"font0": blank(384, [0xE9 + 0x80, 0xE9 + 0x80 + 576, 0x5F, 0x5F + 576])}       # é, □
    fj = {"font0": blank(256, [193, 3989, 1024])}                                           # あ, the extra kanji, 亜
    check(check_typed(us, "Café <□>", us_adr, fu) is None, "USA: é and □ have glyphs")
    check("blank" in (check_typed(us, "Señor", us_adr, fu) or ""), "USA: a Latin-1 letter with a blank cell is refused")
    check(check_typed(us, "Se⟦F1⟧or", us_adr, fu) is None, "... unless it is written as an explicit escape")
    check("would not read" in (check_typed(us, "¤x", us_adr, fu) or ""), "USA: ¤ before another byte would be read as a pair")
    check(check_typed(euc, "あ亜", jp_adr, fj) is None and check_typed(euc, bytes((0xD0, 0xBC)).decode(sbl.CODEC), jp_adr, fj) is None,
          "Japan: kana, kanji and a listed extra kanji pass")
    check("fallback cell" in (check_typed(euc, "～", jp_adr, fj) or ""), "Japan: ～ (U+FF5E -> A2B2) falls to the fallback cell: refused")
    check("fallback cell" in (check_typed(euc, bytes((0xD0, 0xBD)).decode(sbl.CODEC), jp_adr, fj) or ""),
          "Japan: a kanji outside level 1 and the executable's list is refused")
    plane2 = next(chr(c) for c in range(0x3400, 0x9FFF) if len(chr(c).encode(sbl.CODEC, "ignore")) == 3)
    check("3-byte" in (check_typed(euc, plane2, jp_adr, fj) or ""), "Japan: a plane-2 (0x8F) character is refused")
    check("half-width" in (check_typed(euc, "ｶ", jp_adr, fj) or ""), "Japan: half-width kana (0x8E) is refused")
    check("blank" in (check_typed(euc, "い", jp_adr, fj) or ""), "Japan: a code whose cell is blank in the font is refused")
    check(check_typed(euc, "あ　あ", jp_adr, fj) is None, "the ideographic space is allowed to be blank")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print(__doc__)
