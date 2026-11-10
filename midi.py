"""midi.py — small Standard MIDI File helpers for Episode II's music.

The songs are ordinary SMF type 0 files (480 PPQN) that the KORG driver plays
with the project's programs (``prj.py``). The driver has no General-MIDI
drum channel, but GM players (fluidsynth, DAWs) treat MIDI channel 10 as
percussion, so ``gm_safe`` moves channel 10's events to a channel the song
does not use. The original file is never changed on disc.

    python midi.py SONG.mid                  # channels, programs, length
    python midi.py SONG.mid OUT.mid          # write the GM-safe copy
"""
from __future__ import annotations

import struct
import sys
from typing import List, Tuple


def _vlq(data: bytes, p: int) -> Tuple[int, int]:
    v = 0
    while True:
        b = data[p]
        p += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, p


def _events(track: bytes):
    """Yield (delta, status, payload_bytes, raw_slice) keeping running status
    resolved (status is always explicit in the yielded tuple)."""
    p, run = 0, 0
    while p < len(track):
        start = p
        delta, p = _vlq(track, p)
        b = track[p]
        if b == 0xFF:
            ln, q = _vlq(track, p + 2)
            yield delta, 0xFF, track[p + 1:p + 2] + track[q:q + ln], start   # type + data
            p = q + ln
            continue
        if b in (0xF0, 0xF7):
            ln, q = _vlq(track, p + 1)
            yield delta, b, track[q:q + ln], start                            # data only
            p = q + ln
            continue
        if b & 0x80:
            run = b
            p += 1
        n = 1 if (run >> 4) in (0xC, 0xD) else 2
        yield delta, run, track[p:p + n], start
        p += n


def tracks(data: bytes) -> Tuple[Tuple[int, int, int], List[bytes]]:
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hlen = struct.unpack(">I", data[4:8])[0]
    fmt, ntrk, div = struct.unpack(">HHH", data[8:14])
    p, out = 8 + hlen, []
    while p < len(data) and len(out) < ntrk:
        ln = struct.unpack(">I", data[p + 4:p + 8])[0]
        out.append(data[p + 8:p + 8 + ln])
        p += 8 + ln
    return (fmt, ntrk, div), out


def info(data: bytes) -> dict:
    (fmt, ntrk, div), trs = tracks(data)
    chans, progs, ticks = set(), {}, 0
    for t in trs:
        tick = 0
        for delta, st, payload, _ in _events(t):
            tick += delta
            if st < 0xF0:
                chans.add(st & 15)
                if st >> 4 == 0xC:
                    progs.setdefault(st & 15, set()).add(payload[0])
        ticks = max(ticks, tick)
    return {"format": fmt, "tracks": ntrk, "ppqn": div, "channels": sorted(chans),
            "programs": {c: sorted(v) for c, v in sorted(progs.items())}, "ticks": ticks}


def _write_vlq(v: int) -> bytes:
    out = [v & 0x7F]
    v >>= 7
    while v:
        out.append(0x80 | (v & 0x7F))
        v >>= 7
    return bytes(reversed(out))


def gm_safe(data: bytes, drum: int = 9) -> bytes:
    """Move channel ``drum`` (0-based 9 = MIDI channel 10) to an unused channel."""
    used = set(info(data)["channels"])
    if drum not in used:
        return data
    free = [c for c in range(15, -1, -1) if c not in used and c != drum]
    if not free:
        return data          # all 16 channels in use: leave it (prj.to_sf2 mirrors bank 128)
    to = free[0]
    (fmt, ntrk, div), trs = tracks(data)
    out = bytearray(b"MThd" + struct.pack(">IHHH", 6, fmt, ntrk, div))
    for t in trs:
        buf = bytearray()
        for delta, st, payload, _ in _events(t):
            buf += _write_vlq(delta)
            if st == 0xFF:
                buf += b"\xff" + payload[:1] + _write_vlq(len(payload) - 1) + payload[1:]
            elif st in (0xF0, 0xF7):
                buf += bytes([st]) + _write_vlq(len(payload)) + payload
            else:
                if (st & 15) == drum:
                    st = (st & 0xF0) | to
                buf += bytes([st]) + payload
        out += b"MTrk" + struct.pack(">I", len(buf)) + buf
    return bytes(out)


def main() -> int:
    a = sys.argv[1:]
    if len(a) == 1:
        print(info(open(a[0], "rb").read()))
        return 0
    if len(a) == 2:
        open(a[1], "wb").write(gm_safe(open(a[0], "rb").read()))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
