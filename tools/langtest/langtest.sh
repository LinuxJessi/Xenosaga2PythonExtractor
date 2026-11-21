#!/bin/bash
# langtest.sh - the Episode II LANGTEST disc, built from the retail USA Disc 1
# with the kit's own commands, as a user would run them (docs/ALPHABETS.md,
# "Worked example: the LANGTEST disc").
#
#   Hindi    movie subtitles s010100 (font0) and cfe lines sc8001 0006-0008 (font0)
#   Arabic   field box sc1010 0004/0005 (talk.msgDtl) and 0006 (talk.smsg), RTL, A4 pairs
#   Georgian battle names batdat.us S00 @0x0564/@0x0578, field menu MainMenu.txd 0000/0003/0012
#   Amharic  MainMenu.txd 0512 'Save' (AD fixed tier) and BOOT 0x0068b678 'Saving...' (exe-import, last)
#
# usage: tools/langtest/langtest.sh "Disc 1.iso" WORKDIR
#
#   "Disc 1.iso"  the retail USA Disc 1 (only read)
#   WORKDIR       trees, fonts, read-back and build.log; the disc copies go to
#                 WORKDIR/discs and the previews to WORKDIR/previews unless
#                 LANGTEST_DISCS / LANGTEST_PREVIEWS name other folders
#   LANGTEST_FONTS  the folder with SFGeorgian.ttf, Kohinoor.ttc, Supplemental/KefaIII.ttf
#                 and SFArabic.ttf (default /System/Library/Fonts, macOS)
#   PYTHON        the interpreter (default python3)
#
# Every command and its output goes to WORKDIR/build.log; the first failing command
# stops the build. The retail image is only read. The build needs room for two disc
# copies at once (about 9 GB); the intermediate copies are removed as it goes, and the
# finished LANGTEST image is the only one left.
set -u
if [ $# -ne 2 ]; then
    sed -n '11,25p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
KIT="$(cd "$(dirname "$0")/../.." && pwd)"         # the kit (this repository)
RETAIL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
mkdir -p "$2" || exit 1
W="$(cd "$2" && pwd)"                               # trees, fonts, read-back (not in the repo)
D="${LANGTEST_DISCS:-$W/discs}"
P="${LANGTEST_PREVIEWS:-$W/previews}"
F="${LANGTEST_FONTS:-/System/Library/Fonts}"
LOG="$W/build.log"
PY="${PYTHON:-python3}"
[ -f "$RETAIL" ] || { echo "no such disc image: $RETAIL"; exit 1; }

mkdir -p "$W" "$D" "$P"
D="$(cd "$D" && pwd)"; P="$(cd "$P" && pwd)"
FINAL="$D/Xenosaga Episode II (USA) (Disc 1) (LANGTEST).iso"
cd "$KIT" || exit 1
: > "$LOG"
run() {
    printf '\n$' >> "$LOG"; printf ' %q' "$@" >> "$LOG"; printf '\n' >> "$LOG"
    "$@" >> "$LOG" 2>&1
    rc=$?
    echo "[exit $rc]" >> "$LOG"
    if [ $rc -ne 0 ]; then echo "FAILED: $*  (see $LOG)"; exit $rc; fi
}
echo "# LANGTEST build $(date '+%Y-%m-%d %H:%M:%S'), kit $(git -C "$KIT" rev-parse --short HEAD 2>/dev/null || echo unknown)" >> "$LOG"
run shasum -a 256 "$RETAIL"
rm -rf "$W/export" "$W/exe-export" "$W/tr" "$W/tr-exe" "$W/font-ka" "$W/font-hi" "$W/font-am" "$W/font-ar" \
       "$W/tr-enc" "$W/tr-exe-enc" "$W/readback"
rm -f "$D/step1.iso" "$D/step2.iso" "$FINAL"
rm -f "$P"/*.png

# 0. export the retail text and executable strings
run $PY cli.py text-export --iso "$RETAIL" --out "$W/export"
run $PY cli.py exe-export --iso "$RETAIL" --out "$W/exe-export"

# 1. translate: the tree holds the manifest and only the files that change (the
#    translator's step; each block must still hold the retail English given here)
mkdir -p "$W/tr" "$W/tr-exe"
cp "$W/export/xs2-text-export.json" "$W/tr/"
cp "$W/exe-export/xs2-exe-export.json" "$W/tr-exe/"
for f in chain0/cf/en/sc1010.sbl.txt chain0/cf/en/sc8001.sbl.txt chain1/movie/en/s010100.sub.txt \
         chain0/yamamoto/batdat.us.txt chain0/yosikawa/MainMenu.txd.txt; do
    mkdir -p "$(dirname "$W/tr/$f")"; cp "$W/export/$f" "$W/tr/$f"
done
cp "$W/exe-export/BOOT.exe.txt" "$W/tr-exe/"
run $PY - "$W" <<'EOF'
import sys
from pathlib import Path
W = Path(sys.argv[1])
# (file, block header or cue time, retail English it must start with, translation)
EDITS = [
    ("tr/chain1/movie/en/s010100.sub.txt", "00:14:15", "What you're seeing here is actual footage from",
     "आप यहाँ जो देख रहे हैं, वह असली फुटेज है"),
    ("tr/chain1/movie/en/s010100.sub.txt", "00:16:20", r"the violence that erupted\nbefore dawn this morning.",
     r"आज सुबह भोर से पहले भड़की\nहिंसा का।"),
    ("tr/chain1/movie/en/s010100.sub.txt", "00:19:14",
     r"These seemingly coordinated Realian attacks\nwere reported throughout Federation",
     r"ये सुनियोजित-से लगने वाले रियलियन हमले\nपूरे फ़ेडरेशन में दर्ज किए गए"),
    ("tr/chain0/cf/en/sc8001.sbl.txt", "### 0006", "Looks like we're pretty far out from our objective.",
     "लगता है हम अपने लक्ष्य से काफ़ी दूर हैं।"),
    ("tr/chain0/cf/en/sc8001.sbl.txt", "### 0007", r"Plus, I'd say the defensive network \nwe saw",
     r"और नीचे आते समय जो रक्षा-तंत्र दिखा,\nउसके रहते हवाई रास्ते से\nजाना संभव नहीं।"),
    ("tr/chain0/cf/en/sc8001.sbl.txt", "### 0008", "We'll go by ground then.",
     "तो हम ज़मीन के रास्ते चलेंगे।"),
    ("tr/chain0/cf/en/sc1010.sbl.txt", "### 0004", "[Canaan]What is this thing?", "[Canaan]ما هذا الشيء؟"),
    ("tr/chain0/cf/en/sc1010.sbl.txt", "### 0005", "[chaos]It's a save point.", "[chaos]إنها نقطة حفظ."),
    ("tr/chain0/cf/en/sc1010.sbl.txt", "### 0006", "[chaos]If you touch it and press <○>,",
     "[chaos]إذا لمستها وضغطت <○>، يمكنك تسجيل تقدمك الحالي واستعادة صحتك."),
    ("tr/chain0/yamamoto/batdat.us.txt", "### S00 @0x0564 max 5 bytes", "chaos", "ქაოსი"),
    ("tr/chain0/yamamoto/batdat.us.txt", "### S00 @0x0578 max 3 bytes", "Jin", "ჯინ"),
    ("tr/chain0/yosikawa/MainMenu.txd.txt", "### 0000", "chaos", "ქაოსი"),
    ("tr/chain0/yosikawa/MainMenu.txd.txt", "### 0003", "Jin", "ჯინ"),
    ("tr/chain0/yosikawa/MainMenu.txd.txt", "### 0012", "Canaan", "ქანაანი"),
    ("tr/chain0/yosikawa/MainMenu.txd.txt", "### 0512", "Save", "አስቀምጥ"),
    ("tr-exe/BOOT.exe.txt", "### @0x0068b678 max 15 bytes", "Saving...", "በማስቀመጥ ላይ..."),
]
for rel, key, old, new in EDITS:
    p = W / rel
    lines = p.read_text(encoding="utf-8").split("\n")
    if key.startswith("###"):
        i = lines.index(key) + 1
        while lines[i].startswith(("# ", "#sjis: ", "#euc: ")):   # notes under the header (refs, hints)
            i += 1
        body_at = i
        cur = lines[i]
    else:
        body_at = next(i for i, ln in enumerate(lines) if ln.startswith(key + "\t"))
        cur = lines[body_at].split("\t", 1)[1]
    assert cur.startswith(old), f"{rel} {key}: expected {old!r}, found {cur!r}"
    lines[body_at] = new if key.startswith("###") else key + "\t" + new
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"{rel} {key}: {cur!r} -> {new!r}")
EOF

# 2. glyphs: one scan per script, each extending the one before (Geor, Deva, Ethi, Arab).
#    Byte-limited text (battle names, subtitles, the exe string) takes single codes first.
run $PY cli.py lang-scan --iso "$RETAIL" --text "$W/tr" --font "$F/SFGeorgian.ttf" \
    --script Geor --language ka --out "$W/font-ka"
# --px 17.5: at the default 18 the u-sign under सु/फु and the candrabindu of हाँ leave the 24-row
# cell, and lang-scan refuses a glyph that would lose ink (docs/ALPHABETS.md)
run $PY cli.py lang-scan --iso "$RETAIL" --text "$W/tr" --font "$F/Kohinoor.ttc" --face 0 --px 17.5 \
    --script Deva --language hi --extend "$W/font-ka/glyphset.json" \
    --font0 "$W/font-ka/font0.us" --font1 "$W/font-ka/font1.us" --out "$W/font-hi"
run $PY cli.py lang-scan --iso "$RETAIL" --text "$W/tr" --text "$W/tr-exe" --font "$F/Supplemental/KefaIII.ttf" \
    --script Ethi --language am --prefer fixed --extend "$W/font-hi/glyphset.json" \
    --font0 "$W/font-hi/font0.us" --font1 "$W/font-hi/font1.us" --out "$W/font-am"
run $PY cli.py lang-scan --iso "$RETAIL" --text "$W/tr" --font "$F/SFArabic.ttf" \
    --script Arab --language ar --rtl --prefer pair --extend "$W/font-am/glyphset.json" \
    --font0 "$W/font-am/font0.us" --font1 "$W/font-am/font1.us" --out "$W/font-ar"

# 3. encode both trees with the last glyphset (it holds all four scripts).
#    The plan's starting values were bank=360 and the default 9 px space; the field
#    window's measured values (wrap at 412-446 px, $xx1,4 = 5 px space; ALPHABETS.md)
#    are used instead, so the RTL right edge lines up.
run $PY cli.py lang-encode --text "$W/tr" --glyphset "$W/font-ar/glyphset.json" --out "$W/tr-enc" \
    --width-kind bank=410 --width-kind subtitle=440 --align right --space-px 5 --iso "$RETAIL"
run $PY cli.py lang-encode --text "$W/tr-exe" --glyphset "$W/font-ar/glyphset.json" --out "$W/tr-exe-enc" \
    --width-kind bank=410 --width-kind subtitle=440 --align right --space-px 5 --iso "$RETAIL"

# 4. into copies of the disc: the fonts, the text, the executable string (last).
#    Each step writes a new copy; the one before is removed once the next exists (disk room).
run $PY cli.py patch --iso "$RETAIL" --out "$D/step1.iso" \
    --set "chain0:font0.us=$W/font-ar/font0.us" --set "chain0:font1.us=$W/font-ar/font1.us"
run $PY cli.py text-import --iso "$D/step1.iso" --text "$W/tr-enc" --out "$D/step2.iso"
rm -f "$D/step1.iso"
run $PY cli.py exe-import --iso "$D/step2.iso" --text "$W/tr-exe-enc" --out "$FINAL"
rm -f "$D/step2.iso"

# 5. previews: the new glyph cells (one sheet per scan) and every changed line laid out
#    with the game's width rules, in the face and state that draws it
for s in ka hi am ar; do cp "$W/font-$s/sheet0_preview.png" "$P/cells-after-$s.png"; done
G="$W/font-ar/glyphset.json"
pv() { run $PY cli.py lang-preview --glyphset "$G" "$@"; }
B="--face font1 --kind bank --width 410 --align right --space-px 5"
pv $B --out "$P/ar-sc1010-0004.png" --text '[Canaan]ما هذا الشيء؟'
pv $B --out "$P/ar-sc1010-0005.png" --text '[chaos]إنها نقطة حفظ.'
pv $B --out "$P/ar-sc1010-0006.png" --text '[chaos]إذا لمستها وضغطت <○>، يمكنك تسجيل تقدمك الحالي واستعادة صحتك.'
pv --face font0 --kind subtitle --out "$P/hi-s010100-1415.png" --text 'आप यहाँ जो देख रहे हैं, वह असली फुटेज है'
pv --face font0 --kind subtitle --out "$P/hi-s010100-1620.png" --text 'आज सुबह भोर से पहले भड़की\nहिंसा का।'
pv --face font0 --kind subtitle --out "$P/hi-s010100-1914.png" --text 'ये सुनियोजित-से लगने वाले रियलियन हमले\nपूरे फ़ेडरेशन में दर्ज किए गए'
pv --face font0 --kind bank --out "$P/hi-sc8001-0006.png" --text 'लगता है हम अपने लक्ष्य से काफ़ी दूर हैं।'
pv --face font0 --kind bank --out "$P/hi-sc8001-0007.png" --text 'और नीचे आते समय जो रक्षा-तंत्र दिखा,\nउसके रहते हवाई रास्ते से\nजाना संभव नहीं।'
pv --face font0 --kind bank --out "$P/hi-sc8001-0008.png" --text 'तो हम ज़मीन के रास्ते चलेंगे।'
pv --face font1 --kind battle --out "$P/ka-batdat-0564.png" --text 'ქაოსი'
pv --face font1 --kind battle --out "$P/ka-batdat-0578.png" --text 'ჯინ'
pv --face font1 --kind menu --out "$P/ka-menu-0000.png" --text 'ქაოსი'
pv --face font1 --kind menu --out "$P/ka-menu-0003.png" --text 'ჯინ'
pv --face font1 --kind menu --out "$P/ka-menu-0012.png" --text 'ქანაანი'
pv --face font1 --kind menu --out "$P/am-menu-0512.png" --text 'አስቀምጥ'
pv --face font1 --kind exe --out "$P/am-exe-68b678.png" --text 'በማስቀመጥ ላይ...'

# 6. read-back from the finished disc, then the checks (encoded bytes, untouched strings,
#    font cells, the retail image unchanged)
run $PY cli.py text-export --iso "$FINAL" --out "$W/readback/text"
run $PY cli.py exe-export --iso "$FINAL" --out "$W/readback/exe"
run $PY "$KIT/tools/langtest/langtest-verify.py" "$W" "$RETAIL" "$FINAL" "$P"
run shasum -a 256 "$RETAIL" "$FINAL"
echo "built: $FINAL"
