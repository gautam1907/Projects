"""
models.py — Pydantic event schema. Mirrors the required output schema exactly.
"""
import uuid
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
}


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone:    Optional[str] = None
    session_seq: int = 1


class StoreEvent(BaseModel):
    event_id:   str   = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id:   str
    camera_id:  str
    visitor_id: str
    event_type: str
    timestamp:  str
    zone_id:    Optional[str] = None
    dwell_ms:   int   = 0
    is_staff:   bool  = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata:   EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def valid_type(cls, v):
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event_type '{v}'")
        return v

    @field_validator("timestamp")
    @classmethod
    def valid_ts(cls, v):
        v = v.replace("Z", "+00:00")
        datetime.fromisoformat(v)
        return v.replace("+00:00", "Z")

    @field_validator("confidence")
    @classmethod
    def round_conf(cls, v):
        return round(v, 3)


class IngestRequest(BaseModel):
    events: List[StoreEvent] = Field(max_length=500)


class IngestResponse(BaseModel):
    accepted:  int
    rejected:  int
    duplicate: int
    errors:    List[dict] = []
