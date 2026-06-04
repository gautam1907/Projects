"""
ingestion.py — POST /events/ingest
Idempotent by event_id. Partial success on bad events. Max 500/request.
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from app.models import IngestRequest, IngestResponse
from app.database import get_db

router = APIRouter()
logger = logging.getLogger("store_intelligence")


@router.post("/ingest", response_model=IngestResponse)
async def ingest_events(request: Request, payload: IngestRequest, db=Depends(get_db)):
    trace_id    = getattr(request.state, "trace_id", "?")
    now         = datetime.now(timezone.utc).isoformat()
    accepted = rejected = duplicate = 0
    errors = []

    try:
        for ev in payload.events:
            try:
                await db.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id,store_id,camera_id,visitor_id,event_type,
                        timestamp,zone_id,dwell_ms,is_staff,confidence,
                        queue_depth,sku_zone,session_seq,ingested_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ev.event_id, ev.store_id, ev.camera_id, ev.visitor_id,
                     ev.event_type, ev.timestamp, ev.zone_id, ev.dwell_ms,
                     1 if ev.is_staff else 0, ev.confidence,
                     ev.metadata.queue_depth, ev.metadata.sku_zone,
                     ev.metadata.session_seq, now),
                )
                cur = await db.execute("SELECT changes()")
                row = await cur.fetchone()
                if (row[0] if row else 0) == 0:
                    duplicate += 1
                else:
                    accepted  += 1
            except Exception as e:
                rejected += 1
                errors.append({"event_id": ev.event_id, "error": str(e)})

        await db.commit()

    except RuntimeError as e:
        return JSONResponse(503, {"error": "database_unavailable",
                                  "detail": str(e), "trace_id": trace_id})

    logger.info("ingest", extra={"trace_id": trace_id, "event_count": accepted,
                                  "status_code": 200})
    return IngestResponse(accepted=accepted, rejected=rejected,
                          duplicate=duplicate, errors=errors)
