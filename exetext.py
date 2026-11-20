"""exetext.py — the strings inside the executables (the boot ELF, OV01.OVL, OV02.OVL), in place.

Borrowed from Xenosaga1PythonExtractor/ovltext.py (2026-10-15): the reference
scan (pointer words in data, ``lui``/``addiu|ori|load`` pairs in code) and the
slot rule come from there. Re-targeted to Episode II, and in place only: no
string is ever moved. The boot ELF has a bss after its data, and how the
Episode II overlays could grow has not been studied, so this module never
relocates.

Episode II keeps most of its UI words in ``.txd`` menus and ``batdat.us``,
which ``text-export`` already lists. A few displayed strings are still C
literals in the code (read from SLUS_208.92 on 2026-11-20): ``Saving...``
at 0x0068B678, drawn by the save screen FUN_00237830 under
``$init;$bg0;$zoom13;$han1;$prop1;...``; ``No items to sell.`` at
0x0068D430; ``Total Exp``, ``Class Lv``, ``Next Lv``, ``Zone``; the
memory-card and disc-change prompts in the boot ELF and OV02. They go
through the same print function as all other text (FUN_00197bb8, the byte
reading of ``font.us_two_byte``), so the tree uses the kit's USA codec.

**What is listed.** A string is a NUL-terminated run of at least 2 bytes,
each 0x20-0x7E, 0x09, 0x0A or from 0x80 up, with an ASCII letter, digit or
sign in it or reading as Japanese (EUC-JP / Shift-JIS developer strings,
marked); bytes 0x80-0x9F only in a Japanese reading, other control bytes
only in a run with three ASCII letters (the title overlay's memory-card
prompts carry 0x19 0x03 and 0x0C codes). And the code points at it:

* a 32-bit word in a data part equal to the string's address (a pointer in a
  table or a struct);
* a ``lui rX,hi`` and then an ``addiu``, ``ori``, ``daddiu`` or byte
  load/store with base rX whose address is the string's. The ``lui`` is
  followed down the straight path: through the delay slot and the
  fall-through of a conditional branch, saved registers (s0-s8) across
  calls, until rX is written or an unconditional jump or ``jr`` ends the
  path. As a fallback (Episode I's ``last_lui``), a ``lui`` reached only
  through a label (switch arms sharing one ``lui``, a ``lui`` in a likely
  branch's slot) counts when the address is exactly a string's start;
* an ``addiu``/byte load with base ``$gp`` (gp read from the boot ELF's
  ``.reginfo``, 0x006A2DF0 on the USA build): ``Next Lv`` and ``Zone`` live in
  ``.sdata``.

Word and float loads and stores form addresses too: those end slots but do
not make a string. A segment flagged executable and not writable is code, one
not executable is data. The overlays are one RWX segment each (one section,
code then data): their code is taken to end after the last ``jr ra``. A
function's first instruction (``addiu sp,sp,-N``) and ``jal`` targets are
never strings. A data word or an instruction can still look like a reference
by chance: such a false hit only adds a listed string or ends a neighbour's
slot early, never the other way round.

Cross-checked 2026-11-20 against the Ghidra listing of the boot ELF (1,106
strings): 24 are not found, all developer error messages whose ``lui`` sits
on another path with the register reused in between. Every string of the
OV01 and OV02 listings is found.

**Which file owns an address.** OV01.OVL and OV02.OVL both load at
0x00A80000, and the boot ELF carries a third segment at the same address (its
bytes equal OV02.OVL's, compared 2026-11-20): the copy resident at boot. So
a reference from the boot ELF's main code into that range cannot say which
overlay it means, and is ignored. Overlay code may point into the boot ELF's
main data, and those strings are the boot ELF's. A string of the resident
segment that also sits, byte for byte, at the same address of an overlay file
is listed in both trees; an edit must give both the same text (which copy a
given screen draws from has not been read).

**The slot** of a string runs from its first byte to the earliest of the
next non-zero byte, the next address any code or pointer forms, the start of
a font table the kit reads (``region.US``: ASCII table, extra-kanji list,
width table), and the end of the segment's file bytes. ``max N`` in the tree
is that length minus the terminator. The NUL padding after a string is taken
to be free; a variable reached only through a base address below it would
not be seen by the scan.

**Tree.** ``xs2-exe-export.json`` and one block file per executable::

    # exetext 1 file=SLUS_208.92 role=boot sha256=1f978e64...
    ### @0x0068b678 max 15 bytes
    # refs 1 (hilo 0x002379c4)
    Saving...

``BOOT.exe.txt`` is the boot ELF whatever its serial, so a Disc 1 tree
applies to Disc 2 (byte-identical executable). Blocks are the
``textpack.parse_blocks`` form: every block back once, in order.

**Import** writes each changed string over its own bytes and fills the rest
of the slot with NUL; the file size never changes. The disc's file must be
the one the tree was exported from (manifest sha256). ``--retail`` takes the
base bytes from a retail disc instead and applies every edited string of the
tree to them, so an image that is already exe-patched can be done again.
After an import the boot ELF is no longer the retail one, so every command
that reads its tables (text-import, font-extract, lang-scan) refuses the
image: exe-import is the last step.

    python exetext.py export DISC.iso OUTDIR
    python exetext.py import DISC.iso OUTDIR OUT.iso [--retail RETAIL.iso]
    python exetext.py --self-test
"""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region                                   # noqa: E402
import textcodec                                # noqa: E402
from iso9660 import IsoImage                    # noqa: E402
from textcodec import TextError                 # noqa: E402
from textpack import keyed_bodies, parse_blocks  # noqa: E402

MANIFEST = "xs2-exe-export.json"
FORMAT = 1
BOOT_TREE = "BOOT.exe.txt"
MIN_LEN = 2

PT_LOAD = 1
PT_MIPS_REGINFO = 0x70000000
PF_X, PF_W = 1, 2
GP = 28

# sha256 of the retail USA overlays (Disc 1, read 2026-11-20); --retail accepts these
RETAIL_OVERLAYS = {
    "OV01.OVL": "420e78108c3d1fdb2a44be6f74b632fd88e436e6b854b6e7e0929d17183fe9b0",
    "OV02.OVL": "709c09c4ab27770e0e84bf360aeb5990d7e89d90bd52a0bd3adb787087f36bd3",
}


class ExeError(ValueError):
    pass


# ------------------------------------------------------------------ MIPS bits

_USE_OPS = {0x09, 0x0D, 0x19, 0x1A, 0x1B, 0x1E, 0x1F, 0x31, 0x35, 0x36, 0x39, 0x3D, 0x3E, 0x3F} \
    | set(range(0x20, 0x30)) | {0x37}
_RT_WRITERS = set(range(0x08, 0x10)) | {0x18, 0x19, 0x1A, 0x1B, 0x1E, 0x37} | set(range(0x20, 0x28))


