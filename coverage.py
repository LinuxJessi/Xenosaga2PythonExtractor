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
* ``no text``: pictures that were looked at and carry no words (listed so
  that nobody has to look again).

``--verify`` also exports every text and executable string into a temporary
folder and plans an import of the unedited trees onto each disc given: the
plan must change nothing. On the USA executable it also checks the prompts
read by the print function's control-code grammar (the save and boot
prompts): each must be listed with its exact end; and it reads every texture
entry of ``TEX_WORDS`` (texture pages with words a player reads, found by
looking) from the disc: each must be where the list says; and it tries the
battle database's growth writer on the disc's own file (``batdat_probe``).
``--tex-words`` prints the texture list.

    python cli.py text-coverage --iso DISC1.iso [--iso DISC2.iso] [--verify] [--tsv OUT.tsv] [--tex-words]
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
             "editable", "grows inside the room its loader has; --grow relocates within it (bank_budget). The USA "
                         "build reads its banks and subtitles from en\\ only: the language byte (DAT_0063f87a) is also "
                         "the Subtitles switch, and both of its values, 0 and 2, pick 'en' in FUN_001948e8's table"),
    "inline": ("dialogue inside scripts (Japan, demo)", "cf\\*.sb, model\\map\\*.bin, ...", "in place", ""),
    "menu": ("menus, item/skill/location names, help text, database, the Skill Tutorial", "yosikawa\\*.txd", "editable",
             "within the file's sectors (the loader's static buffers)"),
    "battle": ("battle names and descriptions: party, enemies, skills, items, equipment, messages",
               "yamamoto\\batdat.us", "editable",
               "USA: a longer string moves the rest of its section down, through the string fields OV01's getters "
               "read (smallfiles.BATDAT_STRING_FIELDS), the file within its sectors; section 17 and the Japanese "
               "file in place, same length or shorter"),
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
    ("picture", "UMN banners", ("yosikawa\\umn?.jpg",), "no text",
     "no text seen in them (all eight looked at, 2026-11-20)"),
    ("picture", "party-menu labels '<< COMBATANT >>' and '<< RESERVE >>', and '<< ATTACKER >>', which no executable "
                "names",
     ("yosikawa\\model\\atk.txy", "yosikawa\\model\\rsv.txy", "yosikawa\\atk.txy"), "export only",
     "entries dn_atk02, dn_rsv02 and dn_atk (looked at, 2026-11-20; RESERVE is drawn so that it reads RESERUE, as in "
     "the Japanese build's dn_rsv). SLUS_208.92 names model\\atk.txy and model\\rsv.txy (pointer table at "
     "0x003EB098), but not yosikawa\\atk.txy: whether ATTACKER is ever shown is not known. browse writes PNG; "
     "texture-import (another branch, not merged) writes standalone txy pages"),
    ("picture", "battle pop-up words 'Boost Bonus' and 'Stock Bonus'", ("simajiri\\esp\\default.esp",), "export only",
     "texture pages inside an effect file (embedded.py): de_moji01 at 0x14DC0, de_moji02 at 0x15A40 (looked at, "
     "2026-11-20; which screen shows them was not seen in the game). browse writes them under "
     "textures_png/_embedded; texture-import (another branch, not merged) writes embedded pages"),
    ("picture", "glyph effect: the kanji \u546a \u82e6 \u6bba \u6028 \u59ac \u81ed \u5bd2 \u6697 and the word CRY",
     ("simajiri\\esd\\pe820_004.esd",), "export only",
     "nine texture pages moji01-moji09 inside the effect file (0x1C680-0x56CC0), kept in Japanese by the USA "
     "build (looked at, 2026-11-20; which attack plays it was not seen in the game). The other letter-sprite "
     "effects carry decorative Greek or made-up script, not words"),
    ("picture", "the ANIMA logo with its small print on the title backdrop", ("yajima\\titlebg2.bin",), "export only",
     "entry anima_logo of the texture page at 0x583C40 inside the backdrop (embedded.py); browse writes it under "
     "textures_png/_embedded"),
    ("picture", "quest-board pictures: signs in the citizen portraits, numbers on the item cards",
     ("iwase\\img\\citizen\\*.xtx", "iwase\\img\\item\\*.xtx"), "export only",
     "minor drawn words: shop signs behind some of the 50 citizens ('MADE IN HEAVEN', 'NO! TRESPASSING', "
     "'CLOSED', 'ROBO-FOUNDRY', 'UNUS MUNDUS NEWS', 'WEAPON', ...) and digits on some of the 18 item cards "
     "(looked at, 2026-11-20). browse writes PNG cut by the masks ctz_alpha.xtx / item_alpha.xtx; "
     "texture-import (another branch, not merged) writes them"),
    ("picture", "door pictures of the quest board", ("iwase\\img\\door\\doorimg.dat",), "no text",
     "a pack of 18 XTX in its own format (u32 count 0x12, u32 offsets, the first XTX at 0x50); no words in any of "
     "them (looked at, 2026-11-20). browse does not unpack it"),
    ("picture", "the disc-change picture and the boot images", ("yajima\\diskchg.jpg", "yajima\\im.bin"), "no text",
     "art behind prompts the executable prints (im.bin: a pack of 4 JPEG); no words in them (looked at, 2026-11-20)"),
    ("picture", "the matumoto folder's textures (Tra*, Tre*, Item*, Save_P*, Ret_P*, Evs_P, look)", ("matumoto\\*.txy",),
     "no text", "only logos (EVS, UMN, AWGS), no words (looked at, 2026-11-20)"),
    ("picture", "signs and screens on map and model textures", ("*.txy",), "export only",
     "surveyed by OCR, 2026-11-20: at least 109 files carry words, among them status words the player reads "
     "(E2_ELS04 'SEARCH IN PROGRESS / STANDBY / Locked / Unlocked', 'THIS ELEVATOR CANNOT BE USED NOW'), and 12 "
     "entries whose names end in _us where the Japanese build has the same entry without the suffix, most with "
     "another picture (redrawn for the USA build); the named entries are listed with "
     "--tex-words and docs/MODDING.md. OCR misses small, stylised and Japanese text, so this is a lower bound. A "
     "survey of entry names (moji, kanban, poster, sign, text, menu, ...) lists 1,302 such entries in 238 files, "
     "embedded blocks included; a name is a hint, not proof of words. "
     "texture-import (another branch, not merged) writes txy"),
    ("video", "staff roll", ("movie\\epstaff.sfd",), "export only",
     "the credits are drawn into the video (frames looked at, 2026-11-20); movie\\en\\EPSTAFF.txt is its dialogue "
     "subtitles, not the credits. Re-encoding video is not part of the kit"),
    ("video", "event movies", ("movie\\*.sfd",), "export only",
     "subtitles are separate text (the subtitle row). Words drawn into the picture (OCR of a frame every 3 s, "
     "2026-11-20, a lower bound): s010100 the opening cards ('Namco Presents', 'A MONOLITH SOFTWARE INC. "
     "Production', 'Xenosaga EPISODE II Jenseits von Gut und B\u00f6se'), s010310 interface screens "
     "('COMMUNICATION', 'CALL SIGN TO...', 'SEND', 'Connection Viewer', 'Receiving Device', 'Waveform:'), "
     "s010610 'ROOF GARDEN', s020520 a 'SYSTEM' screen (OCR reads 'CRMUS'), s031000 'Unus Mundus Network', "
     "s050400 'DINAH' / 'VECTOR', s080900 'CATHERINE' / 'CECILIA', EP07 'TO BE CONTINUED'. s020310 has a USA "
     "voice track that differs from the Japanese one and no subtitle file in either region (docs/DIALOGUE.md)"),
    ("video", "in-engine event clips", ("movie\\cfevent\\*.ipu",), "export only",
     "words drawn in (OCR of every 20th frame, 2026-11-20, a lower bound): moni_111 (monitor text), noise_umn and "
     "noise_umn_l (the UMN logo)"),
    ("other", "Japanese placeholder quest files", ("iwase\\questmes2.txt", "iwase\\citizen2.txt",
                                                   "iwase\\treasure2.txt"), "not loaded",
     "exported with the quest files; no USA executable names them"),
]

