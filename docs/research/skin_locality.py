"""Non-circular skinning checks: a vertex's dominant joint should be near it, and the two
ends of a mesh edge should hang off the same joint or a parent/child pair. Control: the same
measures with the joint assignment shuffled."""
import sys, glob, os, collections
import numpy as np
kit, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, kit)
import pxy, xhr
rng = np.random.default_rng(1)
tot = collections.Counter(); modes = collections.Counter(); col_hi = col_n = 0; stray = 0
ranks = []; ranks_c = []
for f in sorted(glob.glob(os.path.join(root, '**', '*.pxy'), recursive=True)):
    try:
        p = pxy.parse(open(f, 'rb').read())
    except Exception as e:
        tot['parse-fail'] += 1; continue
    xf = f[:-4] + '.xhr'
    x = None
    if os.path.isfile(xf):
        try: x = xhr.parse(open(xf, 'rb').read())
        except Exception: x = None
    n_j = p.n_joint_matrices
    jpos = parents = None
    if x is not None and n_j and len(x.joints) >= n_j + 2:
        w = x.world_matrices()
        jpos = np.array([w[j + 2][3, :3] for j in range(n_j)])
        parents = [x.joints[j + 2].parent - 2 for j in range(n_j)]
    for d in p.draws:
        for b in d.batches:
            modes[b.skin_mode] += 1
            if b.colors is not None:
                c = np.asarray(b.colors); col_n += c.size; col_hi += int((c[..., :3] > 128).sum())
            if not b.skin_mode: continue
            s = b.slots
            if b.skin_mode != 2:
                used = s != 255
                ended = np.logical_or.accumulate(~used, axis=1)
                stray += int((used & ended).sum())
            if jpos is None: continue
            jm, wts = pxy.batch_joints(p, d.node, b)
            dom = jm[np.arange(b.n), wts.argmax(axis=1)]
            ok = (wts.sum(axis=1) > 0) & (dom >= 0)
            if not ok.any(): continue
            dist = np.linalg.norm(b.pos[ok][:, None, :] - jpos[None], axis=2)
            dj = dom[ok]
            # distance to the joint or to any of its children (a bone runs to its child)
            kids = collections.defaultdict(list)
            for j, pa in enumerate(parents):
                if 0 <= pa < n_j: kids[pa].append(j)
            def rank(djv):
                r = []
                for i, j in enumerate(djv):
                    dmin = min(dist[i, [j] + kids.get(j, [])])
                    r.append(int((dist[i] < dmin - 1e-9).sum()))
                return r
            ranks += rank(dj); ranks_c += rank(rng.integers(0, n_j, len(dj)))
            tri = b.triangles()
            if len(tri):
                okv = ok
                for a, c2 in ((0, 1), (1, 2), (0, 2)):
                    ja, jb = dom[tri[:, a]], dom[tri[:, c2]]
                    good = okv[tri[:, a]] & okv[tri[:, c2]]
                    for u, v in zip(ja[good], jb[good]):
                        tot['edges'] += 1
                        if u == v: tot['edge-same'] += 1
                        elif parents[u] == v or parents[v] == u: tot['edge-parent-child'] += 1
                        elif parents[u] == parents[v]: tot['edge-siblings'] += 1
                    # control: shuffled joints
                    ua = rng.integers(0, n_j, int(good.sum())); ub = rng.integers(0, n_j, int(good.sum()))
                    tot['c-edges'] += len(ua)
                    tot['c-related'] += int(sum(1 for u, v in zip(ua, ub) if u == v or parents[u] == v or parents[v] == u or parents[u] == parents[v]))
r = np.array(ranks); rc = np.array(ranks_c)
print('files parse-fail', tot['parse-fail'], 'skin modes by batch', dict(modes))
print('colour bytes > 128: %d of %d (%.2f%%)' % (col_hi, col_n, 100.0 * col_hi / max(col_n, 1)))
print('non-255 palette bytes after a 255 (weighted modes):', stray)
print('skinned vertices tested', len(r))
print('rank of the dominant joint (or its child) by distance: median %d, <=1: %.1f%%, <=3: %.1f%%, <=8: %.1f%%' % (np.median(r), 100*(r<=1).mean(), 100*(r<=3).mean(), 100*(r<=8).mean()))
print('  control (shuffled joint):                          median %d, <=1: %.1f%%, <=3: %.1f%%, <=8: %.1f%%' % (np.median(rc), 100*(rc<=1).mean(), 100*(rc<=3).mean(), 100*(rc<=8).mean()))
e = tot['edges']
print('mesh edges %d: same joint %.1f%%, parent/child %.1f%%, siblings %.1f%%, unrelated %.1f%%' % (e, 100*tot['edge-same']/e, 100*tot['edge-parent-child']/e, 100*tot['edge-siblings']/e, 100*(e-tot['edge-same']-tot['edge-parent-child']-tot['edge-siblings'])/e))
print('  control: related %.1f%%' % (100*tot['c-related']/max(tot['c-edges'],1)))
