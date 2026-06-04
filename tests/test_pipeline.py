# PROMPT: "Write pytest tests for a retail store CCTV event detection pipeline.
#  Cover: schema compliance (all required fields), event_id uniqueness,
#  ISO-8601 UTC timestamp format, staff flagging, re-entry detection
#  (REENTRY not second ENTRY), group entry (N detections → N ENTRY events),
#  BILLING_QUEUE_JOIN has queue_depth > 0, zero-traffic graceful handling."
# CHANGES MADE:
#  - Confidence range test added (AI omitted it)
#  - Re-entry test uses separate track_id to simulate real re-detection
#  - Removed 'dwell_ms never None' (schema allows 0, not only null)

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import uuid, pytest
from datetime import datetime, timezone
from pipeline.emit    import EventEmitter
from pipeline.tracker import PersonTracker


@pytest.fixture
def emitter(): return EventEmitter("STORE_BLR_002", "CAM_3")

@pytest.fixture
def tracker(): return PersonTracker(fps=15.0)

def det(x=100, y=200, w=60, h=120, conf=0.85):
    return {"bbox": [x, y, x+w, y+h], "conf": conf, "centroid": (x+w/2, y+h/2)}


# ── Schema compliance ──────────────────────────────────────────────────────

REQUIRED = ["event_id","store_id","camera_id","visitor_id","event_type",
            "timestamp","zone_id","dwell_ms","is_staff","confidence","metadata"]
REQUIRED_META = ["queue_depth","sku_zone","session_seq"]

def test_all_top_level_fields(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 0.9, None)
    for f in REQUIRED: assert f in ev, f"Missing: {f}"

def test_all_metadata_fields(emitter):
    ev = emitter.emit("ZONE_ENTER", 1, datetime.now(timezone.utc), False, 0.88, "SKINCARE")
    for f in REQUIRED_META: assert f in ev["metadata"], f"Missing metadata: {f}"

def test_event_id_is_uuid4(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 0.9, None)
    assert uuid.UUID(ev["event_id"]).version == 4

def test_event_ids_unique(emitter):
    ts = datetime.now(timezone.utc)
    ids = [emitter.emit("ENTRY", i, ts, False, 0.9, None)["event_id"] for i in range(50)]
    assert len(set(ids)) == 50

def test_timestamp_utc_z(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 0.9, None)
    assert ev["timestamp"].endswith("Z")
    datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00"))

def test_confidence_not_suppressed(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 0.12, None)
    assert ev["confidence"] == 0.12   # low-conf events must pass through

def test_confidence_clamped(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 1.5, None)
    assert ev["confidence"] <= 1.0

def test_entry_zone_id_null(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 0.9, None)
    assert ev["zone_id"] is None

def test_exit_zone_id_null(emitter):
    ev = emitter.emit("EXIT", 1, datetime.now(timezone.utc), False, 0.9, None)
    assert ev["zone_id"] is None

def test_dwell_ms_integer(emitter):
    ev = emitter.emit("ZONE_DWELL", 1, datetime.now(timezone.utc), False, 0.88,
                      "SKINCARE", dwell_ms=45000)
    assert isinstance(ev["dwell_ms"], int) and ev["dwell_ms"] == 45000


# ── Staff ──────────────────────────────────────────────────────────────────

def test_staff_flagged_true(emitter):
    ev = emitter.emit("ENTRY", 99, datetime.now(timezone.utc), True, 0.9, None)
    assert ev["is_staff"] is True

def test_customer_flagged_false(emitter):
    ev = emitter.emit("ENTRY", 1, datetime.now(timezone.utc), False, 0.9, None)
    assert ev["is_staff"] is False


# ── Re-entry ───────────────────────────────────────────────────────────────

def test_reentry_event_type(emitter):
    ev = emitter.emit_reentry(42, "VIS_ABCDEF",
                              datetime.now(timezone.utc), False, 0.88)
    assert ev["event_type"] == "REENTRY"
    assert ev["visitor_id"] == "VIS_ABCDEF"

def test_reentry_visitor_id_preserved(emitter):
    ts = datetime.now(timezone.utc)
    ev1 = emitter.emit("ENTRY", 1, ts, False, 0.9, None)
    vid = ev1["visitor_id"]
    ev2 = emitter.emit_reentry(2, vid, ts, False, 0.88)
    assert ev2["visitor_id"] == vid


# ── Group entry ────────────────────────────────────────────────────────────

def test_group_of_3_produces_3_entry_events(emitter):
    ts = datetime.now(timezone.utc)
    events = [emitter.emit("ENTRY", i, ts, False, 0.9, None) for i in range(3)]
    assert len(events) == 3
    assert len({e["visitor_id"] for e in events}) == 3


# ── Billing ────────────────────────────────────────────────────────────────

def test_billing_join_has_positive_queue_depth(emitter):
    ev = emitter.emit("BILLING_QUEUE_JOIN", 5, datetime.now(timezone.utc),
                      False, 0.87, "BILLING_QUEUE", queue_depth=3)
    assert ev["metadata"]["queue_depth"] == 3
    assert ev["metadata"]["queue_depth"] > 0

def test_billing_abandon_type(emitter):
    ev = emitter.emit_billing_abandon(5, datetime.now(timezone.utc), False, 0.80)
    assert ev["event_type"] == "BILLING_QUEUE_ABANDON"


# ── Zero traffic ───────────────────────────────────────────────────────────

def test_tracker_empty_detections(tracker):
    result = tracker.update([], datetime.now(timezone.utc))
    assert isinstance(result, list) and result == []

def test_tracker_flush_empty(tracker):
    stale = tracker.flush_stale(datetime.now(timezone.utc))
    assert isinstance(stale, dict) and len(stale) == 0

def test_session_seq_increments(emitter):
    ts = datetime.now(timezone.utc)
    e1 = emitter.emit("ENTRY",      1, ts, False, 0.9, None)
    e2 = emitter.emit("ZONE_ENTER", 1, ts, False, 0.9, "SKINCARE")
    assert e2["metadata"]["session_seq"] == e1["metadata"]["session_seq"] + 1
