#!/usr/bin/env python3
"""Episode II `.xhr` skeletons (hierarchy of transforms, bind pose).

    python xhr.py --self-test
    python xhr.py FILE.xhr [FILE.pxy]   # list the transforms; with the
                                        # mesh, check them against its
                                        # inverse bind matrices

Layout (little endian; proven by FUN_00290ab8 in SLUS_208.92, which walks
the six record runs in this order and sizes, and numerically against the
inverse bind matrices of the matching `.pxy`, see docs/MODELS.md):

    +0x00 "XHR "            +0x04 f32 version (2.3 / 2.4)
    +0x08 u32 file size     +0x0C u32 number of transforms (sum of the runs)
    +0x10 u32 nA  +0x14 u32 nB  +0x18 u32 nC  +0x1C u32 nD
    +0x20 u32 nE  +0x24 u32 nF
    +0x28 u32 offset of run B   +0x2C run C   +0x30 run D
    +0x34 u32 offset of run E   +0x38 run F
    +0x3C.. further counts/offsets of tables this module does not read

    run A  starts at 0x70: two 0x40-byte records {i32 parent, f32 T[3],
           R[3], S[3], 0x18 bytes not read}, then nA-2 records of 0x50
    run A (rest), B, C: 0x50 bytes {i32 parent, f32 T[3], JO[3], R[3],
           S[3], 0x1C bytes not read}
    run D: 0x70 bytes {i32 parent, u32, u32, f32 T[3], JO[3], R[3], S[3],
           then limits etc., not read}
    run E: 0xB0 + 0x10*(i32[+0x1C] + i32[+0x20] + i32[+0x24]) bytes
           {i32 parent, ..., +0x30 f32 T[4], +0x40 JO[4], +0x50 R[4],
           +0x60 S[4], ...}  (the chains the engine simulates: hair,
           skirts)
    run F: 0x40 bytes {i32 parent, f32 T[3], R[3], S[3], ...}

The transform index is the position in A, B, C, D, E, F order.  The bytes
"not read" hold uninitialised memory of the exporter (fragments of Maya UI
strings such as `|Jseb|Jkata_L|`); the file has no joint names.

Local matrix (row vectors, v' = v * M, as the game stores matrices):

    M = scale(S) * Rx(R.x) * Ry(R.y) * Rz(R.z)
                 * Rx(JO.x) * Ry(JO.y) * Rz(JO.z),  then row 3 = T

and world = local * world[parent].  Joint matrix j of the `.pxy` binds to
transform j + 2 (FUN_00289f48: the skinning matrix is
world[j + 2] o invBind[j]).
"""
import struct
import sys

import numpy as np

__all__ = ["XhrError", "Joint", "Xhr", "parse", "bind_error", "self_test"]


class XhrError(ValueError):
    pass


class Joint(object):
    __slots__ = ("index", "kind", "offset", "parent", "t", "jo", "r", "s")

    def __init__(self, index, kind, offset, parent, t, jo, r, s):
        self.index, self.kind, self.offset, self.parent = (index, kind,
                                                           offset, parent)
        self.t, self.jo, self.r, self.s = t, jo, r, s

    @property
    def name(self):
        return "t%02d" % self.index

    def __repr__(self):
        return "<%s %s parent=%d T=%s>" % (self.name, self.kind, self.parent,
                                           np.round(self.t, 4).tolist())


def _rx(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, s, 0], [0, -s, c, 0], [0, 0, 0, 1.0]])


def _ry(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, -s, 0], [0, 1, 0, 0], [s, 0, c, 0], [0, 0, 0, 1.0]])


def _rz(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, s, 0, 0], [-s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])


def euler_rows(v):
    """Row-vector rotation Rx * Ry * Rz (X is applied first)."""
    return _rx(v[0]) @ _ry(v[1]) @ _rz(v[2])


class Xhr(object):
    def __init__(self, version, counts, joints, size):
        self.version = version
        self.counts = counts          # dict A..F
        self.joints = joints
        self.size = size

    def local_matrices(self):
        """Row-vector 4x4 local matrices, (n,4,4) float64."""
        out = np.zeros((len(self.joints), 4, 4))
        for j in self.joints:
            m = np.diag(list(j.s) + [1.0]) @ euler_rows(j.r) @ euler_rows(j.jo)
            m[3, :3] = j.t
            out[j.index] = m
        return out

    def world_matrices(self):
        """Row-vector 4x4 world (bind) matrices, (n,4,4) float64."""
        loc = self.local_matrices()
        out = np.zeros_like(loc)
        done = [False] * len(self.joints)
        for j in self.joints:
            # parents always precede their children except across runs
            # (run E hangs off run B); resolve recursively
            self._world(j.index, loc, out, done, 0)
        return out

    def _world(self, i, loc, out, done, depth):
        if done[i]:
            return
        if depth > len(self.joints):
            raise XhrError("parent cycle at transform %d" % i)
        p = self.joints[i].parent
        if p < 0:
            out[i] = loc[i]
        else:
            self._world(p, loc, out, done, depth + 1)
            out[i] = loc[i] @ out[p]
        done[i] = True


