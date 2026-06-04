"""
detect.py — Main detection + tracking script for Store Intelligence Pipeline.
Uses YOLOv8 for person detection, IoU-based ByteTrack-style tracking,
and histogram-based Re-ID for re-entry detection.

# PROMPT USED (AI-Assisted):
# "Design a retail CCTV detection pipeline in Python using YOLOv8 + ByteTrack.
#  Requirements: detect persons, assign visitor tokens, detect entry/exit via
#  line crossing, classify staff by color histogram, handle group entry,
#  emit structured JSON events per provided schema."
# CHANGES MADE:
#  - Added 30s grace-period re-entry window instead of naive new ID per detection
#  - Staff uses color + movement-distance dual signal (AI suggested pose-only)
#  - Confidence passthrough — low-conf events emitted, never suppressed
#  - 1 fps sampling for zone cameras, 5 fps for entry (AI suggested same rate)
"""

import cv2
import json
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from tracker import PersonTracker
from emit import EventEmitter

LAYOUT_PATH = Path(__file__).parent.parent / "store_layout.json"


def load_layout(store_id: str) -> dict:
    with open(LAYOUT_PATH) as f:
        all_layouts = json.load(f)
    return all_layouts.get(store_id, list(all_layouts.values())[0])


def get_camera_meta(video_stem: str, layout: dict) -> tuple[str, str]:
    """Return (camera_id, camera_type) from video filename."""
    s = video_stem.lower()
    if "entry" in s:
        return layout.get("entry_camera", "CAM_3"), "entry"
    if "billing" in s or "bill" in s:
        return layout.get("billing_camera", "CAM_5"), "billing"
    return layout.get("zone_cameras", ["CAM_1"])[0], "zone"


def classify_staff(frame, bbox, positions: list) -> tuple[bool, float]:
    """
    Two-signal staff classifier: uniform color + movement distance.
    Returns (is_staff, confidence).
    """
    x1, y1, x2, y2 = [max(0, int(v)) for v in bbox]
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return False, 0.5

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    dark_blue = cv2.inRange(hsv, np.array([100, 50, 20]), np.array([160, 255, 100]))
    black     = cv2.inRange(hsv, np.array([0,   0,  0]),  np.array([180,  50,  60]))
    uniform_ratio = np.sum(cv2.bitwise_or(dark_blue, black) > 0) / (crop.shape[0] * crop.shape[1] + 1e-6)

    movement_score = 0.0
    if len(positions) >= 5:
        pts = np.array(positions[-10:])
        total_dist = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1)))
        movement_score = min(total_dist / 600.0, 1.0)

    score = 0.6 * uniform_ratio + 0.4 * movement_score
    return score > 0.42, round(min(score + 0.3, 0.99), 3)


def detect_crossing(positions: list, line_y: float) -> str | None:
    """Return 'ENTRY', 'EXIT', or None based on line crossing."""
    if len(positions) < 3:
        return None
    py, cy = positions[-3][1], positions[-1][1]
    if (py < line_y) == (cy < line_y):
        return None
    return "ENTRY" if cy > py else "EXIT"


def assign_zone(cx_norm: float, zones: list) -> str | None:
    """Simple equal-width horizontal zone assignment from normalised x."""
    if not zones:
        return None
    idx = min(int(cx_norm * len(zones)), len(zones) - 1)
    return zones[idx]["zone_id"]


