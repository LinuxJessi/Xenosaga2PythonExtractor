"""recolor.py — hue-shift named textures in every copy on a disc.

The Episode II counterpart of the Episode I kit's ``pinkhair.py`` and the
Episode III kit's ``chrtex`` recolor. A texture is named by its ``.txy``
entry (``kosmos00_hair_col``), and every copy is found by structure, not by
bytes: each standalone ``.txy`` and each txy block embedded in an effect file
(``embedded.py``) is parsed, every entry whose name matches a rule is
located, and the CLUT tile that entry reads (``palx``, ``paly`` in its bank)
is edited where it lies in that file. Episode III showed why: costume and
model variants carry the same art with palettes re-baked to other colours
and other canvas positions, so a byte signature finds only some copies.
KOS-MOS's hair is ``kosmos00_hair_col`` in ``kosmos00.txy`` and
``kosmos01_hair_col`` in ``kosmos01``, ``02`` and ``03``, at four different
tile positions.

Modes per rule: ``all`` sets the hue of every visible colour of the entry's
palette (hair, specular strips); ``blue`` touches only blue colours (her face
texture keeps skin and lips, loses the blue fringe and forehead marks to the
new hue). Colour maths and band tests are the Episode III kit's
(``chrtex._rot`` / ``_is_blue``). True-colour entries (fmt 0) have their
pixels edited instead of a palette.

    python recolor.py --iso DISC.iso --out PINK.iso [--preset kosmos-hair] [--hue 0.92]
                      [--rule 'name_glob=all|blue' ...] [--preview DIR] [--dry-run]

Edits are same-size, written into a copy of the disc with ``repack.patch_iso``
(every write is read back). Chain 0 (models, effects) is on both discs; patch
each disc you play.
"""
from __future__ import annotations

import argparse
import colorsys
import fnmatch
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import embedded  # noqa: E402
import txy  # noqa: E402
import region
from chains import CHAINS, ChainReader, available_chains  # noqa: E402
from iso9660 import IsoImage  # noqa: E402
from toc import parse_toc  # noqa: E402

DEFAULT_HUE = 0.92
PRESETS = {
    "kosmos-hair": [("kosmos*_hair_col", "all"), ("kosmos*_hair_spe", "all"),
                    ("kosmos*_face", "blue")],
}
CARRIER_EXTS = {"esd", "esp", "bin"}


# --------------------------------------------------- colour (Episode III kit)

def _rot(r, g, b, hue, smin=0.0, sboost=1.1, lboost=1.0):
    _h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    nr, ng, nb = colorsys.hls_to_rgb(hue, min(1.0, l * lboost), min(1.0, max(s * sboost, smin)))
    return int(nr * 255), int(ng * 255), int(nb * 255)


def _is_blue(r, g, b):
    """Blue, or the cool soft edge where painted hair fades into skin (blue
    no more than 30 under red and not green): without the edge a recoloured
    fringe keeps a blue-grey outline. Skin and lips have red far above blue."""
    return ((b > 140 and b > r + 40 and g > r) or (b > 90 and b > r + 30 and b >= g)
            or (b > r - 30 and b >= g - 12))


# ------------------------------------------------------------ locating bytes

def _canvas_offset(t: dict, bank: int, x: int, y: int) -> Optional[int]:
    """Byte offset in the block of canvas pixel (x, y) as the GS holds it
    after banks 0..bank are uploaded (latest covering strip wins)."""
    cw = t["canvas_w"]
    for b in range(min(bank, len(t["banks"]) - 1), -1, -1):
        for s in t["banks"][b]["strips"]:
            x0, y0 = txy.strip_origin(cw, s)
            if x0 <= x < x0 + s["w"] and y0 <= y < y0 + s["h"]:
                return s["off"] + 0x20 + ((y - y0) * s["w"] + (x - x0)) * 4
    return None


