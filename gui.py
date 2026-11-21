"""gui.py — Local web GUI for the Xenosaga Episode II Python Extractor.

Run me directly:
    python gui.py                 # opens your browser on a free localhost port
    python gui.py --no-browser    # just print the URL
    python gui.py --self-test

Stdlib only. The same design as the Episode I kit's GUI: a small HTTP server
on 127.0.0.1, a page with one card per command, and every command run as a
``cli.py`` subprocess whose output streams back live (Server-Sent Events).
The cards sit in sections by goal (Start here first, developer tools last),
each with a plain title, a one-line summary, what it needs and what it makes;
after a run the card says where its output is and can show it in the file
manager.

Unlike the Episode I GUI, the cards are not written by hand. They are built
from ``cli.py``'s own argparse parser when the GUI starts: every subcommand
becomes a card, every option a field (a checkbox for on/off flags, a list box
for options that repeat, a picker for disc images and folders). A command
added to ``cli.py`` later shows up here with no change to this file, in an
"Other commands" section with its argparse help as the summary. The
hand-written text lives in tables: GROUPS (the sections), COMMAND_CARDS (each
card's title, summary, needs and makes), COMMAND_HELP (the "More detail"
text) and FIELD_HINTS (labels, help and pickers, keyed by "command.option"
first, then by option name, then falling back to argparse's own help).
"""
from __future__ import annotations

import argparse
import http.server
from html import escape as html_escape
import json
import os
import queue
import re
import shlex
import shutil
import socket
import socketserver
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
CLI = ROOT / "cli.py"
PY = sys.executable or "python3"
sys.path.insert(0, str(ROOT))      # the kit's modules, also under `python -I`

FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    _cli_name = "xeno-cli.exe" if os.name == "nt" else "xeno-cli"
    _cli_exe = Path(sys.executable).resolve().parent / _cli_name
    CLI_ARGV: List[str] = [str(_cli_exe)]
    CLI_TARGET = _cli_exe
    RUNTIME_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    CLI_ARGV = [PY, str(CLI)]
    CLI_TARGET = CLI
    RUNTIME_DIR = ROOT

TITLE = "Xenosaga II Extractor"


def _fatal(title: str, body: str) -> None:
    msg = f"{title}\n\n{body}"
    print(f"[gui] FATAL: {msg}", file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, body, f"{TITLE} — {title}", 0x10)
        except Exception:
            pass
    elif sys.platform == "darwin" and FROZEN:
        # A double-clicked .app has no console; show the error in a dialog.
        try:
            esc = body.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog "{esc}" with title "{TITLE} — {title}" '
                 'buttons {"OK"} default button 1 with icon stop'],
                timeout=120, check=False,
            )
        except Exception:
            pass
    sys.exit(2)


# ---------------------------------------------------------------------------
# The command list, read from cli.py's own parser
# ---------------------------------------------------------------------------

class _Captured(Exception):
    def __init__(self, parser):
        super().__init__("parser captured")
        self.parser = parser


def cli_parser(module=None) -> argparse.ArgumentParser:
    """cli.py's top-level parser, without running a command.

    ``cli.main`` builds its parser inline and calls ``parse_args`` once; for
    the length of one call, ``parse_args`` is replaced by a hook that raises
    with the parser in hand. If cli.py ever grows a ``build_parser()``, that
    is used instead.
    """
    if module is None:
        import cli as module  # noqa: F401  (also tells PyInstaller to bundle it)
    if hasattr(module, "build_parser"):
        return module.build_parser()
    original = argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        raise _Captured(self)

    argparse.ArgumentParser.parse_args = grab
    saved_argv = sys.argv
    try:
        try:
            module.main([])
        except TypeError:
            sys.argv = [str(CLI)]
            module.main()
    except _Captured as c:
        return c.parser
    finally:
        argparse.ArgumentParser.parse_args = original
        sys.argv = saved_argv
    raise RuntimeError("cli.main() returned without building a parser")


# ---------------------------------------------------------------------------
# Hand-written text. Everything here is optional: a command or option with no
# entry still gets a card or a field, built from its argparse help.
# ---------------------------------------------------------------------------

# The page's sections, in the order they are shown. A command the tables below
# do not know lands in the last one, so nothing cli.py adds is ever lost.
GROUPS = [
    ("start", "Start here",
     "Unpack your discs, then turn the game's files into pictures, 3D models, sound and text your "
     "computer can open. Steps 1 and 3 are all most people need; step 2 is an optional check."),
    ("look", "Look at one thing",
     "Read-only tools that go straight to the disc, no unpacking needed. Nothing is changed."),
    ("pictures", "Change pictures",
     "Each card writes a new copy of the disc image; your original is never changed. "
     "To combine edits, give one card's output disc as the next card's Disc image."),
    ("text", "Change text",
     "Export the text, edit it in any text editor, then put it back into a new copy of the disc. "
     "To combine edits, give one card's output disc as the next card's Disc image. "
     "Program text goes back last."),
    ("fonts", "Fonts and whole files",
     "Draw new letters into the game's fonts as pictures, or swap any file on the disc for your own."),
    ("alphabet", "Translate into a new alphabet",
     "For alphabets the game's font never had (Arabic, Hindi, Georgian...). First export and translate "
     "the text (Change text), then draw the letters, then prepare the text. Preview a line any time "
     "after the letters are drawn."),
    ("dev", "For developers",
     "Lists, script readers and checks for people studying how the game works."),
    ("other", "Other commands",
     "Commands added to the kit after this window's descriptions were written. Their text comes "
     "straight from the command line's own help."),
]
OTHER_GROUP = "other"

# One entry per command: section, step number (only in a real sequence), a
# plain title, a one-line summary for the closed card, what it needs, what it
# makes, what to do next, and where its output lands (an option name, and a
# subfolder of it), so the page can offer to show it. HTML is allowed.
COMMAND_CARDS = {
    "dump": {
        "group": "start", "step": 1, "title": "Unpack the discs",
        "summary": "Copies every game file off your disc images into a folder, unpacked, with a list of "
                   "them all. Start here.",
        "needs": "Your disc image(s) (.iso): Disc 1, plus Disc 2 to get everything.",
        "makes": "In the Output folder: <code>dump/</code> with one subfolder per data area (<code>chain0</code> "
                 "is on both discs, <code>chain1</code> is Disc 1's movies and voices, <code>chain2</code> "
                 "Disc 2's) and <code>manifest.csv</code>, the list of every file.",
        "next": "Next: step 3, <b>Convert to files you can open</b>, with the same folder.",
        "output": ("out", ""),
    },
    "verify": {
        "group": "start", "step": 2, "title": "Check the unpacked files",
        "summary": "Re-checks every unpacked file against the list made while unpacking, so you know "
                   "nothing was lost or damaged.",
        "needs": "The Output folder of step 1.",
        "makes": "Nothing on disk: a pass or fail count in the Output panel.",
        "next": "Next: step 3, <b>Convert to files you can open</b>.",
        "output": None,
    },
    "browse": {
        "group": "start", "step": 3, "title": "Convert to files you can open",
        "summary": "Turns the unpacked files into PNG pictures, 3D models for Blender (.glb), WAV sound, "
                   "MIDI and WAV music, readable text and scripts, and MP4 movies.",
        "needs": "The Output folder of step 1 (it reads the <code>dump</code> folder in it). Movies need "
                 "ffmpeg.",
        "makes": "A <code>browse/</code> folder inside it, one subfolder per kind: <code>textures_png</code>, "
                 "<code>xtx</code> (menu pictures), <code>models</code>, <code>audio</code>, "
                 "<code>music</code>, <code>movies</code>, <code>text</code>, ...",
        "next": "Open the browse folder and look around. To change a picture: copy the browse folder, paint "
                "over PNGs in the copy, then use <b>Put edited pictures back</b>.",
        "output": ("out", "browse"),
    },
    "model": {
        "group": "look", "title": "Export one 3D model for Blender",
        "summary": "Writes one character or map straight from the disc as a .glb file, with its textures, "
                   "skeleton and animations. (Step 3 does every model at once.)",
        "needs": "A disc image and the model's in-game path, e.g. <code>model\\char\\pc\\kosmos00.pxy</code>.",
        "makes": "One <code>.glb</code> file (Blender: File &gt; Import &gt; glTF).",
        "output": ("out", ""),
    },
    "dialogue": {
        "group": "look", "title": "Read the script in play order",
        "summary": "Every spoken and on-screen line in the order the game plays it, with speakers and "
                   "choices, plus the movie subtitles. Can also cut one voice clip per line.",
        "needs": "Your disc image(s): both discs for every scene. Voice clips need ffmpeg.",
        "makes": "A folder with one transcript per scene, subtitle files (<code>.srt</code>), "
                 "<code>dialogue.csv</code>, and WAV clips if <b>Voice clips</b> is ticked.",
        "output": ("out", ""),
    },
    "texture-import": {
        "group": "pictures", "title": "Put edited pictures back",
        "summary": "Paint over PNGs in a copy of the browse folder, then give that folder here. Only the "
                   "pictures you changed are written, into every copy of them on the disc.",
        "needs": "A disc image, and a copy of the browse folder (step 3, with pictures, menu pictures and "
                 "3D models ticked) holding your edited PNGs. Run it once per disc.",
        "makes": "A new disc image (.iso) and a CSV list of the changed pictures. Tick <b>Dry run</b> first "
                 "to see what would change without writing anything.",
        "next": "Test the new disc image in an emulator such as PCSX2. For more edits, give it as the next "
                "card's Disc image.",
        "output": ("out", ""),
    },
    "recolor": {
        "group": "pictures", "title": "Recolour textures (quick)",
        "summary": "Shifts the colour of named textures everywhere they appear; by default it turns "
                   "KOS-MOS's hair pink. Colours only: it cannot redraw anything.",
        "needs": "A disc image. Tick <b>Dry run</b> and give a Preview folder to look before writing.",
        "makes": "A new disc image (.iso); with a Preview folder, before/after PNGs.",
        "next": "Test the new disc image in an emulator such as PCSX2.",
        "output": ("out", ""),
    },
    "text-export": {
        "group": "text", "title": "Export the game text to edit",
        "summary": "Writes the dialogue, menus, battle messages, quest text, subtitles and save title as "
                   "plain text files you can edit in any text editor.",
        "needs": "A disc image.",
        "makes": "A text folder, one UTF-8 file per text object. Use an empty folder of its own.",
        "next": "To find a line, search this folder for a few of its words (most text editors have "
                "<em>Find in Files</em>) and edit it in place (lines starting with # are notes), then use <b>Put edited text back</b> with the same disc and "
                "this folder.",
        "output": ("out", ""),
    },
    "text-import": {
        "group": "text", "title": "Put edited text back",
        "summary": "Writes your edited text folder into a new copy of the disc. A line too long for its "
                   "space is refused and nothing is written.",
        "needs": "The same disc image you exported from, and the folder <b>Export the game text to edit</b> made, "
                 "after your edits.",
        "makes": "A new disc image (.iso). The original is not changed.",
        "next": "Test the new disc image in an emulator such as PCSX2. For more edits, give it as the next "
                "card's Disc image.",
        "output": ("out", ""),
    },
    "exe-export": {
        "group": "text", "title": "Export the text inside the game program",
        "summary": "The few messages stored in the game's program files (Saving..., memory-card prompts, "
                   "Total Exp) as editable text, each with its size limit.",
        "needs": "A disc image (USA).",
        "makes": "A text folder, one block per message with its byte limit.",
        "next": "Edit the messages (each within its limit), then <b>Put program text back</b>, after every "
                "other edit.",
        "output": ("out", ""),
    },
    "exe-import": {
        "group": "text", "title": "Put program text back (do this last)",
        "summary": "Writes the edited program messages back in place, each within its limit. Do it last: "
                   "afterwards the text, font and alphabet cards refuse that disc image.",
        "needs": "Your latest modified disc image (e.g. the one <b>Put edited text back</b> made) and the "
                 "folder <b>Export the text inside the game program</b> made, after your edits.",
        "makes": "A new disc image (.iso).",
        "next": "Test the new disc image in an emulator such as PCSX2.",
        "output": ("out", ""),
    },
    "font-extract": {
        "group": "fonts", "title": "Export the fonts as pictures",
        "summary": "Saves the game's two fonts (font0 for movies and cutscenes, font1 for dialogue and "
                   "menus) as PNG sheets you can draw on, with each letter's spacing as a spreadsheet.",
        "needs": "A disc image.",
        "makes": "A folder with the two font files, their sheet PNGs, spacing CSVs and the "
                 "byte-to-letter table.",
        "next": "Draw on the sheet PNGs, then <b>Build a font from edited sheets</b> with this folder.",
        "output": ("out", ""),
    },
    "font-rebuild": {
        "group": "fonts", "title": "Build a font from edited sheets",
        "summary": "Turns your edited font sheets (and spacing) back into a font file the game reads.",
        "needs": "The folder <b>Export the fonts as pictures</b> made, with your edits, and the original "
                 "font file in it.",
        "makes": "One font file (e.g. <code>new_font0.us</code>).",
        "next": "Put it on a disc copy with <b>Replace a file on the disc</b>: "
                "<code>chain0:font0.us=</code> then this file.",
        "output": ("out", ""),
    },
    "patch": {
        "group": "fonts", "title": "Replace a file on the disc",
        "summary": "Puts files you supply in place of game files, in a new copy of the disc (e.g. a rebuilt "
                   "font).",
        "needs": "A disc image and one line per file: data area, in-game path, then your file.",
        "makes": "A new disc image (.iso).",
        "next": "Test the new disc image in an emulator such as PCSX2. For more edits, give it as the next "
                "card's Disc image.",
        "output": ("out", ""),
    },
    "lang-scan": {
        "group": "alphabet", "title": "Draw the new letters",
        "summary": "Reads your translated text and a TrueType font, and draws every letter the "
                   "translation needs into new game fonts. Once per alphabet.",
        "needs": "The USA disc image, your translated copy of the <b>Export the game text to edit</b> folder, and a "
                 ".ttf/.otf font with your alphabet.",
        "makes": "A folder with the new <code>font0.us</code> and <code>font1.us</code>, "
                 "<code>glyphset.json</code> (the map from letters to game codes) and a preview sheet.",
        "next": "Next: <b>Prepare the translated text</b> with the <code>glyphset.json</code> in this "
                "folder. <b>Preview a translated line</b> works now too.",
        "output": ("out", ""),
    },
    "lang-encode": {
        "group": "alphabet", "title": "Prepare the translated text",
        "summary": "Rewrites your translation with the new letters' codes, in the order the game draws "
                   "them, wrapped and checked against every length limit.",
        "needs": "Your translated text folder and the <code>glyphset.json</code> from <b>Draw the new "
                 "letters</b>.",
        "makes": "An encoded text folder, ready for <b>Put edited text back</b>.",
        "next": "Put the two new fonts on a disc copy with <b>Replace a file on the disc</b> "
                "(<code>chain0:font0.us=...</code> and <code>chain0:font1.us=...</code>), then <b>Put edited "
                "text back</b> on that copy with this folder.",
        "output": ("out", ""),
    },
    "lang-preview": {
        "group": "alphabet", "title": "Preview a translated line",
        "summary": "Draws one line with the new font the way the game would, so you can check it without "
                   "starting the game.",
        "needs": "The <code>glyphset.json</code> from <b>Draw the new letters</b> and a line of text.",
        "makes": "A PNG picture of the line.",
        "output": ("out", ""),
    },
    "list": {
        "group": "dev", "title": "List every file on the disc",
        "summary": "Prints the game's own index of its files: data area, position, size and in-game path. "
                   "Handy for finding paths for other cards.",
        "needs": "Your disc image(s).",
        "makes": "Nothing on disk: the list appears in the Output panel.",
        "output": None,
    },
    "sb": {
        "group": "dev", "title": "Read one scene script",
        "summary": "Shows one scene or map script (an .sb file) as readable instructions, each spoken line "
                   "beside the instruction that uses it.",
        "needs": "A disc image and the script's in-game path, e.g. <code>cf\\sc1010.sb</code>.",
        "makes": "Nothing on disk: the listing appears in the Output panel.",
        "output": None,
    },
    "natives": {
        "group": "dev", "title": "Survey the game functions scripts call",
        "summary": "Counts every built-in game function the scripts call (\"natives\", such as "
                   "bat.msgPut) and the kinds of values passed; can also save every script as a listing.",
        "needs": "Your disc image(s).",
        "makes": "A report in the Output panel; with a folder given, also <code>natives.tsv</code>, "
                 "<code>listings/</code> and <code>strings.tsv</code>.",
        "output": ("out", ""),
    },
    "text-coverage": {
        "group": "dev", "title": "Report where the game shows text",
        "summary": "Counts every place the game shows text and says what the kit can do with each: "
                   "editable text, program text, or words drawn into pictures and movies.",
        "needs": "Your disc image(s).",
        "makes": "A report in the Output panel, and a TSV file if you give one.",
        "output": ("tsv", ""),
    },
    "self-test": {
        "group": "dev", "title": "Check the kit works",
        "summary": "Runs the kit's own built-in checks. No disc needed.",
        "needs": "Nothing.",
        "makes": "Nothing on disk: a pass or fail report in the Output panel.",
        "output": None,
    },
}

