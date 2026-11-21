#!/usr/bin/env python3
"""langtest-verify.py - checks on the finished Episode II LANGTEST disc (run by langtest.sh).

usage: langtest-verify.py W RETAIL FINAL PREVIEWS

W holds export/, exe-export/ (retail), tr/, tr-exe/ (translated), tr-enc/,
tr-exe-enc/ (lang-encode), readback/text, readback/exe (re-exported from FINAL)
and font-ar/ (the last lang-scan). Checks, each a hard failure:

1. every translated string: the encoder's bytes equal the bytes re-exported from
   the disc; the cell sequence the USA print function reads ($han1) is decoded and
   every cell it reaches past retail is one the glyphset handed out; the tiers used
   are listed per string and per surface;
2. every other string of every exported file (text and executables) is unchanged;
3. font0.us / font1.us on the disc equal the scanned files byte for byte; the cells
   and metric slots that differ from retail are only the glyphset's (each slot's
   cell and its proportional image), and font0 and font1 got the same new cells.

Writes PREVIEWS/new-cells-font0.png and new-cells-font1.png (every changed cell
from the disc's fonts, 3x, with grid lines). Uses the kit (this repository, two
folders up) as a library; reads the images only.
"""
import json
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(KIT))

import font                      # noqa: E402
import textcodec                 # noqa: E402
from iso9660 import IsoImage     # noqa: E402
from repack import read_entry    # noqa: E402

COMMENT = ("# ", "#sjis: ", "#euc: ")    # notes and reading hints, not imported

# file -> surface, for the tier report
SURFACE = {
    "chain1/movie/en/s010100.sub.txt": "movie subtitles (font0)",
    "chain0/cf/en/sc8001.sbl.txt": "cfe event lines (font0)",
    "chain0/yamamoto/batdat.us.txt": "battle names",
    "BOOT.exe.txt": "executable string (save screen)",
}


def surface(rel: str, head: str) -> str:
    if rel in SURFACE:
        return SURFACE[rel]
    if rel.endswith("sc1010.sbl.txt"):
        return "field window talk.smsg" if head == "### 0006" else "field box talk.msgDtl"
    if rel.endswith("MainMenu.txd.txt"):
        return "menu"
    return rel


def blocks(path: Path):
    """{header: body} for '### ' files; {cue time: text} for subtitle files."""
    out = {}
    lines = path.read_text(encoding="utf-8").split("\n")
    if path.name.endswith(".sub.txt"):
        n = 0
        for ln in lines:
            m = re.match(r"^(\+?[0-9:]+)\t(.*)$", ln)
            if m:
                n += 1
                out[f"{n:04d} {m.group(1)}"] = m.group(2)
        return out
    head, body = None, []
    for ln in lines:
        if ln.startswith("### "):
            if head is not None:
                out[head] = "\n".join(b for b in body if not b.startswith(COMMENT))
            head, body = ln, []
        elif head is not None:
            body.append(ln)
    if head is not None:
        out[head] = "\n".join(b for b in body if not b.startswith(COMMENT))
    return out


def cells(adr, data: bytes):
    seq, i = [], 0
    if data.startswith(b"["):
        j = data.find(b"]")
        i = j + 1 if j > 0 else 0
    while i < len(data):
        b = data[i]
        if b == 0x24:
            j = data.find(b";", i)
            i = j + 1 if j >= 0 else len(data)
            continue
        if b == 0x5C and data[i + 1:i + 2] == b"n":
            seq.append("NL")
            i += 2
            continue
        if b < 0x20:
            seq.append("NL" if b == 0x0A else f"ctl{b:02X}")
            i += 1
            continue
        g = adr.glyph(data, i, han=True)
        seq.append((g.cell, data[i:i + g.length]))
        i += g.length
    return seq


