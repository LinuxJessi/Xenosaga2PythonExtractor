"""coverage.py — every place a player sees text in Episode II, and what the kit does with it.

``text-coverage`` prints one row per text source of a disc: what it is, the
command that exports and imports it, its count on this disc and its status.
The rows for the text tools (``text-export`` / ``text-import``, ``exe-export``
/ ``exe-import``) are counted from the disc with the same code those commands
run. The rows for text that is not stored as text (texture sheets, pictures,
movies) are matched against the disc's table of contents and carry the status
found when they were looked at; they need picture or video editing, not text
editing, and are listed apart.

Statuses:

* ``editable``: exported to the UTF-8 tree and imported back; grows within a
  limit the loader's code gives (banks, menus, subtitles).
* ``in place``: exported and imported, each string at most its slot.
* ``export only``: the kit writes a viewable copy (PNG, JPEG, MP4) and has no
  checked writer for it.
* ``not loaded``: on the disc, but no code of the USA build names it.

``--verify`` also exports every text and executable string into a temporary
folder and plans an import of the unedited trees onto each disc given: the
plan must change nothing. On the USA executable it also checks the prompts
read by the print function's control-code grammar (the save and boot
prompts): each must be listed with its exact end.

    python cli.py text-coverage --iso DISC1.iso [--iso DISC2.iso] [--verify] [--tsv OUT.tsv]
    python coverage.py --self-test
"""
from __future__ import annotations

import fnmatch
import os
import sys
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import region                                         # noqa: E402
from chains import CHAINS, ChainReader, available_chains  # noqa: E402
from iso9660 import IsoImage                          # noqa: E402
from toc import parse_toc                             # noqa: E402


@dataclass
class Row:
    group: str        # "text", "executable", "picture", "video", "other"
    source: str
    where: str
    command: str
    count: str
    status: str
    note: str = ""


# what each textpack kind is, for the report
KIND_ROWS = {
    "bank": ("dialogue and scene text (USA: one bank per scene or map)", "cf\\en\\*.sbl, model\\map\\en\\*.sbl, ...",
             "editable", "grows inside the room its loader has; --grow relocates within it (bank_budget)"),
    "inline": ("dialogue inside scripts (Japan, demo)", "cf\\*.sb, model\\map\\*.bin, ...", "in place", ""),
    "menu": ("menus, item/skill/location names, help text, database", "yosikawa\\*.txd", "editable",
             "within the file's sectors (the loader's static buffers)"),
    "battle": ("battle names and descriptions: party, enemies, skills, items, equipment, messages",
               "yamamoto\\batdat.us", "in place",
               "same length or shorter: which u16 fields are offsets is not known from the file (smallfiles.batdat_replace)"),
    "quest": ("quest, citizen and treasure text", "iwase\\*.txt", "editable",
              "each text within its field of the loader's record (QUEST_LAYOUTS), the file within its sectors"),
    "subtitle": ("movie subtitles", "movie\\en\\*.txt", "editable", "moviesub.check_edit; --grow relocates"),
    "icontitle": ("the save's title in the PS2 memory-card browser", "carddata.bin (icon.sys)", "in place",
                  "the slot and time characters the save writer stamps stay where they are (USA; Japan: edits refused, "
                  "its save writer not read)"),
}

