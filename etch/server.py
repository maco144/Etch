"""
Etch server — standalone FastAPI application.

Usage:
    uvicorn etch.server:app --reload
    # or
    python -m etch.server
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import router as proof_router
from .db import create_tables

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup (dev convenience)."""
    await create_tables()
    logger.info("[Etch] Server ready — chain initialized")
    yield


app = FastAPI(
    title="Etch",
    description="Content provenance on a tamper-evident Merkle chain",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(proof_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "etch"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("etch.server:app", host="0.0.0.0", port=8100, reload=True)
