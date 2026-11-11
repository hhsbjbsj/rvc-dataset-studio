"""FastAPI application entry point."""

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from config import get_settings
from database import SessionLocal, init_db
from api.routes_jobs import router as jobs_router
from api.routes_convert import router as convert_router
from api.routes_search import router as search_router
from api.routes_history import router as history_router
from api.websocket import router as ws_router
from api.routes_rvc import router as rvc_router
from api.routes_zip_editor import cleanup_zip_sessions, router as zip_editor_router
from processing.rvc_export import cleanup_export_bundles, migrate_export_folders
from rvc_projects import RvcProject, project_data
from workers.rvc_worker import RvcWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings.ensure_dirs()
    with SessionLocal() as db:
        projects = [project_data(project) for project in db.scalars(select(RvcProject))]
    try:
        await asyncio.to_thread(migrate_export_folders, projects)
    except OSError:
        logger.warning("Could not organize existing RVC export folders", exc_info=True)
    await asyncio.to_thread(cleanup_export_bundles)
    await asyncio.to_thread(cleanup_zip_sessions)
    worker = RvcWorker()
    app.state.rvc_worker = worker
    worker.start()
    logger.info("VoiceForge Dataset Studio started")
    try:
        yield
    finally:
        await asyncio.to_thread(worker.stop)
        logger.info("VoiceForge shutting down")


app = FastAPI(
    title="VoiceForge Dataset Studio",
    description="AI Voice Dataset Creator — search, extract, and package voice datasets",
    version="1.0.0",
    lifespan=lifespan,
)

# The API is unauthenticated, so browsers may only call it from local origins. A wildcard
# here would let any website you visit drive (and delete) your jobs through the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs_router)
app.include_router(convert_router)
app.include_router(search_router)
app.include_router(history_router)
app.include_router(ws_router)
app.include_router(rvc_router)
app.include_router(zip_editor_router)

# Serve packaged outputs from wherever OUTPUT_DIR points (it may live outside the repo).
settings.ensure_dirs()
app.mount("/static/outputs", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="outputs")

if settings.RVC_SERVE_FRONTEND:
    frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if not (frontend / "index.html").is_file():
        raise RuntimeError("The launcher needs frontend/dist. Run setup-rvc.ps1 to build it.")
    app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="frontend-assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def frontend_icon():
        return FileResponse(frontend / "favicon.svg")

    def frontend_index():
        return FileResponse(frontend / "index.html", headers={"Cache-Control": "no-cache"})

    for path in ("/", "/rvc", "/convert", "/zip-editor", "/history", "/history/jobs/{job_id}"):
        app.add_api_route(path, frontend_index, include_in_schema=False)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "voiceforge", "version": "1.0.0"}


@app.get("/api/health")
def api_health():
    return {**rvc_health(), "redis": _check_redis()}


@app.get("/api/rvc/health")
def rvc_health():
    health = {"status": "ok", "rvc": "ready" if shutil.which("ffmpeg") and shutil.which("ffprobe") else "missing_ffmpeg"}
    if settings.RVC_SERVE_FRONTEND and (instance := os.environ.get("RVC_LAUNCHER_INSTANCE")):
        health["launcher_instance"] = instance
    return health


def _check_redis() -> str:
    try:
        from redis import Redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        with Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.4,
            socket_timeout=0.4,
            retry=Retry(NoBackoff(), 0),
        ) as client:
            client.ping()
        return "connected"
    except Exception:
        return "disconnected"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT)
