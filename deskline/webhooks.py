import json
from datetime import datetime
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError


class Payload(BaseModel):
    call_control_id: str = Field(min_length=1, max_length=1024)
    connection_id: str = Field(min_length=1, max_length=512)
    direction: Literal["incoming", "outgoing"] | None = None
    client_state: str | None = Field(default=None, max_length=4096)
    digits: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=80)


class Event(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    event_type: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    payload: Payload


async def read_event(request: Request, verifier, settings):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
    if len(body) > 65536:
        raise HTTPException(413, "Webhook is too large")
    try:
        raw = body.decode("utf-8")
        verifier.webhooks.unwrap(
            raw, headers=dict(request.headers), key=settings.public_key
        )
        data = json.loads(raw)["data"]
        if not isinstance(data, dict):
            raise ValueError("Expected event object")
    except Exception:
        raise HTTPException(401, "Invalid webhook signature or payload") from None
    supported = {
        "call.initiated",
        "call.answered",
        "call.gather.ended",
        "call.speak.ended",
        "call.hangup",
    }
    if data.get("event_type") not in supported:
        return None
    try:
        event = Event.model_validate(data)
        if event.occurred_at.tzinfo is None:
            raise ValueError("Timestamp needs a timezone")
    except (ValidationError, ValueError):
        raise HTTPException(422, "Malformed call event") from None
    if event.payload.connection_id != settings.connection_id:
        raise HTTPException(403, "Event belongs to a different Voice application")
    return event.model_dump(mode="json", exclude_none=True)
