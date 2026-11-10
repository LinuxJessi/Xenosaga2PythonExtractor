"""sb.py — Episode II's compiled scripts (``.sb``): parse and disassemble.

Every scene (``cf\\scNNNN.sb``) and map (``model\\map\\*.bin``) runs a small
stack-machine program; its dialogue lives in the ``.sbl`` beside it
(``sbl.py``). Episode I ran Java here; Episode II replaced it with this VM,
found in ``OV01.OVL`` (2026-11-05, from the Ghidra output of the boot ELF and
the overlay):

* the opcode handlers are a 46-entry table at OV01 0xab5fc0 (one function
  per opcode, ``FUN_00a9dbb0`` .. ``FUN_00a9f058``), instruction lengths a
  46-byte table at 0xab5f90, the ``switch`` op (0x25) sized ``4n + 5``;
* natives are registered by class with ``FUN_00a9cdb0(name, table)``, the
  tables being ``{char *name, handler}`` pairs in the boot ELF's data
  (e.g. "runtime" at 0x3e6e40: setPlayer, Item, TimeAttack, message, jumpCF,
  mapChange, Fade, setFlag, getFlag, ...); ``FUN_00a9d148(class, name)``
  resolves an import to ``class << 16 | index`` when a script loads.

Code is a stream of 16-bit words; an operand is the 32-bit value in the two
words after the opcode. Jump targets and entry points count 16-bit words
from the start of the code.

The VM context (from the handlers): +0x00 pc (words), +0x04 stack depth,
+0x08 frame pointer, +0x14 the loaded script image, +0x1C stack base, +0x20
stack size. Globals live IN the script image: ``ldg n`` reads the 32-bit
slot n of the file's globals section (header +0x18), so the file's bytes are
the variables' initial values and their storage. Locals are stack slots
relative to the frame pointer.

Four opcodes push their 32-bit operand and have byte-identical handlers
(03, 04, 06, 07); the compiler picked one by the kind of literal, which the
scripts show without exception (2026-11-05, all 465 scripts):

    03 pushaddr  a variable's slot number, consumed by stg/ldg.ind/incg/decg
                 (60,189 of 60,189 are inside the globals) or stl/ldl.ind
    04 push      an integer (0xFFFFFFFF = -1 is common)
    06 pushtext  0x20 << 16 | line number in the scene's .sbl -- or, in the older
                 build (see below), the byte offset of a string in the globals
    07 pushcode  a code address (8,053 of 8,053 land on an instruction, 7,587
                 on an entry point): threads for waitfor/recv/send, callbacks

A native's arguments are pushed as (value, type) pairs and then the count;
``native`` pops ``2n + 1`` words and pushes the result. The type is a small
constant read by the native through ``FUN_00a9f140``; ``FUN_00a9f1c0`` treats
type 0 as "argument omitted, use the default":

    0 none (omitted)   1 int   3 text (resolved through the .sbl)
    4 code address     5 table (a slot number inside the globals)

``FUN_00a9f238`` reads script +0x1C as a pointer to the loaded .sbl (count
+0x08, offsets +0x14, strings +0x18); in the file that word just equals the
entry-table offset. 0x26 and 0x27 both push 0; the compiler uses 0x27 only
right after 0x26, as the type of an omitted argument (48,363 times).
Opcodes 02 and 05 (and the constants 2 and 6) never occur on the disc.

The older build (the Episode II demo on Xenosaga Freaks, SLPS_253.53, EXP\,
Jan 2004; 2026-11-05): the same file version (+0x04 = 1) and opcodes, but no
.sbl yet. A ``pushtext`` operand is then the byte offset of a NUL-terminated
EUC-JP string inside the script's own globals section (5,053 of 5,053 land
on a string start; 5,049 decode as EUC-JP), so dialogue, debug prints and
effect names all live in the .sb. A few demo headers leave the source-name
offset at 0 (no name). ``Script.text_kind`` says which form a script uses.

File layout::

    +0x00 "SB  "  +0x04 u32 1  +0x08 u32 ?  +0x0C u32 entries  +0x10 u32 imports
    +0x14 code  +0x18 globals  +0x1C (.sbl pointer at run time)  +0x20 entry table
    +0x24 import table  +0x28 source name (e.g. "nakahara\\sc1010", the author's folder;
                        0 = none, seen only in the demo)
    entry table : entries x {u32 name offset, u32 code word}, then the names
    import table: imports x {u16 function name offset, u16 class name offset},
                  offsets into the string pool that follows the table

    python sb.py --self-test
    python sb.py FILE.sb [FILE.sbl]       # disassemble (with the strings)
"""
from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAGIC = b"SB  "

