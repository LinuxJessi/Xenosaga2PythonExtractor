# Where Episode II's music lives, and how to get it out

Research note, 2026-11-05. Everything here was measured on the two USA discs
(SLUS-20892 / SLUS-21133) with scripts that only read the discs. Nobody has
listened to any of it yet: the numbers below are measurements, not
impressions. Inventory: `audio_inventory.csv` (2,804 rows: every audio-bearing
object plus the non-audio ones that look like they might be).

## The short answer

| Where | Objects | Minutes | What it is |
|---|---|---|---|
| Event scenes (`sound\adx\cfevent\S-xx-xx-xx\Sxx_xx_xx.adx`, `BE63xx`) | 88 | 153.7 | the full soundtrack of each real-time cutscene: dialogue, music and SFX premixed in stereo |
| Movies (`movie\*.sfd`) | 50 | 126.8 | the full soundtrack of each pre-rendered cutscene, twice: ADX stereo and AC3 5.1 |
| Field and menu music (`sound\smf\*.mid` + `.vsf`/`.prj`) | 31 | 54.2 (rendered) | sequenced: MIDI played on the game's own samples by the KORG driver |
| Recorded BGM (`sound\adx\battle01/02`, `boss01/02`, `bosslast`, `fanfale01`) | 6 | 11.0 | streamed music with loop points (battle, bosses, victory fanfare) |
| Title (`yajima\M-01.adx`) | 1 | 2.4 | streamed, no loop flag |
| Short phrases (`cfevent\S-Extra\s-Extra0N.adx`) | 5 | 0.7 | 8-10 s, wide stereo, very tonal |
| Cues (`sound\adx\CF_*.adx`) | 25 | 4.9 | 3-41 s stingers; music or SFX, measurements can't tell which |
| Voice | 532 | 29.1 | 480 battle lines, 52 isolated event lines (8 scenes) |
| Sample banks (`sound\vsf\**.vsf`) | 871 | 90.8 | SFX and ambience samples; a few long tonal loops (below) |

The released album, Yuki Kajiura's *Movie Scene Soundtrack* (VICL-61431/2,
40 tracks, 112.5 min), is cutscene music. On the disc, cutscene music exists
**only premixed with dialogue and SFX**: in the 50 movies and the 88 event-scene
files (280 minutes of mixed audio between them). There is no music-only
stream anywhere. The gameplay music by Shinji Hosoe (and, uncredited, Ayako
Saso), which was never released, is the part the disc gives cleanly: the 31
sequences and the 7 recorded streams.

Also checked and empty: every `.sfd` stream id (below); all 90 `.ipu` clips
(video only); 6,144 non-audio files scanned for ADX/VAG/VSF/PRJ/MIDI/RIFF/
MPEG/AFS signatures (the 74 hits are coincidental bytes inside pixel and table
data, checked by eye); the disc area outside the files (the 10,255/10,252
sectors after the last file are zeros but one).

## Measurements, and what they can and cannot say

Per file, from a 16 kHz decode (`music_feat.py`):

* **Loop points** (ADX header). Proof that the engine loops a stream: music.
  A missing flag proves nothing (the title stream has none).
* **L/R correlation and side level.** Speech recorded for games is mono and
  centred (correlation near 1); mixed music is wide (0.1-0.6 here). A wide
  file is music or ambience; a narrow one can still be centred music.
* **Speech-band share** (300-3400 Hz energy over all energy, loud frames).
  High for voice, but mid-range music is high too: a weak signal on its own.
* **Pause fraction** (frames 30 dB under the 95th percentile). Speech pauses,
  music mostly does not. Good separator for isolated voice files, useless for
  mixes.
* **Voiced fraction** (autocorrelation peak at an 80-400 Hz lag). Both speech
  and tonal music score high; only useful together with the others.
* **Spectral flatness.** Noise-like SFX high, sustained tones near 0. Cannot
  tell a drone from a melody.
* **Subtitle timing.** For movies and event scenes the dialogue fork's line
  times say exactly when someone speaks; comparing a channel's speech-band
  energy inside and outside those times is the strongest test used here.

The classification in the CSV combines names with these numbers; the
`evidence` column says what decided each row.

