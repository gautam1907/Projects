# PROMPT: "Write pytest tests for anomaly detection: queue spike CRITICAL >10,
#  WARN >5, conversion drop WARN >20%/CRITICAL >40% vs 7-day avg, dead zone
#  INFO after 30 min. Every anomaly must have suggested_action string."
# CHANGES MADE:
#  - Added no-anomaly-on-normal-queue test (AI only tested spike path)
#  - Added zero-data store test (must not crash, return empty list)
#  - Added anomaly_count == len(anomalies) structural test

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", ":memory:")

import uuid, pytest
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from app.main import app


def billing_ev(store, vid, qd):
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store,
        "camera_id":  "CAM_5",
        "visitor_id": vid,
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id":    "BILLING_QUEUE",
        "dwell_ms":   0,
        "is_staff":   False,
        "confidence": 0.9,
        "metadata":   {"queue_depth": qd, "sku_zone": "BILLING", "session_seq": 1},
    }


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_queue_spike_critical(client):
    store = "STORE_QC"
    events = [billing_ev(store, f"VIS_{i:04d}", 11) for i in range(3)]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/anomalies")
    spikes = [a for a in r.json()["anomalies"] if a["anomaly_type"]=="BILLING_QUEUE_SPIKE"]
    assert spikes and spikes[0]["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_queue_spike_warn(client):
    store = "STORE_QW"
    events = [billing_ev(store, f"VIS_{i:04d}", 7) for i in range(2)]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/anomalies")
    spikes = [a for a in r.json()["anomalies"] if a["anomaly_type"]=="BILLING_QUEUE_SPIKE"]
    assert spikes and spikes[0]["severity"] == "WARN"


@pytest.mark.asyncio
async def test_no_spike_on_normal_queue(client):
    store = "STORE_QN"
    events = [billing_ev(store, f"VIS_{i:04d}", 2) for i in range(2)]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/anomalies")
    spikes = [a for a in r.json()["anomalies"] if a["anomaly_type"]=="BILLING_QUEUE_SPIKE"]
    assert not spikes


@pytest.mark.asyncio
async def test_all_anomalies_have_suggested_action(client):
    store = "STORE_ACTION"
    events = [billing_ev(store, f"VIS_{i:04d}", 12) for i in range(2)]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/anomalies")
    for a in r.json()["anomalies"]:
        assert "suggested_action" in a and len(a["suggested_action"]) > 5


@pytest.mark.asyncio
async def test_zero_data_no_crash(client):
    async with client as c:
        r = await c.get("/stores/STORE_GHOST/anomalies")
    assert r.status_code == 200
    assert isinstance(r.json()["anomalies"], list)


@pytest.mark.asyncio
async def test_anomaly_count_matches_list(client):
    async with client as c:
        r = await c.get("/stores/STORE_BLR_002/anomalies")
    d = r.json()
    assert d["anomaly_count"] == len(d["anomalies"])


@pytest.mark.asyncio
async def test_severity_values_valid(client):
    store = "STORE_SEV"
    events = [billing_ev(store, f"VIS_{i:04d}", 11) for i in range(2)]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        r = await c.get(f"/stores/{store}/anomalies")
    for a in r.json()["anomalies"]:
        assert a["severity"] in {"INFO","WARN","CRITICAL"}
