"""
DataPulse deploy rehearsal backend.

A small FastAPI app shaped like the real DataPulse backend so the Render
deploy can be rehearsed end to end. It covers:

  1. Health check + deployed version     -> GET  /health
  2. Secrets from environment variables  -> GET  /config-check (never prints values)
  3. Basic CRUD (in memory)              -> /items
  4. Long-running job + polling          -> POST /investigations, GET /investigations/{id}
  5. Reading Markdown files (KB)         -> GET  /kb
  6. CORS for the Vercel frontend        -> ALLOWED_ORIGINS env var
  7. Simple API-key protection           -> API_KEY env var, header X-API-Key

All data is in memory: it disappears on every restart, redeploy or Render
free-tier spin-down. That is expected for this rehearsal.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration (all from environment variables)
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("datapulse")

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_KB_PATH = BACKEND_DIR.parent / "knowledge-base"
KB_PATH = Path(os.getenv("KB_PATH", str(DEFAULT_KB_PATH))).resolve()

ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
API_KEY = os.getenv("API_KEY", "")
INVESTIGATION_SECONDS = float(os.getenv("INVESTIGATION_SECONDS", "20"))

# Render sets RENDER_GIT_COMMIT automatically; locally it is "local".
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", "local")[:7]
STARTED_AT = time.time()

# Env vars the real backend will need. /config-check reports only whether each is set.
EXPECTED_ENV_VARS = ["ALLOWED_ORIGINS", "API_KEY", "DUMMY_SECRET"]


def list_kb_files() -> list[str]:
    if not KB_PATH.is_dir():
        return []
    return sorted(p.name for p in KB_PATH.glob("*.md"))


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    kb_files = list_kb_files()
    log.info("Starting DataPulse rehearsal backend, commit=%s", GIT_COMMIT)
    log.info("KB_PATH=%s, kb_files_found=%d", KB_PATH, len(kb_files))
    if not kb_files:
        log.warning("No knowledge-base files found. Check KB_PATH and the Render root directory.")
    log.info("CORS allowed origins: %s", ALLOWED_ORIGINS or "(none set)")
    log.info("API key protection: %s", "ON" if API_KEY else "OFF")
    yield


app = FastAPI(title="DataPulse Deploy Rehearsal", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """If API_KEY is set, every protected route needs header X-API-Key."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid X-API-Key")


# ---------------------------------------------------------------------------
# Public routes (no API key): used by Render's health check and for quick checks
# ---------------------------------------------------------------------------

@app.get("/")
def root() -> dict:
    return {"service": "datapulse-rehearsal-backend", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "commit": GIT_COMMIT,
        "uptime_seconds": round(time.time() - STARTED_AT),
        "kb_files_found": len(list_kb_files()),
    }


# ---------------------------------------------------------------------------
# Protected routes
# ---------------------------------------------------------------------------

@app.get("/config-check", dependencies=[Depends(require_api_key)])
def config_check() -> dict:
    """Shows which env vars are set. Never returns their values."""
    return {
        "env_vars_set": {name: bool(os.getenv(name)) for name in EXPECTED_ENV_VARS},
        "kb_path": str(KB_PATH),
        "allowed_origins": ALLOWED_ORIGINS,
    }


@app.get("/kb", dependencies=[Depends(require_api_key)])
def kb_files() -> dict:
    files = list_kb_files()
    return {"kb_path": str(KB_PATH), "count": len(files), "files": files}


# ---- CRUD: items (in memory) ----------------------------------------------

class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""


class Item(ItemIn):
    id: str
    created_at: str


ITEMS: dict[str, Item] = {}


@app.post("/items", status_code=201, dependencies=[Depends(require_api_key)])
def create_item(body: ItemIn) -> Item:
    item = Item(id=str(uuid.uuid4())[:8], created_at=datetime.now(timezone.utc).isoformat(), **body.model_dump())
    ITEMS[item.id] = item
    return item


@app.get("/items", dependencies=[Depends(require_api_key)])
def list_items() -> list[Item]:
    return list(ITEMS.values())


@app.get("/items/{item_id}", dependencies=[Depends(require_api_key)])
def get_item(item_id: str) -> Item:
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail="Item not found")
    return ITEMS[item_id]


@app.put("/items/{item_id}", dependencies=[Depends(require_api_key)])
def update_item(item_id: str, body: ItemIn) -> Item:
    if item_id not in ITEMS:
        raise HTTPException(status_code=404, detail="Item not found")
    updated = ITEMS[item_id].model_copy(update=body.model_dump())
    ITEMS[item_id] = updated
    return updated


@app.delete("/items/{item_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_item(item_id: str) -> None:
    if ITEMS.pop(item_id, None) is None:
        raise HTTPException(status_code=404, detail="Item not found")


# ---- Long-running job: start, then poll ------------------------------------
# Mirrors the real flow: frontend starts an investigation, gets a job id back
# immediately, then polls until the result is ready.

class InvestigationIn(BaseModel):
    run_id: str = Field(min_length=1, max_length=50)


JOBS: dict[str, dict] = {}
_RUNNING_TASKS: set[asyncio.Task] = set()  # keep references so tasks are not garbage-collected


async def run_fake_investigation(job_id: str, run_id: str) -> None:
    JOBS[job_id]["status"] = "running"
    try:
        await asyncio.sleep(INVESTIGATION_SECONDS)  # stands in for Databricks + LLM calls
        JOBS[job_id]["result"] = {
            "run_id": run_id,
            "failed_stage": "silver_layer_transformation (dummy)",
            "kb_files_consulted": list_kb_files(),
            "next_check": "This is a rehearsal result, not a real investigation.",
        }
        JOBS[job_id]["status"] = "completed"
    except Exception as exc:  # report failures instead of losing them
        log.exception("Investigation %s failed", job_id)
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(exc)
    finally:
        JOBS[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.post("/investigations", status_code=202, dependencies=[Depends(require_api_key)])
async def start_investigation(body: InvestigationIn) -> dict:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "job_id": job_id,
        "run_id": body.run_id,
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
    }
    task = asyncio.create_task(run_fake_investigation(job_id, body.run_id))
    _RUNNING_TASKS.add(task)
    task.add_done_callback(_RUNNING_TASKS.discard)
    log.info("Started investigation job_id=%s run_id=%s", job_id, body.run_id)
    return {"job_id": job_id, "status": "queued", "poll_url": f"/investigations/{job_id}"}


@app.get("/investigations/{job_id}", dependencies=[Depends(require_api_key)])
def get_investigation(job_id: str) -> dict:
    if job_id not in JOBS:
        # After a restart/redeploy/spin-down, old job ids are gone. The frontend should handle 404.
        raise HTTPException(status_code=404, detail="Job not found (it may have been lost on restart)")
    return JOBS[job_id]