## Movies

Every `.sfd` carries exactly the same streams (byte counts for all 50 in
`sfd_streams.csv`): `0xE0` MPEG-1 video, `0xC0` ADX stereo, `0xC1` AC3 (5.1
in 48 movies, stereo in `s040110` and `s06over`), `0xBF` the Sofdec header,
`0xBB`/`0xBE` system and padding. No music-only stream. The `0xBF` header
names the sources: `s010100.sfv` (video, 2004-08-25), `s010100.ac3`
(2004-09-15) and `s010100.sfa` (the ADX audio, 2004-11-25, the same day as
the mux).

**The AC3 keeps dialogue in the centre channel** (`movie_channels.csv`, 39
movies with at least 5 subtitle lines):

| Measure | Median | Range |
|---|---|---|
| centre's share of speech-band energy during lines | 0.84 | 0.62-0.98 |
| centre's share outside lines | 0.41 | 0.10-0.78 |
| centre speech-band level, lines vs no lines | +5.2 dB | +1.4 to +8.5 |
| front left, lines vs no lines | -4.2 dB | -11.8 to +1.5 |
| surround left, lines vs no lines | -3.8 dB | -15.9 to +3.6 |

The other channels get *quieter* when someone speaks, consistent with the music
being ducked under dialogue rather than dialogue leaking into them. The LFE
carries nothing in the speech band.

**The ADX stereo is its own mix.** Aligned to the sample (the AC3 trails the
ADX by about 1,263 samples, 26 ms), ADX left correlates 0.53-0.81 with a
standard downmix of the AC3 (four movies tested) and 0.57-0.90 with the AC3
centre alone; it is about 10 dB louder. Related but separately balanced, so
it cannot be "un-downmixed".

**Prototype: music and SFX without the centre** (`stems/`, `music_stems.py`).
Left = FL + 0.71 SL + 0.5 LFE, right = FR + 0.71 SR + 0.5 LFE, compared with
the full AC3 downmix:

| Movie | Lines | Speech-band drop during lines | Level lost outside lines | Speech band in the stem, lines vs none |
|---|---|---|---|---|
| `s050400` | 30 | 8.0 dB | 2.6 dB | -3.7 dB |
| `s010100` | 118 | 6.7 dB | 1.7 dB | -4.6 dB |
| `EPSTAFF` | 49 | 5.6 dB | 1.5 dB | -3.4 dB |

The last column is negative: during lines the stem is quieter than between
them, so what remains follows the ducking, not the voices. That does not prove
there is no dialogue bleed left in the side channels; only listening can.
Each movie also has the centre alone (`*.ac3_centre_dialogue.wav`, mostly
dialogue) and the untouched ADX for comparison.

## Event scenes

88 stereo files, one per real-time scene (75 `Sxx_xx_xx`, 8 of them split
`a`/`b`, plus 13 `BE63xx`); 8 other scenes instead have mono per-line voice
clips (`030920_Albedo_01` …), and their music must come from somewhere else
(not traced here). Dialogue covers 61-78% of the five scenes measured. The
dialogue sits in the middle of the stereo image: the mid signal's speech band
rises 9-16 dB during lines. The side signal (L-R) keeps far less of the
dialogue, but it is 2.4-9.4 dB weaker than the mid outside lines too, so a
centre-cancel throws away most of the music with the voices. For these,
machine-learning separation is the only real route.

## Gameplay music

* **31 sequences** (`sound\smf`): type-0 MIDI at 480 PPQN on per-song KORG
  banks (751 samples, 11 min, instruments such as `03_pf_2G`, `020_oke_amb`,
  `001_groove_a_001`). Rendered by the kit (`browse --kinds music`) through
  `songfx.py`; 54.2 minutes for one pass of each.
* **6 recorded loops and the title**: loop points from the ADX headers, e.g.
  `battle01` loops 5.26-105.54 s, `bosslast` 24.01-191.78 s, `fanfale01`
  22.14-30.86 s. Already decodable losslessly (relative to ADX).
