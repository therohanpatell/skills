from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from pdfocr import api
from pdfocr.config import Settings
from pdfocr.jobs import JobManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    manager = JobManager(Settings(storage_dir=tmp_path / "storage", engine="native"))
    monkeypatch.setattr(api, "manager", manager)
    with TestClient(api.app) as client:
        yield client
    manager.shutdown()


def _wait_for_done(client, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish in time")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert {engine["name"] for engine in body["engines"]} == {"vlm", "native", "tesseract"}


def test_submit_and_fetch_result(client, text_pdf):
    response = client.post(
        "/api/jobs",
        files={"file": ("text.pdf", text_pdf.read_bytes(), "application/pdf")},
        data={"engine": "native"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]

    job = _wait_for_done(client, job_id)
    assert job["status"] == "done"
    assert job["progress"]["percent"] == 100.0

    text = client.get(f"/api/jobs/{job_id}/result").text
    assert "Invoice 2026-08" in text

    as_json = client.get(f"/api/jobs/{job_id}/result", params={"format": "json"}).json()
    assert len(as_json["pages"]) == 2

    download = client.get(f"/api/jobs/{job_id}/result", params={"download": True})
    assert "attachment" in download.headers["content-disposition"]


def test_result_before_completion_is_conflict(client, text_pdf):
    job_id = client.post(
        "/api/jobs",
        files={"file": ("text.pdf", text_pdf.read_bytes(), "application/pdf")},
        data={"engine": "native"},
    ).json()["id"]
    _wait_for_done(client, job_id)
    assert client.get(f"/api/jobs/{job_id}/result", params={"format": "xml"}).status_code == 400


def test_non_pdf_upload_rejected(client):
    response = client.post("/api/jobs", files={"file": ("note.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_empty_upload_rejected(client):
    response = client.post("/api/jobs", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert response.status_code == 400


def test_unknown_engine_rejected(client, text_pdf):
    response = client.post(
        "/api/jobs",
        files={"file": ("text.pdf", text_pdf.read_bytes(), "application/pdf")},
        data={"engine": "magic"},
    )
    assert response.status_code == 400


def test_missing_job_is_404(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404


def test_events_stream_reports_completion(client, text_pdf):
    job_id = client.post(
        "/api/jobs",
        files={"file": ("text.pdf", text_pdf.read_bytes(), "application/pdf")},
        data={"engine": "native"},
    ).json()["id"]

    states = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                states.append(json.loads(line[6:]))
                if states[-1]["status"] in ("done", "failed", "cancelled"):
                    break

    assert states, "the stream produced no events"
    assert states[-1]["status"] == "done"
    assert states[-1]["progress"]["pages_total"] == 2


def test_api_key_is_enforced(tmp_path, monkeypatch, text_pdf):
    monkeypatch.setattr(api.settings, "api_key", "s3cret")
    manager = JobManager(Settings(storage_dir=tmp_path / "storage", engine="native"))
    monkeypatch.setattr(api, "manager", manager)
    try:
        with TestClient(api.app) as client:
            files = {"file": ("text.pdf", text_pdf.read_bytes(), "application/pdf")}
            assert client.post("/api/jobs", files=files).status_code == 401
            ok = client.post("/api/jobs", files=files, headers={"X-API-Key": "s3cret"})
            assert ok.status_code == 202
    finally:
        manager.shutdown()
