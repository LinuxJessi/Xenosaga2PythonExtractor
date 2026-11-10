"""spu2synth.py — Episode II's field music rendered the way the PS2 plays it.

The 31 field/menu songs are MIDI files that KORG's IOP driver
(``IOP\\KORGIVAG.IRX``, shipped with its symbol table) plays on the SPU2.
This module models that path from the driver's own code and the disc's own
data, instead of handing the song to a General-MIDI synth:

Driver rules (read from KORGIVAG.IRX, 2026-11-05)
* Key layers: a layer sounds when its sample index is non-zero
  (``ChannelNoteOn``); byte +13 bit 0 picks the SPU2 core; byte +11 is a
  choke group (``ChannelCheckGroup``: a new note of group G releases the
  channel's sounding voices of group G); byte +12 non-zero makes the layer
  ignore note-off (``ChannelNoteOff`` skips ``VoiceNoteOff``), so the sample
  plays to its end. 554 of the ~800 music key records hold, among them every
  drum and groove-slice key of the kits.
* Voices: 24 per core; a note takes the first free voice of its core, else
  the oldest one (``GetLowestPriorityVoice`` + ``ForceVoiceOff``).
* Pitch (``VoiceOn``, ``PitchCalc``): 1/256-semitone note pitch plus
  channel bend ``(range * bend14) >> 5`` (RPN 0, clamped 0-24, default 2)
  plus the pitch LFO; register = ``NOTE[p >> 8] * base >> 15 * FINE[p & 0xFE] >> 15``
  with the driver's equal-tempered tables and the sample's base pitch from
  its .vsf record (rate * 4096 / 48000); clamped to 0x3FFF.
* LFO (``PitchLfoProc``, every 2 ms tick): phase += table[CC27] (= CC27²),
  depth = channel scale (2) * CC1 * 2, waveform from program byte +0x18.
* Volume (``VoiceOn``, ``DynamicVolumeExp``): pow2 curve (32767 (x/127)²)
  of velocity, layer volume and program volume times pow2 of channel volume
  (CC7) and expression (CC11); left/right through the driver's equal-power
  pan table (``s_aPanCurve``), pan = layer pan + CC10 - 64.
* Effect send: CC91 >= 64 at note-on sets the voice's reverb bit; the return
  level is pow2(FX VOL) through the pan table (``InternalSetFxVolume``).

SPU2 rules (psx-spx / nocash and PCSX2's SPU2, re-implemented here)
* ADPCM samples, 4-point Gaussian interpolation (the SPU's 512-entry table),
  pitch counter in 1/4096 sample steps at 48 kHz.
* ADSR envelope generator stepped exactly per sample (counter, shift, step,
  exponential modes) as psx-spx documents it.
* Reverb: the SPU reverb network (same/different-side reflections, four comb
  taps, two all-pass filters) running at 24 kHz, left on even and right on odd
  output samples, with libsd's own presets from the disc's ``IOP\\LIBSD.IRX``
  (addresses x4 into SPU2 words, ECHO/DELAY taps and wall volume rewritten
  from the song's delay and feedback exactly as libsd's sceSdSetEffectAttr
  does). The 39-tap half-band resampling filters are designed here, not
  copied, so the reverb's top octave can differ slightly from hardware.

Not modelled: portamento (CC5, a few songs), the pitch envelope (program
parameters, constant across nearly all programs), the driver's volume sweeps
(changes are ramped over one 2 ms tick instead), MIDI loop markers (songs play
once and ring out).

    python spu2synth.py SONG.mid SONG.prj SONG.vsf OUT.wav [--seconds S]
    python spu2synth.py --self-test
"""
from __future__ import annotations

import math
import os
import struct
import sys
import wave
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import midi  # noqa: E402
import prj  # noqa: E402
import vsf  # noqa: E402

SR = 48000
TICK = 96                      # the driver's 2 ms timer at 48 kHz
VOICES_PER_CORE = 24

# ------------------------------------------------------------ driver tables
POW2 = [round(32767 * (i / 127) ** 2) for i in range(128)]          # s_aPow2Curve (checked: identical)
PAN = [round(32767 * math.cos(math.pi / 2 * i / 127)) for i in range(128)]  # placeholder, see _load_pan
LFO_INC = [i * i for i in range(128)]                                 # CcProcLfoFreq table (i^2)


def _pan_table() -> List[int]:
    """s_aPanCurve as the driver ships it: entries 0..127 give the left gain
    for pan 0..127, entries 128..255 the right gain. Equal power, 32767 at the
    hard side and 23169 (0.7071) at centre; entry 64 = entry 192 = 23169."""
    left = [round(32767 * math.cos(math.pi / 2 * i / 127)) for i in range(128)]
    right = [round(32767 * math.sin(math.pi / 2 * i / 127)) for i in range(128)]
    return left + right


PAN_TABLE = _pan_table()
NOTE_PITCH = [round(32768 * 2 ** ((i - 128) / 12)) for i in range(256)]   # s_aNotePitch (Q15)
FINE_PITCH = [round(32768 * 2 ** (i / (128 * 12))) for i in range(128)]   # fine table, 128 steps/semitone


def load_driver_tables(irx: bytes) -> None:
    """Replace the formula tables with the exact ones from KORGIVAG.IRX."""
    global POW2, PAN_TABLE, NOTE_PITCH, FINE_PITCH, LFO_INC
    data_off, data_va = 0xC530, 0xC480

    def at(va: int, n: int) -> bytes:
        return irx[data_off + va - data_va:data_off + va - data_va + n]

    POW2 = list(struct.unpack("<128h", at(0xC990, 256)))
    PAN_TABLE = list(struct.unpack("<256h", at(0xCA90, 512)))
    NOTE_PITCH = list(struct.unpack("<256I", at(0xCC90, 1024)))
    FINE_PITCH = list(struct.unpack("<128H", at(0xD090, 256)))
    LFO_INC = list(struct.unpack("<128H", at(0xD290, 256)))


