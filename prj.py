"""prj.py — Episode II's KORG projects (``.prj``): keys, programs, SoundFont.

A ``.prj`` sits next to every ``.vsf`` bank (same stem) and says how the
samples are played. Three chunks, always in this order (all 1,074 files)::

    "PRJp" u32 0x18  u32 key count  u32 7   name[16]            (0x20 bytes)
    "KEYp" u32 size  u32 count  u32 0x30   count x 0x30-byte key records
    "PRGp" u32 size  u32 count  u32 0x180  count x 0x180-byte programs

Key record (0x30) = two 16-byte layers, then name[16] (the sample's name):

    +0 u16 sample index into the .vsf      +2 u16 SPU ADSR1   +4 u16 SPU ADSR2
    +6 u8 volume (127)   +7 u8 pan (64 = centre)   +8 s8 transpose (semitones)
    +9 s8 fine tune, 1/64 semitone (1.5625 cents)
    +10 u8 key-follow (0xFF = pitch follows the key, 0 = fixed, e.g. groove loops)
    +11 u8 choke group (0 = none)   +12 u8 hold (non-zero: note-off is ignored)
    +13 u8 bit 0 = SPU2 core (1 = core 1, the songs' effect core)   +14 u16 0

What the driver does with them (``ChannelNoteOn``/``ChannelNoteOff``/
``ChannelCheckGroup`` in KORGIVAG.IRX, 2026-11-05): a layer sounds when its
sample index is non-zero (there is no on/off byte; +13 picks the core); a
note releases the channel's sounding voices of the same choke group; and a
hold layer skips the key-off at note-off, so the one-shot sample plays to its
end. 554 of the music keys hold, the drum and groove-slice kits among them,
which the songs strike with short notes (a 0.5 s cymbal slice under a
0.125 s note). ``spu2synth.py`` renders all of this exactly; the SoundFont
below can only approximate it (a long release for hold layers, the choke
group as the SF2 exclusive class).

The pitch rule is the driver's own (``VoiceOn`` in ``IOP\KORGIVAG.IRX``,
which ships with its symbol table): pitch, in 1/256 semitone, is
``(((note - sample root) & follow) + transpose + ...) << 8`` plus
``(layer fine - sample fine [+ channel fine when the pitch follows the key]) << 2``,
the ``... `` being the channel's coarse tune, also masked by key-follow. So
the transpose byte is semitones and the fine byte is 1/64 semitone, both
signed; a fixed-pitch layer still takes its transpose and fine.

The first layer's sample index matches the .vsf sample of the same name in
6,921 of 6,962 records (the rest are name typos in the project, e.g.
"CF_MID0_02.wav" for "CF_MID01_02.wav"). Instruments use the second layer
for stereo spreads: the same sample panned 36 and 92, slightly detuned.

Program (0x180): name[16], parameters (+0x10..+0x3F, not decoded), then
u16 key_map[128] at +0x40: MIDI key -> key record, 0xFFFF = silent.
"Default Program" maps keys one-to-one (a kit); instruments map all 128
keys to one record. A song's ``.mid`` (type 0, 480 PPQN) selects programs
with ordinary program changes, so program N of the project is preset N.

``to_sf2`` turns a project plus its bank into a SoundFont 2 file. Envelope
times come from the SPU ADSR words with the routines of the Episode I kit's
``ssd.py`` (copied below); volume, pan, transpose and fine tune map onto the
SF2 generators (fine tune in cents = fine * 100 / 64). The program parameters
(+0x10..+0x3F) are left out on evidence: of the 1,218 programs on the disc,
+0x13 is 127 in all, +0x18 is 0 in 1,195 and +0x19 is 20 in 1,214, and the
other 44 bytes are zero except in one program, so they cannot make two
programs sound different. The SPU effect the songs set from their MIDI
(reverb, echo) is decoded and rendered by ``songfx.py``.

    python prj.py --self-test
    python prj.py FILE.prj               # print programs and key maps
    python prj.py FILE.prj BANK.vsf OUT.sf2
"""
from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vsf