_TEMPS = tuple(range(1, 16)) + (24, 25, 31)
_TEXT_OPS = {0x09, 0x0D, 0x19, 0x20, 0x24, 0x28}       # addiu, ori, daddiu, lb, lbu, sb
WEAK_REACH = 0x1000          # bytes from a lui to a use reached only through a label


def _branch_kind(w: int) -> Optional[str]:
    """None, or what happens after this instruction's delay slot: "cond" (the fall-through
    continues), "likely" (the slot runs only when taken), "jump", "call" or "ret"."""
    op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
    if op == 0:
        f = w & 0x3F
        if f == 0x08:
            return "ret" if rs == 31 else "jump"
        return "call" if f == 0x09 else None
    if op == 0x02:
        return "jump"
    if op == 0x03:
        return "call"
    if op == 0x01:
        if 0x10 <= rt <= 0x13:
            return "call"
        if rt in (0x01, 0x03) and rs == 0:                 # bgez $0: b
            return "jump"
        return "likely" if rt in (0x02, 0x03, 0x12, 0x13) else "cond"
    if op == 0x04 and rs == 0 and rt == 0:                 # beq $0,$0: b
        return "jump"
    if op in (0x04, 0x05, 0x06, 0x07):
        return "cond"
    if op in (0x14, 0x15, 0x16, 0x17):
        return "likely"
    if op in (0x10, 0x11, 0x12) and rs == 8:               # bc0/bc1/bc2 (t, f, tl, fl)
        return "likely" if rt & 2 else "cond"
    return None


def _writes(w: int) -> Optional[int]:
    """The general register an instruction writes, as far as the scan cares."""
    op, rs, rt, rd = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    if op == 0:
        return None if (w & 0x3F) == 0x08 else rd
    if op == 0x1C:                                         # MMI
        return rd
    if op in _RT_WRITERS:
        return rt
    if op == 0x03 or (op == 0x01 and 0x10 <= rt <= 0x13):  # jal, bltzal & co
        return 31
    if op in (0x10, 0x11, 0x12) and rs in (0, 1, 2):       # mfc/dmfc/cfc
        return rt
    return None


def _addr(hi: int, w: int) -> int:
    imm = w & 0xFFFF
    if (w >> 26) == 0x0D:                                  # ori: zero-extended
        return ((hi << 16) | imm) & 0xFFFFFFFF
    return ((hi << 16) + (imm - 0x10000 if imm & 0x8000 else imm)) & 0xFFFFFFFF


# ------------------------------------------------------------------ one ELF

@dataclass
class Seg:
    va: int
    off: int
    filesz: int
    flags: int
    resident: bool = False     # the boot ELF's copy of an overlay's address range
    code_end: int = 0          # VA where the code part ends (see Exe.__init__)

    def has(self, va: int) -> bool:
        return self.va <= va < self.va + self.filesz


@dataclass
class Ref:
    kind: str                  # "hilo" | "gp" | "ptr" | "call" (a jal target: code, never a string)
    at: int                    # VA of the use instruction or the pointer word
    file: str                  # the file the reference is in
    weak: bool = False         # a lui reached only through a label: counts for a string start only
    text: bool = True          # forms the address or reads a byte; False for word/float loads and stores


@dataclass
class ExeString:
    va: int
    off: int
    raw: bytes
    max: int                   # bytes of text the slot holds (the terminator not counted)
    refs: List[Ref] = field(default_factory=list)
    twins: List[Tuple[str, int]] = field(default_factory=list)   # (file, va): the same bytes elsewhere


class Exe:
    """One ELF file: its loaded segments and every address its code and data form."""

    def __init__(self, name: str, data: bytes, role: str, gp: Optional[int] = None):
        self.name, self.data, self.role = name, bytes(data), role
        try:
            region.elf_segments(self.data)                 # validates the headers
        except region.ElfError as e:
            raise ExeError(f"{name}: {e}")
        phoff = struct.unpack_from("<I", self.data, 0x1C)[0]
        phentsize, phnum = struct.unpack_from("<HH", self.data, 0x2A)
        self.segs: List[Seg] = []
        self.gp = gp
        for i in range(phnum):
            p_type, p_off, p_va, _pa, p_filesz, _mem, p_flags = struct.unpack_from("<7I", self.data,
                                                                                   phoff + phentsize * i)
            if p_type == PT_LOAD and p_filesz:
                seg = Seg(p_va, p_off, p_filesz, p_flags)
                seg.code_end = self._code_end(seg)
                self.segs.append(seg)
            elif p_type == PT_MIPS_REGINFO and p_filesz >= 24 and gp is None:
                self.gp = struct.unpack_from("<I", self.data, p_off + 20)[0]
        self.sha256 = hashlib.sha256(self.data).hexdigest()

    def _code_end(self, seg: Seg) -> int:
        """A segment flagged executable and not writable is all code, one not executable is
        all data. The overlays are one RWX segment each, code first and data after (their
        single section ``ov01``/``ov02``): the code is taken to end after the last
        ``jr ra`` and its delay slot."""
        if not seg.flags & PF_X:
            return seg.va
        if not seg.flags & PF_W:
            return seg.va + seg.filesz
        i = seg.off + seg.filesz - 4
        i -= (seg.va + i - seg.off) % 4
        while i >= seg.off:
            if struct.unpack_from("<I", self.data, i)[0] == 0x03E00008:
                return min(seg.va + i - seg.off + 8, seg.va + seg.filesz)
            i -= 4
        return seg.va

    def seg_of(self, va: int) -> Optional[Seg]:
        return next((s for s in self.segs if s.has(va)), None)

    def off_of(self, va: int) -> int:
        s = self.seg_of(va)
        if s is None:
            raise ExeError(f"{self.name}: {va:#010x} is not in the file bytes of a loaded segment")
        return s.off + va - s.va

    def scan(self) -> List[Tuple[int, Ref, Seg]]:
        """(address formed, reference, segment the reference sits in), every one."""
        out: List[Tuple[int, Ref, Seg]] = []
        d = self.data
        for seg in self.segs:
            start = seg.off + (-seg.va % 4)
            end = seg.off + seg.filesz - 3
            split = seg.off + seg.code_end - seg.va
            for off in range(max(start, split + (-seg.code_end % 4)), end, 4):      # data: pointer words
                out.append((struct.unpack_from("<I", d, off)[0],
                            Ref("ptr", seg.va + off - seg.off, self.name), seg))
            end = min(end, split)
            pending: Dict[int, int] = {}          # reg -> hi16 on the straight path (strong)
            last: Dict[int, Tuple[int, int]] = {}  # reg -> (hi16, lui VA): the fallback (weak)
            after = None                           # what the branch before this instruction does
            for off in range(start, end, 4):
                w = struct.unpack_from("<I", d, off)[0]
                va = seg.va + off - seg.off
                op, rs, rt = w >> 26, (w >> 21) & 31, (w >> 16) & 31
                if op == 0x03:
                    out.append((((va + 4) & 0xF0000000) | ((w & 0x3FFFFFF) << 2),
                                Ref("call", va, self.name, text=False), seg))
                elif op in _USE_OPS:
                    text = op in _TEXT_OPS
                    if rs in pending:
                        out.append((_addr(pending[rs], w), Ref("hilo", va, self.name, text=text), seg))
                    elif rs == GP and self.gp is not None and op != 0x0D:
                        out.append(((self.gp + ((w & 0xFFFF) ^ 0x8000) - 0x8000) & 0xFFFFFFFF,
                                    Ref("gp", va, self.name, text=text), seg))
                    elif rs in last and va - last[rs][1] <= WEAK_REACH and text:
                        out.append((_addr(last[rs][0], w), Ref("hilo", va, self.name, weak=True), seg))
                wr = rt if op == 0x0F else _writes(w)
                if wr:
                    lo_add = op in (0x09, 0x0D) and rs == rt
                    if op == 0x0F:
                        if after != "likely":       # a likely slot runs only on the taken path
                            pending[rt] = w & 0xFFFF
                        else:
                            pending.pop(rt, None)
                        last[rt] = (w & 0xFFFF, va)
                    else:
                        pending.pop(wr, None)
                        if not lo_add and wr in last and op != 0x0F:
                            del last[wr]
                if after == "jump":                 # the delay slot ran; what follows is a label
                    pending.clear()
                elif after == "call":               # the callee clobbers the temporaries
                    for r in _TEMPS:
                        pending.pop(r, None)
                elif after == "ret":                # end of a function
                    pending.clear()
                    last.clear()
                after = _branch_kind(w)
        return out