# text that is not stored as text: (group, source, TOC globs, status, note). Looked at on the USA discs.
MEDIA_ROWS: List[Tuple[str, str, Tuple[str, ...], str, str]] = [
    ("picture", "UI sheets with words drawn in: battle HUD, menu tabs, database headers, decoder letters, "
                "'Loading...', 'FAILED!'",
     ("yosikawa\\obj01.xtx", "yosikawa\\seg01.xtx", "yosikawa\\g2_0?.xtx", "yosikawa\\ctrl01.xtx",
      "yosikawa\\ctrl_pic.xtx", "yosikawa\\battle01.xtx", "yosikawa\\bg01.xtx", "yosikawa\\font.xtx",
      "yamamoto\\stat\\stat.xtx", "omiya\\talk01.xtx", "omiya\\botan.xtx", "iwase\\img\\door\\door_alpha.xtx"),
     "export only", "browse writes PNG; recolor rewrites pixels in place (hue), no PNG-to-XTX import"),
    ("picture", "title menu words (NEW GAME, LOAD, INSTALL, ...)", ("yajima\\titleobj.xtx",), "export only",
     "ARX-compressed XTX; browse writes PNG"),
    ("picture", "synopsis sheets", ("yajima\\synopsis.bin",), "export only", "a pack of 2 XTX"),
    ("picture", "'The Story So Far' pages (prose drawn into JPEG)", ("yajima\\synopsis\\*.jpg",), "export only",
     "loader FUN_001e92b8; patch can replace a whole file, nothing checks its format or the loader's buffer"),
    ("picture", "'TO BE CONTINUED', 'GAME OVER', screen-adjust labels",
     ("yajima\\end.jpg", "yajima\\gameov1.jpg", "yajima\\mnt_set.jpg"), "export only",
     "loaders FUN_001e8950, FUN_001e8bb0, FUN_001ee730"),
    ("picture", "staff-credit images", ("credit.bin",), "export only", "a pack of 6 JPEG (FUN_002b31a0, ov02 FUN_00a80590)"),
    ("picture", "boot logos, 'DISC 1' / 'DISC 2', copyright line", ("yajima\\title.bin", "yajima\\title2.bin"),
     "export only", "ARX-compressed raw 512x448 RGBA (ov02 FUN_00a815f0)"),
    ("picture", "UMN banners", ("yosikawa\\umn?.jpg",), "export only",
     "no text seen in them (all eight looked at, 2026-11-20)"),
    ("picture", "signs and screens on map and model textures", ("*.txy",), "export only",
     "texture-import (another branch) writes txy; which textures carry words has not been surveyed"),
    ("video", "staff roll", ("movie\\epstaff.sfd",), "export only",
     "the credits are drawn into the video (frames looked at, 2026-11-20); movie\\en\\EPSTAFF.txt is its dialogue "
     "subtitles, not the credits. Re-encoding video is not part of the kit"),
    ("video", "event movies", ("movie\\*.sfd",), "export only",
     "subtitles are separate text (the subtitle row); words drawn into the picture are video"),
    ("video", "in-engine event clips", ("movie\\cfevent\\*.ipu",), "export only",
     "some carry drawn graphics (a sampled clip shows the UMN logo); not surveyed clip by clip"),
    ("other", "Japanese placeholder quest files", ("iwase\\questmes2.txt", "iwase\\citizen2.txt",
                                                   "iwase\\treasure2.txt"), "not loaded",
     "exported with the quest files; no USA executable names them"),
]

# prompts the print function's grammar reads past NUL parameters (USA SLUS_208.92 and OV02.OVL):
# (file role, VA, the end the string must have, its first characters)
GRAMMAR_FACTS = [
    ("boot", 0x002B7950, 0x002B797B, b"\x0b\x0d\x03\x0c \x80 \xa2\xa4"),
    ("boot", 0x00688498, 0x006884F6, b"\x0b\x0e\x01\x01\x00\x00\x00\x0d\x03\x19\x03Save Xeno"),
    ("boot", 0x006884F8, 0x00688557, b"\x0b\x0e\x01\x01\x00\x00\x00\x0d\x03\x19\x03Ending"),
    ("boot", 0x00688590, 0x006885D9, b"\x0b\x0e\x01\x01\x00\x00\x00\x0d\x03\x19\x03Save game"),
    ("boot", 0x006885E0, 0x00688643, b"\x0b\x0e\x01\x01\x00\x00\x00\x0d\x03\x19\x03Contin"),
    ("OV02.OVL", 0x00A83348, 0x00A8338D, b"\n\n\x19\x03Start game?"),
    ("OV02.OVL", 0x00A83390, 0x00A83433, b"\x19\x03Please insert"),
]


