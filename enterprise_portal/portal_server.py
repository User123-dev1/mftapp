"""
Enterprise App Portal

A single landing page that lists every internal application (MFT, Asset
Manager, Label Generator, Service Desk, ...) with live up/down status.

Apps are defined in apps.json, so adding a new one needs no code change.

Run:
    python portal_server.py              # http://localhost:8080
    PORTAL_PORT=9000 python portal_server.py
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
APPS_FILE = Path(os.getenv("PORTAL_APPS_FILE", BASE_DIR / "apps.json"))

HEALTH_TIMEOUT = float(os.getenv("PORTAL_HEALTH_TIMEOUT", "3"))
HEALTH_CACHE_SECONDS = float(os.getenv("PORTAL_HEALTH_CACHE", "30"))


class AppEntry(BaseModel):
    id: str
    name: str
    description: str = ""
    url: str
    health_url: Optional[str] = None
    category: str = "General"
    icon: str = "apps"
    color: str = "#4f46e5"
    owner: str = ""


class PortalInfo(BaseModel):
    title: str = "Enterprise App Portal"
    organization: str = ""
    support_email: str = ""


class Catalog(BaseModel):
    portal: PortalInfo = PortalInfo()
    apps: List[AppEntry] = []


class AppStatus(BaseModel):
    id: str
    status: str  # "up" | "down"
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    checked_at: float


def load_catalog() -> Catalog:
    """Read apps.json on every call so edits show up without a restart."""
    try:
        with open(APPS_FILE, "r", encoding="utf-8") as f:
            catalog = Catalog(**json.load(f))
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"App catalog not found: {APPS_FILE}")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Invalid app catalog: {e}")

    ids = [a.id for a in catalog.apps]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=500, detail="Duplicate app ids in catalog")
    return catalog


async def check_app(client: httpx.AsyncClient, app_entry: AppEntry) -> AppStatus:
    """An app is 'up' if it answers with anything below 500.

    Login redirects (3xx) and 401/403 still mean the app is running.
    """
    target = app_entry.health_url or app_entry.url
    start = time.perf_counter()
    try:
        response = await client.get(target)
        latency = int((time.perf_counter() - start) * 1000)
        state = "up" if response.status_code < 500 else "down"
        return AppStatus(id=app_entry.id, status=state, http_status=response.status_code,
                         latency_ms=latency, checked_at=time.time())
    except httpx.HTTPError as e:
        logger.debug("Health check failed for %s: %s", app_entry.id, e)
        return AppStatus(id=app_entry.id, status="down", checked_at=time.time())


_status_cache: Dict[str, AppStatus] = {}


async def check_all(apps: List[AppEntry], force: bool = False) -> List[AppStatus]:
    now = time.time()
    stale = [a for a in apps
             if force or a.id not in _status_cache
             or now - _status_cache[a.id].checked_at > HEALTH_CACHE_SECONDS]

    if stale:
        async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT, follow_redirects=False) as client:
            results = await asyncio.gather(*(check_app(client, a) for a in stale))
        for result in results:
            _status_cache[result.id] = result

    return [_status_cache[a.id] for a in apps]


app = FastAPI(title="Enterprise App Portal", version="1.0.0")


@app.get("/api/apps", response_model=Catalog)
async def list_apps():
    catalog = load_catalog()
    # health_url is internal plumbing; the browser only needs the launch url
    for entry in catalog.apps:
        entry.health_url = None
    return catalog


@app.get("/api/status", response_model=List[AppStatus])
async def apps_status(refresh: bool = False):
    return await check_all(load_catalog().apps, force=refresh)


@app.get("/health")
async def health():
    return {"status": "healthy", "apps": len(load_catalog().apps)}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app,
                host=os.getenv("PORTAL_HOST", "0.0.0.0"),
                port=int(os.getenv("PORTAL_PORT", "8080")))
