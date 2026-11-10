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
``.sbl`` (strings may grow or shrink; offsets are recomputed), refuses the
whole import if any file outgrows its slot on the disc (no relocation is
offered until the engine is shown to follow a moved object), and writes
the result into a COPY of the disc with ``repack.patch_iso``, which reads
every write back.

    python textpack.py export DISC.iso OUTDIR
    python textpack.py import DISC.iso OUTDIR MODDED.iso
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

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


def _sbl_entries(iso: IsoImage):
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        entries = parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])
        alloc = _allocations(entries)
        for e in entries:
            if e.ext == "sbl":
                yield chain, reader, e, alloc[e.path]


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
    for chain, reader, e, alloc in _sbl_entries(iso):
        data = reader.read(e.byte_offset, e.size)
        strings = sbl.parse(data)
        lines = [f"# {e.path}  chain{chain}  {len(strings)} strings  "
                 f"stored {e.size} bytes, slot {alloc} bytes"]
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


def plan_import(iso_path: str, text_dir: str) -> Tuple[Dict[Tuple[int, str], bytes], List[str]]:
    iso = IsoImage(iso_path)
    root = Path(text_dir)
    changed: Dict[Tuple[int, str], bytes] = {}
    problems: List[str] = []
    for chain, reader, e, alloc in _sbl_entries(iso):
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
        if len(blob) > alloc:
            problems.append(f"{e.path}: rebuilt size {len(blob)} exceeds its {alloc}-byte slot")
            continue
        changed[(chain, e.path)] = blob
    plan_inline(iso, root, changed, problems)
    return changed, problems


def import_text(iso_path: str, text_dir: str, out_iso: str) -> int:
    from repack import patch_iso

    changed, problems = plan_import(iso_path, text_dir)
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
    patch_iso(out_iso, changed)
    print(f"patched {len(changed)} text file(s) into {out_iso}", file=sys.stderr)
    return 0


def main() -> int:
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == "export":
        return export_text(a[1], a[2])
    if len(a) == 4 and a[0] == "import":
        return import_text(a[1], a[2], a[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
