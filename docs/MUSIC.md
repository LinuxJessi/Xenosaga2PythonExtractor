# The music of Xenosaga Episode II: how it works, and what the disc told us

Episode II streams its battle and boss music (`sound\adx\*.adx`) but plays
its field and menu music live: 31 Standard MIDI files, each with a project
(`.prj`) and a bank of samples (`.vsf`), played by a sound driver from KORG
on the PS2's sound processor. The kit turns each song into the MIDI, a
SoundFont 2 built from the song's own samples, and a WAV render. KORG's
driver (`IOP\KORGIVAG.IRX`, "KORG IOP Sound Driver … (C)2001-2004 KORG
Inc.") ships with its symbol table, so the rules below are read from its
code, not guessed; the reverb presets are the disc's own `IOP\LIBSD.IRX`.

```sh
python cli.py dump   --iso "Disc 1.iso" --out OUT          # also copies IOP\KORGIVAG.IRX and IOP\LIBSD.IRX
python cli.py browse --out OUT --kinds music,audio
python songfx.py OUT/dump/chain0/sound/smf/103_kukai.mid                 # the song's effect settings
python spu2synth.py OUT/dump/chain0/sound/smf/103_kukai.mid OUT/dump/chain0/sound/smf/103_kukai.prj \
       OUT/dump/chain0/sound/smf/103_kukai.vsf kukai.wav --seconds 30     # SPU2 render (finds the IRXs in OUT)
python prj.py OUT/dump/chain0/sound/smf/103_kukai.prj OUT/dump/chain0/sound/smf/103_kukai.vsf kukai.sf2
```

| Output | What it is |
|---|---|
| `browse/music/NAME.mid` | the song as stored: type 0, 480 PPQN, real tempo |
| `browse/music/NAME.gm.mid` | the same with MIDI channel 10 moved, for General-MIDI players (see below) |
| `browse/music/NAME.sf2` | a SoundFont 2 of the song's project and samples (an approximation, see below) |
| `browse/music/NAME.wav` | the render: `spu2synth.py`'s model of the driver on the SPU2 |
| `browse/audio/**.wav` | every named sample of every `.vsf` bank, with loop points (3,733 samples) |

`browse/music.csv` lists each song's effect and length. The renderer needs
numpy (`requirements.txt`) and two of the disc's IOP modules: the driver,
`KORGIVAG.IRX`, for its tables, and libsd, `LIBSD.IRX`, for the reverb
presets. `dump` copies both into `OUT/browse/code/IOP/`.

The renders are derived from the disc's data: for your own use, made from
your own copy of the game.

## The pieces

**Samples** (`.vsf`, "VSFp"): SPU ADPCM, one 0x28-byte record per sample
`{u32, u32, u16 rate, u8 root key, u8 fine, u32 base pitch, u32, u32 size,
name[16]}`, then the data. The base pitch is `rate * 4096 // 48000` (751 of
751 music samples), the SPU pitch register for the sample's own rate. The
names are the composers' file names (`001_groove_a_001`, `005_synbass_a_00`,
`CFS00001.wav`).

**Projects** (`.prj`, "PRJp"): a key record per sample use (two 16-byte
layers: sample, SPU ADSR1/ADSR2, volume, pan, transpose, fine tune,
key-follow, choke group, hold, core) and programs that map the 128 MIDI
keys onto key records. Program N is the song's program change N.

**Songs** (`.mid`): ordinary MIDI. The driver has no drum channel: MIDI
channel 10 is an instrument like any other. A General-MIDI player would
play it as drums, so the kit writes a copy with channel 10 moved to a free
channel and also mirrors every program into bank 128 of the SoundFont.

## What the driver does

### Keys: hold, choke groups, cores (`ChannelNoteOn`, `ChannelNoteOff`, `ChannelCheckGroup`)

* A layer sounds whenever it names a sample (index non-zero). Byte +13 is
  not an on/off flag: bit 0 picks the SPU2 core (1 in nearly every music
  key; the few 0/2 layers play on core 0).