def _toc_paths(iso: IsoImage) -> List[Tuple[int, str]]:
    out = []
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        out += [(chain, e.path) for e in parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])]
    return out


def _text_rows(isos: List[IsoImage]) -> List[Row]:
    import textpack
    seen: Dict[Tuple[int, str], str] = {}
    files: Dict[str, int] = {}
    strings: Dict[str, int] = {}
    compressed: Dict[str, int] = {}
    for iso in isos:
        ctx = textpack.Context(iso)
        for chain, e, kind, stored, data, alloc, sizes in textpack._objects(iso):
            if (chain, e.path) in seen:
                continue
            seen[(chain, e.path)] = kind.name
            try:                                   # what text-export writes: an object with text that rebuilds
                text = kind.export(ctx, e.path, data, {"chain": chain, "alloc": alloc, "sizes": sizes})
                if not text or kind.rebuild(ctx, e.path, data, text) != data:
                    continue
                n = kind.count(ctx, data)
            except (textpack.TextError, ValueError):
                continue
            files[kind.name] = files.get(kind.name, 0) + 1
            strings[kind.name] = strings.get(kind.name, 0) + n
            if e.compressed:
                compressed[kind.name] = compressed.get(kind.name, 0) + 1
    rows = []
    for k in textpack.KINDS:
        if k.name not in files:
            continue
        what, where, status, note = KIND_ROWS.get(k.name, (k.name, "", "editable", ""))
        n = f"{files[k.name]} file(s)" + (f", {strings[k.name]} string(s)" if strings.get(k.name) else "")
        if compressed.get(k.name):
            note = (note + "; " if note else "") + f"{compressed[k.name]} stored compressed: import refuses those"
        rows.append(Row("text", what, where, "text-export / text-import", n, status, note))
    return rows


def _exe_rows(iso: IsoImage) -> Tuple[List[Row], Optional[object]]:
    import exetext
    try:
        prof = exetext._us_only(iso, "text-coverage")
    except region.UnknownBuild as e:
        return [Row("executable", "strings in the executables", "boot ELF, *.OVL", "exe-export / exe-import",
                    "-", "not supported", str(e))], None
    files = [(n, bytes(iso.read_file(n)), role) for n, role in exetext.disc_executables(iso)]
    es = exetext.ExeSet(files, prof)
    rows = []
    for name, exe in es.exes.items():
        strs = es.strings[name].values()
        debug = sum(es.debug_only(s) for s in strs)
        jp = sum(bool(exetext._euc_hint(s.raw)) for s in strs)
        ctl = sum(exetext.has_controls(s.raw) for s in strs)
        sysf = sum(bool(exetext.system_font_chars(s.raw)) for s in strs)
        twins = sum(bool(s.twins) for s in strs)
        note = (f"{ctl} with control codes (read by the print function's grammar), {debug} debug only "
                f"(FUN_00195760), {jp} read as Japanese developer text, {sysf} drawn partly with the 8x8 system "
                f"font" + (f", {twins} also in another file (edit both alike)" if twins else ""))
        rows.append(Row("executable", f"strings in {name}" + (" (boot ELF)" if exe.role == "boot" else ""),
                        name, "exe-export / exe-import", f"{len(es.strings[name])} strings", "in place", note))
    return rows, es


def grammar_facts(es) -> List[str]:
    """The prompts of GRAMMAR_FACTS that are missing or end elsewhere (empty: all found exactly)."""
    bad = []
    for role, va, end, head in GRAMMAR_FACTS:
        name = es.boot.name if role == "boot" and es.boot else role
        s = es.strings.get(name, {}).get(va)
        if s is None:
            bad.append(f"{name} 0x{va:08x}: not listed")
        elif va + len(s.raw) != end or not s.raw.startswith(head):
            bad.append(f"{name} 0x{va:08x}: ends at 0x{va + len(s.raw):08x}, expected 0x{end:08x}")
    return bad


