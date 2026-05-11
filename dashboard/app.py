"""
FastAPI application — includes lifespan (DB init + APScheduler),
API routes, and the dashboard HTML endpoint.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import storage.database as db
from api.routes import router
from config import COLLECTION_INTERVAL_MINUTES
from utils.logger import logger

_templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)
_scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise database
    db.init_db()
    logger.info("Database initialised.")

    # Import here to avoid a circular import at module level
    from scheduler import collect_and_analyze

    _scheduler.add_job(
        collect_and_analyze,
        "interval",
        minutes=COLLECTION_INTERVAL_MINUTES,
        id="collect_and_analyze",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        f"Scheduler started — collection every {COLLECTION_INTERVAL_MINUTES} min."
    )

    # Run one cycle immediately so the dashboard has data straight away
    logger.info("Running initial collection…")
    await collect_and_analyze()

    yield

    _scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


app = FastAPI(
    title="Narrative Radar",
    description="Local crypto narrative & momentum detection engine",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _templates.TemplateResponse(
        request=request,
        name="index.html",
        context = {}
    )


@app.get("/health")
def health():
    return {"status": "ok"}