# ------------------------------------------------------- SPU interpolation
# The SPU's Gaussian interpolation table (psx-spx, "4-Point Gaussian
# Interpolation"); hardware constants.
_GAUSS_HEX = """
-001 -001 -001 -001 -001 -001 -001 -001 -001 -001 -001 -001 -001 -001 -001 -001
0000 0000 0000 0000 0000 0000 0000 0001 0001 0001 0001 0002 0002 0002 0003 0003
0003 0004 0004 0005 0005 0006 0007 0007 0008 0009 0009 000A 000B 000C 000D 000E
000F 0010 0011 0012 0013 0015 0016 0018 0019 001B 001C 001E 0020 0021 0023 0025
0027 0029 002C 002E 0030 0033 0035 0038 003A 003D 0040 0043 0046 0049 004D 0050
0054 0057 005B 005F 0063 0067 006B 006F 0074 0078 007D 0082 0087 008C 0091 0096
009C 00A1 00A7 00AD 00B3 00BA 00C0 00C7 00CD 00D4 00DB 00E3 00EA 00F2 00FA 0101
010A 0112 011B 0123 012C 0135 013F 0148 0152 015C 0166 0171 017B 0186 0191 019C
01A8 01B4 01C0 01CC 01D9 01E5 01F2 0200 020D 021B 0229 0237 0246 0255 0264 0273
0283 0293 02A3 02B4 02C4 02D6 02E7 02F9 030B 031D 0330 0343 0356 036A 037E 0392
03A7 03BC 03D1 03E7 03FC 0413 042A 0441 0458 0470 0488 04A0 04B9 04D2 04EC 0506
0520 053B 0556 0572 058E 05AA 05C7 05E4 0601 061F 063E 065C 067C 069B 06BB 06DC
06FD 071E 0740 0762 0784 07A7 07CB 07EF 0813 0838 085D 0883 08A9 08D0 08F7 091E
0946 096F 0998 09C1 09EB 0A16 0A40 0A6C 0A98 0AC4 0AF1 0B1E 0B4C 0B7A 0BA9 0BD8
0C07 0C38 0C68 0C99 0CCB 0CFD 0D30 0D63 0D97 0DCB 0E00 0E35 0E6B 0EA1 0ED7 0F0F
0F46 0F7F 0FB7 0FF1 102A 1065 109F 10DB 1116 1153 118F 11CD 120B 1249 1288 12C7
1307 1347 1388 13C9 140B 144D 1490 14D4 1517 155C 15A0 15E6 162C 1672 16B9 1700
1747 1790 17D8 1821 186B 18B5 1900 194B 1996 19E2 1A2E 1A7B 1AC8 1B16 1B64 1BB3
1C02 1C51 1CA1 1CF1 1D42 1D93 1DE5 1E37 1E89 1EDC 1F2F 1F82 1FD6 202A 207F 20D4
2129 217F 21D5 222C 2282 22DA 2331 2389 23E1 2439 2492 24EB 2545 259E 25F8 2653
26AD 2708 2763 27BE 281A 2876 28D2 292E 298B 29E7 2A44 2AA1 2AFF 2B5C 2BBA 2C18
2C76 2CD4 2D33 2D91 2DF0 2E4F 2EAE 2F0D 2F6C 2FCC 302B 308B 30EA 314A 31AA 3209
3269 32C9 3329 3389 33E9 3449 34A9 3509 3569 35C9 3629 3689 36E8 3748 37A8 3807
3867 38C6 3926 3985 39E4 3A43 3AA2 3B00 3B5F 3BBD 3C1B 3C79 3CD7 3D35 3D92 3DEF
3E4C 3EA9 3F05 3F62 3FBD 4019 4074 40D0 412A 4185 41DF 4239 4292 42EB 4344 439C
43F4 444C 44A3 44FA 4550 45A6 45FC 4651 46A6 46FA 474E 47A1 47F4 4846 4898 48E9
493A 498A 49D9 4A29 4A77 4AC5 4B13 4B5F 4BAC 4BF7 4C42 4C8D 4CD7 4D20 4D68 4DB0
4DF7 4E3E 4E84 4EC9 4F0E 4F52 4F95 4FD7 5019 505A 509A 50DA 5118 5156 5194 51D0
520C 5247 5281 52BA 52F3 532A 5361 5397 53CC 5401 5434 5467 5499 54CA 54FA 5529
5558 5585 55B2 55DE 5609 5632 565B 5684 56AB 56D1 56F6 571B 573E 5761 5782 57A3
57C3 57E2 57FF 581C 5838 5853 586D 5886 589E 58B5 58CB 58E0 58F4 5907 5919 592A
593A 5949 5958 5965 5971 597C 5986 598F 5997 599E 59A4 59A9 59AD 59B0 59B2 59B3
"""
GAUSS = np.array([int(t, 16) for t in _GAUSS_HEX.split()], dtype=np.float64)
assert len(GAUSS) == 512
_G = (GAUSS[0xFF - np.arange(256)], GAUSS[0x1FF - np.arange(256)],
      GAUSS[0x100 + np.arange(256)], GAUSS[np.arange(256)])

# ------------------------------------------------------------ ADSR (exact)


def _env_params(shift: int, step_val: int, exp: bool, dec: bool, level: int,
                never_mask: Optional[int]) -> Tuple[int, int]:
    step = 7 - step_val
    if dec:
        step = ~step                       # +7..+4 -> -8..-5
    step = step << max(0, 11 - shift)
    inc = 0x8000 >> max(0, shift - 11)
    if exp and not dec and level > 0x6000:
        if shift < 10:
            step >>= 2
        elif shift >= 11:
            inc >>= 2
        else:
            step >>= 1
            inc >>= 1
    elif exp and dec:
        step = (step * level) >> 15        # floors, so a decreasing level still reaches 0
    if never_mask is None or ((step_val | (shift << 2)) != never_mask):
        inc = max(inc, 1)
    return step, inc


_ADS_CACHE: Dict[Tuple[int, int], np.ndarray] = {}
_REL_CACHE: Dict[int, np.ndarray] = {}


def ads_curve(adsr1: int, adsr2: int, n: int) -> np.ndarray:
    """Envelope level (0..0x7FFF) per output sample from key-on: attack,
    decay to the sustain level, then the sustain slope, for n samples."""
    key = (adsr1, adsr2 & 0xFFC0)
    got = _ADS_CACHE.get(key)
    if got is not None and len(got) >= n:
        return got[:n]
    a_exp, a_shift, a_step = bool(adsr1 & 0x8000), (adsr1 >> 10) & 0x1F, (adsr1 >> 8) & 3
    d_shift, sl = (adsr1 >> 4) & 0xF, ((adsr1 & 0xF) + 1) * 0x800
    s_exp, s_dec = bool(adsr2 & 0x8000), bool(adsr2 & 0x4000)
    s_shift, s_step = (adsr2 >> 8) & 0x1F, (adsr2 >> 6) & 3
    n_alloc = max(n, 48000)
    out = np.empty(n_alloc, dtype=np.int32)
    level, counter, phase = 0, 0, 0       # 0 attack, 1 decay, 2 sustain
    for i in range(n_alloc):
        if phase == 0:
            step, inc = _env_params(a_shift, a_step, a_exp, False, level, 0x7F)
        elif phase == 1:
            step, inc = _env_params(d_shift, 0, True, True, level, None)
        else:
            step, inc = _env_params(s_shift, s_step, s_exp, s_dec, level, 0x7F)
        counter += inc
        if counter & 0x8000:
            counter = 0
            level += step
            if phase == 0:
                if level >= 0x7FFF:
                    level, phase = 0x7FFF, 1
            elif phase == 1:
                level = max(level, 0)
                if level <= sl:
                    phase = 2
            else:
                level = max(0, min(0x7FFF, level))
        out[i] = level
    _ADS_CACHE[key] = out
    return out[:n]


def release_curve(adsr2: int) -> np.ndarray:
    """Release from full level to silence (the trajectory from any lower
    level is the tail of this one)."""
    key = adsr2 & 0x3F
    got = _REL_CACHE.get(key)
    if got is not None:
        return got
    r_exp, r_shift = bool(adsr2 & 0x20), adsr2 & 0x1F
    level, counter, vals = 0x7FFF, 0, []
    limit = SR * 60
    while level > 0 and len(vals) < limit:
        step, inc = _env_params(r_shift, 0, r_exp, True, level, 0x7C)
        counter += inc
        if counter & 0x8000:
            counter = 0
            level = max(0, level + step)
        vals.append(level)
        if inc == 0:            # release shift 0x1F: never steps
            break
    arr = np.array(vals or [0], dtype=np.int32)
    _REL_CACHE[key] = arr
    return arr


def envelope(adsr1: int, adsr2: int, keyoff: Optional[int], n_max: int) -> np.ndarray:
    """Full envelope from key-on; key-off at sample ``keyoff`` (None = never);
    ends where the level reaches 0 or at n_max."""
    if keyoff is None or keyoff >= n_max:
        return ads_curve(adsr1, adsr2, n_max)
    head = ads_curve(adsr1, adsr2, max(keyoff, 1))[:keyoff]
    lvl = int(head[-1]) if keyoff else 0
    rel = release_curve(adsr2)
    j = int(np.searchsorted(-rel, -lvl, side="left"))     # first index with rel <= lvl
    tail = rel[j:]
    out = np.concatenate([head, tail])
    return out[:n_max]


