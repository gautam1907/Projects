"""
main.py — FastAPI entrypoint. Structured JSON logging, trace IDs, graceful 503s.
"""
import uuid, time, logging, json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.ingestion import router as r_ingest
from app.metrics   import router as r_metrics
from app.funnel    import router as r_funnel
from app.heatmap   import router as r_heatmap
from app.anomalies import router as r_anomalies
from app.health    import router as r_health
from app.database  import init_db


class _JSON(logging.Formatter):
    def format(self, r):
        d = {"ts": datetime.now(timezone.utc).isoformat(), "level": r.levelname,
             "msg": r.getMessage()}
        for k in ("trace_id","store_id","endpoint","latency_ms","event_count","status_code"):
            if hasattr(r, k): d[k] = getattr(r, k)
        return json.dumps(d)

_h = logging.StreamHandler()
_h.setFormatter(_JSON())
logger = logging.getLogger("store_intelligence")
logger.setLevel(logging.INFO)
logger.addHandler(_h)
logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting — initialising DB")
    try:
        await init_db()
        logger.info("DB ready")
    except Exception as e:
        logger.error(f"DB init failed: {e}")
    yield
    logger.info("Shutting down")


app = FastAPI(title="Purplle Store Intelligence API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    request.state.trace_id = trace_id
    t0 = time.time()

    parts = request.url.path.split("/")
    store_id = parts[parts.index("stores") + 1] if "stores" in parts else None

    try:
        resp = await call_next(request)
        extra = {"trace_id": trace_id, "endpoint": request.url.path,
                 "latency_ms": round((time.time()-t0)*1000, 2),
                 "status_code": resp.status_code}
        if store_id: extra["store_id"] = store_id
        logger.info(f"{request.method} {request.url.path}", extra=extra)
        resp.headers["X-Trace-ID"] = trace_id
        return resp
    except Exception as e:
        logger.error(f"Unhandled: {e}", extra={"trace_id": trace_id})
        return JSONResponse(500, {"error": "internal_server_error", "trace_id": trace_id})


@app.exception_handler(Exception)
async def exc_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", "?")
    return JSONResponse(503, {"error": "service_unavailable",
                               "detail": "Temporary error. Please retry.",
                               "trace_id": trace_id})


app.include_router(r_ingest,   prefix="/events",  tags=["Events"])
app.include_router(r_metrics,  prefix="/stores",  tags=["Metrics"])
app.include_router(r_funnel,   prefix="/stores",  tags=["Funnel"])
app.include_router(r_heatmap,  prefix="/stores",  tags=["Heatmap"])
app.include_router(r_anomalies,prefix="/stores",  tags=["Anomalies"])
app.include_router(r_health,                      tags=["Health"])