def _textlike(raw: bytes) -> bool:
    """At least 2 bytes, each 0x20-0x7E, 0x09, 0x0A or from 0x80 up; and either one printable
    ASCII byte or the whole run reading as EUC-JP with kana or kanji (a developer string),
    so float constants and code bytes are not taken for text."""
    if len(raw) < MIN_LEN:
        return False
    if not all(0x20 <= b <= 0x7E or b in (0x09, 0x0A) or b >= 0x80 for b in raw):
        # other control bytes: the title and memory-card screens (OV02) carry 0x19 0x03 and
        # 0x0C codes in their prompts; taken only with three ASCII letters in the run
        if 0x7F in raw or sum(0x41 <= (b | 0x20) - 0x20 <= 0x5A for b in raw if b < 0x80) < 3:
            return False
    if any(0x80 <= b <= 0x9F for b in raw):           # never text on the USA build, but Shift-JIS leads
        return bool(_euc_hint(raw))
    return any(0x21 <= b <= 0x7E for b in raw) or bool(_euc_hint(raw))


def _string_at(exe: "Exe", seg: "Seg", va: int) -> Optional[bytes]:
    """The string starting at ``va``, or None: never in a code-only segment, never at a
    function's first instruction (``addiu sp,sp,-N``), and it must look like text."""
    if va < seg.code_end:
        return None
    off = seg.off + va - seg.va
    j = exe.data.find(b"\0", off, seg.off + seg.filesz)
    if j < 0:
        return None
    if va % 4 == 0 and off + 4 <= len(exe.data):
        w = struct.unpack_from("<I", exe.data, off)[0]
        if w >> 16 == 0x27BD and w & 0x8000:
            return None
    raw = exe.data[off:j]
    return raw if _textlike(raw) else None


def font_table_ranges(boot: bytes, prof) -> List[Tuple[int, int]]:
    """VA ranges of the font tables the kit reads from the boot ELF (``region`` profile)."""
    out = []
    if prof.ascii_table_va is not None:
        out.append((prof.ascii_table_va, prof.ascii_table_va + 0x200))
    if prof.sys_width_va is not None:
        out.append((prof.sys_width_va, prof.sys_width_va + 4 * 0x80))
    if prof.kanji_list_va is not None:
        va = prof.kanji_list_va
        try:
            while region.read_va(boot, va, 2)[0] != 0 and va < prof.kanji_list_va + 0x4000:
                va += 2
        except region.ElfError:
            pass
        out.append((prof.kanji_list_va, va + 2))
    return out


class ExeSet:
    """The executables of one disc, scanned together: which strings each owns."""

    def __init__(self, files: List[Tuple[str, bytes, str]], prof=None):
        """``files``: (name, bytes, role) with role "boot" or "overlay"; at most one boot."""
        self.prof = prof or region.US
        boot = [f for f in files if f[2] == "boot"]
        self.boot: Optional[Exe] = Exe(*boot[0]) if boot else None
        gp = self.boot.gp if self.boot else None
        self.exes: Dict[str, Exe] = {}
        if self.boot:
            self.exes[self.boot.name] = self.boot
        for name, data, role in files:
            if role != "boot":
                self.exes[name] = Exe(name, data, role, gp=gp)
        overlays = [e for e in self.exes.values() if e.role != "boot"]
        if self.boot:
            for s in self.boot.segs:
                s.resident = any(o.va < s.va + s.filesz and s.va < o.va + o.filesz
                                 for ov in overlays for o in ov.segs)
        self.tables = font_table_ranges(self.boot.data, self.prof) if self.boot else []
        self.strings: Dict[str, Dict[int, ExeString]] = {}
        self._build()

    def _owner(self, target: int, exe: Exe, seg: Seg) -> Optional[Tuple[Exe, Seg]]:
        boot = self.boot
        main = (lambda va: next((s for s in boot.segs if not s.resident and s.has(va)), None)) if boot else \
            (lambda va: None)
        if exe.role != "boot":
            own = exe.seg_of(target)
            if own is not None:
                return exe, own
            m = main(target)
            return (boot, m) if m else None
        if seg.resident:
            if seg.has(target):
                return exe, seg
            m = main(target)
            return (exe, m) if m else None
        m = main(target)
        return (exe, m) if m else None

    def _build(self) -> None:
        targets: Dict[str, Dict[int, List[Ref]]] = {n: {} for n in self.exes}
        for exe in self.exes.values():
            for target, ref, seg in exe.scan():
                own = self._owner(target, exe, seg)
                if own is None:
                    continue
                if ref.weak and _string_at(own[0], own[1], target) is None:
                    continue
                targets[own[0].name].setdefault(target, []).append(ref)
        for name, exe in self.exes.items():
            tabs = self.tables if exe is self.boot else []
            bounds = sorted(set(targets[name]) | {a for a, _ in tabs})
            found: Dict[int, ExeString] = {}
            for va, refs in sorted(targets[name].items()):
                if any(a <= va < b for a, b in tabs):
                    continue
                if not any(r.text for r in refs) or any(r.kind == "call" for r in refs):
                    continue
                seg = exe.seg_of(va)
                raw = _string_at(exe, seg, va)
                if raw is None:
                    continue
                off = seg.off + va - seg.va
                seg_end = seg.off + seg.filesz
                k = off + len(raw)
                while k < seg_end and exe.data[k] == 0:
                    k += 1
                end = k
                i = bisect.bisect_right(bounds, va)
                if i < len(bounds) and bounds[i] < seg.va + seg.filesz:
                    end = min(end, seg.off + bounds[i] - seg.va)
                found[va] = ExeString(va, off, raw, end - off - 1,
                                      sorted((r for r in refs if r.text),
                                             key=lambda r: (r.file != name, r.at, r.kind)))
            self.strings[name] = found
        # the boot ELF's resident overlay copy and the overlay file: the same string twice
        if self.boot:
            for va, s in self.strings[self.boot.name].items():
                seg = self.boot.seg_of(va)
                if not seg.resident:
                    continue
                for name, strs in self.strings.items():
                    t = strs.get(va)
                    if name != self.boot.name and t is not None and t.raw == s.raw:
                        s.twins.append((name, va))
                        t.twins.append((self.boot.name, va))

    # -- text ------------------------------------------------------------

    def tree_text(self, name: str) -> str:
        exe = self.exes[name]
        codec = textcodec.UsCodec()
        lines = [f"# exetext 1 file={name} role={exe.role} sha256={exe.sha256}",
                 "# Each '### @ADDRESS max N bytes' block is one string the code points at. Edit the line under",
                 "# it, in place: at most N bytes as the game stores them (one byte per ASCII or Latin-1",
                 "# letter, one per escape). A real line break is \u27e60A\u27e7. Keep every block, in order.",
                 "# exe-import must be the last step: afterwards this disc's executable is not the retail one."]
        for va, s in sorted(self.strings[name].items()):
            lines.append(f"### @0x{va:08x} max {s.max} bytes")
            shown = ", ".join(f"{r.kind} {'' if r.file == name else r.file + ' '}0x{r.at:08x}" for r in s.refs[:4])
            more = ", ..." if len(s.refs) > 4 else ""
            lines.append(f"# refs {len(s.refs)} ({shown}{more})")
            if s.max < len(s.raw):
                lines.append(f"# the code also points inside this string, {s.max} bytes in: the slot ends there")
            for f, a in s.twins:
                lines.append(f"# the same string is in {f} at 0x{a:08x}: give both the same text")
            if len(s.raw) == 3 and va % 4 == 0 and exe.seg_of(struct.unpack_from("<I", exe.data, s.off)[0]):
                lines.append("# probably not text: these 4 bytes are also an address in this file (a pointer)")
            hint = _euc_hint(s.raw)
            if hint:
                lines.append(f"# a developer string? reads as {hint}")
            lines.append(codec.to_text(s.raw))
        return "\n".join(lines) + "\n"