# Texture entries with words a player reads, found by looking (USA discs, 2026-11-20): (TOC path, offset of
# the embedded txy block or None for a standalone .txy, entry name, what is drawn). ``--verify`` checks on a
# USA disc that every entry is where this list says; ``--tex-words`` prints the list. Not every texture with
# words is here: the *.txy row gives the survey's count.
TEX_WORDS: List[Tuple[str, Optional[int], str, str]] = [
    ("yosikawa\\model\\atk.txy", None, "dn_atk02", "<< COMBATANT >> (party menu; USA name, Japanese dn_atk)"),
    ("yosikawa\\model\\rsv.txy", None, "dn_rsv02", "<< RESERVE >>, drawn so that it reads RESERUE (party menu; USA "
                                                 "name, Japanese dn_rsv)"),
    ("yosikawa\\atk.txy", None, "dn_atk", "<< ATTACKER >> (no executable names this file)"),
    ("simajiri\\esp\\default.esp", 0x14DC0, "de_moji01", "Boost Bonus (battle pop-up)"),
    ("simajiri\\esp\\default.esp", 0x15A40, "de_moji02", "Stock Bonus (battle pop-up)"),
    ("simajiri\\esd\\pe820_004.esd", 0x21140, "moji01", "\u546a (glyph effect)"),
    ("simajiri\\esd\\pe820_004.esd", 0x31C40, "moji02", "\u82e6"),
    ("simajiri\\esd\\pe820_004.esd", 0x50780, "moji03", "\u6bba"),
    ("simajiri\\esd\\pe820_004.esd", 0x56CC0, "moji04", "\u6028"),
    ("simajiri\\esd\\pe820_004.esd", 0x1C680, "moji05", "\u59ac"),
    ("simajiri\\esd\\pe820_004.esd", 0x25C00, "moji06", "\u81ed"),
    ("simajiri\\esd\\pe820_004.esd", 0x2D180, "moji07", "\u5bd2"),
    ("simajiri\\esd\\pe820_004.esd", 0x36700, "moji08", "\u6697"),
    ("simajiri\\esd\\pe820_004.esd", 0x4BCC0, "moji09", "CRY"),
    ("yajima\\titlebg2.bin", 0x583C40, "anima_logo", "ANIMA logo with small print (title backdrop)"),
    ("model\\map\\E2_ELS04.txy", None, "moji_us", "SEARCH IN PROGRESS / COMPARTMENT EXTENSION / STANDBY / Locked / "
                                               "Unlocked (USA; the Japanese moji ends USABLE / NONUSABLE)"),
    ("model\\map\\E2_ELS04.txy", None, "attention", "ATTENTION / THIS ELEVATOR CANNOT BE USED NOW"),
    ("model\\map\\E2_ELS07.txy", None, "moji", "SEARCH IN PROGRESS / COMPARTMENT EXTENSION / STANDBY / USABLE / "
                                          "NONUSABLE"),
    ("model\\map\\E2_MIL03.txy", None, "moji", "the same as E2_ELS07 moji"),
    ("model\\map\\E2_MIL09.txy", None, "moji", "the same as E2_ELS07 moji"),
    ("model\\map\\E2_DYU03.txy", None, "e_monita_02", "NOW AN ELEVATOR CANNOT BE USED"),
    ("model\\map\\E2_MID04.txy", None, "E2_MID_04_089_us", "No Entry (USA; Japanese E2_MID_04_089: NO THOROUGHFARE)"),
    ("model\\map\\E2_KUK11.txy", None, "kanban02_01_us", "FISHMAN CLASSIC shop sign (USA)"),
    ("model\\map\\E2_UZU01.txy", None, "book_kou_01_us", "kanji numerals (USA; Japanese book_kou_01: \u7532 \u4e59 \u4e19)"),
    ("model\\map\\E2_UZU01.txy", None, "book_hei_01_us", "kanji numerals (USA)"),
    ("model\\map\\E2_UZU01.txy", None, "book_bou_01_us", "kanji numerals (USA)"),
    ("model\\map\\E2_UZU01.txy", None, "book_kou_us", "kanji numerals (USA)"),
    ("model\\map\\E2_UZU01.txy", None, "book_hei_us", "kanji numerals (USA)"),
    ("model\\map\\E2_UZU01.txy", None, "book_bou_us", "kanji numerals (USA)"),
    ("model\\map\\E2_MID01.txy", None, "m01_con0000_us", "no words (USA; the Japanese m01_con0000 has a label)"),
    ("model\\map\\E2_MID01.txy", None, "m01_con0000_1_us", "no words (USA)"),
    ("model\\map\\E2_MID02.txy", None, "m01_con0000_us", "no words (USA)"),
    ("model\\map\\E2_MIL12.txy", None, "warning", "WARNING"),
    ("model\\map\\E2_KUK11.txy", None, "kanbanmoji", "Reel Dude's Fishing Lab. (shop sign)"),
    ("model\\map\\E2_KUK02.txy", None, "yado_kanban01", "OUR TREASURE / INN.SHOP (sign)"),
    ("model\\map\\E2_KUK05.txy", None, "kanban01", "IRONMAN (poster)"),
    ("model\\map\\E2_ELS05.txy", None, "font0001", "MENU, with a line of small print"),
    ("model\\map\\CFEVE04.txy", None, "koukoku_moji02", "SECOND MILTIA SPACE PORT"),
    ("model\\map\\CFEVE03.txy", None, "text", "five lines of blocky English words, partly legible (WE ... THE ONE)"),
    ("model\\map\\E2_MIL02.txy", None, "poster07", "SECOND MILTIA AEROSPACE INDUSTRY"),
    ("model\\map\\E2_MOM11.txy", None, "toilet_poster", "SURPRISE & DISCOVERY!! Dogs love GRACE ONE!! / GRACEFUL DOG "
                                                       "FOOD (poster)"),
    ("model\\map\\E2_MOM11.txy", None, "kanban", "NEW TASTY FLAVOR / VIRGINIAN BRIGHT / refresh yourself!! (sign)"),
    ("model\\map\\E2_DYU03.txy", None, "keijiban4", "DURANDAL, with columns of small print (notice board)"),
    ("model\\map\\E2_MIL04.txy", None, "nagare_moji03", "INFORMATION SERVICE COUNTER"),
]


