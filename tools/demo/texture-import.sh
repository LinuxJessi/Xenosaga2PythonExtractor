#!/usr/bin/env bash
# End-to-end demo: edited PNGs (a character texture, an outfit texture, a UI sheet and a
# model .glb image) back into a COPY of Episode II Disc 1 with `cli.py texture-import`,
# then re-extracted from that copy and checked (docs/MODDING.md, "Editing textures").
# Every command a user would type, in order. The retail image is only read.
#
# usage: tools/demo/texture-import.sh "Disc 1.iso" EXTRACT OUTDIR
#
#   "Disc 1.iso"  the retail USA Disc 1 (only read)
#   EXTRACT       a full `cli.py dump` of that disc (the folder holding dump/ and
#                 manifest.csv); step 8 builds a full browse tree from it, read only
#   OUTDIR        logs, PNGs and checks; the modded copy goes to OUTDIR/discs (about
#                 4.4 GB; delete it when done) and the full browse tree to
#                 OUTDIR/fulltree (1 GB and more) unless TEXDEMO_DISCS / TEXDEMO_FULLTREE
#                 name other folders
#   PYTHON        the interpreter (default python3)
set -euo pipefail
if [ $# -ne 3 ]; then
    sed -n '7,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT="$(cd "$HERE/../.." && pwd)"                # the kit (this repository)
ISO1="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
EXTRACT="$(cd "$2" && pwd)"                     # existing full dump, read only
mkdir -p "$3"
OUT="$(cd "$3" && pwd)"
DISCS="${TEXDEMO_DISCS:-$OUT/discs}"
mkdir -p "$DISCS"
DISCS="$(cd "$DISCS" && pwd)"
MOD="$DISCS/xs2-d1-textures.iso"
NOOP="$DISCS/xs2-d1-noop.iso"                   # must NOT be created (step 8)
FULL="${TEXDEMO_FULLTREE:-$OUT/fulltree}"       # full browse tree of Disc 1
mkdir -p "$(dirname "$FULL")"
FULL="$(cd "$(dirname "$FULL")" && pwd)/$(basename "$FULL")"
PY="${PYTHON:-python3}"
STATUS=0
cd "$KIT"
echo "kit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown) on $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo -)" | tee "$OUT/kit.log"

# 0. the kit's own tests
$PY cli.py self-test > "$OUT/selftest.log" 2>&1 || STATUS=1
tail -3 "$OUT/selftest.log"
shasum "$ISO1" | tee "$OUT/retail-sha1.before"

# 1. what a user does first: pull the files out of the retail disc and convert them
rm -rf "$OUT/retail" "$OUT/reextract" "$OUT/preview"
$PY cli.py dump   --iso "$ISO1" --glob 'model\char\pc\kosmos0*'  --out "$OUT/retail/char"
$PY cli.py dump   --iso "$ISO1" --glob 'yajima\titleobj.xtx'     --out "$OUT/retail/title"
$PY cli.py browse --out "$OUT/retail/char"  --kinds textures,models
$PY cli.py browse --out "$OUT/retail/title" --kinds xtx

# 2. "paint": paint.py stands in for an image editor and writes a sparse edited tree
$PY "$HERE/textures/paint.py" "$OUT/edited" "$OUT/retail/char/browse" "$OUT/retail/title/browse" | tee "$OUT/paint.log"

# 3. look before writing
$PY cli.py texture-import --iso "$ISO1" --tree "$OUT/edited" --dry-run 2>&1 | tee "$OUT/dryrun.log"

# 4. the real write, onto a copy (the retail image is only read)
$PY cli.py texture-import --iso "$ISO1" --tree "$OUT/edited" --out "$MOD" \
    --preview "$OUT/preview" --report "$OUT/report.csv" 2>&1 | tee "$OUT/import.log"

# 5. re-extract from the modded copy exactly as from a retail disc
$PY cli.py dump   --iso "$MOD" --glob 'model\char\pc\kosmos0*'  --out "$OUT/reextract/char"
$PY cli.py dump   --iso "$MOD" --glob 'yajima\titleobj.xtx'     --out "$OUT/reextract/title"
$PY cli.py browse --out "$OUT/reextract/char"  --kinds textures,models
$PY cli.py browse --out "$OUT/reextract/title" --kinds xtx

# 6. before/after PNGs, contact sheet, the edited and re-extracted .glb
$PY "$HERE/textures/checks.py" collect "$OUT/report.csv" "$OUT/preview" "$OUT/edited" "$OUT" \
    --before "$OUT/retail/char/browse" "$OUT/retail/title/browse" \
    --after "$OUT/reextract/char/browse" "$OUT/reextract/title/browse" 2>&1 | tee "$OUT/collect.log" || STATUS=1

# 7. whole-disc checks: unrelated objects byte-identical, every duplicate changed, byte ranges
$PY "$HERE/textures/checks.py" disc "$ISO1" "$MOD" "$OUT/report.csv" 2>&1 | tee "$OUT/disc-check.log" || STATUS=1

# 8. an unedited full browse tree changes nothing (and writes nothing)
KITREV="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if [ ! -d "$FULL/browse" ] || [ "$(cat "$FULL/kit-rev" 2>/dev/null)" != "$KITREV" ]; then
    rm -rf "$FULL"; mkdir -p "$FULL"
    ln -s "$EXTRACT/dump" "$FULL/dump"; ln -s "$EXTRACT/manifest.csv" "$FULL/manifest.csv"
    $PY cli.py browse --out "$FULL" --kinds textures,xtx,models 2>&1 | tee "$OUT/fulltree-browse.log"
    echo "$KITREV" > "$FULL/kit-rev"
fi
rm -f "$NOOP"
$PY cli.py texture-import --iso "$ISO1" --tree "$FULL/browse" --out "$NOOP" 2>&1 | tee "$OUT/noop.log"
if [ -e "$NOOP" ] || ! grep -q "^plan: 0 changed entries" "$OUT/noop.log"; then
    echo "FAIL: the unedited full tree planned changes or wrote $NOOP" | tee -a "$OUT/noop.log"; STATUS=1
else
    echo "PASS: unedited full tree: 0 changed entries, $NOOP not created" | tee -a "$OUT/noop.log"
fi

# 9. the retail image is untouched
shasum "$ISO1" | tee "$OUT/retail-sha1.after"
if cmp -s "$OUT/retail-sha1.before" "$OUT/retail-sha1.after"; then echo "PASS: retail Disc 1 unchanged"
else echo "FAIL: retail Disc 1 hash changed"; STATUS=1; fi

echo "PCSX2 boot check: PENDING (user)"
exit $STATUS