def entry_pixel_offsets(t: dict, e: dict) -> List[int]:
    """Offsets of the 4-byte RGBA words this entry's colours come from:
    its CLUT tile (PSMT8 16x16, PSMT4 8x2) or, for true colour, its rect."""
    if e["fmt"] == txy.PSMT8:
        cells = [(e["palx"] + x, e["paly"] + y) for y in range(16) for x in range(16)]
    elif e["fmt"] == txy.PSMT4:
        cells = [(e["palx"] + x, e["paly"] + y) for y in range(2) for x in range(8)]
    elif e["fmt"] == txy.PSMCT32:
        cells = [(e["x"] + x, e["y"] + y) for y in range(e["h"]) for x in range(e["w"])]
    else:
        return []
    out = []
    for x, y in cells:
        o = _canvas_offset(t, e["bank"], x, y)
        if o is not None:
            out.append(o)
    return out


@dataclass
class Hit:
    chain: int
    path: str
    block_off: int          # offset of the txy block in the file (0 for .txy files)
    entry: dict
    mode: str


def _rules(preset: Optional[str], extra: Iterable[str]) -> List[Tuple[str, str]]:
    rules = list(PRESETS[preset]) if preset else []
    for spec in extra:
        glob, _, mode = spec.partition("=")
        rules.append((glob, mode or "all"))
    return rules


def find(iso: IsoImage, rules: List[Tuple[str, str]]):
    """Yield (chain, toc entry, file bytes, [Hit]) for every file holding a
    matching entry."""
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        for e in parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0]):
            if e.compressed or (e.ext != "txy" and e.ext not in CARRIER_EXTS):
                continue
            data = reader.read(e.byte_offset, e.size)
            blocks = [(0, data)] if e.ext == "txy" and data[:4] == b"txy\x00" else list(embedded.iter_blocks(data))
            hits = []
            for off, block in blocks:
                try:
                    t = txy.parse(block)
                except txy.TxyError:
                    continue
                for ent in t["entries"]:
                    for glob, mode in rules:
                        if fnmatch.fnmatch(ent["name"].lower(), glob.lower()):
                            hits.append(Hit(chain, e.path, off, ent, mode))
                            break
            if hits:
                yield chain, e, data, hits


def apply(data: bytes, hits: List[Hit], hue: float) -> Tuple[bytes, int]:
    """Edit every hit's palette (or pixels) once; 'all' beats 'blue' when two
    rules reach the same word."""
    buf = bytearray(data)
    words: Dict[int, str] = {}
    for h in hits:
        t = txy.parse(data[h.block_off:])
        for o in entry_pixel_offsets(t, h.entry):
            o += h.block_off
            if words.get(o) != "all":
                words[o] = h.mode
    edits = 0
    for o, mode in sorted(words.items()):
        r, g, b, a = buf[o:o + 4]
        if (r, g, b, a) == (0, 0, 0, 0):
            continue
        if mode == "all":
            buf[o:o + 3] = bytes(_rot(r, g, b, hue, 0.35, 1.5, 1.12))
            edits += 1
        elif mode == "blue" and _is_blue(r, g, b):
            buf[o:o + 3] = bytes(_rot(r, g, b, hue, 0.3, 1.3))
            edits += 1
    return bytes(buf), edits


