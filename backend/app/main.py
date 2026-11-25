from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.client import close_db, init_db
from app.routers import (
    agents,
    artifacts,
    attachments,
    conversations,
    deployments,
    fs,
    messages,
    misc,
    mobile,
    pending,
    stream,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield
    close_db()


app = FastAPI(title="AgentHub FastAPI Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stream.router)
app.include_router(agents.router)
app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(artifacts.router)
app.include_router(attachments.router)
app.include_router(fs.router)
app.include_router(pending.router)
app.include_router(deployments.router)
app.include_router(misc.router)
app.include_router(mobile.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
