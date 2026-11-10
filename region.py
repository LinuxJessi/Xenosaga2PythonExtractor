"""region.py — which Episode II build a disc carries, and where its text lives.

The kit was written against the USA discs. The Japanese retail discs
(SLPS_253.68 / SLPS_253.69, 2026-11-05) use the same TOC grammar, chain
numbering, ARX codec and SB VM, but lay text out the older way:

    what                     USA (SLUS_208.92/211.33)   Japan (SLPS_253.68/.69)
    scene/map dialogue       cf\\en\\scNNNN.sbl,          inline EUC-JP strings in the
                             model\\map\\en\\*.sbl          .sb globals (pushtext = byte
                             (pushtext = 0x20<<16|line)  offset), as on the Freaks demo
    movie subtitles          movie\\en\\*.txt             movie\\*.txt (same cue grammar)
    fonts                    font0.us / font1.us        font0.tex / font1.tex
                             (384 metric pairs)         (256 metric pairs)
    battle database          yamamoto\\batdat.us         yamamoto\\batdat.bin (EUC-JP)
    memory-card pack         carddata.bin (2 members)   carddata.new (4 members)
    battle AI scripts        (absent from the TOC)      yamamoto\\think\\think*.sb

Most rules below are a TOC path or a text-model choice. The one executable
address the kit reads is the font's extra-kanji list (``font.py``): it is
given per build as a virtual address and mapped to a file offset through the
ELF's program headers (``va_to_offset``), never as a fixed file offset.

Serials are read from SYSTEM.CNF on the images (2026-11-10): USA Disc 1
SLUS_208.92, USA Disc 2 SLUS_211.33 (the same executable byte for byte,
sha256 1f978e64c837f9b5...), Japan SLPS_253.68 / SLPS_253.69 (again one
executable), Xenosaga Freaks SLPS_253.53 (a launcher; the Episode II demo's
own executable is ``EXP\\EXP.ELF``).

``mixed_regions`` reports a disc set that mixes builds (Japan Disc 1 with USA
Disc 2, say): chain 0 would come from one and the story chain from the other,
and their text layouts differ. The commands that take several images warn.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class Region:
    name: str                   # "us", "jp", "demo"
    lang_dir: Optional[str]     # the per-language folder beside scripts/movies, or None
    text: str                   # "sbl" (string banks) or "inline" (strings in the .sb)
    fonts: Tuple[str, ...]
    batdat: str
    carddata: str
    exe: Optional[str] = None           # the game executable when it is not the boot ELF
    kanji_list_va: Optional[int] = None  # the font's extra-kanji list (font.py); None = not located
    ascii_table_va: Optional[int] = None  # the font's ASCII -> EUC table: u16 at VA + 2 * byte
    exe_sha256: Optional[str] = None    # the executable every address above was read from
    serial: Optional[str] = None        # set only on the profile of an UNKNOWN build


US = Region("us", "en", "sbl", ("font0.us", "font1.us"), "yamamoto\\batdat.us", "carddata.bin",
            kanji_list_va=0x63E870, ascii_table_va=0x63E770,     # FUN_00197988, FUN_00198b90
            exe_sha256="1f978e64c837f9b5f0dcaee4bbe3fc28b45368fdac47404eecf2249dcbf7462e")
JP = Region("jp", None, "inline", ("font0.tex", "font1.tex"), "yamamoto\\batdat.bin", "carddata.new",
            kanji_list_va=0x63A2A0, ascii_table_va=0x63A1A0,     # code at 0x197828, 0x198a58
            exe_sha256="4e6b7fa2d0fb21af561519d914cf5b84598b6ad8622412a0daa6963825942c75")
DEMO = Region("demo", None, "inline", ("font0.tex", "font1.tex"), "yamamoto\\batdat.bin", "carddata.new",
              exe="EXP\\EXP.ELF",
              kanji_list_va=0x639060, ascii_table_va=0x638F60,   # code at 0x197978, 0x198bb8
              exe_sha256="6205048be90af43bf43e7dfeb3a4b2fa67278b82ad894bdb9313ff725afd5b53")

PROFILES = {
    "SLUS_208.92": US,          # Disc 1
    "SLUS_211.33": US,          # Disc 2 (byte-identical executable)
    "SLPS_253.68": JP,          # Disc 1
    "SLPS_253.69": JP,          # Disc 2 (byte-identical executable)
    "SLPS_253.53": DEMO,        # Xenosaga Freaks (EXP\\ chain 0)
}


class UnknownBuild(ValueError):
    pass


def region_of(iso) -> Region:
    """The profile for an IsoImage, by the serial of its boot executable. A serial the
    kit does not know gets a profile named "unknown" that carries the serial and
    nothing else: no known build's layout, addresses or loader rules are assumed for
    it. Such an image can be listed, dumped and browsed (those read the TOC only);
    every writer and every reader of executable tables calls ``require_known`` and
    refuses."""
    name = iso.boot_elf_name() if hasattr(iso, "boot_elf_name") else None
    if name in PROFILES:
        return PROFILES[name]
    return Region("unknown", None, "unknown", (), "", "", serial=name or "(no boot executable)")


def require_known(iso, what: str) -> Region:
    """The image's profile, or UnknownBuild naming the serial that was seen."""
    prof = region_of(iso)
    if prof.name == "unknown":
        raise UnknownBuild(f"{what} is refused: this image's boot executable is {prof.serial}, a build "
                           f"the kit does not know (known: {', '.join(sorted(PROFILES))}). "
                           f"list, dump and browse still work")
    return prof