def parse(buf):
    if buf[:4] != b"XHR ":
        raise XhrError("not an xhr (magic)")
    version, = struct.unpack_from("<f", buf, 4)
    size, ntrans, na, nb, nc, nd, ne, nf, ob, oc, od, oe, of = \
        struct.unpack_from("<13I", buf, 8)
    if size != len(buf):
        raise XhrError("size field %#x != file length %#x" % (size, len(buf)))
    if na + nb + nc + nd + ne + nf != ntrans:
        raise XhrError("run counts do not add up to the transform count")
    if na < 2:
        raise XhrError("run A shorter than its two fixed records")
    joints = []

    def f3(o):
        return np.array(struct.unpack_from("<3f", buf, o), dtype=np.float64)

    def add(kind, o, parent, t, jo, r, s):
        if not -1 <= parent < ntrans:
            raise XhrError("transform %d: parent %d out of range"
                           % (len(joints), parent))
        joints.append(Joint(len(joints), kind, o, parent, t, jo, r, s))

    zero = np.zeros(3)
    o = 0x70
    for _ in range(2):
        p, = struct.unpack_from("<i", buf, o)
        add("A0", o, p, f3(o + 4), zero, f3(o + 0x10), f3(o + 0x1C))
        o += 0x40
    for _ in range(na - 2):
        p, = struct.unpack_from("<i", buf, o)
        add("A", o, p, f3(o + 4), f3(o + 0x10), f3(o + 0x1C), f3(o + 0x28))
        o += 0x50
    if o != ob:
        raise XhrError("run A ends at %#x, run B starts at %#x" % (o, ob))
    for kind, n, start in (("B", nb, ob), ("C", nc, oc)):
        for k in range(n):
            o = start + k * 0x50
            p, = struct.unpack_from("<i", buf, o)
            add(kind, o, p, f3(o + 4), f3(o + 0x10), f3(o + 0x1C),
                f3(o + 0x28))
    if ob + nb * 0x50 != oc or oc + nc * 0x50 != od:
        raise XhrError("runs B/C/D do not tile")
    for k in range(nd):
        o = od + k * 0x70
        p, = struct.unpack_from("<i", buf, o)
        add("D", o, p, f3(o + 0xC), f3(o + 0x18), f3(o + 0x24), f3(o + 0x30))
    if od + nd * 0x70 != oe:
        raise XhrError("run D does not end at run E")
    o = oe
    for k in range(ne):
        if o + 0xB0 > len(buf):
            raise XhrError("run E runs past the file")
        p, = struct.unpack_from("<i", buf, o)
        c = struct.unpack_from("<3i", buf, o + 0x1C)
        if min(c) < 0 or sum(c) > 4096:
            raise XhrError("run E record %d: implausible counts %r" % (k, c))
        add("E", o, p, f3(o + 0x30), f3(o + 0x40), f3(o + 0x50), f3(o + 0x60))
        o += 0xB0 + 0x10 * sum(c)
    e_end = o
    if e_end > of and nf:
        raise XhrError("run E overlaps run F")
    for k in range(nf):
        o = of + k * 0x40
        p, = struct.unpack_from("<i", buf, o)
        add("F", o, p, f3(o + 4), zero, f3(o + 0x10), f3(o + 0x1C))
    if nf and of + nf * 0x40 > len(buf):
        raise XhrError("run F runs past the file")
    x = Xhr(version, dict(A=na, B=nb, C=nc, D=nd, E=ne, F=nf), joints, size)
    x.e_end = e_end
    return x


def bind_error(x, inv_bind):
    """Largest |invBind[j] * world[j+2] - I| over the mesh's joint
    matrices (row-vector matrices, as stored in the .pxy).  This is the
    engine's skinning matrix in the bind pose, so it must be the identity.
    """
    inv_bind = np.asarray(inv_bind, dtype=np.float64).reshape(-1, 4, 4)
    if len(inv_bind) + 2 > len(x.joints):
        raise XhrError("mesh has %d joint matrices, skeleton only %d "
                       "transforms" % (len(inv_bind), len(x.joints)))
    w = x.world_matrices()
    if not len(inv_bind):
        return 0.0
    prod = np.einsum("nij,njk->nik", inv_bind, w[2:2 + len(inv_bind)])
    return float(np.abs(prod - np.eye(4)).max())


# --------------------------------------------------------------- self-test

