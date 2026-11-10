#!/usr/bin/env python3
"""Pull the five mesh VU1 microprograms out of the Episode II executable.

    python vuprog.py --self-test
    python vuprog.py SLUS_208.92 OUTDIR     # writes prog0.bin .. prog4.bin

The programs sit in the `.vudata` section as ready-to-send VIF packets
(`MPG` commands of up to 256 instructions each, then `BASE 0`,
`OFFSET 276`).  FUN_001ccae8 in SLUS_208.92 returns, for the program
selected by FUN_001cf520, the packet's address, its length in qwords
(the word 0x10 bytes before the packet) and the entry address of the mesh
routine that every draw starts with `MSCAL`:

    program  packet VA   mesh entry   chosen when
    0        0x673160    0x73A        default
    1        0x677010    0x68F        the .pxy header flags have bit 0
    2        0x67A890    0x592        instance flag 0x8000
    3        0x67D9B0    0x765        a renderer global is set
    4        0x6819C0    0x652        another renderer global is set

The output files are raw VU code, 8 bytes per instruction (lower word
first), ready for any VU disassembler; the kit does not ship one.  The
instruction addresses quoted in docs/MODELS.md are indices into prog0.bin.
"""
import os
import struct
import sys

import vif

PROGRAMS = ((0x673160, 0x73A), (0x677010, 0x68F), (0x67A890, 0x592),
            (0x67D9B0, 0x765), (0x6819C0, 0x652))
SLUS_SHA1 = "200605e6eb52910f015f6b86896d323fef47f44e"


def elf_va_to_offset(elf, va):
    phoff, = struct.unpack_from("<I", elf, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    for i in range(phnum):
        t, off, vaddr, _, filesz = struct.unpack_from(
            "<5I", elf, phoff + i * phentsize)
        if t == 1 and vaddr <= va < vaddr + filesz:
            return off + va - vaddr
    raise ValueError("address %#x is not in a loaded segment" % va)


def extract(elf, va):
    """(code bytes, {'base': n, 'offset': n}) of the packet at `va`."""
    off = elf_va_to_offset(elf, va)
    qwc, = struct.unpack_from("<I", elf, off - 0x10)
    mem = bytearray(0x4000)
    top = 0
    regs = {}
    for c in vif.walk(elf, off, off + qwc * 16):
        if c.name == "MPG":
            n = c.num or 256
            mem[c.imm * 8:(c.imm + n) * 8] = elf[c.data_off:c.data_off + n * 8]
            top = max(top, c.imm + n)
        elif c.name in ("BASE", "OFFSET"):
            regs[c.name.lower()] = c.imm
        elif c.name != "NOP":
            raise vif.VifError("unexpected %s in a microprogram packet"
                               % c.name)
    return bytes(mem[:top * 8]), regs


def self_test():
    w = lambda cmd, num, imm: struct.pack("<I", cmd << 24 | num << 16 | imm)
    code = bytes(bytearray(range(48)))
    pkt = w(0, 0, 0) + w(0x4A, 4, 0x10) + code[:32]
    pkt += w(0, 0, 0) + w(0x4A, 2, 0x14) + code[32:]
    pkt += w(0x03, 0, 0) + w(0x02, 0, 276)
    pkt += b"\0" * (-len(pkt) % 16)
    head = bytearray(0x54)
    head[:4] = b"\x7fELF"
    struct.pack_into("<I", head, 0x1C, 0x34)
    struct.pack_into("<HH", head, 0x2A, 0x20, 1)
    struct.pack_into("<8I", head, 0x34, 1, 0x60, 0x1000, 0x1000,
                     0x20 + len(pkt), 0x20 + len(pkt), 7, 16)
    elf = bytes(head) + b"\0" * 12 + struct.pack("<4I", len(pkt) // 16, 0, 0, 0) \
        + b"\0" * 0 + pkt
    # the packet starts 0x10 after the qword count
    blob, regs = extract(elf, 0x1000 + 0x10)
    assert regs == {"base": 0, "offset": 276}, regs
    assert len(blob) == 0x16 * 8 and blob[0x10 * 8:] == code
    try:
        extract(elf, 0x9000)
    except ValueError:
        pass
    else:
        raise AssertionError("accepted an unmapped address")
    print("vuprog self-test ok")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] == "--self-test":
        return self_test()
    import hashlib
    with open(argv[0], "rb") as f:
        elf = f.read()
    if hashlib.sha1(elf).hexdigest() != SLUS_SHA1:
        sys.stderr.write("warning: not the SLUS_208.92 these addresses were "
                         "read from\n")
    out = argv[1] if len(argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for i, (va, entry) in enumerate(PROGRAMS):
        blob, regs = extract(elf, va)
        path = os.path.join(out, "prog%d.bin" % i)
        with open(path, "wb") as f:
            f.write(blob)
        print("program %d: packet %#x, %d instructions, mesh entry %#x, "
              "BASE %d OFFSET %d -> %s" % (i, va, len(blob) // 8, entry,
                                           regs.get("base", -1),
                                           regs.get("offset", -1), path))


if __name__ == "__main__":
    sys.exit(main())