SPU_RATE = 48000


class PrjError(ValueError):
    pass


@dataclass
class Layer:
    sample: int
    adsr1: int
    adsr2: int
    volume: int
    pan: int
    transpose: int
    fine: int
    key_follow: bool
    raw: bytes

    @property
    def on(self) -> bool:
        """The driver plays a layer whenever it names a sample."""
        return self.sample != 0

    @property
    def group(self) -> int:
        return self.raw[11]

    @property
    def hold(self) -> bool:
        return self.raw[12] != 0

    @property
    def core(self) -> int:
        return self.raw[13] & 1


@dataclass
class Key:
    index: int
    name: str
    layers: List[Layer]


@dataclass
class Program:
    index: int
    name: str
    params: bytes
    key_map: Tuple[int, ...]


@dataclass
class Project:
    name: str
    keys: List[Key] = field(default_factory=list)
    programs: List[Program] = field(default_factory=list)


def _layer(b: bytes) -> Layer:
    s, a1, a2, vol, pan, tr, fine, kf = struct.unpack_from("<HHHBBbbB", b, 0)
    return Layer(s, a1, a2, vol, pan, tr, fine, kf == 0xFF, bytes(b[:16]))


def fine_cents(layer: "Layer", sample_fine: int = 0) -> int:
    """The layer's fine tune in cents: (layer fine - sample fine) in 1/64 semitone."""
    sf = sample_fine - 256 if sample_fine > 127 else sample_fine
    return round((layer.fine - sf) * 100 / 64)


def parse(data: bytes) -> Project:
    if data[:4] != b"PRJp":
        raise PrjError("not a PRJ (magic)")
    proj = Project(data[16:32].split(b"\x00")[0].decode("latin1"))
    p = 0x20
    if data[p:p + 4] != b"KEYp":
        raise PrjError("KEYp chunk missing")
    size, n, rs = struct.unpack_from("<III", data, p + 4)
    for i in range(n):
        r = data[p + 16 + i * rs:p + 16 + (i + 1) * rs]
        proj.keys.append(Key(i, r[32:48].split(b"\x00")[0].decode("latin1"),
                             [_layer(r[0:16]), _layer(r[16:32])]))
    p += 8 + size
    if data[p:p + 4] != b"PRGp":
        raise PrjError("PRGp chunk missing")
    size, n, rs = struct.unpack_from("<III", data, p + 4)
    for i in range(n):
        r = data[p + 16 + i * rs:p + 16 + (i + 1) * rs]
        proj.programs.append(Program(i, r[:16].split(b"\x00")[0].decode("latin1"),
                                     bytes(r[16:64]), struct.unpack_from("<128H", r, 64)))
    return proj


# ---------------------------------------------------------- SPU envelope
# (copied from Xenosaga1PythonExtractor/ssd.py, the nocash SPU2 model)

def _spu_env_step(shift: int, step_val: int) -> Tuple[int, int]:
    step = step_val << max(0, 11 - shift)
    ticks = 1 << max(0, shift - 11)
    return step, ticks


def spu_attack_seconds(adsr1: int) -> float:
    exp_mode = bool(adsr1 & 0x8000)
    shift = (adsr1 >> 10) & 0x1F
    step_sel = (adsr1 >> 8) & 3
    step, ticks = _spu_env_step(shift, 7 - step_sel)
    if step <= 0:
        return 10.0
    level, t = 0, 0
    while level < 0x7FFF and t < SPU_RATE * 10:
        s = step
        if exp_mode and level >= 0x6000:
            s = max(step >> 2, 1)
        level += s
        t += ticks
    return t / SPU_RATE


def spu_decay_seconds(adsr1: int) -> Tuple[float, float]:
    shift = (adsr1 >> 4) & 0xF
    sl = ((adsr1 & 0xF) + 1) / 16.0
    step, ticks = _spu_env_step(shift, 8)
    level, t = 0x7FFF, 0
    target = 0x7FFF * sl
    while level > target and t < SPU_RATE * 30:
        level -= max((step * level) >> 15, 1)
        t += ticks
    return t / SPU_RATE, sl


