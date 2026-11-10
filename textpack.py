"""textpack.py — Episode II's dialogue as an editable UTF-8 tree, and back.

``export_text`` writes one ``.txt`` per ``.sbl`` text bank (field and scene
dialogue, map messages, quest prompts) under ``OUT/chainN/<in-game path>.txt``::

    # cf\\en\\sc1010.sbl  chain0  7 strings  stored 284 bytes, slot 2048 bytes
    ### 0004
    [Canaan]What is this thing?
    ### 0005
    #sjis: テストメッセージ      <- a reading hint, ignored on import
    11⟦83⟧e⟦83⟧X...

Each ``### NNNN`` block is one string, on one line. ``[Speaker]``, ``\\n``
(the game's own line-break code, typed as backslash + n), ``$zoom13;`` and
``$col...;`` are the game's codes: keep them. ``⟦XX⟧`` is a raw byte the
editor cannot show; keep it too. If you do type a real line break inside a
block, it is joined with the game's ``\\n`` on import.

``import_text`` re-encodes every changed file (EUC-JP), rebuilds the
``.sbl`` (strings may grow or shrink; offsets are recomputed) and writes the
result into a COPY of the disc with ``repack.patch_iso``, which reads every
write back. A bank that outgrows its slot on the disc is refused unless
``grow`` is set (``text-import --grow``); it is then appended to the image
and its TOC entry repointed.

How far a bank may grow (read from the USA code, 2026-11-10; NOT YET SEEN IN
THE RUNNING GAME). Both loaders read a file by its TOC size, rounded up to
2,048-byte sectors (``FUN_0019bc98``), into a buffer of fixed size, script
first and bank right after it:

* scenes, ``FUN_001eece0``: ``cf\\scNNNN.sb`` goes to a static buffer at
  0x3C6DC0 and ``cf\\en\\scNNNN.sbl`` to that address + the script's
  sector-rounded size. The buffer is 0x20000 bytes (the next object, the
  script-native tables, starts at 0x3E6DC0), so
  ``round2048(sb) + round2048(sbl) <= 131,072``.
* maps, ``FUN_001e00f0``: ``model\\map\\X.bin`` goes to a block of 0x12000
  bytes allocated once (``FUN_001df928(0x12000)``) and
  ``model\\map\\en\\X.sbl`` to that block + the script's size rounded up
  to 64, so ``align64(bin) + round2048(sbl) <= 73,728``.

Every retail pair obeys both rules (352 scenes, least room 10,240 bytes; 113
maps, least room 1,664 bytes). ``bank_budget`` applies them and the import
refuses a bank past its budget whether or not ``grow`` is set: past it the
load overwrites what follows the buffer. A bank at any other path has no
known loader and may not leave its slot.

    python textpack.py export DISC.iso OUTDIR
    python textpack.py import DISC.iso OUTDIR MODDED.iso [--grow]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbl
from chains import CHAINS, ChainReader, available_chains
from iso9660 import IsoImage, SECTOR
from toc import parse_toc


def _allocations(entries) -> Dict[str, int]:
    starts = sorted({e.sector for e in entries})
    nxt = {s: n for s, n in zip(starts, starts[1:])}
    out = {}
    for e in entries:
        end = nxt.get(e.sector)
        out[e.path] = ((end - e.sector) * SECTOR if end is not None
                       else (e.size + SECTOR - 1) // SECTOR * SECTOR)
    return out


SCENE_BUFFER = 0x20000      # the static script buffer at USA 0x3C6DC0 (FUN_001eece0)
MAP_BUFFER = 0x12000        # the block FUN_001df928(0x12000) returns (FUN_001e00f0)


def _round(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def bank_budget(bank_path: str, sizes: Dict[str, int]) -> Tuple[Optional[int], str]:
    """(largest .sbl byte size the loader has room for, why) for a bank path.
    ``sizes`` maps lower-case TOC path -> stored size. (None, reason) when no
    loader is known for the path or its script is missing."""
    low = bank_path.lower()
    head, _, name = low.rpartition("\\")
    parent, _, lang = head.rpartition("\\")
    stem = name.rsplit(".", 1)[0]
    if parent == "cf":
        script, buf, align, who = f"cf\\{stem}.sb", SCENE_BUFFER, SECTOR, "FUN_001eece0"
    elif parent == "model\\map":
        script, buf, align, who = f"model\\map\\{stem}.bin", MAP_BUFFER, 64, "FUN_001e00f0"
    else:
        return None, "no loader is known for a bank at this path"
    if script not in sizes:
        return None, f"its script {script} is not in the table of contents"
    used = _round(sizes[script], align)
    room = (buf - used) // SECTOR * SECTOR          # the bank is read in whole sectors
    return room, (f"{who} loads {script} ({sizes[script]} bytes, {used} with rounding) and then this bank "
                  f"into one {buf}-byte buffer")


def _sbl_entries(iso: IsoImage):
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        alloc = _allocations(entries)
        sizes = {e.path.lower(): e.size for e in entries}
        for e in entries:
            if e.ext == "sbl":
                yield chain, reader, e, alloc[e.path], sizes


# ------------------------------------------------ inline text (Japan, the demo)
#
# The Japanese discs have no .sbl: a pushtext operand is the byte offset of a
# NUL-terminated EUC-JP string in the script's own globals (sb.py). Export
# writes OUT/chainN/<script>.inline.txt with one ``### @0xOFFSET`` block per
# string; import re-encodes and writes each string back IN PLACE, so a string
# may shrink (NUL-padded) but never grow: every other pushtext and every global
# slot keep their offsets, and the .sb keeps its size.
#
# A string is listed when a pushtext names it or when a variable slot holds its
# offset (``sb.Script.pointer_slots``; the block header then says "table").
# Japan Disc 1: 17,053 + 172 = 17,225 strings in 592 scripts, which is every
# string of every text pool. A rebuild that lets strings grow would have to
# move the pool and rewrite both the pushtext operands and those slots; the kit
# does not offer one.

def _inline_entries(iso: IsoImage):
    import sb
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        for e in entries:
            if e.ext not in ("sb", "bin") or e.compressed:
                continue
            data = reader.read(e.byte_offset, e.size)
            if data[:4] != sb.MAGIC:
                continue
            s = sb.parse(data)
            if s.text_kind in ("inline", "mixed"):
                yield chain, e, data, s


def _inline_path(root: Path, chain: int, path: str) -> Path:
    return root / f"chain{chain}" / Path(*path.split("\\")).with_suffix(".inline.txt")


def export_inline(iso: IsoImage, root: Path) -> Tuple[int, int]:
    import sb
    n_files = n_strings = 0
    for chain, e, data, s in _inline_entries(iso):
        lines = [f"# {e.path}  chain{chain}  inline strings (grow refused; shrink is NUL-padded)"]
        for o, how in s.text_offsets():
            raw = s.inline_text(o)
            lines += [f"### @{o:#x} max {len(raw)} bytes" + ("  table" if how == "table" else ""),
                      sbl.to_text(raw)]
            n_strings += 1
        dest = _inline_path(root, chain, e.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n_files += 1
    return n_files, n_strings


def plan_inline(iso: IsoImage, root: Path, changed, problems) -> None:
    import struct
    for chain, e, data, s in _inline_entries(iso):
        src = _inline_path(root, chain, e.path)
        if not src.exists():
            continue
        glob_off = struct.unpack_from("<I", data, 0x18)[0]
        known = {o for o, _ in s.text_offsets()}
        out = bytearray(data)
        cur = None
        blocks = {}
        for line in src.read_text(encoding="utf-8").split("\n"):
            if line.startswith("### @"):
                cur = int(line[5:].split()[0], 16)
                blocks[cur] = []
            elif cur is not None and not line.startswith("#"):
                blocks[cur].append(line)
        for o, b in blocks.items():
            while len(b) > 1 and b[-1] == "":
                b.pop()
            old = s.inline_text(o) if o in known else None
            if old is None:
                problems.append(f"{e.path}: @{o:#x} is not a string this script's export lists")
                continue
            new = sbl.from_text("\\n".join(b))
            if new == old:
                continue
            if len(new) > len(old):
                problems.append(f"{e.path}: @{o:#x} grows {len(old)} -> {len(new)} bytes (in-place only)")
                continue
            a = glob_off + o
            out[a:a + len(old)] = new + b"\x00" * (len(old) - len(new))
        if bytes(out) != data:
            changed[(chain, e.path)] = bytes(out)


def _sjis_hint(s: bytes) -> str:
    try:
        t = s.decode("shift_jis")
    except UnicodeDecodeError:
        return ""
    return t if any(ord(c) > 0x7F for c in t) else ""


def _out_path(root: Path, chain: int, path: str) -> Path:
    return root / f"chain{chain}" / Path(*path.split("\\")).with_suffix(".sbl.txt")


def export_text(iso_path: str, out_dir: str) -> int:
    iso = IsoImage(iso_path)
    root = Path(out_dir)
    n_files = n_strings = 0
    for chain, reader, e, alloc, sizes in _sbl_entries(iso):
        data = reader.read(e.byte_offset, e.size)
        strings = sbl.parse(data)
        room, _why = bank_budget(e.path, sizes)
        lines = [f"# {e.path}  chain{chain}  {len(strings)} strings  "
                 f"stored {e.size} bytes, slot {alloc} bytes"
                 + (f", loader room {room} bytes (--grow)" if room is not None else "")]
        for i, s in enumerate(strings):
            lines.append(f"### {i:04d}")
            t = sbl.to_text(s)
            if "⟦" in t:
                hint = _sjis_hint(s)
                if hint:
                    lines.append("#sjis: " + hint.replace("\n", " "))
            lines.append(t)
        dest = _out_path(root, chain, e.path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n_files += 1
        n_strings += len(strings)
    print(f"exported {n_strings} strings from {n_files} .sbl files to {root}", file=sys.stderr)
    fi, si = export_inline(iso, root)
    if fi:
        print(f"exported {si} inline strings from {fi} .sb scripts (Japan/demo layout) to {root}", file=sys.stderr)
    return 0


def read_text_file(path: Path) -> List[bytes]:
    blocks: List[List[str]] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("### "):
            blocks.append([])
        elif blocks and not line.startswith("#"):
            blocks[-1].append(line)
    out = []
    for b in blocks:
        while len(b) > 1 and b[-1] == "":
            b.pop()
        out.append(sbl.from_text("\\n".join(b)))
    return out


def plan_import(iso_path: str, text_dir: str,
                grow: bool = False) -> Tuple[Dict[Tuple[int, str], bytes], List[str]]:
    iso = IsoImage(iso_path)
    root = Path(text_dir)
    changed: Dict[Tuple[int, str], bytes] = {}
    problems: List[str] = []
    for chain, reader, e, alloc, sizes in _sbl_entries(iso):
        src = _out_path(root, chain, e.path)
        if not src.exists():
            continue
        data = reader.read(e.byte_offset, e.size)
        old = sbl.parse(data)
        new = read_text_file(src)
        if len(new) != len(old):
            problems.append(f"{e.path}: {len(new)} strings, the disc has {len(old)} (do not add or remove ### blocks)")
            continue
        if new == old:
            continue
        blob = sbl.build(new, data)
        room, why = bank_budget(e.path, sizes)
        if room is not None and len(blob) > room:
            problems.append(f"{e.path}: rebuilt size {len(blob)} is {len(blob) - room} bytes past the "
                            f"{room} bytes the game has room for ({why}); shorten the text, "
                            f"relocating cannot help")
            continue
        if len(blob) > alloc:
            if room is None:
                problems.append(f"{e.path}: rebuilt size {len(blob)} exceeds its {alloc}-byte slot and "
                                f"cannot be relocated: {why}")
                continue
            if not grow:
                problems.append(f"{e.path}: rebuilt size {len(blob)} exceeds its {alloc}-byte slot "
                                f"(over by {len(blob) - alloc}; --grow relocates it to the end of the "
                                f"image, loader room {room} bytes)")
                continue
        changed[(chain, e.path)] = blob
    plan_inline(iso, root, changed, problems)
    return changed, problems


def import_text(iso_path: str, text_dir: str, out_iso: str, grow: bool = False) -> int:
    from repack import patch_iso

    changed, problems = plan_import(iso_path, text_dir, grow=grow)
    for p in problems:
        print("refused: " + p, file=sys.stderr)
    if problems:
        return 1
    if not changed:
        print("no text changed; nothing written", file=sys.stderr)
        return 0
    if Path(out_iso).resolve() != Path(iso_path).resolve():
        print(f"copying {iso_path} -> {out_iso} ...", file=sys.stderr)
        shutil.copyfile(iso_path, out_iso)
    reports = patch_iso(out_iso, changed, grow=grow)
    moved = [r for r in reports if r.relocated_lba is not None]
    print(f"patched {len(changed)} text file(s) into {out_iso}"
          + (f"; {len(moved)} relocated to the end of the image (not yet seen in the running game)"
             if moved else ""), file=sys.stderr)
    return 0


def _edit_block(path: Path, index: int, new_text: str, marker: str = "### ") -> None:
    """Replace the text of block ``index`` (0-based) of an exported file (self-tests)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    heads = [k for k, ln in enumerate(lines) if ln.startswith(marker)]
    k = heads[index]
    end = heads[index + 1] if index + 1 < len(heads) else len(lines) - 1
    body = [ln for ln in lines[k + 1:end] if ln.startswith("#")] + [new_text]
    path.write_text("\n".join(lines[:k + 1] + body + lines[end:]), encoding="utf-8")