def _media_rows(paths: List[Tuple[int, str]]) -> List[Row]:
    rows = []
    low = [(c, p, p.lower()) for c, p in paths]
    claimed = set()
    for group, what, globs, status, note in MEDIA_ROWS:
        hit = [(c, p) for c, p, lp in low if any(fnmatch.fnmatchcase(lp, g) for g in globs)
               and (c, p) not in claimed]
        if not hit:
            continue
        claimed |= set(hit)
        where = ", ".join(p for _, p in hit[:3]) + (f", ... ({len(hit)})" if len(hit) > 3 else "")
        rows.append(Row(group, what, where, "browse (viewing only)", f"{len(hit)} file(s)", status, note))
    return rows


def report(iso_paths: List[str], verify: bool = False) -> Tuple[List[Row], List[str]]:
    """(rows, problems). Problems are only found with ``verify``."""
    isos = [IsoImage(p) for p in iso_paths]
    problems: List[str] = []
    try:
        rows = _text_rows(isos)
        exe_rows, es = _exe_rows(isos[0])
        rows += exe_rows
        paths: List[Tuple[int, str]] = []
        for iso in isos:
            paths += [cp for cp in _toc_paths(iso) if cp not in paths]
        rows += _media_rows(paths)
        if verify and es is not None and es.boot is not None and es.boot.sha256 == region.US.exe_sha256:
            problems += [f"grammar: {b}" for b in grammar_facts(es)]
    finally:
        for iso in isos:
            iso.close()
    if verify:
        problems += _verify(iso_paths)
    return rows, problems


def _verify(iso_paths: List[str]) -> List[str]:
    """Each disc's text exported and planned back onto it; the executable strings exported from the
    first disc and planned onto every disc (the USA discs share one executable). Every plan of an
    unedited tree must be empty."""
    import contextlib
    import io

    import exetext
    import textpack
    problems = []
    with tempfile.TemporaryDirectory() as td:
        exe_dir = os.path.join(td, "exe")
        with contextlib.redirect_stderr(io.StringIO()):
            rc_e = exetext.export(iso_paths[0], exe_dir)
        for i, p in enumerate(iso_paths):
            name = os.path.basename(p)
            text_dir = os.path.join(td, f"text{i}")
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    rc_t = textpack.export_text(p, text_dir)
                    ch, pr = textpack.plan_import(p, text_dir) if not rc_t else ({}, ["text-export failed"])
                except region.UnknownBuild as e:
                    ch, pr = {}, [str(e)]
            if ch or pr:
                problems.append(f"{name}: the unedited text tree plans {len(ch)} change(s), {len(pr)} problem(s)"
                                + (f": {pr[0]}" if pr else ""))
            if not rc_e:
                ch, pr = exetext.plan(p, exe_dir)
                if ch or pr:
                    problems.append(f"{name}: the unedited executable tree plans {len(ch)} change(s), "
                                    f"{len(pr)} problem(s)" + (f": {pr[0]}" if pr else ""))
    return problems


def format_rows(rows: List[Row]) -> str:
    out = []
    titles = {"text": "Text the kit edits (text-export / text-import)",
              "executable": "Strings in the executables (exe-export / exe-import, run last)",
              "picture": "Words drawn into pictures (picture editing, not text editing)",
              "video": "Words drawn into video",
              "other": "Other"}
    for group in ("text", "executable", "picture", "video", "other"):
        rs = [r for r in rows if r.group == group]
        if not rs:
            continue
        out.append(f"== {titles[group]}")
        for r in rs:
            out.append(f"[{r.status}] {r.source}: {r.count}")
            out.append(f"    where: {r.where}")
            if r.note:
                out.append(f"    {r.note}")
        out.append("")
    return "\n".join(out)


def cmd(iso_paths: List[str], verify: bool = False, tsv: Optional[str] = None) -> int:
    rows, problems = report(iso_paths, verify)
    print(format_rows(rows))
    if tsv:
        import csv
        with open(tsv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter="\t", lineterminator="\n")
            w.writerow(["group", "source", "where", "command", "count", "status", "note"])
            for r in rows:
                w.writerow([r.group, r.source, r.where, r.command, r.count, r.status, r.note])
    if verify:
        for p in problems:
            print("FAILED: " + p)
        print("verify: " + ("every unedited tree imports as no change; the control-code prompts are listed with "
                            "their exact ends" if not problems else f"{len(problems)} problem(s)"))
    return 1 if problems else 0