* **Hold** (byte +12 non-zero): `ChannelNoteOff` skips the key-off, so the
  one-shot sample plays to its end. 554 music keys hold, the drum and
  groove-slice kits among them, which the songs strike with short notes:
  in 001 Milchia the cymbal slice `001_groove_a_009` (0.504 s) sits under
  0.125 s notes, the long kick `020_kick_long.ai` (3.53 s) under 0.045-0.125 s
  ones. Releasing at note-off, as a SoundFont player does, cuts the cymbal
  to about 0.14 s, a quarter of its length, and the kick to under 4 %: the
  "crash cuts off early" a listener heard.
* **Choke group** (byte +11): a note releases the channel's sounding voices
  of the same group (normal release), before its own layers start.
* **Voices**: 24 per core. A note takes the first idle voice of its core;
  if none, `GetLowestPriorityVoice` steals the voice with the largest
  priority word: 0x80 once a voice is released, else its age rank (the
  channel priority byte above it, CC89, is unused by the songs). Released
  voices go first, then the oldest; most steals land on release tails.
* **Timing**: the sequencer runs in the driver's 2 ms timer; events take
  effect on the tick at or after their time.

### Pitch (`VoiceOn`, `PitchCalc`)

Pitch is kept in 1/256 semitone:

```
semitones = ((note - sample root) & follow) + layer transpose + (channel coarse & follow)
fine      = layer fine - sample fine  (+ channel fine when the pitch follows the key)
pitch     = semitones << 8  +  fine << 2  +  bend  +  LFO
register  = NOTE[pitch >> 8] * base >> 15 * FINE[pitch & 0xFE] >> 15   (max 0x3FFF)
```

with the driver's own note and fine tables. Bend is `(range * bend14) >> 5`
(range from RPN 0, clamped 0-24, default 2); the pitch LFO advances its phase
by CC27² per tick with depth `2 * CC1 * 2`, waveform from program byte +0x18.
The transpose byte is semitones and the fine byte is 1/64 semitone (1.5625
cents), both signed; `follow` is the key-follow byte (0xFF, or 0 for
fixed-pitch groove loops, which keep their transpose and fine anyway).

### Volume and pan (`VoiceOn`, `DynamicVolumeExp`, `DynamicPan`)

Every volume goes through the driver's square-law table `32767 (x/127)²`:
voice level = velocity × layer volume × program volume (+0x13), channel
level = unit volume × CC11 × CC7; left/right through its equal-power pan
table (`s_aPanCurve`: 32767 at the hard side, 23170 at centre), pan = layer
pan + CC10 − 64. The register is `level × pan >> 16` (0x3FFF = full scale).

### The effect unit (`CcProcDataEntryMsb`, `FxProc`, `FxChangeThread`)

The songs set the SPU2 reverb/echo themselves, with NRPNs that each song
labels with a text event:

| Text in the MIDI | NRPN | Meaning |
|---|---|---|
| `FX1 MODE` | 2 / 16 | effect type |
| `FX1 VOL` | 3 / 16 | return volume 0-127 |
| `FX1 PAN` | 3 / 17 | return pan |
| `FX1 DELAY` | 3 / 18 | delay time 0-127 (ECHO, DELAY) |
| `FX1 FEEDBACK` | 3 / 19 | feedback 0-127 (ECHO, DELAY) |

LSB bit 4 picks the SPU2 core: the songs all use core 1 ("EFX core1").
The driver clamps the type to 0-9 and hands it to `sceSdSetEffectAttr`
(a type change, in `FxChangeThread`, which also clears the work area) or
`sceSdSetEffectModeParams` (delay/feedback/volume changes, in `FxProc`), so
the numbers are libsd's: 0 OFF, 1 ROOM, 2 STUDIO_A (small), 3 STUDIO_B
(medium), 4 STUDIO_C (large), 5 HALL, 6 SPACE (space echo), 7 ECHO, 8 DELAY,
9 PIPE. The return level (EVOL) is `pow2(vol) × pan >> 15`, i.e.
`(vol/127)² × 0.7071` at centre. The driver's default is OFF.