# lengths in 16-bit words, from the OV01 table at 0xab5f90 (op 0x25 is 4n+5)
LENGTHS = [1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 1, 1, 1, 1, 3, 3, 3, 1, 3, 1, 1, 3, 1, 1,
           1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1]

# names from what each OV01 handler does (see docs/SCRIPTS.md)
NAMES = {
    0x00: "nop", 0x01: "op", 0x02: "line", 0x03: "pushaddr", 0x04: "push", 0x05: "mark",
    0x06: "pushtext", 0x07: "pushcode", 0x08: "ldg", 0x09: "ldl", 0x0A: "stg", 0x0B: "stl",
    0x0C: "stg.keep", 0x0D: "stl.keep", 0x0E: "jmp", 0x0F: "jz", 0x10: "jnz",
    0x11: "enter", 0x12: "call", 0x13: "ret", 0x14: "retv", 0x15: "native",
    0x16: "waitfor", 0x17: "waitfor2", 0x18: "dup", 0x19: "drop", 0x1A: "ldg.ind",
    0x1B: "ldl.ind", 0x1C: "yield", 0x1D: "end", 0x1E: "sleep", 0x1F: "send",
    0x20: "recv", 0x21: "incg", 0x22: "incl", 0x23: "decg", 0x24: "decl",
    0x25: "switch", 0x26: "push0", 0x27: "push0", 0x28: "push1", 0x29: "push2",
    0x2A: "push3", 0x2B: "push4", 0x2C: "push5", 0x2D: "push6",
}
OPERATORS = {1: "add", 2: "sub", 3: "mul", 4: "div", 5: "mod", 6: "or", 7: "and",
             8: "shr", 9: "shl", 10: "neg", 11: "eq", 12: "gt", 13: "lt", 14: "ge",
             15: "le", 16: "ne", 17: "land", 18: "lor"}
JUMPS = {0x0E, 0x0F, 0x10, 0x12}
CODE_REFS = JUMPS | {0x07}                     # operands that are code addresses

# argument types of a native call (the constant pushed after each value)
ARG_TYPES = {0: "none", 1: "int", 3: "text", 4: "code", 5: "table"}
CONST_OPS = {0x26: 0, 0x27: 0, 0x28: 1, 0x29: 2, 0x2A: 3, 0x2B: 4, 0x2C: 5, 0x2D: 6}
# instructions that leave one value on the stack (an argument's value)
VALUE_OPS = {0x01, 0x04, 0x06, 0x07, 0x08, 0x09, 0x15, 0x1A, 0x1B, 0x26}
# what may follow the type half of an argument pair: the next argument's value or the count
AFTER_TYPE = {0x03, 0x04, 0x06, 0x07, 0x08, 0x09, 0x1A, 0x1B} | set(CONST_OPS)
# consumers of a pushaddr that address the globals (stg, stg.keep, ldg.ind, incg, decg)
GLOBAL_ADDR_OPS = {0x0A, 0x0C, 0x1A, 0x21, 0x23}
TEXT_TAG = 0x20


class SbError(ValueError):
    pass


@dataclass
class Script:
    code: bytes
    entries: List[Tuple[str, int]]
    imports: List[Tuple[str, str]]          # (class, function)
    globals_: bytes
    source: str
    header: Tuple[int, ...] = field(default=())

    def inline_text(self, operand: int) -> Optional[bytes]:
        """The globals string a demo-style pushtext operand points at (None if it is not
        the start of a NUL-terminated string inside the globals)."""
        g = self.globals_
        if not (0 <= operand < len(g)) or (operand and g[operand - 1] != 0) or b"\x00" not in g[operand:]:
            return None
        return g[operand:g.index(b"\x00", operand)]

    @property
    def text_kind(self) -> str:
        """'sbl' (pushtext = .sbl line, retail), 'inline' (pushtext = globals string,
        the demo), 'none' (no pushtext) or 'mixed'."""
        kinds = {"sbl" if (i.operand >> 16) == TEXT_TAG else "inline"
                 for i in disassemble(self.code) if i.op == 0x06}
        return "none" if not kinds else kinds.pop() if len(kinds) == 1 else "mixed"

    def inline_strings(self) -> List[Tuple[int, str]]:
        """(offset, text) of every globals string a pushtext names, in offset order."""
        offs = sorted({i.operand for i in disassemble(self.code)
                       if i.op == 0x06 and (i.operand >> 16) != TEXT_TAG})
        return [(o, decode_inline(t)) for o in offs if (t := self.inline_text(o)) is not None]

    @property
    def global_slots(self) -> List[int]:
        """The globals section as 32-bit slots (initial values)."""
        n = len(self.globals_) // 4
        return list(struct.unpack(f"<{n}I", self.globals_[:4 * n]))