def _euc_hint(raw: bytes) -> str:
    """The run read as EUC-JP or Shift-JIS when that gives kana or kanji (a developer
    string), else ""."""
    if not any(b >= 0x81 for b in raw):
        return ""
    for enc in ("euc_jp", "shift_jis"):
        try:
            t = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        jp = sum(0x3040 <= ord(c) <= 0x30FF or 0x4E00 <= ord(c) <= 0x9FFF or 0xFF61 <= ord(c) <= 0xFF9F for c in t)
        if jp >= 2 and 2 * jp >= len(t.replace(" ", "")):
            return f"{enc.replace('_', '-').upper()}: " + t.replace("\n", "\\n").replace("\t", " ")
    return ""


def tree_name(name: str, role: str) -> str:
    return BOOT_TREE if role == "boot" else f"{name}.exe.txt"


# ------------------------------------------------------------------ disc

def disc_executables(iso: IsoImage) -> List[Tuple[str, str]]:
    """(name, role) of the boot ELF and every root .OVL, in that order."""
    boot = iso.boot_elf_name()
    ovl = sorted(n for n, f in iso.files.items()
                 if not f.is_dir and "\\" not in n and n.upper().endswith(".OVL"))
    return ([(boot, "boot")] if boot else []) + [(n, "overlay") for n in ovl]


def _us_only(iso: IsoImage, what: str):
    prof = region.require_known(iso, what)
    if prof.name != "us":
        raise region.UnknownBuild(f"{what} is refused: it is written for the USA build (SLUS_208.92 / SLUS_211.33); "
                                  f"this image is the {prof.name} build ({iso.boot_elf_name()}), whose "
                                  f"executables and glyph reading have not been studied for it")
    return prof


def export(iso_path, out_dir) -> int:
    root = Path(out_dir)
    iso = IsoImage(iso_path)
    try:
        try:
            prof = _us_only(iso, "exe-export")
        except region.UnknownBuild as e:
            print(f"refused: {e}", file=sys.stderr)
            return 1
        files = [(n, bytes(iso.read_file(n)), role) for n, role in disc_executables(iso)]
    finally:
        iso.close()
    try:
        es = ExeSet(files, prof)
    except ExeError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    mpath = root / MANIFEST
    if mpath.exists():
        try:
            old = json.loads(mpath.read_text(encoding="utf-8"))
        except ValueError:
            old = None
        if not old or old.get("format") != FORMAT or old.get("build") != "us":
            print(f"refused: {mpath} is not an exetext {FORMAT} manifest of the USA build; export into an "
                  f"empty folder", file=sys.stderr)
            return 1
    manifest = {"format": FORMAT, "build": "us", "files": {}}
    kept, total = [], 0
    root.mkdir(parents=True, exist_ok=True)
    for name, exe in es.exes.items():
        tname = tree_name(name, exe.role)
        text = es.tree_text(name)
        dest = root / tname
        manifest["files"][tname] = {"name": name, "role": exe.role, "sha256": exe.sha256}
        n = len(es.strings[name])
        total += n
        print(f"{tname} ({name}): {n} strings", file=sys.stderr)
        if dest.exists():
            with open(dest, encoding="utf-8-sig", newline="") as fh:
                if fh.read() != text:
                    kept.append(tname)
                    continue
        with open(dest, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    if kept:
        print(f"refused to overwrite {len(kept)} file(s) in {root} that differ from this disc's strings (edited, "
              f"or another export): {', '.join(kept)}. The manifest was not written either. Export into an "
              f"empty folder for a fresh copy", file=sys.stderr)
        return 1
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"exported {total} strings from {len(es.exes)} executable(s) to {root}", file=sys.stderr)
    return 0