# ------------------------------------------------------------- the reverb
# libsd's mode numbering (SD_REV_MODE_*) -> preset index in LIBSD.IRX .data
MODES = ["OFF", "ROOM", "STUDIO_A", "STUDIO_B", "STUDIO_C", "HALL", "SPACE", "ECHO", "DELAY", "PIPE"]
REG_NAMES = ["dAPF1", "dAPF2", "vIIR", "vCOMB1", "vCOMB2", "vCOMB3", "vCOMB4", "vWALL",
             "vAPF1", "vAPF2", "mLSAME", "mRSAME", "mLCOMB1", "mRCOMB1", "mLCOMB2", "mRCOMB2",
             "dLSAME", "dRSAME", "mLDIFF", "mRDIFF", "mLCOMB3", "mRCOMB3", "mLCOMB4", "mRCOMB4",
             "dLDIFF", "dRDIFF", "mLAPF1", "mRAPF1", "mLAPF2", "mRAPF2", "vLIN", "vRIN"]


def libsd_presets(libsd: bytes) -> Tuple[List[List[int]], List[int]]:
    """(32 register halfwords per mode, work-area size per mode in 8-byte
    units) from LIBSD.IRX 2.7.1.0: presets at .data 0x4A90 + mode*0x44 (u32
    write mask, then the registers), sizes at 0x4A68 + mode*4."""
    data_off, data_va = 0x4AE0, 0x4A30
    presets, sizes = [], []
    for m in range(10):
        o = data_off + 0x4A90 - data_va + m * 0x44 + 4
        presets.append(list(struct.unpack_from("<32H", libsd, o)))
        sizes.append(struct.unpack_from("<I", libsd, data_off + 0x4A68 - data_va + m * 4)[0])
    return presets, sizes


def effect_registers(preset: List[int], mode: int, delay: int, feedback: int) -> List[int]:
    """sceSdSetEffectAttr's rewrite for ECHO and DELAY (LIBSD.IRX code at
    0xE34..0xF5C): D = (delay+1)*32; mLSAME = 2D - dAPF1, mRSAME = D - dAPF2,
    dLSAME = dRSAME + D, mLCOMB1 = mRCOMB1 + D, mLAPF1 = mLAPF2 + D,
    mRAPF1 = mRAPF2 + D; vWALL = feedback * 258."""
    r = list(preset)
    if mode in (7, 8):
        d = (delay + 1) * 32
        idx = {n: i for i, n in enumerate(REG_NAMES)}
        r[idx["mLSAME"]] = (2 * d - r[idx["dAPF1"]]) & 0xFFFF
        r[idx["mRSAME"]] = (d - r[idx["dAPF2"]]) & 0xFFFF
        r[idx["dLSAME"]] = (r[idx["dRSAME"]] + d) & 0xFFFF
        r[idx["mLCOMB1"]] = (r[idx["mRCOMB1"]] + d) & 0xFFFF
        r[idx["mLAPF1"]] = (r[idx["mLAPF2"]] + d) & 0xFFFF
        r[idx["mRAPF1"]] = (r[idx["mRAPF2"]] + d) & 0xFFFF
        r[idx["vWALL"]] = (feedback * 258) & 0xFFFF
    return r


def _s16(v: int) -> float:
    v &= 0xFFFF
    return float(v - 0x10000 if v & 0x8000 else v) / 32768.0


