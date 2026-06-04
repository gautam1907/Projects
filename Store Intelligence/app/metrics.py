"""
metrics.py — GET /stores/{store_id}/metrics
Real-time: unique visitors, conversion rate, avg dwell per zone,
queue depth, abandonment rate. Staff excluded. Zero-safe.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from app.database import get_db

router = APIRouter()


@router.get("/{store_id}/metrics")
async def get_metrics(store_id: str, db=Depends(get_db)):
    try:
        def z(row, key): return row[key] if row and row[key] is not None else 0

        # Unique customer entries today
        cur = await db.execute(
            "SELECT COUNT(DISTINCT visitor_id) AS n FROM events "
            "WHERE store_id=? AND event_type='ENTRY' AND is_staff=0 AND DATE(timestamp)=DATE('now')",
            (store_id,))
        unique_visitors = z(await cur.fetchone(), "n")

        # Converted = reached billing without abandoning
        cur = await db.execute(
            "SELECT COUNT(DISTINCT visitor_id) AS n FROM events "
            "WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0 "
            "  AND DATE(timestamp)=DATE('now') "
            "  AND visitor_id NOT IN ("
            "    SELECT DISTINCT visitor_id FROM events "
            "    WHERE store_id=? AND event_type='BILLING_QUEUE_ABANDON' "
            "    AND DATE(timestamp)=DATE('now'))",
            (store_id, store_id))
        converted = z(await cur.fetchone(), "n")

        conversion_rate = round(converted / unique_visitors, 4) if unique_visitors else 0.0

        # Avg dwell per zone
        cur = await db.execute(
            "SELECT zone_id, ROUND(AVG(dwell_ms)) AS avg_dwell, COUNT(*) AS visits "
            "FROM events WHERE store_id=? AND event_type IN ('ZONE_EXIT','ZONE_DWELL') "
            "  AND is_staff=0 AND zone_id IS NOT NULL AND DATE(timestamp)=DATE('now') "
            "GROUP BY zone_id ORDER BY avg_dwell DESC",
            (store_id,))
        zone_dwell = [{"zone_id": r["zone_id"],
                       "avg_dwell_ms": int(r["avg_dwell"] or 0),
                       "visits": r["visits"]}
                      for r in await cur.fetchall()]

        # Current queue depth
        cur = await db.execute(
            "SELECT queue_depth FROM events WHERE store_id=? "
            "  AND event_type='BILLING_QUEUE_JOIN' AND DATE(timestamp)=DATE('now') "
            "ORDER BY timestamp DESC LIMIT 1",
            (store_id,))
        row = await cur.fetchone()
        queue_depth = row["queue_depth"] if row and row["queue_depth"] else 0

        # Abandonment rate
        cur = await db.execute(
            "SELECT "
            "  COUNT(CASE WHEN event_type='BILLING_QUEUE_ABANDON' THEN 1 END) AS ab, "
            "  COUNT(CASE WHEN event_type='BILLING_QUEUE_JOIN'    THEN 1 END) AS jn "
            "FROM events WHERE store_id=? "
            "  AND event_type IN ('BILLING_QUEUE_JOIN','BILLING_QUEUE_ABANDON') "
            "  AND is_staff=0 AND DATE(timestamp)=DATE('now')",
            (store_id,))
        row = await cur.fetchone()
        jn = z(row, "jn"); ab = z(row, "ab")
        abandonment_rate = round(ab / jn, 4) if jn else 0.0

        return {
            "store_id": store_id,
            "as_of": _utcnow(),
            "unique_visitors":    unique_visitors,
            "converted_visitors": converted,
            "conversion_rate":    conversion_rate,
            "current_queue_depth": queue_depth,
            "abandonment_rate":   abandonment_rate,
            "zone_dwell":         zone_dwell,
        }
    except RuntimeError as e:
        raise HTTPException(503, {"error": "database_unavailable", "detail": str(e)})


def _utcnow(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