def self_test() -> int:
    import contextlib
    import io
    import tempfile

    import minidisc

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    def quiet(fn, *a, **k):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)

    # the loader budgets
    sizes = {"cf\\sc0001.sb": 3000, "model\\map\\x.bin": 70004}
    check(bank_budget("cf\\en\\sc0001.sbl", sizes)[0] == SCENE_BUFFER - 4096, "scene bank: 0x20000 less the script's sectors")
    check(bank_budget("model\\map\\en\\X.sbl", sizes)[0] == 2048, "map bank: 0x12000 less the script rounded to 64, whole sectors")
    check(bank_budget("yamamoto\\think\\jp\\think001.sbl", sizes)[0] is None
          and bank_budget("cf\\en\\sc9999.sbl", sizes)[0] is None, "no budget without a known loader or script")

    scene = [b"[A]One.", b"[B]Two, a longer line.", b"<\xa2\xa2> three"]
    files = {
        "cf\\sc0001.sb": b"\x00" * 3000,                       # not parsed: only its size matters here
        "cf\\en\\sc0001.sbl": sbl.build(scene),
        "model\\map\\X.bin": b"\x00" * 70004,
        "model\\map\\en\\X.sbl": sbl.build([b"map line"]),
        "other\\en\\odd.sbl": sbl.build([b"odd"]),
        "zz\\end.bin": b"E" * 100,
    }
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        iso = minidisc.build(td / "mini.iso", files)
        tree = td / "text"
        quiet(export_text, str(iso), str(tree))
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and not pr, "an unedited export imports as no change")
        f = _out_path(tree, 0, "cf\\en\\sc0001.sbl")
        check("loader room 126976" in f.read_text(encoding="utf-8").split("\n")[0], "the export header states the loader room")
        _edit_block(f, 1, "[B]Two!")
        ch, pr = plan_import(str(iso), str(tree))
        check(list(ch) == [(0, "cf\\en\\sc0001.sbl")] and not pr
              and sbl.parse(ch[(0, "cf\\en\\sc0001.sbl")]) == [scene[0], b"[B]Two!", scene[2]], "one edited line changes one bank")
        _edit_block(f, 1, "[B]" + "long " * 500)                # 2.5 KB: past the one-sector slot
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and len(pr) == 1 and "--grow" in pr[0], "a bank past its slot is refused without --grow")
        out = td / "grown.iso"
        rc = quiet(import_text, str(iso), str(tree), str(out), grow=True)
        got = sbl.parse(minidisc.read(out, "cf\\en\\sc0001.sbl"))
        check(rc == 0 and got[1] == b"[B]" + b"long " * 500 and got[0] == scene[0]
              and out.stat().st_size == iso.stat().st_size + 2 * SECTOR, "--grow relocates it; the image gains its sectors")
        check(all(minidisc.read(out, p) == d for p, d in files.items() if p != "cf\\en\\sc0001.sbl"),
              "every other object is unchanged")
        tree2 = td / "text2"
        quiet(export_text, str(out), str(tree2))
        ch, pr = plan_import(str(out), str(tree2))
        check(not ch and not pr, "the grown image exports and re-imports as no change")
        # the map bank has 2,048 bytes of loader room: more is refused even with --grow
        quiet(export_text, str(iso), str(tree))
        m = _out_path(tree, 0, "model\\map\\en\\X.sbl")
        _edit_block(m, 0, "x" * 3000)
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(not ch and len(pr) == 1 and "FUN_001e00f0" in pr[0] and "relocating cannot help" in pr[0],
              "a bank past the loader's buffer is refused even with --grow")
        _edit_block(m, 0, "map line")
        o = _out_path(tree, 0, "other\\en\\odd.sbl")
        _edit_block(o, 0, "y" * 3000)
        ch, pr = plan_import(str(iso), str(tree), grow=True)
        check(not ch and len(pr) == 1 and "no loader is known" in pr[0], "a bank with no known loader may not leave its slot")
        _edit_block(o, 0, "odd")
        _edit_block(f, 0, "[A]One.")
        (tree / "chain0" / "cf" / "en" / "sc0001.sbl.txt").write_text(
            f.read_text(encoding="utf-8").replace("### 0002", "### 0002\n### 0003"), encoding="utf-8")
        ch, pr = plan_import(str(iso), str(tree))
        check(not ch and any("do not add or remove" in p for p in pr), "an added block is refused")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    a = sys.argv[1:]
    if a[:1] == ["--self-test"]:
        return self_test()
    if len(a) == 3 and a[0] == "export":
        return export_text(a[1], a[2])
    grow = "--grow" in a
    a = [x for x in a if x != "--grow"]
    if len(a) == 4 and a[0] == "import":
        return import_text(a[1], a[2], a[3], grow=grow)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
