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


US = Region("us", "en", "sbl", ("font0.us", "font1.us"), "yamamoto\\batdat.us", "carddata.bin",
            kanji_list_va=0x63E870)
JP = Region("jp", None, "inline", ("font0.tex", "font1.tex"), "yamamoto\\batdat.bin", "carddata.new",
            kanji_list_va=0x63A2A0)
DEMO = Region("demo", None, "inline", ("font0.tex", "font1.tex"), "yamamoto\\batdat.bin", "carddata.new",
              exe="EXP\\EXP.ELF")

PROFILES = {
    "SLUS_208.92": US,          # Disc 1
    "SLUS_211.33": US,          # Disc 2 (byte-identical executable)
    "SLPS_253.68": JP,          # Disc 1
    "SLPS_253.69": JP,          # Disc 2 (byte-identical executable)
    "SLPS_253.53": DEMO,        # Xenosaga Freaks (EXP\\ chain 0)
}


def region_of(iso) -> Region:
    """The profile for an IsoImage, by boot executable; unknown discs fall back on
    what their chain 0 holds (any ``\\en\\`` folder = USA layout)."""
    name = iso.boot_elf_name() if hasattr(iso, "boot_elf_name") else None
    if name in PROFILES:
        return PROFILES[name]
    return US if any("\\EN\\" in p.upper() for p in getattr(iso, "files", {})) else JP


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
    """(virtual address, file offset, file size) of every PT_LOAD program header."""
    if elf[:4] != b"\x7fELF" or len(elf) < 0x34:
        raise ElfError("not an ELF")
    phoff = struct.unpack_from("<I", elf, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x2A)
    segs = []
    for i in range(phnum):
        p_type, p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<5I", elf, phoff + phentsize * i)
        if p_type == 1:
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
    o = va_to_offset(elf, va, length)
    return bytes(elf[o:o + length])


def executable(iso) -> Tuple[str, bytes]:
    """(name, bytes) of the game executable on an image: the profile's ``exe`` when it
    names one (the demo), else the boot ELF."""
    name = region_of(iso).exe
    if name is None or name not in iso.files:
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
    # a two-segment ELF built here: VA -> file offset goes through the program headers
    ph = struct.pack("<8I", 1, 0x1000, 0x120000, 0x120000, 0x200, 0x200, 5, 0x1000) \
        + struct.pack("<8I", 1, 0x1300, 0x2B0000, 0x2B0000, 0x100, 0x900, 6, 0x1000)
    head = bytearray(0x34)
    head[:4] = b"\x7fELF"
    struct.pack_into("<I", head, 0x1C, 0x34)
    struct.pack_into("<HH", head, 0x2A, 32, 2)
    elf = bytes(head) + ph + bytes(0x1000 - 0x34 - 64) + bytes(range(256)) * 2 + bytes(0x100) + b"KANJI".ljust(0x100, b"\0")
    check(va_to_offset(elf, 0x120010) == 0x1010 and read_va(elf, 0x2B0000, 5) == b"KANJI", "VA maps through PT_LOAD")
    try:
        va_to_offset(elf, 0x2B0100, 4)              # inside memsz, past filesz: .bss
        check(False, "a .bss address is refused")
    except ElfError:
        check(True, "a .bss address is refused")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print(__doc__)
