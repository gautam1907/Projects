"""
heatmap.py — GET /stores/{store_id}/heatmap
Zone visit frequency + avg dwell, normalised 0-100.
data_confidence='low' when <20 sessions.
"""
from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db

router = APIRouter()


@router.get("/{store_id}/heatmap")
async def get_heatmap(store_id: str, db=Depends(get_db)):
    try:
        cur = await db.execute(
            "SELECT zone_id, COUNT(*) AS visits, "
            "  ROUND(AVG(dwell_ms)) AS avg_dwell, "
            "  COUNT(DISTINCT visitor_id) AS unique_vis "
            "FROM events "
            "WHERE store_id=? AND event_type IN ('ZONE_ENTER','ZONE_EXIT','ZONE_DWELL') "
            "  AND is_staff=0 AND zone_id IS NOT NULL AND DATE(timestamp)=DATE('now') "
            "GROUP BY zone_id ORDER BY visits DESC",
            (store_id,))
        rows = await cur.fetchall()

        if not rows:
            return {"store_id": store_id, "zones": [],
                    "data_confidence": "low", "note": "No zone data today"}

        max_v = max(r["visits"] for r in rows)
        max_d = max(r["avg_dwell"] or 0 for r in rows) or 1
        total_sessions = sum(r["unique_vis"] for r in rows)

        zones = []
        for r in rows:
            nv = round(r["visits"] / max_v * 100)
            nd = round((r["avg_dwell"] or 0) / max_d * 100)
            zones.append({
                "zone_id":           r["zone_id"],
                "visit_count":       r["visits"],
                "unique_visitors":   r["unique_vis"],
                "avg_dwell_ms":      int(r["avg_dwell"] or 0),
                "normalised_visits": nv,
                "normalised_dwell":  nd,
                "heat_score":        round(nv * 0.6 + nd * 0.4),
            })

        return {
            "store_id":        store_id,
            "zones":           sorted(zones, key=lambda z: z["heat_score"], reverse=True),
            "data_confidence": "low" if total_sessions < 20 else "normal",
            "total_sessions":  total_sessions,
        }
    except RuntimeError as e:
        raise HTTPException(503, {"error": "database_unavailable", "detail": str(e)})
