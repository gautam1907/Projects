"""
health.py — GET /health
Service status + per-store last-event timestamp + STALE_FEED if >10 min lag.
"""
from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from app.database import get_db

router = APIRouter()
STALE_MINUTES = 10


@router.get("/health")
async def health(db=Depends(get_db)):
    now = datetime.now(timezone.utc)
    try:
        cur = await db.execute(
            "SELECT store_id, MAX(ingested_at) AS last_at FROM events GROUP BY store_id")
        rows = await cur.fetchall()

        stores = []
        for r in rows:
            lt_str = r["last_at"]
            status = "OK"
            lag    = None
            if lt_str:
                lt  = datetime.fromisoformat(lt_str.replace("Z", "+00:00"))
                lag = round((now - lt).total_seconds() / 60, 1)
                if lag > STALE_MINUTES:
                    status = "STALE_FEED"
            stores.append({"store_id": r["store_id"], "last_event_at": lt_str,
                            "lag_minutes": lag, "status": status})

        overall = "OK" if all(s["status"] == "OK" for s in stores) else "DEGRADED"
        return {"service": "store-intelligence-api", "status": overall,
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "database": "connected", "stores": stores}

    except Exception as e:
        return {"service": "store-intelligence-api", "status": "UNHEALTHY",
                "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "database": "disconnected", "error": "Database query failed", "stores": []}