# ------------------------------------------------------------------ self-test

def self_test() -> int:
    import contextlib
    import io
    import struct

    import exetext
    import minidisc
    import sbl
    import smallfiles
    import textpack
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    boot = exetext.synthetic_boot()
    minidisc.register_test_executable(boot)
    font = minidisc.synthetic_font(384)
    icon_sys = bytearray(964)
    icon_sys[:4] = b"PS2D"
    title = "ＸｅｎｏｓａｇａＥＰＩＩ−００［００：００］".encode("shift_jis")
    struct.pack_into("<H", icon_sys, textpack.ICON_BREAK_AT, 30)
    icon_sys[textpack.ICON_TITLE_AT:textpack.ICON_TITLE_AT + len(title)] = title
    files = {"font0.us": font, "font1.us": font,
             "cf\\en\\sc0001.sbl": sbl.build([b"[A]Hello.", b"Bye."]),
             "carddata.bin": smallfiles.build_pack([bytes(icon_sys), b"\x00\x00\x01\x00" + bytes(64)]),
             "yosikawa\\obj01.xtx": b"XTX\x00" + bytes(60),
             "movie\\EPSTAFF.sfd": bytes(2048),
             "iwase\\QuestMes2.txt": b'No.1\r\n{\r\n\tSubject:"x"\r\n}\r\n'}
    with tempfile.TemporaryDirectory() as td:
        iso = minidisc.build(os.path.join(td, "c.iso"), files, elf=boot)
        with contextlib.redirect_stderr(io.StringIO()):
            rows, problems = report([str(iso)], verify=True)
        by = {r.source.split(":")[0]: r for r in rows}
        text = {r.source: r for r in rows if r.group == "text"}
        check(any("dialogue" in s and r.count == "1 file(s), 2 string(s)" for s, r in text.items()),
              "the dialogue bank is counted from the disc: 1 file, 2 strings")
        check(any("memory-card" in s and r.status == "in place" for s, r in text.items()),
              "the memory-card title is a text row")
        exe = [r for r in rows if r.group == "executable"]
        check(len(exe) == 1 and exe[0].count == f"{len(exetext.ExeSet([('SLUS_208.92', boot, 'boot')]).strings['SLUS_208.92'])} strings"
              and "control codes" in exe[0].note, "the executable row counts what exe-export lists")
        pics = [r for r in rows if r.group == "picture"]
        check(len(pics) == 1 and "yosikawa\\obj01.xtx" in pics[0].where and pics[0].status == "export only",
              "a UI sheet on the disc is listed as picture text, export only; sheets not on the disc are not")
        vids = [r for r in rows if r.group == "video"]
        check(len(vids) == 1 and "staff roll" in vids[0].source, "the staff-roll movie is video text, matched once")
        check(any(r.status == "not loaded" for r in rows), "the placeholder quest file is 'not loaded'")
        check(problems == [], "verify: the unedited trees of the mini disc import as no change")
        out = format_rows(rows)
        check(out.index("== Text the kit edits") < out.index("== Strings in the executables")
              < out.index("== Words drawn into pictures"), "the report lists text, executables, then pictures")
        del by
    # the facts about the retail prompts are checked against a scan, not against themselves
    es = exetext.ExeSet([("SLUS_208.92", boot, "boot")])
    check(len(grammar_facts(es)) == len(GRAMMAR_FACTS), "on a build without those prompts every fact is reported "
                                                       "missing (the check can fail)")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--iso", action="append")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--tsv")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.iso:
        ap.print_help()
        return 2
    return cmd(a.iso, a.verify, a.tsv)


if __name__ == "__main__":
    sys.exit(main())
