"""
anomalies.py — GET /stores/{store_id}/anomalies
Detects: queue spike, conversion drop vs 7-day avg, dead zone (30 min).
Severity: INFO / WARN / CRITICAL. Every anomaly has suggested_action.
"""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone
from app.database import get_db

router = APIRouter()

QUEUE_WARN     = 5
QUEUE_CRIT     = 10
CONV_WARN_DROP = 0.20   # 20% relative drop
CONV_CRIT_DROP = 0.40
DEAD_ZONE_MIN  = 30


@router.get("/{store_id}/anomalies")
async def get_anomalies(store_id: str, db=Depends(get_db)):
    now = datetime.now(timezone.utc)
    anomalies = []

    try:
        # ── 1. Queue spike ─────────────────────────────────────────────────
        cur = await db.execute(
            "SELECT MAX(queue_depth) AS mx FROM events "
            "WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' "
            "  AND timestamp > DATETIME('now','-30 minutes')",
            (store_id,))
        row = await cur.fetchone()
        mq = row["mx"] if row and row["mx"] else 0

        if mq > QUEUE_CRIT:
            anomalies.append({
                "anomaly_type": "BILLING_QUEUE_SPIKE", "severity": "CRITICAL",
                "current_value": mq, "threshold": QUEUE_CRIT,
                "suggested_action": f"Queue depth {mq}. Open additional billing counter immediately.",
                "detected_at": _ts(now),
            })
        elif mq > QUEUE_WARN:
            anomalies.append({
                "anomaly_type": "BILLING_QUEUE_SPIKE", "severity": "WARN",
                "current_value": mq, "threshold": QUEUE_WARN,
                "suggested_action": f"Queue depth {mq}. Call additional staff to billing counter.",
                "detected_at": _ts(now),
            })

        # ── 2. Conversion drop vs 7-day average ───────────────────────────
        cur = await db.execute(
            "SELECT "
            "  COUNT(DISTINCT CASE WHEN event_type='ENTRY'              THEN visitor_id END) AS vis, "
            "  COUNT(DISTINCT CASE WHEN event_type='BILLING_QUEUE_JOIN' THEN visitor_id END) AS conv "
            "FROM events WHERE store_id=? AND is_staff=0 AND DATE(timestamp)=DATE('now')",
            (store_id,))
        row = await cur.fetchone()
        today_vis  = row["vis"]  or 0
        today_conv = row["conv"] or 0
        today_rate = today_conv / today_vis if today_vis else None

        cur = await db.execute(
            "SELECT DATE(timestamp) AS day, "
            "  COUNT(DISTINCT CASE WHEN event_type='ENTRY'              THEN visitor_id END) AS vis, "
            "  COUNT(DISTINCT CASE WHEN event_type='BILLING_QUEUE_JOIN' THEN visitor_id END) AS conv "
            "FROM events WHERE store_id=? AND is_staff=0 "
            "  AND DATE(timestamp) BETWEEN DATE('now','-7 days') AND DATE('now','-1 day') "
            "GROUP BY day",
            (store_id,))
        hist = await cur.fetchall()

        if hist and today_rate is not None:
            rates = [r["conv"] / r["vis"] for r in hist if r["vis"]]
            if rates:
                avg7 = sum(rates) / len(rates)
                if avg7 > 0:
                    drop = (avg7 - today_rate) / avg7
                    if drop > CONV_CRIT_DROP:
                        anomalies.append({
                            "anomaly_type": "CONVERSION_DROP", "severity": "CRITICAL",
                            "current_value": round(today_rate, 3),
                            "baseline_7d_avg": round(avg7, 3),
                            "drop_pct": round(drop * 100, 1),
                            "suggested_action": "Conversion critically low. Check stock, staff, and product placement.",
                            "detected_at": _ts(now),
                        })
                    elif drop > CONV_WARN_DROP:
                        anomalies.append({
                            "anomaly_type": "CONVERSION_DROP", "severity": "WARN",
                            "current_value": round(today_rate, 3),
                            "baseline_7d_avg": round(avg7, 3),
                            "drop_pct": round(drop * 100, 1),
                            "suggested_action": f"Conversion down {round(drop*100,1)}% vs 7-day avg. Check zone stock levels.",
                            "detected_at": _ts(now),
                        })

        # ── 3. Dead zones (no traffic in 30 min) ──────────────────────────
        cur = await db.execute(
            "SELECT DISTINCT zone_id FROM events "
            "WHERE store_id=? AND zone_id IS NOT NULL AND is_staff=0 "
            "  AND DATE(timestamp)=DATE('now')",
            (store_id,))
        zones = [r["zone_id"] for r in await cur.fetchall()]

        for zid in zones:
            cur = await db.execute(
                "SELECT MAX(timestamp) AS lt FROM events "
                "WHERE store_id=? AND zone_id=? AND is_staff=0 "
                "  AND event_type IN ('ZONE_ENTER','ZONE_DWELL')",
                (store_id, zid))
            row = await cur.fetchone()
            if row and row["lt"]:
                lt = datetime.fromisoformat(row["lt"].replace("Z", "+00:00"))
                mins = (now - lt).total_seconds() / 60
                if mins > DEAD_ZONE_MIN:
                    anomalies.append({
                        "anomaly_type": "DEAD_ZONE", "severity": "INFO",
                        "zone_id": zid,
                        "minutes_since_last_visit": round(mins),
                        "suggested_action": f"Zone {zid} idle {round(mins)} min. Consider display refresh or staff engagement.",
                        "detected_at": _ts(now),
                    })

        return {
            "store_id":      store_id,
            "as_of":         _ts(now),
            "anomaly_count": len(anomalies),
            "anomalies":     anomalies,
        }
    except RuntimeError as e:
        raise HTTPException(503, {"error": "database_unavailable", "detail": str(e)})


def _ts(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
