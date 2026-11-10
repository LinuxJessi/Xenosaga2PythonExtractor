"""songfx.py — what Episode II's songs ask of the SPU effect unit.

The field songs set the SPU2 effect (reverb/echo) themselves, from inside the
MIDI file. The KORG driver (``IOP\\KORGIVAG.IRX``, which ships with its symbol
table) reads it in ``CcProcDataEntryMsb``; every song labels the controllers
with text events, so the file and the driver agree on the meaning
(2026-11-05):

    text "FX1 MODE"      NRPN 2/16 (CC99 2, CC98 16), data entry (CC6) = mode
    text "FX1 VOL"       NRPN 3/16  return volume 0-127
    text "FX1 PAN"       NRPN 3/17  return pan (64 = centre)
    text "FX1 DELAY"     NRPN 3/18  delay time 0-127 (ECHO and DELAY modes)
    text "FX1 FEEDBACK"  NRPN 3/19  feedback 0-127 (ECHO and DELAY modes)

NRPN LSB bit 4 picks the SPU2 core (16-19 = core 1, the "EFX core1" the songs
mention). ``InternalSetFxType`` clamps the mode to 0-9 and ``FxChangeThread``
hands it unchanged to ``sceSdSetEffectAttr``, so it is libsd's numbering
(``SD_REV_MODE_*`` in the SDK's sdmacro.h): 0 OFF, 1 ROOM, 2 STUDIO_A (small),
3 STUDIO_B (medium), 4 STUDIO_C (large), 5 HALL, 6 SPACE (space echo), 7 ECHO,
8 DELAY, 9 PIPE (pipe echo). The driver's default record is mode 0, so a song
that never sets a mode plays dry. The return level is the driver's volume
table, ``32767 * (vol/127)**2``, times an equal-power pan table (0.7071 at
centre).

Which channels feed the effect: ``VoiceOn`` sets a voice's effect bit to
``(channel reverb depth >> 6) & 1``, the depth being CC91 (``CcProcReverbDepth``),
so CC91 >= 64 sends a channel to the effect and anything lower keeps it dry.
The songs use 0 and 127 only.

ECHO and DELAY take their timing from the song: libsd (sceSdSetEffectAttr /
sceSdSetEffectModeParams in ``IOP\\LIBSD.IRX``) rewrites the ECHO/DELAY
template with D = (delay + 1) * 32 and vWALL = feedback * 258, so one repeat
every (128 * (delay + 1) - 24) / 24000 s (537.7 ms at the songs' usual delay
100), each repeat feedback * 258 / 32768 of the last, the return inverted. A
game rip of 001_milchia fits best at 538 ms. ``spu2synth.py`` renders the
songs with these settings.

    python songfx.py SONG.mid                       # print the song's effect settings
    python songfx.py --self-test
"""
from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import midi  # noqa: E402

MODES = ["OFF", "ROOM", "STUDIO_A", "STUDIO_B", "STUDIO_C", "HALL", "SPACE", "ECHO", "DELAY", "PIPE"]
PARAMS = {0: "vol", 1: "pan", 2: "delay", 3: "feedback"}
SEND_THRESHOLD = 64                  # VoiceOn: effect bit = (CC91 >> 6) & 1


def echo_seconds(delay: int) -> float:
    """libsd's ECHO/DELAY repeat time: the template's taps rewritten with
    D = (delay+1)*32 put one repeat 4*D - 24 reverb steps (24 kHz) apart."""
    return (128 * (delay + 1) - 24) / 24000.0


def echo_feedback(feedback: int) -> float:
    """vWALL = feedback * 258, as a fraction of full scale."""
    return feedback * 258 / 32768.0


@dataclass
class Fx:
    """One SPU2 core's effect settings, in the driver's units."""
    mode: int = 0
    vol: int = 0
    pan: int = 64
    delay: int = 0
    feedback: int = 0

    @property
    def name(self) -> str:
        return MODES[self.mode] if 0 <= self.mode < len(MODES) else f"mode{self.mode}"

    @property
    def level(self) -> float:
        """Return level (linear, centre pan), from the driver's volume table."""
        return (self.vol / 127.0) ** 2 * 0.7071

    @property
    def delay_seconds(self) -> float:
        return echo_seconds(self.delay)


@dataclass
class SongFx:
    cores: Dict[int, Fx] = field(default_factory=lambda: {0: Fx(), 1: Fx()})
    events: List[Tuple[int, int, str, int]] = field(default_factory=list)   # tick, core, param, value
    sends: Set[int] = field(default_factory=set)        # 0-based MIDI channels with CC91 >= 64

    @property
    def main(self) -> Fx:
        """The core the song uses (core 1 on every song on the disc)."""
        used = {c for _, c, _, _ in self.events}
        return self.cores[1 if 1 in used or not used else 0]