def _synthetic():
    """A 9-transform file with every run, built from known matrices."""
    rng = np.random.RandomState(7)

    def trs():
        return (rng.uniform(-1, 1, 3), rng.uniform(-3, 3, 3),
                rng.uniform(-3, 3, 3), np.array([1.0, 1.0, 1.0]))
    spec = [("A0", -1), ("A0", 0), ("A", 1), ("A", 2), ("B", 3), ("C", 2),
            ("D", 4), ("E", 4), ("E", 7), ("F", 3)]
    vals = [trs() for _ in spec]
    body = {k: bytearray() for k in "ABCDEF"}
    junk = b"|Jseb|Jkata_L|\0\0" * 4
    for (kind, par), (t, jo, r, s) in zip(spec, vals):
        f = lambda v: struct.pack("<3f", *v)
        if kind in ("A0", "F"):
            rec = struct.pack("<i", par) + f(t) + f(r) + f(s) + junk[:0x18]
            body["A" if kind == "A0" else "F"] += rec
        elif kind in "ABC":
            body[kind] += (struct.pack("<i", par) + f(t) + f(jo) + f(r) + f(s)
                           + junk[:0x1C])
        elif kind == "D":
            body["D"] += (struct.pack("<iII", par, 0, 92) + f(t) + f(jo)
                          + f(r) + f(s) + junk[:0x34])
        else:
            rec = bytearray(0xB0 + 0x20)
            struct.pack_into("<i", rec, 0, par)
            struct.pack_into("<3i", rec, 0x1C, 0, 1, 1)
            for off, v in ((0x30, t), (0x40, jo), (0x50, r), (0x60, s)):
                struct.pack_into("<4f", rec, off, v[0], v[1], v[2], 1.0)
            body["E"] += rec
    head = bytearray(0x70)
    offs = {}
    pos = 0x70
    for k in "ABCDEF":
        offs[k] = pos
        pos += len(body[k])
    counts = [sum(1 for kd, _ in spec if kd.rstrip("0") == k) for k in "ABCDEF"]
    struct.pack_into("<4sf", head, 0, b"XHR ", 2.4)
    struct.pack_into("<13I", head, 8, pos, len(spec), *(counts + [
        offs["B"], offs["C"], offs["D"], offs["E"], offs["F"]]))
    data = bytes(head) + b"".join(bytes(body[k]) for k in "ABCDEF")
    return data, spec, vals


def self_test():
    data, spec, vals = _synthetic()
    x = parse(data)
    assert len(x.joints) == len(spec) and x.counts["E"] == 2
    assert [j.kind for j in x.joints] == [k for k, _ in spec]
    assert [j.parent for j in x.joints] == [p for _, p in spec]
    # independent reference: column-vector composition, built differently
    def col(t, jo, r, kind):
        def rot(v):
            cx, sx = np.cos(v[0]), np.sin(v[0])
            cy, sy = np.cos(v[1]), np.sin(v[1])
            cz, sz = np.cos(v[2]), np.sin(v[2])
            mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
            my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
            mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
            return mz @ my @ mx
        m = np.eye(4)
        m[:3, :3] = (rot(jo) if kind not in ("A0", "F") else np.eye(3)) \
            @ rot(r)
        m[:3, 3] = t
        return m
    ref = []
    for i, ((kind, par), (t, jo, r, s)) in enumerate(zip(spec, vals)):
        m = col(t, jo, r, kind)
        ref.append(m if par < 0 else ref[par] @ m)
    w = x.world_matrices()
    for i in range(len(spec)):
        assert np.abs(w[i].T - ref[i]).max() < 1e-5, (i, spec[i])
    inv = np.stack([np.linalg.inv(w[i]) for i in range(2, 9)])
    assert bind_error(x, inv) < 1e-9
    inv[3, 3, 0] += 0.25
    assert bind_error(x, inv) > 0.1
    for bad in (b"XHS " + data[4:], data[:-4],
                data[:0x10] + struct.pack("<I", 9) + data[0x14:]):
        try:
            parse(bad)
        except XhrError:
            pass
        else:
            raise AssertionError("parse accepted a damaged file")
    print("xhr self-test ok")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] == "--self-test":
        return self_test()
    with open(argv[0], "rb") as f:
        x = parse(f.read())
    print("xhr %.1f: %d transforms %r" % (x.version, len(x.joints), x.counts))
    w = x.world_matrices()
    for j in x.joints:
        print("  %-4s %-2s parent %3d  T %s  world %s" % (
            j.name, j.kind, j.parent, np.round(j.t, 4).tolist(),
            np.round(w[j.index][3, :3], 4).tolist()))
    if len(argv) > 1:
        import pxy
        with open(argv[1], "rb") as f:
            m = pxy.parse(f.read())
        print("bind check against %d joint matrices: max |invBind*world - I|"
              " = %.3g" % (len(m.inv_bind), bind_error(x, m.inv_bind)))


if __name__ == "__main__":
    sys.exit(main())