Which voices go through the effect: the voice's effect bit is
`(CC91 >> 6) & 1`, so CC91 of 64 or more sends the channel (the songs use 0
and 127). The SPU2 is set up by `korgIopVagInitSpu2` with MMIX core 0 =
0xFC0 and core 1 = 0xFCC: each core's voices feed its own dry mix and its own
effect unit, and core 0's output enters core 1 dry only. With every song's
effect on core 1, only core-1 voices with the bit set are reverberated.

**The presets** come from `IOP\LIBSD.IRX` (2.7.1.0): 10 records at .data
0x4A90 (a u32 write mask, then the 32 reverb registers), work-area sizes at
0x4A68, addresses in 8-byte units written ×4 into SPU2 words. STUDIO_A/B/C,
HALL and SPACE equal psx-spx's PS1 tables register for register; ROOM
differs only in its DIFF and COMB3/4 offsets, unused by psx-spx's version;
ECHO and DELAY are templates that libsd rewrites from the song's values:

```
D = (delay + 1) * 32
mLSAME = 2D - dAPF1     mRSAME = D - dAPF2      dLSAME = dRSAME + D
mLCOMB1 = mRCOMB1 + D   mLAPF1 = mLAPF2 + D      mRAPF1 = mRAPF2 + D
vWALL = feedback * 258
```