# sha256 of executables a self-test builds in code (minidisc.py); never a real build
TEST_EXECUTABLES = set()


def require_executable(iso, what: str) -> Region:
    """``require_known`` plus: the executable on the image is byte for byte the one the
    profile's addresses and loader rules were read from (a re-release under the same
    serial, or a patched executable, is refused)."""
    import hashlib
    prof = require_known(iso, what)
    name, data = executable(iso)
    got = hashlib.sha256(data).hexdigest()
    if got != prof.exe_sha256 and got not in TEST_EXECUTABLES:
        raise UnknownBuild(f"{what} is refused: {name} on this image is not the executable the kit's "
                           f"{prof.name} rules were read from (sha256 {got[:16]}..., expected "
                           f"{(prof.exe_sha256 or '?')[:16]}...)")
    return prof


def mixed_regions(isos: Iterable) -> Optional[str]:
    """A warning text when the images (IsoImage objects) are not all one build, else None."""
    seen: Dict[str, List[str]] = {}
    for iso in isos:
        seen.setdefault(region_of(iso).name, []).append(
            f"{getattr(iso, 'path', iso)} ({iso.boot_elf_name() if hasattr(iso, 'boot_elf_name') else '?'})")
    if len(seen) < 2:
        return None
    return ("warning: these discs are different builds of the game and should not be combined: "
            + "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(seen.items()))
            + ". Chain 0 is taken from the first image and the text layouts differ; "
              "give Disc 1 and Disc 2 of one region.")


def warn_mixed(isos: Iterable) -> bool:
    """Print the mixed-region warning to stderr; True if there was one."""
    msg = mixed_regions(isos)
    if msg:
        print(msg, file=sys.stderr)
    return bool(msg)


# ------------------------------------------------------ executable addresses

class ElfError(ValueError):
    pass


