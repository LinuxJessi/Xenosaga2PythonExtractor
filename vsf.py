"""vsf.py — Episode II's KORG sample banks (``.vsf``) to WAV.

Episode II's field music and sound effects run on KORG's IOP driver
(``IOP\\KORGIVAG.IRX``, "KORG IOP Sound Driver ... (C)2001-2004 KORG Inc."):
a Standard MIDI File (``sound\\smf\\NNN_name.mid``) plays programs from a
project (``.prj``, see ``prj.py``) whose keys point at samples in this bank.
Sound-effect banks (``sound\\vsf\\*.vsf``) use the same container.

Layout (all 1,074 banks walk to the exact end of file)::

    +0x00 "VSFp"  +0x04 u32 0x10  +0x08 u32 sample count  +0x0C u32 sample bytes
    +0x10 u32 ?   +0x14 u32 ?          (stale values from the authoring PC)
    +0x18 records, each 0x28 bytes followed by its data:
        +0x00 u32 ?  +0x04 u32 ?  +0x08 u16 rate (Hz)  +0x0A u8 root key  +0x0B u8 fine
        +0x0C u32 ?  +0x10 u32 ?  +0x14 u32 data size  +0x18 name[16]
        then `size` bytes of SPU ADPCM (16-byte frames, 28 samples each)

Record 0 of every bank is a "-- no sample --" placeholder whose 64 data bytes
are leftover memory, not audio. Every real sample carries an SPU end flag:
3,425 are one-shots (flag 1) followed by the usual silent pad frame, 308 end
with flag 3 (loop back to the frame flagged 4). Decoding stops at the first
end flag, which is also where the 9 malformed frames found inside real
samples begin (tail padding; one holds UTF-16 "\\DosDevices\\R:\\..." from
the authoring tool's memory).

    python vsf.py --self-test
    python vsf.py FILE.vsf OUTDIR      # every sample as <index>_<name>.wav
"""
from __future__ import annotations

import array
import struct
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

MAGIC = b"VSFp"
RECORD = 0x28
PLACEHOLDER = b"-- no sample --"


class VsfError(ValueError):
    pass


@dataclass
class Sample:
    index: int
    name: str
    rate: int
    root: int
    fine: int
    offset: int          # data offset in the file
    size: int
    data: bytes
    unk: Tuple[int, int, int, int]

    @property
    def placeholder(self) -> bool:
        return self.name.encode("latin1") == PLACEHOLDER


def parse(data: bytes) -> List[Sample]:
    if data[:4] != MAGIC:
        raise VsfError("not a VSF (magic)")
    count = struct.unpack_from("<I", data, 8)[0]
    p, out = 0x18, []
    for i in range(count):
        if p + RECORD > len(data):
            raise VsfError(f"record {i} runs past the end")
        a0, a1, rate, root, fine, c, d, size = struct.unpack_from("<IIHBBIII", data, p)
        name = data[p + 0x18:p + 0x28].split(b"\x00")[0].decode("latin1")
        if p + RECORD + size > len(data):
            raise VsfError(f"record {i} ({name}): data runs past the end")
        out.append(Sample(i, name, rate, root, fine, p + RECORD, size,
                          data[p + RECORD:p + RECORD + size], (a0, a1, c, d)))
        p += RECORD + size
    if p != len(data):
        raise VsfError(f"{len(data) - p} bytes after the last record")
    return out


# ------------------------------------------------------------- SPU ADPCM
# (decoder from Xenosaga1PythonExtractor/browse.py, validated there against
#  ffmpeg's adpcm_psx; here it stops at the end flag and reports the loop)

_FILTERS = ((0, 0), (60, 0), (115, -52), (98, -55), (122, -60))
_NIB = tuple(n - 16 if n >= 8 else n for n in range(16))


def frames_until_end(adpcm: bytes) -> Tuple[int, Optional[int]]:
    """(frames to decode, loop-start frame or None). Stops after the first
    frame with the end flag; a loop exists when that frame also has the
    repeat flag (bit 1), starting at the last loop-start flag (bit 2) seen."""
    loop = None
    n = len(adpcm) // 16
    for k in range(n):
        f = adpcm[k * 16 + 1]
        if f & 4:
            loop = k
        if f & 1:
            return k + 1, (loop if f & 2 else None)
    return n, None


