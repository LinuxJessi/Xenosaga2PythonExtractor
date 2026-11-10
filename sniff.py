"""sniff.py — name a disc object's format from its first bytes (and path).

One table, used by the dump manifest (``kind`` column), ``browse`` and the
verifier. Kinds are short stable words; FORMATS.md and
docs/research/small-files.md have a section per kind. Magic numbers come
first; a few one-off files with no magic are named by their path.

    python sniff.py --self-test
"""
from __future__ import annotations

import sys

MAGICS = (
    (b"\x7fELF", "elf"),          # boot executable, overlays, IOP modules
    (b"pxy\x00", "pxy"),          # mesh (float version 0.14/0.15)
    (b"txy\x00", "txy"),          # texture pages (GS uploads + named entries)
    (b"XHR ", "xhr"),             # joint hierarchy
    (b"XAP ", "xap"),             # motion
    (b"FAP ", "fap"),             # face motion: a table of FAC clips
    (b"XAC ", "xac"),             # motion curves (title scene pack members)
    (b"FAC ", "fac"),             # face motion clip
    (b"XCM ", "xcm"),             # camera motion (title scene pack members)
    (b"EPF ", "epf"),             # per-model point records (two u32 + six floats)
    (b"DEV ", "dev"),
    (b"SBL ", "sbl"),             # script text bank (dialogue)
    (b"SB  ", "sb"),              # compiled script
    (b"VSFp", "vsf"),             # KORG sample bank (SPU ADPCM)
    (b"PRJp", "prj"),             # KORG project: key map + programs
    (b"MThd", "midi"),
    (b"XTX\x00", "xtx"),
    (b"ARX\x00", "arx"),
    (b"ipum", "ipu"),
    (b"\x00\x00\x01\xba", "mpeg-ps"),   # Sofdec .sfd
    (b"\xff\xd8\xff", "jpeg"),
    (b"#scn", "scene-list"),
    (b"//  ", "jrd"),
    (b"PS2D", "icon-sys"),        # memory-card save description
)

# one-off files without a magic of their own (smallfiles.py decodes them)
PATHS = {
    "credit.bin": "pack",               # offset-table pack: 6 JPEGs
    "carddata.bin": "pack",             # icon.sys + the save's 3D icon
    "yajima\\im.bin": "pack",           # 4 JPEGs
    "yajima\\synopsis.bin": "pack",     # 2 XTX
    "yajima\\titlebg2.bin": "pack",     # the title scene: 190 models, motions, cameras
    "yajima\\title.bin": "rgba-image",  # title logo, 512x448 RGBA ("DISC 1")
    "yajima\\title2.bin": "rgba-image",  # the same for "DISC 2"
    "yamamoto\\batdat.us": "battle-db",  # names and descriptions of skills, items, enemies
    "yamamoto\\batdat.bin": "battle-db",  # the same, Japan (EUC-JP)
    "carddata.new": "pack",             # Japan: icon.sys + three .ico
}


def sniff(head: bytes, path: str = "") -> str:
    key = path.replace("/", "\\").lower()
    if key in PATHS:
        return PATHS[key]
    for magic, kind in MAGICS:
        if head.startswith(magic):
            return kind
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if head[:2] == b"\x80\x00" and ext == "adx":
        return "adx"
    if ext in ("us", "tex") and key.startswith("font"):     # font0.us (USA), font0.tex (Japan)
        return "font"
    if ext in ("txt",):
        return "text"
    if ext == "id" and head[:1] == b"\x03":
        return "map-id"                 # u8 version 3, u8 section count, u32 offsets
    if ext == "ene" and len(head) >= 4:
        return "ene"                    # 48-byte parameter records
    if head and not any(head):
        return "zero" if ext == "bin" else ext or "data"
    return ext or "data"


def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    check(sniff(b"SB  \x01\x00\x00\x00", "cf\\sc1010.sb") == "sb", "magic first")
    check(sniff(b"\xff\xff\xff\x00" * 4, "yajima\\title.bin") == "rgba-image", "a one-off file by its path")
    check(sniff(b"\x18\x00\x00\x00", "credit.bin") == "pack", "the packs")
    check(sniff(b"\x13\x00\x00\x00", "yamamoto\\batdat.us") == "battle-db", "the battle database")
    check(sniff(b"\x03\x06\x00\x00", "model\\map\\E2_KML01.id") == "map-id", "map .id by extension and version")
    check(sniff(b"XAC \x00\x00\x80\x3f", "x") == "xac" and sniff(b"PS2D", "x") == "icon-sys", "new magics")
    check(sniff(b"\x00" * 16, "16m.bin") == "zero", "zero padding files")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print(__doc__)
