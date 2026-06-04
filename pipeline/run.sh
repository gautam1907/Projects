#!/usr/bin/env bash
# run.sh — Process all CCTV clips → events/events.jsonl
# Usage:  STORE_ID=STORE_BLR_002 CLIPS_DIR=./clips bash pipeline/run.sh

set -euo pipefail

STORE_ID="${STORE_ID:-STORE_BLR_002}"
CLIPS_DIR="${CLIPS_DIR:-./clips}"
OUTPUT="${OUTPUT:-events/events.jsonl}"
CLIP_START="${CLIP_START:-2026-03-03T12:00:00Z}"

mkdir -p "$(dirname "$OUTPUT")"
> "$OUTPUT"   # truncate

echo "======================================================"
echo " Store Intelligence — Detection Pipeline"
echo " Store    : $STORE_ID"
echo " Clips    : $CLIPS_DIR"
echo " Output   : $OUTPUT"
echo " Clip start: $CLIP_START"
echo "======================================================"

# Check Python deps
python3 -c "from ultralytics import YOLO" 2>/dev/null \
  || echo "[warn] ultralytics not installed — run: pip install ultralytics"

run_clip() {
    local video="$1"
    [ -f "$video" ] || return
    echo ""
    echo "[run] Processing: $(basename "$video")"
    python3 pipeline/detect.py \
        --video       "$video" \
        --store-id    "$STORE_ID" \
        --clip-start  "$CLIP_START" \
        --output      "$OUTPUT"
}

# Process in logical order: entry → zone → billing
for pattern in "*entry*" "*zone*" "*billing*" "*bill*"; do
    for f in "$CLIPS_DIR"/$pattern; do
        run_clip "$f" 2>/dev/null || true
    done
done

# Catch anything not matched above
for f in "$CLIPS_DIR"/*.mp4 "$CLIPS_DIR"/*.MP4; do
    run_clip "$f" 2>/dev/null || true
done

COUNT=$(wc -l < "$OUTPUT" 2>/dev/null || echo 0)
echo ""
echo "======================================================"
echo " Done. Events emitted: $COUNT"
echo " Output: $OUTPUT"
echo "======================================================"
echo ""
echo "Ingest into API:"
echo "  python3 pipeline/ingest_events.py --file $OUTPUT --api http://localhost:8000"
