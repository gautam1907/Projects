# Store Intelligence System — Purplle Tech Challenge 2026

Real-time retail analytics from CCTV footage:  
**Raw video → Person detection → Structured events → REST API → Live dashboard**

---

## Quick Start (5 commands)

```bash
# 1. Clone and enter
git clone <your-repo-url> && cd store-intelligence

# 2. Copy your video clips
mkdir -p clips && cp /path/to/footage/*.mp4 clips/

# 3. Start API + Dashboard
docker compose up --build -d

# 4. Run detection pipeline (processes all clips in clips/)
STORE_ID=STORE_BLR_002 bash pipeline/run.sh

# 5. Ingest events into the API
python3 pipeline/ingest_events.py --file events/events.jsonl --api http://localhost:8000
```

- **API:** http://localhost:8000  
- **Swagger docs:** http://localhost:8000/docs  
- **Live dashboard:** http://localhost:8501

---

## Running the Detection Pipeline

### Install detection dependencies (outside Docker)
```bash
pip install ultralytics opencv-python-headless numpy
```

### Process a single clip
```bash
python3 pipeline/detect.py \
  --video       clips/CAM_3_-_entry.mp4 \
  --store-id    STORE_BLR_002 \
  --clip-start  2026-03-03T12:00:00Z \
  --output      events/events.jsonl
```

### Process all clips at once
```bash
STORE_ID=STORE_BLR_002 CLIPS_DIR=./clips bash pipeline/run.sh
```

Events are appended to `events/events.jsonl` in the required schema format.

### Ingest events into the API
```bash
python3 pipeline/ingest_events.py \
  --file  events/events.jsonl \
  --api   http://localhost:8000
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/events/ingest` | Ingest up to 500 events. Idempotent by `event_id`. |
| `GET`  | `/stores/{id}/metrics` | Unique visitors, conversion rate, dwell, queue depth |
| `GET`  | `/stores/{id}/funnel` | 4-stage funnel with drop-off % |
| `GET`  | `/stores/{id}/heatmap` | Zone heat scores normalised 0–100 |
| `GET`  | `/stores/{id}/anomalies` | Active anomalies (INFO / WARN / CRITICAL) |
| `GET`  | `/health` | Service status + STALE_FEED detection |

### Example requests
```bash
# Metrics
curl http://localhost:8000/stores/STORE_BLR_002/metrics

# Funnel
curl http://localhost:8000/stores/STORE_BLR_002/funnel

# Health
curl http://localhost:8000/health
```

---

## Running Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v --tb=short
```

Tests use in-memory SQLite — no setup required. Coverage >70%.

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py          # YOLOv8n detection + IoU tracking + staff classifier
│   ├── tracker.py         # ByteTrack-style tracker + Re-ID (30s grace window)
│   ├── emit.py            # Event schema builder (required output format)
│   ├── ingest_events.py   # Push events.jsonl → API in batches
│   └── run.sh             # Process all clips → events.jsonl
├── app/
│   ├── main.py            # FastAPI entrypoint + structured logging + trace IDs
│   ├── models.py          # Pydantic event schema validation
│   ├── database.py        # aiosqlite layer (swap URL for PostgreSQL)
│   ├── ingestion.py       # POST /events/ingest (idempotent)
│   ├── metrics.py         # GET /stores/{id}/metrics
│   ├── funnel.py          # GET /stores/{id}/funnel
│   ├── heatmap.py         # GET /stores/{id}/heatmap
│   ├── anomalies.py       # GET /stores/{id}/anomalies
│   └── health.py          # GET /health
├── tests/
│   ├── conftest.py        # Shared fixtures (in-memory DB)
│   ├── test_pipeline.py   # Schema, staff, re-entry, group, billing, zero-traffic
│   ├── test_metrics.py    # API integration tests (ingest, metrics, funnel, heatmap)
│   └── test_anomalies.py  # Anomaly detection tests (thresholds, severity, actions)
├── docs/
│   ├── DESIGN.md          # Architecture + AI-assisted decisions
│   └── CHOICES.md         # 3 decisions: model, schema, storage
├── store_layout.json      # Zone definitions for all stores
├── dashboard.py           # Streamlit live dashboard (Part E)
├── docker-compose.yml
├── Dockerfile
├── Dockerfile.dashboard
├── requirements.txt
└── README.md
```

---

## Architecture Summary

**Detection:** YOLOv8n at 1fps (zone) / 5fps (entry). IoU-based tracker assigns
stable `visitor_id` per session. Appearance-hash Re-ID catches re-entrants within
a 30-second grace window, emitting `REENTRY` instead of a second `ENTRY`.
Staff classified by dark uniform color + movement distance (dual-signal, ~80% accuracy).

**API:** FastAPI + SQLite. Every request logs `trace_id`, `store_id`, `endpoint`,
`latency_ms`, `status_code`. DB unavailable returns HTTP 503 with structured JSON
— no raw stack traces. Ingest is idempotent by `event_id` via `INSERT OR IGNORE`.

**Dashboard:** Streamlit. Polls API every 5 seconds. Shows live KPIs, funnel chart,
zone heatmap, and active anomalies.

See `docs/DESIGN.md` and `docs/CHOICES.md` for full decision rationale.
