#!/usr/bin/env python3
"""Check that Episode II's TOC grammar (toc.py) also reads Episode III's catalogs.

Episode III's X3.00 / X3.10 / X3.20 are the same front-coded TOC (their filler
even says "Episode.2"). This compares every parsed entry against the text
tables Lybac's Xeno23Lbae produced in 2006 (the Xenosaga3PythonExtractor's
``lba/Lba*.txt``): names and sizes must agree, and the TOC must rebuild
byte-identically.

    python docs/research/xs3_toc_check.py XS3_DISC1.iso path/to/Xenosaga3PythonExtractor/lba
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from iso9660 import IsoImage            # noqa: E402
from toc import parse_events, parse_toc, serialize   # noqa: E402

PAIRS = (("X3.00", "Lba0.txt"), ("X3.10", "Lba1.txt"), ("X3.20", "Lba2.txt"))


def main() -> int:
    iso = IsoImage(sys.argv[1])
    lba_dir = sys.argv[2]
    rc = 0
    for toc_name, table in PAIRS:
        if toc_name not in iso.files or not os.path.exists(os.path.join(lba_dir, table)):
            continue
        data = bytes(iso.read_file(toc_name))
        ents = parse_toc(data, toc_name)
        events, _ = parse_events(data, toc_name)
        rebuilt = serialize(events, data[0]) == data
        want = {}
        for line in open(os.path.join(lba_dir, table), errors="ignore"):
            parts = line.strip().split("|")
            if len(parts) == 4:
                want[parts[3].lstrip("\\").lower()] = int(parts[1], 16)
        agree = sum(1 for e in ents if want.get(e.path.lower()) == e.size)
        missing = [e.path for e in ents if e.path.lower() not in want]
        print(f"{toc_name}: {len(ents)} entries, table {len(want)} rows, "
              f"name+size agree {agree}, not in table {len(missing)}, "
              f"rebuild byte-identical {rebuilt}")
        rc |= agree != len(ents) or len(want) != len(ents) or not rebuilt
    return 1 if rc else 0


if __name__ == "__main__":
    sys.exit(main())