# The longer explanation behind each card's "More detail". A command without
# an entry simply has no "More detail".
COMMAND_HELP = {
    "list": "Print every entry of the game's own index of files (its table of contents): data area, sector, "
            "size and in-game path. The game keeps its files in three data areas, called chains: chain 0 is "
            "the shared data (on both discs), chain 1 is Disc 1's movies and voices, chain 2 is Disc 2's.",
    "dump": "Unpack the disc images into <code>OUT/dump/chain0</code>, <code>chain1</code> and "
            "<code>chain2</code> (the three data areas), with <code>OUT/manifest.csv</code> listing every "
            "file (position, size, checksum, kind). Give both discs to get all three. Files packed with the "
            "game's own compression (ARX) are written unpacked.",
    "verify": "Re-checks every unpacked file's checksum against <code>manifest.csv</code>. Files that were "
              "compressed on the disc are compressed again with the kit's copy of the game's packer and "
              "compared byte for byte, so a clean run proves the unpacking works both ways.",
    "browse": "Convert the unpacked files into files your computer opens, under <code>OUT/browse/</code>: "
              "textures and menu pictures as PNG, every model as a <code>.glb</code> Blender opens "
              "(textured, with its skeleton), sound effects and voices as WAV, songs as MIDI + SoundFont "
              "and a WAV rendered the way the PS2 plays it, dialogue as UTF-8 text, scripts as readable "
              "listings, movies as MP4 (needs ffmpeg).",
    "model": "One model straight from the disc to a <code>.glb</code> for Blender: the mesh with the "
             "textures and skeleton of the same name, and its animation files (<code>.xap</code>) as "
             "animations, one action per clip at 30 frames per second. Step 3 does every model at once.",
    "sb": "Turns one scene or map script (<code>.sb</code>, the game's compiled script files) into a "
          "readable listing, with every call into the game program named and every spoken line shown beside "
          "the instruction that uses it.",
    "text-export": "Write the game's text to an editable UTF-8 folder, one file per text object: dialogue, "
                   "menus (item, skill and location names, help, the database, the Skill Tutorial), the battle "
                   "names and messages, the battle banners, the quest, citizen and treasure text, movie "
                   "subtitles and the save's title in the memory-card browser. Each file is named after the "
                   "game file it comes from, inside a <code>chain0</code>, <code>chain1</code> or "
                   "<code>chain2</code> folder (the data area), so the quickest way to find a line is to "
                   "search the whole folder for a few of its words. In most files each line of text sits under "
                   "its own <code>###</code> heading; lines starting with <code>#</code> are notes. "
                   "Text inside the game program is <em>exe-export</em>.",
    "text-import": "Writes the edited text folder into a <em>copy</em> of the disc. Each kind of text keeps "
                   "to the room the game allows for it; anything past a limit is refused and nothing is "
                   "written. More detail: dialogue and menus must fit their space, a longer battle string "
                   "moves the rest of its section down within the file, quest texts must fit their fields. "
                   "The memory-card slot number and the Skill Tutorial's commands are kept; a battle banner "
                   "edited in one copy but not the others gets a note. An unedited folder imports as no "
                   "change, and every write is read back to check it.",
    "dialogue": "Every spoken and displayed line in the order the game plays it: one transcript per scene, "
                "field and battle script, with speakers, choices, battle banners and branches; the movies' "
                "and real-time cutscenes' subtitles as SRT; and <code>dialogue.csv</code>. Each line keeps "
                "its number, so it maps back to the <em>text-export</em> block you would edit. "
                "<em>Voice clips</em> also cuts one WAV per timed line (ffmpeg). Read only: it changes "
                "nothing.",
    "font-extract": "The two fonts (<code>font0</code> for movies and cutscenes, <code>font1</code> for "
                    "dialogue and menus) as PNG sheets of 20x24-pixel cells, a CSV of each cell's left and "
                    "right spacing, and the table of which byte draws which cell. Draw new letters on the "
                    "sheets, then use <em>font-rebuild</em>.",
    "font-rebuild": "Build a font file from edited sheets and spacing, starting from the original font file. "
                    "A folder without any edited sheet or spacing is refused unless <em>Allow no changes</em> "
                    "is ticked. Put the new font on a copy of the disc with <em>patch</em> "
                    "(<code>chain0:font0.us=...</code>).",
    "natives": "Natives are the functions inside the game program that scripts call, such as "
               "<code>bat.msgPut</code>. This lists every one with how often it is called and the kinds of "
               "values passed. With a folder, it also writes a readable listing of every script.",
    "recolor": "Shifts the colour of named textures in every copy on a <em>copy</em> of the disc. Textures "
               "are found by their name inside the game's texture files (on their own and inside effect "
               "files); the default recolours KOS-MOS's hair. <em>One menu sheet instead</em> colourises one "
               "full-colour menu picture, such as the title menu words. Colours only: it cannot redraw words.",
    "exe-export": "Write the messages stored in the game program (the main program file, SLUS, and its two "
                  "add-on code files OV01.OVL and OV02.OVL: <em>Saving...</em>, the memory-card prompts, "
                  "<em>Total Exp</em>) to an editable folder, one <code>### @ADDRESS max N bytes</code> block "
                  "per message.",
    "exe-import": "Writes the edited messages back <em>in place</em> into a copy of the disc: each at most "
                  "its <code>max N</code> bytes, the rest of its space filled with zeros. Run it <em>last</em>: "
                  "afterwards the program is no longer the original, and text-import, font-extract and "
                  "lang-scan refuse the image. For another round, give the original disc as <em>Original "
                  "disc</em>.",
    "text-coverage": "Lists every place the game shows text and what the kit does with it, counted from the "
                     "disc: the text it edits, the messages in the game program, and the words drawn into "
                     "pictures and movies (those need picture editing). <em>Also check round trip</em> also "
                     "checks that unedited exports import as no change.",
    "patch": "Replace whole files in a copy of the disc. One replacement per line: the data area "
             "(<code>chain0</code> is on both discs, <code>chain1</code>/<code>chain2</code> are Disc 1's and "
             "Disc 2's movies and voices), a colon, the in-game path, <code>=</code>, and your file, e.g. "
             "<code>chain0:font0.us=/path/to/new_font0.us</code>.",
    "lang-scan": "For an alphabet the game never had: shapes the translated text with a TrueType font (joined "
                 "letters, right-to-left order), gives every letter a code the USA game can draw, and writes "
                 "<code>font0.us</code>, <code>font1.us</code>, <code>glyphset.json</code> and a preview "
                 "sheet. Run it once per alphabet; a second alphabet starts from the first one's files "
                 "(<em>Earlier ...</em> fields). See <code>docs/ALPHABETS.md</code>.",
    "lang-encode": "Turns the translated folder into the same folder with the new letters written as codes, "
                   "in the order the game draws them (right-to-left lines reordered), wrapped and checked "
                   "against every byte limit. Nothing is written if a letter or a limit is missing. Then put "
                   "the two fonts on a disc copy and run <code>text-import</code> on it.",
    "lang-preview": "Draws one line with the rebuilt font to a PNG, placing the cells the way the game's own "
                    "print function does, to check a translation without the game.",
    "texture-import": "Edit PNGs in a copy of the <code>browse</code> folder (textures, menu pictures, or the "
                      "images inside a model's <code>.glb</code>) and give that folder here. Only PNGs that "
                      "differ from the disc are written, into a <em>copy</em> of the disc, and every other copy "
                      "of the same picture on the disc is updated too. Shared colour tables (palettes) are kept "
                      "for the other textures that use them. Chain 0 (the shared data) is on both discs: run it "
                      "once per disc. <em>Dry run</em> shows what would change without writing.",
    "self-test": "Runs every module's built-in checks (no disc needed).",
}

# Words the cards use, explained once at the top of the page.
GLOSSARY = [
    ("Disc image", "a copy of a game disc as one <code>.iso</code> file"),
    ("Data area (chain)", "the game keeps its files in three areas: chain 0 is the shared data on both discs, "
                          "chain 1 is Disc 1's movies and voices, chain 2 is Disc 2's"),
    ("In-game path", "a file's name inside the game, e.g. <code>model\\char\\pc\\kosmos00.pxy</code>; "
                     "<b>List every file on the disc</b> shows them all"),
    ("ARX", "the game's own compression"),
    ("Index of files (TOC)", "the game's own table of contents of its files"),
    (".sb script", "a compiled scene or map script"),
    ("Natives", "functions inside the game program that scripts call"),
    ("Game program (SLUS, overlays)", "the main program file on the disc and its add-on code files"),
    (".glb", "a 3D model file Blender opens"),
    ("Menu pictures (xtx)", "full-colour pictures used by menus and the title screen"),
    ("glyphset.json", "the map from your alphabet's letters to the codes the game draws"),
]