def _plan(iso_path, tree, retail_iso=None) -> Tuple[Dict[str, bytes], List[str], List[str]]:
    """({disc file name: new bytes}, problems, log)."""
    root = Path(tree)
    problems: List[str] = []
    log: List[str] = []
    mpath = root / MANIFEST
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, [f"{mpath} is missing or unreadable; run exe-export first"], log
    if manifest.get("format") != FORMAT or manifest.get("build") != "us" or not isinstance(manifest.get("files"), dict):
        return {}, [f"{mpath} is not an exetext {FORMAT} manifest of the USA build"], log
    iso = IsoImage(iso_path)
    retail = IsoImage(retail_iso) if retail_iso else None
    try:
        try:
            prof = _us_only(iso, "exe-import")
            if retail is not None:
                _us_only(retail, "exe-import --retail")
        except region.UnknownBuild as e:
            return {}, [str(e)], log
        on_disc = dict((role, n) if role == "boot" else (n, n) for n, role in disc_executables(iso))
        r_disc = dict((role, n) if role == "boot" else (n, n) for n, role in disc_executables(retail)) \
            if retail is not None else {}
        files, disc_names, current = [], {}, {}
        for tname, rec in sorted(manifest["files"].items()):
            role, name = rec.get("role"), rec.get("name")
            key = "boot" if role == "boot" else name
            dname = on_disc.get(key)
            if dname is None:
                problems.append(f"{tname}: {name} is not on this disc")
                continue
            cur = bytes(iso.read_file(dname))
            if retail is not None:
                rname = r_disc.get(key)
                if rname is None:
                    problems.append(f"{tname}: {name} is not on the retail disc {retail_iso}")
                    continue
                base = bytes(retail.read_file(rname))
                sha = hashlib.sha256(base).hexdigest()
                ok = (sha == prof.exe_sha256 or sha in region.TEST_EXECUTABLES) if role == "boot" else \
                    sha in (rec.get("sha256"), RETAIL_OVERLAYS.get(name))
                if not ok:
                    problems.append(f"{tname}: {rname} on {retail_iso} is not the retail file (sha256 {sha[:16]}...)")
                    continue
                if len(base) != len(cur):
                    problems.append(f"{tname}: {dname} on this disc is {len(cur)} bytes, the retail one {len(base)}; "
                                    f"exe-import writes in place and never changes a file's size")
                    continue
            else:
                base = cur
                sha = hashlib.sha256(base).hexdigest()
                if sha != rec.get("sha256"):
                    problems.append(f"{tname}: {dname} on this disc is not the file this tree was exported from "
                                    f"(sha256 {sha[:16]}..., exported {str(rec.get('sha256'))[:16]}...). If the disc "
                                    f"was exe-patched before, pass --retail RETAIL.iso to start again from the retail "
                                    f"bytes")
                    continue
            files.append((name, base, role))
            disc_names[name] = (tname, dname)
            current[name] = cur
        if problems:
            return {}, problems, log
        try:
            es = ExeSet(files, prof)
        except ExeError as e:
            return {}, [str(e)], log
        codec = textcodec.UsCodec()
        new_bytes: Dict[str, bytearray] = {}
        encoded: Dict[Tuple[str, int], bytes] = {}
        checker = _TypedCheck(iso, es)
        for name, exe in es.exes.items():
            tname, dname = disc_names[name]
            try:
                with open(root / tname, encoding="utf-8-sig", newline="") as fh:
                    text = fh.read()
            except OSError as e:
                problems.append(f"{tname}: {e}")
                continue
            head = text.split("\n", 1)[0].split()
            fields = dict(t.split("=", 1) for t in head[3:] if "=" in t)
            if head[:3] != ["#", "exetext", "1"] or fields.get("file") != name or fields.get("role") != exe.role:
                problems.append(f"{tname}: the first line must be '# exetext 1 file={name} role={exe.role} ...'")
                continue
            strs = es.strings[name]
            order = sorted(strs)
            try:
                bodies = keyed_bodies(text, [f"@0x{va:08x}" for va in order])
            except TextError as e:
                problems.append(f"{tname}: {e}")
                continue
            heads = [h for h, _ in parse_blocks(text)]
            buf = bytearray(exe.data)
            for va, h, body in zip(order, heads, bodies):
                s = strs[va]
                key = f"{tname} @0x{va:08x}"
                if h.split()[1:] != ["max", str(s.max), "bytes"]:
                    problems.append(f"{key}: the header says '{h}', but this executable gives the string "
                                    f"max {s.max} bytes; keep the header as exported")
                    continue
                if body == codec.to_text(s.raw):
                    continue
                try:
                    raw = codec.from_text(body)
                except TextError as e:
                    problems.append(f"{key}: {e}")
                    continue
                if b"\0" in raw:
                    problems.append(f"{key}: a NUL byte (\u27e600\u27e7) inside a string; the game would stop there")
                    continue
                if len(raw) > s.max:
                    problems.append(f"{key}: {len(raw)} bytes, but its slot holds max {s.max} bytes "
                                    f"(0x{va:08x}-0x{va + s.max:08x} with the terminator); shorten it by "
                                    f"{len(raw) - s.max}")
                    continue
                why = checker.check(codec, body)
                if why:
                    problems.append(f"{key}: {why}. Write \u27e6XX\u27e7 escapes if these bytes are meant")
                    continue
                buf[s.off:s.off + s.max + 1] = raw + bytes(s.max + 1 - len(raw))
                encoded[(name, va)] = raw
                log.append(f"{dname} 0x{va:08x}: {len(s.raw)} -> {len(raw)} of {s.max} bytes")
            new_bytes[name] = buf
        # the same string in the boot ELF's resident segment and in an overlay file
        for name, strs in es.strings.items():
            for va, s in strs.items():
                for other, ova in s.twins:
                    if name < other:
                        a = encoded.get((name, va), s.raw)
                        b = encoded.get((other, ova), es.strings[other][ova].raw)
                        if a != b:
                            problems.append(f"@0x{va:08x}: {disc_names[name][0]} and {disc_names[other][0]} hold the "
                                            f"same string and now differ; give both blocks the same text")
        if problems:
            return {}, problems, log
        out = {}
        for name, buf in new_bytes.items():
            if len(buf) != len(es.exes[name].data):
                return {}, [f"{name}: internal error, the size changed"], log
            if bytes(buf) != current[name]:
                out[disc_names[name][1]] = bytes(buf)
        return out, problems, log
    finally:
        iso.close()
        if retail is not None:
            retail.close()


class _TypedCheck:
    """textcodec.check_typed with the fonts of the disc being patched (read through its TOC)
    and the addressing of the base boot ELF (retail bytes: the image's own may be patched)."""

    def __init__(self, iso: IsoImage, es: ExeSet):
        self.iso, self.es = iso, es
        self._ready = None

    def _load(self):
        import font
        from chains import CHAINS, ChainReader
        from toc import parse_toc
        if self.es.boot is None:
            raise TextError("no boot executable to read the font tables from")
        try:
            adr = font.Addressing.from_executable(self.es.boot.data, self.es.prof)
            reader = ChainReader(self.iso, 0)
            entries = {e.path.lower(): e for e in parse_toc(bytes(reader.toc_bytes()), CHAINS[0][0])}
            fonts = {n: font.Font.from_bytes(reader.read(entries[n].byte_offset, entries[n].size))
                     for n in self.es.prof.fonts if n in entries}
        except (font.FontError, KeyError, ValueError) as e:
            raise TextError(f"the fonts of this disc cannot be read, so typed text cannot be checked: {e}")
        if not fonts:
            raise TextError("this disc has no font file to check typed text against")
        return adr, fonts

    def check(self, codec, text: str) -> Optional[str]:
        if all(ch == "" or ord(ch) < 0x80 for ch, _ in textcodec.tokens(text)):
            return None
        try:
            if self._ready is None:
                self._ready = self._load()
        except TextError as e:
            return str(e)
        return textcodec.check_typed(codec, text, *self._ready)