def spu_release_seconds(adsr2: int) -> float:
    exp_mode = bool(adsr2 & 0x20)
    shift = adsr2 & 0x1F
    step, ticks = _spu_env_step(shift, 8)
    level, t = 0x7FFF, 0
    while level > 0x100 and t < SPU_RATE * 30:
        level -= max((step * level) >> 15, 1) if exp_mode else step
        t += ticks
    return t / SPU_RATE


def _timecents(seconds: float) -> int:
    return max(-12000, min(8000, round(1200 * math.log2(max(seconds, 0.001)))))


# A SoundFont decay or release time is the time a full-scale envelope takes to
# fall 100 dB (linearly in dB). The SPU times above are the time to reach the
# sustain level (decay) and level 0x100 = -42.1 dB (release), so scale them.
_RELEASE_DB = -20 * math.log10(0x100 / 0x7FFF)


def sf2_release_seconds(adsr2: int) -> float:
    return spu_release_seconds(adsr2) * 100.0 / _RELEASE_DB


def sf2_decay_seconds(adsr1: int) -> Tuple[float, float]:
    dec, sl = spu_decay_seconds(adsr1)
    drop = -20 * math.log10(max(sl, 1e-4))
    return (dec * 100.0 / drop if drop > 0.01 else dec), sl


# -------------------------------------------------------------- SoundFont

def _runs(key_map, wanted: int) -> List[Tuple[int, int]]:
    out, start = [], None
    for k in range(129):
        hit = k < 128 and key_map[k] == wanted
        if hit and start is None:
            start = k
        elif not hit and start is not None:
            out.append((start, k - 1))
            start = None
    return out


