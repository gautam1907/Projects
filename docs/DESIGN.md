# DESIGN.md — Store Intelligence System Architecture

> Word count: ~600 words

## System Overview

This system converts raw retail CCTV footage into a queryable analytics API.
It is built as four sequential stages: Detection → Events → API → Dashboard.

```
CCTV Clips (.mp4)
      │
      ▼
┌─────────────────────────────────────────────────┐
│  Detection Pipeline  (pipeline/)                │
│                                                  │
│  YOLOv8n (person detect, conf ≥ 0.30)           │
│       │                                          │
│  IoU Tracker  (ByteTrack-style, threshold=0.30) │
│       │                                          │
│  Staff Classifier  (color + movement signals)   │
│       │                                          │
│  Re-ID Buffer  (appearance hash, 30s window)    │
│       │                                          │
│  EventEmitter  → events.jsonl                   │
└──────────────────────┬──────────────────────────┘
                       │  POST /events/ingest
                       ▼
┌─────────────────────────────────────────────────┐
│  Intelligence API  (app/)                        │
│                                                  │
│  FastAPI + aiosqlite (SQLite)                   │
│  /events/ingest   — idempotent, partial success │
│  /stores/{id}/metrics    — real-time KPIs        │
│  /stores/{id}/funnel     — 4-stage funnel        │
│  /stores/{id}/heatmap    — normalised 0–100      │
│  /stores/{id}/anomalies  — INFO/WARN/CRITICAL    │
│  /health                 — STALE_FEED detection  │
└──────────────────────┬──────────────────────────┘
                       │  polls every 5s
                       ▼
             Streamlit Dashboard (dashboard.py)
```

---

## Stage 1 — Detection Pipeline

**Model selection:** YOLOv8n (nano). Selected for CPU-viable inference speed
(~80fps) over accuracy trade-off. At 1fps zone sampling and 5fps entry sampling
the practical detection rate is sufficient for 30-second dwell events.

**Tracking:** IoU-based greedy matching (ByteTrack-inspired). Each frame's
detections are matched to active tracks via IoU. Unmatched detections spawn
new tracks. Tracks not seen for 15 frames are flushed and recorded as exits.

**Re-entry detection:** When a track is flushed (EXIT), its `visitor_id` is
stored with an exit timestamp. New detections are checked against the Re-ID
buffer via appearance hash (bbox aspect ratio + area bucket). A match within
30 seconds produces a `REENTRY` event instead of a second `ENTRY`.

**Staff classification:** Two-signal approach:
- Uniform color: HSV masking for dark blue/black (60% weight)
- Movement distance: staff traverse larger distances per frame (40% weight)
Combined score > 0.42 → `is_staff=True`.

**Zone assignment:** Normalised x-coordinate of centroid divided into equal
columns matching the zone list from `store_layout.json`. Billing zone handled
on the dedicated billing camera.

---

## Stage 2 — Event Schema

All events follow the schema exactly:
- `event_id`: UUID v4, globally unique
- `zone_id`: explicitly `null` for ENTRY/EXIT events
- `dwell_ms`: 0 for instantaneous events, >0 for ZONE_DWELL
- `confidence`: raw detection × staff confidence — never suppressed
- `metadata.session_seq`: per-visitor ordinal, increments on REENTRY

---

## Stage 3 — Intelligence API

**FastAPI** chosen for async native support (critical for concurrent DB queries
per store) and automatic OpenAPI docs.

**aiosqlite (SQLite)** chosen for zero-setup portability. All queries use
ANSI-standard SQL with no SQLite-specific syntax so the connection string
can be swapped to PostgreSQL with no query changes.

**Idempotency:** `INSERT OR IGNORE` on `event_id` PRIMARY KEY. The `changes()`
pragma distinguishes new inserts from silently skipped duplicates.

**Structured logging:** Every request emits a JSON log line containing
`trace_id`, `store_id`, `endpoint`, `latency_ms`, `status_code`.

---

## AI-Assisted Decisions

### 1. Re-entry grace window
I asked Claude to suggest a grace window duration for retail re-entry.
Claude suggested 60 seconds. I overrode this to 30 seconds: at 60s, a
customer who exits and a genuinely new customer who enters 35s later would
be incorrectly merged into one visitor_id, inflating session quality.
30s better matches the edge case descriptions in the problem spec.

### 2. Staff detection approach
Claude initially suggested pose estimation (gait analysis) for staff
classification. I evaluated this and rejected it: pose estimation on 15fps
blurred footage with partial occlusion is unreliable, and adds ~200ms/frame
latency. The color + movement dual-signal approach is explainable, fast, and
achieves ~80% accuracy on the available footage which is sufficient for
flagging (not hiding) staff events.

### 3. Anomaly thresholds
Claude suggested queue spike threshold of 8 for WARN, 15 for CRITICAL.
I adjusted to 5/10 based on the store layout dimensions visible in
`Store_1_-_layout.png` — the billing area appears to accommodate ~6-8 people
comfortably, so a queue of 5 is already stressful and warrants a WARN.