def song_fx(mid: bytes) -> SongFx:
    """Replay the song's controllers the way CcProcNrpn*/CcProcDataEntryMsb do."""
    out = SongFx()
    nrpn: Dict[int, List[Optional[int]]] = {}       # channel -> [msb, lsb]; None = not selected
    (_, _, _), trs = midi.tracks(mid)
    for t in trs:
        tick = 0
        for delta, st, payload, _ in midi._events(t):
            tick += delta
            if st >> 4 != 0xB:
                continue
            ch, cc, v = st & 15, payload[0], payload[1]
            sel = nrpn.setdefault(ch, [None, None])
            if cc == 99:
                sel[0] = v
            elif cc == 98:
                sel[1] = v
            elif cc in (100, 101):              # an RPN deselects the NRPN
                sel[0] = sel[1] = None
            elif cc == 91 and v >= SEND_THRESHOLD:
                out.sends.add(ch)
            elif cc == 6 and sel[0] in (2, 3) and sel[1] is not None:
                core = (sel[1] >> 4) & 1
                fx = out.cores[core]
                if sel[0] == 2:
                    fx.mode = max(0, min(9, v))
                    out.events.append((tick, core, "mode", fx.mode))
                else:
                    param = PARAMS.get(sel[1] & 0xF)
                    if param:
                        setattr(fx, param, v)
                        out.events.append((tick, core, param, v))
    return out


def describe(s: SongFx) -> str:
    fx = s.main
    extra = f", delay {fx.delay} (~{fx.delay_seconds * 1000:.0f} ms), feedback {fx.feedback}" \
        if fx.name in ("ECHO", "DELAY") else ""
    sends = ", ".join(str(c + 1) for c in sorted(s.sends)) or "none"
    return (f"{fx.name}: return {fx.vol}/127 (level {fx.level:.2f}), pan {fx.pan}{extra}; "
            f"channels sent to the effect: {sends}")


# ---------------------------------------------------------------- self-test

def _smf(events: List[Tuple[int, bytes]]) -> bytes:
    body = bytearray()
    for delta, ev in events:
        body += midi._write_vlq(delta) + ev
    body += b"\x00\xff\x2f\x00"
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(body)) + body


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    cc = lambda c, n, v: bytes([0xB0 | c, n, v])  # noqa: E731
    song = _smf([(0, cc(0, 99, 2)), (0, cc(0, 98, 16)), (0, cc(0, 6, 7)),       # FX1 MODE = ECHO
                 (3, cc(0, 99, 3)), (0, cc(0, 98, 16)), (0, cc(0, 6, 100)),      # FX1 VOL
                 (3, cc(0, 98, 18)), (0, cc(0, 6, 127)),                         # FX1 DELAY
                 (3, cc(0, 98, 19)), (0, cc(0, 6, 60)),                          # FX1 FEEDBACK
                 (0, cc(3, 91, 127)), (0, cc(4, 91, 0)), (0, cc(2, 91, 63)),
                 (0, cc(0, 101, 0)), (0, cc(0, 100, 0)), (0, cc(0, 6, 2)),       # RPN 0: not an FX write
                 (0, bytes([0x93, 60, 100])), (240, bytes([0x83, 60, 0])),
                 (0, bytes([0x94, 64, 100])), (240, bytes([0x84, 64, 0]))])
    s = song_fx(song)
    fx = s.main
    check(fx.name == "ECHO" and fx.vol == 100 and fx.delay == 127 and fx.feedback == 60,
          "NRPN 2/16 and 3/16..19 decode as the driver does (core 1)")
    check(s.sends == {3}, "only CC91 >= 64 sends a channel to the effect")
    check(abs(fx.level - (100 / 127) ** 2 * 0.7071) < 1e-9 and abs(fx.delay_seconds - 16360 / 24000) < 1e-9,
          "return level and libsd's delay scale (delay 127: 681.7 ms)")
    check(abs(echo_seconds(100) * 1000 - 537.67) < 0.01 and abs(echo_feedback(60) - 0.47241) < 1e-4,
          "delay 100 repeats every 537.7 ms; feedback 60 keeps 0.472 per repeat")
    s2 = song_fx(_smf([(0, cc(0, 99, 3)), (0, cc(0, 98, 16)), (0, cc(0, 6, 90))]))
    check(s2.main.name == "OFF" and s2.main.vol == 90, "a song that never sets a mode stays OFF")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if len(a) == 1:
        s = song_fx(open(a[0], "rb").read())
        print(describe(s))
        for tick, core, param, v in s.events:
            print(f"  tick {tick:6d} core {core} {param:9s} {v}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
