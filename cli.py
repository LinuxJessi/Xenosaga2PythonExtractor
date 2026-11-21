#!/usr/bin/env python3
"""cli.py — Xenosaga Episode II (USA) disc extractor.

Pure Python: the standard library plus numpy (music), fontTools and uharfbuzz
(new alphabets), all in requirements.txt; ffmpeg optional, for movies. Same shape
as the Episode I kit: a reading stack (iso9660 -> chains -> toc -> arx), a
``browse`` step that converts, and a write path (``repack.py``).

Usage:
    python cli.py list    --iso DISC.iso [--chain 0|1|2]
    python cli.py dump    --iso DISC1.iso [--iso DISC2.iso] --out OUT
                          [--chain N] [--glob 'model\\char\\pc\\*'] [--raw] [--code]
    python cli.py verify  --out OUT
    python cli.py browse  --out OUT [--kinds textures,models,xtx,fonts,text,scripts,audio,music,images,movies,small]
    python cli.py sb      --iso DISC.iso --path 'cf\\sc1010.sb'      (disassemble one script)
    python cli.py text-export --iso DISC.iso --out TEXTDIR
    python cli.py dialogue --iso DISC1.iso [--iso DISC2.iso] --out DIR [--voice]
    python cli.py text-import --iso DISC.iso --text TEXTDIR --out MODDED.iso [--grow] [--rebase]
    python cli.py exe-export --iso DISC.iso --out EXEDIR
    python cli.py exe-import --iso DISC.iso --text EXEDIR --out MODDED.iso [--retail RETAIL.iso]   (run last)
    python cli.py font-extract --iso DISC.iso --out FONTDIR
    python cli.py font-rebuild --ref FONTDIR/font0.us --sheets FONTDIR --out new.us [--metrics CSV]
    python cli.py patch   --iso DISC.iso --out MODDED.iso --set 'chain0:cf\\en\\sc1010.sbl=new.sbl'
    python cli.py lang-scan   --iso DISC.iso --text TR --font F.ttf --script Arab --rtl --out FONTS
    python cli.py lang-encode --text TR --glyphset FONTS/glyphset.json --out TR-ENC [--width-kind bank=360]
    python cli.py lang-preview --glyphset FONTS/glyphset.json --text "..." --out line.png
    python cli.py recolor --iso DISC.iso --out PINK.iso [--preset kosmos-hair] [--hue 0.92]
    python cli.py self-test

``dump`` writes OUT/dump/chain0|chain1|chain2/<in-game path>. ARX-compressed
objects are written decompressed (``--raw`` keeps them as stored); the
manifest records the stored size and sha1, so ``verify`` can recompress and
prove the round trip. Chain 0 is identical on both discs and is taken once.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

from chains import CHAINS, ChainReader, available_chains, disc_number  # noqa: E402
from iso9660 import BOOT_RE, IsoImage                                    # noqa: E402
from sniff import sniff                                                  # noqa: E402
from toc import parse_toc                                                # noqa: E402
import region                                                            # noqa: E402

MANIFEST_FIELDS = ["area", "path", "sector", "size", "compressed", "usize", "sha1",
                   "kind", "disc", "bigfile", "bigfile_offset"]


def _entries(iso: IsoImage, chain: int):
    reader = ChainReader(iso, chain)
    return reader, parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])


def _sources(isos):
    """chain -> IsoImage, taking each chain from the first image that has it."""
    have = {}
    images = [IsoImage(path) for path in isos]
    region.warn_mixed(images)            # Japan Disc 1 + USA Disc 2, say
    for iso in images:
        for c in available_chains(iso):
            have.setdefault(c, iso)
    return have


def cmd_list(args) -> int:
    for chain, iso in sorted(_sources(args.iso).items()):
        if args.chain is not None and chain != args.chain:
            continue
        _, entries = _entries(iso, chain)
        for e in entries:
            flag = "C" if e.compressed else " "
            print(f"chain{chain} {flag} {e.sector:>9} {e.size:>11} {e.path}")
    return 0


# browse renders the music with these two (the driver's tables, libsd's reverb
# presets), so dump always copies them; --code copies the rest of the code
MUSIC_IOP = {"IOP\\KORGIVAG.IRX", "IOP\\LIBSD.IRX"}


def cmd_dump(args) -> int:
    import arx

    out = Path(args.out)
    rows = []
    for chain, iso in sorted(_sources(args.iso).items()):
        if args.chain is not None and chain != args.chain:
            continue
        reader, entries = _entries(iso, chain)
        if args.glob:
            entries = [e for e in entries if fnmatch.fnmatch(e.path.lower(), args.glob.lower())]
        print(f"chain {chain} (disc {disc_number(iso)}): {len(entries)} entries", file=sys.stderr)
        for i, e in enumerate(entries):
            dest = out / "dump" / f"chain{chain}" / Path(*e.path.split("\\"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha1()
            if e.compressed:
                raw = reader.read(e.byte_offset, e.size)
                h.update(raw)
                payload = raw if args.raw else arx.decompress(raw)
                dest.write_bytes(payload)
                head = payload[:16]
            else:
                head = b""
                with open(dest, "wb") as fh:
                    for block in reader.read_iter(e.byte_offset, e.size):
                        if not head:
                            head = bytes(block[:16])
                        h.update(block)
                        fh.write(block)
            bigfile, local = reader.locate(e.byte_offset)
            rows.append({
                "area": f"chain{chain}", "path": e.path, "sector": e.sector, "size": e.size,
                "compressed": int(e.compressed), "usize": e.usize or "", "sha1": h.hexdigest(),
                "kind": sniff(head, e.path), "disc": disc_number(iso),
                "bigfile": bigfile, "bigfile_offset": local,
            })
            if (i + 1) % 1000 == 0:
                print(f"  {i + 1}/{len(entries)}", file=sys.stderr)
    seen = set()
    for path in args.iso:
        iso = IsoImage(path)
        cdir = out / "browse" / "code"
        for name, f in iso.files.items():
            code = args.code and (name.endswith((".OVL", ".IRX", ".IMG")) or BOOT_RE.match(name))
            if f.is_dir or name in seen or not (code or name in MUSIC_IOP):
                continue
            seen.add(name)
            data = bytes(iso.read_file(name))
            dest = cdir / name.replace("\\", "/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rows.append({"area": "code", "path": name, "sector": f.lba, "size": f.size,
                         "compressed": 0, "usize": "", "sha1": hashlib.sha1(data).hexdigest(),
                         "kind": sniff(data[:16], name), "disc": disc_number(iso),
                         "bigfile": "(iso fs)", "bigfile_offset": f.lba * 2048})
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {out / 'manifest.csv'}", file=sys.stderr)
    return 0


def cmd_verify(args) -> int:
    """Re-hash the dump against the manifest. Decompressed ARX objects are
    recompressed with the kit's clone of Monolith's packer: a byte-identical
    result proves both directions of the codec on that object."""
    import arx

    out = Path(args.out)
    bad = checked = rearx = 0
    with open(out / "manifest.csv") as fh:
        for row in csv.DictReader(fh):
            if row["area"] == "code":
                p = out / "browse" / "code" / Path(*row["path"].split("\\"))
            else:
                p = out / "dump" / row["area"] / Path(*row["path"].split("\\"))
            if not p.exists():
                print(f"MISSING {p}")
                bad += 1
                continue
            data = p.read_bytes()
            if int(row["compressed"]) and not data.startswith(b"ARX\x00"):
                data = arx.compress(data)
                rearx += 1
            checked += 1
            if len(data) != int(row["size"]) or hashlib.sha1(data).hexdigest() != row["sha1"]:
                print(f"MISMATCH {p}")
                bad += 1
    print(f"verified {checked} objects ({rearx} recompressed from their decompressed dump), {bad} problems")
    return 1 if bad else 0


def cmd_browse(args) -> int:
    from browse import ALL_KINDS, build_browse

    kinds = [k.strip() for k in (args.kinds or ",".join(ALL_KINDS)).split(",") if k.strip()]
    unknown = [k for k in kinds if k not in ALL_KINDS]
    if unknown:
        print(f"unknown kind(s): {', '.join(unknown)} (choose from {', '.join(ALL_KINDS)})",
              file=sys.stderr)
        return 2
    return build_browse(Path(args.out), kinds, limit=args.limit, movie_audio=args.movie_audio)


def cmd_model(args) -> int:
    """One model straight from the disc to a .glb: the .pxy mesh with the texture pages
    (.txy) and skeleton (.xhr) of the same name."""
    import pxy
    import txy
    import xhr

    want = args.path.lower()
    stem = want[:-4] if want.endswith(".pxy") else want
    for chain, iso in sorted(_sources([args.iso]).items()):
        reader, entries = _entries(iso, chain)
        byp = {e.path.lower(): e for e in entries}
        e = byp.get(stem + ".pxy")
        if e is None:
            continue
        read = lambda ent: reader.read(ent.byte_offset, ent.size)
        mesh = pxy.parse(read(e))
        t, x = byp.get(stem + ".txy"), byp.get(stem + ".xhr")
        txy_data = read(t) if t else None
        try:
            tex_entries = txy.parse(txy_data)["entries"] if txy_data else None
        except Exception:
            txy_data = tex_entries = None
        skel = xhr.parse(read(x)) if x else None
        r = pxy.check(mesh, tex_entries)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            blob = pxy.to_glb(mesh, txy_data, skel)
        except pxy.PxyError as err:                  # skeleton shorter than the mesh's joint list
            if skel is None:
                raise
            print(f"skeleton not used: {err}", file=sys.stderr)
            blob, skel = pxy.to_glb(mesh, txy_data, None), None
        out.write_bytes(blob)
        print(f"{e.path}: {r.get('triangles', '?')} triangles, textures {'yes' if txy_data else 'no'}, "
              f"skeleton {'yes' if skel else 'no'}, checks "
              f"{'all pass' if not r['failed'] else 'FAILED: ' + '; '.join(r['failed'])} -> {out}")
        return 0
    print(f"{args.path}: no such .pxy on this disc (see `list`)", file=sys.stderr)
    return 1


def cmd_sb(args) -> int:
    import sb
    import sbl

    for chain, iso in sorted(_sources([args.iso]).items()):
        reader, entries = _entries(iso, chain)
        byp = {e.path.lower(): e for e in entries}
        e = byp.get(args.path.lower())
        if e is None:
            continue
        s = sb.parse(reader.read(e.byte_offset, e.size))
        bank = byp.get(region.bank_path(e.path).lower())   # None on Japan: inline text
        strings = sbl.parse(reader.read(bank.byte_offset, bank.size)) if bank else None
        print(sb.listing(s, strings))
        return 0
    print(f"no script {args.path!r} on this disc", file=sys.stderr)
    return 1


def cmd_natives(args) -> int:
    """Every native the disc's scripts call: class.function, how often, from how many
    scripts, and the argument-type signatures seen (most common first). With --out,
    also natives.tsv, one disassembly per script under listings/, and (for the demo's
    inline-text scripts) strings.tsv: every string a pushtext names."""
    import collections
    import sb

    calls, scripts, sigs = collections.Counter(), collections.defaultdict(set), collections.defaultdict(collections.Counter)
    strings, n = [], 0
    out = Path(args.out) if args.out else None
    for chain, iso in sorted(_sources(args.iso).items()):
        reader, entries = _entries(iso, chain)
        for e in entries:
            if e.ext not in ("sb", "bin") or (args.glob and not fnmatch.fnmatch(e.path.lower(), args.glob.lower())):
                continue
            data = reader.read(e.byte_offset, e.size)
            if data[:4] != sb.MAGIC:
                continue
            s = sb.parse(data)
            n += 1
            for name, types in sb.native_calls(s):
                calls[name] += 1
                scripts[name].add(e.path)
                sigs[name][types] += 1
            if out:
                dest = out / "listings" / Path(*e.path.split("\\")).with_suffix(".txt")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(sb.listing(s) + "\n", encoding="utf-8")
                strings += [(e.path, off, text) for off, text in s.inline_strings()]
    print(f"{n} scripts, {len(calls)} natives, {sum(calls.values())} calls")
    rows = []
    for name in sorted(calls, key=lambda k: (k.split(".")[0], k)):
        sig = "; ".join(f"({', '.join(t)}) x{c}" for t, c in sigs[name].most_common(3))
        rows.append((name, calls[name], len(scripts[name]), sig))
        print(f"  {name:<28} {calls[name]:>6} calls {len(scripts[name]):>4} scripts  {sig}")
    if out:
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "natives.tsv", "w", encoding="utf-8") as fh:
            fh.write("native\tcalls\tscripts\tsignatures\n")
            fh.writelines(f"{a}\t{b}\t{c}\t{d}\n" for a, b, c, d in rows)
        if strings:
            with open(out / "strings.tsv", "w", encoding="utf-8") as fh:
                fh.write("script\toffset\ttext\n")
                for p, o, t in strings:
                    t = t.replace("\t", " ").replace("\n", "\\n")
                    fh.write(f"{p}\t{o:#x}\t{t}\n")
        print(f"wrote {out / 'natives.tsv'}" + (f", {len(strings)} strings" if strings else "") + f", {n} listings")
    return 0


def cmd_recolor(args) -> int:
    import recolor

    if args.xtx:
        if not args.out:
            print("recolor --xtx needs --out (a new file; the input image is never patched)", file=sys.stderr)
            return 2
        return recolor.run_xtx(args.iso, args.out, args.xtx, args.hue)
    rules = recolor._rules(args.preset or (None if args.rule else "kosmos-hair"), args.rule)
    return recolor.run(args.iso, args.out, rules, args.hue, args.preview, args.dry_run)


def cmd_text_export(args) -> int:
    from textpack import export_text

    return export_text(args.iso, args.out)


def cmd_text_import(args) -> int:
    from textpack import import_text

    return import_text(args.iso, args.text, args.out, grow=args.grow, rebase=args.rebase)


def cmd_exe_export(args) -> int:
    import exetext

    return exetext.export(args.iso, args.out)


def cmd_exe_import(args) -> int:
    import exetext

    return exetext.import_(args.iso, args.text, args.out, retail_iso=args.retail)


def cmd_dialogue(args) -> int:
    import dialogue

    return dialogue.run(args.iso, Path(args.out), voice=args.voice, only=args.only)


def cmd_patch(args) -> int:
    from repack import RepackError, patch_copy

    replacements = {}
    for spec in args.set:
        try:
            target, src = spec.split("=", 1)
            chain_s, path = target.split(":", 1)
            chain = int(chain_s[5:] if chain_s.startswith("chain") else chain_s)
        except ValueError:
            print(f"bad --set {spec!r} (want 'chain0:some\\path=file')", file=sys.stderr)
            return 2
        replacements[(chain, path)] = Path(src).read_bytes()
    try:
        patch_copy(args.iso, args.out, replacements, grow=args.grow,
                   relocate=set(replacements) if args.relocate else ())
    except (RepackError, region.UnknownBuild) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_font_extract(args) -> int:
    """The region's two fonts (font0/font1 .us or .tex) as files, four PNG sheets
    each, the metrics as CSV, and the byte -> cell -> metric table of this build."""
    import font
    from repack import read_entry

    iso = IsoImage(args.iso)
    prof = region.region_of(iso)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        adr = font.Addressing.from_iso(iso)
    except font.FontError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    for name in prof.fonts:
        (out / name).write_bytes(read_entry(args.iso, 0, name))
        font.cmd_extract(str(out / name), str(out))
        font.write_table(out / f"{Path(name).stem}_bytes.csv", adr, font.Font.load(out / name))
    return 0


def cmd_font_rebuild(args) -> int:
    import font

    try:
        return font.cmd_rebuild(args.ref, args.sheets, args.out, args.metrics, args.allow_unchanged)
    except font.FontError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1


def cmd_lang_scan(args) -> int:
    import langpack

    return langpack.run("scan", args)


def cmd_lang_encode(args) -> int:
    import langpack

    return langpack.run("encode", args)


def cmd_lang_preview(args) -> int:
    import langpack

    return langpack.run("preview", args)


SELF_TESTS = ("region", "font", "toc", "txy", "embedded", "recolor", "sbl", "sb", "vsf", "prj",
              "sniff", "songfx", "spu2synth", "smallfiles", "toolfind", "gui", "dialogue",
              "minidisc", "moviesub", "textcodec", "textpack", "glyphs", "langpack", "exetext",
              "vif", "gltf", "xhr", "pxy", "models_sweep", "vuprog")


def cmd_self_test(args) -> int:
    import importlib

    rc = 0
    for name in SELF_TESTS:
        try:
            mod = importlib.import_module(name)
        except ImportError as e:            # a missing module or requirement fails the run
            print(f"--- {name}: FAILED to import ({e}); "
                  "install the requirements: python -m pip install -r requirements.txt")
            rc |= 1
            continue
        print(f"--- {name}")
        try:
            rc |= mod.self_test() or 0
        except Exception as e:              # a crash is a failure, and the other modules still run
            print(f"FAIL  {name}: the self-test stopped with {type(e).__name__}: {e}")
            rc |= 1
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Xenosaga Episode II (USA) disc extractor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="print every TOC entry")
    p.add_argument("--iso", action="append", required=True)
    p.add_argument("--chain", type=int)
    p.set_defaults(func=cmd_list)

    for name in ("dump", "extract"):
        p = sub.add_parser(name, help="unpack the disc(s) into OUT/dump + manifest.csv")
        p.add_argument("--iso", action="append", required=True,
                       help="a disc image; give Disc 1 and Disc 2 to get all three chains")
        p.add_argument("--out", required=True)
        p.add_argument("--chain", type=int)
        p.add_argument("--glob", help="only paths matching this (case-insensitive) pattern")
        p.add_argument("--raw", action="store_true", help="keep ARX objects compressed")
        p.add_argument("--code", action="store_true", help="also copy SLUS, overlays and every IOP module (the sound driver "
                       "and libsd, which the music render reads, are always copied)")
        p.set_defaults(func=cmd_dump)

    p = sub.add_parser("verify", help="re-hash a dump against its manifest")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("browse", help="convert a dump into viewable files under OUT/browse")
    p.add_argument("--out", required=True)
    p.add_argument("--kinds")
    p.add_argument("--limit", type=int, help="convert at most N objects per kind (sampling)")
    p.add_argument("--movie-audio", choices=("adx", "ac3", "both"), default="adx",
                   help="movies: ADX stereo as AAC (default), the AC3 5.1 track copied, or both tracks")
    p.set_defaults(func=cmd_browse)

    p = sub.add_parser("model", help="one model from the disc to a .glb for Blender (browse does them all)")
    p.add_argument("--iso", required=True)
    p.add_argument("--path", required=True, help="e.g. 'model\\char\\pc\\kosmos00.pxy'")
    p.add_argument("--out", required=True, help="the .glb to write")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("sb", help="disassemble one script, dialogue inline")
    p.add_argument("--iso", required=True)
    p.add_argument("--path", required=True)
    p.set_defaults(func=cmd_sb)

    p = sub.add_parser("natives", help="every script native with call counts and argument types (+ listings)")
    p.add_argument("--iso", action="append", required=True)
    p.add_argument("--glob", help="only scripts whose path matches (e.g. 'yamamoto\\think\\*')")
    p.add_argument("--out", help="write natives.tsv, listings/ and strings.tsv here")
    p.set_defaults(func=cmd_natives)

    p = sub.add_parser("recolor", help="hue-shift named textures in every copy on a disc copy")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", help="the modded copy to write (the original is never changed)")
    p.add_argument("--preset", choices=("kosmos-hair",), help="default when no --rule: kosmos-hair")
    p.add_argument("--rule", action="append", default=[], help="entry-name glob=all|blue, repeatable")
    p.add_argument("--hue", type=float, default=0.92, help="target hue 0..1 (0.92 = pink)")
    p.add_argument("--preview", help="write before/after PNGs of every edited entry here")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--xtx", help="colourise this true-colour .xtx instead (e.g. 'yajima\\titleobj.xtx')")
    p.set_defaults(func=cmd_recolor)

    p = sub.add_parser("text-export", help="dialogue and menu text to an editable UTF-8 tree")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_text_export)

    p = sub.add_parser("dialogue", help="every dialogue line in play order, movie subtitles as SRT, voice clips")
    p.add_argument("--iso", action="append", required=True,
                   help="a disc image; give Disc 1 and Disc 2 for every scene and movie")
    p.add_argument("--out", required=True)
    p.add_argument("--voice", action="store_true",
                   help="also cut one WAV per timed line from the movies and event voice tracks (needs ffmpeg)")
    p.add_argument("--only", action="append", help="only scenes/movies whose name contains this (repeatable)")
    p.set_defaults(func=cmd_dialogue)

    p = sub.add_parser("text-import", help="write an edited text tree back into a copy of the disc")
    p.add_argument("--iso", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--grow", action="store_true",
                   help="relocate a text bank that outgrows its slot to the end of the image, within "
                        "the room the game's loader has (read from code; not yet seen in the running game)")
    p.add_argument("--rebase", action="store_true",
                   help="apply the text to this disc even though it is not the one it was exported from "
                        "(another revision, or an image that is already patched); the manifest check is skipped")
    p.set_defaults(func=cmd_text_import)

    p = sub.add_parser("exe-export", help="the strings in the executables (boot ELF, OV01, OV02) to an editable tree")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_exe_export)

    p = sub.add_parser("exe-import", help="write edited executable strings in place into a copy of the disc (run last)")
    p.add_argument("--iso", required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--retail", help="the retail disc image: start from its executables, so an image that was "
                                    "exe-patched before can be done again")
    p.set_defaults(func=cmd_exe_import)

    p = sub.add_parser("font-extract", help="the fonts as PNG sheets + metrics CSV + the byte-to-cell table")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", required=True, help="directory for font0/font1, sheet PNGs, metrics CSV")
    p.set_defaults(func=cmd_font_extract)

    p = sub.add_parser("font-rebuild", help="a font file from edited sheets (and metrics)")
    p.add_argument("--ref", required=True, help="the retail font file to start from")
    p.add_argument("--sheets", required=True, help="directory with edited sheet PNGs")
    p.add_argument("--metrics", help="edited metrics CSV")
    p.add_argument("--allow-unchanged", action="store_true",
                   help="write a copy of the reference when no sheet or metrics were found (otherwise refused)")
    p.add_argument("--out", required=True, help="font file to write")
    p.set_defaults(func=cmd_font_rebuild)

    p = sub.add_parser("patch", help="replace objects in a copy of the disc")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--set", action="append", required=True)
    p.add_argument("--grow", action="store_true",
                   help="relocate an object that outgrows its slot to the end of the image")
    p.add_argument("--relocate", action="store_true",
                   help="append every --set object at the end of the image even if it fits (tests relocation)")
    p.set_defaults(func=cmd_patch)

    import langpack
    p = sub.add_parser("lang-scan", help="shape a translated tree in a new alphabet and draw its glyphs into "
                       "font0.us and font1.us (USA)")
    langpack.add_scan_args(p)
    p.set_defaults(func=cmd_lang_scan)

    p = sub.add_parser("lang-encode", help="turn a translated tree into the same tree with the new glyphs as "
                       "codes, ordered, wrapped and size-checked")
    langpack.add_encode_args(p)
    p.set_defaults(func=cmd_lang_encode)

    p = sub.add_parser("lang-preview", help="draw one line with the rebuilt font to a PNG")
    langpack.add_preview_args(p)
    p.set_defaults(func=cmd_lang_preview)

    p = sub.add_parser("self-test", help="run every module's self-test")
    p.set_defaults(func=cmd_self_test)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
