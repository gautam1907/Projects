"""
funnel.py — GET /stores/{store_id}/funnel
4-stage conversion funnel. Session = visitor_id. Re-entries deduplicated.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db

router = APIRouter()


@router.get("/{store_id}/funnel")
async def get_funnel(store_id: str, db=Depends(get_db)):
    try:
        async def count(sql, *args):
            cur = await db.execute(sql, args)
            row = await cur.fetchone()
            return (row[0] if row and row[0] else 0)

        d = "DATE('now')"
        entries   = await count(
            f"SELECT COUNT(DISTINCT visitor_id) FROM events "
            f"WHERE store_id=? AND event_type='ENTRY' AND is_staff=0 AND DATE(timestamp)={d}",
            store_id)
        zones     = await count(
            f"SELECT COUNT(DISTINCT visitor_id) FROM events "
            f"WHERE store_id=? AND event_type='ZONE_ENTER' AND is_staff=0 AND DATE(timestamp)={d}",
            store_id)
        billing   = await count(
            f"SELECT COUNT(DISTINCT visitor_id) FROM events "
            f"WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0 AND DATE(timestamp)={d}",
            store_id)
        purchased = await count(
            f"SELECT COUNT(DISTINCT visitor_id) FROM events "
            f"WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0 "
            f"AND DATE(timestamp)={d} "
            f"AND visitor_id NOT IN ("
            f"  SELECT DISTINCT visitor_id FROM events "
            f"  WHERE store_id=? AND event_type='BILLING_QUEUE_ABANDON' AND DATE(timestamp)={d})",
            store_id, store_id)

        def drop(curr, prev):
            return round((prev - curr) / prev * 100, 2) if prev else 0.0

        return {
            "store_id": store_id,
            "funnel": [
                {"stage": "entry",         "label": "Store Entry",         "count": entries,   "drop_off_pct": 0.0},
                {"stage": "zone_visit",    "label": "Zone Engagement",     "count": zones,     "drop_off_pct": drop(zones,     entries)},
                {"stage": "billing_queue", "label": "Billing Queue",       "count": billing,   "drop_off_pct": drop(billing,   zones)},
                {"stage": "purchase",      "label": "Completed Purchase",  "count": purchased, "drop_off_pct": drop(purchased, billing)},
            ],
            "conversion_rate": round(purchased / entries, 4) if entries else 0.0,
        }
    except RuntimeError as e:
        raise HTTPException(503, {"error": "database_unavailable", "detail": str(e)})