def process_video(video_path: str, store_id: str,
                  clip_start: datetime, output_path: str):
    layout = load_layout(store_id)
    cam_id, cam_type = get_camera_meta(Path(video_path).stem, layout)
    entry_line_y_norm = layout.get("entry_line_y_norm", 0.83)
    zone_defs = [z for z in layout["zones"] if z["zone_id"] != "BILLING_QUEUE"]
    billing_zone_id = "BILLING_QUEUE"

    # Load model
    try:
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")
    except Exception:
        model = None
        print("[detect] YOLOv8 not found — no detections will run")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[detect] Cannot open: {video_path}")
        return []

    fps   = cap.get(cv2.CAP_PROP_FPS) or 15.0
    fw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    entry_line_y = entry_line_y_norm * fh

    # Sample rate: entry cameras need finer resolution
    sample_every = max(1, int(fps / (5 if cam_type == "entry" else 1)))

    tracker = PersonTracker(fps=fps)
    emitter = EventEmitter(store_id=store_id, camera_id=cam_id)
    events  = []
    frame_idx = 0

    print(f"[detect] {Path(video_path).name} | cam={cam_id} | type={cam_type}")

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        if frame_idx % sample_every != 0:
            continue

        frame_ts = clip_start + timedelta(seconds=frame_idx / fps)

        # ── Detect ──
        dets = []
        if model is not None:
            try:
                res = model(frame, classes=[0], verbose=False, conf=0.30)[0]
                for box in res.boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    dets.append({
                        "bbox": [x1, y1, x2, y2],
                        "conf": float(box.conf[0]),
                        "centroid": ((x1+x2)/2, (y1+y2)/2),
                    })
            except Exception as e:
                print(f"[detect] Inference error frame {frame_idx}: {e}")

        # ── Track ──
        tracks = tracker.update(dets, frame_ts)

        for t in tracks:
            tid      = t["track_id"]
            vid      = t["visitor_id"]
            bbox     = t["bbox"]
            centroid = t["centroid"]
            conf     = t["conf"]
            positions = t["positions"]
            is_reentry = t.get("is_reentry", False)

            is_staff, s_conf = classify_staff(frame, bbox, positions)
            final_conf = round(conf * max(s_conf, 0.5), 3)

            # ── Entry camera: line-crossing ──
            if cam_type == "entry":
                direction = detect_crossing(positions, entry_line_y)
                if direction:
                    etype = "REENTRY" if (is_reentry and direction == "ENTRY") else direction
                    ev = emitter.emit(etype, tid, frame_ts, is_staff, final_conf, None,
                                      visitor_id=vid)
                    events.append(ev)

            # ── Zone camera: zone enter/exit/dwell ──
            elif cam_type == "zone":
                cx_norm = centroid[0] / fw
                zone_id = assign_zone(cx_norm, zone_defs)
                prev    = tracker.get_zone(tid)
                if zone_id and zone_id != prev:
                    if prev:
                        dwell = tracker.get_dwell_ms(tid, frame_ts)
                        events.append(emitter.emit(
                            "ZONE_EXIT", tid, frame_ts, is_staff, final_conf, prev,
                            dwell_ms=dwell, visitor_id=vid))
                        if dwell >= 30_000:
                            events.append(emitter.emit(
                                "ZONE_DWELL", tid, frame_ts, is_staff, final_conf, prev,
                                dwell_ms=dwell, visitor_id=vid))
                    events.append(emitter.emit(
                        "ZONE_ENTER", tid, frame_ts, is_staff, final_conf, zone_id,
                        visitor_id=vid))
                    tracker.set_zone(tid, zone_id, frame_ts)

            # ── Billing camera ──
            elif cam_type == "billing":
                if not tracker.in_billing(tid):
                    qdepth = tracker.billing_count()
                    if qdepth > 0:
                        events.append(emitter.emit(
                            "BILLING_QUEUE_JOIN", tid, frame_ts, is_staff, final_conf,
                            billing_zone_id, queue_depth=qdepth + 1, visitor_id=vid))
                    tracker.mark_billing(tid, frame_ts)

        # Flush disappeared tracks → EXIT / BILLING_QUEUE_ABANDON
        for tid, info in tracker.flush_stale(frame_ts).items():
            if cam_type == "entry":
                events.append(emitter.emit(
                    "EXIT", tid, frame_ts, info.get("is_staff", False), 0.70, None,
                    visitor_id=info["visitor_id"]))
            elif cam_type == "billing" and tracker.in_billing(tid):
                events.append(emitter.emit(
                    "BILLING_QUEUE_ABANDON", tid, frame_ts, info.get("is_staff", False),
                    0.72, billing_zone_id, visitor_id=info["visitor_id"]))
                tracker.clear_billing(tid)

    cap.release()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "a") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    print(f"[detect] ✓  {len(events)} events  →  {output_path}")
    return events


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    ap.add_argument("--video",       required=True)
    ap.add_argument("--store-id",    default="STORE_BLR_002")
    ap.add_argument("--clip-start",  default="2026-03-03T12:00:00Z")
    ap.add_argument("--output",      default="events/events.jsonl")
    args = ap.parse_args()

    clip_start = datetime.fromisoformat(args.clip_start.replace("Z", "+00:00"))
    process_video(args.video, args.store_id, clip_start, args.output)
