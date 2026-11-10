# Historical context: what this disc records about Episode II

The extraction is also a record of how the game was built. Facts from the
disc are reproducible with this kit; the few outside facts are linked to
their source.

## The game

*Xenosaga Episode II: Jenseits von Gut und Böse* was developed by Monolith
Soft and published by Namco: in Japan on
[June 24, 2004](https://classic.rpgfan.com/news/2004/1150.html), in North
America on [February 15, 2005](https://en.wikipedia.org/wiki/List_of_Xenosaga_media)
(this kit's discs, SLUS-20892 and SLUS-21133), and in Europe on
[October 28, 2005](https://en.wikipedia.org/wiki/List_of_Xenosaga_media).
It sits between Episode I (2002) and Episode III (2006), and the disc shows
it: half Episode I's machinery, half Episode III's.

## Dates on the disc

| Date | What | Where |
|---|---|---|
| 2001-2004 | the KORG sound driver's copyright, "test19" build | `IOP\KORGIVAG.IRX` |
| January 23, 2004 | CRI's middleware builds: ADX, Sofdec, the Pro Logic II encoder | `Build:Jan 23 2004 …` strings in the executable |
| SDK 2.7 | Sony's libraries (libkernl, libgraph, libcdvd, libpad … 2.7.x) | `PsIIlib…27x0` banners in the executable |
| "© 2001 2004 NAMCO LTD." | the title logo | `yajima\title.bin` |
| November 21 (year not given) | Yonesaka's check of the subtitle sample file | `movie\en\s010100.txt` |
| December 10, 2004 | Disc 1's file system written at 12:57, Disc 2's at 13:45 | the ISO 9660 volume descriptors |

The USA build has its own name in the developers' paths: `x2` is the game,
`x2u` the USA build (`host0:/home/xeno/vcd-f/x2u/movie/sfd.lst`).

## From Episode I to Episode III

| | Episode I (2002) | Episode II (2004-05) | Episode III (2006) |
|---|---|---|---|
| Table of contents | binary, plain names | the same, with front-coded names | Episode II's format, built by the same tool ("Episode.2" in its padding) |
| Compression | ARX | ARX (three files) | LZSS in model packages |
| Scene scripts | Java classes (JDK 1.1) | a stack machine of Monolith's own (`.sb`) | `.sb` files with a different layout (not decoded yet) |
| Field music | Procyon SMD/SWD sequences | Standard MIDI files on a KORG driver | DTPK banks |
| Models | `.lex` + `.xtx` | loose `.pxy` + `.txy` + `.xhr` + `.epf` | the same four, inside packages |
| Movies | PSS (MPEG-2) | Sofdec, 512 × 448, ADX + AC3 5.1 | Sofdec, 512 × 320, ADX |
| Sony SDK | 2.4 | 2.7 | 3.0 |

## Who

The asset folders and the scripts' source paths name the people who made
them: Shimajiri (effects), Yamamoto (battle), Iwase (quests and citizens),
Matsumoto (field objects), Yoshikawa (menus), Yajima (title and ending
screens), Omiya; and the scene writers Nakahara, Koji, Fuji, Yonesaka and
Kuramoto. [FINDS.md](FINDS.md) has the counts. Names are as the folders
spell them (`simajiri`, `matumoto`, `yosikawa`); the scripts give only
surnames or given names, so nothing here identifies a person beyond that.
