"""minidisc.py — a tiny Episode II disc image built in code, for self-tests.

No game data: ``build`` lays out an ISO9660 image with a SYSTEM.CNF, an empty
boot executable of the given name (so ``region.region_of`` sees a region) and
chain 0 (``XENOSAGA.00`` = the TOC, ``.01`` = the objects, ``.02`` = one spare
sector), packed to the sector the way the retail chains are. The kit's own
readers and writers (``iso9660``, ``chains``, ``toc``, ``repack``) then work
on it as on a real disc, so ``textpack`` and friends can test export, import,
relocation and refusal end to end without a disc.

    from minidisc import build
    build("t.iso", {"cf\\\\sc0001.sb": sb_bytes, "cf\\\\en\\\\sc0001.sbl": sbl_bytes})

    python minidisc.py --self-test
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toc                                   # noqa: E402
from iso9660 import SECTOR                   # noqa: E402


# the stand-in boot executable: an ELF header and nothing else. Its hash is registered
# so that the executable check passes on a mini disc (and on nothing real).
TEST_ELF = b"\x7fELF\x01\x01" + bytes(58)


def register_test_executable() -> None:
    import hashlib

    import region
    region.TEST_EXECUTABLES.add(hashlib.sha256(TEST_ELF).hexdigest())


def _record(name: bytes, lba: int, size: int, is_dir: bool = False) -> bytes:
    n = len(name)
    rec = bytearray(33 + n + (1 - n % 2))
    rec[0] = len(rec)
    struct.pack_into("<I", rec, 2, lba)
    struct.pack_into(">I", rec, 6, lba)
    struct.pack_into("<I", rec, 10, size)
    struct.pack_into(">I", rec, 14, size)
    rec[25] = 2 if is_dir else 0
    struct.pack_into("<H", rec, 28, 1)
    struct.pack_into(">H", rec, 30, 1)
    rec[32] = n
    rec[33:33 + n] = name
    return bytes(rec)


def toc_events(paths_sectors_sizes: List[Tuple[str, int, int]]) -> List[toc.Event]:
    """TOC items for (path, sector, size) rows, in the given order: directories are
    opened and closed as the paths require."""
    events: List[toc.Event] = []
    stack: List[str] = []
    for path, sector, size in paths_sectors_sizes:
        parts = path.split("\\")
        dirs, name = parts[:-1], parts[-1]
        common = 0
        while common < min(len(stack), len(dirs)) and stack[common].lower() == dirs[common].lower():
            common += 1
        pops = len(stack) - common
        new = dirs[common:]
        if new:
            events.append(toc.Event("dir", name=new[0].encode("ascii"), pops=pops))
            events += [toc.Event("dir", name=d.encode("ascii")) for d in new[1:]]
        else:
            events += [toc.Event("pop")] * pops
        stack = list(dirs)
        events.append(toc.Event("file", name=name.encode("ascii"), sector=sector, size=size))
    events += [toc.Event("pop")] * len(stack)
    return events


def build(path, files: Dict[str, bytes], boot: str = "SLUS_208.92",
          slack: Dict[str, int] = None) -> Path:
    """Write the image. ``files`` maps TOC path -> bytes, laid out in the given
    order; ``slack`` adds spare sectors after a path (a slot larger than the file)."""
    slack = slack or {}
    toc_sectors = 1
    rows, cur, blobs = [], toc_sectors, []
    for p, data in files.items():
        n = max(1, (len(data) + SECTOR - 1) // SECTOR) + slack.get(p, 0)
        rows.append((p, cur, len(data)))
        blobs.append(data + bytes(n * SECTOR - len(data)))
        cur += n
    body = b"".join(blobs)
    toc_bytes = toc.serialize(toc_events(rows), toc_sectors)
    cnf = f"BOOT2 = cdrom0:\\{boot};1\r\nVER = 1.00\r\nVMODE = NTSC\r\n".encode("ascii")
    elf = TEST_ELF
    root_lba, cnf_lba, elf_lba, toc_lba = 18, 19, 20, 21
    f01_lba = toc_lba + toc_sectors
    f02_lba = f01_lba + len(body) // SECTOR
    total = f02_lba + 1
    root = _record(b"\x00", root_lba, SECTOR, True) + _record(b"\x01", root_lba, SECTOR, True)
    for name, lba, size in (("SYSTEM.CNF;1", cnf_lba, len(cnf)), (boot + ";1", elf_lba, len(elf)),
                            ("XENOSAGA.00;1", toc_lba, len(toc_bytes)),
                            ("XENOSAGA.01;1", f01_lba, len(body)), ("XENOSAGA.02;1", f02_lba, SECTOR)):
        root += _record(name.encode("ascii"), lba, size)
    pvd = bytearray(SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    struct.pack_into("<I", pvd, 80, total)
    struct.pack_into(">I", pvd, 84, total)
    pvd[156:156 + 34] = _record(b"\x00", root_lba, SECTOR, True)
    img = bytearray(total * SECTOR)
    img[16 * SECTOR:17 * SECTOR] = pvd
    img[17 * SECTOR:17 * SECTOR + 7] = b"\xffCD001\x01"
    img[root_lba * SECTOR:root_lba * SECTOR + len(root)] = root
    img[cnf_lba * SECTOR:cnf_lba * SECTOR + len(cnf)] = cnf
    img[elf_lba * SECTOR:elf_lba * SECTOR + len(elf)] = elf
    img[toc_lba * SECTOR:toc_lba * SECTOR + len(toc_bytes)] = toc_bytes
    img[f01_lba * SECTOR:f01_lba * SECTOR + len(body)] = body
    out = Path(path)
    out.write_bytes(bytes(img))
    return out


def read(iso_path, path: str) -> bytes:
    """One chain-0 object of an image, as stored."""
    from repack import read_entry
    return read_entry(iso_path, 0, path)


def self_test() -> int:
    import tempfile

    import region
    from chains import CHAINS, ChainReader, available_chains
    from iso9660 import IsoImage
    from repack import patch_iso

    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    files = {"top.bin": b"T" * 10, "cf\\sc0001.sb": b"S" * 3000, "cf\\en\\sc0001.sbl": b"L" * 100,
             "model\\map\\X.bin": b"M" * 2048, "model\\map\\en\\X.sbl": b"N" * 5, "zz\\last.txt": b"end"}
    with tempfile.TemporaryDirectory() as td:
        p = build(Path(td) / "mini.iso", files, slack={"cf\\en\\sc0001.sbl": 1})
        iso = IsoImage(p)
        check(region.region_of(iso).name == "us" and available_chains(iso) == [0], "a USA image with chain 0")
        reader = ChainReader(iso, 0)
        entries = toc.parse_toc(bytes(reader.toc_bytes()), CHAINS[0][0])
        check([e.path for e in entries] == list(files), "the TOC lists every path in order")
        check(all(reader.read(e.byte_offset, e.size) == files[e.path] for e in entries), "every object reads back")
        check(entries[3].sector - entries[2].sector == 2, "slack leaves a spare sector")
        iso.close()
        size0 = p.stat().st_size
        patch_iso(p, {(0, "top.bin"): b"t" * 5000}, verbose=False, grow=True)       # outgrows its sector
        check(read(p, "top.bin") == b"t" * 5000 and p.stat().st_size == size0 + 3 * SECTOR,
              "repack relocates a grown object to the end of the image")
        iso = IsoImage(p)
        check(iso.volume_sectors * SECTOR == p.stat().st_size, "the volume size follows")
        iso.close()
        jp = build(Path(td) / "jp.iso", {"a.bin": b"1"}, boot="SLPS_253.68")
        iso = IsoImage(jp)
        check(region.region_of(iso).name == "jp", "the boot name sets the region")
        iso.close()

        # patch_copy: the source is never written, the output appears only when complete
        import contextlib
        import io
        import os

        from repack import RepackError, patch_copy
        td = Path(td)
        src = build(td / "src.iso", files)
        before = src.read_bytes()
        edit = {(0, "top.bin"): b"t" * 9}

        def refused(out, **kw):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    patch_copy(src, out, edit, **kw)
                return False
            except (RepackError, region.UnknownBuild, RuntimeError):
                return True

        os.symlink(src, td / "link.iso")
        os.link(src, td / "hard.iso")
        check(refused(src) and refused(td / "link.iso") and refused(td / "hard.iso") and refused(td / "." / "src.iso")
              and src.read_bytes() == before,
              "the input image is refused as output: by name, through a symlink, through a hard link")
        out = td / "out.iso"
        with contextlib.redirect_stdout(io.StringIO()):
            patch_copy(src, out, edit)
        check(read(out, "top.bin") == b"t" * 9 and src.read_bytes() == before and not (td / "out.iso.partial").exists(),
              "a patched copy is written; the source is untouched; no partial file is left")
        good = out.read_bytes()

        def boom(tmp):
            raise RuntimeError("write failed half way")

        check(refused(out, _fail_after_copy=boom) and out.read_bytes() == good
              and not (td / "out.iso.partial").exists() and src.read_bytes() == before,
              "a failure in the middle leaves an existing output as it was and removes the partial file")
        check(refused(td / "new.iso", _fail_after_copy=boom) and not (td / "new.iso").exists(),
              "... and creates no output when there was none")
        check(not refused(out) and out.read_bytes() == good, "a second run over an existing output replaces it")
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                patch_copy(src, td / "big.iso", {(0, "top.bin"): b"t" * 5000})      # outgrows its slot, no grow
            check(False, "a refused patch writes nothing")
        except RepackError:
            check(not (td / "big.iso").exists() and not (td / "big.iso.partial").exists(), "a refused patch writes nothing")
        unk = build(td / "unk.iso", {"a.bin": b"1"}, boot="SLES_999.99")
        iso = IsoImage(unk)
        check(region.region_of(iso).name == "unknown" and available_chains(iso) == [0], "an unknown serial still lists")
        iso.close()
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                patch_copy(unk, td / "unk_out.iso", {(0, "a.bin"): b"2"})
            check(False, "an unknown build is not patched")
        except region.UnknownBuild as e:
            check("SLES_999.99" in str(e) and not (td / "unk_out.iso").exists(), "an unknown build is not patched; the error names its serial")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print(__doc__)
