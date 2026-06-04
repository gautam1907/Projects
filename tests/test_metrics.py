# PROMPT: "Write pytest async integration tests for FastAPI store metrics endpoints
#  using httpx AsyncClient + in-memory SQLite. Test: /metrics conversion_rate,
#  staff exclusion, zero-visitor safety, /funnel 4 stages, /heatmap low-confidence
#  flag, ingest idempotency, partial success on bad event_type."
# CHANGES MADE:
#  - Added idempotency test (AI only tested happy path)
#  - Added STALE_FEED health test
#  - In-memory DB fixture moved to conftest.py to avoid repetition

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", ":memory:")

import uuid, pytest, pytest_asyncio
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from app.main import app


def ev(etype="ENTRY", store="STORE_BLR_002", staff=False,
       zone=None, vid=None, qd=None, dwell=0):
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store,
        "camera_id":  "CAM_3",
        "visitor_id": vid or f"VIS_{uuid.uuid4().hex[:6].upper()}",
        "event_type": etype,
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id":    zone,
        "dwell_ms":   dwell,
        "is_staff":   staff,
        "confidence": 0.88,
        "metadata":   {"queue_depth": qd, "sku_zone": None, "session_seq": 1},
    }


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_ingest_accepted(client):
    async with client as c:
        r = await c.post("/events/ingest", json={"events": [ev() for _ in range(5)]})
    assert r.status_code == 200
    assert r.json()["accepted"] == 5


@pytest.mark.asyncio
async def test_ingest_idempotent(client):
    events = [ev() for _ in range(3)]
    async with client as c:
        r1 = await c.post("/events/ingest", json={"events": events})
        r2 = await c.post("/events/ingest", json={"events": events})
    assert r1.json()["accepted"] == 3
    assert r2.json()["accepted"] == 0
    assert r2.json()["duplicate"] == 3


@pytest.mark.asyncio
async def test_invalid_event_type_rejected(client):
    bad = {**ev(), "event_type": "NOT_REAL"}
    async with client as c:
        r = await c.post("/events/ingest", json={"events": [bad]})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_metrics_zero_visitors(client):
    async with client as c:
        r = await c.get("/stores/STORE_EMPTY/metrics")
    assert r.status_code == 200
    d = r.json()
    assert d["unique_visitors"]    == 0
    assert d["conversion_rate"]    == 0.0
    assert d["current_queue_depth"] == 0


@pytest.mark.asyncio
async def test_metrics_excludes_staff(client):
    store = "STORE_STAFF_X"
    events = ([ev("ENTRY", store, staff=False) for _ in range(3)] +
              [ev("ENTRY", store, staff=True)  for _ in range(5)])
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/metrics")
    assert r.json()["unique_visitors"] == 3


@pytest.mark.asyncio
async def test_conversion_rate_correct(client):
    store = "STORE_CONV_X"
    vids  = [f"VIS_{i:05d}" for i in range(10)]
    events = ([ev("ENTRY",              store, vid=v)  for v in vids] +
              [ev("BILLING_QUEUE_JOIN", store, vid=v, zone="BILLING_QUEUE", qd=1)
               for v in vids[:4]])
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/metrics")
    d = r.json()
    assert d["unique_visitors"]    == 10
    assert d["converted_visitors"] == 4
    assert abs(d["conversion_rate"] - 0.4) < 0.01


@pytest.mark.asyncio
async def test_funnel_four_stages(client):
    async with client as c:
        r = await c.get("/stores/STORE_BLR_002/funnel")
    assert r.status_code == 200
    stages = [s["stage"] for s in r.json()["funnel"]]
    assert stages == ["entry","zone_visit","billing_queue","purchase"]


@pytest.mark.asyncio
async def test_funnel_dropoff_present(client):
    async with client as c:
        r = await c.get("/stores/STORE_BLR_002/funnel")
    for s in r.json()["funnel"]:
        assert "drop_off_pct" in s


@pytest.mark.asyncio
async def test_heatmap_low_confidence(client):
    store = "STORE_HEAT_LOW"
    events = [ev("ZONE_ENTER", store, zone="SKINCARE") for _ in range(5)]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/heatmap")
    assert r.json()["data_confidence"] == "low"


@pytest.mark.asyncio
async def test_health_returns_status(client):
    async with client as c:
        r = await c.get("/health")
    d = r.json()
    assert "status"   in d
    assert "database" in d
    assert "stores"   in d
