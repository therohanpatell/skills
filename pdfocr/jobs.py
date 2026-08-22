"""Job manager: queues PDFs, runs OCR page by page and broadcasts live progress."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .config import settings as default_settings
from .engines import OcrError, PageRequest, resolve
from .models import Job, JobProgress, JobStatus, PageResult, PageStatus
from .pdf import PdfError, page_count

log = logging.getLogger("pdfocr.jobs")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobCancelled(Exception):
    """Raised inside a worker when the job was cancelled by the client."""


class JobManager:
    """Owns every job's state, its worker threads and its event subscribers."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or default_settings
        self._jobs: dict[str, Job] = {}
        self._cancelled: set[str] = set()
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, self.config.job_concurrency), thread_name_prefix="pdfocr-job"
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self.root = Path(self.config.storage_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- lifecycle -------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the server's event loop so worker threads can push SSE events."""
        self._loop = loop

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- storage ---------------------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.root / job_id

    def input_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "input.pdf"

    # -- queries ---------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self) -> list[Job]:
        with self._lock:
            jobs = [job.model_copy(deep=True) for job in self._jobs.values()]
        return sorted(jobs, key=lambda job: job.created_at, reverse=True)

    # -- submission ------------------------------------------------------------
    def submit(
        self, filename: str, data: bytes, engine: str | None = None, dpi: int | None = None
    ) -> Job:
        job_id = uuid.uuid4().hex[:16]
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.input_path(job_id)
        path.write_bytes(data)

        try:
            total = page_count(path)
        except PdfError:
            shutil.rmtree(directory, ignore_errors=True)
            raise

        limit = self.config.max_pages
        if limit and total > limit:
            total = limit

        job = Job(
            id=job_id,
            filename=filename,
            status=JobStatus.queued,
            engine=engine or self.config.engine,
            created_at=_now(),
            progress=JobProgress(pages_total=total, stage="queued", message="waiting for a worker"),
            pages=[PageResult(number=n) for n in range(1, total + 1)],
        )
        with self._lock:
            self._jobs[job_id] = job

        self._pool.submit(self._run, job_id, dpi or self.config.dpi)
        return job.model_copy(deep=True)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in (JobStatus.done, JobStatus.failed, JobStatus.cancelled):
                return False
            self._cancelled.add(job_id)
            if job.status is JobStatus.queued:
                job.status = JobStatus.cancelled
                job.finished_at = _now()
                job.progress.stage = "cancelled"
                job.progress.message = "cancelled before it started"
        self._publish(job_id, "status")
        return True

    def delete(self, job_id: str) -> bool:
        self.cancel(job_id)
        with self._lock:
            existed = self._jobs.pop(job_id, None) is not None
            self._subscribers.pop(job_id, None)
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return existed

    def purge_expired(self) -> int:
        cutoff = time.time() - self.config.job_ttl_seconds
        removed = 0
        for job in self.list():
            if job.created_at.timestamp() < cutoff:
                removed += int(self.delete(job.id))
        return removed

    # -- events ----------------------------------------------------------------
    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        with self._lock:
            self._subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            listeners = self._subscribers.get(job_id)
            if listeners:
                listeners.discard(queue)
                if not listeners:
                    self._subscribers.pop(job_id, None)

    def _publish(self, job_id: str, event: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            listeners = list(self._subscribers.get(job_id, ()))
        if job is None or not listeners:
            return
        payload = json.dumps(
            {
                "event": event,
                "id": job.id,
                "status": job.status.value,
                "engine": job.engine,
                "progress": job.progress.model_dump(),
                "pages": [
                    {
                        "number": page.number,
                        "status": page.status.value,
                        "chars": page.chars,
                        "error": page.error,
                    }
                    for page in job.pages
                ],
                "error": job.error,
            }
        )
        loop = self._loop
        for queue in listeners:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._offer, queue, payload)
            else:  # tests and CLI use, no server loop running
                self._offer(queue, payload)

    @staticmethod
    def _offer(queue: asyncio.Queue, payload: str) -> None:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:  # a slow client must not stall OCR
            pass

    # -- execution -------------------------------------------------------------
    def _check_cancelled(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._cancelled:
                raise JobCancelled

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)

    def _recompute_progress(self, job_id: str, stage: str, message: str, started: float) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            done = sum(1 for page in job.pages if page.status is PageStatus.done)
            failed = sum(1 for page in job.pages if page.status is PageStatus.failed)
            total = max(1, job.progress.pages_total)
            settled = done + failed
            elapsed = time.monotonic() - started
            job.progress = JobProgress(
                pages_total=job.progress.pages_total,
                pages_done=done,
                pages_failed=failed,
                percent=round(100.0 * settled / total, 1),
                stage=stage,
                message=message,
                eta_seconds=(
                    round(elapsed / settled * (total - settled), 1)
                    if settled and settled < total
                    else None
                ),
            )

    def _run(self, job_id: str, dpi: int) -> None:
        started = time.monotonic()
        path = self.input_path(job_id)
        try:
            self._check_cancelled(job_id)
            self._update(job_id, status=JobStatus.running, started_at=_now())
            self._recompute_progress(job_id, "preparing", "selecting an OCR engine", started)
            self._publish(job_id, "status")

            job = self.get(job_id)
            assert job is not None
            engine = resolve(job.engine, path, self.config)
            self._update(job_id, engine=engine.name)
            self._recompute_progress(job_id, "ocr", f"running {engine.name}", started)
            self._publish(job_id, "progress")

            numbers = [page.number for page in job.pages]
            workers = max(1, self.config.page_concurrency)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pdfocr-page") as pool:
                futures = [
                    pool.submit(self._run_page, job_id, engine, number, dpi) for number in numbers
                ]
                for future in as_completed(futures):
                    future.result()
                    current = self.get(job_id)
                    progress = current.progress if current else None
                    done = progress.pages_done + progress.pages_failed if progress else 0
                    self._recompute_progress(
                        job_id, "ocr", f"page {done}/{len(numbers)} with {engine.name}", started
                    )
                    self._publish(job_id, "progress")

            self._check_cancelled(job_id)
            self._finish(job_id, started)
        except JobCancelled:
            self._update(job_id, status=JobStatus.cancelled, finished_at=_now())
            self._recompute_progress(job_id, "cancelled", "cancelled by client", started)
            self._publish(job_id, "status")
        except (OcrError, PdfError) as exc:
            log.warning("job %s failed: %s", job_id, exc)
            self._update(job_id, status=JobStatus.failed, finished_at=_now(), error=str(exc))
            self._recompute_progress(job_id, "failed", str(exc), started)
            self._publish(job_id, "status")
        except Exception as exc:  # pragma: no cover - unexpected, still must not kill the worker
            log.exception("job %s crashed", job_id)
            self._update(job_id, status=JobStatus.failed, finished_at=_now(), error=repr(exc))
            self._recompute_progress(job_id, "failed", repr(exc), started)
            self._publish(job_id, "status")
        finally:
            with self._lock:
                self._cancelled.discard(job_id)

    def _run_page(self, job_id: str, engine, number: int, dpi: int) -> None:
        self._check_cancelled(job_id)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobCancelled
            job.pages[number - 1].status = PageStatus.running

        page_started = time.monotonic()
        try:
            text = engine.run(PageRequest(path=self.input_path(job_id), number=number, dpi=dpi))
        except JobCancelled:
            raise
        except Exception as exc:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    page = job.pages[number - 1]
                    page.status = PageStatus.failed
                    page.error = str(exc)
                    page.engine = engine.name
                    page.duration_ms = int((time.monotonic() - page_started) * 1000)
            return

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                page = job.pages[number - 1]
                page.status = PageStatus.done
                page.text = text
                page.chars = len(text)
                page.engine = engine.name
                page.duration_ms = int((time.monotonic() - page_started) * 1000)

    def _finish(self, job_id: str, started: float) -> None:
        job = self.get(job_id)
        if job is None:
            return
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "result.txt").write_text(job.text(), encoding="utf-8")
        (directory / "result.json").write_text(
            job.model_dump_json(indent=2), encoding="utf-8"
        )
        failed = sum(1 for page in job.pages if page.status is PageStatus.failed)
        message = "complete" if not failed else f"complete with {failed} failed page(s)"
        self._update(job_id, status=JobStatus.done, finished_at=_now())
        self._recompute_progress(job_id, "done", message, started)
        with self._lock:
            current = self._jobs.get(job_id)
            if current is not None:
                current.progress.percent = 100.0
        self._publish(job_id, "done")

    # -- synchronous helper (CLI) ---------------------------------------------
    def wait(self, job_id: str, poll: float = 0.25) -> Job:
        while True:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.status in (JobStatus.done, JobStatus.failed, JobStatus.cancelled):
                return job
            time.sleep(poll)

    def watch(self, job_id: str, poll: float = 0.25) -> Iterator[Job]:
        """Yield the job state until it settles - used by the CLI progress bar."""
        while True:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            yield job
            if job.status in (JobStatus.done, JobStatus.failed, JobStatus.cancelled):
                return
            time.sleep(poll)
