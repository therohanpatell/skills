"""FastAPI application: upload a PDF, watch the progress, download the text."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .engines import ENGINE_NAMES
from .engines import status as engine_status
from .jobs import JobManager
from .models import HealthResponse, Job, JobStatus, SubmitResponse
from .pdf import PdfError

log = logging.getLogger("pdfocr.api")

STATIC_DIR = Path(__file__).parent / "static"
manager = JobManager(settings)


async def _janitor() -> None:
    """Drop finished jobs (and their PDFs) once they are older than the TTL."""
    while True:
        await asyncio.sleep(600)
        try:
            removed = manager.purge_expired()
            if removed:
                log.info("purged %d expired job(s)", removed)
        except Exception:  # pragma: no cover - the janitor must never die
            log.exception("purge failed")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    manager.bind_loop(asyncio.get_running_loop())
    task = asyncio.create_task(_janitor())
    try:
        yield
    finally:
        task.cancel()
        manager.shutdown()


app = FastAPI(
    title="pdfocr",
    version=__version__,
    summary="Local PDF to text OCR service backed by a vision LLM",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """No-op unless PDFOCR_API_KEY is set - a LAN service usually does not need one."""
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


def _get_job(job_id: str) -> Job:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
    return job


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        engines=engine_status(settings),
        default_engine=settings.engine,
    )


@app.post(
    "/api/jobs",
    response_model=SubmitResponse,
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def submit_job(
    file: UploadFile = File(..., description="the PDF to transcribe"),
    engine: str = Form(default=""),
    dpi: int = Form(default=0),
) -> SubmitResponse:
    engine = (engine or settings.engine).strip()
    if engine not in ENGINE_NAMES:
        raise HTTPException(
            status_code=400, detail=f"engine must be one of {', '.join(ENGINE_NAMES)}"
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail=f"file larger than the {settings.max_upload_mb} MB limit"
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="that does not look like a PDF")

    try:
        job = manager.submit(file.filename or "document.pdf", data, engine=engine, dpi=dpi or None)
    except PdfError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SubmitResponse(
        id=job.id,
        status=job.status,
        events_url=f"/api/jobs/{job.id}/events",
        result_url=f"/api/jobs/{job.id}/result",
    )


@app.get("/api/jobs", response_model=list[Job], dependencies=[Depends(require_api_key)])
def list_jobs() -> list[Job]:
    return manager.list()


@app.get("/api/jobs/{job_id}", response_model=Job, dependencies=[Depends(require_api_key)])
def get_job(job_id: str) -> Job:
    return _get_job(job_id)


@app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def delete_job(job_id: str) -> dict:
    if not manager.delete(job_id):
        raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
    return {"deleted": job_id}


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_job(job_id: str) -> dict:
    _get_job(job_id)
    return {"cancelled": manager.cancel(job_id)}


@app.get("/api/jobs/{job_id}/events", dependencies=[Depends(require_api_key)])
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events carrying every progress update for one job."""
    _get_job(job_id)
    queue = manager.subscribe(job_id)

    finished = (JobStatus.done, JobStatus.failed, JobStatus.cancelled)

    def settled(job: Job | None) -> bool:
        return job is None or job.status in finished

    async def stream() -> AsyncIterator[str]:
        try:
            snapshot = manager.get(job_id)
            if snapshot is not None:
                yield f"data: {snapshot.model_dump_json()}\n\n"
            if settled(snapshot):  # job finished before the client connected
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    if settled(manager.get(job_id)):
                        return
                    yield ": keep-alive\n\n"  # stops proxies from closing an idle stream
                    continue
                yield f"data: {payload}\n\n"
                if settled(manager.get(job_id)):
                    return
        finally:
            manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/jobs/{job_id}/result", dependencies=[Depends(require_api_key)])
def job_result(job_id: str, format: str = "txt", download: bool = False):
    job = _get_job(job_id)
    if job.status is not JobStatus.done:
        raise HTTPException(status_code=409, detail=f"job is {job.status.value}, not done")

    if format == "json":
        return job
    if format not in ("txt", "md"):
        raise HTTPException(status_code=400, detail="format must be txt, md or json")

    text = job.text()
    if download:
        stem = Path(job.filename).stem or job.id
        path = manager.job_dir(job.id) / f"result.{format}"
        path.write_text(text, encoding="utf-8")
        return FileResponse(path, media_type="text/plain", filename=f"{stem}.{format}")
    return PlainTextResponse(text)


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")


def run() -> None:  # pragma: no cover - entrypoint
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
