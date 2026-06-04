"""
tracker.py — IoU-based multi-object tracker with appearance Re-ID.
Provides stable visitor_id assignment and re-entry detection within
a 30-second grace window.

# PROMPT USED (AI-Assisted):
# "Implement ByteTrack-style IoU tracker in pure Python+NumPy.
#  Handle occlusion via IoU matching, assign stable visitor tokens,
#  detect re-entry with 30s grace window, track per-person movement
#  history for staff classification."
# CHANGES MADE:
#  - Reduced max_age from 30 frames → 15 frames (fewer ghost tracks)
#  - Re-ID buffer keyed on (aspect_ratio_bucket, size_bucket) not full bbox
#  - Added billing_tracks dict separate from main track state
"""

import numpy as np
import hashlib
from datetime import datetime, timedelta

MAX_AGE_FRAMES     = 15
IOU_THRESHOLD      = 0.30
REENTRY_GRACE_S    = 30


class _Track:
    __slots__ = ("track_id", "visitor_id", "bbox", "centroid", "conf",
                 "hits", "time_since_update", "positions", "last_seen",
                 "is_reentry")

    def __init__(self, track_id, visitor_id, bbox, centroid, conf, ts):
        self.track_id          = track_id
        self.visitor_id        = visitor_id
        self.bbox              = bbox
        self.centroid          = centroid
        self.conf              = conf
        self.hits              = 1
        self.time_since_update = 0
        self.positions         = [centroid]
        self.last_seen         = ts
        self.is_reentry        = False


class PersonTracker:
    def __init__(self, fps: float = 15.0):
        self.fps              = fps
        self._tracks: dict[int, _Track] = {}
        self._next_tid        = 1
        self._visitor_counter = 1
        # visitor_id → exit timestamp
        self._exited: dict[str, datetime]  = {}
        # appearance_key → visitor_id  (Re-ID buffer)
        self._reid: dict[str, str]         = {}
        # zone state
        self._zone:     dict[int, str]         = {}
        self._zone_ts:  dict[int, datetime]    = {}
        # billing state
        self._billing:  dict[int, datetime]    = {}

    # ── Public helpers ─────────────────────────────────────────────────────

    def get_zone(self, tid: int):             return self._zone.get(tid)
    def set_zone(self, tid, zid, ts):         self._zone[tid] = zid; self._zone_ts[tid] = ts
    def in_billing(self, tid: int) -> bool:   return tid in self._billing
    def billing_count(self) -> int:           return len(self._billing)
    def mark_billing(self, tid, ts):          self._billing[tid] = ts
    def clear_billing(self, tid):             self._billing.pop(tid, None)

    def get_dwell_ms(self, tid: int, now: datetime) -> int:
        ts = self._zone_ts.get(tid)
        if not ts:
            return 0
        return max(0, int((now - ts).total_seconds() * 1000))

    # ── Core update ────────────────────────────────────────────────────────

    def update(self, detections: list, ts: datetime) -> list:
        for t in self._tracks.values():
            t.time_since_update += 1

        if not detections:
            return self._active()

        tids = list(self._tracks.keys())
        d_boxes = [d["bbox"] for d in detections]

        matched_t, matched_d = set(), set()
        if tids:
            iou_mat = np.zeros((len(tids), len(d_boxes)))
            for i, tid in enumerate(tids):
                for j, db in enumerate(d_boxes):
                    iou_mat[i, j] = self._iou(self._tracks[tid].bbox, db)

            while iou_mat.max() >= IOU_THRESHOLD:
                i, j = np.unravel_index(iou_mat.argmax(), iou_mat.shape)
                tid = tids[i]
                d   = detections[j]
                t   = self._tracks[tid]
                t.bbox              = d["bbox"]
                t.centroid          = d["centroid"]
                t.conf              = d["conf"]
                t.positions         = (t.positions + [d["centroid"]])[-30:]
                t.time_since_update = 0
                t.hits             += 1
                t.last_seen         = ts
                matched_t.add(i); matched_d.add(j)
                iou_mat[i, :] = -1; iou_mat[:, j] = -1

        # New tracks for unmatched detections
        for j, d in enumerate(detections):
            if j in matched_d:
                continue
            vid, is_reentry = self._reid_lookup(d["bbox"], ts)
            if not vid:
                vid = f"VIS_{self._visitor_counter:05d}"
                self._visitor_counter += 1
                is_reentry = False
            new_t = _Track(self._next_tid, vid, d["bbox"], d["centroid"], d["conf"], ts)
            new_t.is_reentry = is_reentry
            self._tracks[self._next_tid] = new_t
            self._next_tid += 1
            self._reid[self._appearance_key(d["bbox"])] = vid

        return self._active()

    def flush_stale(self, ts: datetime) -> dict:
        """Remove aged-out tracks; record exits for re-entry buffer."""
        stale = {}
        for tid in [tid for tid, t in self._tracks.items()
                    if t.time_since_update > MAX_AGE_FRAMES]:
            t = self._tracks.pop(tid)
            self._exited[t.visitor_id] = ts
            stale[tid] = {"visitor_id": t.visitor_id, "is_staff": False}
        return stale

    # ── Private ────────────────────────────────────────────────────────────

    def _active(self) -> list:
        return [
            {
                "track_id":   t.track_id,
                "visitor_id": t.visitor_id,
                "bbox":       t.bbox,
                "centroid":   t.centroid,
                "conf":       t.conf,
                "positions":  t.positions,
                "is_reentry": t.is_reentry,
            }
            for t in self._tracks.values()
            if t.time_since_update <= MAX_AGE_FRAMES
        ]

    @staticmethod
    def _iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2-ix1) * max(0, iy2-iy1)
        union = (ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter
        return inter / (union + 1e-6)

    @staticmethod
    def _appearance_key(bbox) -> str:
        x1, y1, x2, y2 = bbox
        w, h = x2-x1, y2-y1
        aspect = round(w / (h + 1e-6), 1)
        size_b = round((w*h) / 4000) * 4000
        return hashlib.md5(f"{aspect}_{size_b}".encode()).hexdigest()[:8]

    def _reid_lookup(self, bbox, ts: datetime):
        key = self._appearance_key(bbox)
        vid = self._reid.get(key)
        if vid:
            exit_ts = self._exited.get(vid)
            if exit_ts and (ts - exit_ts).total_seconds() <= REENTRY_GRACE_S:
                return vid, True
        return None, False