# Field text keyed by option name, or by "command.option" for one command
# only (looked up first). Keys: label, help (shown under the field; replaces
# the argparse help), placeholder (an example), pick (file, dir, save), filter,
# suggest (the file name a Save-as picker adds), prefill (the starting value:
# {out} = the suggested output folder, {disc} = the first disc found, {discs} =
# every disc found, "" = start empty), choices (value -> plain text for a list
# box), tri (three plain choices for an on/off pair of flags), advanced (True:
# shown under the card's "More settings", for fields most people leave alone),
# required_unless (marks an optional option REQUIRED, except when one of these
# other fields of the card is filled in or ticked).
FIELD_HINTS = {
    "iso": {"label": "Disc image", "pick": "file", "filter": "iso",
            "help": "One disc image (.iso): Disc 1 or Disc 2.",
            "help_multi": "Your disc images, one per line: Disc 1 and Disc 2 to cover everything."},
    "out": {"label": "Output folder", "pick": "dir"},
    "text": {"label": "Text folder", "pick": "dir"},
    "retail": {"label": "Original disc", "pick": "file", "filter": "iso",
               "help": "Only for a second round: the original disc image, whose program files are used as the "
                       "starting point."},
    "dump": {"label": "Unpacked folder", "pick": "dir"},
    "path": {"label": "In-game path"},
    "glob": {"label": "Only matching paths", "placeholder": "e.g. model\\char\\pc\\*",
             "help": "Optional: only in-game paths matching this pattern (* matches anything)."},
    "limit": {"label": "At most (per kind)", "placeholder": "e.g. 20",
              "help": "Optional: convert only this many files of each kind, for a quick sample."},
    "chain": {"label": "Data area",
              "help": "The game keeps its files in three areas (chains). Leave on All unless you want one.",
              "choices": [["", "All areas"], ["0", "0: shared data (on both discs)"],
                          ["1", "1: Disc 1 movies and voices"], ["2", "2: Disc 2 movies and voices"]]},
    "font": {"label": "TrueType font", "pick": "file"},
    "glyphset": {"label": "Letter map (glyphset.json)", "pick": "file", "filter": "json",
                 "prefill": "{out}/lang/glyphset.json",
                 "help": "The glyphset.json that <b>Draw the new letters</b> wrote."},
    "dry_run": {"label": "Dry run"},
    "preview": {"label": "Preview folder", "pick": "dir"},
    "grow": {"label": "Allow moving"},
    "space_px": {"label": "Space width (pixels)"},
    "width": {"label": "Wrap width (pixels)",
              "help": "Wrap lines at this many pixels for every kind of text; 0 = do not wrap "
                      "(subtitles wrap at 440)."},
    "align": {"label": "Line alignment", "help": "Right alignment suits right-to-left alphabets.",
              "choices": {"left": "left", "right": "right (pads right-to-left lines of dialogue and menus)"}},
    "rtl": {"label": "Right-to-left order",
            "help": "Right-to-left alphabets are stored in reading order; the game draws left to right, so "
                    "their lines are reordered.",
            "tri": ["Automatic: reorder when the alphabet is right to left",
                    "Always reorder right-to-left paragraphs", "Never reorder"]},

    "dump.out": {"label": "Output folder", "prefill": "{out}",
                 "help": "Where the unpacked files go. Keep it: steps 2 and 3 read this folder."},
    "dump.raw": {"label": "Keep compressed",
                 "help": "Leave files the game keeps compressed (ARX) as they are instead of unpacking them. "
                         "Most people leave this off."},
    "dump.code": {"label": "Program files too",
                  "help": "Also copy the game's program files: the main program (SLUS), its add-on code files "
                          "(overlays) and the sound-chip modules (IOP). The music modules that step 3 needs are "
                          "always copied."},
    "verify.out": {"label": "Unpacked folder", "prefill": "{out}",
                   "help": "The Output folder you gave <b>Unpack the discs</b>."},
    "browse.out": {"label": "Unpacked folder", "prefill": "{out}",
                   "help": "The Output folder you gave <b>Unpack the discs</b>. The converted files go into "
                           "its browse subfolder."},
    "browse.kinds": {"label": "What to convert",
                     "help": "Untick what you do not need. Movies need ffmpeg; music WAVs need numpy.",
                     "choices": {"textures": "pictures (textures)", "models": "3D models",
                                 "xtx": "menu pictures (xtx)", "fonts": "fonts", "text": "text",
                                 "scripts": "scripts", "audio": "sound effects and voices", "music": "music",
                                 "images": "photos (JPEG)", "movies": "movies",
                                 "small": "small files (logos, save icon, packs)"}},
    "browse.movie_audio": {"label": "Movie sound", "help": "Which sound track the MP4 movies get.",
                           "choices": {"adx": "the stereo track (ADX, the movies' own sound format), "
                                              "converted to AAC",
                                       "ac3": "the 5.1 surround track (AC-3), copied as is",
                                       "both": "both tracks"}},
    "model.path": {"label": "Model's in-game path", "placeholder": "e.g. model\\char\\pc\\kosmos00.pxy",
                   "help": "Where the model sits on the disc. Find paths with <b>List every file on the disc</b> "
                           "or in browse/models."},
    "model.out": {"label": "Save as (.glb)", "pick": "save", "filter": "glb", "suggest": "model.glb",
                  "prefill": "{out}/model.glb", "help": "The .glb file to write."},
    "model.motion": {"label": "Extra animation files", "placeholder": "e.g. motion\\shion00.xap",
                     "help": "Optional: animation files on the disc (.xap or .fap) to add, one per line; add "
                             "#0,1 to pick clips. Empty = the ones with the model's name."},
    "model.slots": {"label": "Only these clips", "placeholder": "e.g. 0,1,2",
                    "help": "Optional: clip numbers to keep from each animation file. Empty = every clip."},
    "model.face": {"label": "Face animations",
                   "help": "Also add the facial expression clips (motion\\face_all.fap) that fit this skeleton."},
    "model.no_motion": {"label": "No animations", "help": "Mesh and skeleton only."},
    "model.matched_only": {"label": "Only clips that fit",
                           "help": "Leave out clips made for a different number of bones. By default they are "
                                   "written, with the counts in the animation's name."},
    "sb.path": {"label": "Script's in-game path", "placeholder": "e.g. cf\\sc1010.sb",
                "help": "A scene or map script (.sb). Find paths with <b>List every file on the disc</b>."},
    "natives.glob": {"label": "Only matching scripts", "placeholder": "e.g. yamamoto\\think\\*",
                     "help": "Optional: only scripts whose in-game path matches this pattern (* matches anything)."},
    "natives.out": {"label": "Save listings to", "prefill": "",
                    "help": "Optional folder: also writes natives.tsv, one readable listing per script "
                            "(listings/) and strings.tsv."},
    "recolor.out": {"label": "Output disc", "suggest": "modded-recolor.iso", "prefill": "{out}/modded-recolor.iso",
                    "help": "The new disc image to write. Empty = nothing is written (tick Dry run to only "
                            "look). Your original disc is never changed."},
    "recolor.preset": {"label": "Preset", "help": "A ready-made set of textures to recolour.", "choices": {"": "KOS-MOS's hair (when no rules are given)",
                                                      "kosmos-hair": "KOS-MOS's hair"}},
    "recolor.rule": {"label": "Rules", "placeholder": "e.g. kosmos*_hair_col=all",
                     "help": "Optional: which textures to recolour, one per line: a texture-name pattern, then "
                             "=all (every colour) or =blue (only the blue tones). Rules without a preset replace "
                             "the KOS-MOS preset."},
    "recolor.hue": {"label": "New colour (hue)",
                    "help": "0 to 1 around the colour wheel: 0 red, 0.33 green, 0.66 blue, 0.92 pink."},
    "recolor.preview": {"help": "Optional: writes before/after PNGs of every changed texture here."},
    "recolor.dry_run": {"help": "Show what would change; write no disc."},
    "recolor.xtx": {"label": "One menu picture instead", "placeholder": "e.g. yajima\\titleobj.xtx",
                    "help": "Optional: colourise the full-colour menu picture (.xtx) at this in-game path instead "
                            "of the named textures. Needs an Output disc."},
    "texture-import.out": {"label": "Output disc", "suggest": "modded-textures.iso",
                           "prefill": "{out}/modded-textures.iso",
                           "required_unless": ["dry_run", "verify_roundtrip"],
                           "help": "The new disc image to write. Can stay empty only when Dry run or Self-check only "
                                   "is ticked."},
    "texture-import.tree": {"label": "Edited browse folder", "pick": "dir", "prefill": "",
                            "required_unless": ["textures", "xtx", "models", "verify_roundtrip"],
                            "placeholder": "a copy of your browse folder, with edited PNGs",
                            "help": "Run step 3 (pictures, menu pictures, 3D models), copy its browse folder, "
                                    "paint over PNGs in the copy and give the copy here. Only pictures that "
                                    "differ from the disc are written. (Under More settings you can give "
                                    "single folders instead.)"},
    "texture-import.textures": {"label": "Pictures folder", "pick": "dir", "advanced": True,
                                "help": "Use this textures_png folder instead of the one in the edited browse folder."},
    "texture-import.xtx": {"label": "Menu pictures folder", "pick": "dir", "advanced": True,
                           "help": "Use this xtx folder instead of the one in the edited browse folder."},
    "texture-import.models": {"label": "Models folder", "pick": "dir", "advanced": True,
                              "help": "Use this folder of .glb models (their images) instead of the one in the "
                                      "edited browse folder."},
    "texture-import.palette": {"label": "Shared colours", "advanced": True,
                               "help": "Many textures share one colour table (palette).",
                               "choices": {"auto": "keep the colours of the other textures that share it",
                                           "keep": "never change a colour table",
                                           "joint": "recompute the table for every texture that shares it"}},
    "texture-import.dither": {"label": "Dithering", "advanced": True,
                              "help": "How colours are reduced when a texture has only a few to choose from.",
                              "choices": {"none": "none", "fs": "Floyd-Steinberg (mixes pixels to fake more colours)"}},
    "texture-import.sweep": {"label": "Other copies", "advanced": True,
                             "help": "The disc holds several copies of many pictures.",
                             "choices": {"pixels": "update every copy with the same pixels",
                                         "name": "only copies with the same texture name",
                                         "none": "only the picture you edited"}},
    "texture-import.mips": {"label": "Smaller versions", "advanced": True,
                            "help": "Textures carry smaller copies of themselves (mip levels) for distant objects.",
                            "choices": {"rebuild": "rebuild them from your edit", "keep": "leave them as they are"}},
    "texture-import.grow": {"advanced": True,
                            "help": "Only if a run says an edited file no longer fits its space: let that file "
                                    "move to the end of the disc image. The kit checks the moved file, but such "
                                    "a disc has not yet been tried in the game itself."},
    "texture-import.dry_run": {"help": "List what would change and write nothing. A safe first try."},
    "texture-import.preview": {"help": "Optional: writes before/after PNGs of every changed picture here."},
    "texture-import.report": {"label": "Change list", "pick": "save", "filter": "csv", "advanced": True,
                              "suggest": "textures.csv", "prefill": "",
                              "help": "Where to save the CSV of changed pictures. Empty = next to the output disc."},
    "texture-import.verify_roundtrip": {"label": "Self-check only", "advanced": True,
                                        "help": "Write nothing: re-encode every texture on the disc from its own "
                                                "picture and check the bytes come back identical. This checks "
                                                "the kit, not your edits."},
    "text-export.out": {"label": "Text folder to create", "prefill": "{out}/text",
                        "help": "An empty folder of its own; one text file per text object is written here."},
    "dialogue.out": {"prefill": "{out}/dialogue", "help": "Where the transcripts and subtitles go."},
    "dialogue.voice": {"label": "Voice clips",
                       "help": "Also cut one WAV per timed line from the movies and cutscene voice tracks "
                               "(needs ffmpeg)."},
    "dialogue.only": {"label": "Only scenes", "placeholder": "e.g. 1001",
                      "help": "Optional: only scenes or movies whose name contains this, one per line."},
    "text-import.iso": {"help": "The same disc image you exported the text from."},
    "text-import.text": {"label": "Edited text folder", "prefill": "{out}/text",
                         "help": "The folder <b>Export the game text to edit</b> made, after your edits."},
    "text-import.out": {"suggest": "modded-text.iso", "prefill": "{out}/modded-text.iso",
                        "help": "The new disc image to write. Your original disc is never changed."},
    "text-import.grow": {"help": "Let a text file that outgrows its space move to the end of the disc image, "
                                 "within the room the game's loader has (read from the code; not yet seen in the "
                                 "running game)."},
    "text-import.rebase": {"label": "Apply to another disc",
                           "help": "Apply the text even though this is not the disc it was exported from (another "
                                   "revision, or an already modified copy). The check against the export is "
                                   "skipped."},
    "exe-export.out": {"label": "Text folder to create", "prefill": "{out}/exe",
                       "help": "An empty folder of its own; the editable messages are written here."},
    "exe-import.iso": {"prefill": "", "placeholder": "your latest modified disc image, or the original",
                       "help": "Start from your latest modified disc image, so your earlier edits are kept."},
    "exe-import.text": {"label": "Edited program-text folder", "prefill": "{out}/exe",
                        "help": "The folder <b>Export the text inside the game program</b> made, after your edits."},
    "exe-import.out": {"suggest": "modded-exe.iso", "prefill": "{out}/modded-exe.iso",
                       "help": "The new disc image to write."},
    "text-coverage.verify": {"label": "Also check round trip",
                             "help": "Also export everything and check that unedited exports import as no change "
                                     "(slower)."},
    "text-coverage.tsv": {"label": "Save report as", "pick": "save", "filter": "tsv",
                          "suggest": "coverage.tsv", "prefill": "", "help": "Optional: also save the report as a TSV file."},
    "text-coverage.tex_words": {"label": "Words in pictures",
                                "help": "Also list textures with words a player reads (they need picture editing)."},
    "font-extract.out": {"label": "Folder to create", "prefill": "{out}/fonts",
                         "help": "Where the font files, sheet PNGs and spacing CSVs go."},
    "font-rebuild.ref": {"label": "Original font file", "pick": "file", "prefill": "{out}/fonts/font0.us",
                         "help": "The font file <b>Export the fonts as pictures</b> saved (font0.us or font1.us); "
                                 "the new font starts from it."},
    "font-rebuild.sheets": {"label": "Edited sheets folder", "pick": "dir", "prefill": "{out}/fonts",
                            "help": "The folder with your edited sheet PNGs (font0_sheet0.png to _sheet3.png)."},
    "font-rebuild.metrics": {"label": "Edited spacing", "pick": "file", "filter": "csv",
                             "help": "Optional: the edited spacing CSV, e.g. font0_metrics.csv."},
    "font-rebuild.allow_unchanged": {"label": "Allow no changes",
                                     "help": "Write a plain copy of the original when no edited sheet or spacing "
                                             "is found (otherwise refused)."},
    "font-rebuild.out": {"label": "Save as", "pick": "save", "suggest": "new_font0.us",
                         "prefill": "{out}/new_font0.us", "help": "The new font file to write."},
    "patch.out": {"suggest": "modded-patch.iso", "prefill": "{out}/modded-patch.iso",
                  "help": "The new disc image to write."},
    "patch.set": {"label": "Replacements", "placeholder": "chain0:font0.us=/path/to/new_font0.us",
                  "help": "One per line: the data area (chain0 = shared data on both discs; chain1, chain2 = "
                          "Disc 1's, Disc 2's movies and voices), a colon, the in-game path, =, then your file."},
    "patch.grow": {"help": "Let a file bigger than its space move to the end of the disc image."},
    "patch.relocate": {"advanced": True, "label": "Always move (test)",
                       "help": "Put every replacement at the end of the image even if it fits (to test moving)."},
    "lang-scan.iso": {"help": "The USA disc image the translation is for."},
    "lang-scan.text": {"label": "Translated text folder", "prefill": "",
                       "placeholder": "your translated copy of the text folder",
                       "help": "Your translated copy of the <b>Export the game text to edit</b> folder (one per line for "
                               "more than one)."},
    "lang-scan.font": {"filter": "", "help": "A .ttf, .otf or .ttc font that has your alphabet; the letters are "
                                             "drawn from it."},
    "lang-scan.face": {"advanced": True, "label": "Font number in a .ttc",
                       "help": "Only for .ttc collections: which font inside it (0 = the first)."},
    "lang-scan.px": {"label": "Letter size (pixels)",
                     "help": "Font size in pixels; 18 puts Latin capitals where the game's own are."},
    "lang-scan.baseline": {"advanced": True, "label": "Baseline row",
                           "help": "The pixel row the letters stand on in the 24-pixel-high cell."},
    "lang-scan.script": {"label": "Alphabet code", "placeholder": "e.g. Arab, Deva, Geor",
                         "help": "Optional: a four-letter ISO 15924 alphabet code; only words in this alphabet "
                                 "are scanned."},
    "lang-scan.language": {"label": "Language code", "placeholder": "e.g. ar, hi, ka",
                           "help": "Optional: a language code (BCP-47), so letters join the way that language "
                                   "writes them."},
    "lang-scan.rtl": {"label": "Right to left", "help": "The alphabet is written right to left (Arabic, Hebrew...)."},
    "lang-scan.cells": {"advanced": True, "label": "Hand-drawn letters (PNG)", "pick": "file", "filter": "png",
                        "help": "Optional, instead of a font: a PNG sheet of hand-drawn 20x24-pixel letter cells."},
    "lang-scan.keys": {"advanced": True, "label": "Letters in that sheet", "pick": "file",
                       "help": "With hand-drawn letters: a text file naming the letter in each cell, one per line."},
    "lang-scan.prefer": {"advanced": True, "label": "Codes to fill first",
                         "help": "Text with tight byte limits takes one-byte codes first anyway.",
                         "choices": {"single": "one-byte codes", "pair": "two-byte codes",
                                     "fixed": "fixed-width two-byte cells (for wide letters)"}},
    "lang-scan.keep": {"advanced": True, "label": "Letters to keep", "placeholder": "e.g. éèàç",
                       "help": "Optional: accented Latin letters whose cells must stay as they are."},
    "lang-scan.reclaim": {"advanced": True, "label": "Free reserved codes", "placeholder": "e.g. FC",
                          "help": "Optional: reserved one-byte codes to free up, in hex, comma-separated."},
    "lang-scan.prop_only": {"advanced": True, "label": "Narrow cells only",
                            "help": "Refuse letters too wide for the narrow (proportional) cells."},
    "lang-scan.tight": {"advanced": True, "label": "Touching letters",
                        "help": "Draw ink to the cell edge so joining letters touch (worked out from the code, not "
                                "yet seen in the game)."},
    "lang-scan.space_px": {"advanced": True, "help": "Width of a space, for the line-width report."},
    "lang-scan.font0": {"advanced": True, "label": "Earlier font0.us", "pick": "file",
                        "help": "For a second alphabet: the font0.us an earlier scan wrote (give font1.us too)."},
    "lang-scan.font1": {"advanced": True, "label": "Earlier font1.us", "pick": "file",
                        "help": "For a second alphabet: the font1.us an earlier scan wrote (give font0.us too)."},
    "lang-scan.extend": {"advanced": True, "label": "Earlier glyphset.json", "pick": "file", "filter": "json",
                         "help": "For a second alphabet: the glyphset.json an earlier scan wrote; its codes are kept."},
    "lang-scan.out": {"label": "Folder to create", "prefill": "{out}/lang",
                      "help": "Where the new fonts, glyphset.json and the preview sheet go."},
    "lang-encode.text": {"label": "Translated text folder", "prefill": "", "pick": "dir",
                         "placeholder": "your translated copy of the text folder",
                         "help": "Your translated copy of the <b>Export the game text to edit</b> folder."},
    "lang-encode.out": {"label": "Folder to create", "prefill": "{out}/text-encoded",
                        "help": "The encoded text folder; <b>Put edited text back</b> reads it."},
    "lang-encode.space_px": {"help": "Width of a space; the game's is 9."},
    "lang-encode.width_kind": {"label": "Wrap width per kind", "placeholder": "e.g. subtitle=440",
                               "help": "Optional, one per line: a kind of text (bank = dialogue, menu, subtitle, "
                                       "battle, exe = program text), =, then a width in pixels."},
    "lang-encode.iso": {"label": "Disc copy with new fonts, to check", "prefill": "",
                        "help": "Optional: a disc copy with the two new fonts already put in. The encoded text is "
                                "then test-imported into it (nothing written) to catch problems early."},
    "lang-preview.line": {"label": "Line of text", "placeholder": "the line as you would write it in the text file",
                          "help": "Written the way you would type it in the translated text file."},
    "lang-preview.out": {"label": "Save as (PNG)", "pick": "save", "filter": "png", "suggest": "line.png",
                         "prefill": "{out}/lang/line.png",
                         "help": "The PNG to write (several lines: NAME_0.png, NAME_1.png, ...)."},
    "lang-preview.space_px": {"help": "Width of a space; the game's is 9."},
    "lang-preview.scale": {"label": "Zoom", "help": "Enlarge the picture this many times."},
    "lang-preview.face": {"label": "Which font", "help": "The game draws with two fonts.",
                          "choices": {"font1": "font1: dialogue, menus and battle",
                                      "font0": "font0: movies and real-time cutscenes"}},
    "lang-preview.kind": {"label": "Kind of text",
                          "help": "Where the line would appear; subtitles wrap at 440 pixels by default.",
                          "choices": {"bank": "dialogue", "menu": "menu", "subtitle": "movie subtitle",
                                      "battle": "battle", "exe": "program text"}},
}


def field_hint(command: str, dest: str) -> dict:
    """The hint for one option of one command: "command.option" over "option"."""
    hint = dict(FIELD_HINTS.get(dest, {}))
    hint.update(FIELD_HINTS.get(f"{command}.{dest}", {}))
    return hint


def card_info(command: str, short: str) -> dict:
    """A command's card text, with the fallback for a command the table lacks."""
    info = COMMAND_CARDS.get(command)
    if info is None:
        return {"group": OTHER_GROUP, "step": None, "title": command, "summary": short or command,
                "needs": "", "makes": "", "next": "", "output": ("out", "")}
    return {"group": info.get("group", OTHER_GROUP), "step": info.get("step"),
            "title": info.get("title") or command, "summary": info.get("summary") or short or command,
            "needs": info.get("needs", ""), "makes": info.get("makes", ""), "next": info.get("next", ""),
            "output": info.get("output", ("out", ""))}


# Commands whose --out is a disc image they write, not a folder.
_WRITES_ISO = re.compile(r"\b(copy of the disc|disc copy|modded|\.iso)\b", re.I)


def _help_of(sub_action, name) -> str:
    for ca in getattr(sub_action, "_choices_actions", []):
        if ca.dest == name or getattr(ca, "metavar", None) == name:
            return ca.help or ""
    return ""


def _kinds_choices() -> Optional[List[str]]:
    try:
        from browse import ALL_KINDS
        return list(ALL_KINDS)
    except Exception:
        return None


def _field(name: str, a: argparse.Action, writes_iso: bool) -> dict:
    """One option of one command as a form field, hints applied."""
    flag = next((s for s in a.option_strings if s.startswith("--")),
                a.option_strings[0] if a.option_strings else None)
    if isinstance(a, argparse._StoreTrueAction):
        kind = "flag"
    elif isinstance(a, argparse._StoreFalseAction):
        kind = "flag-off"
    elif isinstance(a, argparse._AppendAction) or a.nargs in ("+", "*"):
        kind = "multi"
    else:
        kind = "value"
    hint = field_hint(name, a.dest)
    help_text = hint.get("help_multi") if kind == "multi" and hint.get("help_multi") else hint.get("help")
    f = {
        "dest": a.dest,
        "flag": flag,
        "positional": not a.option_strings,
        "kind": kind,
        "required": bool(a.required) if a.option_strings else (a.nargs not in ("?", "*")),
        "help": help_text or html_escape(a.help or ""),
        "default": None if a.default is None or a.default is False or a.default is argparse.SUPPRESS
        else a.default,
        "type": getattr(a.type, "__name__", None) if a.type else None,
        "choices": [str(c) for c in a.choices] if a.choices else None,
        "label": hint.get("label") or a.dest.replace("_", " ").capitalize(),
        "placeholder": hint.get("placeholder", ""),
        "advanced": bool(hint.get("advanced")),
    }
    if hint.get("required_unless"):
        # Needed for a real run, but the command accepts it missing when one
        # of these other fields is set (a dry run, a self-check, an override).
        f["required"] = True
        f["required_unless"] = list(hint["required_unless"])
    labels = hint.get("choices")
    if a.dest == "kinds" and name == "browse":
        f["kind"] = "kinds"
        f["choices"] = _kinds_choices()
        if not f["choices"]:
            f["kind"] = "value"
        elif isinstance(labels, dict):
            f["choice_labels"] = labels
    elif isinstance(labels, list) and not f["choices"]:
        f["select"] = labels                      # a fixed list of values for a free field
    elif isinstance(labels, dict) and f["choices"]:
        f["choice_labels"] = labels
    pick = hint.get("pick")
    suggest = hint.get("suggest")
    if a.dest == "out" and writes_iso:
        # The shared "out" hint says "Output folder"; a disc-writing card's
        # --out is a file, so only its own command.out label may replace this.
        pick = "save"
        f["label"] = FIELD_HINTS.get(f"{name}.out", {}).get("label") or "Output disc"
        suggest = suggest or "modded.iso"
        f["filter"] = "iso"
    elif a.dest == "out" and re.search(r"\bPNG to write\b", a.help or "") and not pick:
        pick = "save"
        f["label"] = hint.get("label") or "Output PNG"
        suggest = suggest or "line.png"
    if pick:
        f["pick"] = pick
        if hint.get("filter") and "filter" not in f:
            f["filter"] = hint["filter"]
        if suggest:
            f["suggest"] = suggest
    elif kind in ("value", "multi") and not f.get("select") and not f["choices"] and \
            re.search(r"\b(file|png|wav|sf2|json|csv|folder|dir)\b", a.help or "", re.I):
        f["pick"] = "any"
    # The starting value: a hint's prefill, else the argparse default, else (for a
    # required disc image or output only) a suggestion; optional fields start empty.
    if "prefill" in hint:
        f["prefill"] = hint["prefill"]
    elif f["default"] is not None and kind == "value":
        f["prefill"] = str(f["default"])
    elif f["required"] and a.dest == "iso":
        f["prefill"] = "{discs}" if kind == "multi" else "{disc}"
    elif f["required"] and a.dest == "out":
        f["prefill"] = "{out}/" + suggest if pick == "save" and suggest else "{out}"
    else:
        f["prefill"] = ""
    return f


