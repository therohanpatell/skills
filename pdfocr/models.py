"""Pydantic schemas shared by the API, the CLI and the web UI."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class PageStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class PageResult(BaseModel):
    number: int = Field(..., description="1-based page number")
    status: PageStatus = PageStatus.pending
    engine: str | None = None
    text: str = ""
    chars: int = 0
    duration_ms: int | None = None
    error: str | None = None


class JobProgress(BaseModel):
    pages_total: int = 0
    pages_done: int = 0
    pages_failed: int = 0
    percent: float = 0.0
    stage: str = "queued"
    message: str = ""
    eta_seconds: float | None = None


class JobSummary(BaseModel):
    id: str
    filename: str
    status: JobStatus
    engine: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: JobProgress
    error: str | None = None


class Job(JobSummary):
    pages: list[PageResult] = Field(default_factory=list)

    def text(self, separator: str = "\n\n") -> str:
        parts = []
        for page in self.pages:
            body = page.text.strip()
            if body:
                parts.append(body)
        return separator.join(parts)


class SubmitResponse(BaseModel):
    id: str
    status: JobStatus
    events_url: str
    result_url: str


class EngineInfo(BaseModel):
    name: str
    available: bool
    detail: str = ""


class HealthResponse(BaseModel):
    status: str
    version: str
    engines: list[EngineInfo]
    default_engine: str
