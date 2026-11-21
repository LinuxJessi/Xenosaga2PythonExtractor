#!/usr/bin/env bash
# End-to-end demo: Episode II motion (.xap / .fap) -> glTF animations (docs/MOTION.md).
# Every command a user would type, in order. Disc images and the dump are only read.
#
# usage: tools/demo/motion.sh "Disc 1.iso" DUMP OUTDIR
#
#   "Disc 1.iso"  the USA Disc 1
#   DUMP          a `cli.py dump` of that disc (the folder holding chain0/)
#   OUTDIR        where the .glb files, contact sheets and reports go
#   BLENDER       Blender's executable for step 6 (default: `blender` on PATH, else
#                 the macOS app in /Applications); skipped when there is none
#   PYTHON        the interpreter (default python3)
#
# Step 5 needs node and npm: it installs the Khronos glTF validator into a private
# folder under $TMPDIR (not a kit dependency).
set -euo pipefail
if [ $# -ne 3 ]; then
    sed -n '5,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
KIT="$(cd "$HERE/../.." && pwd)"                # the kit (this repository)
ISO1="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
DUMP="$(cd "$2" && pwd)"
mkdir -p "$3"
OUT="$(cd "$3" && pwd)"
BLENDER="${BLENDER:-$(command -v blender || echo /Applications/Blender.app/Contents/MacOS/Blender)}"
PY="${PYTHON:-python3}"
cd "$KIT"

# 0. the kit's own tests (xap + gltf self-tests are part of `cli.py self-test`)
$PY xap.py --self-test
$PY cli.py self-test

# 1. disc-wide decode sweep of every .xap / .fap (read-only, from the dump)
$PY xap.py --sweep "$DUMP" > "$OUT/sweep.txt"

# 2. one file's clip table, as a user would look before choosing slots
$PY xap.py "$DUMP/chain0/motion/shion00.xap" > "$OUT/shion00_xap_table.txt"

# 3. the three models with their field clips (slots 0-2) and battle clips,
#    exactly as a user exports them:
$PY cli.py model --iso "$ISO1" --path 'model\char\pc\shion00.pxy' --out "$OUT/shion00.glb" \
    --motion 'motion\shion00.xap#0,1,2' --motion 'yamamoto\mot\shio00.xap#0,1' \
    --motion 'motion\face_all.fap#0'
$PY cli.py model --iso "$ISO1" --path 'model\char\pc\kosmos00.pxy' --out "$OUT/kosmos00.glb" \
    --motion 'motion\kosmos00.xap#0,1,2' --motion 'yamamoto\mot\kosm00.xap#0,1'
$PY cli.py model --iso "$ISO1" --path 'model\char\pc\jr00.pxy' --out "$OUT/jr00.glb" \
    --motion 'motion\jr00.xap#0,1,2'
#    and the default pairing (every same-stem .xap) for one of them
$PY cli.py model --iso "$ISO1" --path 'model\char\pc\shion00.pxy' --out "$OUT/shion00_default.glb"

# 4. read-back checks + contact sheets (re-runs the same three `cli.py model`
#    commands, then reads the .glb back through gltf.py and compares with
#    xap.pose; numpy rasteriser): report.txt / report.json / *_NN.png
$PY "$HERE/motion/make_demo.py" "$ISO1" "$DUMP" "$OUT"

# 5. Khronos glTF validator (npm package, private temp dir, not a kit dependency)
GV="${TMPDIR:-/tmp}/xs2-gltf-validator"
if [ ! -d "$GV/node_modules/gltf-validator" ]; then
    mkdir -p "$GV" && (cd "$GV" && npm init -y >/dev/null && npm install --silent gltf-validator@2.0.0-dev.3.10)
fi
node "$HERE/motion/khronos_validate.js" "$GV" "$OUT/shion00.glb" "$OUT/kosmos00.glb" "$OUT/jr00.glb" \
    "$OUT/shion00_default.glb" | tee "$OUT/khronos_validator.txt"

# 6. Blender (background): import check + one Workbench render per file
if [ -x "$BLENDER" ]; then
    "$BLENDER" -b --factory-startup --python "$HERE/motion/blender_check.py" -- \
        "$OUT/shion00.glb" "$OUT/kosmos00.glb" "$OUT/jr00.glb" > "$OUT/blender_import.txt" 2>&1
    tail -n 30 "$OUT/blender_import.txt"
    "$BLENDER" -b --factory-startup --python "$HERE/motion/blender_render.py" -- \
        "$OUT/shion00.glb" "$OUT/kosmos00.glb" "$OUT/jr00.glb" > "$OUT/blender_render.txt" 2>&1
else
    echo "no Blender at $BLENDER: step 6 skipped (set BLENDER)"
fi
echo "motion demo done: $OUT"