def halfband(taps: int = 39) -> np.ndarray:
    """A windowed-sinc low-pass at a quarter of 48 kHz (12 kHz), unit DC gain."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(n / 2) / 2 * np.blackman(taps)
    return h / h.sum()


def _scan_iir(u: np.ndarray, c: float, y_prev: float) -> np.ndarray:
    """y[n] = u[n] + c*y[n-1], y[-1] = y_prev, by doubling (stable, vectorised)."""
    y = u.astype(np.float64).copy()
    y[0] += c * y_prev
    if c == 0.0:
        return y
    step, ck = 1, c
    n = len(y)
    while step < n:
        y[step:] = y[step:] + ck * y[:-step]
        ck = ck * ck
        step *= 2
    return y


class SpuReverb:
    """The SPU reverb network, run as delay-line equations: every buffer read
    resolves to the lane that last wrote that address and how many 24 kHz
    steps ago, so the circular buffer never has to be simulated word by word.

    Timing follows the hardware as PCSX2 models it: the buffer position moves
    one word every two output samples; the left side runs on even samples and
    the right side on odd ones at the same position; within one side's cycle
    every read happens before any write. ``reverb_direct`` is the word-by-word
    version the self-test compares against."""

    ORDER = ["SAME_L", "DIFF_L", "APF1_L", "APF2_L", "SAME_R", "DIFF_R", "APF1_R", "APF2_R"]

    def __init__(self, regs: List[int], size_units: int):
        g = {n: regs[i] for i, n in enumerate(REG_NAMES)}
        self.N = max(8, size_units * 4)                 # work area in SPU2 words
        a = lambda n: (g[n] * 4) % self.N               # noqa: E731  addresses in words
        self.v = {n: _s16(g[n]) for n in REG_NAMES if n.startswith("v")}
        self.dapf = (g["dAPF1"] * 4, g["dAPF2"] * 4)
        self.write = {"SAME_L": a("mLSAME"), "DIFF_L": a("mLDIFF"), "APF1_L": a("mLAPF1"),
                      "APF2_L": a("mLAPF2"), "SAME_R": a("mRSAME"), "DIFF_R": a("mRDIFF"),
                      "APF1_R": a("mRAPF1"), "APF2_R": a("mRAPF2")}
        self.src = {
            "SAME_L": a("dLSAME"), "SAME_R": a("dRSAME"),
            "DIFF_L": a("dRDIFF"), "DIFF_R": a("dLDIFF"),
            "COMB_L": [a("mLCOMB1"), a("mLCOMB2"), a("mLCOMB3"), a("mLCOMB4")],
            "COMB_R": [a("mRCOMB1"), a("mRCOMB2"), a("mRCOMB3"), a("mRCOMB4")],
            "APF1_L": (a("mLAPF1") - self.dapf[0]) % self.N, "APF1_R": (a("mRAPF1") - self.dapf[0]) % self.N,
            "APF2_L": (a("mLAPF2") - self.dapf[1]) % self.N, "APF2_R": (a("mRAPF2") - self.dapf[1]) % self.N,
        }
        self.block = 0

    def resolve(self, addr: int, side: str) -> Tuple[str, int]:
        """(lane, steps back) of the word at ``addr`` (relative to the buffer
        position) as the ``side`` ("L"/"R") cycle reads it. A write made in the
        same step is visible only from the left cycle to the right one; when
        two lanes wrote the word equally recently, the later write won."""
        best = None
        for lane in self.ORDER:
            d = (self.write[lane] - addr) % self.N
            if d == 0 and not (lane.endswith("_L") and side == "R"):
                d = self.N
            if best is None or d <= best[1]:
                best = (lane, d)
        return best

    def process(self, wet: np.ndarray, resample: bool = True) -> np.ndarray:
        """wet: (n, 2) float input at 48 kHz (16-bit scale) -> reverb output
        (n, 2) at 48 kHz. With ``resample=False`` the input is already the
        (steps, 2) 24 kHz signal and the 24 kHz output comes back."""
        if resample:
            n = len(wet)
            if n < 4:
                return np.zeros_like(wet)
            h = halfband()
            xl = np.convolve(wet[:, 0], h, mode="same")
            xr = np.convolve(wet[:, 1], h, mode="same")
            steps = n // 2
            in_l, in_r = xl[0:2 * steps:2], xr[1:2 * steps:2]
        else:
            steps = len(wet)
            in_l, in_r = wet[:, 0].astype(np.float64), wet[:, 1].astype(np.float64)
        v = self.v
        inp = {"L": v["vLIN"] * in_l, "R": v["vRIN"] * in_r}
        order = {lane: i for i, lane in enumerate(self.ORDER)}
        comb_v = [v["vCOMB1"], v["vCOMB2"], v["vCOMB3"], v["vCOMB4"]]
        reads = {}                                     # (consumer, what) -> (lane, delta)
        for side in ("L", "R"):
            for kind in ("SAME", "DIFF"):
                lane = f"{kind}_{side}"
                reads[(lane, "src")] = self.resolve(self.src[lane], side)
                reads[(lane, "prv")] = self.resolve((self.write[lane] - 1) % self.N, side)
            for k, ad in enumerate(self.src["COMB_" + side]):
                reads[("COMB_" + side, k)] = self.resolve(ad, side)
            for apf in ("APF1", "APF2"):
                reads[(f"{apf}_{side}", "src")] = self.resolve(self.src[f"{apf}_{side}"], side)
        # which lanes reach the output at all (some presets' lanes feed only
        # themselves): the rest are skipped
        deps = {}
        for side in ("L", "R"):
            for kind in ("SAME", "DIFF"):
                lane = f"{kind}_{side}"
                deps[lane] = {reads[(lane, "prv")][0]} | ({reads[(lane, "src")][0]} if v["vWALL"] else set())
            combs = {reads[(f"COMB_{side}", k)][0] for k in range(4) if comb_v[k]}
            a1 = {reads[(f"APF1_{side}", "src")][0]}
            a2 = {reads[(f"APF2_{side}", "src")][0]}
            deps[f"APF1_{side}"] = combs | (a1 if v["vAPF1"] else set())
            deps[f"APF2_{side}"] = combs | a1 | ({f"APF1_{side}"} if v["vAPF1"] else set()) | \
                (a2 if v["vAPF2"] else set())
            deps[f"OUT_{side}"] = deps[f"APF2_{side}"] | a2 | ({f"APF2_{side}"} if v["vAPF2"] else set())
        needed, todo = set(), ["OUT_L", "OUT_R"]
        while todo:
            for y in deps.get(todo.pop(), ()):
                if y not in needed:
                    needed.add(y)
                    todo.append(y)
        self.needed = needed
        # block length: a stage's reads of lanes not yet computed for the
        # current block must reach back past the block's start
        need = []
        for (cons, what), (lane, d) in reads.items():
            cons_lane = ("APF1_" + cons[-1]) if cons.startswith("COMB") else cons
            if cons_lane not in needed or lane not in needed:
                continue
            if cons.startswith("COMB") and not comb_v[what]:
                continue
            if what == "src" and cons.startswith(("SAME", "DIFF")) and not v["vWALL"]:
                continue
            if what == "prv" and lane == cons and d == 1:
                continue                    # the IIR's own last output: solved in closed form
            if what == "src" and lane == cons and cons.startswith("APF") and not v["v" + cons[:4]]:
                continue                    # all-pass gain 0: a plain delay, written before it is read
            if order[lane] < order[cons_lane] and d < self.N:
                continue                    # computed earlier in the same block
            need.append(d)
        B = max(1, min([steps] + need))
        hist = {lane: np.zeros(steps, dtype=np.float64) for lane in self.ORDER}
        out = {"L": np.zeros(steps), "R": np.zeros(steps)}

        def get(lane: str, d: int, k0: int, k1: int) -> np.ndarray:
            idx = np.arange(k0, k1) - d
            vals = np.zeros(k1 - k0)
            ok = idx >= 0
            vals[ok] = hist[lane][idx[ok]]
            return vals

        k0 = 0
        while k0 < steps:
            k1 = min(steps, k0 + B)
            for side in ("L", "R"):
                for kind in ("SAME", "DIFF"):
                    lane = f"{kind}_{side}"
                    if lane not in needed:
                        continue
                    s_lane, s_d = reads[(lane, "src")]
                    p_lane, p_d = reads[(lane, "prv")]
                    x = inp[side][k0:k1] + (v["vWALL"] * get(s_lane, s_d, k0, k1) if v["vWALL"] else 0.0)
                    if p_lane == lane and p_d == 1:
                        a_ = v["vIIR"]
                        y_prev = hist[lane][k0 - 1] if k0 > 0 else 0.0
                        y = _scan_iir(a_ * x, 1.0 - a_, y_prev)
                    else:
                        prv = get(p_lane, p_d, k0, k1)
                        y = v["vIIR"] * (x - prv) + prv
                    hist[lane][k0:k1] = np.clip(y, -32768, 32767)
                acc = np.zeros(k1 - k0)
                for k in range(4):
                    if comb_v[k]:
                        c_lane, c_d = reads[(f"COMB_{side}", k)]
                        acc += comb_v[k] * get(c_lane, c_d, k0, k1)
                a1_lane, a1_d = reads[(f"APF1_{side}", "src")]
                if v["vAPF1"]:
                    s1 = get(a1_lane, a1_d, k0, k1)
                    apf1 = acc - v["vAPF1"] * s1
                    hist[f"APF1_{side}"][k0:k1] = np.clip(apf1, -32768, 32767)
                    o1 = s1 + v["vAPF1"] * apf1
                else:
                    hist[f"APF1_{side}"][k0:k1] = np.clip(acc, -32768, 32767)
                    o1 = get(a1_lane, a1_d, k0, k1)
                a2_lane, a2_d = reads[(f"APF2_{side}", "src")]
                if v["vAPF2"]:
                    s2 = get(a2_lane, a2_d, k0, k1)
                    apf2 = o1 - v["vAPF2"] * s2
                    hist[f"APF2_{side}"][k0:k1] = np.clip(apf2, -32768, 32767)
                    res = s2 + v["vAPF2"] * apf2
                else:
                    hist[f"APF2_{side}"][k0:k1] = np.clip(o1, -32768, 32767)
                    res = get(a2_lane, a2_d, k0, k1)
                out[side][k0:k1] = np.clip(res, -32768, 32767)
            k0 = k1
        self.block = B
        if not resample:
            return np.stack([out["L"], out["R"]], axis=1)
        h = halfband()
        up = np.zeros((n, 2))
        up[0:2 * steps:2, 0] = out["L"]
        up[1:2 * steps:2, 1] = out["R"]
        up[:, 0] = np.convolve(up[:, 0], h * 2, mode="same")
        up[:, 1] = np.convolve(up[:, 1], h * 2, mode="same")
        return up


def reverb_direct(regs: List[int], size_units: int, x: np.ndarray) -> np.ndarray:
    """The same reverb word by word on a simulated work area (slow; for the
    self-test). x: (steps, 2) 24 kHz input -> (steps, 2) output."""
    g = {n: regs[i] for i, n in enumerate(REG_NAMES)}
    N = max(8, size_units * 4)
    v = {n: _s16(g[n]) for n in REG_NAMES if n.startswith("v")}
    A = {n: g[n] * 4 for n in REG_NAMES if not n.startswith("v")}
    mem = [0.0] * N
    out = np.zeros((len(x), 2))

    def cl(q: float) -> float:
        return -32768.0 if q < -32768.0 else 32767.0 if q > 32767.0 else q

    for k in range(len(x)):
        for si, (s, o) in enumerate((("L", "R"), ("R", "L"))):
            def rd(a: int) -> float:
                return mem[(k + a) % N]
            xin = (v["vLIN"] if s == "L" else v["vRIN"]) * float(x[k, si])
            sp, dp = rd(A[f"m{s}SAME"] - 1), rd(A[f"m{s}DIFF"] - 1)
            same = v["vIIR"] * (xin + v["vWALL"] * rd(A[f"d{s}SAME"]) - sp) + sp
            diff = v["vIIR"] * (xin + v["vWALL"] * rd(A[f"d{o}DIFF"]) - dp) + dp
            acc = sum(v[f"vCOMB{j}"] * rd(A[f"m{s}COMB{j}"]) for j in (1, 2, 3, 4))
            s1 = rd(A[f"m{s}APF1"] - A["dAPF1"])
            apf1 = acc - v["vAPF1"] * s1
            o1 = s1 + v["vAPF1"] * apf1
            s2 = rd(A[f"m{s}APF2"] - A["dAPF2"])
            apf2 = o1 - v["vAPF2"] * s2
            out[k, si] = cl(s2 + v["vAPF2"] * apf2)
            for name, val in (("SAME", same), ("DIFF", diff), ("APF1", apf1), ("APF2", apf2)):
                mem[(k + A[f"m{s}{name}"]) % N] = cl(val)
    return out


# --------------------------------------------------------------- the song

@dataclass
class Chan:
    program: int = 0
    vol: int = 127
    expr: int = 127
    pan: int = 0                   # CC10 - 64
    mod: int = 0
    lfo: int = 0                   # CC27
    bend: int = 0                  # -8192..8191
    bend_range: int = 2
    coarse: int = 0
    fine: int = 0
    rev: int = 0                   # CC91
    rpn: Tuple[Optional[int], Optional[int]] = (None, None)
    nrpn: bool = False


@dataclass
class Voice:
    core: int
    slot: int
    channel: int
    key: int
    velocity: int
    layer: "prj.Layer"
    sample: "vsf.Sample"
    program: int
    start: int
    keyoff: Optional[int] = None
    end: Optional[int] = None
    send: bool = False
    hold: bool = False
    group: int = 0
    age: int = 0


def _tempo_events(trs) -> List[Tuple[int, int]]:
    ev = []
    for t in trs:
        tick = 0
        for d, st, pl, _ in midi._events(t):
            tick += d
            if st == 0xFF and pl[:1] == b"\x51":
                ev.append((tick, int.from_bytes(pl[1:4], "big")))
    ev.sort()
    return ev or [(0, 500000)]


def _tick_to_sample(ticks: np.ndarray, tempo: List[Tuple[int, int]], ppqn: int) -> np.ndarray:
    out = np.zeros(len(ticks), dtype=np.float64)
    bounds = [t for t, _ in tempo] + [1 << 62]
    base_s = 0.0
    tick_prev, us_prev = 0, 500000
    seg_start = []
    for t, us in tempo:
        base_s += (t - tick_prev) * us_prev / 1e6 / ppqn
        seg_start.append((t, base_s, us))
        tick_prev, us_prev = t, us
    if seg_start[0][0] != 0:
        seg_start.insert(0, (0, 0.0, 500000))
    starts = np.array([s[0] for s in seg_start])
    idx = np.searchsorted(starts, ticks, side="right") - 1
    for i, (t0, s0, us) in enumerate(seg_start):
        m = idx == i
        out[m] = s0 + (ticks[m] - t0) * us / 1e6 / ppqn
    return np.round(out * SR).astype(np.int64)


def _events(mid: bytes):
    (fmt, ntrk, ppqn), trs = midi.tracks(mid)
    tempo = _tempo_events(trs)
    raw = []
    for ti, t in enumerate(trs):
        tick, seq = 0, 0
        for d, st, pl, _ in midi._events(t):
            tick += d
            if st < 0xF0:
                raw.append((tick, ti, seq, st, bytes(pl)))
                seq += 1
    raw.sort(key=lambda e: (e[0], e[1], e[2]))
    ticks = np.array([e[0] for e in raw], dtype=np.int64)
    samples = _tick_to_sample(ticks, tempo, ppqn)
    # the sequencer runs in the driver's 2 ms timer: an event takes effect on
    # the first timer tick at or after its time (key-ons are written once per tick)
    samples = -(-samples // TICK) * TICK
    return [(int(s), e[3], e[4]) for s, e in zip(samples, raw)]


class Song:
    """One song's KORG playback, reduced to voices and controller timelines."""

    def __init__(self, mid: bytes, project: "prj.Project", bank: List["vsf.Sample"]):
        self.mid, self.proj, self.bank = mid, project, bank
        self.voices: List[Voice] = []
        self.timeline: Dict[int, List[Tuple[int, Chan]]] = {c: [] for c in range(16)}
        self._decoded: Dict[int, Tuple[np.ndarray, Optional[int]]] = {}
        self.steals: List[Tuple[int, Voice, Optional[int]]] = []   # (time, voice, its end before)
        self._play()

    def pcm(self, si: int) -> Tuple[np.ndarray, Optional[int]]:
        got = self._decoded.get(si)
        if got is None:
            pcm, loop = vsf.decode(self.bank[si])
            got = (np.frombuffer(pcm.tobytes() if hasattr(pcm, "tobytes") else bytes(pcm),
                                 dtype=np.int16).astype(np.float64), loop)
            self._decoded[si] = got
        return got

    def _note_pitch(self, ch: Chan, key: int, layer: "prj.Layer", smp: "vsf.Sample") -> int:
        kf = layer.key_follow
        semis = ((key - (smp.root or 60)) if kf else 0) + layer.transpose + (ch.coarse if kf else 0)
        sfine = smp.fine - 256 if smp.fine > 127 else smp.fine
        fine = layer.fine - sfine + (ch.fine if kf else 0)
        return (semis << 8) + (fine << 2)

    def _length_samples(self, v: Voice, ch: Chan) -> Optional[int]:
        """Output samples until a one-shot voice reaches its end flag, at its
        key-on pitch (None for a looped sample)."""
        pcm, loop = self.pcm(v.layer.sample)
        if loop is not None:
            return None
        p = self._note_pitch(ch, v.key, v.layer, v.sample) + 0x8000
        reg = min(0x3FFF, (NOTE_PITCH[max(0, min(255, p >> 8))] * v.sample.unk[2] >> 15)
                  * FINE_PITCH[(p & 0xFE) >> 1] >> 15)
        return int(len(pcm) * 4096 / max(reg, 1)) + 4

    def _release_end(self, v: Voice) -> int:
        head = ads_curve(v.layer.adsr1, v.layer.adsr2, max(1, v.keyoff - v.start))
        lvl = int(head[-1])
        rel = release_curve(v.layer.adsr2)
        j = int(np.searchsorted(-rel, -lvl, side="left"))
        return v.keyoff + (len(rel) - j)

    def _play(self) -> None:
        chans = [Chan() for _ in range(16)]
        slots: Dict[int, List[Optional[Voice]]] = {0: [None] * VOICES_PER_CORE, 1: [None] * VOICES_PER_CORE}
        progs = self.proj.programs
        for t, st, pl in _events(self.mid):
            c, kind = st & 15, st >> 4
            ch = chans[c]
            if kind == 0x9 and pl[1] > 0:
                self._note_on(t, c, ch, pl[0], pl[1], slots)
            elif kind == 0x8 or (kind == 0x9 and pl[1] == 0):
                for core in (0, 1):
                    for v in slots[core]:
                        if (v is not None and v.channel == c and v.key == pl[0] and v.keyoff is None
                                and (v.end is None or v.end > t) and not v.hold):
                            v.keyoff = t
                            e = self._release_end(v)
                            v.end = e if v.end is None else min(v.end, e)
            elif kind == 0xC:
                ch.program = pl[0] if pl[0] < len(progs) else 0
            elif kind == 0xE:
                ch.bend = ((pl[1] << 7) | pl[0]) - 8192
                self._mark(c, t, ch)
            elif kind == 0xB:
                cc, val = pl[0], pl[1]
                if cc == 7:
                    ch.vol = val
                elif cc == 11:
                    ch.expr = val
                elif cc == 10:
                    ch.pan = val - 64
                elif cc == 1:
                    ch.mod = val
                elif cc == 27:
                    ch.lfo = val
                elif cc == 91:
                    ch.rev = val
                elif cc == 18:
                    ch.coarse = val - 64
                elif cc == 19:
                    ch.fine = val - 64
                elif cc in (101, 100):
                    msb, lsb = ch.rpn
                    ch.rpn = (val, lsb) if cc == 101 else (msb, val)
                    ch.nrpn = False
                elif cc in (99, 98):
                    ch.nrpn = True
                elif cc == 6 and not ch.nrpn and ch.rpn[0] == 0:
                    if ch.rpn[1] == 0:
                        ch.bend_range = max(0, min(24, val))
                    elif ch.rpn[1] == 1:
                        ch.fine = val - 64
                    elif ch.rpn[1] == 2:
                        ch.coarse = max(-24, min(24, val - 64))
                self._mark(c, t, ch)
        self.length = max([v.end or 0 for v in self.voices] + [0])

    def _mark(self, c: int, t: int, ch: Chan) -> None:
        tl = self.timeline[c]
        snap = Chan(**{k: getattr(ch, k) for k in ch.__dataclass_fields__})
        if tl and tl[-1][0] == t:
            tl[-1] = (t, snap)
        else:
            tl.append((t, snap))

    def _note_on(self, t: int, c: int, ch: Chan, key: int, vel: int, slots) -> None:
        progs = self.proj.programs
        if ch.program >= len(progs):
            return
        ki = progs[ch.program].key_map[key]
        if ki == 0xFFFF or ki >= len(self.proj.keys):
            return
        rec = self.proj.keys[ki]
        layers = rec.layers
        if layers[0].sample == 0 and layers[1].sample == 0:
            return
        for layer in layers:                      # ChannelCheckGroup, layer 0 then layer 1
            g = layer.raw[11]
            if g:
                for core in (0, 1):
                    for v in slots[core]:
                        if (v is not None and v.channel == c and v.group == g and v.keyoff is None
                                and (v.end is None or v.end > t)):
                            v.keyoff = t
                            e = self._release_end(v)
                            v.end = e if v.end is None else min(v.end, e)
        for layer in layers:
            if layer.sample == 0 or layer.sample >= len(self.bank):
                continue
            smp = self.bank[layer.sample]
            if smp.placeholder:
                continue
            core = layer.raw[13] & 1
            pool = slots[core]
            free = [i for i, v in enumerate(pool) if v is None or (v.end is not None and v.end <= t)]
            if free:
                slot = free[0]
            else:
                # GetLowestPriorityVoice: the largest priority word wins; that
                # word is 0x80 once a voice is released (VoiceNoteOff), else its
                # age rank (IncrementAllVoicesPriority); ties go to the last slot.
                # The channel priority byte above it (CC89) is unused by the songs.
                def steal_key(i: int) -> Tuple[int, int, int]:
                    o = pool[i]
                    if o.keyoff is not None and o.keyoff <= t:
                        return (1, 0, i)
                    return (0, o.age, i)
                slot = max(range(VOICES_PER_CORE), key=steal_key)
                old = pool[slot]
                self.steals.append((t, old, old.end))
                old.end = t if old.end is None else min(old.end, t)      # ForceVoiceOff + new key-on
            for v in pool:
                if v is not None:
                    v.age += 1
            v = Voice(core, slot, c, key, vel, layer, smp, ch.program, t,
                      send=ch.rev >= 64, hold=layer.raw[12] != 0, group=layer.raw[11])
            n = self._length_samples(v, ch)
            if n is not None:
                v.end = t + n
            pool[slot] = v
            self.voices.append(v)
            self._mark(c, t, ch)

    # ------------------------------------------------------------- render
    def _chan_at(self, c: int, ticks: np.ndarray):
        """Per-tick controller arrays for channel c at absolute sample times."""
        tl = self.timeline[c]
        times = np.array([t for t, _ in tl], dtype=np.int64)
        idx = np.searchsorted(times, ticks, side="right") - 1
        idx = np.clip(idx, 0, len(tl) - 1)
        fields = ("vol", "expr", "pan", "mod", "lfo", "bend", "bend_range")
        return {f: np.array([getattr(tl[i][1], f) for i in range(len(tl))])[idx] for f in fields}

    def render_voice(self, v: Voice, song_end: int) -> Tuple[np.ndarray, np.ndarray, int]:
        end = v.end if v.end is not None else song_end
        n = max(0, min(end, song_end) - v.start)
        if n <= 0:
            return np.zeros(0), np.zeros(0), v.start
        pcm, loop = self.pcm(v.layer.sample)
        if not len(pcm):
            return np.zeros(0), np.zeros(0), v.start
        # per-tick controller state, ticks on the driver's absolute 2 ms grid
        t_first = (v.start // TICK) * TICK
        nt = (v.start + n - t_first) // TICK + 2
        tick_t = t_first + np.arange(nt) * TICK
        cs = self._chan_at(v.channel, tick_t)
        chan0 = Chan()
        prog = self.proj.programs[v.program]
        wave_type = prog.params[8] & 0xF
        base = self._note_pitch(chan0, v.key, v.layer, v.sample)
        # the voice uses the channel tuning at key-on
        tl = self.timeline[v.channel]
        times = [t for t, _ in tl]
        snap = tl[max(0, np.searchsorted(times, v.start, side="right") - 1)][1] if tl else Chan()
        base = self._note_pitch(snap, v.key, v.layer, v.sample)
        bend = (cs["bend_range"] * cs["bend"]) >> 5
        inc = np.array([LFO_INC[x] for x in cs["lfo"]], dtype=np.int64)
        phase = (np.cumsum(np.concatenate([[0], inc[:-1]])) + 0x8000) % 0x10000 - 0x8000
        if wave_type == 1:
            w = phase
        elif wave_type == 2:
            w = np.where(phase > 0, 0x7FFF, -0x7FFF)
        elif wave_type == 3:
            w = (np.abs(phase) - 0x4000) * 2
        elif wave_type == 4:
            w = ((0x7FFF - np.abs(phase)) * phase) >> 13
        else:
            w = -phase
        depth = 2 * cs["mod"] * 2
        lfo = (w * depth) >> 15
        p = base + bend + lfo + 0x8000
        p = np.clip(p, 0, 0xFFFF)
        note_tab = np.array(NOTE_PITCH, dtype=np.int64)
        fine_tab = np.array(FINE_PITCH, dtype=np.int64)
        reg = (note_tab[p >> 8] * v.sample.unk[2]) >> 15
        reg = (reg * fine_tab[(p & 0xFE) >> 1]) >> 15
        reg = np.minimum(reg, 0x3FFF)
        per_sample = np.repeat(reg, TICK)[v.start - t_first:v.start - t_first + n]
        counter = np.cumsum(per_sample) - per_sample[0]
        ipos = counter >> 12
        frac = (counter >> 4) & 0xFF
        L = len(pcm)
        if loop is None:
            alive = ipos < L
            if not alive.all():
                n = int(np.argmin(alive))          # the first position past the sample's end
                if n == 0:
                    return np.zeros(0), np.zeros(0), v.start
                ipos, frac = ipos[:n], frac[:n]
            idx = ipos
        else:
            ll = max(1, L - loop)
            idx = np.where(ipos < L, ipos, loop + (ipos - L) % ll)
        padded = np.concatenate([np.zeros(3), pcm])
        g0, g1, g2, g3 = _G
        s = (g0[frac] * padded[idx] + g1[frac] * padded[idx + 1]
             + g2[frac] * padded[idx + 2] + g3[frac] * padded[idx + 3]) / 32768.0
        keyoff = None if v.keyoff is None else v.keyoff - v.start
        env = envelope(v.layer.adsr1, v.layer.adsr2, keyoff, len(s)).astype(np.float64)
        if len(env) < len(s):
            s = s[:len(env)]
        s = s * env / 32768.0
        # volume and pan per tick, as VoiceOn/DynamicVolumeExp write them
        pow2 = np.array(POW2, dtype=np.int64)
        pant = np.array(PAN_TABLE, dtype=np.int64)
        level = (pow2[v.velocity] * pow2[v.layer.volume & 0x7F]) >> 15
        level = (level * pow2[prog.params[3] & 0x7F]) >> 15
        chvol = (pow2[127] * pow2[cs["expr"]]) >> 15
        chvol = (chvol * pow2[cs["vol"]]) >> 15
        vol = (level * chvol) >> 15
        pan = np.clip(v.layer.pan + cs["pan"], 0, 127)
        vl = (vol * pant[pan]) >> 16
        vr = (vol * pant[pan + 128]) >> 16
        grid = tick_t - v.start
        sx = np.arange(len(s))
        gl = np.interp(sx, grid, vl.astype(np.float64))
        gr = np.interp(sx, grid, vr.astype(np.float64))
        return s * gl * 2 / 32768.0, s * gr * 2 / 32768.0, v.start


def _clamp(x: np.ndarray) -> np.ndarray:
    return np.clip(x, -32768.0, 32767.0)


def render(mid: bytes, prj_bytes: bytes, vsf_bytes: bytes, seconds: Optional[float] = None,
           tail: float = 6.0, irx: Optional[bytes] = None, libsd: Optional[bytes] = None,
           fx_override=None, stems: bool = False, gain: float = 1.0, headroom: bool = False):
    """Render a song at SPU2 scale. Returns ((n, 2) int16 array, info), plus
    a dict of float stems (``dry``, ``send``, ``wet``) when ``stems``.

    The mix follows the driver's SPU2 set-up (korgIopVagInitSpu2): MMIX core 0
    = 0xFC0 and core 1 = 0xFCC, so each core's voices feed its own dry mix
    and (by their effect bit) its own effect unit, and core 0's output enters
    core 1 dry only. Each core's voice sums are clamped to 16 bits as the
    hardware does; master volumes are taken at full scale (the driver never
    writes them). The default is the PS2's own level at unit volume 127, clamps
    included: a (peak-normalised) game rip of 001_milchia has a crest factor of
    14.0 dB, against 14.5 dB for this render and 16.9 dB without the clamps.
    ``gain`` scales the voices; with ``headroom`` a song whose mix would pass
    full scale is scaled down to just under it (one constant gain, reported as
    ``gain_db``), as a lower unit volume would."""
    import songfx
    if irx:
        load_driver_tables(irx)
    proj = prj.parse(prj_bytes)
    bank = vsf.parse(vsf_bytes)
    song = Song(mid, proj, bank)
    last_note = max([v.start for v in song.voices] + [0])
    song_end = int((seconds * SR) if seconds else (last_note + tail * SR))
    song_end = min(song_end, max(song.length, last_note) + int(tail * SR))
    n = song_end + 4
    dry = {0: np.zeros((n, 2)), 1: np.zeros((n, 2))}
    send = {0: np.zeros((n, 2)), 1: np.zeros((n, 2))}
    for v in song.voices:
        if v.start >= song_end:
            continue
        l, r, s0 = song.render_voice(v, song_end)
        if not len(l):
            continue
        e = min(n, s0 + len(l))
        dry[v.core][s0:e, 0] += l[:e - s0]
        dry[v.core][s0:e, 1] += r[:e - s0]
        if v.send:
            send[v.core][s0:e, 0] += l[:e - s0]
            send[v.core][s0:e, 1] += r[:e - s0]
    sfx = songfx.song_fx(mid)
    fxs = dict(sfx.cores) if fx_override is None else {0: songfx.Fx(), 1: fx_override}
    info = {"voices": len(song.voices), "fx": fxs[1].name, "fx_vol": fxs[1].vol}
    wet = {0: np.zeros((n, 2)), 1: np.zeros((n, 2))}
    for core in (0, 1):
        fx = fxs[core]
        if not (libsd and fx.mode and fx.vol and send[core].any()):
            continue
        presets, sizes = libsd_presets(libsd)
        regs = effect_registers(presets[fx.mode], fx.mode, fx.delay, fx.feedback)
        rv = SpuReverb(regs, sizes[fx.mode])
        el = POW2[fx.vol] * PAN_TABLE[fx.pan] >> 15            # InternalSetFxVolume
        er = POW2[fx.vol] * PAN_TABLE[fx.pan + 128] >> 15
        wet[core] = rv.process(_clamp(send[core])) * (np.array([el, er]) / 32768.0)
        if core == 1:
            info.update(block=rv.block, evol=(el, er), work_words=rv.N)
    if headroom:
        # a song whose SPU2-scale mix would pass full scale is played as if at
        # a lower unit volume: one constant gain for the whole song, applied
        # before the core clamps (the reverb is linear, so its output scales too)
        raw_peak = float(np.abs(dry[0] + dry[1] + wet[0] + wet[1]).max()) * gain if n else 0.0
        if raw_peak > 32767 * 0.99:
            gain *= 32767 * 0.99 / raw_peak
    for core in (0, 1):
        dry[core] *= gain
        wet[core] *= gain
        send[core] *= gain
    core0 = _clamp(_clamp(dry[0]) + wet[0])
    out = _clamp(dry[1]) + core0 + wet[1]
    info["gain_db"] = round(20 * math.log10(gain), 2) if gain > 0 else None
    info["peak"] = float(np.abs(out).max()) if len(out) else 0.0
    info["clipped_samples"] = int((np.abs(out) > 32767).sum()
                                  + (np.abs(dry[1]) > 32767).sum() + (np.abs(dry[0]) > 32767).sum())
    pcm = np.clip(np.round(out), -32768, 32767).astype(np.int16)
    if stems:
        return pcm, info, {"dry": dry[0] + dry[1], "send": send[1], "wet": wet[0] + wet[1]}
    return pcm, info


def write_wav(path: str, pcm: np.ndarray) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype("<i2").tobytes())