def tex_word_problems(read, facts=None) -> List[str]:
    """The TEX_WORDS entries (or ``facts``) that are not where the list says. ``read(path)`` returns the
    object's bytes (decompressed) or None when the disc has no such object."""
    import embedded
    import txy
    bad = []
    cache: Dict[Tuple[str, Optional[int]], Optional[List[str]]] = {}
    for path, block, entry, _ in (TEX_WORDS if facts is None else facts):
        if (path, block) not in cache:
            data = read(path)
            names = None
            if data is not None:
                try:
                    if block is None:
                        names = [e["name"] for e in txy.parse(data)["entries"]]
                    else:
                        found = dict(embedded.iter_blocks(data))
                        if block in found:
                            names = [e["name"] for e in txy.parse(found[block])["entries"]]
                except txy.TxyError:
                    names = None
            cache[(path, block)] = names
        names = cache[(path, block)]
        where = path + (f" block 0x{block:X}" if block is not None else "")
        if names is None:
            bad.append(f"{where}: no such " + ("txy block" if block is not None else "texture file"))
        elif entry not in names:
            bad.append(f"{where}: no entry {entry}")
    return bad


def batdat_probe(data: Optional[bytes]) -> List[str]:
    """Problems found when the battle database's growth writer is tried on the disc's own file: unedited it
    must give the file back, and one string of every section it may grow, made one byte longer, must fit
    the file's sectors and come back from the next export with every other string unchanged."""
    import smallfiles
    if data is None:
        return []
    bad = []
    try:
        if smallfiles.batdat_rebuild(data, {}, grow=True) != data:
            bad.append("the unedited rebuild is not the file")
        raw = smallfiles.batdat_raw_strings(data)
        edits = {}
        for si in smallfiles.BATDAT_STRING_FIELDS:
            rows = [(o, r) for s, o, r in raw if s == si and r]
            if rows:
                o, r = rows[len(rows) // 2]
                edits[(si, o)] = r + r[-1:]
        blob = smallfiles.batdat_rebuild(data, edits, grow=True, max_size=-(-len(data) // 2048) * 2048)
        got = sorted((si, r) for si, _, r in smallfiles.batdat_raw_strings(blob))
        want = sorted([(si, r) for si, o, r in raw if (si, o) not in edits] + [(k[0], v) for k, v in edits.items()])
        if got != want:
            bad.append("after growing one string per section the next export finds other strings")
    except smallfiles.SmallFileError as e:
        bad.append(str(e))
    return bad


def format_tex_words() -> str:
    out = ["== Texture entries with words (picture editing; looked at on the USA discs, 2026-11-20)"]
    for path, block, entry, words in TEX_WORDS:
        out.append(f"{path}" + (f" @0x{block:X}" if block is not None else "") + f"  {entry}: {words}")
    return "\n".join(out) + "\n"


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
    ("boot", 0x006899D0, 0x006899D8, b"\x0d\x02\xa5\xcf\xa5\xb7\xa5\xb4"),       # field-debug label, 3 kana
]

# strings the USA scripts pass to natives that draw them, counted on the retail discs (both discs: all in chain
# 0): native -> (calls, distinct strings, banks). ``--verify`` recounts them with dialogue.native_calls.
NATIVE_FACTS = {"bat.namePut": (1363, 416, 113), "samp.fontPut": (28, 3, 17)}


def _toc_paths(iso: IsoImage) -> List[Tuple[int, str]]:
    out = []
    for chain in available_chains(iso):
        reader = ChainReader(iso, chain)
        out += [(chain, e.path) for e in parse_toc(bytes(reader.toc_bytes()), CHAINS[chain][0])]
    return out


def _text_rows(isos: List[IsoImage]) -> List[Row]:
    import sbl
    import textpack
    seen: Dict[Tuple[int, str], str] = {}
    builds = set()
    files: Dict[str, int] = {}
    strings: Dict[str, int] = {}
    compressed: Dict[str, int] = {}
    slots = tutorial = 0
    hints: Dict[str, int] = {}
    hint_files = set()
    for iso in isos:
        ctx = textpack.Context(iso)
        builds.add(ctx.build)
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
            if ctx.build == "us" and kind.name in ("bank", "menu"):
                strs = sbl.parse(data) if kind.name == "bank" else textpack.txd_parse(data)[0]
                for s in strs:
                    h = textpack._jp_hint(s)
                    if h:
                        hints[h[1:h.index(":")]] = hints.get(h[1:h.index(":")], 0) + 1
                        hint_files.add(e.path)
                if kind.name == "menu":
                    low = e.path.lower()
                    if low == textpack.SLOT_MENU:
                        slots += sum(bool(textpack.slot_markers(s)) for s in strs)
                    elif low == textpack.TUTORIAL:
                        tutorial += sum(textpack.tutorial_role(s) != "text" for s in strs)
            if e.compressed:
                compressed[kind.name] = compressed.get(kind.name, 0) + 1
    rows = []
    for k in textpack.KINDS:
        if k.name not in files:
            continue
        what, where, status, note = KIND_ROWS.get(k.name, (k.name, "", "editable", ""))
        if k.name == "battle" and "us" not in builds:  # strings grow only on the USA build
            where, status, note = "yamamoto\\batdat.bin", "in place", "same length or shorter"
        n = f"{files[k.name]} file(s)" + (f", {strings[k.name]} string(s)" if strings.get(k.name) else "")
        if compressed.get(k.name):
            note = (note + "; " if note else "") + f"{compressed[k.name]} stored compressed: import refuses those"
        if k.name == "menu" and (slots or tutorial):
            note += (f". Guarded: {slots} MainMenu strings hold the memory-card slot placeholder that FUN_00238038 "
                     f"overwrites (an edit must keep it, findable by the game's walk); {tutorial} Tutorial.txd entries "
                     f"are commands FUN_00215f10 runs (refused if changed)")
        rows.append(Row("text", what, where, "text-export / text-import", n, status, note))
    if hints:
        rows.append(Row("other", "Japanese developer text left in the USA files (test lines such as "
                                 "'11テストメッセージ', notes in MainMenu.txd)",
                        f"{len(hint_files)} bank and menu files", "text-export / text-import",
                        ", ".join(f"{v} {k.upper()} string(s)" for k, v in sorted(hints.items())), "editable",
                        "exported with a #sjis: or #euc: reading line above the block; whether a player can reach any "
                        "of them is not verified (the test lines sit behind talk.check results 11 and 12 of a template "
                        "routine); probably debug"))
    return rows


def native_counts(iso_paths: List[str]) -> Dict[str, Tuple[int, int, int]]:
    """native -> (calls, distinct strings, banks) for the natives of NATIVE_FACTS, from the scripts."""
    import dialogue
    uses = dialogue.native_text_uses(dialogue.Disc(iso_paths), NATIVE_FACTS)
    return {n: (len(v), len({s for *_, s in v}), len({(c, p.lower()) for c, p, _, _ in v})) for n, v in uses.items()}


def _native_rows(counts: Dict[str, Tuple[int, int, int]]) -> List[Row]:
    rows = []
    calls, distinct, banks = counts.get("bat.namePut", (0, 0, 0))
    if calls:
        rows.append(Row("text", "battle banners: the enemy skill and action names a battle shows (bat.namePut)",
                        "inside the bank files (model\\map\\en\\*.sbl)", "text-export / text-import (bank files)",
                        f"{calls} call(s), {distinct} distinct string(s) in {banks} bank(s)", "editable",
                        "marked '# battle banner' in the export; a name has a copy in each bank that uses it: "
                        "text-import notes copies edited unalike, refuses a banner that ends with a one-byte letter "
                        "from 0x80 up (FUN_002353b8 would step over the terminator) and notes one wider than any "
                        "banner of the disc (FUN_002355d8 sizes the box with no limit). The dialogue command lists them "
                        "as 'banner'"))
    calls, distinct, banks = counts.get("samp.fontPut", (0, 0, 0))
    if calls:
        rows.append(Row("other", "digit strings drawn by samp.fontPut ('100', '802', '102')",
                        "inside the bank files (cf\\en\\*.sbl)", "text-export / text-import (bank files)",
                        f"{calls} call(s), {distinct} distinct string(s) in {banks} bank(s)", "editable",
                        "probably a debug or template routine (not verified in the game)"))
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


def _reader(iso_path: str):
    """read(path) -> the chain-0 object's bytes (decompressed), None when the disc has no such object."""
    import repack

    def read(path: str) -> Optional[bytes]:
        try:
            return repack.read_entry(iso_path, 0, path)
        except repack.RepackError:
            return None
    return read


def report(iso_paths: List[str], verify: bool = False, done: Optional[List[str]] = None
           ) -> Tuple[List[Row], List[str]]:
    """(rows, problems). Problems are only found with ``verify``; ``done`` collects what was checked."""
    done = [] if done is None else done
    isos = [IsoImage(p) for p in iso_paths]
    problems: List[str] = []
    try:
        rows = _text_rows(isos)
        counts = native_counts(iso_paths)
        rows += _native_rows(counts)
        exe_rows, es = _exe_rows(isos[0])
        rows += exe_rows
        paths: List[Tuple[int, str]] = []
        for iso in isos:
            paths += [cp for cp in _toc_paths(iso) if cp not in paths]
        rows += _media_rows(paths)
        if verify and es is not None and es.boot is not None and es.boot.sha256 == region.US.exe_sha256:
            problems += [f"grammar: {b}" for b in grammar_facts(es)]
            read = _reader(iso_paths[0])
            problems += [f"texture words: {b}" for b in tex_word_problems(read)]
            problems += [f"battle database: {b}" for b in batdat_probe(read("yamamoto\\batdat.us"))]
            problems += [f"script text: {n} counts {counts.get(n)}, the retail discs {want}"
                         for n, want in NATIVE_FACTS.items() if counts.get(n) != want]
            done += ["the control-code prompts are listed with their exact ends",
                     "the battle banners and fontPut strings are counted as on the retail discs",
                     "the texture entries with words are where the list says",
                     "the battle database's strings grow on this disc's file"]
    finally:
        for iso in isos:
            iso.close()
    if verify:
        problems += _verify(iso_paths)
        done.insert(0, "every unedited tree imports as no change")
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


def cmd(iso_paths: List[str], verify: bool = False, tsv: Optional[str] = None, tex_words: bool = False) -> int:
    done: List[str] = []
    rows, problems = report(iso_paths, verify, done)
    print(format_rows(rows))
    if tex_words:
        print(format_tex_words())
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
        print("verify: " + ("; ".join(done) if not problems else f"{len(problems)} problem(s)"))
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
    import sb

    def op(o, v=None):
        return struct.pack("<H", o) if v is None else struct.pack("<HHH", o, v & 0xFFFF, (v >> 16) & 0xFFFF)
    # a scene script: bat.namePut(1, 2, 3, line 1, 5) twice, samp.fontPut(line 0) once
    put = (op(0x04, 1) + op(0x28) + op(0x04, 2) + op(0x28) + op(0x04, 3) + op(0x28) + op(0x06, 0x200001) + op(0x2A)
           + op(0x04, 5) + op(0x28) + op(0x04, 5) + op(0x15, 0) + op(0x19))
    code = put + put + op(0x06, 0x200000) + op(0x2A) + op(0x04, 1) + op(0x15, 1) + op(0x19) + op(0x13)
    files = {"font0.us": font, "font1.us": font,
             "cf\\sc0001.sb": sb.build_script(code, [("bat", "namePut"), ("samp", "fontPut")]),
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
        ban = [r for r in rows if "bat.namePut" in r.source]
        fp = [r for r in rows if "samp.fontPut" in r.source]
        check(len(ban) == 1 and ban[0].count == "2 call(s), 1 distinct string(s) in 1 bank(s)" and len(fp) == 1
              and fp[0].count == "1 call(s), 1 distinct string(s) in 1 bank(s)",
              "battle banners and fontPut strings are counted from the scripts' calls")
        check(native_counts([str(iso)]) != NATIVE_FACTS, "the retail script counts differ from a mini disc's (the "
                                                          "--verify check can fail)")
        check(problems == [], "verify: the unedited trees of the mini disc import as no change")
        out = format_rows(rows)
        check(out.index("== Text the kit edits") < out.index("== Strings in the executables")
              < out.index("== Words drawn into pictures"), "the report lists text, executables, then pictures")
        del by
    # the picture rows: a specific row claims its files before the generic *.txy row does
    toc = [(0, p) for p in ("yosikawa\\model\\atk.txy", "yosikawa\\model\\rsv.txy", "yosikawa\\atk.txy",
                            "yosikawa\\model\\atk.pxy", "yosikawa\\skillmdl\\skilball01.txy",
                            "model\\map\\E2_ELS04.txy", "simajiri\\esp\\default.esp", "simajiri\\esp\\pe001.esp",
                            "simajiri\\esd\\pe820_004.esd", "simajiri\\esd\\pe820_005.esd", "yajima\\titlebg2.bin",
                            "iwase\\img\\citizen\\c01.xtx", "iwase\\img\\citizen\\ctz_alpha.xtx",
                            "iwase\\img\\item\\item00.xtx", "iwase\\img\\door\\door_alpha.xtx",
                            "iwase\\img\\door\\doorimg.dat", "matumoto\\Save_P.txy", "yajima\\diskchg.jpg")]
    rows = {r.source: r for r in _media_rows(toc)}
    party = next((r for s, r in rows.items() if "COMBATANT" in s), None)
    check(party is not None and party.count == "3 file(s)" and "yosikawa\\model\\rsv.txy" in party.where
          and "yosikawa\\atk.txy" in party.where, "the party-menu row claims exactly its three txy files")
    generic = next((r for s, r in rows.items() if s.startswith("signs and screens")), None)
    check(generic is not None and generic.count == "2 file(s)" and "skilball01" in generic.where
          and "E2_ELS04" in generic.where, "the generic *.txy row gets only what no specific row claimed")
    fx = [r for s, r in rows.items() if "Boost Bonus" in s or "glyph effect" in s]
    check(len(fx) == 2 and all(r.count == "1 file(s)" for r in fx) and "pe001" not in fx[0].where + fx[1].where
          and "pe820_005" not in fx[0].where + fx[1].where, "the effect rows name default.esp and pe820_004.esd only")
    quest = next((r for s, r in rows.items() if s.startswith("quest-board")), None)
    check(quest is not None and quest.count == "3 file(s)", "the quest-board row: the portraits, their mask and "
                                                           "the cards (door_alpha stays with the UI sheets)")
    check(sum(r.status == "no text" for r in rows.values()) == 3, "doorimg.dat, matumoto and diskchg.jpg are "
                                                                  "'no text' rows")
    # the texture facts are checked against the bytes of a texture, standalone and embedded
    import txy
    block, _ = txy._build_synthetic()
    junk = b"\x00" * 37 + b"ef_ring.txy\x00" + b"\x11" * 19
    objs = {"a.txy": block, "fx.esd": junk + block + b"\x22" * 10}
    read = objs.get
    good = [("a.txy", None, "face_00", ""), ("fx.esd", len(junk), "face_01", "")]
    check(tex_word_problems(read, good) == [], "texture facts: an entry of a standalone txy and of a block "
                                                "embedded in an effect file are found")
    bad = [("a.txy", None, "face_09", ""), ("fx.esd", len(junk) + 4, "face_00", ""), ("none.txy", None, "x", "")]
    check(len(tex_word_problems(read, bad)) == 3, "a missing entry, a wrong block offset and a missing file "
                                                   "are each reported")
    # the battle-database probe: passes on a well-formed file, fails where a string field points inside a string
    secs = [struct.pack("<4H", 1, 2, 3, 4)] * 19
    secs[6] = struct.pack("<6H", 24, 31, 0, 0, 0, 0) + struct.pack("<6H", 40, 31, 0, 0, 0, 0) + \
        b"Medica\x00One ally\x00Revert\x00\x00"
    offs = [84 + sum(len(x) for x in secs[:i]) for i in range(19)]
    db = struct.pack("<II", 19, 84 + sum(len(x) for x in secs)) + struct.pack("<19I", *offs) + b"".join(secs)
    check(batdat_probe(db) == [], "the battle probe passes on a well-formed database")
    broken = bytearray(db)
    struct.pack_into("<h", broken, offs[6] + 12 + 2, 33)          # a description that starts inside 'One ally'
    check(len(batdat_probe(bytes(broken))) == 1, "and reports a field that points inside the string it grows")
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
    ap.add_argument("--tex-words", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.iso:
        ap.print_help()
        return 2
    return cmd(a.iso, a.verify, a.tsv, a.tex_words)


if __name__ == "__main__":
    sys.exit(main())