def to_sf2(proj: Project, bank: bytes, name: str = "") -> bytes:
    samples = vsf.parse(bank)
    used: Dict[int, int] = {}           # vsf index -> sf2 sample id
    shdr, pcm_all = bytearray(), bytearray()
    smeta: Dict[int, Tuple[int, int, int, int, int, int]] = {}

    def add_sample(si: int) -> Optional[int]:
        if si in used:
            return used[si]
        if si >= len(samples) or samples[si].placeholder:
            return None
        s = samples[si]
        pcm, loop = vsf.decode(s)
        if not pcm:
            return None
        start = len(pcm_all) // 2
        pcm_all.extend(pcm.tobytes() if sys.byteorder == "little" else vsf._swapped(pcm))
        end = len(pcm_all) // 2
        pcm_all.extend(bytes(92))
        ls, le = (start + loop, end) if loop is not None else (start, end)
        sid = len(used)
        used[si] = sid
        nm = (s.name or f"smp{si}").encode("ascii", "replace")[:19]
        shdr.extend(nm.ljust(20, b"\x00") + struct.pack("<IIIIIBbHH", start, end, ls, le,
                                                         s.rate or 22050, s.root or 60, 0, 0, 1))
        smeta[si] = (start, end, ls, le, s.rate, 1 if loop is not None else 0)
        return sid

    def gen(op, amount):
        return struct.pack("<Hh", op, amount)

    def genu(op, lo, hi):
        return struct.pack("<HBB", op, lo, hi)

    inst, ibag, igen = bytearray(), bytearray(), bytearray()
    phdr, pbag, pgen = bytearray(), bytearray(), bytearray()
    ibag_n = igen_n = pbag_n = pgen_n = 0
    for prog in proj.programs:
        inst += prog.name.encode("ascii", "replace")[:19].ljust(20, b"\x00") + struct.pack("<H", ibag_n)
        for ki in sorted({k for k in prog.key_map if k != 0xFFFF}):
            if ki >= len(proj.keys):
                continue
            for layer in proj.keys[ki].layers:
                if not layer.on:
                    continue
                sid = add_sample(layer.sample)
                if sid is None:
                    continue
                smp = samples[layer.sample]
                for lo, hi in _runs(prog.key_map, ki):
                    g = bytearray(genu(43, lo, hi))
                    vol = max(layer.volume / 127.0, 1e-4)
                    g += gen(48, min(1440, round(-200 * math.log10(vol))))
                    if layer.pan != 64:
                        g += gen(17, round((layer.pan - 64) / 64 * 500))
                    g += gen(34, _timecents(spu_attack_seconds(layer.adsr1)))
                    dec, sl = sf2_decay_seconds(layer.adsr1)
                    if sl < 1.0:
                        g += gen(36, _timecents(dec))
                        g += gen(37, min(1440, round(-200 * math.log10(max(sl, 1e-4)))))
                    if layer.hold and not smeta[layer.sample][5]:
                        g += gen(38, 8000)                      # hold: a one-shot plays out
                    else:
                        g += gen(38, _timecents(sf2_release_seconds(layer.adsr2)))
                    if layer.group:
                        g += gen(57, layer.group)               # exclusiveClass ~ choke group
                    if layer.key_follow:
                        g += gen(58, max(0, min(127, (smp.root or 60) - layer.transpose)))
                    else:
                        g += gen(56, 0)                         # scaleTuning 0: fixed pitch
                        g += gen(58, lo)
                        if layer.transpose:
                            g += gen(51, layer.transpose)       # coarseTune: still transposed
                    cents = fine_cents(layer, getattr(smp, "fine", 0) or 0)
                    if cents:
                        g += gen(52, max(-99, min(99, cents)))  # fineTune
                    if smeta[layer.sample][5]:
                        g += gen(54, 1)                         # loop
                    g += gen(53, sid)
                    ibag += struct.pack("<HH", igen_n, 0)
                    ibag_n += 1
                    igen += g
                    igen_n += len(g) // 4
    inst += b"EOI".ljust(20, b"\x00") + struct.pack("<H", ibag_n)
    ibag += struct.pack("<HH", igen_n, 0)
    # every program twice: bank 0, and bank 128 so a General-MIDI player's
    # percussion channel (MIDI channel 10, which the KORG driver treats like
    # any other) finds the same instrument instead of a drum kit
    for bank_no in (0, 128):
        for n, prog in enumerate(proj.programs):
            phdr += prog.name.encode("ascii", "replace")[:19].ljust(20, b"\x00")
            phdr += struct.pack("<HHHIII", prog.index, bank_no, pbag_n, 0, 0, 0)
            pbag += struct.pack("<HH", pgen_n, 0)
            pbag_n += 1
            pgen += gen(41, n)
            pgen_n += 1
    phdr += b"EOP".ljust(20, b"\x00") + struct.pack("<HHHIII", 0, 0, pbag_n, 0, 0, 0)
    pbag += struct.pack("<HH", pgen_n, 0)
    shdr += b"EOS".ljust(20, b"\x00") + struct.pack("<IIIIIBbHH", 0, 0, 0, 0, 0, 0, 0, 0, 0)

    def sub(tag, payload):
        if len(payload) & 1:
            payload += b"\x00"
        return tag + struct.pack("<I", len(payload)) + payload

    info = sub(b"ifil", struct.pack("<HH", 2, 1)) + sub(b"isng", b"EMU8000\x00")
    info += sub(b"INAM", (name or proj.name).encode("ascii", "replace") + b"\x00")
    info += sub(b"ICMT", b"Converted from a Xenosaga Episode II KORG .prj/.vsf pair by prj.py\x00")
    info_list = b"LIST" + struct.pack("<I", 4 + len(info)) + b"INFO" + info
    sdta = sub(b"smpl", bytes(pcm_all))
    sdta_list = b"LIST" + struct.pack("<I", 4 + len(sdta)) + b"sdta" + sdta
    pdta = (sub(b"phdr", bytes(phdr)) + sub(b"pbag", bytes(pbag)) + sub(b"pmod", bytes(10))
            + sub(b"pgen", bytes(pgen) + bytes(4)) + sub(b"inst", bytes(inst))
            + sub(b"ibag", bytes(ibag)) + sub(b"imod", bytes(10))
            + sub(b"igen", bytes(igen) + bytes(4)) + sub(b"shdr", bytes(shdr)))
    pdta_list = b"LIST" + struct.pack("<I", 4 + len(pdta)) + b"pdta" + pdta
    body = b"sfbk" + info_list + sdta_list + pdta_list
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ------------------------------------------------------------------ self-test