def plan(iso_path, tree, retail_iso=None) -> Tuple[Dict[str, bytes], List[str]]:
    changes, problems, _ = _plan(iso_path, tree, retail_iso)
    return changes, problems


ORDER_WARNING = ("WARNING: the executable on {out} is no longer the retail one, so text-import, font-extract, "
                 "recolor with text and lang-scan will refuse this image. Run exe-import last; for another round, "
                 "import into the image again with --retail RETAIL.iso (the retail disc), which starts from the "
                 "retail bytes and applies the whole tree")


def import_(iso_path, tree, out_iso, retail_iso=None, verbose: bool = True) -> int:
    from repack import RepackError, patch_copy, same_file
    if same_file(iso_path, out_iso):
        print(f"refused: --out {out_iso} is the input image itself; the kit never patches its source", file=sys.stderr)
        return 1
    try:
        changes, problems, log = _plan(iso_path, tree, retail_iso)
    except ExeError as e:
        changes, problems, log = {}, [str(e)], []
    for p in problems:
        print("refused: " + p, file=sys.stderr)
    if problems:
        print(f"{len(problems)} problem(s); nothing was written", file=sys.stderr)
        return 1
    for line in log:
        print(line, file=sys.stderr)
    if not changes:
        print("no string changed; nothing written", file=sys.stderr)
        return 0
    try:
        if verbose:
            patch_copy(iso_path, out_iso, {}, fs_replacements=changes)
        else:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                patch_copy(iso_path, out_iso, {}, fs_replacements=changes)
    except (RepackError, region.UnknownBuild) as e:
        print(f"refused: {e}; nothing was written", file=sys.stderr)
        return 1
    print(f"patched {len(log)} string(s) in {len(changes)} executable(s) into {out_iso}", file=sys.stderr)
    print(ORDER_WARNING.format(out=out_iso), file=sys.stderr)
    return 0


# ------------------------------------------------------------------ self-test

def _i(op, rs=0, rt=0, imm=0) -> int:
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def _hi(addr: int) -> int:
    return ((addr + 0x8000) >> 16) & 0xFFFF


TEXT_VA = 0x120000
DATA_VA = 0x63E000           # region.US font tables live here (font._synthetic_elf)
GP_VAL = 0x648000
OVL_VA = 0xA80000


def _test_layout():
    """Strings and code for the synthetic boot ELF: {name: VA} and the data bytes past the tables."""
    S = {"pair": 0x63F000, "ori": 0x63F010, "load": 0x63F020, "twice": 0x63F030, "padded": 0x63F040,
         "unref": 0x63F060, "ptr": 0x63F070, "inner": 0x63F080, "gp": 0x647FF0, "blocked": 0x63F0A0,
         "late": 0x63F0B0, "slot": 0x63F0C0, "saved": 0x63F0D0, "arm": 0x63F0E0,
         "ctl": 0x63F0F0, "noise": 0x647FE0}
    return S