def _preview(before: bytes, after: bytes, hits: List[Hit], out_dir: Path, tag: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for h in hits:
        for label, data in (("before", before), ("after", after)):
            block = data[h.block_off:]
            t = txy.parse(block)
            canvas = txy.compose(block, t, h.entry["bank"])[0]
            rgba = txy.decode_entry(canvas, t["canvas_w"], txy.canvas_height(t), h.entry)
            if rgba is not None:
                txy.write_png(out_dir / f"{tag}__{h.entry['name']}__{label}.png",
                              h.entry["w"], h.entry["h"], rgba)


def run(iso_path: str, out_iso: Optional[str], rules: List[Tuple[str, str]], hue: float,
        preview: Optional[str] = None, dry_run: bool = False) -> int:
    iso = IsoImage(iso_path)
    replacements: Dict[Tuple[int, str], bytes] = {}
    total_hits = 0
    for chain, e, data, hits in find(iso, rules):
        new, edits = apply(data, hits, hue)
        total_hits += len(hits)
        names = ", ".join(sorted({h.entry["name"] for h in hits}))
        where = "standalone" if all(h.block_off == 0 for h in hits) else "embedded"
        print(f"chain{chain} {e.path} ({where}): {names} -> {edits} colours changed")
        if preview:
            _preview(data, new, hits, Path(preview), e.path.replace("\\", "__"))
        if edits:
            replacements[(chain, e.path)] = new
    iso.close()
    print(f"{total_hits} matching entries in {len(replacements)} files")
    if dry_run or not replacements:
        return 0
    if not out_iso:
        print("no --out given; nothing written", file=sys.stderr)
        return 2
    from repack import RepackError, patch_copy

    try:
        patch_copy(iso_path, out_iso, replacements)
    except (RepackError, region.UnknownBuild) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


# ------------------------------------------------- true-colour .xtx sheets

def recolor_xtx(data: bytes, hue: float) -> Tuple[bytes, int]:
    """Colourise a true-colour XTX sheet (no CLUT, e.g. the title menu words
    ``yajima\\titleobj.xtx``): every visible pixel takes the hue, with
    saturation lifted so white text turns pastel rather than staying white.
    ``data`` may be ARX-compressed; the result is uncompressed (``patch_iso``
    recompresses for compressed TOC entries)."""
    import struct

    import arx
    if data[:4] == b"ARX\x00":
        data = arx.decompress(data)
    if data[:4] != b"XTX\x00":
        raise ValueError("not an XTX sheet")
    buf = bytearray(data)
    _total, count, hdr = struct.unpack_from("<III", buf, 4)
    edits = 0
    for i in range(count):
        w, _bufw, h = struct.unpack_from("<HHH", buf, hdr + 20 * i)
        addr = struct.unpack_from("<I", buf, hdr + 20 * i + 16)[0] + 32
        for o in range(addr, addr + w * h * 4, 4):
            r, g, b, a = buf[o:o + 4]
            if a:
                buf[o:o + 3] = bytes(_rot(r, g, b, hue, 0.6, 1.0, 0.85))
                edits += 1
    return bytes(buf), edits


def run_xtx(iso_path: str, out_iso: str, path: str, hue: float, chain: int = 0) -> int:
    from repack import RepackError, patch_copy, read_entry

    new, edits = recolor_xtx(read_entry(iso_path, chain, path), hue)
    print(f"chain{chain} {path}: {edits} pixels recoloured")
    try:
        patch_copy(iso_path, out_iso, {(chain, path): new}, grow=True)
    except (RepackError, region.UnknownBuild) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


def self_test() -> int:
    block, _ = txy._build_synthetic()          # two PSMT8 entries, face_00 / face_01, own palettes
    t = txy.parse(block)
    hits = [Hit(0, "x.txy", 0, e, "all") for e in t["entries"] if e["name"] == "face_01"]
    new, edits = apply(block, hits, 0.92)
    before = {e["name"]: rgba for e, _w, _h, rgba in txy.decode(block)}
    after = {e["name"]: rgba for e, _w, _h, rgba in txy.decode(new)}
    ok = edits > 0 and after["face_00"] == before["face_00"] and after["face_01"] != before["face_01"]
    print(("ok    " if ok else "FAIL  ") + "only the named entry's palette changes")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    if argv is None and sys.argv[1:] == ["--self-test"]:
        return self_test()
    ap = argparse.ArgumentParser(description="hue-shift named textures in every copy on a disc")
    ap.add_argument("--iso", required=True)
    ap.add_argument("--out")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--rule", action="append", default=[], help="entry-name glob=all|blue")
    ap.add_argument("--hue", type=float, default=DEFAULT_HUE, help="target hue 0..1 (0.92 = pink)")
    ap.add_argument("--preview", help="write before/after PNGs of every edited entry here")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--xtx", help="instead: colourise this true-colour .xtx (TOC path, chain 0)")
    a = ap.parse_args(argv)
    if a.xtx:
        if not a.out:
            print("--xtx needs --out (a new file; the input image is never patched)", file=sys.stderr)
            return 2
        return run_xtx(a.iso, a.out, a.xtx, a.hue)
    rules = _rules(a.preset or (None if a.rule else "kosmos-hair"), a.rule)
    return run(a.iso, a.out, rules, a.hue, a.preview, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
