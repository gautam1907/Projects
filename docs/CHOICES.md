# CHOICES.md — Architecture Decision Record

> Word count: ~650 words

Three key architectural decisions, each with options considered,
AI input, and my final reasoning.

---

## Decision 1: Detection Model — YOLOv8n

### Options Considered
| Model       | mAP (COCO) | CPU fps | Notes |
|-------------|-----------|---------|-------|
| YOLOv8n     | 37.3      | ~80     | Lowest latency, good occlusion handling |
| YOLOv8s     | 44.9      | ~50     | Better accuracy, still CPU-viable |
| RT-DETR-L   | 53.0      | ~8      | Best accuracy, needs GPU for real-time |
| MediaPipe   | —         | ~120    | Fast but not trained for partial occlusions |

### What AI Suggested
I asked Claude to compare YOLOv8n vs RT-DETR for retail person counting.
Claude recommended RT-DETR, citing an estimated 8–12% improvement on
partially-occluded detections and arguing that accuracy in entry counting
directly impacts the headline conversion rate metric.

### What I Chose and Why
**YOLOv8n** with 1fps zone sampling, 5fps entry sampling.

The core problem is deployment target. Retail stores run CCTV on commodity
NVR hardware (typically Intel N-series, not GPU). RT-DETR at 8fps on CPU
means we process only 1 in 2 frames at 15fps — the same effective coverage
as YOLOv8n at full speed, but with worse latency and higher error risk from
queue buildup.

I agreed with Claude that accuracy matters for the conversion metric. But
accuracy gains from a better model are erased if inference speed causes us
to miss entire entry events due to frame skipping. YOLOv8n at confident
threshold 0.30 with temporal smoothing (15-frame track age) achieves
equivalent practical accuracy.

**Upgrade path:** Switch to YOLOv8s at first sign that entry count accuracy
drops below 90% on held-out clips.

---

## Decision 2: Event Schema Design

### Options Considered
**Option A — Minimal schema:** event_id, store_id, camera_id, visitor_id,
event_type, timestamp only. Simpler validation, fewer ingest failures.

**Option B — Full schema with metadata block:** All required fields +
`metadata.queue_depth`, `metadata.sku_zone`, `metadata.session_seq`.
More fields to validate but enables richer queries without joins.

**Option C — Flat denormalized schema:** Everything at top level, no
metadata sub-object. Easiest to query but schema drift is uncontrolled.

### What AI Suggested
Claude suggested Option A as the starting point and iterating toward B
as API queries revealed which fields were needed. Argument: YAGNI
(you aren't gonna need it) reduces ingest validation failures.

### What I Chose and Why
**Option B (full schema from day one)** — for three specific reasons:

1. The problem spec explicitly defines the metadata block. Deviating from
   it fails the schema compliance scoring criteria regardless of whether
   I "need" it yet.
2. `session_seq` enables visitor journey reconstruction without expensive
   timestamp-ordered joins at query time. It costs one integer per event
   at ingest and saves significant query complexity in `/funnel`.
3. `sku_zone` enables the "zones with high dwell but no conversion" business
   query directly from the events table without a join to `store_layout.json`.

I disagreed with Claude's YAGNI argument because the schema scoring criteria
and the downstream API queries both imply the full schema is required from
the start. Claude was reasoning about a generic API design problem; the
problem spec changes the trade-off.

---

## Decision 3: API Storage — SQLite vs PostgreSQL

### Options Considered
- **SQLite + aiosqlite:** Zero-setup, file-based, ANSI SQL, ~10k events/s
- **PostgreSQL + asyncpg:** Production-grade, multi-writer, ~100k events/s
- **TimescaleDB (PostgreSQL extension):** Automatic time partitioning,
  continuous aggregates for pre-computed metrics

### What AI Suggested
Claude strongly recommended TimescaleDB, citing:
- Automatic time-based partitioning for timestamp range scans
- `time_bucket()` functions simplifying the 7-day anomaly baseline query
- Continuous aggregates eliminating `COUNT(DISTINCT ...)` per request

### What I Chose and Why
**SQLite** for this submission, with PostgreSQL as the documented upgrade path.

The `docker compose up` acceptance gate requires zero manual setup. PostgreSQL
adds a service dependency that can fail silently due to port conflicts,
volume permissions, or OS-level restrictions. SQLite is unconditionally
portable.

Data volume sanity check: 40 stores × 1,000 events/hour × 12 hours =
480,000 events/day. With the four indexes I've defined (store_id+timestamp,
visitor_id, event_type, zone_id), every API query runs a single index scan.
The `EXPLAIN QUERY PLAN` for the `/metrics` query shows two index lookups
with no table scans. P99 latency under this load is well under 50ms on a
commodity laptop.

**Where I agreed with Claude:** At production scale (40 live stores with
concurrent camera feeds), the `COUNT(DISTINCT ...)` queries in `/funnel` and
`/metrics` will degrade under write contention. The correct upgrade is:
PostgreSQL + a materialized hourly summary table (updated by a background
worker), falling back to live count when the summary is stale. I would make
this change at the point where ingest lag on a single SQLite file exceeds
100ms under write load — approximately 5M events/day.
