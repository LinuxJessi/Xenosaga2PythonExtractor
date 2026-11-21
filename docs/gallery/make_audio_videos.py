"""Turn the README's audio samples into short videos GitHub can play inline.

GitHub strips <audio> from a README, but plays a video uploaded as an
attachment. Each video is a still card (a map render from this folder's
gallery, darkened, with the track's title) under a moving waveform of the
sample's own audio; the audio is the sample unchanged (re-encoded to AAC).

    python docs/gallery/make_audio_videos.py --out av/

Needs ffmpeg and Pillow (Pillow only for this script; the kit does not use it).
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DOCS = Path(__file__).resolve().parents[1]
SAMPLES = DOCS / "samples"
GALLERY = DOCS / "images" / "gallery"
W, H = 960, 400
FONT_DIRS = [Path("/System/Library/Fonts/Supplemental"), Path("C:/Windows/Fonts"),
             Path("/usr/share/fonts/truetype/dejavu")]

MUSIC = [  # sample, title, subtitle, background render
    ("bgm_001_milchia.mp3", "001  Milchia", "music/001_milchia", "E2_MIL12"),
    ("bgm_005_morgenroete.mp3", "005  Morgenröte", "music/005_morgenroete", "E2_UZU01"),
    ("bgm_009_lost_jerusalem.mp3", "009  Lost Jerusalem", "music/009_lost_jerusalem", "E2_SUI06"),
    ("bgm_103_kukai.mp3", "103  Kukai", "music/103_kukai", "E2_KUK06"),
    ("bgm_111_moby-dick.mp3", "111  Moby-Dick", "music/111_moby-dick", "E2_MOM11"),
]
SHORT = [  # played one after another in one video, each with its own card
    ("voice_S05_06_05_04_KOS_MOS.mp3", "\u201cKOS-MOS\u2026\u201d", "event S05_06_05 (sc8047), line 04"),
    ("voice_S05_06_05_05_Thank_you.mp3", "\u201cThank you.\u201d", "event S05_06_05 (sc8047), line 05"),
    ("voice_S05_02_00_07_Oh_be_a_man_already.mp3", "\u201cOh, be a man already!\u201d", "event S05_02_00 (sc8046), line 07"),
    ("sfx_CFS_SE_CFS00004.mp3", "Sound effect CFS00004", "CFS_SE.vsf"),
    ("sfx_CFS_SE_CFS00010.mp3", "Sound effect CFS00010", "CFS_SE.vsf"),
]


def font(names, size):
    for d in FONT_DIRS:
        for n in names:
            if (d / n).exists():
                return ImageFont.truetype(str(d / n), size)
    return ImageFont.load_default()


def card(title: str, sub: str, render: str | None, note: str) -> Image.Image:
    if render:
        bg = Image.open(GALLERY / f"{render}.jpg").convert("RGB")
        scale = max(W / bg.width, H / bg.height)
        bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
        left, top = (bg.width - W) // 2, (bg.height - H) // 2
        bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(2))
    else:
        bg = Image.new("RGB", (W, H), (8, 14, 28))
    shade = Image.new("L", (W, H))
    ImageDraw.Draw(shade).rectangle((0, 0, W, H), fill=150)
    img = Image.composite(Image.new("RGB", (W, H), (4, 8, 18)), bg, shade)
    d = ImageDraw.Draw(img)
    d.text((40, 34), "XENOSAGA EPISODE II", font=font(["Georgia.ttf", "georgia.ttf", "DejaVuSerif.ttf"], 20),
           fill=(120, 190, 230))
    d.text((40, 66), title, font=font(["Georgia Bold.ttf", "georgiab.ttf", "DejaVuSerif-Bold.ttf"], 48),
           fill=(240, 240, 245))
    d.text((40, 128), sub, font=font(["Arial Unicode.ttf", "arial.ttf", "DejaVuSans.ttf"], 20), fill=(200, 205, 215))
    d.text((40, H - 34), note, font=font(["Arial Unicode.ttf", "arial.ttf", "DejaVuSans.ttf"], 14),
           fill=(150, 160, 175))
    return img


def video(png: Path, audio: Path, out: Path, pad: float = 0.0) -> None:
    af = f"apad=pad_dur={pad}" if pad else "anull"
    graph = (f"[1:a]{af},asplit[a][w];"
             f"[w]showwaves=s={W}x150:mode=cline:rate=25:scale=sqrt:colors=0x7fd4ff[wave];"
             f"[0:v][wave]overlay=0:{H - 200}:shortest=1,format=yuv420p[v]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "25", "-i", str(png),
                    "-i", str(audio), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-tune", "stillimage",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)],
                   check=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=True)
    note = "Rendered by the kit from the game's MIDI and KORG samples \u00b7 background: the kit's render of map {}"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for mp3, title, sub, render in MUSIC:
            png = td / f"{mp3}.png"
            card(title, sub, render, note.format(render)).save(png)
            video(png, SAMPLES / mp3, out / mp3.replace(".mp3", ".mp4"))
        parts = []
        for i, (mp3, title, sub) in enumerate(SHORT):
            png = td / f"s{i}.png"
            card(title, sub, None, "Decoded by the kit from Disc 1").save(png)
            part = td / f"s{i}.mp4"
            video(png, SAMPLES / mp3, part, pad=1.2)
            parts.append(part)
        lst = td / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", "-movflags", "+faststart", str(out / "voices_and_effects.mp4")], check=True)
    for f in sorted(out.glob("*.mp4")):
        print(f"{f.stat().st_size // 1024:6d} KB  {f.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
