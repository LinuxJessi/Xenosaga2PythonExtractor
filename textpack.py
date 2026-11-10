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
    print(f"patched {len(changed)} .sbl file(s) into {out_iso}", file=sys.stderr)
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