def _merge_flag_pairs(name: str, fields: List[dict]) -> List[dict]:
    """An on flag and an off flag that write one option (--rtl / --ltr) become a
    single three-way choice: leave it to the command, on, or off. Two checkboxes
    would share one form name, and an unticked off-flag would always be sent."""
    out: List[dict] = []
    for f in fields:
        if f["kind"] in ("flag", "flag-off"):
            other = next((g for g in out if g["dest"] == f["dest"] and g["kind"] in ("flag", "flag-off")
                          and g["kind"] != f["kind"]), None)
            if other is not None:
                on, off = (other, f) if other["kind"] == "flag" else (f, other)
                tri = field_hint(name, f["dest"]).get("tri") or [
                    "Default", f"{on['flag']}: {on['help']}", f"{off['flag']}: {off['help']}"]
                other.update({"kind": "tristate", "flag_on": on["flag"], "flag_off": off["flag"],
                              "flag": None, "select": [["", tri[0]], ["on", tri[1]], ["off", tri[2]]],
                              "help": field_hint(name, f["dest"]).get("help") or ""})
                continue
        out.append(f)
    return out


def command_schema(parser: Optional[argparse.ArgumentParser] = None) -> List[dict]:
    """One dict per subcommand: name, card text, aliases and its fields."""
    parser = parser or cli_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    commands: List[dict] = []
    by_signature: Dict[tuple, dict] = {}
    for name, p in sub.choices.items():
        short = _help_of(sub, name)
        writes_iso = bool(_WRITES_ISO.search(short)) or name in ("patch", "text-import", "exe-import")
        fields = [_field(name, a, writes_iso) for a in p._actions if not isinstance(a, argparse._HelpAction)]
        fields = _merge_flag_pairs(name, fields)
        signature = (short, tuple((f["dest"], f["kind"]) for f in fields))
        if short and signature in by_signature:        # dump / extract: one card, two names
            by_signature[signature]["aliases"].append(name)
            continue
        info = card_info(name, short)
        out_field, out_sub = info["output"] or (None, "")
        cmd = {"name": name, "short": short, "help": COMMAND_HELP.get(name, ""),
               "aliases": [], "fields": fields, "writes_iso": writes_iso,
               "group": info["group"], "step": info["step"], "title": info["title"],
               "summary": info["summary"] if name in COMMAND_CARDS else html_escape(info["summary"]),
               "needs": info["needs"], "makes": info["makes"], "next": info["next"],
               "output_field": out_field if any(f["dest"] == out_field for f in fields) else None,
               "output_sub": out_sub}
        by_signature[signature] = cmd
        commands.append(cmd)
    return commands


def grouped(commands: List[dict]) -> List[dict]:
    """The page's sections in GROUPS order, each with its commands: table order
    for the known ones, parser order for the rest. Empty sections are left out."""
    order = {n: i for i, n in enumerate(COMMAND_CARDS)}
    known = {g for g, _, _ in GROUPS}
    sections = []
    for gid, title, blurb in GROUPS:
        members = [c for c in commands if (c["group"] if c["group"] in known else OTHER_GROUP) == gid]
        members.sort(key=lambda c: order.get(c["name"], len(order)))
        if members:
            sections.append({"id": gid, "title": title, "blurb": blurb, "commands": [c["name"] for c in members]})
    return sections


def _truthy(v: str) -> bool:
    return v.lower() in ("1", "on", "true", "yes")


def build_argv(cmd: dict, form: dict) -> List[str]:
    """Turn a submitted card into the argv the CLI gets. Raises ValueError
    with a readable message when something required is missing."""
    def val(key):
        v = form.get(key)
        if isinstance(v, list):
            v = v[0] if v else ""
        return (v or "").strip()

    by_dest = {f["dest"]: f for f in cmd["fields"]}

    def excused(f) -> bool:
        """A required_unless field may be empty when one of its listed fields is set."""
        for d in f.get("required_unless") or ():
            g = by_dest.get(d)
            if g is None:
                continue
            if g["kind"] in ("flag", "flag-off"):
                if _truthy(val(d)) != (g["kind"] == "flag-off"):
                    return True
            elif val(d):
                return True
        return False

    def missing(f) -> str:
        if not f.get("required_unless"):
            return f"{f['label']} is required"
        others = [by_dest[d]["label"] + (" (under More settings)" if by_dest[d].get("advanced") else "")
                  for d in f["required_unless"] if d in by_dest]
        return f"{f['label']} is required (unless you tick or fill in {' or '.join(others)})"

    argv = [*CLI_ARGV, cmd["name"]]
    positional: List[str] = []
    for f in cmd["fields"]:
        key = f["dest"]
        if f["kind"] == "flag":
            if _truthy(val(key)):
                argv.append(f["flag"])
            continue
        if f["kind"] == "flag-off":
            if not _truthy(val(key)):
                argv.append(f["flag"])
            continue
        if f["kind"] == "tristate":
            v = val(key).lower()
            if v == "on":
                argv.append(f["flag_on"])
            elif v == "off":
                argv.append(f["flag_off"])
            elif v:
                raise ValueError(f"{f['label']} must be one of the listed choices")
            continue
        if f["kind"] == "kinds":
            picked = [k for k in f["choices"] if _truthy(val(f"{key}.{k}"))]
            if not picked:
                raise ValueError("Pick at least one kind to convert")
            if len(picked) < len(f["choices"]):
                argv += [f["flag"], ",".join(picked)]
            continue
        if f["kind"] == "multi":
            items = [s.strip() for s in re.split(r"[\n;]", val(key)) if s.strip()]
            if f["required"] and not items and not excused(f):
                raise ValueError(missing(f))
            if f["positional"]:
                positional += items
            else:
                for it in items:
                    argv += [f["flag"], it]
            continue
        v = val(key)
        if not v:
            if f["required"] and not excused(f):
                raise ValueError(missing(f))
            continue
        if "\n" in v or "\r" in v:
            raise ValueError(f"{f['label']} takes one value on one line")
        if f["type"] == "int":
            try:
                int(v, 0)
            except ValueError:
                raise ValueError(f"{f['label']} must be a whole number") from None
        if f["type"] == "float":
            try:
                float(v)
            except ValueError:
                raise ValueError(f"{f['label']} must be a number") from None
        if f["choices"] and v not in f["choices"]:
            raise ValueError(f"{f['label']} must be one of {', '.join(f['choices'])}")
        if not f["positional"] and not f["required"] and f["default"] is not None and v == str(f["default"]):
            continue                                   # the command's own default: no need to say it
        if f["positional"]:
            positional.append(v)
        else:
            argv += [f["flag"], v]
    return argv + positional


def output_path(cmd: dict, form: dict) -> Optional[str]:
    """Where a run of this card puts its output (a folder or a file), from the
    card's own form, or None for a command that writes nothing."""
    key = cmd.get("output_field")
    if not key:
        return None
    v = form.get(key)
    if isinstance(v, list):
        v = v[0] if v else ""
    v = (v or "").strip().split("\n")[0].strip()
    if not v:
        return None
    p = Path(v).expanduser()
    if not p.is_absolute():
        p = ROOT / p                                   # the CLI runs in the kit folder
    if cmd.get("output_sub"):
        p = p / cmd["output_sub"]
    return str(p)


def reveal_argv(path: str) -> Optional[List[str]]:
    """The command that shows ``path`` in the system's file manager: a folder is
    opened, a file is shown selected in its folder. Nothing is ever run or
    opened with an application. None when the path is not there."""
    p = Path(path)
    if not p.exists():
        return None
    if sys.platform == "darwin":
        # `open` on a folder with a suffix may hand it to an app (a bundle:
        # .app, .pkg, .workflow, ...); -R only shows it. Plain folders open.
        return ["open", str(p)] if p.is_dir() and not p.suffix else ["open", "-R", str(p)]
    if os.name == "nt":
        return ["explorer", str(p)] if p.is_dir() else ["explorer", f"/select,{p}"]
    return ["xdg-open", str(p if p.is_dir() else p.parent)]


def reveal_label() -> str:
    if sys.platform == "darwin":
        return "Show in Finder"
    if os.name == "nt":
        return "Show in Explorer"
    return "Open folder"


_SCHEMA_CACHE: Optional[List[dict]] = None
_SCHEMA_LOCK = threading.Lock()


def schema() -> List[dict]:
    global _SCHEMA_CACHE
    with _SCHEMA_LOCK:
        if _SCHEMA_CACHE is None:
            _SCHEMA_CACHE = command_schema()
        return _SCHEMA_CACHE


def find_command(name: str) -> Optional[dict]:
    for c in schema():
        if c["name"] == name or name in c["aliases"]:
            return c
    return None


# ---------------------------------------------------------------------------
# Job runner: one subprocess per launched command, lines streamed to clients
# ---------------------------------------------------------------------------

class Job:
    def __init__(self, cmd: List[str], cwd: Optional[Path], output: Optional[str] = None):
        self.id = uuid.uuid4().hex[:12]
        self.cmd = cmd
        self.output = output            # where the card says its output goes (shown after the run)
        self.cwd = str(cwd or ROOT)
        self.started_at = time.time()
        self.ended_at: Optional[float] = None
        self.exit_code: Optional[int] = None
        self.lines: List[str] = []
        self.subscribers: List["queue.Queue[Optional[str]]"] = []
        self._lock = threading.Lock()
        self._proc = subprocess.Popen(
            cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        self.lines.append(f"$ {' '.join(shlex.quote(c) for c in cmd)}")
        threading.Thread(target=self._pump, daemon=True).start()

    def _broadcast(self, line: Optional[str]) -> None:
        with self._lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip("\r\n")
            self.lines.append(line)
            self._broadcast(line)
        rc = self._proc.wait()
        self.exit_code = rc
        self.ended_at = time.time()
        summary = f"[exit {rc}]"
        self.lines.append(summary)
        self._broadcast(summary)
        self._broadcast(None)

    def subscribe(self) -> "queue.Queue[Optional[str]]":
        q: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=50000)
        with self._lock:
            self.subscribers.append(q)
            for line in self.lines:
                try:
                    q.put_nowait(line)
                except queue.Full:
                    break
            if self.ended_at is not None:
                q.put_nowait(None)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            try:
                self.subscribers.remove(q)
            except ValueError:
                pass

    def status(self) -> str:
        if self.exit_code is None:
            return "running"
        return "ok" if self.exit_code == 0 else "err"

    def to_summary(self) -> dict:
        return {"id": self.id, "cmd": self.cmd, "status": self.status(),
                "exit_code": self.exit_code, "started_at": self.started_at,
                "ended_at": self.ended_at, "output": self.output,
                "output_exists": bool(self.output and os.path.exists(self.output))}


JOBS: Dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Auto-detect helpers
# ---------------------------------------------------------------------------

def _walk_for_isos(root: Path, max_depth: int, want: int, out: List[Path]) -> None:
    if len(out) >= want or max_depth < 0:
        return
    try:
        entries = list(root.iterdir())
    except (OSError, PermissionError):
        return
    entries.sort(key=lambda p: (not p.is_file(), p.name.lower()))
    for child in entries:
        if len(out) >= want:
            return
        try:
            if child.is_file():
                if child.suffix.lower() == ".iso" and child.stat().st_size > 100 * 1024 * 1024:
                    out.append(child)
            elif child.is_dir() and not child.name.startswith("."):
                _walk_for_isos(child, max_depth - 1, want, out)
        except (OSError, PermissionError):
            continue


def detect_isos() -> List[str]:
    candidates: List[Path] = []
    for root, depth in ((ROOT.parent, 2), (Path.home() / "Downloads", 2),
                        (Path.home() / "Desktop", 3), (Path.home() / "Documents", 3)):
        try:
            if root.is_dir():
                _walk_for_isos(root, depth, want=64, out=candidates)
        except OSError:
            continue
    return list(dict.fromkeys(str(p) for p in candidates))


_XS2 = re.compile(r"episode ii\b|jenseits|xenosaga ?2|slus.?208\.?92|slus.?211\.?33", re.I)


def xs2_discs(isos: List[str]) -> List[str]:
    """The Episode II images among ``isos``, Disc 1 before Disc 2, one of each."""
    picked: Dict[int, str] = {}
    for p in isos:
        name = Path(p).name
        if not _XS2.search(name) or "copy" in name.lower():
            continue
        m = re.search(r"disc\s*([12])", name, re.I)
        disc = int(m.group(1)) if m else 1
        picked.setdefault(disc, p)
    return _one_region([picked[d] for d in sorted(picked)], isos)


def _one_region(pair: List[str], isos: List[str]) -> List[str]:
    """Keep Disc 1 and Disc 2 of one build: when the pair found by file name mixes
    regions (Japan Disc 1 beside USA Disc 2 in one folder), swap Disc 2 for one of
    Disc 1's region if the folder has it, else keep Disc 1 alone. Images that
    cannot be opened are left as they are."""
    if len(pair) < 2:
        return pair
    try:
        import region
        from iso9660 import IsoImage

        def reg(p):
            iso = IsoImage(p)
            try:
                return region.region_of(iso).name
            finally:
                iso.close()

        want = reg(pair[0])
        if reg(pair[1]) == want:
            return pair
        for p in isos:
            name = Path(p).name
            if (p not in pair and _XS2.search(name) and "copy" not in name.lower()
                    and re.search(r"disc\s*2", name, re.I) and reg(p) == want):
                return [pair[0], p]
        print(f"warning: {Path(pair[1]).name} is a different region from {Path(pair[0]).name}; using Disc 1 only",
              file=sys.stderr)
        return pair[:1]
    except Exception:                    # not an image the kit can open: leave the choice alone
        return pair


def _tool(name: str) -> Optional[str]:
    """A helper program: the copy bundled under tools/ first, then PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    for d in (RUNTIME_DIR / "tools", ROOT / "packaging" / "tools"):
        p = d / exe
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def _numpy_version() -> str:
    """numpy's version, or "" (the music renderer needs it). The app bundles
    carry it; build.py's bundle self-test fails without it."""
    if FROZEN:
        return "bundled"
    try:
        import numpy
    except ImportError:
        return ""
    return getattr(numpy, "__version__", "installed")


def _module_version(name: str) -> str:
    """A required module's version, or "" when it is missing (fontTools and
    uharfbuzz draw new alphabets; the app bundles carry them)."""
    if FROZEN:
        return "bundled"
    try:
        mod = __import__(name)
    except ImportError:
        return ""
    return str(getattr(mod, "__version__", None) or getattr(mod, "version", None) or "installed")


def detect_deps() -> dict:
    return {
        "python": sys.version.split()[0],
        "cli": str(CLI_TARGET) if CLI_TARGET.exists() else "",
        "ffmpeg": _tool("ffmpeg") or "",
        "numpy": _numpy_version(),
        "fonttools": _module_version("fontTools"),
        "uharfbuzz": _module_version("uharfbuzz"),
    }


def default_out_dir(discs: List[str]) -> str:
    if discs:
        return str(Path(discs[0]).parent / "xs2-out")
    return str(Path.home() / "xs2" / "out")


# ---------------------------------------------------------------------------
# Filesystem browser for the picker
# ---------------------------------------------------------------------------

def list_roots() -> List[str]:
    roots: List[str] = []
    if os.name == "nt":
        for d in string.ascii_uppercase:
            p = Path(f"{d}:\\")
            if p.exists():
                roots.append(str(p))
    else:
        roots += ["/", str(Path.home())]
        for parent in ("/mnt", "/media", "/Volumes"):
            try:
                pp = Path(parent)
                if pp.is_dir():
                    roots += [str(c) for c in sorted(pp.iterdir()) if c.is_dir()]
            except (OSError, PermissionError):
                continue
    return list(dict.fromkeys(roots))


def list_dir(path_str: str, ext_filter: Optional[str] = None) -> dict:
    p = Path(path_str).expanduser() if path_str else Path.home()
    if not p.exists():
        p = Path.home()
    if not p.is_dir():
        p = p.parent
    p = p.resolve()
    entries: List[dict] = []
    err: Optional[str] = None
    try:
        children = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except (OSError, PermissionError) as exc:
        children, err = [], str(exc)
    for child in children:
        try:
            if child.is_symlink() and not child.exists():
                continue
            if child.is_dir():
                if child.name.startswith("."):
                    continue
                entries.append({"name": child.name, "type": "dir", "path": str(child)})
            else:
                if ext_filter and child.suffix.lower().lstrip(".") != ext_filter.lower().lstrip("."):
                    continue
                entries.append({"name": child.name, "type": "file", "path": str(child),
                                "size": child.stat().st_size})
        except (OSError, PermissionError):
            continue
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "entries": entries, "roots": list_roots(), "error": err}