def decode_inline(raw: bytes) -> str:
    """A demo inline string: EUC-JP, falling back to Shift-JIS, then latin-1."""
    for enc in ("euc_jp", "shift_jis"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin1")


def _cstr(data: bytes, off: int) -> str:
    return data[off:data.index(b"\x00", off)].decode("latin1")


def parse(data: bytes) -> Script:
    if data[:4] != MAGIC:
        raise SbError("not an SB (magic)")
    h = struct.unpack_from("<11I", data, 0)
    n_ent, n_imp, code_off, glob_off, _unk, ent_off, imp_off, src_off = h[3], h[4], h[5], h[6], h[7], h[8], h[9], h[10]
    if not (0x40 <= code_off <= glob_off <= ent_off <= imp_off <= (src_off or imp_off) <= len(data)):
        raise SbError(f"section offsets out of order: {[hex(x) for x in h[5:]]}")
    entries = []
    for i in range(n_ent):
        name_off, word = struct.unpack_from("<II", data, ent_off + 8 * i)
        entries.append((_cstr(data, ent_off + name_off), word))
    pool = imp_off + 4 * n_imp
    imports = []
    for i in range(n_imp):
        fn, cls = struct.unpack_from("<HH", data, imp_off + 4 * i)
        imports.append((_cstr(data, pool + cls), _cstr(data, pool + fn)))
    return Script(data[code_off:glob_off], entries, imports, data[glob_off:ent_off],
                  _cstr(data, src_off) if 0 < src_off < len(data) else "", h)


@dataclass
class Insn:
    word: int
    op: int
    operand: Optional[int]
    table: Optional[List[Tuple[int, int]]] = None   # switch: (key, target)
    default: Optional[int] = None


def disassemble(code: bytes) -> List[Insn]:
    if len(code) % 2:
        raise SbError("odd code length")
    words = struct.unpack(f"<{len(code) // 2}H", code)
    out, pc = [], 0

    def u32(p):
        return words[p] | (words[p + 1] << 16)

    while pc < len(words):
        op = words[pc]
        if op >= len(LENGTHS):
            raise SbError(f"bad opcode {op:#x} at word {pc:#x}")
        if op == 0x25:
            n = u32(pc + 1)
            default = u32(pc + 3)
            table = [(u32(pc + 5 + 4 * i), u32(pc + 7 + 4 * i)) for i in range(n)]
            out.append(Insn(pc, op, n, table, default))
            pc += 4 * n + 5
            continue
        ln = LENGTHS[op]
        operand = u32(pc + 1) if ln == 3 else None
        out.append(Insn(pc, op, operand))
        pc += ln
    if pc != len(words):
        raise SbError(f"code ends mid-instruction ({pc} of {len(words)} words)")
    return out


def check(script: Script) -> List[str]:
    """Problems found by structural checks (empty list = clean)."""
    insns = disassemble(script.code)
    starts = {i.word for i in insns}
    n_glob = len(script.globals_) // 4
    probs = []
    for k, i in enumerate(insns):
        targets = []
        if i.op in CODE_REFS:
            targets = [i.operand]
        elif i.op == 0x25:
            targets = [i.default] + [t for _, t in i.table]
        for t in targets:
            if t not in starts:
                probs.append(f"word {i.word:#x}: {NAMES[i.op]} target {t:#x} is not an instruction")
        if i.op == 0x15 and not (0 <= (i.operand & 0xFFFF) < len(script.imports)):
            probs.append(f"word {i.word:#x}: native index {i.operand} outside the import table")
        if i.op == 0x06 and (i.operand >> 16) != TEXT_TAG and script.inline_text(i.operand) is None:
            probs.append(f"word {i.word:#x}: pushtext operand {i.operand:#x} is neither a .sbl line "
                         f"nor a string in the globals")
        if i.op == 0x03 and k + 1 < len(insns) and insns[k + 1].op in GLOBAL_ADDR_OPS \
                and not (0 <= i.operand < n_glob):
            probs.append(f"word {i.word:#x}: pushaddr g{i.operand} outside the {n_glob} globals")
        if i.op == 0x08 and not (0 <= i.operand < n_glob):
            probs.append(f"word {i.word:#x}: ldg g{i.operand} outside the {n_glob} globals")
    for name, w in script.entries:
        if w not in starts:
            probs.append(f"entry {name} at word {w:#x} is not an instruction")
    return probs


def native_calls(script: Script) -> List[Tuple[str, Tuple[str, ...]]]:
    """Every native call as (class.function, argument types), types read from the
    (value, type) pairs the compiler pushes before the count. The types sit on a
    stack so an argument that is itself a native call keeps its own; a call whose
    count does not match the types seen gets '?' for the missing ones."""
    insns = disassemble(script.code)
    out, stack = [], []
    for k, i in enumerate(insns):
        prev = insns[k - 1] if k else None
        nxt = insns[k + 1] if k + 1 < len(insns) else None
        if i.op == 0x27 or (i.op in CONST_OPS and prev is not None and prev.op in VALUE_OPS
                            and not (prev.op == 0x26 and i.op == 0x26)
                            and nxt is not None and nxt.op in AFTER_TYPE):
            t = CONST_OPS[i.op]
            stack.append(ARG_TYPES.get(t, str(t)))
        elif i.op == 0x15 and prev is not None:
            n = prev.operand if prev.op == 0x04 else CONST_OPS.get(prev.op, -1)
            n = n if 0 <= n < 64 else 0
            types = stack[len(stack) - n:] if n <= len(stack) else ["?"] * (n - len(stack)) + stack
            del stack[max(0, len(stack) - n):]
            cls, fn = script.imports[i.operand & 0xFFFF]
            out.append((f"{cls}.{fn}", tuple(types)))
    return out


def _signed(v: int) -> int:
    return v - (1 << 32) if v & 0x80000000 else v


def listing(script: Script, strings: Optional[List[bytes]] = None) -> str:
    insns = disassemble(script.code)
    labels: Dict[int, str] = {w: n for n, w in script.entries}
    for i in insns:
        tgts = [i.operand] if i.op in CODE_REFS else ([i.default] + [t for _, t in i.table] if i.op == 0x25 else [])
        for t in tgts:
            labels.setdefault(t, f"L_{t:04x}")
    lines = [f"; source {script.source}", f"; {len(script.entries)} entries, {len(script.imports)} imports, "
             f"{len(insns)} instructions, {len(script.globals_) // 4} global slots"]
    for k, (cls, fn) in enumerate(script.imports):
        lines.append(f";   import {k:3d} = {cls}.{fn}")
    prev: Optional[Insn] = None
    following = {a.word: b for a, b in zip(insns, insns[1:])}
    for i in insns:
        if i.word in labels:
            lines.append(f"{labels[i.word]}:")
        name = NAMES[i.op]
        arg = ""
        if i.op == 0x01:
            arg = OPERATORS.get(i.operand, f"op{i.operand}")
        elif i.op == 0x15:
            cls, fn = script.imports[i.operand & 0xFFFF]
            arg = f"{cls}.{fn}"
        elif i.op in CODE_REFS:
            arg = labels.get(i.operand, f"{i.operand:#x}")
        elif i.op == 0x25:
            arg = f"default {labels.get(i.default)}; " + ", ".join(f"{k}:{labels.get(t)}" for k, t in i.table)
        elif i.op in (0x03, 0x08):
            arg = f"g{i.operand}"
        elif i.op == 0x09:
            arg = f"l{_signed(i.operand)}"
        elif i.op == 0x06 and (i.operand >> 16) != TEXT_TAG and script.inline_text(i.operand) is not None:
            t = decode_inline(script.inline_text(i.operand)).replace("\n", "\\n")
            arg = f"str@{i.operand:#x}    ; \"{t[:70]}\""
        elif i.op == 0x06:
            k = i.operand & 0xFFFF
            arg = f"text#{k}"
            if strings is not None and k < len(strings):
                import sbl
                arg += f"    ; {sbl.to_text(strings[k])[:70]}"
        elif i.op == 0x04:
            arg = f"{_signed(i.operand)}"
        elif i.operand is not None:
            arg = f"{i.operand}"
        nxt = following.get(i.word)
        if i.op == 0x27 or (i.op in CONST_OPS and prev is not None and prev.op in VALUE_OPS
                            and not (prev.op == 0x26 and i.op == 0x26)
                            and nxt is not None and nxt.op in AFTER_TYPE):
            t = CONST_OPS[i.op]
            arg = f"; type {ARG_TYPES.get(t, t)}"
        lines.append(f"  {i.word:04x}  {name:9s} {arg}".rstrip())
        prev = i
    return "\n".join(lines)


def self_test() -> int:
    ok = True

    def check_(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    # push 2 ; push 3 ; op add ; native 0 ; jz end ; end:  end
    code = struct.pack("<HHH", 0x04, 2, 0) + struct.pack("<HHH", 0x04, 3, 0) + struct.pack("<HHH", 0x01, 1, 0)
    code += struct.pack("<HHH", 0x15, 0, 0) + struct.pack("<HHH", 0x0F, 15, 0) + struct.pack("<H", 0x1D)
    insns = disassemble(code)
    check_([NAMES[i.op] for i in insns] == ["push", "push", "op", "native", "jz", "end"], "instruction stream")
    pool = b"runtime\x00setFlag\x00"
    imp = struct.pack("<HH", 8, 0) + pool
    ent = struct.pack("<II", 8, 0) + b"main\x00\x00\x00\x00"

    def build(body, glob=b""):
        glob_off = 0x40 + len(body)
        ent_off = glob_off + len(glob)
        imp_off = ent_off + len(ent)
        src_off = imp_off + len(imp)
        head = MAGIC + struct.pack("<10I", 1, 0, 1, 1, 0x40, glob_off, ent_off, ent_off, imp_off, src_off)
        return head.ljust(0x40, b"\x00") + body + glob + ent + imp + b"test\\x\x00"

    s = parse(build(code))
    check_(s.entries == [("main", 0)] and s.imports == [("runtime", "setFlag")], "header, entries, imports")
    check_(check(s) == [], "structural check passes")
    check_("runtime.setFlag" in listing(s), "listing names the native")
    # a call with typed arguments: push -1 (int), pushtext #2 (text), push0+push0 (omitted), count 3
    call = (struct.pack("<HHH", 0x04, 0xFFFF, 0xFFFF) + struct.pack("<H", 0x28)
            + struct.pack("<HHH", 0x06, 2, TEXT_TAG) + struct.pack("<H", 0x2A)
            + struct.pack("<HH", 0x26, 0x27)
            + struct.pack("<HHH", 0x04, 3, 0) + struct.pack("<HHH", 0x15, 0, 0)
            + struct.pack("<HHH", 0x07, 0, 0) + struct.pack("<H", 0x2B)
            + struct.pack("<HHH", 0x03, 1, 0) + struct.pack("<H", 0x0A) + struct.pack("<H", 0x1D))
    s = parse(build(call, struct.pack("<II", 0, 7)))
    text = listing(s, [b"zero", b"one", b"[KOS-MOS]Hello."])
    check_("push      -1" in text and "; type int" in text, "an int argument and its type")
    check_("pushtext  text#2" in text and "Hello." in text and "; type text" in text, "a text argument shows its line")
    check_("; type none" in text, "an omitted argument (0x26 0x27)")
    check_("pushcode  main" in text and "; type code" in text, "a code address is a label")
    check_("pushaddr  g1" in text and s.global_slots == [0, 7], "pushaddr names a global slot")
    check_(check(s) == [], "typed call passes the structural checks")
    check_(s.text_kind == "sbl", "a retail script's text is .sbl lines")
    # the demo form: pushtext names a globals string; no source name (offset 0)
    glob = b"ce000_062\x00" + "勝利者".encode("euc_jp") + b"\x00\x00\x00\x00"
    demo = build(struct.pack("<HHH", 0x06, 10, 0) + struct.pack("<H", 0x2A) + struct.pack("<H", 0x1D), glob)
    demo = demo[:0x28] + struct.pack("<I", 0) + demo[0x2C:]
    d = parse(demo)
    check_(d.source == "" and d.text_kind == "inline", "a demo script: no source name, inline text")
    check_(check(d) == [] and d.inline_strings() == [(10, "勝利者")], "an inline string passes and decodes")
    check_('str@0xa' in listing(d) and "勝利者" in listing(d), "the listing shows the inline string")
    mid = parse(build(struct.pack("<HHH", 0x06, 3, 0) + struct.pack("<H", 0x1D), glob))
    check_(any("pushtext" in p for p in check(mid)), "a pushtext into the middle of a string is caught")
    s2 = parse(build(call, struct.pack("<II", 0, 7)))
    check_(native_calls(s2) == [("runtime.setFlag", ("int", "text", "none"))], "native_calls reads the argument types")
    bad = parse(build(struct.pack("<HHH", 0x07, 1, 0) + struct.pack("<H", 0x1D)))
    check_(any("pushcode" in p for p in check(bad)), "a pushcode into the middle of an instruction is caught")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if not a:
        print(__doc__)
        return 2
    s = parse(open(a[0], "rb").read())
    strings = None
    if len(a) > 1:
        import sbl
        strings = sbl.parse(open(a[1], "rb").read())
    print(listing(s, strings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