def synthetic_boot() -> bytes:
    """A boot ELF built in code: text with lui/addiu, lui/ori, lui+lbu, a gp-relative addiu
    and a lui cut by a branch; data with the font tables of region.US (made-up contents),
    pointer words, padded, unreferenced and twice-referenced strings."""
    import font
    S = _test_layout()
    prof = region.US
    tab = font._synthetic_elf(prof.ascii_table_va, prof.kanji_list_va, (), prof.sys_width_va)
    seg = bytearray(tab[0x1000:])
    seg += bytes(0xA000 - len(seg))                          # data: 0x63E000..0x648000
    put = {"pair": b"Saving...", "ori": b"Total Exp", "load": b"Class Lv", "twice": b"Zone",
           "padded": b"No items to sell.", "unref": b"Unused", "ptr": b"Next Lv", "inner": b"Hello World",
           "gp": b"GP str", "blocked": b"Cut off", "late": b"\xa4\xa6Xy", "slot": b"Delay", "saved": b"Saved reg",
           "arm": b"Arm two", "ctl": b"\x19\x03Card OK", "noise": b"\x19\x03\x0cAB\x01"}
    for k, v in put.items():
        o = S[k] - DATA_VA
        seg[o:o + len(v)] = v
    # pointer words: 'twice' (its second reference) and 'ptr'; one inside 'inner' (+6 = "World")
    struct.pack_into("<III", seg, 0x63F100 - DATA_VA, S["twice"], S["ptr"], S["inner"] + 6)
    code = []
    a0, a1, t0, v0 = 4, 5, 8, 2
    code += [_i(0x0F, 0, a0, _hi(S["pair"])), 0, _i(0x09, a0, a0, S["pair"])]           # lui/addiu
    code += [_i(0x0F, 0, a1, S["ori"] >> 16), _i(0x0D, a1, a1, S["ori"])]                # lui/ori
    code += [_i(0x0F, 0, t0, _hi(S["load"])), _i(0x24, t0, v0, S["load"])]               # lui + lbu
    code += [_i(0x0F, 0, a0, _hi(S["twice"])), _i(0x09, a0, a0, S["twice"])]
    code += [_i(0x0F, 0, a0, _hi(S["padded"])), _i(0x09, a0, a1, S["padded"])]
    code += [_i(0x0F, 0, a0, _hi(S["inner"])), _i(0x09, a0, a0, S["inner"])]
    code += [_i(0x09, GP, a0, S["gp"] - GP_VAL)]                                         # gp-relative
    code += [_i(0x0F, 0, a0, _hi(S["ctl"])), _i(0x09, a0, a0, S["ctl"])]                 # control bytes
    code += [_i(0x0F, 0, a0, _hi(S["noise"])), _i(0x09, a0, a0, S["noise"])]             # ... too few letters
    code += [_i(0x0F, 0, a0, _hi(S["late"])), _i(0x09, a0, a0, S["late"])]
    code += [_i(0x0F, 0, a0, _hi(prof.ascii_table_va + 0x40)),                           # into the font table
             _i(0x09, a0, a0, prof.ascii_table_va + 0x40)]
    code += [_i(0x04, a0, a1, 2), _i(0x0F, 0, a0, _hi(S["slot"])),                        # lui in a delay slot
             _i(0x09, a0, a0, S["slot"])]
    code += [_i(0x0F, 0, 16, _hi(S["saved"])), 3 << 26, 0, _i(0x09, 16, a0, S["saved"])]  # s0 across a call
    code += [_i(0x0F, 0, a0, _hi(S["arm"])), _i(0x04, 0, 0, 2), 0,                        # b: the use is
             _i(0x09, a0, a0, S["arm"])]                                                  # behind a label
    code += [_i(0x0F, 0, a0, _hi(S["blocked"])), (31 << 21) | 8, 0,                       # jr ra: function ends
             _i(0x09, a0, a0, S["blocked"])]
    code += [_i(0x0F, 0, a0, _hi(S["unref"])), _i(0x0F, 0, a0, 0x1234),                   # a0 rewritten
             _i(0x09, a0, a0, S["unref"])]
    code += [(31 << 21) | 8, 0]
    text = b"".join(struct.pack("<I", w) for w in code)
    text += bytes(-len(text) % 0x1000)
    head = bytearray(0x34)
    head[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", head, 0x18, TEXT_VA)
    struct.pack_into("<I", head, 0x1C, 0x34)
    struct.pack_into("<HH", head, 0x2A, 32, 3)
    reginfo = bytes(20) + struct.pack("<I", GP_VAL)
    text_off, data_off = 0x1000, 0x1000 + len(text)
    rinfo_off = data_off + len(seg)
    ph = struct.pack("<8I", PT_MIPS_REGINFO, rinfo_off, 0, 0, 24, 24, 4, 4) \
        + struct.pack("<8I", PT_LOAD, text_off, TEXT_VA, TEXT_VA, len(text), len(text), 5, 0x1000) \
        + struct.pack("<8I", PT_LOAD, data_off, DATA_VA, DATA_VA, len(seg), len(seg) + 0x1000, 6, 0x1000)
    elf = bytes(head) + ph
    elf += bytes(0x1000 - len(elf)) + text + bytes(seg) + reginfo
    return elf


def synthetic_overlay() -> bytes:
    """An overlay ELF (one RWX segment at OVL_VA): a string of its own, a pointer to a boot string."""
    S = _test_layout()
    body = bytearray(0x100)
    code = [_i(0x0F, 0, 4, _hi(OVL_VA + 0x80)), _i(0x09, 4, 4, OVL_VA + 0x80), (31 << 21) | 8, 0]
    body[:16] = b"".join(struct.pack("<I", w) for w in code)
    body[0x80:0x80 + 12] = b"Please wait."
    struct.pack_into("<I", body, 0xC0, S["unref"])          # the overlay points at a boot string
    head = bytearray(0x34)
    head[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", head, 0x1C, 0x34)
    struct.pack_into("<HH", head, 0x2A, 32, 1)
    ph = struct.pack("<8I", PT_LOAD, 0x1000, OVL_VA, OVL_VA, len(body), len(body), 7, 0x1000)
    elf = bytes(head) + ph
    return elf + bytes(0x1000 - len(elf)) + bytes(body)


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    import minidisc
    import textpack
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    S = _test_layout()
    boot = synthetic_boot()
    minidisc.register_test_executable(boot)
    ovl = synthetic_overlay()
    es = ExeSet([("SLUS_208.92", boot, "boot")])
    got = {va: (s.raw, s.max, sorted(r.kind for r in s.refs)) for va, s in es.strings["SLUS_208.92"].items()}
    want = {S["pair"]: (b"Saving...", 15, ["hilo"]), S["ori"]: (b"Total Exp", 15, ["hilo"]),
            S["load"]: (b"Class Lv", 15, ["hilo"]), S["twice"]: (b"Zone", 15, ["hilo", "ptr"]),
            S["padded"]: (b"No items to sell.", 31, ["hilo"]), S["ptr"]: (b"Next Lv", 15, ["ptr"]),
            S["inner"]: (b"Hello World", 5, ["hilo"]), S["inner"] + 6: (b"World", 25, ["ptr"]),
            S["gp"]: (b"GP str", 15, ["gp"]), S["late"]: (b"\xa4\xa6Xy", 15, ["hilo"]),
            S["slot"]: (b"Delay", 15, ["hilo"]), S["saved"]: (b"Saved reg", 15, ["hilo"]),
            S["arm"]: (b"Arm two", 15, ["hilo"]), S["ctl"]: (b"\x19\x03Card OK", 15, ["hilo"])}
    check(set(got) == set(want), "exactly the referenced strings are listed: lui in a delay slot, a saved register "
                                 "across a call, a use behind a label; not the unreferenced one, not past a return, "
                                 "not the font table, not after the register is rewritten")
    check(all(got.get(k) == v for k, v in want.items()), "slots run to the next non-zero byte or referenced address, "
                                                         "refs are pointer words, lui pairs and gp")
    tree_txt = es.tree_text("SLUS_208.92")
    check(f"### @0x{S['pair']:08x} max 15 bytes\n# refs 1 (hilo 0x{TEXT_VA + 8:08x})\nSaving...\n" in tree_txt,
          "a block: header, refs comment, body")
    check(tree_txt.startswith(f"# exetext 1 file=SLUS_208.92 role=boot sha256={hashlib.sha256(boot).hexdigest()}"),
          "the first line names the file, role and sha256")
    check(f"0x{S['blocked']:08x}" not in tree_txt and f"0x{S['unref']:08x}" not in tree_txt, "no block for them")
    es2 = ExeSet([("SLUS_208.92", boot, "boot"), ("OV01.OVL", ovl, "overlay")])
    check(S["unref"] in es2.strings["SLUS_208.92"] and es2.strings["SLUS_208.92"][S["unref"]].refs[0].file == "OV01.OVL",
          "a boot string an overlay points at belongs to the boot ELF, the reference names the overlay")
    check(list(es2.strings["OV01.OVL"]) == [OVL_VA + 0x80], "the overlay's own string")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        fonts = {"font0.us": minidisc.synthetic_font(384), "font1.us": minidisc.synthetic_font(384)}
        src = minidisc.build(td / "src.iso", fonts, elf=boot, root_files={"OV01.OVL": ovl})
        before = src.read_bytes()
        tree = td / "tree"

        def q(fn, *a, **k):
            with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
                rc = fn(*a, **k)
            return rc, err.getvalue()

        rc, err = q(export, src, tree)
        man = json.loads((tree / MANIFEST).read_text(encoding="utf-8"))
        check(rc == 0 and set(man["files"]) == {BOOT_TREE, "OV01.OVL.exe.txt"} and man["build"] == "us"
              and man["files"][BOOT_TREE]["name"] == "SLUS_208.92" and man["files"][BOOT_TREE]["role"] == "boot",
              "export writes the manifest and one tree file per executable")
        check("BOOT.exe.txt (SLUS_208.92): 15 strings" in err, "export prints the count per file")
        changes, problems = plan(src, tree)
        check(changes == {} and problems == [], "an unedited tree changes nothing")
        rc, err = q(import_, src, tree, td / "none.iso")
        check(rc == 0 and "no string changed" in err and not (td / "none.iso").exists(), "... and writes nothing")
        bt = tree / BOOT_TREE

        def edit(old, new):
            t = bt.read_text(encoding="utf-8")
            assert old in t, old
            bt.write_text(t.replace(old, new, 1), encoding="utf-8")

        pristine = bt.read_text(encoding="utf-8")
        edit("\nSaving...\n", "\nSave\n")
        edit("\nTotal Exp\n", "\nTotal XP!\n")
        edit("\nGP str\n", "\nGP \u00e9t\n")                   # a typed Latin-1 letter, checked against the fonts
        changes, problems = plan(src, tree)
        new = changes.get("SLUS_208.92")
        o1, o2 = es.boot.off_of(S["pair"]), es.boot.off_of(S["ori"])
        check(problems == [] and new is not None and len(new) == len(boot)
              and new[o1:o1 + 16] == b"Save" + bytes(12) and new[o2:o2 + 10] == b"Total XP!\0",
              "a shorter and an equal-length edit are written in place with NUL fill")
        diff = [i for i in range(len(boot)) if new[i] != boot[i]]
        og = es.boot.off_of(S["gp"])
        check(all(o1 <= i < o1 + 16 or o2 <= i < o2 + 10 or og <= i < og + 16 for i in diff),
              "nothing else in the file changes")
        out = td / "A.iso"
        rc, err = q(import_, src, tree, out)
        a = out.read_bytes()
        check(rc == 0 and src.read_bytes() == before and len(a) == len(before), "exe-import writes a copy; the source "
                                                                                "is byte-identical")
        iso = IsoImage(src)
        f = iso.files["SLUS_208.92"]
        start = f.lba * 2048
        iso.close()
        bdiff = [i for i in range(len(a)) if a[i] != before[i]]
        check(bdiff and all(start + o1 <= i < start + o1 + 16 or start + o2 <= i < start + o2 + 10
                            or start + og <= i < start + og + 16 for i in bdiff),
              "the image differs from its source only in the edited bytes")
        check("no longer the retail one" in err, "the import warns that it must be the last step")
        rc, err = q(textpack.import_text, out, td / "nothing", td / "t.iso")
        check(rc == 1 and "not the executable" in err, "text-import refuses the exe-patched image")
        # refusals
        bt.write_text(pristine, encoding="utf-8")

        def refused(needle, path=src, retail=None):
            ch, pr = plan(path, tree, retail)
            hit = any(needle in p for p in pr)
            if not hit:
                print("      problems:", pr)
            return ch == {} and hit

        edit("\nSaving...\n", "\nSaving.........\n")                  # 15 bytes: fits exactly
        check(plan(src, tree)[1] == [], "an edit of exactly max bytes is accepted")
        edit("\nSaving.........\n", "\nSaving..........\n")           # 16
        check(refused(f"@0x{S['pair']:08x}: 16 bytes, but its slot holds max 15"), "one byte past max is refused, "
                                                                                     "the slot named")
        bt.write_text(pristine, encoding="utf-8")
        edit("\nZone\n", "\nZo\u27e600\u27e7e\n")
        check(refused("NUL"), "a NUL is refused")
        bt.write_text(pristine, encoding="utf-8")
        edit("\nZone\n", "\nZ\u3042\n")
        check(refused("cannot be written on the USA build"), "a typed Japanese character is refused")
        bt.write_text(pristine, encoding="utf-8")
        tab = region.US.ascii_table_va + 0x40
        bt.write_text(pristine + f"### @0x{tab:08x} max 3 bytes\nabc\n", encoding="utf-8")
        check(refused("not in the export"), "a block for an address in the font tables is refused")
        blocks = pristine.split("\n### ")
        bt.write_text("\n### ".join([blocks[0]] + [blocks[2], blocks[1]] + blocks[3:]), encoding="utf-8")
        check(refused("out of order"), "a moved block is refused")
        bt.write_text("\n### ".join(blocks[:2] + blocks[3:]), encoding="utf-8")
        check(refused("missing"), "a missing block is refused")
        bt.write_text(pristine.replace(f"@0x{S['pair']:08x} max 15", f"@0x{S['pair']:08x} max 16"), encoding="utf-8")
        check(refused("keep the header as exported"), "a changed max is refused")
        bt.write_text(pristine, encoding="utf-8")
        check(refused("not the file this tree was exported from", path=out), "an exe-patched disc needs --retail")
        # --retail: a second round onto the patched copy equals a fresh import from retail
        edit("\nNext Lv\n", "\nNext LV\n")
        out2, fresh = td / "B.iso", td / "C.iso"
        rc1, _ = q(import_, out, tree, out2, src)
        rc2, _ = q(import_, src, tree, fresh)
        b = out2.read_bytes()
        check(rc1 == 0 and rc2 == 0 and b == fresh.read_bytes() and a == out.read_bytes(),
              "--retail reapplies the tree onto an exe-patched copy; the result equals a fresh import")
        check(b[start + o1:start + o1 + 9] == b"Saving...", "... and the earlier edits not in the tree are undone")
        bad = minidisc.build(td / "bad.iso", fonts, elf=boot[:-1] + b"\x01", root_files={"OV01.OVL": ovl})
        check(refused("is not the retail file", path=out, retail=bad), "--retail refuses a non-retail source")
        # the overlay tree
        ot = tree / "OV01.OVL.exe.txt"
        otext = ot.read_text(encoding="utf-8")
        ot.write_text(otext.replace("\nPlease wait.\n", "\nOne moment\n"), encoding="utf-8")
        ch, pr = plan(src, tree)
        check(pr == [] and set(ch) == {"OV01.OVL", "SLUS_208.92"}, "an overlay edit is planned beside the boot ELF's")
        ot.write_text(otext, encoding="utf-8")
        # Japan is refused
        jp = minidisc.build(td / "jp.iso", fonts, boot="SLPS_253.68", elf=boot)
        rc, err = q(export, jp, td / "jptree")
        check(rc == 1 and "USA build" in err, "Japan is refused, saying the tool is USA only")
        # patch_copy with fs_replacements cleans up on failure
        from repack import RepackError, patch_copy

        def boom(tmp):
            raise RuntimeError("write failed half way")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                patch_copy(src, td / "D.iso", {}, fs_replacements={"SLUS_208.92": boot}, _fail_after_copy=boom)
            check(False, "a failure leaves nothing")
        except RuntimeError:
            check(not (td / "D.iso").exists() and not (td / "D.iso.partial").exists(), "patch_copy with "
                  "fs_replacements removes the partial file on failure")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                patch_copy(src, td / "E.iso", {}, fs_replacements={"SLUS_208.92": boot + b"x"})
            check(False, "a size change is refused")
        except RepackError:
            check(not (td / "E.iso").exists() and not (td / "E.iso.partial").exists(), "a file-size change is refused")
        check(src.read_bytes() == before, "the source image was never written")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("export")
    p.add_argument("iso")
    p.add_argument("out")
    p = sub.add_parser("import")
    p.add_argument("iso")
    p.add_argument("text")
    p.add_argument("out")
    p.add_argument("--retail")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.cmd == "export":
        return export(a.iso, a.out)
    if a.cmd == "import":
        return import_(a.iso, a.text, a.out, a.retail)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