# ---------------------------------------------------------------------------
# Embedded UI. The stylesheet is the Episode I GUI's, unchanged; the cards
# are generated from /schema.
# ---------------------------------------------------------------------------

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>XENOSAGA II · Extractor</title>
<style>
/*__XS1_CSS__*/
.card .row .kinds-wrap { display: flex; flex-wrap: wrap; gap: 6px 16px; }
.card .row .kinds-wrap label {
  text-transform: none; letter-spacing: 0; font-size: 13px; color: var(--fg);
  display: flex; align-items: center; gap: 6px;
}
.card .row .req, .card .row .opt {
  display: block; margin-top: 2px; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
}
.card .row .req { color: var(--accent-warm); }
.card .row .opt { color: var(--muted); opacity: 0.7; }
.card .row .field { min-width: 0; }
.card .row .fhelp { color: var(--muted); font-size: 12px; margin-top: 4px; line-height: 1.45; }
.card .row .fhelp b, .card .check-wrap span b { color: var(--fg); font-weight: 500; }
.card .row .check-wrap { align-items: flex-start; }
.card .row .check-wrap input { margin-top: 1px; flex: none; }
.card textarea.paths { white-space: pre-wrap; word-break: break-all; }
.card textarea.one { resize: none; overflow: hidden; min-height: 0; }
.card-body details.adv { margin: 4px 0 10px; border-top: 1px dashed var(--border); padding-top: 8px; }
.card-body details.adv > summary { cursor: pointer; color: var(--accent); font-size: 12px; margin-bottom: 6px; }
.card-body details.adv > summary .n { color: var(--muted); }
#groups .loading { color: var(--muted); font-size: 13px; padding: 8px 0; }
.card-head { align-items: flex-start; }
.card-head .head-text { flex: 1; min-width: 0; }
.card-head .head-text h3 { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; }
.card-head .cmd {
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 11px; letter-spacing: 0;
  color: var(--muted); background: rgba(110, 138, 171, 0.10); padding: 1px 6px; border-radius: 2px;
}
.card-head .tag {
  font-family: "Inter", system-ui, sans-serif; font-size: 10px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--accent-warm); border: 1px solid rgba(255, 157, 58, 0.35);
  padding: 0 6px; border-radius: 2px;
}
.card-head .summary { margin: 4px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
.card-head .step {
  font-style: normal; font-size: 13px; font-family: "Inter", system-ui, sans-serif; font-weight: 600;
  min-width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  border: 1px solid var(--accent-warm); color: var(--accent-warm); margin-top: 1px;
}
.card-head .badge:empty { display: none; }
.card-head .chev { margin-top: 4px; }
.card-body .io { display: grid; grid-template-columns: 70px 1fr; gap: 4px 12px; margin: 0 0 12px;
  font-size: 13px; line-height: 1.5; }
.card-body .io dt { color: var(--accent); font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; padding-top: 2px; }
.card-body .io dd { margin: 0; color: var(--fg); }
.card-body .io code, .card .fhelp code { font-size: 11.5px; }
.card-body details.more { margin: 0 0 14px; }
.card-body details.more summary { cursor: pointer; color: var(--accent); font-size: 12px; }
.card-body details.more p.help { margin: 8px 0 0; }
.card .result { margin-top: 14px; padding: 10px 12px; border-radius: 4px; font-size: 13px; line-height: 1.5;
  border: 1px solid var(--border); background: rgba(2, 5, 11, 0.4); }
.card .result[hidden] { display: none; }
.card .result.ok { border-color: rgba(98, 255, 161, 0.4); }
.card .result.err { border-color: rgba(255, 110, 127, 0.5); }
.card .result .where { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; word-break: break-all; color: var(--fg); }
.card .result .reveal { margin-left: 8px; padding: 4px 10px; font-size: 11px; }
.card .result .next { margin-top: 6px; color: var(--muted); }
.card .result .next b { color: var(--fg); font-weight: 500; }
.group { margin: 0 0 26px; }
.group > h2 {
  font-family: "Cormorant Garamond", "Georgia", serif; font-weight: 500; font-size: 20px;
  letter-spacing: 0.08em; margin: 0 0 2px; color: var(--accent); text-transform: uppercase;
}
.group > p.blurb { margin: 0 0 12px; color: var(--muted); font-size: 13px; max-width: 760px; }
.howto {
  border: 1px solid var(--border-strong); border-radius: 6px; padding: 14px 18px; margin: 0 0 26px;
  background: linear-gradient(180deg, rgba(93, 213, 255, 0.06) 0%, rgba(10, 21, 37, 0.6) 100%);
}
.howto h2 { margin: 0 0 8px; font-family: "Cormorant Garamond", "Georgia", serif; font-weight: 500;
  font-size: 20px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--fg); }
.howto ol { margin: 0 0 8px; padding-left: 22px; }
.howto li { margin: 3px 0; }
.howto li a { color: var(--accent); cursor: pointer; text-decoration: underline; text-underline-offset: 2px; }
.howto p { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
.howto .safe { color: var(--ok); }
.howto details { margin-top: 8px; font-size: 13px; }
.howto details summary { cursor: pointer; color: var(--accent); font-size: 12px; }
.howto dl { display: grid; grid-template-columns: 210px 1fr; gap: 3px 14px; margin: 8px 0 0; }
.howto dt { color: var(--fg); }
.howto dd { margin: 0; color: var(--muted); }
#log:empty::before { content: "Nothing has run yet. Press Run on a card and its progress appears here."; color: var(--muted); }
</style>
</head>
<body>
<header>
  <div class="title">
    <h1>
      <span>Xenosaga</span><span class="i"> II</span>
      <span class="sep">·</span><span class="extractor">EXTRACTOR</span>
    </h1>
    <div class="tagline">Jenseits von Gut und Böse · unpack, view and mod the game's files</div>
  </div>
  <div class="actions">
    <button class="ghost" id="btn-refresh">Refresh status</button>
  </div>
</header>
<main>
  <section id="cards">
    <div class="howto" id="howto">
      <h2>How to use this window</h2>
      <ol>
        <li><b>Check your disc images.</b> <span id="howto-discs">Looking for them…</span></li>
        <li><b>Unpack:</b> in <a data-goto="dump">1 · Unpack the discs</a> (already open below), press Run.</li>
        <li><b>Convert:</b> then <a data-goto="browse">3 · Convert to files you can open</a>, with the same folder.
          Pictures, models, sound and text land in its <code>browse</code> folder.</li>
      </ol>
      <p>After that, use any section further down. Each card says what it needs and what it makes; click its
        title to open it. Fields marked <span style="color:var(--accent-warm)">REQUIRED</span> must be filled in
        (a field's note says if a tick box lets it stay empty); the others can stay as they are. Settings most
        people never change are folded away under <b>More settings</b>.</p>
      <p class="safe">Your original disc images are never changed: every card that edits the game writes a new copy.</p>
      <details><summary>Words you will see</summary><dl id="glossary"></dl></details>
    </div>
    <div id="groups"><p class="loading">Loading the cards…</p><noscript><p>This window needs JavaScript
      turned on in your browser.</p></noscript></div>
  </section>
  <aside id="right">
    <h2>Environment</h2>
    <div id="status-box"></div>
    <h2>Output</h2>
    <pre id="log"></pre>
  </aside>
</main>

<div class="modal" id="fs-modal" hidden>
  <div class="modal-backdrop" data-close></div>
  <div class="modal-window">
    <div class="modal-header">
      <h2 id="fs-title">Select…</h2>
      <button type="button" class="x" data-close>×</button>
    </div>
    <div class="fs-toolbar">
      <button type="button" class="ghost" id="fs-up">↑ Up</button>
      <div class="fs-crumb" id="fs-crumb"></div>
    </div>
    <div class="modal-error" id="fs-error" hidden></div>
    <div class="fs-quick" id="fs-quick" hidden></div>
    <div class="fs-list" id="fs-list"></div>
    <div class="fs-roots" id="fs-roots"></div>
    <div class="modal-footer">
      <div class="sel-path" id="fs-sel">No selection</div>
      <button type="button" class="ghost" data-close>Cancel</button>
      <button type="button" id="fs-select" disabled>Select</button>
    </div>
  </div>
</div>

<script>
const log = document.getElementById('log');
let currentEventSource = null;
const cardEls = {};
const cmdByName = {};
let _detectedIsos = [];
let _detect = {};

const STATUS_TEXT = {run: 'running', ok: 'done', err: 'failed'};
function setStatus(name, status) {
  const el = cardEls[name];
  if (!el) return;
  el.classList.remove('run','ok','err');
  if (status) el.classList.add(status);
  const badge = el.querySelector('.badge');
  badge.classList.remove('run','ok','err');
  badge.textContent = status ? STATUS_TEXT[status] : '';
  if (status) badge.classList.add(status);
}
function appendLog(line) { log.textContent += line + '\n'; log.scrollTop = log.scrollHeight; }
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB','MB','GB','TB']; let v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 10 ? 0 : 1) + ' ' + units[i];
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);
}
function baseName(p) { return String(p).split('/').pop().split('\\').pop(); }

function showResult(name, job) {
  const el = cardEls[name], c = cmdByName[name];
  if (!el || !c) return;
  const box = el.querySelector('.result');
  box.hidden = false;
  box.className = 'result ' + (job.status === 'ok' ? 'ok' : 'err');
  if (job.status !== 'ok') {
    box.innerHTML = '<b>Failed.</b> The messages in the Output panel on the right say why. '
      + 'Nothing was changed on your original disc.';
    return;
  }
  let html = '<b>Done.</b> ';
  if (job.output) {
    html += 'Output is in <span class="where">' + escapeHtml(job.output) + '</span>';
    if (job.output_exists) html += `<button type="button" class="ghost reveal">${escapeHtml(_detect.reveal_label || 'Open folder')}</button>`;
  } else {
    html += 'See the Output panel on the right for the result.';
  }
  if (c.next) html += '<div class="next">' + c.next + '</div>';
  box.innerHTML = html;
  const btn = box.querySelector('.reveal');
  if (btn) btn.addEventListener('click', () => fetch('/reveal/' + job.id, {method: 'POST'})
    .then(r => r.json()).then(j => { if (j.error) appendLog('[reveal] ' + j.error); }));
}

async function runCommand(name, body) {
  if (currentEventSource) currentEventSource.close();
  log.textContent = '';
  setStatus(name, 'run');
  const box = cardEls[name] && cardEls[name].querySelector('.result');
  if (box) { box.hidden = false; box.className = 'result'; box.textContent = 'Running… progress appears in the Output panel on the right.'; }
  const res = await fetch('/run/' + encodeURIComponent(name), {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body});
  if (!res.ok) {
    const t = await res.text(); let msg = t;
    try { const j = JSON.parse(t); if (j && j.error) msg = j.error; } catch (_) {}
    setStatus(name, 'err'); appendLog('[error] ' + msg);
    if (box) { box.className = 'result err'; box.textContent = msg; }
    return;
  }
  const {job_id} = await res.json();
  const es = new EventSource('/stream/' + job_id);
  currentEventSource = es;
  es.onmessage = (e) => {
    if (e.data === '__eof__') {
      es.close();
      fetch('/job/' + job_id).then(r => r.json()).then(j => {
        setStatus(name, j.status === 'ok' ? 'ok' : 'err'); showResult(name, j);
      });
    } else { appendLog(e.data); }
  };
  es.onerror = () => { es.close(); };
}

// ------------------------------------------------------------------ picker
const fsModal = document.getElementById('fs-modal'), fsTitle = document.getElementById('fs-title');
const fsCrumb = document.getElementById('fs-crumb'), fsList = document.getElementById('fs-list');
const fsRoots = document.getElementById('fs-roots'), fsError = document.getElementById('fs-error');
const fsQuick = document.getElementById('fs-quick'), fsSel = document.getElementById('fs-sel');
const fsSelectBtn = document.getElementById('fs-select'), fsUp = document.getElementById('fs-up');
const fsState = { mode: 'file', filter: null, current: null, parent: null, selected: null, onChoose: null };

function _renderQuickPick() {
  const isos = fsState.filter === 'iso' ? _detectedIsos : [];
  if (!isos.length) { fsQuick.hidden = true; return; }
  fsQuick.hidden = false;
  fsQuick.innerHTML = '<span>Detected:</span>' + isos.map(p =>
    `<a class="qp" data-path="${escapeHtml(p)}" title="${escapeHtml(p)}">${escapeHtml(p.split('/').pop().split('\\').pop())}</a>`).join('');
  fsQuick.querySelectorAll('.qp').forEach(a => {
    a.addEventListener('click', () => {
      fsQuick.querySelectorAll('.qp').forEach(x => x.classList.remove('selected'));
      a.classList.add('selected'); fsState.selected = a.dataset.path;
      fsSel.classList.add('has'); fsSel.textContent = a.dataset.path; fsSelectBtn.disabled = false;
    });
    a.addEventListener('dblclick', () => { fsState.selected = a.dataset.path; chooseAndClose(); });
  });
}
function openPicker({mode, filter, startPath, title, onChoose}) {
  Object.assign(fsState, {mode, filter: filter || null, current: null, parent: null, selected: null, onChoose});
  fsTitle.textContent = title || (mode === 'file' ? 'Select a file' : 'Select a folder');
  fsSel.classList.remove('has'); fsSel.textContent = 'No selection';
  fsSelectBtn.disabled = mode === 'file'; fsError.hidden = true;
  _renderQuickPick(); fsModal.hidden = false; loadDir(startPath || '');
}
function closePicker() { fsModal.hidden = true; fsState.onChoose = null; }
async function loadDir(path) {
  fsError.hidden = true;
  try {
    const qs = new URLSearchParams();
    if (path) qs.set('path', path);
    if (fsState.filter) qs.set('filter', fsState.filter);
    const res = await fetch('/browse-fs?' + qs.toString());
    if (!res.ok) { fsError.hidden = false; fsError.textContent = 'Cannot read directory.'; return; }
    const data = await res.json();
    if (data.error) { fsError.hidden = false; fsError.textContent = data.error; }
    fsState.current = data.path; fsState.parent = data.parent; fsState.selected = null;
    if (fsState.mode !== 'file') {
      fsState.selected = data.path; fsSel.classList.add('has'); fsSel.textContent = data.path; fsSelectBtn.disabled = false;
    } else { fsSel.classList.remove('has'); fsSel.textContent = 'No selection'; fsSelectBtn.disabled = true; }
    fsUp.disabled = !data.parent;
    renderCrumb(data.path); renderList(data.entries); renderRoots(data.roots);
  } catch (err) { fsError.hidden = false; fsError.textContent = 'Error loading directory: ' + err.message; }
}
function renderCrumb(fullPath) {
  const sep = fullPath.indexOf('\\') >= 0 && fullPath.indexOf('/') < 0 ? '\\' : '/';
  let parts;
  if (sep === '\\') { parts = fullPath.split('\\').filter(Boolean); if (parts.length && /^[A-Z]:$/i.test(parts[0])) parts[0] += '\\'; }
  else { parts = ['/'].concat(fullPath.split('/').filter(Boolean)); }
  let acc = '';
  fsCrumb.innerHTML = parts.map((p, i) => {
    if (sep === '/') acc = (i === 0) ? '/' : (acc.endsWith('/') ? acc : acc + '/') + p;
    else acc = (i === 0) ? p : (acc + (acc.endsWith('\\') ? '' : '\\') + p);
    const a = `<a data-path="${escapeHtml(acc)}">${escapeHtml(p)}</a>`;
    return i === parts.length - 1 ? a : a + '<span class="sep">/</span>';
  }).join('');
  fsCrumb.querySelectorAll('a').forEach(a => a.addEventListener('click', () => loadDir(a.dataset.path)));
}
function renderList(entries) {
  if (!entries.length) {
    fsList.innerHTML = `<div class="fs-empty">(empty${fsState.filter ? ' — no .' + fsState.filter + ' files here' : ''})</div>`;
    return;
  }
  fsList.innerHTML = entries.map(e => {
    const icon = e.type === 'dir' ? '📁' : '📄', iconCls = e.type === 'dir' ? 'icon dir' : 'icon';
    const size = e.type === 'file' ? `<span class="size">${fmtSize(e.size)}</span>` : '';
    return `<div class="fs-entry" data-type="${e.type}" data-path="${escapeHtml(e.path)}">
      <span class="${iconCls}">${icon}</span><span class="name">${escapeHtml(e.name)}</span>${size}</div>`;
  }).join('');
  fsList.querySelectorAll('.fs-entry').forEach(el => {
    el.addEventListener('click', () => {
      if (el.dataset.type === 'dir') { loadDir(el.dataset.path); }
      else if (fsState.mode === 'file' || fsState.mode === 'any') {
        fsList.querySelectorAll('.fs-entry.selected').forEach(x => x.classList.remove('selected'));
        el.classList.add('selected'); fsState.selected = el.dataset.path;
        fsSel.classList.add('has'); fsSel.textContent = el.dataset.path; fsSelectBtn.disabled = false;
      }
    });
    el.addEventListener('dblclick', () => {
      if (el.dataset.type === 'dir') loadDir(el.dataset.path);
      else if (fsState.mode === 'file' || fsState.mode === 'any') { fsState.selected = el.dataset.path; chooseAndClose(); }
    });
  });
}
function renderRoots(roots) {
  fsRoots.innerHTML = '<span>Roots:</span>' + roots.map(r => `<a data-path="${escapeHtml(r)}">${escapeHtml(r)}</a>`).join('');
  fsRoots.querySelectorAll('a').forEach(a => a.addEventListener('click', () => loadDir(a.dataset.path)));
}
function chooseAndClose() { if (!fsState.selected) return; const cb = fsState.onChoose; closePicker(); if (cb) cb(fsState.selected); }
fsUp.addEventListener('click', () => { if (fsState.parent) loadDir(fsState.parent); });
fsSelectBtn.addEventListener('click', chooseAndClose);
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closePicker));
document.addEventListener('keydown', (e) => { if (!fsModal.hidden && e.key === 'Escape') closePicker(); });

