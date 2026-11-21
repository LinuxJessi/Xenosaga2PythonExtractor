# Dialogue: every line, in the order the game plays it

Episode I keeps its spoken lines inside Java scene scripts (`evt-export` in
the Episode I kit) and its voices in streams. Episode II spreads the same
material over three places, and `dialogue` carves all of them out as lines:

```sh
python cli.py dialogue --iso "Disc 1.iso" --iso "Disc 2.iso" --out lines/            # text, ~10 s
python cli.py dialogue --iso "Disc 1.iso" --iso "Disc 2.iso" --out lines/ --voice    # + one WAV per timed line
python cli.py dialogue --iso "Disc 1.iso" --out lines/ --only sc8001                 # one scene or movie
```

`text-export` stays the tool for *editing*: it dumps the 465 `.sbl` banks
whole, resource names included, in table order. `dialogue` is the tool for
*reading and listening*: only what reaches the screen or the speakers, in
code order, with speakers, choices, branches, timings and voices. Every
script line keeps its `.sbl` id, so it maps straight back to `text-export`'s
`### NNNN` block when you want to change it.

## What comes out

| Path | What |
|---|---|
| `scripts/<scene>.txt` | a play-script transcript per scene, field or battle script (363 of the 465 have lines) |
| `subtitles/<movie>.srt` | the 45 movies' subtitles |
| `subtitles/<scene>.srt` | the 80 real-time events' timed lines, timed against the event's voice track |
| `voice/<movie>/NN_<words>.wav` | `--voice`: one clip per movie cue, cut from the movie's own soundtrack |
| `voice/<track>/NN_<words>.wav` | `--voice`: one clip per event line, cut from the event's voice track |
| `dialogue.csv` | every row: `source, scene, order, entry, branch, speaker, text, sbl_id, native, start, end, voice, clip, raw` |
| `voice_check.csv` | `--voice`: per clip, its length, loudness, and the loudness just before and after its start |

A transcript reads like this (`sc1010`, the first save point):

```
== goteinei ==
  Canaan: What is this thing?  [#4]
  chaos: It's a save point.  [#5]
  chaos (message): If you touch it and press <○>, you can record your current progress and restore your health.  [#6]
```

and a real-time event like this (`sc8001`, the Old Miltia opening):

```
== L_0000 ==
   (voice track: cfevent/S-01-03-00/S01_03_00)
  [00:00.33-00:03.50] Looks like we're pretty far out from our objective.  [#6]
  [00:04.00-00:09.67] Plus, I'd say the defensive network we saw on the way down takes out the idea of an airborne approach.  [#7]
```

`[#N]` is the line's number in the scene's `.sbl`.

## Where the lines come from

### Scene, field and battle scripts

