"""
Etch server — standalone FastAPI application.

Serves both the legacy /v1/proof API and the new /v1/records SoR API.

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
from .assent_api import assent_router, ensure_assent_namespace
from .assent_docs_api import assent_docs_router
from .c2pa import c2pa_router
from .dooh_api import dooh_router
from .glyph_api import glyph_router
from .records_api import records_router
from .db import create_tables

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables + bootstrap public namespaces on startup (dev convenience)."""
    await create_tables()
    await ensure_assent_namespace()
    logger.info("[Etch] Server ready — chain initialized (v1/proof + v1/records + v1/assent)")
    yield


app = FastAPI(
    title="Etch",
    description="System of Record provenance on a tamper-evident Merkle chain",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(proof_router)
app.include_router(c2pa_router)
app.include_router(records_router)
app.include_router(dooh_router)
app.include_router(assent_router)
app.include_router(assent_docs_router)
app.include_router(glyph_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "etch", "version": "0.2.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("etch.server:app", host="0.0.0.0", port=8100, reload=True)