// ------------------------------------------------------------------ cards from the CLI's parser
// A field's starting value: its prefill template from /schema, with {out} the
// suggested output folder and {disc}/{discs} the disc images found.
function defaultFor(f, d) {
  const t = f.prefill || '';
  if (!t) return '';
  const discs = d.discs || [];
  if ((/\{discs?\}/.test(t) && !discs.length) || (t.includes('{out}') && !d.default_out)) return '';
  return t.replace('{discs}', discs.join('\n')).replace('{disc}', discs[0] || '').replace('{out}', d.default_out || '');
}
function choiceText(f, v) {
  const l = (f.choice_labels || {})[v];
  return l ? (l === v ? v : `${v}: ${l}`) : v;
}
function fieldHtml(f, d) {
  const name = escapeHtml(f.dest);
  const mark = f.required ? '<span class="req">required</span>' : '<span class="opt">optional</span>';
  const label = `<label>${escapeHtml(f.label)}${mark}</label>`;
  const help = f.help ? `<div class="fhelp">${f.help}</div>` : '';
  if (f.kind === 'flag' || f.kind === 'flag-off') {
    return `<div class="row"><label>${escapeHtml(f.label)}</label><div class="check-wrap"><input type="checkbox" name="${name}"
      ${f.kind === 'flag-off' ? 'checked' : ''}><span>${f.help || ''}</span></div></div>`;
  }
  if (f.kind === 'kinds') {
    const boxes = f.choices.map(k => {
      const l = (f.choice_labels || {})[k] || k;
      return `<label title="${escapeHtml(k)}"><input type="checkbox" name="${name}.${escapeHtml(k)}" checked> ${escapeHtml(l)}</label>`;
    }).join('');
    return `<div class="row">${label}<div class="field"><div class="kinds-wrap">${boxes}</div>${help}</div></div>`;
  }
  let opts = f.select;
  if (!opts && f.choices) {
    const def = f.default === null || f.default === undefined ? null : String(f.default);
    opts = f.choices.map(c => [c, choiceText(f, c) + (c === def ? ' (default)' : '')]);
    if (def === null || !f.choices.includes(def)) {
      const blank = (f.choice_labels || {})[''] || 'Default (the command decides)';
      opts = [['', blank]].concat(opts);
    }
    opts.selected = def;
  }
  if (opts) {
    const o = opts.map(([v, l]) => `<option value="${escapeHtml(v)}"${v === opts.selected ? ' selected' : ''}>${escapeHtml(l)}</option>`).join('');
    return `<div class="row">${label}<div class="field"><select name="${name}">${o}</select>${help}</div></div>`;
  }
  const value = escapeHtml(defaultFor(f, d)), ph = escapeHtml(f.placeholder || '');
  const isPath = !!f.pick;
  // A one-value path box is a one-line textarea, so a long path wraps instead of
  // being cut off (Enter and pasted line breaks are dropped).
  const input = f.kind === 'multi'
    ? `<textarea name="${name}" class="${isPath ? 'paths' : ''}" rows="${Math.max(2, value.split('\n').reduce((n, l) => n + Math.max(1, Math.ceil(l.length / (isPath ? 56 : 80))), 0))}"
        placeholder="${ph ? ph + ' (one per line)' : 'one per line'}">${value}</textarea>`
    : isPath
      ? `<textarea name="${name}" class="paths one" rows="1" placeholder="${ph}">${value}</textarea>`
      : `<input type="text" name="${name}" placeholder="${ph}" value="${value}">`;
  if (!f.pick) return `<div class="row">${label}<div class="field">${input}${help}</div></div>`;
  const btn = {file: 'Choose file…', dir: 'Choose folder…', save: 'Save as…', any: 'Choose…'}[f.pick] || 'Choose…';
  return `<div class="row">${label}<div class="field"><div class="with-pick">${input}
    <button type="button" class="pick" data-pick="${f.pick}" data-pick-filter="${escapeHtml(f.filter || '')}"
      data-pick-for="${name}" data-suggest="${escapeHtml(f.suggest || '')}" data-multi="${f.kind === 'multi' ? 1 : 0}">${btn}</button></div>${help}</div></div>`;
}
function formBody(form) {
  const fd = new FormData(form);
  form.querySelectorAll('input[type=checkbox]').forEach(cb => fd.set(cb.name, cb.checked ? 'on' : ''));
  return new URLSearchParams(fd).toString();
}
function fit(t) {
  if (!t.offsetParent) return;               // hidden (closed card): measured when it opens
  t.style.height = 'auto'; t.style.height = (t.scrollHeight + t.offsetHeight - t.clientHeight) + 'px';
}
function fitAll(root) { root.querySelectorAll('textarea.one').forEach(fit); }
window.addEventListener('resize', () => document.querySelectorAll('.card.open').forEach(fitAll));
document.addEventListener('toggle', e => { if (e.target.matches && e.target.matches('details.adv')) fitAll(e.target); }, true);
function makeCard(c, d) {
  const div = document.createElement('div');
  div.className = 'card'; div.dataset.name = c.name; div.id = 'card-' + c.name;
  const alias = c.aliases.length ? ` <span class="cmd" title="The same command under another name">also: ${c.aliases.map(escapeHtml).join(', ')}</span>` : '';
  const step = c.step ? `<span class="step">${c.step}</span>` : '';
  const tag = c.writes_iso ? '<span class="tag" title="Writes a new disc image; your original is not changed">new disc copy</span>' : '';
  const io = (c.needs || c.makes) ? `<dl class="io">${c.needs ? `<dt>Needs</dt><dd>${c.needs}</dd>` : ''}
      ${c.makes ? `<dt>Makes</dt><dd>${c.makes}</dd>` : ''}</dl>` : '';
  const more = c.help ? `<details class="more"><summary>More detail</summary><p class="help">${c.help}</p></details>` : '';
  const basic = c.fields.filter(f => !f.advanced), extra = c.fields.filter(f => f.advanced);
  const adv = extra.length ? `<details class="adv"><summary>More settings <span class="n">(${extra.length}, most
      people leave these as they are)</span></summary>${extra.map(f => fieldHtml(f, d)).join('')}</details>` : '';
  div.innerHTML = `
    <div class="card-head">${step}
      <div class="head-text"><h3>${escapeHtml(c.title)} <span class="cmd" title="The command-line name of this card">${escapeHtml(c.name)}</span>${alias}${tag}</h3>
        <p class="summary">${c.summary}</p></div>
      <span class="badge"></span><span class="chev">▼</span></div>
    <div class="card-body">${io}${more}
      <form data-cmd="${escapeHtml(c.name)}">${basic.map(f => fieldHtml(f, d)).join('')}${adv}
        <div class="actions"><button type="submit">Run</button>
          <button type="button" class="ghost dryrun" title="Show the command line this card would run, without running it">Show command</button></div>
      </form>
      <div class="result" hidden></div></div>`;
  div.querySelector('.card-head').addEventListener('click', () => { div.classList.toggle('open'); fitAll(div); });
  div.querySelectorAll('textarea.one').forEach(t => {
    t.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); t.form.requestSubmit(); } });
    t.addEventListener('input', () => { const v = t.value.replace(/[\r\n]+/g, ''); if (v !== t.value) t.value = v; fit(t); });
  });
  div.querySelectorAll('button.pick').forEach(btn => btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const el = btn.closest('.row').querySelector(`[name="${btn.dataset.pickFor}"]`);
    const multi = btn.dataset.multi === '1', mode = btn.dataset.pick;
    const current = multi ? (el.value.split('\n').filter(Boolean).pop() || '') : el.value;
    const fileName = (mode === 'save' && current) ? baseName(current) : (btn.dataset.suggest || 'modded.iso');
    openPicker({
      mode: mode === 'save' ? 'dir' : mode, filter: mode === 'save' ? null : (btn.dataset.pickFilter || null),
      startPath: current || undefined,
      title: mode === 'save' ? 'Save as: choose the folder for ' + fileName : undefined,
      onChoose: (p) => {
        if (mode === 'save') p = p.replace(/[\/\\]$/, '') + '/' + fileName;
        if (multi) el.value = (el.value.trim() ? el.value.trim() + '\n' : '') + p; else el.value = p;
        if (el.classList.contains('one')) fit(el);
      },
    });
  }));
  const form = div.querySelector('form');
  form.addEventListener('submit', (e) => { e.preventDefault(); runCommand(c.name, formBody(form)); });
  div.querySelector('.dryrun').addEventListener('click', (e) => {
    e.stopPropagation();
    fetch('/preview/' + encodeURIComponent(c.name), {method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: formBody(form)})
      .then(r => r.json()).then(j => {
        log.textContent = '';
        if (j.error) { appendLog('[error] ' + j.error); return; }
        appendLog('# Would run:'); appendLog(j.cmd.map(x => /\s/.test(x) ? `"${x}"` : x).join(' '));
      });
  });
  cardEls[c.name] = div;
  cmdByName[c.name] = c;
  return div;
}
function openCard(name, scroll) {
  const el = cardEls[name];
  if (!el) return;
  el.classList.add('open');
  fitAll(el);
  if (scroll) el.scrollIntoView({behavior: scroll === 'instant' ? 'auto' : 'smooth', block: 'start'});
}

async function loadStatus() {
  const d = await (await fetch('/detect')).json();
  _detect = d;
  _detectedIsos = d.isos || [];
  const box = document.getElementById('status-box');
  const rows = [
    ['Python', d.deps.python, true],
    ['CLI', d.deps.cli || 'NOT FOUND', !!d.deps.cli],
    ['Discs found', (d.discs || []).length ? d.discs.map(baseName).join(' + ') : 'none (use Choose file)', (d.discs || []).length > 0],
    ['ffmpeg (movies)', d.deps.ffmpeg || 'not found: movies are skipped', !!d.deps.ffmpeg],
    ['numpy (music WAV)', d.deps.numpy || 'MISSING: python -m pip install -r requirements.txt', !!d.deps.numpy],
    ['fontTools (new alphabets)', d.deps.fonttools || 'MISSING: python -m pip install -r requirements.txt', !!d.deps.fonttools],
    ['uharfbuzz (new alphabets)', d.deps.uharfbuzz || 'MISSING: python -m pip install -r requirements.txt', !!d.deps.uharfbuzz],
  ];
  box.innerHTML = rows.map(([k, v, ok]) =>
    `<div class="row"><span>${k}</span><span class="v ${ok ? 'ok' : 'bad'}" title="${escapeHtml(String(v))}">${escapeHtml(String(v))}</span></div>`).join('');
  const discs = d.discs || [];
  document.getElementById('howto-discs').innerHTML = discs.length
    ? 'Found: ' + discs.map(p => `<code title="${escapeHtml(p)}">${escapeHtml(baseName(p))}</code>`).join(' and ')
      + '. Each card is filled in with them; change a card\'s Disc image to use others.'
    : 'None found next to the kit or in Downloads, Desktop or Documents: use <b>Choose file…</b> in each card.';
  return d;
}

(async () => {
  const d = await loadStatus();
  const s = await (await fetch('/schema')).json();
  if (s.error) { appendLog('[error] could not read the CLI\'s commands: ' + s.error); return; }
  document.getElementById('glossary').innerHTML = (s.glossary || []).map(([w, m]) => `<dt>${w}</dt><dd>${m}</dd>`).join('');
  const byName = {};
  s.commands.forEach(c => { byName[c.name] = c; });
  const groups = document.getElementById('groups');
  groups.textContent = '';
  s.groups.forEach(g => {
    const sec = document.createElement('section');
    sec.className = 'group'; sec.dataset.group = g.id;
    sec.innerHTML = `<h2>${escapeHtml(g.title)}</h2><p class="blurb">${escapeHtml(g.blurb)}</p>`;
    g.commands.forEach(n => sec.appendChild(makeCard(byName[n], d)));
    groups.appendChild(sec);
  });
  document.querySelectorAll('.howto [data-goto]').forEach(a => a.addEventListener('click', () => openCard(a.dataset.goto, true)));
  // ?open=a,b opens those cards (for links and screenshots); otherwise step 1 starts open.
  const want = (new URLSearchParams(location.search).get('open') || '').split(',').filter(Boolean);
  if (want.length) { want.forEach(n => openCard(n, false)); openCard(want[0], 'instant'); }
  else if (!new URLSearchParams(location.search).has('collapsed')) openCard('dump', false);
})();
document.getElementById('btn-refresh').addEventListener('click', loadStatus);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

