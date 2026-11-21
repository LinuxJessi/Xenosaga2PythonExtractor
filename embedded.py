"""embedded.py — texture pages (``txy``) stored inside other files.

Besides the 941 standalone ``.txy`` files, 2,167 more txy blocks live inside
697 other objects: effect libraries ``simajiri\\esd\\*.esd`` (1,821 blocks in
592 files), effect presets ``simajiri\\esp\\*.esp`` (334 in 104) and the title
backdrop ``yajima\\titlebg2.bin`` (12). They carry the battle and field
effect art (flares, rings, smoke, the title's backdrops).

A block is accepted where ``txy\\0`` is followed by a bank count of 1..32, a
total size that fits inside the container, a first bank table at +0x10 equal
to 0x90, and a table ``txy.parse`` accepts. A ``txy\\0`` preceded by ``.`` is
a texture *name* (``"ef_ring.txy\\0"``), not a block: 2,348 of those are
rejected, plus 193 hits whose +0x10 is not 0x90.

Like the Episode I kit's embedded-XTX sweep, ``sweep`` writes only images
not already produced: every decoded entry is PNG-encoded and its sha1
checked against the standalone pass and against everything already written,
and ``embedded_textures.csv`` records the provenance of every entry
(container, block offset, entry, where it was written or what it duplicates).
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Iterator, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import txy  # noqa: E402

MAGIC = b"txy\x00"
# kinds that never hold txy blocks (and the big ones are slow to scan)
NOT_CARRIERS = {"txy", "mpeg-ps", "adx", "ipu", "vsf", "prj", "midi", "jpeg", "sbl", "sb",
                "text", "scene-list", "font", "zero", "elf"}


def iter_blocks(data: bytes) -> Iterator[Tuple[int, bytes]]:
    """(offset, block bytes) for every txy block inside ``data``."""
    for m in re.finditer(re.escape(MAGIC), data):
        o = m.start()
        if o > 0 and data[o - 1] == 0x2E:            # "name.txy\0": a reference
            continue
        if o + 0x90 > len(data):
            continue
        nbanks, total, _, first = struct.unpack_from("<IIII", data, o + 4)
        if not (1 <= nbanks <= 32) or first != 0x90 or o + total > len(data) or total < 0x90:
            continue
        block = data[o:o + total]
        try:
            txy.parse(block)
        except txy.TxyError:
            continue
        yield o, block


def _scan_one(args) -> List[dict]:
    src, container = args
    data = Path(src).read_bytes()
    out = []
    for off, block in iter_blocks(data):
        bsha = hashlib.sha1(block).hexdigest()
        multi = len(txy.parse(block)["banks"]) > 1
        for e, w, h, rgba in txy.decode(block):
            png = txy.png_bytes(w, h, rgba)
            out.append({"container": container, "offset": f"{off:#x}", "block_sha1": bsha,
                        "entry": e["i"], "name": e["name"], "bank": e["bank"], "fmt": e["fmt"],
                        "w": w, "h": h, "png_sha1": hashlib.sha1(png).hexdigest(),
                        "file": txy.entry_filename(e, multi), "_png": png})
    return out


def sweep(out: Path, rows: List[dict], seen: Set[str], limit: Optional[int] = None) -> List[dict]:
    """Decode every embedded block of the dump at ``out``; write unseen images
    under browse/textures_png/_embedded/<container>/<offset>/ and return the
    provenance rows (``seen`` = PNG sha1s already written, updated in place)."""
    jobs = []
    for r in rows:
        if r["area"] == "code" or r["kind"] in NOT_CARRIERS:
            continue
        jobs.append((str(out / "dump" / r["area"] / Path(*r["path"].split("\\"))),
                     f"{r['area']}\\{r['path']}"))
    jobs = jobs[:limit] if limit else jobs
    root = out / "browse" / "textures_png" / "_embedded"
    first_writer: dict = {}
    result: List[dict] = []
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        for found in ex.map(_scan_one, jobs, chunksize=8):
            for row in found:
                png = row.pop("_png")
                sha = row["png_sha1"]
                if sha in seen:
                    row["written"] = ""
                    row["duplicate_of"] = first_writer.get(sha, "standalone .txy")
                else:
                    area, _, path = row["container"].partition("\\")
                    d = root / area / Path(*path.split("\\")) / row["offset"]
                    d.mkdir(parents=True, exist_ok=True)
                    (d / row["file"]).write_bytes(png)
                    rel = str((d / row["file"]).relative_to(out / "browse"))
                    seen.add(sha)
                    first_writer[sha] = rel
                    row["written"] = rel
                    row["duplicate_of"] = ""
                result.append(row)
    with open(out / "browse" / "embedded_textures.csv", "w", newline="", encoding="utf-8") as fh:
        keys = ["container", "offset", "block_sha1", "entry", "name", "bank", "fmt", "w", "h",
                "png_sha1", "file", "written", "duplicate_of"]
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(result)
    return result


def self_test() -> int:
    block, _ = txy._build_synthetic()
    junk = b"\x00" * 37 + b"ef_ring.txy\x00" + b"\x11" * 19
    container = junk + block + b"\x22" * 10
    found = list(iter_blocks(container))
    ok = len(found) == 1 and found[0][0] == len(junk) and found[0][1] == block
    print(("ok    " if ok else "FAIL  ") + "one block found at its offset; the 'x.txy' name string is not a block")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(self_test() if sys.argv[1:] == ["--self-test"] else 0)