def decode_adpcm(adpcm: bytes, frames: Optional[int] = None) -> array.array:
    out = array.array("h")
    h1 = h2 = 0
    n = len(adpcm) // 16 if frames is None else frames
    for k in range(n):
        base = k * 16
        hdr = adpcm[base]
        shift, filt = hdr & 0x0F, hdr >> 4
        if filt > 4 or shift > 12:
            out.extend((0,) * 28)
            continue
        f0, f1 = _FILTERS[filt]
        up = 12 - shift
        for b in adpcm[base + 2:base + 16]:
            for nib in (_NIB[b & 0x0F], _NIB[b >> 4]):
                p = h1 * f0 + h2 * f1
                s = (nib << up) + (p // 64 if p >= 0 else -((-p) // 64))
                s = 32767 if s > 32767 else -32768 if s < -32768 else s
                h2, h1 = h1, s
                out.append(s)
    return out


def decode(sample: Sample) -> Tuple[array.array, Optional[int]]:
    """(PCM, loop start in samples or None)."""
    frames, loop = frames_until_end(sample.data)
    pcm = decode_adpcm(sample.data, frames)
    return pcm, (loop * 28 if loop is not None else None)


def health(pcm: array.array) -> dict:
    if not pcm:
        return {"samples": 0, "peak": 0, "clipped": 0, "dc": 0.0}
    peak = max(max(pcm), -min(pcm))
    clipped = sum(1 for s in pcm if s in (32767, -32768))
    return {"samples": len(pcm), "peak": peak, "clipped": clipped, "dc": sum(pcm) / len(pcm)}


def write_wav(path, pcm: array.array, rate: int, loop: Optional[int] = None) -> None:
    """16-bit mono WAV; a loop is recorded in a standard 'smpl' chunk so
    samplers loop it the way the SPU does."""
    data = pcm.tobytes() if sys.byteorder == "little" else _swapped(pcm)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    if loop is not None and len(pcm) > loop:
        smpl = struct.pack("<9I", 0, 0, 1_000_000_000 // max(rate, 1), 60, 0, 0, 0, 1, 0)
        smpl += struct.pack("<6I", 0, 0, loop, len(pcm) - 1, 0, 0)
        chunk = b"smpl" + struct.pack("<I", len(smpl)) + smpl
        with open(path, "r+b") as fh:
            fh.seek(0, 2)
            fh.write(chunk)
            size = fh.tell() - 8
            fh.seek(4)
            fh.write(struct.pack("<I", size))


def _swapped(pcm: array.array) -> bytes:
    c = array.array("h", pcm)
    c.byteswap()
    return c.tobytes()


def safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name) or "unnamed"


def export(data: bytes, out_dir) -> List[dict]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in parse(data):
        if s.placeholder:
            continue
        pcm, loop = decode(s)
        stem = Path(safe_name(s.name)).stem
        fname = f"{s.index:03d}_{stem}.wav"
        write_wav(out / fname, pcm, s.rate or 22050, loop)
        rows.append({"index": s.index, "name": s.name, "rate": s.rate, "root": s.root,
                     "fine": s.fine, "loop": loop if loop is not None else "", "file": fname,
                     **health(pcm)})
    return rows


# ------------------------------------------------------------------ self-test

def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    # frame: filter 0, shift 12 -> sample = nibble; nibbles 1..7, -8..-1 pattern
    frame = bytes([0x0C, 0x00]) + bytes(((2 * i + 1) & 0xF) | ((((2 * i + 2) & 0xF)) << 4) for i in range(14))
    pcm = decode_adpcm(frame)
    want = [_NIB[(k + 1) & 0xF] for k in range(28)]
    check(list(pcm) == want, "filter 0 / shift 12 decodes nibbles verbatim")
    end = bytes([0x0C, 0x01]) + bytes(14)
    pad = bytes([0x00, 0x07]) + bytes(14)
    check(frames_until_end(frame + end + pad) == (2, None), "one-shot stops after the end-flag frame")
    loop_start = bytes([0x0C, 0x04]) + bytes(14)
    loop_end = bytes([0x0C, 0x03]) + bytes(14)
    check(frames_until_end(frame + loop_start + frame + loop_end) == (4, 1), "loop starts at the flag-4 frame")
    rec = struct.pack("<IIHBBIII", 0, 0, 22050, 60, 0, 0, 0, 48) + b"test.wav".ljust(16, b"\x00")
    blob = MAGIC + struct.pack("<IIIII", 0x10, 1, 48, 0, 0) + rec + frame + end + pad
    s = parse(blob)
    check(len(s) == 1 and s[0].name == "test.wav" and s[0].rate == 22050, "container parse")
    try:
        parse(blob + b"\x00")
        check(False, "trailing bytes are refused")
    except VsfError:
        check(True, "trailing bytes are refused")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return self_test()
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    rows = export(Path(sys.argv[1]).read_bytes(), sys.argv[2])
    print(f"{len(rows)} samples -> {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