A string in a scene's `.sbl` is dialogue when the script hands it to a
native that shows text. All 13,818 text pushes on the disc reach a native
(a symbolic walk of the script machine's stack, docs/SCRIPTS.md); these are
the ones that put a line on screen:

| Native | Calls | What it is | Arguments |
|---|---|---|---|
| `talk.msgDtl` | 3,886 | a field text box, `[Speaker]` first | (4, text) |
| `talk.smsg` | 1,563 | a message window: tutorials, "You obtained ..." | (text) |
| `talk.menuAddItem` | 534 | a choice; the options are one string split by `\n` | (0, text) |
| `cfe.setMes` | 1,946 | a real-time event subtitle | (text, start, end, voice, 0) |
| `bat.msgPut` | 688 | a line spoken in battle (`$col`/`$zoom` codes) | (x, y, text, ..., ..., ...) |
| `bat.namePut` | 1,363 | a battle banner: the name of an enemy's skill or action, 416 different names (`$zoom13;Medica`) | (..., ..., ..., text, ...) |

`bat.namePut` lines are listed as `(banner)` in the transcripts and with
native `bat.namePut` in `dialogue.csv`. OV01's `FUN_00aa1330` looks the
fourth argument up in the bank through `FUN_00a9f238` (the lookup
`bat.msgPut` uses) and hands it to `FUN_002355d8`, which draws the box as
wide as the text. A name has a copy in every bank whose battles use it;
`text-export` marks them and `text-import` notes copies edited unalike
(docs/MODDING.md, "Battle banners").

`cfe.setMes` is two things. Called with an empty text it names the event's
voice track, `cfevent/S-01-03-00/S01_03_00`, which is
`sound\adx\cfevent\S-01-03-00\S01_03_00.adx`. Called with a line, its two
integers are the line's first and last frame on the event's timeline, at 30
frames a second (BE6302's last line ends at frame 600 = 20.0 s; the track runs
20.76 s). A text of only `\n` clears the line and is not listed.

The voice track starts at the event's first sound keyframe, the third
argument of the first `cfe.stKySnd` call: frame 0 in most events, 85 in
`sc8016`, 1830 in `sc8073`, the second half of an event split over two
tracks (`S02_14_00a`/`b`) whose frames carry on from the first half. Times in
the transcripts, SRTs and the CSV are on the voice track; the CSV's `frames`
column keeps the script's own numbers. This was checked against the audio:
for every voiced event the kit finds the offset that makes the track loudest
inside the lines against outside them. 76 of the 78 come out within a few
frames of the scripted time, and `sc8016` and `sc8073` come out on their
keyframe (84 against 85, 1828 against 1830). One event sets no keyframe but
runs past its track, `sc8074` (the second half of `S03_09_10`). With
`--voice` its start is taken from that measurement, 2300 frames (contrast
11.3 dB, next best 5.8 dB), and `voice_check.csv` says so. Without `--voice`
its times stay on the event timeline.

So every event line has an exact time and an exact stretch of voice, written
by the scene's author.

Voice without text is listed as well, so the order of what you hear is
complete: `bat.streamPlay` (303 battle barks, `sound\adx\battle\<name>.adx`,
mostly without a subtitle) and `runtime.adxplay`/`runtime.adx` (31 field voice
clips, `sound\adx\CF_*.adx`). Most else the scripts pass as text is a
resource name and is left out: effect names to `runtime.setNumber`, motion
files to `cfe.ldMt`, movie names to `runtime.mpeg2`. Not resource names but
also left out of the transcripts: the strings `100`, `802` and `102` that
`samp.fontPut` draws (28 calls; they look like a debug or template routine,
not checked in the game). They are exported in the bank files like every
string, and `text-coverage` counts them.

Speakers: field and battle lines carry `[Speaker]` in the text and it is split
off. Event and movie lines carry no speaker anywhere on the disc.

### Order and branches

A script is a set of routines: its named entry points, every `call` target
and every thread start (`pushcode`). Lines come out per routine, in code
order. Code that branches is not flattened into one story: the transcript
opens an `if:`, `else:`, `loop:` or `case N:` block where the compiler laid
the code out that way, and the CSV's `branch` column holds the same path
(`if@06a0 > case 1@0712`). The conditions are game flags and are not named;
a choice's options are printed as `CHOICE: Yes | No`, and the cases that
follow it are its answers.

### Movie subtitles

`movie\en\*.txt` (EUC-JP, 20 on Disc 1, 25 on Disc 2) time each cue as
`mm:ss:ff` (ff at 30 fps; a bare frame count and `+N` frames after the
previous cue are accepted too). An empty cue clears the line, so a line ends
where the next cue starts; `end` closes the file. `$ay390;$xx0,22;` place the
text, `\n` breaks it, `[漢字/かんじ]` is ruby and keeps its base. The movies'
lines carry no speaker. Besides the `sNNNNNN` story movies there are `EPSTAFF`
(the staff-roll movie, which has dialogue) and `EP07` (a closing scene).
Five of the 50 movies have no subtitle file: `s020310`, `s060400`,
`s06over`, `s080500`, `s090500`. Four of them have the same audio on the
Japanese and the USA discs, so they are probably wordless. `s020310` (108.6
s) is not: its USA audio track differs from the Japanese one (re-dubbed, so
probably voiced; nobody has listened to it yet), and neither region has a
subtitle file for it, so whatever is said in it exists only as sound and no
text tool can reach it. Adding one is not possible within what the code
allows: the player (`FUN_001e7d08`) builds the path from the movie's name
and reads it through the TOC (`FUN_0019c1e0`, which returns -1 for a missing
file, and then the movie plays without subtitles); the cues are kept at the
start of the block allocated for the video decoder and the decoder's work
area starts right after them (see "Subtitle limit" in MODDING.md). With no
file that work area starts at the block's start, so there is no room for any
cue. The table of contents itself could take a new name (the lookup
`FUN_0019b458` walks it from the start and compares whole paths, order does
not matter, and Disc 1's movie TOC, which one branch of `FUN_0019d850` reads
into a 0x1800-byte area, has about 1,900 bytes of filler left), but that
does not help the decoder's block. `text-import` therefore refuses a
subtitle file in the tree for a movie that has none on the disc, and says
why.

## Voice clips (`--voice`)

Movie clips are cut from the movie's ADX stereo stream: the soundtrack as
the player hears it, voice, music and effects mixed, which is all the disc
has for movies. Each clip runs from its cue to the next cue.

Event clips are cut from the event's ADX track by the script's own start and
end frames.

`voice_check.csv` gives every clip's RMS level, and the level in the 0.6 s
after the cue's start against the 0.6 s before it: a voice that starts on its
cue makes the first louder. Nobody has listened to the clips yet; the numbers
are the check (see "Measured" below).

## Measured (2026-11-05, both USA discs)

* 465 scripts walked, 363 with lines: 10,151 rows. That is 3,886 text boxes,
  1,563 messages, 534 choices, 1,647 timed event lines, 688 battle lines,
  1,499 movie cues, and 334 voice-only calls. About 10 s without
  `--voice`.
* `--voice`: 3,143 clips (1,498 movie, 1,645 event), 1.6 GB, about 5 minutes
  for both discs. Two event scenes, `sc8096` and `sc8097`, have lines but no
  voice track.
* Loudness at each clip's start against the 0.6 s before it: louder for 84 %
  of movie clips (median +4.3 dB) and 91 % of event clips (median +9.6 dB).
  One clip of all 3,143 is below -50 dBFS.
* Every movie's last cue ends inside the movie, e.g. `s010100` 499.3 s of
  511.8 s and `EP07` 83.3 s of 88.7 s. The exception is `s090410`, whose
  final cue overruns its 26.5 s movie by 0.12 s and is cut at the end.
* The event tracks are full mixes, not voice stems: in none of the 78 do the
  gaps between lines fall below -45 dB (typically -15 to -30 dB), and the
  lines are a median 8.9 dB louder than the gaps. Event clips, like movie
  clips, carry the scene's ambience and music under the voice.
* Nobody has listened to the clips; the numbers above are the check.

## The other voice files in `sound\adx\cfevent`

145 event ADX files are on the two discs. The scripts name 91 of them, the
tracks above. The other 54 are named by no script and by no string in the
executable: one-line clips named by speaker in 12 folders
(`S-03-09-20\030920_Albedo_01` … `_Rubedo_09`, `_Negredo_0x`;
`S-04-04-00\040400_margulis_0x`, `_pope_0x`; `S-04-07-00\040700_Juli_0x`,
`_MOMO_0x`; `S-04-0x-00\0402..0600_NA_0x`; `S-05-01-06\050106_shion_0x`),
plus the `b` halves `S03_01_00b` and `S03_02_00b`. How (or whether) the game
plays them is not known yet, so `dialogue` does not pair them with text;
`browse --kinds movies` converts them to WAV with every other ADX.

## Open

* Speakers of event and movie lines: not on the disc as text; the voice is.
* The conditions behind each branch (game flags) are not named.
* Battle barks (`bat.streamPlay`) have no subtitle; the few `bat.msgPut`
  lines near them are not paired with them, because the code does not pair
  them.
* The 54 unnamed one-line voice files above.