def request_allowed(host: str, origin: Optional[str], port: int) -> bool:
    """Only this window's own page may use the server: the Host header must name
    the local address and port (so a web page cannot reach it through a DNS
    rebinding trick), and a browser's Origin, when sent, must be that same
    address (so another site cannot post a form to /run/ and start a command)."""
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if (host or "").lower() not in allowed:
        return False
    return origin is None or origin.lower() in {f"http://{a}" for a in allowed}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "Xenosaga2GUI/1.0"

    def _allowed(self) -> bool:
        if request_allowed(self.headers.get("Host", ""), self.headers.get("Origin"), self.server.server_address[1]):
            return True
        self._send(403, "forbidden: this server only answers its own window")
        return False

    def log_message(self, fmt, *args):  # noqa: N802
        return

    def _send(self, status, body, content_type="text/plain", extra_headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json")

    def do_GET(self):  # noqa: N802
        if not self._allowed():
            return
        url = urlparse(self.path)
        path = url.path
        if path in ("/", "/index.html"):
            return self._send(200, page(), "text/html; charset=utf-8")
        if path == "/detect":
            isos = detect_isos()
            discs = xs2_discs(isos)
            return self._send_json(200, {"root": str(ROOT), "isos": isos, "discs": discs,
                                         "deps": detect_deps(), "default_out": default_out_dir(discs),
                                         "reveal_label": reveal_label()})
        if path == "/schema":
            try:
                cmds = schema()
                return self._send_json(200, {"commands": cmds, "groups": grouped(cmds),
                                             "glossary": GLOSSARY})
            except Exception as exc:  # the page shows it instead of an empty list
                return self._send_json(200, {"error": f"{type(exc).__name__}: {exc}"})
        if path == "/browse-fs":
            params = parse_qs(url.query, keep_blank_values=True)
            return self._send_json(200, list_dir(params.get("path", [""])[0],
                                                 params.get("filter", [""])[0] or None))
        if path.startswith("/job/"):
            with JOBS_LOCK:
                job = JOBS.get(path[len("/job/"):])
            if not job:
                return self._send_json(404, {"error": "no such job"})
            return self._send_json(200, job.to_summary())
        if path.startswith("/stream/"):
            return self._serve_stream(path[len("/stream/"):])
        return self._send(404, "not found")

    def do_POST(self):  # noqa: N802
        if not self._allowed():
            return
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        form = parse_qs(body, keep_blank_values=True)
        for prefix, handler in (("/preview/", self._preview), ("/run/", self._run), ("/reveal/", self._reveal)):
            if url.path.startswith(prefix):
                return handler(unquote(url.path[len(prefix):]), form)
        return self._send(404, "not found")

    def _argv(self, name, form):
        cmd = find_command(name)
        if cmd is None:
            raise ValueError(f"unknown command: {name}")
        return build_argv(cmd, form)

    def _preview(self, name, form):
        try:
            return self._send_json(200, {"cmd": self._argv(name, form)})
        except Exception as exc:
            return self._send_json(400, {"error": str(exc)})

    def _reveal(self, job_id, form):
        """Show a finished job's output in the file manager. Only the path the
        job's own card named is shown; nothing else can be asked for."""
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job or not job.output:
            return self._send_json(404, {"error": "this run has no output to show"})
        argv = reveal_argv(job.output)
        if argv is None:
            return self._send_json(404, {"error": f"not found: {job.output}"})
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return self._send_json(500, {"error": f"could not open the file manager: {exc}"})
        return self._send_json(200, {"ok": True})

    def _run(self, name, form):
        try:
            argv = self._argv(name, form)
        except Exception as exc:
            return self._send_json(400, {"error": str(exc)})
        try:
            job = Job(argv, cwd=ROOT, output=output_path(find_command(name), form))
        except OSError as exc:
            shown = " ".join(shlex.quote(c) for c in argv)
            return self._send_json(500, {"error":
                f"could not start the extractor:\n  $ {shown}\n  {type(exc).__name__}: {exc}\n\n"
                f"Expected CLI at: {CLI_TARGET}"
                + ("  (NOT FOUND — rebuild so xeno-cli sits next to the GUI)" if not CLI_TARGET.exists() else "")})
        with JOBS_LOCK:
            JOBS[job.id] = job
        return self._send_json(200, {"job_id": job.id})

    def _serve_stream(self, job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            return self._send(404, "no such job")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = job.subscribe()
        try:
            while True:
                try:
                    line = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if line is None:
                    self.wfile.write(b"data: __eof__\n\n")
                    self.wfile.flush()
                    return
                for chunk in line.replace("\r", "").split("\n"):
                    self.wfile.write(b"data: " + chunk.encode("utf-8") + b"\n")
                self.wfile.write(b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            job.unsubscribe(q)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


_PAGE: Optional[str] = None


def page() -> str:
    global _PAGE
    if _PAGE is None:
        _PAGE = INDEX_HTML.replace("/*__XS1_CSS__*/", XS1_CSS)
    return _PAGE


# ---------------------------------------------------------------------------
# Self-test (no browser, no disc)
# ---------------------------------------------------------------------------

def self_test() -> int:
    ok = True

    def check(cond, msg):
        nonlocal ok
        print(("ok    " if cond else "FAIL  ") + msg)
        ok &= bool(cond)

    cmds = command_schema()
    names = {c["name"] for c in cmds} | {a for c in cmds for a in c["aliases"]}
    import cli as climod
    real = cli_parser(climod)
    sub = next(a for a in real._actions if isinstance(a, argparse._SubParsersAction))
    check(set(sub.choices) == names, f"every cli.py subcommand has a card ({len(sub.choices)} commands)")
    missing = sorted(n for n in sub.choices if not COMMAND_HELP.get(n) and not any(
        n in c["aliases"] and COMMAND_HELP.get(c["name"]) for c in cmds))
    check(not missing, "every cli.py subcommand's card has its long help text"
          + (f" (missing: {', '.join(missing)})" if missing else ""))
    named = {m for v in COMMAND_HELP.values()
             for m in re.findall(r"\b(?:text|exe|font|lang|texture|model)-[a-z]+\b", v)}
    check(named and named <= set(sub.choices), "every command a card's text names is a cli.py subcommand"
          + (f" (not: {', '.join(sorted(named - set(sub.choices)))})" if named - set(sub.choices) else ""))
    dump = find_command("dump")
    check(dump is not None and "extract" in dump["aliases"], "dump and extract share one card")
    form = {"iso": ["/d/Disc 1.iso\n/d/Disc 2.iso"], "out": ["/o"], "code": ["on"], "glob": [""]}
    argv = build_argv(dump, form)[len(CLI_ARGV):]
    check(argv == ["dump", "--iso", "/d/Disc 1.iso", "--iso", "/d/Disc 2.iso", "--out", "/o", "--code"],
          "dump card -> argv (two discs, a flag, an empty optional left out)")
    try:
        build_argv(dump, {"iso": ["/d/x.iso"]})
        check(False, "a missing required field is refused")
    except ValueError as exc:
        check("required" in str(exc), "a missing required field is refused")
    browse = find_command("browse")
    kinds = next(f for f in browse["fields"] if f["dest"] == "kinds")
    check(kinds["kind"] == "kinds" and "textures" in kinds["choices"], "browse --kinds becomes checkboxes")
    picked = {f"kinds.{k}": ["on"] for k in kinds["choices"][:2]}
    argv = build_argv(browse, {"out": ["/o"], **picked})[len(CLI_ARGV):]
    check(argv == ["browse", "--out", "/o", "--kinds", ",".join(kinds["choices"][:2])], "a subset of kinds")
    patch = find_command("patch")
    out = next(f for f in patch["fields"] if f["dest"] == "out")
    check(out.get("pick") == "save", "patch --out is a disc image to write, not a folder")
    ti = find_command("texture-import")
    ti_out = next(f for f in ti["fields"] if f["dest"] == "out") if ti else {}
    ti_tree = next(f for f in ti["fields"] if f["dest"] == "tree") if ti else {}
    check(ti is not None and ti_out.get("pick") == "save" and ti_out.get("label") == "Output disc"
          and ti_tree.get("pick") == "dir" and "copy of the disc" in ti["short"],
          "texture-import: --out is the output disc (its help says 'copy of the disc'), --tree a folder")
    ti_rep = next(f for f in ti["fields"] if f["dest"] == "report") if ti else {}
    check(ti_rep.get("pick") == "save" and ti_rep.get("suggest") == "textures.csv" and ti_rep.get("prefill") == "",
          "texture-import --report: its picker suggests textures.csv and the field starts empty (never the disc path)")
    # a command cli.py does not have yet: generic fields still work
    fake = argparse.ArgumentParser()
    fs = fake.add_subparsers(dest="cmd")
    p = fs.add_parser("recolor", help="recolour a texture entry in a copy of the disc")
    p.add_argument("--iso", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--hue", type=float, default=0.92)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("entry")
    c = command_schema(fake)[0]
    argv = build_argv(c, {"iso": ["a.iso"], "out": ["b.iso"], "hue": ["0.5"], "dry_run": ["on"],
                          "entry": ["shion00_hair"]})[len(CLI_ARGV):]
    check(argv == ["recolor", "--iso", "a.iso", "--out", "b.iso", "--hue", "0.5", "--dry-run", "shion00_hair"],
          "a new command (options, a float, a flag, a positional) needs no GUI change")
    check(next(f for f in c["fields"] if f["dest"] == "out").get("pick") == "save",
          "its --out is recognised as a disc image from the help text")
    try:
        build_argv(c, {"iso": ["a.iso"], "out": ["b.iso"], "hue": ["pink"], "entry": ["x"]})
        check(False, "a non-number in a float field is refused")
    except ValueError:
        check(True, "a non-number in a float field is refused")
    check(xs2_discs(["/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 2).iso",
                     "/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 1) copy.iso",
                     "/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 1).iso",
                     "/x/Xenosaga Episode I - Der Wille zur Macht (USA).iso"])
          == ["/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 1).iso",
              "/x/Xenosaga Episode II - Jenseits von Gut und Bose (USA) (Disc 2).iso"],
          "disc detection: Disc 1 then Disc 2, copies and other episodes skipped")
    scan = find_command("lang-scan")
    fl = {f["dest"]: f for f in scan["fields"]}
    check(fl["font"].get("pick") == "file" and fl["text"]["kind"] == "multi" and fl["iso"].get("pick") == "file"
          and find_command("lang-encode") and find_command("lang-preview"),
          "lang-scan / lang-encode / lang-preview have cards; the TrueType font is a file picker")
    prev = {f["dest"]: f for f in find_command("lang-preview")["fields"]}
    argv = build_argv(find_command("lang-preview"), {"glyphset": ["/g/glyphset.json"], "line": ["abc"],
                                                     "out": ["/o.png"]})[len(CLI_ARGV):]
    check(prev["out"].get("pick") == "save", "lang-preview's --out is a PNG file to save, not a folder")
    check(prev["line"].get("pick") is None and argv[:5] == ["lang-preview", "--glyphset", "/g/glyphset.json",
                                                            "--text", "abc"],
          "lang-preview's line of text is a text field passed as --text")
    # --- the page's structure: sections, card text, field hints
    known_groups = [g for g, _, _ in GROUPS]
    no_text = sorted(c["name"] for c in cmds if c["name"] not in COMMAND_CARDS
                     or not COMMAND_CARDS[c["name"]].get("summary") or not COMMAND_CARDS[c["name"]].get("title"))
    check(not no_text, "every cli.py subcommand has a plain title and a summary"
          + (f" (missing: {', '.join(no_text)})" if no_text else ""))
    bad_group = sorted(n for n, v in COMMAND_CARDS.items() if v.get("group") not in known_groups)
    check(not bad_group, "every card's section is in GROUPS" + (f" (not: {', '.join(bad_group)})" if bad_group else ""))
    stale = sorted(set(COMMAND_CARDS) - names) + sorted(k for k in COMMAND_HELP if k not in names)
    check(not stale, "the text tables name only real commands" + (f" (not: {', '.join(stale)})" if stale else ""))
    stale_hints = sorted(k for k in FIELD_HINTS if "." in k and k.split(".", 1)[0] not in names)
    check(not stale_hints, "every command.option hint names a real command"
          + (f" (not: {', '.join(stale_hints)})" if stale_hints else ""))
    real_opts = {(c["name"], f["dest"]) for c in cmds for f in c["fields"]}
    dead = sorted(k for k in FIELD_HINTS if "." in k and tuple(k.split(".", 1)) not in real_opts)
    check(not dead, "every command.option hint names a real option" + (f" (not: {', '.join(dead)})" if dead else ""))
    sections = grouped(cmds)
    placed = [n for g in sections for n in g["commands"]]
    check(sorted(placed) == sorted(c["name"] for c in cmds) and len(placed) == len(set(placed)),
          f"every card is in exactly one section ({len(sections)} sections, {len(placed)} cards)")
    order = [g["id"] for g in sections]
    check(order == [g for g in known_groups if g in order] and order[0] == "start" and order[-1] in ("dev", "other"),
          "sections render in GROUPS order: Start here first, developer tools last")
    check([n for g in sections if g["id"] == "start" for n in g["commands"]] == ["dump", "verify", "browse"]
          and [find_command(n)["step"] for n in ("dump", "verify", "browse")] == [1, 2, 3]
          and all(c["step"] is None for c in cmds if c["group"] != "start"),
          "only the Start-here cards are numbered, 1-2-3")
    blurbs = [b for _, _, b in GROUPS if not b]
    check(not blurbs, "every section has a line saying when to use it")
    named = {m for v in COMMAND_CARDS.values() for k in ("needs", "makes", "next") for m in re.findall(r"<b>([^<]+)</b>", v.get(k, ""))}
    titles = {v["title"] for v in COMMAND_CARDS.values()} | {f["label"] for c in cmds for f in c["fields"]}
    loose = sorted(n for n in named if not any(t.startswith(n.split(",")[0].rstrip(".")) for t in titles))
    check(not loose, "every card or field a card's text names exists" + (f" (not: {', '.join(loose)})" if loose else ""))
    # a command the tables do not know: Other section, argparse help as its summary, generic fields
    fake = argparse.ArgumentParser()
    fs = fake.add_subparsers(dest="cmd")
    fs.add_parser("dump", help="unpack")
    fp = fs.add_parser("frobnicate", help="frobnicate the <disc> into a folder")
    fp.add_argument("--iso", required=True)
    fp.add_argument("--out", required=True)
    fp.add_argument("--wide", action="store_true", help="wide mode")
    fake_cmds = command_schema(fake)
    fc = next(c for c in fake_cmds if c["name"] == "frobnicate")
    fsec = grouped(fake_cmds)
    check(fc["group"] == OTHER_GROUP and fc["title"] == "frobnicate" and "&lt;disc&gt;" in fc["summary"]
          and fsec[-1]["id"] == OTHER_GROUP and fsec[-1]["commands"] == ["frobnicate"]
          and fsec[0]["id"] == "start",
          "a command the tables lack lands in Other, its argparse help (escaped) as the summary")
    check(build_argv(fc, {"iso": ["a.iso"], "out": ["/o"], "wide": [""]})[len(CLI_ARGV):]
          == ["frobnicate", "--iso", "a.iso", "--out", "/o"] and output_path(fc, {"out": ["/o"]}) == str(Path("/o")),
          "its fields still build the command line, and its --out is where the output is shown")
    # an on/off pair of flags on one option (--rtl / --ltr): one three-way choice
    enc = find_command("lang-encode")
    rtl = [f for f in enc["fields"] if f["dest"] == "rtl"]
    base = {"text": ["/t"], "glyphset": ["/g.json"], "out": ["/o"]}
    argv0 = build_argv(enc, base)[len(CLI_ARGV):]
    check(len(rtl) == 1 and rtl[0]["kind"] == "tristate" and "--rtl" not in argv0 and "--ltr" not in argv0,
          "lang-encode: --rtl/--ltr are one choice, and an untouched card sends neither")
    check(build_argv(enc, {**base, "rtl": ["on"]})[-1] == "--rtl" and build_argv(enc, {**base, "rtl": ["off"]})[-1] == "--ltr"
          and len([f for f in find_command("lang-preview")["fields"] if f["dest"] == "rtl"]) == 1,
          "the choice sends --rtl or --ltr when picked (lang-preview too)")
    check(argv0 == ["lang-encode", "--text", "/t", "--glyphset", "/g.json", "--out", "/o"],
          "fields left at the command's own default are not sent")
    # per-command hints
    fl = lambda c, d: next(f for f in find_command(c)["fields"] if f["dest"] == d)
    check(fl("model", "out").get("pick") == "save" and fl("model", "out").get("suggest", "").endswith(".glb")
          and fl("font-rebuild", "out").get("pick") == "save" and fl("recolor", "xtx").get("pick") is None,
          "model/font-rebuild --out are files to save; recolor --xtx is an in-game path, not a folder")
    check("kosmos00.pxy" in fl("model", "path")["placeholder"] and ".sb" in fl("sb", "path")["placeholder"]
          and "think" in fl("natives", "glob")["placeholder"],
          "a command.option hint wins over the option's shared hint")
    check(fl("lang-encode", "iso")["prefill"] == "" and fl("natives", "out")["prefill"] == ""
          and fl("exe-import", "iso")["prefill"] == "" and fl("dump", "iso")["prefill"] == "{discs}",
          "optional checks and outputs start empty; required disc images start filled")
    outs = [fl(c, "out")["prefill"] for c in ("text-import", "texture-import", "recolor", "exe-import", "patch")]
    check(len(set(outs)) == len(outs) and all(o.endswith(".iso") for o in outs),
          "every disc-writing card suggests its own output disc name")
    check(fl("text-import", "text")["prefill"] == fl("text-export", "out")["prefill"]
          and fl("exe-import", "text")["prefill"] == fl("exe-export", "out")["prefill"]
          and len({fl(c, "out")["prefill"] for c in ("text-export", "exe-export", "font-extract", "dialogue",
                                                      "lang-scan", "lang-encode")}) == 6,
          "each folder-writing card has its own subfolder, and each importer starts at its exporter's")
    check(output_path(find_command("browse"), {"out": ["/x"]}) == str(Path("/x") / "browse")
          and output_path(find_command("list"), {"iso": ["a"]}) is None,
          "the output shown after a run: browse -> OUT/browse, list -> nothing")
    with tempfile.TemporaryDirectory() as td:
        f1 = Path(td) / "a.iso"
        f1.write_bytes(b"")
        bundle = Path(td) / "Thing.workflow"
        bundle.mkdir()
        ra, rd, rm = reveal_argv(str(f1)), reveal_argv(td), reveal_argv(str(Path(td) / "nope" / "x"))
        rn, rb = reveal_argv(str(Path(td) / "nope")), reveal_argv(str(bundle))
    check(ra is not None and rd is not None and rm is None and rn is None and str(f1) in " ".join(ra)
          and (sys.platform != "darwin" or (ra[:2] == ["open", "-R"] and rb[:2] == ["open", "-R"]
                                            and rd == ["open", td])),
          "reveal: a file or a bundle-like folder is only shown (never opened), a missing path is refused "
          "even when its folder exists")
    check(request_allowed("127.0.0.1:8123", None, 8123) and request_allowed("localhost:8123", "http://localhost:8123", 8123)
          and not request_allowed("127.0.0.1:8123", "https://example.com", 8123)
          and not request_allowed("127.0.0.1:8123", "null", 8123)
          and not request_allowed("evil.example:8123", None, 8123) and not request_allowed("", None, 8123),
          "the server answers only its own page: another site's form post or a foreign Host is refused")
    # disc-writing cards: --out is a disc file, never labelled a folder
    iso_outs = [(c["name"], f["label"], f.get("pick")) for c in cmds if c["writes_iso"]
                for f in c["fields"] if f["dest"] == "out"]
    check(iso_outs and all(lab != "Output folder" and pick == "save" for _, lab, pick in iso_outs)
          and fl("text-import", "out")["label"] == "Output disc",
          "every card that writes a disc labels its --out as an output disc with Save as, not a folder ("
          + ", ".join(n for n, _, _ in iso_outs) + ")")
    # texture-import: the edited folder and the output disc are required, with their stated exceptions
    ti = find_command("texture-import")
    check(fl("texture-import", "tree")["required"] and fl("texture-import", "out")["required"],
          "texture-import: Edited browse folder and Output disc are marked REQUIRED")
    tiform = {"iso": ["a.iso"], "out": ["/o.iso"]}
    try:
        build_argv(ti, tiform)
        check(False, "texture-import without an edited folder is refused")
    except ValueError as exc:
        check("Edited browse folder" in str(exc), "texture-import without an edited folder is refused: " + str(exc))
    check(build_argv(ti, {"iso": ["a.iso"], "tree": ["/t"], "dry_run": ["on"]})[len(CLI_ARGV):]
          == ["texture-import", "--iso", "a.iso", "--tree", "/t", "--dry-run"]
          and build_argv(ti, {"iso": ["a.iso"], "verify_roundtrip": ["on"]})[len(CLI_ARGV):]
          == ["texture-import", "--iso", "a.iso", "--verify-roundtrip"]
          and "--textures" in build_argv(ti, {"iso": ["a.iso"], "out": ["/o.iso"], "textures": ["/p"]}),
          "...but Dry run excuses the output disc, Self-check excuses both, a Pictures folder excuses the browse folder")
    try:
        build_argv(ti, {"iso": ["a.iso"], "tree": ["/t"]})
        check(False, "texture-import without an output disc or Dry run is refused")
    except ValueError as exc:
        check("Output disc" in str(exc) and "Dry run" in str(exc),
              "texture-import without an output disc or Dry run is refused, naming Dry run")
    req_adv = sorted(f"{c['name']}.{f['dest']}" for c in cmds for f in c["fields"] if f["advanced"] and f["required"])
    check(not req_adv, "no REQUIRED field is folded away under More settings"
          + (f" ({', '.join(req_adv)})" if req_adv else ""))
    vis = [f["dest"] for f in ti["fields"] if not f["advanced"]]
    check(vis[:3] == ["iso", "out", "tree"] and len(vis) <= 6 and "verify_roundtrip" not in vis,
          f"texture-import shows {len(vis)} fields up front; the rest are under More settings")
    unknown_unless = sorted(f"{c['name']}.{f['dest']}" for c in cmds for f in c["fields"]
                            for d in f.get("required_unless", ()) if d not in {g["dest"] for g in c["fields"]})
    check(not unknown_unless, "every required_unless names a field of the same card"
          + (f" ({', '.join(unknown_unless)})" if unknown_unless else ""))
    try:
        build_argv(find_command("model"), {"iso": ["a.iso\nb.iso"], "path": ["x"], "out": ["/o.glb"]})
        check(False, "a line break in a one-value field is refused")
    except ValueError as exc:
        check("one line" in str(exc), "a line break in a one-value field is refused")
    check(fl("lang-scan", "text")["prefill"] == "" and fl("lang-encode", "text")["prefill"] == "",
          "the translated-text fields start empty, not at the untranslated export")
    deps = detect_deps()
    check("fonttools" in deps and "uharfbuzz" in deps, "the status panel reports fontTools and uharfbuzz")
    html = page()
    check("__XS1_CSS__" not in html and ".card-head" in html and "/schema" in html
          and "How to use this window" in html and "never changed" in html and "More settings" in html
          and "Loading the cards" in html and "textarea.one" in html, "page assembled")
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if not CLI_TARGET.exists():
        if FROZEN:
            _fatal("CLI binary missing", f"Expected {CLI_TARGET.name} next to the GUI.\n\n"
                   "Unzip the whole release folder first; running from inside the zip fails.")
        else:
            _fatal("cli.py missing", f"Expected cli.py next to gui.py at {CLI_TARGET}.")
    try:
        schema()
    except Exception as exc:
        _fatal("Could not read the CLI's commands", f"{type(exc).__name__}: {exc}")
    port = int(os.environ.get("PORT") or _free_port())
    url = f"http://localhost:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        _fatal("Could not start local server", f"Failed to bind to 127.0.0.1:{port} — {exc}.")
    print(f"[gui] {TITLE}")
    print(f"[gui] Open: {url}")
    print("[gui] Press Ctrl+C to stop.", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[gui] stopping")
    finally:
        server.server_close()
    return 0


XS1_CSS = r"""
:root {
  --bg-deep:        #04070d;
  --bg-mid:         #0a1525;
  --bg-panel:       #0c1828;
  --bg-card:        #0e1c30;
  --bg-input:       #060d18;
  --border:         rgba(93, 213, 255, 0.16);
  --border-strong:  rgba(93, 213, 255, 0.38);
  --fg:             #d8e8f5;
  --muted:          #6e8aab;
  --accent:         #5dd5ff;
  --accent-warm:    #ff9d3a;
  --accent-glow:    rgba(93, 213, 255, 0.35);
  --ok:             #62ffa1;
  --warn:           #ffc15d;
  --err:            #ff6e7f;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background:
    radial-gradient(ellipse 70% 50% at 50% 0%, rgba(93, 213, 255, 0.06) 0%, transparent 70%),
    linear-gradient(180deg, var(--bg-deep) 0%, #02040a 100%);
  color: var(--fg);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 14px; line-height: 1.5;
  overflow: hidden;
}
body::before {
  content: '';
  position: fixed; inset: 0; pointer-events: none; z-index: 1000;
  background: repeating-linear-gradient(180deg,
    transparent 0, transparent 2px,
    rgba(255, 255, 255, 0.012) 2px, rgba(255, 255, 255, 0.012) 3px);
  mix-blend-mode: lighten;
}

header {
  padding: 20px 28px 18px;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 24px;
  background: linear-gradient(180deg, rgba(10, 21, 37, 0.92) 0%, rgba(10, 21, 37, 0.55) 100%);
  position: relative;
  backdrop-filter: blur(2px);
}
header::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent) 50%, transparent);
  opacity: 0.5;
}
header .title { display: flex; flex-direction: column; gap: 2px; line-height: 1; }
header h1 {
  margin: 0;
  font-family: "Cormorant Garamond", "Cormorant", "Georgia", serif;
  font-size: 28px; font-weight: 400; letter-spacing: 0.12em;
  text-transform: uppercase;
}
header h1 .i {
  color: var(--accent-warm);
  font-style: italic; font-weight: 500;
  margin: 0 0.15em;
  text-shadow: 0 0 14px rgba(255, 157, 58, 0.5);
}
header h1 .sep {
  color: var(--accent); opacity: 0.7; margin: 0 0.5em;
  font-weight: 300;
}
header h1 .extractor {
  font-family: "Inter", system-ui, sans-serif;
  font-size: 13px; letter-spacing: 0.35em; color: var(--muted);
  font-style: normal; font-weight: 500;
}
header .tagline {
  color: var(--muted); font-style: italic;
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 14px; letter-spacing: 0.04em;
  margin-top: 4px;
}
header .actions { margin-left: auto; display: flex; gap: 8px; }

main {
  display: grid; grid-template-columns: 1fr 480px;
  height: calc(100vh - 82px);
}
#cards {
  padding: 22px 28px 80px; overflow-y: auto;
}
#cards::-webkit-scrollbar { width: 10px; }
#cards::-webkit-scrollbar-thumb { background: rgba(93, 213, 255, 0.18); border-radius: 5px; }
#cards::-webkit-scrollbar-track { background: transparent; }

#right {
  border-left: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(12, 24, 40, 0.85) 0%, rgba(8, 16, 28, 0.95) 100%);
  display: flex; flex-direction: column; min-height: 0;
}
#right h2 {
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 14px; font-weight: 500;
  margin: 14px 16px 6px;
  color: var(--accent);
  text-transform: uppercase; letter-spacing: 0.22em;
  position: relative; padding-left: 14px;
}
#right h2::before {
  content: ''; position: absolute; left: 0; top: 50%; width: 7px; height: 1px;
  background: var(--accent); transform: translateY(-50%);
  box-shadow: 0 0 6px var(--accent-glow);
}

#status-box { padding: 0 16px 14px; }
#status-box .row {
  display: flex; justify-content: space-between;
  padding: 5px 0; font-size: 13px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}
#status-box .row:last-child { border-bottom: 0; }
#status-box .row span:first-child {
  color: var(--muted); font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase;
}
#status-box .row .v {
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
  color: var(--muted); max-width: 60%;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
#status-box .row .v.ok  { color: var(--ok); }
#status-box .row .v.bad { color: var(--err); }

#log {
  flex: 1; margin: 0; padding: 14px 16px;
  background: #02050b; color: #b8d3eb;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; line-height: 1.55;
  overflow-y: auto; white-space: pre-wrap;
  border-top: 1px solid var(--border);
  min-height: 0;
}
#log::-webkit-scrollbar { width: 8px; }
#log::-webkit-scrollbar-thumb { background: rgba(93, 213, 255, 0.2); border-radius: 4px; }

.card {
  background: linear-gradient(180deg, rgba(14, 28, 48, 0.7) 0%, rgba(10, 21, 37, 0.7) 100%);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px 18px;
  margin-bottom: 12px;
  transition: border-color 0.18s, box-shadow 0.18s;
  position: relative;
}
.card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 2px;
  background: transparent; transition: background 0.18s, box-shadow 0.18s;
  border-radius: 6px 0 0 6px;
}
.card.run { border-color: var(--accent); box-shadow: 0 0 22px rgba(93, 213, 255, 0.12); }
.card.run::before { background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); }
.card.ok  { border-color: rgba(98, 255, 161, 0.4); }
.card.ok::before  { background: var(--ok); }
.card.err { border-color: rgba(255, 110, 127, 0.5); }
.card.err::before { background: var(--err); }

.card-head {
  display: flex; align-items: baseline; gap: 14px;
  cursor: pointer; user-select: none;
}
.card-head h3 {
  margin: 0;
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 18px; font-weight: 500;
  letter-spacing: 0.04em;
}
.card-head .step {
  font-family: "Cormorant Garamond", "Georgia", serif;
  color: var(--accent-warm); font-style: italic;
  font-size: 16px; min-width: 26px;
}
.card-head .badge {
  font-size: 10px; padding: 3px 9px; border-radius: 2px;
  background: rgba(110, 138, 171, 0.12);
  color: var(--muted);
  margin-left: auto;
  text-transform: uppercase; letter-spacing: 0.14em; font-weight: 600;
}
.card-head .badge.run { background: rgba(93, 213, 255, 0.18);  color: var(--accent); }
.card-head .badge.ok  { background: rgba(98, 255, 161, 0.18);  color: var(--ok); }
.card-head .badge.err { background: rgba(255, 110, 127, 0.18); color: var(--err); }
.card-head .chev {
  color: var(--muted); transition: transform 0.2s;
  margin-left: 10px; font-size: 12px;
}
.card.open .chev { transform: rotate(180deg); }

.card-body { display: none; margin-top: 14px; }
.card.open .card-body { display: block; }
.card-body p.help {
  margin: 0 0 14px; color: var(--muted); font-size: 13px; line-height: 1.55;
}
.card-body p.help code { color: var(--accent); font-size: 12px; }

.card .row {
  display: grid; grid-template-columns: 130px 1fr; gap: 12px;
  margin-bottom: 9px; align-items: center;
}
.card .row label {
  color: var(--muted); font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.08em;
}
.card .row .with-pick {
  display: grid; grid-template-columns: 1fr auto; gap: 6px;
}
.card .row .check-wrap {
  display: flex; align-items: center; gap: 10px;
}
.card .row .check-wrap span { color: var(--muted); font-size: 12px; }

.card input[type=text], .card textarea, .card select {
  width: 100%; background: var(--bg-input); color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 3px; padding: 8px 10px;
  font: inherit;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 12.5px; transition: border-color 0.15s, box-shadow 0.15s;
}
.card input[type=text]:focus, .card textarea:focus, .card select:focus {
  outline: none; border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent-glow), 0 0 10px rgba(93, 213, 255, 0.18);
}
.card select option { background: var(--bg-mid); }
.card input[type=checkbox] {
  accent-color: var(--accent); width: 16px; height: 16px;
}
.card textarea { min-height: 60px; resize: vertical; line-height: 1.5; }
.card .actions { margin-top: 14px; display: flex; gap: 8px; }

button {
  background: linear-gradient(180deg, #5dd5ff 0%, #3da8d8 100%);
  color: #02101e; border: 0;
  padding: 8px 16px; border-radius: 3px;
  font: inherit; font-weight: 600; font-size: 12px;
  letter-spacing: 0.1em; text-transform: uppercase;
  cursor: pointer; transition: filter 0.15s, transform 0.05s;
  box-shadow: 0 0 12px rgba(93, 213, 255, 0.25);
}
button:hover { filter: brightness(1.15); }
button:active { transform: translateY(1px); }
button.ghost {
  background: transparent; color: var(--fg);
  border: 1px solid var(--border);
  font-weight: 500; box-shadow: none;
}
button.ghost:hover { background: rgba(93, 213, 255, 0.08); border-color: var(--accent); }
button.pick {
  padding: 7px 12px; font-size: 11px;
  background: rgba(93, 213, 255, 0.12); color: var(--accent);
  border: 1px solid var(--border); box-shadow: none; font-weight: 500;
}
button.pick:hover { background: rgba(93, 213, 255, 0.2); border-color: var(--accent); }
button:disabled { opacity: 0.4; cursor: not-allowed; }

code, kbd {
  background: rgba(93, 213, 255, 0.08); color: var(--accent);
  border-radius: 2px; padding: 1px 5px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
}

/* ------------------------------------------------------------------ */
/* File picker modal                                                   */
/* ------------------------------------------------------------------ */
.modal[hidden] { display: none; }
.modal {
  position: fixed; inset: 0; z-index: 100;
  display: flex; align-items: center; justify-content: center;
}
.modal-backdrop {
  position: absolute; inset: 0;
  background: rgba(2, 5, 11, 0.82);
  backdrop-filter: blur(4px);
  animation: fadeIn 0.15s ease-out;
}
.modal-window {
  position: relative; z-index: 1;
  background: linear-gradient(180deg, var(--bg-card) 0%, var(--bg-mid) 100%);
  border: 1px solid var(--border-strong);
  box-shadow: 0 0 0 1px var(--border), 0 0 80px rgba(93, 213, 255, 0.18), 0 20px 60px rgba(0,0,0,0.5);
  border-radius: 8px;
  width: 760px; max-width: 92vw; height: 580px; max-height: 88vh;
  display: flex; flex-direction: column;
  animation: slideIn 0.18s ease-out;
  overflow: hidden;
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes slideIn {
  from { opacity: 0; transform: translateY(-8px) scale(0.985); }
  to   { opacity: 1; transform: translateY(0) scale(1); }
}
.modal-header {
  padding: 16px 22px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 12px;
  background: linear-gradient(180deg, rgba(93, 213, 255, 0.04), transparent);
}
.modal-header h2 {
  margin: 0;
  font-family: "Cormorant Garamond", "Georgia", serif;
  font-size: 18px; font-weight: 500; letter-spacing: 0.06em;
}
.modal-header .x {
  margin-left: auto; background: transparent; border: 0; color: var(--muted);
  font-size: 22px; cursor: pointer; padding: 0 6px; box-shadow: none;
  text-transform: none; letter-spacing: 0;
}
.modal-header .x:hover { color: var(--err); background: transparent; }
.fs-toolbar {
  padding: 12px 18px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
  background: rgba(2, 5, 11, 0.3);
}
.fs-crumb {
  flex: 1; overflow-x: auto; white-space: nowrap;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px;
}
.fs-crumb a { color: var(--accent); cursor: pointer; padding: 2px 4px; border-radius: 2px; }
.fs-crumb a:hover { background: rgba(93, 213, 255, 0.12); }
.fs-crumb .sep { color: var(--muted); margin: 0 2px; opacity: 0.6; }
.fs-list { flex: 1; overflow-y: auto; padding: 4px 6px; }
.fs-list::-webkit-scrollbar { width: 10px; }
.fs-list::-webkit-scrollbar-thumb { background: rgba(93, 213, 255, 0.18); border-radius: 5px; }
.fs-entry {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 12px; cursor: pointer; border-radius: 3px; font-size: 13px;
}
.fs-entry:hover { background: rgba(93, 213, 255, 0.08); }
.fs-entry.selected { background: rgba(93, 213, 255, 0.18); outline: 1px solid var(--accent); }
.fs-entry .icon { width: 18px; text-align: center; opacity: 0.85; font-size: 14px; }
.fs-entry .icon.dir { color: var(--accent-warm); }
.fs-entry .name { flex: 1; }
.fs-entry .size {
  margin-left: auto; color: var(--muted); font-size: 11px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
}
.fs-empty { padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
.fs-roots {
  border-top: 1px solid var(--border);
  padding: 8px 18px; display: flex; flex-wrap: wrap; gap: 6px;
  font-size: 11px; align-items: center;
}
.fs-roots span { color: var(--muted); text-transform: uppercase; letter-spacing: 0.1em; margin-right: 4px; }
.fs-roots a {
  color: var(--accent); padding: 3px 8px; border-radius: 2px;
  border: 1px solid var(--border); cursor: pointer;
  font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 11px;
}
.fs-roots a:hover { background: rgba(93, 213, 255, 0.12); border-color: var(--accent); }
.modal-footer {
  padding: 14px 18px; border-top: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
  background: rgba(2, 5, 11, 0.3);
}
.modal-footer .sel-path {
  flex: 1; min-width: 0; color: var(--muted); font-size: 12px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.modal-footer .sel-path.has { color: var(--fg); }
.modal-error {
  padding: 8px 18px; color: var(--err); font-size: 12px;
  background: rgba(255, 110, 127, 0.06);
  border-bottom: 1px solid rgba(255, 110, 127, 0.2);
}
.fs-quick {
  padding: 8px 18px; border-bottom: 1px solid var(--border);
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
  background: rgba(93, 213, 255, 0.03);
}
.fs-quick span { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; }
.fs-quick a {
  color: var(--fg); padding: 4px 10px; border-radius: 2px;
  border: 1px solid var(--border); cursor: pointer; font-size: 12px;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis;
}
.fs-quick a:hover { background: rgba(93, 213, 255, 0.12); border-color: var(--accent); color: var(--accent); }
.fs-quick a.selected { background: rgba(93, 213, 255, 0.18); border-color: var(--accent); color: var(--accent); }
"""

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        import traceback
        _fatal("Unexpected error during startup", f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")