def cell_png(fnt, idx, path: Path, scale=3):
    from PIL import Image
    cols = 16
    rows = (len(idx) + cols - 1) // cols
    cw, ch = 20 * scale + 1, 24 * scale + 1
    im = Image.new("L", (cols * cw + 1, max(rows, 1) * ch + 1), 40)
    px = im.load()
    shade = (0, 90, 170, 255)
    for n, c in enumerate(idx):
        x0, y0 = (n % cols) * cw + 1, (n // cols) * ch + 1
        for r, row in enumerate(fnt.cell(c)):
            for k, v in enumerate(row):
                for dy in range(scale):
                    for dx in range(scale):
                        px[x0 + k * scale + dx, y0 + r * scale + dy] = shade[v]
    im.save(path)


def main() -> int:
    W, retail, final, prev = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
    gs = json.loads((W / "font-ar" / "glyphset.json").read_text(encoding="utf-8"))
    by_cell = {int(v["cell"]): (k, v) for k, v in gs["slots"].items()}
    adr = font.Addressing.from_iso(IsoImage(retail))
    codec, sub_codec = textcodec.for_build("us"), textcodec.subtitle_codec("us")
    bad = 0
    # cells whose image or metrics the disc's font1 changed (a retail cell reached by a string,
    # such as the A1FB button of <○>, must be one of the unchanged ones)
    ra, fa1 = font.Font.from_bytes(read_entry(retail, 0, "font1.us")), font.Font.from_bytes(read_entry(final, 0, "font1.us"))
    changed1 = {c for c in range(ra.cell_count) if ra.cell(c) != fa1.cell(c)}
    changed1 |= {s for s in range(len(ra.metrics)) if ra.metrics[s] != fa1.metrics[s]}

    # 1 + 2: strings
    print("== translated strings (encoder bytes vs the disc's re-export)")
    tiers_by_surface = {}
    trees = [(W / "tr", W / "export", W / "tr-enc", W / "readback" / "text"),
             (W / "tr-exe", W / "exe-export", W / "tr-exe-enc", W / "readback" / "exe")]
    edited = set()
    nchanged = 0
    for tr, ref, enc, back in trees:
        for f in sorted(tr.rglob("*.txt")):
            rel = str(f.relative_to(tr))
            a, r, e, k = blocks(f), blocks(ref / rel), blocks(enc / rel), blocks(back / rel)
            cd = sub_codec if f.name.endswith(".sub.txt") else codec
            for h, body in a.items():
                if r.get(h) == body:
                    continue
                edited.add((str(back), rel, h))
                nchanged += 1
                if h not in e or h not in k:
                    print(f"BAD   {rel} {h}: missing from the encoded tree or the re-export")
                    bad += 1
                    continue
                be, bk = cd.from_text(e[h]), cd.from_text(k[h])
                seq = cells(adr, bk)
                tiers, unknown, retail_cells = set(), [], set()
                for it in seq:
                    if not isinstance(it, tuple) or it[1][0] < 0x80:
                        continue
                    c = it[0]
                    if c in by_cell:
                        tiers.add(by_cell[c][1]["tier"])
                    elif c in changed1 or adr.drawn_cell(c, True) in changed1:
                        unknown.append((c, it[1].hex().upper()))
                    else:
                        retail_cells.add(it[1].hex().upper())
                ok = be == bk and not unknown
                bad += not ok
                tiers_by_surface.setdefault(surface(rel, h), set()).update(tiers)
                n_glyph = sum(1 for it in seq if isinstance(it, tuple))
                print(f"{'OK  ' if ok else 'BAD '}  {rel} {h}: {len(bk)} bytes, {n_glyph} cells, "
                      f"tiers {sorted(tiers) or '-'}"
                      + (f", retail glyphs {sorted(retail_cells)}" if retail_cells else "")
                      + (f", CHANGED CELLS NOT FROM THE GLYPHSET {unknown}" if unknown else "")
                      + ("" if be == bk else f"\n      enc  {be!r}\n      disc {bk!r}"))
    print(f"{nchanged} translated strings")
    print("== tiers per surface")
    for s, t in sorted(tiers_by_surface.items()):
        print(f"  {s}: {', '.join(sorted(t)) or '-'}")

    print("== every other string unchanged")
    for ref, back in ((W / "export", W / "readback" / "text"), (W / "exe-export", W / "readback" / "exe")):
        nfiles = nblocks = 0
        for f in sorted(ref.rglob("*.txt")):
            rel = str(f.relative_to(ref))
            g = back / rel
            if not g.exists():
                print(f"BAD   {rel}: not in the re-export")
                bad += 1
                continue
            r, k = blocks(f), blocks(g)
            nfiles += 1
            for h in r.keys() | k.keys():
                if (str(back), rel, h) in edited:
                    continue
                nblocks += 1
                if r.get(h) != k.get(h):
                    print(f"BAD   {rel} {h}: {r.get(h)!r} -> {k.get(h)!r}")
                    bad += 1
        print(f"  {ref.name}: {nfiles} files, {nblocks} untouched strings compared")

    # 3: fonts
    print("== fonts")
    expect_cells, expect_slots = set(), set()
    for c in by_cell:
        expect_cells.add(c)
        expect_cells.add(adr.drawn_cell(c, True))
        for p in (True, False):
            s = adr.metric_slot(c, p)
            if s is not None:
                expect_slots.add(s)
    new = {}
    for name in ("font0.us", "font1.us"):
        a, b = read_entry(retail, 0, name), read_entry(final, 0, name)
        scanned = (W / "font-ar" / name).read_bytes()
        if b != scanned:
            print(f"BAD   {name}: the disc's file differs from the scanned one")
            bad += 1
        fa, fb = font.Font.from_bytes(a), font.Font.from_bytes(b)
        diff = [c for c in range(fa.cell_count) if fa.cell(c) != fb.cell(c)]
        mdiff = [s for s in range(len(fa.metrics)) if fa.metrics[s] != fb.metrics[s]]
        stray = sorted(set(diff) - expect_cells)
        mstray = sorted(set(mdiff) - expect_slots)
        missing = sorted(c for c in by_cell if adr.drawn_cell(c, True) not in diff and c not in diff)
        hdr = fa.headers == fb.headers and fa.tail == fb.tail
        ok = not stray and not mstray and not missing and hdr
        bad += not ok
        new[name] = (fb, diff)
        print(f"{'OK  ' if ok else 'BAD '}  {name}: {len(diff)} cells and {len(mdiff)} metric pairs differ from "
              f"retail, {len(by_cell)} glyph slots; outside the slots: cells {stray or 'none'}, "
              f"metrics {mstray or 'none'}; slots with no new ink {missing or 'none'}; headers/tail "
              f"{'same' if hdr else 'CHANGED'}")
        cell_png(fb, diff, prev / f"new-cells-{name.split('.')[0]}.png")
    f0, d0 = new["font0.us"]
    f1, d1 = new["font1.us"]
    same = d0 == d1 and all(f0.cell(c) == f1.cell(c) for c in d0)
    bad += not same
    print(f"{'OK  ' if same else 'BAD '}  font0 and font1 got the same new cells")
    tiers = {}
    for k, v in gs["slots"].items():
        tiers[v["tier"]] = tiers.get(v["tier"], 0) + 1
    print(f"  glyphset: {len(gs['slots'])} glyphs, by tier {tiers}")
    print(f"{'PASSED' if not bad else 'FAILED'}: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