(at delay 127 this rebuilds psx-spx's "Chaos Echo"). So an ECHO repeats every
`(128 (delay + 1) − 24) / 24000` s, each repeat `feedback × 258 / 32768` of
the last, phase-inverted (input volume 0x8000); DELAY uses the same rewrite,
so with a feedback set it behaves as ECHO.

| Effect | RT60 of the SPU2 effect (model) | Songs |
|---|---|---|
| HALL | 1.89 s | 007 Ormus inside, 008 Imaginary Territory, 009 Lost Jerusalem, 013/015/016/017 CF extend maps, 106 Milchia 2nd, 110 CF quest, 111 Moby-Dick |
| ECHO | 1.1-6.3 s (feedback), repeats every 324-628 ms | 001 Milchia (538 ms), 002 Milchia 2nd, 011 Omega System inside, 012 Lasdan, 101 Elsa, 102 Durandal, 103 Kukai, 109 Deep Zone (324 ms), 203 Game Over (return 0: silent) |
| STUDIO_C | 1.98 s | 004 Deep Zone 2, 010 Labyrinthos, 014 Ormus escape, 107 Home, 108 UMN center |
| SPACE | 3.83 s | 003 Deep Zone, 006 Ormus, 202 UMN |
| STUDIO_B | 0.85 s | 105 Vector 2nd |
| ROOM | 0.69 s | 201 Menu |
| OFF | | 104 Robo lab (set to OFF), 005 Morgenröte (never sets a type) |

`python songfx.py SONG.mid` prints one song's settings and every change.

## The renderer (`spu2synth.py`)

A voice-by-voice model of the above on the SPU2, in numpy: ADPCM decode,
the pitch counter in 1/4096 sample, the SPU's 4-point Gaussian interpolation,
the ADSR envelope stepped exactly per sample (psx-spx's rules), per-tick
volume, pan, bend and LFO, hold, choke groups, 24 voices per core with the
driver's steal order, each core's 16-bit clamps, and the reverb network
(same- and different-side reflections, four comb taps, two all-pass
filters) running at 24 kHz, left on even and right on odd samples, with
every read of a cycle before its writes (PCSX2's order). The reverb is solved
as delay-line equations rather than a simulated buffer; the self-test checks
it against a word-by-word work area to 1e-12. The 48↔24 kHz filters are
designed here, not the hardware's, so the effect's top octave can differ.
The level is the PS2's own at unit volume 127, clamps included: 27 of the 31
songs clamp briefly at their loudest hits (at most 0.14 % of samples), as the
console does. (`render(..., headroom=True)` scales a song down instead.)

Not modelled: portamento (CC5), the pitch envelope, the driver's hardware
volume sweeps (changes are ramped over one 2 ms tick), and the songs'
`loop start` / `loop end` markers (renders play once and ring out 6 s).

### Checked against game rips

Five tracks of a 128 kbps game rip (KHInsider) were lined up with the
renders by chroma and onsets: each matched its song at a chroma similarity
of 0.87-0.94 against ≤ 0.58 for other songs, except that Moby-Dick's
runner-up is 008 Imaginary Territory (0.93), the same piece in another
arrangement, which fits its spectrum worse (0.42 against 0.36). Tempo is
equal (drift −12 ppm over 001), pitch offset 0 cents.
Numbers below are the rip against each render over the first pass of the
song (log-band error: rms of log₁₀ energy in 40 bands × 20 ms frames, per-band
offset removed; lower is closer):

| Song (effect) | log-band error v1 → v2 (v2 dry) | 1/3-octave balance rms v1 → v2 | best wet gain ×EVOL | best of the 9 presets |
|---|---|---|---|---|
| 001 Milchia (ECHO) | 0.504 → 0.278 (0.290) | 1.5 → 0.2 dB | 0.71-1.0 | ECHO |
| 106 Milchia 2nd (HALL) | 0.383 → 0.316 (0.325) | 1.0 → 0.2 dB | 0.71-1.0 | HALL |
| 109 Deep Zone (ECHO) | 0.756 → 0.423 (0.547) | 2.4 → 0.3 dB | 1.0 | ECHO |
| 111 Moby-Dick (HALL) | 0.440 → 0.293 (0.475) | 1.4 → 0.3 dB | 0.71-1.0 | HALL |
| 201 Menu (ROOM) | 0.522 → 0.223 (0.378) | 1.5 → 0.3 dB | 1.0 | ROOM |

* **Echo delay** (001): a 1 ms scan of the echo against the rip has its
  minimum at 538 ms (model 537.7 ms; the old render used 403 ms). 109's
  scan is broader and sits at 330 ms (model 324 ms), its eighth note being
  315 ms; the old 242 ms is clearly worse.
* **Held hits** (001, envelope 0-0.6 s after each hit against the rip, rms
  dB): cymbal `_009` 6.0 → 1.6, cymbal `_010` 6.5 → 1.1 (−20 dB at 503 ms in
  the rip, 505 ms now, 160 ms before), hit `33u-hit07` 6.5 → 1.9, long kick
  10.8 → 5.2. At the first long kick (28 s) the old render lacked 12 dB at
  40-80 Hz; now 0.9 dB.
* **Stereo** (side/mid in four bands): within 0.5 dB of the rip on all five
  (the old renders were up to 3.3 dB off).
* **Level**: the rip's 2 s loudness contour follows the render with
  correlation 0.97-0.99 (0.57-0.98 before). The rips are peak-normalised, so
  absolute level cannot be checked; 001's crest factor (14.0 dB) agrees with
  the render at the PS2's level with its clamps (14.5 dB), not without them
  (16.9 dB).

The rip is a lossy copy of unknown chain (fades, possible normalisation), so
it can confirm shapes and ratios, not fine detail above ~16 kHz. A PSF2 set
(the game's driver running in an emulated IOP and SPU2) would allow sample-
level checks: per-voice envelopes, the exact reverb tail, the start-up order
of the effect settings.

### The SoundFont

`prj.to_sf2` maps what a SoundFont can carry: volume, pan, tuning, the
ADSR times (scaled to SoundFont semantics: a decay or release time is the
time to fall 100 dB), hold layers as a release long enough for the one-shot
to play out, choke groups as the exclusive class. It is there for playing
the songs in other MIDI players and DAWs; the kit's own render does not use
it. (The kit's first renders, "v1" above, were this SoundFont played by
fluidsynth.)

## Song names

The MIDI files name themselves in a track-name event, sometimes with a
working title: `001_milchia` calls itself `001_miltia`, `006_ormus`
`006_olmus`, `008_imaginary_territory` `300_an_chloe`, `111_moby-dick`
`111_an_chloe`, and `016`/`017_cf_extend_map3`/`4` carry the names of
`015` and `013`, the files they were copied from.