def elf_segments(elf: bytes) -> List[Tuple[int, int, int]]:
    """(virtual address, file offset, file size) of every PT_LOAD program header.
    The file must be a 32-bit little-endian ELF whose program-header table and
    every loaded segment lie inside the file; anything else is an ElfError."""
    if len(elf) < 0x34 or elf[:4] != b"\x7fELF":
        raise ElfError("not an ELF")
    if elf[4] != 1 or elf[5] != 1:
        raise ElfError(f"not a 32-bit little-endian ELF (class {elf[4]}, data {elf[5]})")
    phoff = struct.unpack_from("<I", elf, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    if phnum and phentsize < 32:
        raise ElfError(f"program-header entries of {phentsize} bytes")
    if phoff + phentsize * phnum > len(elf):
        raise ElfError("the program-header table runs past the end of the file")
    segs = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<5I", elf, phoff + phentsize * i)
        if p_type == 1:
            if p_offset + p_filesz > len(elf):
                raise ElfError(f"segment {i} ({p_filesz:#x} bytes at file offset {p_offset:#x}) runs past "
                               f"the end of the {len(elf)}-byte file")
            segs.append((p_vaddr, p_offset, p_filesz))
    return segs


def va_to_offset(elf: bytes, va: int, length: int = 1) -> int:
    """The file offset of ``length`` bytes at virtual address ``va``; ElfError when the
    range is not backed by file bytes of one loaded segment (.bss, unmapped)."""
    for vaddr, offset, filesz in elf_segments(elf):
        if vaddr <= va and va + length <= vaddr + filesz:
            return offset + (va - vaddr)
    raise ElfError(f"VA {va:#x}+{length} is not in the file image of any PT_LOAD segment")


def read_va(elf: bytes, va: int, length: int) -> bytes:
    """Exactly ``length`` bytes at ``va``, or ElfError: never a short slice."""
    if length < 0:
        raise ElfError("negative length")
    o = va_to_offset(elf, va, max(length, 1))
    out = bytes(elf[o:o + length])
    if len(out) != length:
        raise ElfError(f"short read at VA {va:#x}: {len(out)} of {length} bytes")
    return out


def executable(iso) -> Tuple[str, bytes]:
    """(name, bytes) of the game executable on an image: the profile's ``exe`` when it
    names one (the demo), else the boot ELF."""
    prof = region_of(iso)
    name = prof.exe
    if name is not None and name not in iso.files:
        raise ElfError(f"this {prof.name} image has no {name}")
    if name is None:
        name = iso.boot_elf_name()
    if name is None:
        raise ElfError("no boot executable on this image")
    return name, bytes(iso.read_file(name))


def bank_path(script_path: str, lang: str = "en") -> str:
    """The USA string bank that belongs to a script path (cf\\sc1010.sb -> cf\\en\\sc1010.sbl)."""
    head, _, name = script_path.rpartition("\\")
    return f"{head}\\{lang}\\{name.rsplit('.', 1)[0]}.sbl"


def is_movie_subtitle(path: str) -> bool:
    """movie\\en\\X.txt (USA) or movie\\X.txt (Japan)."""
    low = path.lower()
    return low.startswith("movie\\") and low.endswith(".txt") and (
        low.count("\\") == 1 or (low.count("\\") == 2 and low.split("\\")[1] in ("en", "jp")))


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    check(bank_path("cf\\sc1010.sb") == "cf\\en\\sc1010.sbl", "scene bank path")
    check(is_movie_subtitle("movie\\en\\s010100.txt") and is_movie_subtitle("movie\\s010100.txt"),
          "both subtitle layouts")
    check(not is_movie_subtitle("movie\\cfevent\\x.txt"), "cfevent is not a subtitle folder")
    check(PROFILES["SLPS_253.68"] is PROFILES["SLPS_253.69"] is JP, "both JP discs share a profile")
    check(PROFILES["SLUS_208.92"] is PROFILES["SLUS_211.33"] is US, "both USA discs share a profile")

    class _Iso:                                     # a stand-in: only what region_of reads
        def __init__(self, boot):
            self.boot, self.files, self.path = boot, {}, boot

        def boot_elf_name(self):
            return self.boot

    check(mixed_regions([_Iso("SLUS_208.92"), _Iso("SLUS_211.33")]) is None, "USA Disc 1 + Disc 2 is one region")
    msg = mixed_regions([_Iso("SLPS_253.68"), _Iso("SLUS_211.33")])
    check(msg is not None and "jp" in msg and "us" in msg, "Japan Disc 1 + USA Disc 2 is flagged")
    unk = region_of(_Iso("SLES_999.99"))
    check(unk.name == "unknown" and unk.serial == "SLES_999.99" and unk.kanji_list_va is None
          and unk.lang_dir is None and not unk.fonts, "an unknown serial gets no known build's profile")
    try:
        require_known(_Iso("SLES_999.99"), "text-import")
        check(False, "require_known refuses an unknown serial")
    except UnknownBuild as e:
        check("SLES_999.99" in str(e) and "text-import" in str(e), "require_known refuses, naming the serial it saw")
    check(require_known(_Iso("SLUS_211.33"), "x") is US, "a known serial passes")
    # a two-segment ELF built here: VA -> file offset goes through the program headers
    ph = struct.pack("<8I", 1, 0x1000, 0x120000, 0x120000, 0x200, 0x200, 5, 0x1000) \
        + struct.pack("<8I", 1, 0x1300, 0x2B0000, 0x2B0000, 0x100, 0x900, 6, 0x1000)
    head = bytearray(0x34)
    head[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", head, 0x1C, 0x34)
    struct.pack_into("<HH", head, 0x2A, 32, 2)
    elf = bytes(head) + ph + bytes(0x1000 - 0x34 - 64) + bytes(range(256)) * 2 + bytes(0x100) + b"KANJI".ljust(0x100, b"\0")
    check(va_to_offset(elf, 0x120010) == 0x1010 and read_va(elf, 0x2B0000, 5) == b"KANJI", "VA maps through PT_LOAD")
    try:
        va_to_offset(elf, 0x2B0100, 4)              # inside memsz, past filesz: .bss
        check(False, "a .bss address is refused")
    except ElfError:
        check(True, "a .bss address is refused")

    def refused(blob, why):
        try:
            elf_segments(blob)
            check(False, f"ELF refused: {why}")
        except ElfError:
            check(True, f"ELF refused: {why}")

    big = bytearray(elf)
    big[4] = 2
    refused(bytes(big), "64-bit class")
    big = bytearray(elf)
    big[5] = 2
    refused(bytes(big), "big-endian")
    refused(elf[:0x40], "program headers past the end of the file")
    refused(elf[:0x1340], "a segment whose file bytes are cut off")
    big = bytearray(elf)
    struct.pack_into("<I", big, 0x34 + 32 + 16, 0x100000)       # second segment: p_filesz far too large
    refused(bytes(big), "a segment size larger than the file")
    try:
        read_va(elf, 0x2B00FE, 4)                   # two bytes inside the segment, two outside
        check(False, "a read across the end of a segment is an error")
    except ElfError:
        check(True, "a read across the end of a segment is an error, not a short slice")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print(__doc__)