def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    def layer(sample, on=1, follow=0xFF, pan=64):
        return struct.pack("<HHHBBbBBBBB", sample, 0x808F, 0xDFE6, 127, pan, 0, 0, follow, 0, 0, on) + bytes(2)

    keys = [layer(0, 2) + layer(0, 0) + b"-- no sample --\x00",
            layer(1) + layer(1, pan=92) + b"tone.wav".ljust(16, b"\x00")]
    prog = b"Lead".ljust(16, b"\x00") + bytes(48) + struct.pack("<128H", *([1] * 128)) + bytes(0x40)
    blob = (b"PRJp" + struct.pack("<III", 0x18, 2, 7) + b"test".ljust(16, b"\x00")
            + b"KEYp" + struct.pack("<III", 8 + 0x60, 2, 0x30) + b"".join(keys)
            + b"PRGp" + struct.pack("<III", 8 + 0x180, 1, 0x180) + prog)
    p = parse(blob)
    check(p.name == "test" and len(p.keys) == 2 and p.programs[0].name == "Lead", "chunks parse")
    check(p.keys[1].layers[1].pan == 92 and p.keys[1].layers[0].key_follow, "layer fields")
    check(not p.keys[0].layers[0].on and p.keys[1].layers[0].on, "a layer sounds when it names a sample")
    hl = _layer(struct.pack("<HHHBBbbBBBB", 3, 0x808F, 0xDFE6, 127, 64, 0, 0, 0, 5, 0xFF, 2) + bytes(2))
    check(hl.group == 5 and hl.hold and hl.core == 0 and hl.on, "choke group, hold and core bytes")
    r42 = spu_release_seconds(0xDFE6)
    check(abs(sf2_release_seconds(0xDFE6) - r42 * 100 / _RELEASE_DB) < 1e-12 and sf2_release_seconds(0xDFE6) > 2 * r42,
          "SF2 release = time to -100 dB at the SPU's rate")
    fl = _layer(struct.pack("<HHHBBbbBBBB", 1, 0x808F, 0xDFE6, 127, 64, -12, -10, 0xFF, 0, 0, 1) + bytes(2))
    check(fl.transpose == -12 and fl.fine == -10 and fine_cents(fl) == -16,
          "transpose and fine are signed; fine is 1/64 semitone")
    frame = bytes([0x0C, 0x00]) + bytes(range(1, 15))
    bank = (vsf.MAGIC + struct.pack("<IIIII", 0x10, 2, 96, 0, 0)
            + struct.pack("<IIHBBIII", 0, 0, 48000, 60, 0, 0, 0, 16) + vsf.PLACEHOLDER.ljust(16, b"\x00") + bytes(16)
            + struct.pack("<IIHBBIII", 0, 0, 22050, 60, 0, 0, 0, 32) + b"tone.wav".ljust(16, b"\x00")
            + frame + bytes([0x0C, 0x01]) + bytes(14))
    sf2 = to_sf2(p, bank)
    check(sf2[:4] == b"RIFF" and sf2[8:12] == b"sfbk" and b"phdr" in sf2 and b"Lead" in sf2,
          "a SoundFont 2 file comes out")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if len(a) == 1:
        p = parse(open(a[0], "rb").read())
        print(f"project {p.name}: {len(p.keys)} keys, {len(p.programs)} programs")
        for prog in p.programs:
            used = sorted({k for k in prog.key_map if k != 0xFFFF})
            print(f"  program {prog.index} {prog.name!r}: keys -> {used[:12]}{' ...' if len(used) > 12 else ''}")
        return 0
    if len(a) == 3:
        sf2 = to_sf2(parse(open(a[0], "rb").read()), open(a[1], "rb").read())
        open(a[2], "wb").write(sf2)
        print(f"wrote {a[2]} ({len(sf2)} bytes)")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
