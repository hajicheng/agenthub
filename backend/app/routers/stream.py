from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.event_bus import event_bus

router = APIRouter()


def _format_sse(data: dict, event_id: str | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/api/stream")
async def stream_events(request: Request) -> StreamingResponse:
    last_event_id = request.headers.get("last-event-id") or request.query_params.get("lastEventId")

    async def generator() -> AsyncIterator[str]:
        queue, unsubscribe = event_bus.subscribe()
        try:
            yield _format_sse({"type": "connected", "timestamp": int(time.time() * 1000)})

            if last_event_id:
                kind, events = event_bus.replay_since(last_event_id)
                if kind == "resync":
                    yield _format_sse({"type": "resync", "timestamp": int(time.time() * 1000)})
                else:
                    for entry in events:
                        yield _format_sse(entry.event, entry.id)

            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield _format_sse(entry.event, entry.id)
                except asyncio.TimeoutError:
                    yield _format_sse({"type": "heartbeat", "timestamp": int(time.time() * 1000)})
        finally:
            unsubscribe()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