# ---------------------------------------------------------------- self-test

def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    check(len(GAUSS) == 512 and GAUSS[0x1FF] == 0x59B3 and GAUSS[0] == -1, "Gauss table (512 entries)")
    s = [GAUSS[i] + GAUSS[0xFF - i] + GAUSS[0x100 + i] + GAUSS[0x1FF - i] for i in range(256)]
    check(0x7F7F <= min(s) and max(s) <= 0x7F81, "Gauss quadruples sum to 7F7F..7F81 as psx-spx says")
    a = ads_curve(0x808F, 0xDFE6, 2000)
    check(a[0] > 0 and a.max() == 0x7FFF, "instant attack reaches full level")
    r = release_curve(0xDFE6)
    check(0 < len(r) < 2000, f"exp release shift 6 ends in {len(r)} samples (~{len(r) / 48:.1f} ms)")
    lin = release_curve(0x0000)
    check(len(lin) <= 0x7FFF // (8 << 11) + 2, "linear release shift 0 steps -16384 per sample")
    e = envelope(0x808F, 0xDFE6, 100, 5000)
    check(e[99] >= 0x7F00 and e[-1] == 0 and len(e) < 5000, "key-off starts the release from the current level")
    regs = effect_registers([0] * 32, 7, 100, 60)
    check(regs[REG_NAMES.index("vWALL")] == 60 * 258 and regs[REG_NAMES.index("mLSAME")] == 2 * 101 * 32,
          "ECHO rewrite: D=(delay+1)*32, vWALL=feedback*258")
    h = halfband()
    check(abs(h.sum() - 1) < 1e-12 and len(h) == 39, "half-band filter, unit DC gain")
    y = _scan_iir(np.ones(10), 0.5, 2.0)
    ref, prev = [], 2.0
    for k in range(10):
        prev = 1 + 0.5 * prev
        ref.append(prev)
    check(np.allclose(y, ref), "closed-form IIR matches the recursion")
    # reverb: the lane equations against the word-by-word work area, on
    # psx-spx's published PS1 examples (Hall; Chaos Echo = the ECHO rewrite)
    hall = [0x01A5, 0x0139, 0x6000, 0x5000, 0x4C00, 0xB800, 0xBC00, 0xC000,
            0x6000, 0x5C00, 0x15BA, 0x11BB, 0x14C2, 0x10BD, 0x11BC, 0x0DC1,
            0x11C0, 0x0DC3, 0x0DC0, 0x09C1, 0x0BC4, 0x07C1, 0x0A00, 0x06CD,
            0x09C2, 0x05C1, 0x05C0, 0x041A, 0x0274, 0x013A, 0x8000, 0x8000]
    chaos = [0x0001, 0x0001, 0x7FFF, 0x7FFF, 0, 0, 0, 0x8100, 0, 0, 0x1FFF, 0x0FFF, 0x1005, 0x0005, 0, 0,
             0x1005, 0x0005, 0, 0, 0, 0, 0, 0, 0, 0, 0x1004, 0x1002, 0x0004, 0x0002, 0x8000, 0x8000]
    tmpl = list(chaos)
    for nm in ("mLSAME", "mRSAME", "dLSAME", "mLCOMB1", "mLAPF1", "mRAPF1", "vWALL"):
        tmpl[REG_NAMES.index(nm)] = 0
    check(effect_registers(tmpl, 7, 127, 0x8100 // 258)[:7] == chaos[:7] and
          effect_registers(tmpl, 7, 127, 0)[10:30] == chaos[10:30],
          "the ECHO rewrite at delay 127 rebuilds psx-spx's Chaos Echo")
    rng = np.random.default_rng(7)
    for name, regs, size, steps in (("Hall", hall, 0xADE0 // 8, 6000), ("Chaos Echo", chaos, 0x18040 // 8, 18000)):
        x = np.zeros((steps, 2))
        x[50:250] = rng.normal(0, 3000, (200, 2))
        x[300, 0] = 20000
        rv = SpuReverb(regs, size)
        got = rv.process(x, resample=False)
        want = reverb_direct(regs, size, x)
        err = float(np.abs(got - want).max())
        check(err < 1e-6 and np.abs(want).max() > 100,
              f"{name}: lane equations = word-by-word work area (max diff {err:.1e}, block {rv.block})")
    rv = SpuReverb(chaos, 0x18040 // 8)
    x = np.zeros((18000, 2))
    x[100, 0] = 10000
    got = rv.process(x, resample=False)
    first = int(np.argmax(np.abs(got[:, 0]) > 1)) - 100
    check(first == 4 * (0x1FFF - 0x1005) + 4 * 1 + 4 * 1,          # (mLSAME - mLCOMB1) + dAPF1 + dAPF2
          f"Chaos Echo: first repeat {first} steps = {first / 24000 * 1000:.1f} ms after the input")
    # the driver's key rules on a synthetic song (formula tables, no disc data)
    adpcm = (bytes([0x00, 0x00]) + bytes([0x77] * 14)) * 200 + bytes([0x00, 0x01]) + bytes(14)
    bank = [vsf.Sample(0, vsf.PLACEHOLDER.decode(), 48000, 60, 0, 0, 0, b"", (0, 0, 0, 0)),
            vsf.Sample(1, "dc", 48000, 60, 0, 0, len(adpcm), adpcm, (0, 0, 4096, 0))]

    def lay(sample: int, group: int, hold: int, core: int) -> "prj.Layer":
        return prj._layer(struct.pack("<HHHBBbbBBBB", sample, 0x808F, 0xDFE6, 127, 64, 0, 0, 0,
                                      group, hold, core) + bytes(2))
    keys = [prj.Key(0, "none", [lay(0, 0, 0, 1), lay(0, 0, 0, 1)]),
            prj.Key(1, "hold", [lay(1, 0, 0xFF, 1), lay(0, 0, 0, 1)]),
            prj.Key(2, "plain", [lay(1, 0, 0, 1), lay(0, 0, 0, 1)]),
            prj.Key(3, "grp", [lay(1, 4, 0xFF, 0), lay(0, 0, 0, 1)])]
    kmap = [0xFFFF] * 128
    kmap[60], kmap[62], kmap[64] = 1, 2, 3
    proj = prj.Project("t", keys, [prj.Program(0, "p", bytes([0, 0, 0, 127]) + bytes(44), tuple(kmap))])

    def smf(evs) -> bytes:
        body = bytearray()
        for d, e in evs:
            body += midi._write_vlq(d) + e
        body += b"\x00\xff\x2f\x00"
        return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", len(body)) + body
    # 120 bpm: 480 ticks = 0.5 s; notes last 10 ticks (~10 ms)
    song = Song(smf([(0, b"\x90\x3c\x7f"), (10, b"\x80\x3c\x00"), (470, b"\x90\x3e\x7f"), (10, b"\x80\x3e\x00"),
                     (470, b"\x90\x40\x7f"), (10, b"\x90\x40\x7f")]), proj, bank)
    v_hold, v_plain, g1, g2 = song.voices
    n_hold = len(song.render_voice(v_hold, 10 * SR)[0])
    n_plain = len(song.render_voice(v_plain, 10 * SR)[0])
    check(abs(n_hold - 201 * 28) <= 2,          # 200 frames + the frame carrying the end flag
          f"a hold layer ignores note-off and plays its one-shot to the end ({n_hold} of {201 * 28} samples)")
    check(800 < n_plain < 1600, f"a plain layer releases at note-off ({n_plain} samples: 10 ms + the release)")
    check(g1.keyoff == g2.start and g1.core == 0 and g2.core == 0,
          "a new note of the same choke group releases the last one; byte +13 bit 0 picks core 0")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if len(a) >= 4:
        secs = None
        if "--seconds" in a:
            secs = float(a[a.index("--seconds") + 1])
        here = os.path.dirname(os.path.abspath(a[0]))
        # next to the song, or where `cli.py dump` copies them: the song
        # is OUT/dump/chainN/sound/smf/NAME.mid, the modules OUT/browse/code/IOP
        dirs = (here, os.path.join(here, "..", "..", "..", "..", "browse", "code", "IOP"))
        found = {}
        for name in ("KORGIVAG.IRX", "LIBSD.IRX"):
            for d in dirs:
                p = os.path.join(d, name)
                if os.path.exists(p):
                    found[name] = open(p, "rb").read()
                    break
        irx, libsd = found.get("KORGIVAG.IRX"), found.get("LIBSD.IRX")
        if libsd is None:
            print("warning: IOP\\LIBSD.IRX not found (cli.py dump copies it): "
                  "rendering without the reverb/echo", file=sys.stderr)
        if irx is None:
            print("warning: IOP\\KORGIVAG.IRX not found: formula tables instead of the driver's",
                  file=sys.stderr)
        pcm, info = render(open(a[0], "rb").read(), open(a[1], "rb").read(), open(a[2], "rb").read(),
                           secs, irx=irx, libsd=libsd)
        write_wav(a[3], pcm)
        print(info)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
