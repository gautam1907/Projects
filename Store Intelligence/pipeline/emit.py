"""
emit.py — Event schema builder conforming to the required Store Intelligence schema.

# PROMPT USED (AI-Assisted):
# "Create a Python event emitter that maps internal tracker state to a
#  required JSON schema with: uuid4 event_id, ISO-8601 UTC timestamp,
#  zone_id null for ENTRY/EXIT, dwell_ms=0 for instantaneous events,
#  metadata.session_seq as per-visitor ordinal counter."
# CHANGES MADE:
#  - session_seq tracks per visitor_id (not per track_id) for re-entry continuity
#  - Confidence clamped to [0.0, 1.0] but never suppressed
#  - ZONE_SKU_MAP loaded from layout at runtime; static fallback kept here
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

ZONE_SKU_MAP = {
    "SKINCARE":      "MOISTURISER",
    "MAKEUP":        "FOUNDATION",
    "FRAGRANCE":     "PERFUME",
    "HAIRCARE":      "SHAMPOO",
    "NAILS":         "NAIL_POLISH",
    "MENS":          "MENS_GROOMING",
    "BILLING_QUEUE": "BILLING",
    "GENERAL":       "GENERAL",
}


class EventEmitter:
    def __init__(self, store_id: str, camera_id: str):
        self.store_id   = store_id
        self.camera_id  = camera_id
        self._seq: dict[str, int]         = {}   # visitor_id → session_seq
        self._vid: dict[int, str]         = {}   # track_id   → visitor_id

    # ── Public API ─────────────────────────────────────────────────────────

    def emit(
        self,
        event_type: str,
        track_id:   int,
        timestamp:  datetime,
        is_staff:   bool,
        confidence: float,
        zone_id:    Optional[str],
        dwell_ms:   int = 0,
        queue_depth: Optional[int] = None,
        visitor_id: Optional[str] = None,
    ) -> dict:
        vid = visitor_id or self._vid.setdefault(
            track_id, f"VIS_{uuid.uuid4().hex[:6].upper()}")
        self._vid[track_id] = vid

        seq = self._seq.get(vid, 0) + 1
        self._seq[vid] = seq

        ts_str = self._fmt_ts(timestamp)

        return {
            "event_id":   str(uuid.uuid4()),
            "store_id":   self.store_id,
            "camera_id":  self.camera_id,
            "visitor_id": vid,
            "event_type": event_type,
            "timestamp":  ts_str,
            "zone_id":    zone_id,                # explicitly None for ENTRY/EXIT
            "dwell_ms":   dwell_ms,               # 0 for instantaneous events
            "is_staff":   is_staff,
            "confidence": round(min(max(confidence, 0.0), 1.0), 3),
            "metadata": {
                "queue_depth": queue_depth,
                "sku_zone":    ZONE_SKU_MAP.get(zone_id) if zone_id else None,
                "session_seq": seq,
            },
        }

    def emit_reentry(self, track_id, visitor_id, timestamp, is_staff, confidence):
        return self.emit("REENTRY", track_id, timestamp, is_staff, confidence,
                         None, visitor_id=visitor_id)

    def emit_billing_abandon(self, track_id, timestamp, is_staff, confidence):
        return self.emit("BILLING_QUEUE_ABANDON", track_id, timestamp,
                         is_staff, confidence, "BILLING_QUEUE")

    # ── Private ────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_ts(ts: datetime) -> str:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