* **Sample banks**: 871 banks with real samples (172 hold only placeholders),
  median sample 0.75 s. Of 86 samples longer than 8 s, a few are very tonal
  long loops (`CF_EXD_08`, `CF_MID07_01`, `CF_MID08_03`, `CF_MOM_A_09`,
  `CF_MOM_C_09`, `CF_OLM13_01`, `CF_OLM14_02`, `CF_MIL09_01`, `CF_UMN_05`):
  drones or music phrases; ears needed. Nothing suggests album music hides in
  the banks.

## Mapping the album

Track titles and lengths: [Wikipedia, Music of Xenosaga](https://en.wikipedia.org/wiki/Music_of_Xenosaga)
(Movie Scene Soundtrack 40 tracks 1:52:26; the 11-track soundtrack CD packed
with the BradyGames guide, 30:25, all of them titles that are also on the
main album). [Xeno Series Wiki](https://www.xenoserieswiki.org/wiki/Music_of_Xenosaga_Episode_II)
says only Kajiura's work was ever released, and not all of it.

**Durations alone do not map anything.** Matching the 40 album lengths against
the 150 disc objects within 1 s finds 18 tracks; shifting every album length
by ±5-9 s finds 17-21 (median 19.5). Pure chance. What remains:

| Album track | Disc candidate | Evidence | Confidence |
|---|---|---|---|
| Sweet Song (Xenosaga II ending theme), 5:33 | `EPSTAFF.sfd`, the staff roll, 332.3 s | the credits movie; length within 1 s | likely |
| lamentation, 5:24 | `s030500.sfd`, 323.1 s | length only; the scene is mostly MOMO and Albedo | guess |
| Nephilim, 1:29 | `EP07.sfd`, 88.7 s | length only | guess |
| the other 37 | somewhere in the 50 movies and 88 scenes | none | unknown |

**The way to map it properly**, without fetching any audio: if Jessi rips her
own copy of the album, match chroma or Chromaprint fingerprints of each track
against the movie and scene audio. That finds every cue, its start time and
which album tracks are edits of in-game cues. The disc also certainly holds
Kajiura cues that never made the album; the same match lists them as the
unmatched music segments.

## How to get each kind out

| Source | Route | Ceiling |
|---|---|---|
| Sequences | the kit's render, or record PCSX2's SPU2 output for the console's exact sound | our synth model (reverb approximated) vs the real thing |
| Recorded loops, title, `S-Extra` | decode the ADX; export intro + N loops + fade from the header loop points | 48 kHz 4-bit ADX: clean |
| Movie music | 1. the AC3 minus the centre (prototype above): dialogue mostly gone, SFX kept, ducking dips kept. 2. Demucs v4 / UVR-MDX on that stem or on the full AC3 to split music from SFX and residual voice | AC3 448 kbps lossy, separation artefacts; the album where a track exists |
| Event-scene music | ML separation of the stereo file; mid/side only as a fallback | separation artefacts; dialogue fills most of the time |
| Album tracks | Jessi's own CD | studio master |

ML separation was assessed, not installed (torch-sized). Demucs' "vocals" stem
targets singing, so dialogue goes there reasonably well; "other"+"bass"+"drums"
approximates the music, with SFX split between them. Running it on the no-centre
AC3 stem first should help, since the dialogue is already 5-8 dB down.

**What the Japanese disc could add** (untested until Jessi rips hers). If its
movies and scenes carry Japanese dialogue over the *same* music and SFX bed,
sample-aligned, the two mixes share everything but the voices. Where only one
language speaks at a moment, the other mix is clean; per time-frequency bin,
the quieter of the two mixes approximates the bed. The difference of the two
(US - JP) cancels music and SFX entirely, so it also tells you exactly where
dialogue sits. The JP disc may also differ in which scenes are real-time and
which are movies, and in its Sofdec header dates.

## Open questions

* Where the music of the 8 scenes with per-line voice clips comes from (MIDI?
  a movie? a `CF_` cue?): needs the event scripts' calls.
* Whether the 25 `CF_*` cues and the tonal sample-bank loops are music.
* Whether the AC3 side channels carry any dialogue bleed (listen).
* Whether the JP disc's beds are sample-identical to the USA ones.

Scripts (session scratch): `music_sfdprobe.py`, `music_feat.py`,
`music_adxinv.py`, `music_movies.py`, `music_stems.py`.
