"""moviesub.py — how the movie player packs a subtitle file, and the size rule for edits.

``movie\\en\\<movie>.txt`` (USA; Japan: ``movie\\<movie>.txt``) is plain text:
``;`` comment lines, cue lines ``mm:ss:ff<TAB>text`` (or a frame count, or
``+N`` frames after the previous cue), ``\\n`` inside a text for a line break,
``[kanji/ruby]``, ``@`` for a leading space, and a line that is not a cue
(``end``) closes the list.

The player (USA ``FUN_001e7d08``) reads the file, whole sectors, into the
start of the block it allocates for the Sofdec decoder, then packs the cues
in place with ``FUN_001e7a08`` / ``FUN_001e7610``, which ``pack`` below
follows instruction for instruction (checked against the disassembly at
0x1e7a08, 2026-11-10)::

    first text + NUL         the cue at frame 0 if the file starts with one, else one NUL
    then per cue             u16 frame, 0x01, x-offset byte, text, NUL
                             (a line break is 0x0A, 0x01, x-offset byte;
                              ruby becomes $rubyb;..$rubys;..$rubye;)
    terminator               0xFFFF, NUL, one stale byte

``packed_size`` is the length of that data: the cue bytes the game keeps.

The packing is done in place, the output chasing the input, and nothing in the
game checks that it stays behind. ``pack`` does: it refuses a file in which a
write would land on text not yet read (the first cue too close to the top of
a file that does not open with a frame-0 cue; a run of very short cue lines)
or whose time field is followed by anything but a blank, a tab or a line end
(the game's parser would not come back from that), and a list that is closed
only by whatever follows the file on the disc.

The size rule ``check_edit`` enforces is the one recorded in docs/MODDING.md
from the running game (PCSX2, 2026-11-05): **the packed cues may not grow past
the next 64-byte boundary of the retail file's packed cues.** One movie was
tried: longer comments were harmless, 52 more characters in one line broke
playback.

What the code says about WHY is less settled, and MODDING.md records the gap:
the decoder's work area starts at ``block + align64(L)``, and ``L`` is what
``FUN_001e7a08`` returns, which is NOT the packed size. After the terminator
the function keeps scanning to the next NUL, through the unpacked rest of the
file, so ``L`` ends at the first NUL after the end of the FILE (on the retail
disc, the filler in the sector's slack: the next multiple of 32 above the file
size). ``work_offset`` computes it. By that reading comment lines move the
work area and displayed text does not, the opposite of the recorded
explanation; the recorded observations (comments safe, a much longer line
not) fit a limit on something else, such as the length of one line. Until
that is seen in the game the kit enforces the recorded rule, which is the
stricter one for displayed text.

Because the 64-byte rule is known not to be the mechanism, ``check_edit`` also
applies bounds taken from the retail files of the disc being patched
(``Limits``, built by ``limits_of``): no line longer in bytes, and none wider in
pixels by the game's own width function, than the longest retail line on that
disc; no cue with more lines than the retail maximum; the same cues at the
same frames. These are NOT proven limits. They say only "the retail game is
known to display this much", and an edit inside them stays inside what has
been seen to work.

Three more things the packer does, read from the same code, are refused in an
edited file because the result is certain: a backslash is a line break
whatever follows it and the next byte is dropped (0x1e79b4-0x1e79cc; every
retail backslash is followed by ``n``), so ``\`` before anything else is
refused; a frame number is stored in 16 bits and 0xFFFF ends the list, so a
cue at frame 65535 or later is refused; and the x-offset byte, ``~(width / 2)``,
is zero for a line 510 or 511 pixels wide, which would end the cue's text
early, so such a line is refused when widths are known.

    python moviesub.py --self-test
    python moviesub.py FILE.txt           # packed size and the room to the boundary
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

BOUNDARY = 64


class SubtitleError(ValueError):
    pass


def _skip(b: bytearray, p: int) -> int:
    """FUN_001e7588: step over the rest of a line and any blank or ``;`` lines after it."""
    c = b[p]
    if c not in (13, 10):
        if c != 0x3B:
            return p
        p += 1
        c = b[p]
    while True:
        while True:
            p += 1
            if c in (13, 10):
                break
            c = b[p]
        c = b[p]
        if c not in (13, 10, 0x3B):
            return p


def _put(b: bytearray, o: int, value, p: int) -> None:
    """Write packed bytes at ``o`` while the reader is at ``p``: the game does this
    unchecked; here a write onto unread text is an error."""
    n = 1 if isinstance(value, int) else len(value)
    if o + n > p:
        raise SubtitleError("the packed text would overwrite cue lines not yet read (the game packs in "
                            "place): keep the comment lines above the first cue, and do not use many "
                            "very short cue lines in a row")
    if n == 1:
        b[o] = value
    else:
        b[o:o + n] = value


def _cue(b: bytearray, p: int, state: int, out: int, width: Callable[[bytes], int],
         strict: bool = False) -> Tuple[int, int, int]:
    """FUN_001e7610: parse the cue at ``p``, write its text at ``out``; (next p, frame
    or -1, the offset after the last byte written)."""
    c = b[p]
    if not 0x30 <= c <= 0x39 and c != 0x2B:
        b[out] = 0
        return _skip(b, p), -1, out + 1
    plus = c == 0x2B
    if plus:
        p += 1
        c = b[p]
    colons = t = 0
    spaced = False
    while True:
        v = 0
        while 0x30 <= c <= 0x39:
            p += 1
            v = v * 10 + c - 0x30
            c = b[p]
        t += v
        if c == 0x3A:
            colons += 1
            p += 1
            c = b[p]
            t = t * 60 if colons == 1 else t * 30
        if c == 0x20:
            p += 1
            spaced = True
            break
        if c in (9, 13, 10):
            break
        if not 0x30 <= c <= 0x39:
            raise SubtitleError(f"a cue's time is followed by byte {c:#04x}; it must be followed by a "
                                f"tab, a blank or the end of the line (the game's parser would hang)")
    if spaced or c == 9:
        if not spaced:
            p += 1
        while b[p] in (0x20, 9):
            p += 1
        c = b[p]
    if plus:
        t += state
    if strict and t >= 0xFFFF:
        raise SubtitleError(f"a cue at frame {t}: the game keeps 16 bits of the frame number and 0xFFFF "
                            f"ends the list")
    head = out
    while True:
        o = head + 2
        if c == 0x40:                               # '@': a leading space
            p += 1
            _put(b, o, 0x20, p)
            o += 1
            c = b[p]
        ch = c
        p += 1
        if ch not in (0x5C, 13, 10):
            while True:
                if ch == 10:
                    break
                if ch == 0x5B:                      # [kanji/ruby]
                    for tag, stop in ((b"$rubyb;", 0x2F), (b"$rubys;", 0x5D)):
                        _put(b, o, tag, p)
                        o += 7
                        while True:
                            c2 = b[p]
                            p += 1
                            if c2 == stop:
                                break
                            if c2 in (0, 10, 13):
                                raise SubtitleError("a '[' without its '/' and ']' on the same line "
                                                    "(ruby is written [kanji/reading])")
                            _put(b, o, c2, p)
                            o += 1
                    _put(b, o, b"$rubye;", p)
                    o += 7
                    ch = b[p]
                else:
                    _put(b, o, ch, p)
                    o += 1
                    ch = b[p]
                p += 1
                if ch in (0x5C, 13):
                    break
        _put(b, o, 0, p)
        b[head] = 1
        b[head + 1] = ~(width(bytes(b[head + 2:o])) // 2) & 0xFF
        if strict and b[head + 1] == 0:
            raise SubtitleError("a line 510 or 511 pixels wide: its x-offset byte would be 0 and end the "
                                "cue's text early")
        if ch != 0x5C:
            return _skip(b, p), t, o + 1
        if strict and b[p] != 0x6E:
            raise SubtitleError(f"a backslash followed by {bytes(b[p:p + 1])!r}: the game makes every backslash "
                                f"a line break and drops the byte after it; write \\n")
        b[o] = 10                                   # "\n": the next line of the same cue
        p += 1
        c = b[p]
        head = o + 1


def pack(data: bytes, slack: bytes = b"\x00", width: Optional[Callable[[bytes], int]] = None,
         strict: bool = False) -> Tuple[int, int, bytes]:
    """Run the game's in-place packing on ``data`` followed by ``slack`` (what follows
    the file in its last sector; one NUL when unknown). Returns (packed size, L, packed
    bytes): the cue data's length, the value FUN_001e7a08 returns, and the data.
    ``width`` is the pixel width of a line (the x-offset byte depends on it; sizes do not).
    ``strict`` adds the refusals for an EDITED file (backslash, frame range, x-offset 0)."""
    if b"\x00" in data:
        raise SubtitleError("a NUL byte in the file: the game's packer stops at it")
    width = width or (lambda s: 0)
    b = bytearray(data) + bytearray(slack) + bytearray(8)
    try:
        p0 = _skip(b, 0)
        p, state, wrote = _cue(b, p0, 0, 0, width, strict)
        q = 0
        if state == 0:
            while b[q]:
                q += 1
            q += 1
        else:
            if state > 0 and wrote > p0:        # the first pass wrote over the line it is about to re-read
                raise SubtitleError("the file's first cue is not at frame 0 and there is too little text "
                                    "above it: the game packs in place and would overwrite the cue list. "
                                    "Keep the comment lines at the top of the file")
            b[0] = 0
            q, p = 1, p0
        state = 0
        while True:
            last = p
            p, state, _ = _cue(b, p, state, q + 2, width, strict)
            b[q], b[q + 1] = state & 0xFF, (state >> 8) & 0xFF
            q += 4
            packed = q
            while b[q]:
                q += 1
            q += 1
            if state < 0:
                break
    except IndexError:
        raise SubtitleError("the cue list does not end: the last line must be one that is not a cue "
                            "(the retail files end with 'end')")
    if last >= len(data):
        raise SubtitleError("the cue list is not closed inside the file: its last line must be one that "
                            "is not a cue (the retail files end with 'end')")
    return packed, q, bytes(b[:packed])


def packed_size(data: bytes) -> int:
    return pack(data)[0]


def cues(data: bytes, width: Optional[Callable[[bytes], int]] = None, strict: bool = False
         ) -> Tuple[List[bytes], List[Tuple[int, List[bytes]]]]:
    """(lines of the first text, [(frame, lines)]) as the game will hold them: each
    line without its 2-byte prefix, ruby already expanded."""
    packed = pack(data, width=width, strict=strict)[2]

    def lines(t: bytes) -> List[bytes]:
        return [ln[2:] for ln in t.split(b"\x0a")] if t else []

    end = packed.index(b"\x00")
    first, pos, out = lines(packed[:end]), end + 1, []
    while True:
        frame = packed[pos] | packed[pos + 1] << 8
        if frame == 0xFFFF:
            break
        end = packed.index(b"\x00", pos + 4)          # +2, +3 are the first line's prefix
        out.append((frame, lines(packed[pos + 2:end])))
        pos = end + 1
    return first, out


@dataclass(frozen=True)
class Limits:
    """What the retail subtitle files of one disc reach. Not a proven limit of the
    game: a bound on what it is known to display."""
    line_bytes: int             # the longest displayed line, in bytes
    line_width: Optional[int]   # ... and in pixels by the game's width function (None: not measured)
    cue_lines: int              # the most lines in one cue
    files: int


def limits_of(files: Iterable[bytes], measure: Optional[Callable[[], Callable[[bytes], int]]] = None) -> Limits:
    """``measure()`` returns a fresh width function for one file (the game's width
    state starts over with each file's ``$bg3;$prop1;$han1;...`` line)."""
    nb = nw = nl = n = 0
    for data in files:
        w = measure() if measure else None
        first, rest = cues(data)
        n += 1
        for group in [first] + [g for _, g in rest]:
            nl = max(nl, len(group))
            for ln in group:
                nb = max(nb, len(ln))
                if w:
                    nw = max(nw, w(ln))
    return Limits(nb, nw if measure else None, nl, n)


def work_offset(data: bytes, slack: bytes = b"\x00") -> int:
    """Where the decoder's work area starts, per the code: align64 of FUN_001e7a08's
    return value, which runs to the first NUL after the end of the file."""
    return (pack(data, slack)[1] + BOUNDARY - 1) // BOUNDARY * BOUNDARY


def room(data: bytes) -> int:
    """Bytes the packed cues of a retail file may grow before the next 64-byte boundary."""
    return -packed_size(data) % BOUNDARY


def check_edit(old: bytes, new: bytes, limits: Optional[Limits] = None,
               measure: Optional[Callable[[], Callable[[bytes], int]]] = None) -> Optional[str]:
    """None when ``new`` may replace the retail ``old``; else the reason it may not.
    ``limits`` are the disc's retail bounds (``limits_of``) and ``measure`` the same
    width-function factory they were built with."""
    try:
        a = packed_size(old)
        old_first, old_cues = cues(old)
    except SubtitleError as e:
        return f"the original does not parse as a cue list ({e}); not edited"
    try:
        b = pack(new, strict=True)[0]
        w = measure() if measure else None
        new_first, new_cues = cues(new, width=w, strict=True)
    except SubtitleError as e:
        return str(e)
    except ValueError as e:                     # the width function could not follow a line
        return f"a line cannot be measured: {e}"
    if [f for f, _ in new_cues] != [f for f, _ in old_cues] or bool(new_first) != bool(old_first):
        return (f"the cue list changed: {len(new_cues)} cues, the original has {len(old_cues)}, or their "
                f"frames differ. text-import changes text only; every cue must stay, at its frame")
    if limits is not None:
        note = (" This bound is what the retail files of this disc reach, not a proven limit of the game: "
                "longer text has not been seen to work (docs/MODDING.md)")
        w = measure() if measure and limits.line_width is not None else None
        for group in [new_first] + [g for _, g in new_cues]:
            if len(group) > limits.cue_lines:
                return (f"a cue has {len(group)} lines; no retail cue on this disc has more than "
                        f"{limits.cue_lines}." + note)
            for ln in group:
                if len(ln) > limits.line_bytes:
                    return (f"the line {ln[:40]!r}... is {len(ln)} bytes; the longest retail line on this "
                            f"disc is {limits.line_bytes}." + note)
                if w is not None:
                    px = w(ln)
                    if px > limits.line_width:
                        return (f"the line {ln[:40]!r}... is {px} pixels wide by the game's width function; "
                                f"the widest retail line on this disc is {limits.line_width}." + note)
    limit = (a + BOUNDARY - 1) // BOUNDARY * BOUNDARY
    if b > limit:
        return (f"the displayed text packs to {b} bytes, the original to {a}; it may grow to {limit} "
                f"({limit - a} more bytes), the next 64-byte boundary, and this is {b - limit} past it. "
                f"The movie player keeps the packed cues at the start of the video decoder's memory "
                f"block; a longer line broke playback in the running game (docs/MODDING.md). "
                f"Comment lines (';') are not counted")
    return None


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    src = (b";a comment\n; another\n\n0\t$ay390;\n00:01:00\tHello\n00:02:15\tTwo\\nlines\n"
           b"00:03:00\t\n+30\t@Again\n150\t[K/r]x\nend\n;trailer\n")
    n, L, out = pack(src)
    want = (b"\x01\xff$ay390;\x00"                               # the frame-0 cue: the first text
            + b"\x1e\x00\x01\xffHello\x00"                       # 00:01:00 = frame 30
            + b"\x4b\x00\x01\xffTwo\x0a\x01\xfflines\x00"        # 00:02:15 = 75; "\n" = 0A + a new prefix
            + b"\x5a\x00\x01\xff\x00"                            # an empty cue clears the line
            + b"\x78\x00\x01\xff Again\x00"                      # +30 after 90; '@' = a space
            + b"\x96\x00\x01\xff$rubyb;K$rubys;r$rubye;x\x00"    # frame 150, ruby
            + b"\xff\xff\x00")                                   # the terminator (+ one stale byte)
    check(out[:len(want)] == want and n == len(want) + 1, "the packed cue list, byte for byte")
    check(L == len(src) + 1, "the function's own return value runs to the first NUL after the file")
    check(pack(src, b"xyz\x00")[1] == len(src) + 4 and pack(src, b"xyz\x00")[0] == n,
          "... wherever that NUL is; the packed size does not depend on it")
    nocue0 = b"; a comment line\n00:01:00\tHi\nend\n"
    check(pack(nocue0)[2] == b"\x00" + b"\x1e\x00\x01\xffHi\x00" + b"\xff\xff\x00" + pack(nocue0)[2][-1:],
          "no cue at frame 0: the first text is empty")
    for bad, why in ((b"; c\n00:01:00\tHi\nend\n", "too little text above"),
                     (b"0\tA\n5\tb\n6\tc\n7\td\n8\te\nend\n", "not yet read"),
                     (b"0\tA\n00:01:00;x\nend\n", "time is followed"),
                     (b";" + b"-" * 40 + b"\n0\tA\n00:01:00\t[kanji\nend\n", "'[' without")):
        try:
            pack(bad)
            check(False, f"refused: {why}")
        except SubtitleError as e:
            check(why in str(e), f"refused: {why}")
    crlf = src.replace(b"\n", b"\r\n")
    check(packed_size(crlf) == n, "CRLF files pack to the same size")
    check(packed_size(src.replace(b";a comment", b";a much, much longer comment " * 20)) == n,
          "comment lines do not count")
    check(packed_size(src.replace(b"Hello", b"Hello!")) == n + 1, "one more displayed byte, one more packed byte")
    r = room(src)
    check(check_edit(src, src.replace(b"Hello", b"Hello" + b"!" * r)) is None, "text may grow up to the 64-byte boundary")
    msg = check_edit(src, src.replace(b"Hello", b"Hello" + b"!" * (r + 1)))
    check(msg is not None and "1 past it" in msg and "64-byte" in msg, "one byte past the boundary is refused, with the reason")
    check(check_edit(src, src.replace(b"Hello", b"He")) is None, "shorter text is accepted")
    check("not closed inside the file" in (check_edit(src, src.replace(b"end\n;trailer\n", b"")) or ""),
          "a list without its end line is refused")
    check(work_offset(src) == (len(src) + 1 + 63) // 64 * 64, "the work-area offset follows the file size (per the code)")
    # the cue list as the game holds it, and the retail bounds
    first, rest = cues(src)
    check(first == [b"$ay390;"] and rest[1] == (75, [b"Two", b"lines"]) and rest[2] == (90, [b""])
          and rest[4] == (150, [b"$rubyb;K$rubys;r$rubye;x"]) and len(rest) == 5, "cues(): frames and lines")
    lim = limits_of([src], lambda: len)                          # a stand-in width: one pixel per byte
    check(lim == Limits(24, 24, 2, 1), "limits_of: longest line 24 bytes, 2 lines in a cue")
    grow = lambda old, new: check_edit(src, src.replace(old, new), lim, lambda: len)     # noqa: E731
    check(grow(b"Hello", b"Hi") is None and grow(b"Hello", b"Hello!") is None, "an edit inside every bound passes")
    msg = grow(b";a comment", b";c") and None or grow(b"Two\\nlines", b"T\\nw\\no")
    check(msg is not None and "3 lines" in msg and "not a proven limit" in msg, "more lines in a cue than retail has: refused, and says what the bound is")
    msg = grow(b"00:03:00\t\n", b"00:03:00\t" + b"x" * 25 + b"\n")
    check(msg is not None and "25 bytes" in msg, "a line longer than the longest retail line: refused")
    msg = check_edit(src, src.replace(b"Hello", b"WWWWWW"), lim, lambda: (lambda s: 5 * len(s)))
    check(msg is not None and "pixels wide" in msg, "a line wider than the widest retail line: refused")
    check("cue list changed" in (grow(b"00:03:00\t\n", b"") or "") and "cue list changed" in (grow(b"00:01:00", b"00:01:01") or ""),
          "a removed cue or a moved frame is refused")
    check("line break" in (check_edit(src, src.replace(b"Two\\nlines", b"Two\\tlines")) or ""), "a backslash not followed by n is refused")
    check("16 bits" in (check_edit(src, src.replace(b"150\t", b"70000\t")) or ""), "a frame past 16 bits is refused")
    check("x-offset" in (check_edit(src, src, None, lambda: (lambda s: 510 if s == b"Hello" else 0)) or ""),
          "a line whose x-offset byte would be 0 is refused")
    check("NUL" in (check_edit(src, src.replace(b"Hello", b"He\x00lo")) or ""), "a NUL in the file is refused")
    multi = b";" + b"-" * 9 + b"\n00:01:00\tab\\ncdefgh\nend\n"       # first cue not at 0, two lines, 10 bytes above it
    try:
        pack(multi)
        check(False, "first cue with two lines and too little text above it")
    except SubtitleError as e:
        check("too little text above" in str(e), "the first-cue check counts every byte the first pass wrote (two lines)")
    check(pack(b";" + b"-" * 20 + b"\n00:01:00\tab\\ncdefgh\nend\n")[2][1:3] == b"\x1e\x00", "... and passes with enough room")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    d = open(sys.argv[1], "rb").read()
    print(f"{len(d)} bytes; cues pack to {packed_size(d)} bytes; room to the 64-byte boundary: {room(d)}")
