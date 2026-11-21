"""browse.py — turn a dump (``cli.py dump``) into files a desktop can open.

Same shape as the Episode I kit's ``browse.py``: one decoder per format,
each writing under ``OUT/browse/<kind>/`` with a CSV beside it. Kinds:

    textures  .txy texture pages      -> PNG per named entry (txy.py), per bank, in
                                         textures_png/; txy blocks inside effect files
                                         in textures_png/_embedded/ (embedded.py)
    xtx       .xtx UI/sprite sheets   -> PNG (xtx.py; true-colour sheets drawn as such)
    fonts     font0.us / font1.us     -> the four glyph sheets as PNG + metrics.csv
    text      .sbl .txd .txt          -> UTF-8 (dialogue banks in editable form)
    scripts   .sb (+ its .sbl)        -> disassembly with natives and dialogue inline
    audio     .vsf sample banks       -> one WAV per named sample (+ loop points)
    music     .mid + .prj/.vsf        -> the MIDI, a SoundFont 2 per song, and a WAV
                                         rendered the way the PS2 plays it (spu2synth.py)
    images    .jpg (and the two .txy files that are really JPEGs)
    movies    .sfd .ipu .adx          -> MP4 / WAV through ffmpeg when installed

Everything is standard library except numpy (the music renderer;
requirements.txt); ffmpeg is used only when found. ``--limit N`` converts at most N objects per kind (for a quick look).
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ALL_KINDS = ("textures", "models", "xtx", "fonts", "text", "scripts", "audio", "music", "images", "movies", "small")


def _rows(out: Path) -> List[dict]:
    with open(out / "manifest.csv", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["area"] != "code"]


def _src(out: Path, row: dict) -> Path:
    return out / "dump" / row["area"] / Path(*row["path"].split("\\"))


def _dest(out: Path, kind: str, row: dict, suffix: str = "") -> Path:
    p = out / "browse" / kind / row["area"] / Path(*row["path"].split("\\"))
    return p.with_name(p.name + suffix) if suffix else p


def _write_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _which(name: str) -> Optional[str]:
    """ffmpeg: a copy bundled with the app first, then PATH."""
    try:
        from toolfind import find_tool
    except ImportError:
        return shutil.which(name)
    return find_tool(name)


# --------------------------------------------------------------- textures

def _one_txy(args) -> List[dict]:
    src, dest, path = args
    import txy

    data = Path(src).read_bytes()
    try:
        t = txy.parse(data)
    except txy.TxyError as e:
        return [{"file": path, "entry": "", "status": f"error: {e}"}]
    if not t["banks"]:
        return [{"file": path, "entry": "", "status": "placeholder (0 banks)"}]
    import hashlib

    multi = len(t["banks"]) > 1
    out = []
    Path(dest).mkdir(parents=True, exist_ok=True)
    for e, w, h, rgba in txy.decode(data):
        name = txy.entry_filename(e, multi)
        png = txy.png_bytes(w, h, rgba)
        (Path(dest) / name).write_bytes(png)
        out.append({"file": path, "entry": e["name"], "bank": e["bank"], "fmt": e["fmt"],
                    "w": w, "h": h, "png": name, "png_sha1": hashlib.sha1(png).hexdigest(),
                    "status": "ok"})
    return out


def browse_textures(out: Path, rows, limit) -> None:
    """Standalone .txy pages to browse/textures_png/<chain>/<path>/, then the
    txy blocks embedded in effect files to browse/textures_png/_embedded/
    (embedded.py), skipping any image the standalone pass already wrote."""
    import embedded

    jobs, jpegs = [], []
    for r in rows:
        if r["path"].lower().endswith(".txy"):
            if r["kind"] == "jpeg":
                jpegs.append(r)
                continue
            jobs.append((str(_src(out, r)), str(_dest(out, "textures_png", r)), r["path"]))
    jobs = jobs[:limit] if limit else jobs
    results: List[dict] = []
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        for rs in ex.map(_one_txy, jobs, chunksize=4):
            results += rs
    for r in jpegs:
        d = _dest(out, "textures_png", r).with_suffix(".jpg")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_src(out, r), d)
        results.append({"file": r["path"], "entry": "", "status": "a JPEG with a .txy name, copied"})
    _write_csv(out / "browse" / "textures.csv", results)
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"textures: {len(jobs)} .txy files -> {ok} PNGs", file=sys.stderr)
    seen = {r["png_sha1"] for r in results if r.get("png_sha1")}
    emb = embedded.sweep(out, rows, seen, limit)
    blocks = {(r["container"], r["offset"]) for r in emb}
    written = sum(1 for r in emb if r["written"])
    print(f"textures: {len(blocks)} embedded txy blocks in "
          f"{len({c for c, _ in blocks})} files -> {len(emb)} entries, {written} new PNGs "
          f"({len(emb) - written} duplicates) in textures_png/_embedded", file=sys.stderr)


# --------------------------------------------------------------------- xtx

def _one_xtx(args) -> dict:
    src, dest, path, mask_src = args
    import xtx

    data = Path(src).read_bytes()
    r = xtx.decode(data)
    if r is None:
        return {"file": path, "status": "undecodable"}
    masked = ""
    if mask_src and r[3] == "corner":
        # the quest board draws each portrait / item card through the frame
        # mask beside it (iwase\img\citizen\ctz_alpha.xtx, item\item_alpha.xtx):
        # rounded corners, the dark margin right of the art cut away
        m = xtx.decode(Path(mask_src).read_bytes())
        if m is not None and m[3] == "mask":
            cut = xtx.apply_mask(r, m)
            if cut is not None:
                r, masked = cut, Path(mask_src).name
    w, h, rgba, source = r
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    xtx.write_png(Path(dest), w, h, rgba)
    return {"file": path, "w": w, "h": h, "palette": source, "mask": masked, "status": "ok"}


def browse_xtx(out: Path, rows, limit) -> None:
    sheets = [r for r in rows if r["kind"] in ("xtx", "arx") and r["path"].lower().endswith(".xtx")]
    masks = {}                       # directory -> its *_alpha.xtx frame mask
    for r in sheets:
        d, _, name = r["path"].rpartition("\\")
        if name.lower().endswith("_alpha.xtx"):
            masks[(r["area"], d.lower())] = str(_src(out, r))
    jobs = [(str(_src(out, r)), str(_dest(out, "xtx", r, ".png")), r["path"],
             "" if r["path"].lower().endswith("_alpha.xtx")
             else masks.get((r["area"], r["path"].rpartition("\\")[0].lower()), ""))
            for r in sheets]
    jobs = jobs[:limit] if limit else jobs
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        results = list(ex.map(_one_xtx, jobs))
    _write_csv(out / "browse" / "xtx.csv", results)
    print(f"xtx: {sum(r['status'] == 'ok' for r in results)}/{len(jobs)} sheets", file=sys.stderr)


# ------------------------------------------------------------------ models

def _one_model(args) -> dict:
    """One .pxy mesh, with the texture pages (.txy) and skeleton (.xhr) of the same name
    beside it, to a .glb Blender opens: mesh, UVs, textures, vertex colours, skin weights
    and an armature. A file the decoder does not understand is reported, never guessed at;
    one that decodes but fails a numeric check is still written and says which check."""
    src, dest, path, motion_files = args
    import pxy
    import txy
    import xhr
    from vif import VifError

    src = Path(src)
    row = {"file": path, "skeleton": "", "textures": "", "triangles": "", "checks": "", "motion": "",
           "motion_counts_differ": "", "status": ""}
    try:
        p = pxy.parse(src.read_bytes())
        txy_data = entries = skel = None
        tsrc, xsrc = src.with_suffix(".txy"), src.with_suffix(".xhr")
        if tsrc.is_file():
            try:
                txy_data = tsrc.read_bytes()
                entries = txy.parse(txy_data)["entries"]
                row["textures"] = tsrc.name
            except Exception as e:                   # a JPEG or placeholder named .txy: export untextured
                txy_data = entries = None
                row["textures"] = f"unreadable ({type(e).__name__})"
        if xsrc.is_file():
            try:
                skel = xhr.parse(xsrc.read_bytes())
                row["skeleton"] = xsrc.name
            except xhr.XhrError as e:
                row["skeleton"] = f"unreadable ({e})"
        r = pxy.check(p, entries)
        row["triangles"] = r.get("triangles", "")
        row["checks"] = "all pass" if not r["failed"] else "; ".join(r["failed"])
        try:
            blob = pxy.to_glb(p, txy_data, skel)
        except pxy.PxyError as e:
            if skel is None:
                raise
            # a skeleton file with fewer transforms than the mesh has joints: the mesh
            # is still right, so it is written with its joints left where the file binds them
            row["skeleton"] = f"not used ({e})"
            skel = None
            blob = pxy.to_glb(p, txy_data, None)
        if skel is not None and motion_files:
            blob, row["motion"], row["motion_counts_differ"] = _add_motion(blob, skel, motion_files)
    except Exception as e:                           # a damaged file must not stop the others
        known = isinstance(e, (pxy.PxyError, VifError, xhr.XhrError))
        row["status"] = f"not decoded: {e}" if known else f"not decoded: {type(e).__name__}: {e}"
        try:
            Path(dest).unlink()                      # no stale .glb from an earlier run beside a failure
        except OSError:
            pass
        return row
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dest) + ".partial")              # a .glb under its final name is always complete
    part.write_bytes(blob)
    os.replace(part, dest)
    row["status"] = "ok" if row["checks"] == "all pass" else "written, a check failed"
    return row


def _add_motion(blob, skel, motion_files):
    """The same-stem .xap clips as glTF animations (xap.py): (blob, models.csv "motion"
    text, number of clips whose run counts differ). A clip whose block count in a run it
    drives is not the skeleton's is not known to be made for it (docs/MOTION.md): it is
    written with the counts in its name and listed here (clip/xhr). A file that does not
    decode leaves the mesh as it was and says why."""
    import xap

    try:
        sources = []
        for label, mpath in motion_files:
            sources.append((label, xap.parse(Path(mpath).read_bytes()), None, False))
        blob2, summ, _ = xap.add_to_glb(blob, skel, sources)
    except (xap.XapError, ValueError, KeyError, OSError) as e:
        return blob, f"not decoded: {e}", ""
    parts, differ = [], 0
    for label, _ in motion_files:
        mine = [s for s in summ if s["name"].startswith(label + "#")]
        text = f"{label}.xap: {len(mine)} animations"
        odd = [s for s in mine if s["counts_differ"]]
        differ += len(odd)
        if odd:
            text += (f", {len(odd)} whose run counts differ from the skeleton's, not known to "
                     f"fit it (" + ", ".join(
                         f"#{s['name'].split('#', 1)[1].split(' ', 1)[0]} "
                         f"{xap.mismatch_text(s['counts_differ'])}" for s in odd) + ")")
        parts.append(text)
    return blob2, "; ".join(parts), differ or ""


def browse_models(out: Path, rows, limit) -> None:
    # motion pairing: '<dir>\\<stem>.xap' anywhere on the disc whose stem is the model's
    # (motion\\ and yamamoto\\mot\\); other .xap files are bound by tables not traced
    xap_by_stem: Dict[str, Dict[str, dict]] = {}
    for r in rows:
        if r["path"].lower().endswith(".xap"):
            stem = r["path"].lower().rsplit("\\", 1)[-1][:-4]
            xap_by_stem.setdefault(stem, {}).setdefault(r["path"].lower(), r)
    model_stems = {r["path"].lower().rsplit("\\", 1)[-1][:-4] for r in rows
                   if r["path"].lower().endswith(".xhr")}

    def motion_for(r):
        stem = r["path"].lower().rsplit("\\", 1)[-1][:-4]
        return [(m["path"].replace("\\", "/")[:-4], str(_src(out, m)))
                for _, m in sorted(xap_by_stem.get(stem, {}).items())]

    jobs = [(str(_src(out, r)), str(_dest(out, "models", r).with_suffix(".glb")), r["path"], motion_for(r))
            for r in rows if r["kind"] == "pxy" or r["path"].lower().endswith(".pxy")]
    jobs = jobs[:limit] if limit else jobs
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        results = list(ex.map(_one_model, jobs, chunksize=4))
    _write_csv(out / "browse" / "models.csv", results)
    ok = sum(r["status"] == "ok" for r in results)
    flagged = sum(r["status"].startswith("written") for r in results)
    animated = sum("animations" in r.get("motion", "") for r in results)
    differ = sum(r.get("motion_counts_differ") or 0 for r in results)
    differ_models = sum(1 for r in results if r.get("motion_counts_differ"))
    unpaired = sum(1 for st in xap_by_stem if st not in model_stems)
    print(f"models: {ok + flagged}/{len(jobs)} .glb written ({flagged} with a failed check, "
          f"{len(jobs) - ok - flagged} not decoded; {animated} with motion, {differ} clips in "
          f"{differ_models} models with run counts that differ from the skeleton's, flagged; {unpaired} unpaired motion "
          f"files (no same-named model, see docs/MOTION.md); see browse/models.csv)", file=sys.stderr)


# ------------------------------------------------------------------- fonts

def browse_fonts(out: Path, rows, limit) -> None:
    import font

    for r in rows:
        if r["kind"] != "font":
            continue
        f = font.Font.from_bytes(_src(out, r).read_bytes())
        d = out / "browse" / "fonts"
        d.mkdir(parents=True, exist_ok=True)
        stem = r["path"].replace(".", "_")
        for i, sheet in enumerate(f.sheets):
            (d / f"{stem}.sheet{i}.png").write_bytes(font.sheet_to_png(sheet))
        _write_csv(d / f"{stem}.metrics.csv",
                   [{"slot": i, "left": a, "right": b} for i, (a, b) in enumerate(f.metrics)])
    print("fonts: written", file=sys.stderr)


# -------------------------------------------------------------------- text

def decode_text_file(b: bytes) -> str:
    """EUC-JP or Shift-JIS, whichever decodes cleanly with fewer half-width
    kana (the mojibake tell); Latin-1 as the last resort."""
    best = None
    import sbl
    for enc in (sbl.CODEC, "shift_jis"):
        try:
            t = b.decode(enc)
        except UnicodeDecodeError:
            continue
        score = sum(1 for c in t if "｡" <= c <= "ﾟ")
        if best is None or score < best[0]:
            best = (score, t)
    return best[1] if best else b.decode("latin1")


def txd_strings(data: bytes) -> List[bytes]:
    import struct

    n = struct.unpack_from("<I", data, 0)[0] // 4
    offs = struct.unpack_from(f"<{n}I", data, 0)
    return [data[o:data.index(b"\x00", o)] for o in offs]


def browse_text(out: Path, rows, limit) -> None:
    import sbl

    n = 0
    for r in rows:
        p = r["path"].lower()
        src = _src(out, r)
        try:
            if r["kind"] == "sbl":
                body = "\n".join(f"### {i:04d}\n{sbl.to_text(s)}" for i, s in enumerate(sbl.parse(src.read_bytes())))
            elif p.endswith(".txd"):
                body = "\n".join(f"### {i:04d}\n{sbl.to_text(s)}" for i, s in enumerate(txd_strings(src.read_bytes())))
            elif p.endswith(".txt"):
                body = decode_text_file(src.read_bytes())
            else:
                continue
        except Exception as e:                       # a broken file must not stop the run
            print(f"text: {r['path']}: {e}", file=sys.stderr)
            continue
        d = _dest(out, "text", r, ".txt" if not p.endswith(".txt") else "")
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(body, encoding="utf-8")
        n += 1
        if limit and n >= limit:
            break
    print(f"text: {n} files as UTF-8", file=sys.stderr)


# ----------------------------------------------------------------- scripts

def browse_scripts(out: Path, rows, limit) -> None:
    import sb
    import sbl

    by_path = {r["path"].lower(): r for r in rows}
    n = bad = 0
    for r in rows:
        if r["kind"] != "sb":
            continue
        import region
        bank = by_path.get(region.bank_path(r["path"]).lower())   # None on Japan: inline text
        try:
            s = sb.parse(_src(out, r).read_bytes())
            strings = sbl.parse(_src(out, bank).read_bytes()) if bank else None
            text = sb.listing(s, strings)
            problems = sb.check(s)
        except Exception as e:
            print(f"scripts: {r['path']}: {e}", file=sys.stderr)
            bad += 1
            continue
        if problems:
            bad += 1
        d = _dest(out, "scripts", r, ".asm")
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_text(text + "\n", encoding="utf-8")
        n += 1
        if limit and n >= limit:
            break
    print(f"scripts: {n} disassembled, {bad} with problems", file=sys.stderr)


# ------------------------------------------------------------------- audio

def _one_vsf(args) -> List[dict]:
    src, dest, path = args
    import vsf

    try:
        rows = vsf.export(Path(src).read_bytes(), dest)
    except vsf.VsfError as e:
        return [{"bank": path, "status": f"error: {e}"}]
    for r in rows:
        r["bank"] = path
    return rows


def browse_audio(out: Path, rows, limit) -> None:
    jobs = [(str(_src(out, r)), str(_dest(out, "audio", r)), r["path"]) for r in rows if r["kind"] == "vsf"]
    jobs = jobs[:limit] if limit else jobs
    results: List[dict] = []
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        for rs in ex.map(_one_vsf, jobs, chunksize=8):
            results += rs
    _write_csv(out / "browse" / "audio.csv", results)
    print(f"audio: {len(jobs)} banks -> {sum(1 for r in results if 'file' in r)} WAVs", file=sys.stderr)


# ------------------------------------------------------------------- music

def browse_music(out: Path, rows, limit) -> None:
    """The field and menu songs: the MIDI as shipped, a GM-safe copy, a
    SoundFont of the song's kit for other MIDI players and DAWs, and a WAV.

    The WAV is spu2synth's model of the KORG driver on the SPU2: hold keys,
    choke groups, the two cores, exact envelopes and the SPU2 reverb, with
    the driver's tables and libsd's reverb presets read from the disc's
    IOP\\KORGIVAG.IRX and IOP\\LIBSD.IRX (``dump`` copies both)."""
    import midi
    import prj
    try:
        import spu2synth                       # needs numpy (requirements.txt)
    except ImportError as e:
        raise SystemExit(f"music: {e}. numpy is required: python -m pip install -r requirements.txt")

    by_path: Dict[str, dict] = {r["path"].lower(): r for r in rows}
    d = out / "browse" / "music"
    d.mkdir(parents=True, exist_ok=True)
    songs = [r for r in rows if r["kind"] == "midi"]
    songs = songs[:limit] if limit else songs
    irx = libsd = b""
    if songs:
        iop = out / "browse" / "code" / "IOP"      # copied by `dump`
        missing = [n for n in ("KORGIVAG.IRX", "LIBSD.IRX") if not (iop / n).exists()]
        if missing:
            raise SystemExit(f"music: {' and '.join(missing)} not found in {iop}; run dump "
                             "again (it copies the sound driver and libsd from the disc)")
        irx, libsd = (iop / "KORGIVAG.IRX").read_bytes(), (iop / "LIBSD.IRX").read_bytes()
    results = []
    for r in songs:
        stem = r["path"][:-4]
        name = stem.rsplit("\\", 1)[-1]
        p_row, v_row = by_path.get((stem + ".prj").lower()), by_path.get((stem + ".vsf").lower())
        mid = _src(out, r).read_bytes()
        (d / f"{name}.mid").write_bytes(mid)
        gm = midi.gm_safe(mid)
        (d / f"{name}.gm.mid").write_bytes(gm)
        row = {"song": name, "mid": f"{name}.mid", **{k: v for k, v in midi.info(mid).items()
                                                      if k in ("ppqn", "ticks")}}
        if p_row and v_row:
            prj_bytes, vsf_bytes = _src(out, p_row).read_bytes(), _src(out, v_row).read_bytes()
            sf2 = prj.to_sf2(prj.parse(prj_bytes), vsf_bytes, name)
            (d / f"{name}.sf2").write_bytes(sf2)
            row["sf2"] = f"{name}.sf2"
            wav = d / f"{name}.wav"
            try:
                pcm, info = spu2synth.render(mid, prj_bytes, vsf_bytes, irx=irx, libsd=libsd)
                spu2synth.write_wav(str(wav), pcm)
                row.update(wav=wav.name, fx=f"{info['fx']} (return {info['fx_vol']}/127)",
                           seconds=round(len(pcm) / spu2synth.SR, 1))
            except Exception as e:                   # a broken song must not stop the run
                row["wav"] = f"render failed: {e}"
                print(f"music: {name}: render failed: {e}", file=sys.stderr)
        results.append(row)
    _write_csv(out / "browse" / "music.csv", results)
    print(f"music: {len(results)} songs", file=sys.stderr)


# ------------------------------------------------------------------ images

def browse_images(out: Path, rows, limit) -> None:
    n = 0
    for r in rows:
        if r["kind"] != "jpeg":
            continue
        d = _dest(out, "images", r)
        d = d.with_suffix(".jpg")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_src(out, r), d)
        n += 1
        if limit and n >= limit:
            break
    print(f"images: {n} JPEGs", file=sys.stderr)


# ------------------------------------------------------------------ movies

MOVIE_AUDIO = "adx"     # browse --movie-audio: adx | ac3 | both


def browse_movies(out: Path, rows, limit) -> None:
    """.sfd -> MP4. Every .sfd carries two audio tracks: CRI ADX stereo (stream
    0xC0, what the PS2 plays) and AC3 (0xC1; 5.1 in 48 movies, stereo in
    s040110 and s06over). ``MOVIE_AUDIO`` picks: adx (AAC from the ADX), ac3
    (the AC3 track copied bit-exact), or both (two audio tracks); with ac3 or
    both the raw AC3 is also written beside the MP4 as <name>.ac3."""
    ff = _which("ffmpeg")
    if not ff:
        print("movies: ffmpeg not found; skipped (the .sfd files play in VLC as they are)", file=sys.stderr)
        return
    done = []
    for r in rows:
        p = r["path"].lower()
        if not p.endswith((".sfd", ".ipu", ".adx")):
            continue
        if limit and len(done) >= limit:
            break
        src = _src(out, r)
        if p.endswith(".adx"):
            dest = _dest(out, "movies", r, ".wav")
            cmd = [ff, "-y", "-loglevel", "error", "-i", str(src), str(dest)]
        else:
            dest = _dest(out, "movies", r, ".mp4")
            video = ["-map", "0:v:0", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
            if p.endswith(".ipu") or MOVIE_AUDIO == "adx":
                audio = ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k"]
            elif MOVIE_AUDIO == "ac3":
                audio = ["-map", "0:a:1", "-c:a", "copy"]
            else:   # both: track 1 = ADX stereo as AAC, track 2 = AC3 as stored
                audio = ["-map", "0:a:0", "-map", "0:a:1", "-c:a:0", "aac", "-b:a:0", "192k",
                         "-c:a:1", "copy"]
            cmd = [ff, "-y", "-loglevel", "error", "-i", str(src)] + video + audio + [str(dest)]
            if p.endswith(".sfd") and MOVIE_AUDIO in ("ac3", "both"):
                raw = dest.with_name(dest.name[:-len(".sfd.mp4")] + ".ac3")
                raw.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-map", "0:a:1",
                                "-c", "copy", "-f", "ac3", str(raw)], capture_output=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(cmd, capture_output=True, text=True)
        done.append({"file": r["path"], "out": str(dest.relative_to(out / "browse")),
                     "status": "ok" if res.returncode == 0 and dest.exists() else res.stderr[-200:]})
    _write_csv(out / "browse" / "movies.csv", done)
    print(f"movies: {sum(d['status'] == 'ok' for d in done)}/{len(done)} converted", file=sys.stderr)


def browse_small(out: Path, rows, limit) -> None:
    """Packs, title logos, the save icon, battle strings and the small-file table (smallfiles.py)."""
    import smallfiles
    smallfiles.browse_small(out, rows, limit)


BUILDERS = {"textures": browse_textures, "models": browse_models, "xtx": browse_xtx, "fonts": browse_fonts, "text": browse_text,
            "scripts": browse_scripts,
            "audio": browse_audio, "music": browse_music, "images": browse_images, "movies": browse_movies,
            "small": browse_small}


def build_browse(out: Path, kinds: Iterable[str], limit: Optional[int] = None,
                 movie_audio: str = "adx") -> int:
    global MOVIE_AUDIO
    MOVIE_AUDIO = movie_audio
    rows = _rows(out)
    for k in kinds:
        BUILDERS[k](out, rows, limit)
    return 0
