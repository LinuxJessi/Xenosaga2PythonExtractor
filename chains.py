"""chains.py — Episode II's three bigfile chains.

Same shape as Episode I (this module is Xenosaga1PythonExtractor/chains.py
with the chain table changed): a TOC's sector numbers address one virtual
byte space formed by the TOC file itself followed by its sibling bigfiles.

    chain 0:  XENOSAGA.00 + .01 + .02            on both discs, byte-identical
              (system, models, effects, field sound, scripts, text)
    chain 1:  XENOSAGA.10 + .11 + .12 + .13 + .14   Disc 1 only
              (movies, cutscene voice, cutscene motion, monitor clips)
    chain 2:  XENOSAGA.20 + .21 + .22 + .23 + .24   Disc 2 only (same roles)

Each disc is a single-layer DVD-5; there is no second volume. The engine
reads by raw sector (LBA = TOC file LBA + entry sector), which is why the
bigfiles sit back to back on the image, and why ``read`` falls back to
absolute addressing for an object relocated past the chain's own extents.

Verified: every chain's entries tile it with no gap or overlap, and chains 1
and 2 end exactly at their last byte.

A chain may also sit in a directory: Xenosaga Freaks (SLPS_253.53) carries the
Episode II demo's chain 0 as ``EXP\\XENOSAGA.00``-``.02`` (2026-11-05, 3,612
entries, the same TOC grammar). ``chain_files`` finds the trio wherever all
three sit in one directory, the root first, so every reader and writer of the
kit works on that disc unchanged.
"""
from __future__ import annotations

from typing import Iterator, List, Optional, Tuple

from iso9660 import IsoImage, SECTOR

CHAINS: dict[int, List[str]] = {
    0: ["XENOSAGA.00", "XENOSAGA.01", "XENOSAGA.02"],
    1: ["XENOSAGA.10", "XENOSAGA.11", "XENOSAGA.12", "XENOSAGA.13", "XENOSAGA.14"],
    2: ["XENOSAGA.20", "XENOSAGA.21", "XENOSAGA.22", "XENOSAGA.23", "XENOSAGA.24"],
}

CHUNK = 8 * 1024 * 1024


def chain_files(iso: IsoImage, chain: int) -> Optional[List[str]]:
    """The ISO paths of CHAIN's bigfiles on this image: at the root, else in the one
    directory that holds all of them (``EXP\\`` on Xenosaga Freaks), else None."""
    names = CHAINS[chain]
    if all(n in iso.files for n in names):
        return list(names)
    dirs = sorted({p[:-len(names[0])] for p, f in iso.files.items()
                   if not f.is_dir and p.upper().endswith("\\" + names[0])})
    for d in dirs:
        if all(d + n in iso.files for n in names):
            return [d + n for n in names]
    return None


def available_chains(iso: IsoImage) -> List[int]:
    """Chains whose bigfiles all exist on this image: [0, 1] on Disc 1, [0, 2] on Disc 2,
    [0] on Xenosaga Freaks (the demo's chain, under EXP\\)."""
    return [c for c in CHAINS if chain_files(iso, c) is not None]


def disc_number(iso: IsoImage) -> int:
    """1 or 2, from which story chain the image carries (0 if neither)."""
    have = available_chains(iso)
    return 1 if 1 in have else 2 if 2 in have else 0


class ChainReader:
    def __init__(self, iso: IsoImage, chain: int):
        self.iso = iso
        self.chain = chain
        names = chain_files(iso, chain)
        if names is None:
            raise FileNotFoundError(f"{CHAINS[chain][0]} chain missing from ISO filesystem")
        self.names = names
        self.sizes = [iso.files[n].size for n in self.names]
        self.total = sum(self.sizes)
        self.toc_lba = iso.files[self.names[0]].lba

    def toc_bytes(self) -> bytes:
        return self.iso.read_file(self.names[0])

    def read_iter(self, offset: int, length: int) -> Iterator[bytes]:
        """Yield the byte range [offset, offset+length) of the chain in chunks,
        transparently spanning bigfile boundaries."""
        if offset + length > self.total:
            # relocated object: the engine addresses by absolute sector
            start = self.toc_lba * SECTOR + offset
            if start + length > self.iso.image_size:
                raise ValueError(f"read past image end: chain offset {offset}+{length}")
            yield self.iso.mm[start:start + length]
            return
        remaining = length
        for name, size in zip(self.names, self.sizes):
            if offset >= size:
                offset -= size
                continue
            take = min(remaining, size - offset)
            pos = 0
            while pos < take:
                n = min(CHUNK, take - pos)
                yield self.iso.read_file(name, offset + pos, n)
                pos += n
            remaining -= take
            offset = 0
            if remaining == 0:
                return

    def read(self, offset: int, length: int) -> bytes:
        return b"".join(self.read_iter(offset, length))

    def locate(self, offset: int) -> Tuple[str, int]:
        """Map a chain byte offset to (bigfile name, offset within it)."""
        for name, size in zip(self.names, self.sizes):
            if offset < size:
                return name, offset
            offset -= size
        raise ValueError(f"offset {offset} beyond chain end")
