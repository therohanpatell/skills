from __future__ import annotations

import pytest

from pdfocr.config import Settings
from pdfocr.jobs import JobManager
from pdfocr.models import JobStatus
from pdfocr.pdf import PdfError


@pytest.fixture
def manager(tmp_path):
    manager = JobManager(Settings(storage_dir=tmp_path / "storage", engine="native"))
    yield manager
    manager.shutdown()


def test_job_runs_to_completion(manager, text_pdf):
    job = manager.submit("text.pdf", text_pdf.read_bytes(), engine="native")
    final = manager.wait(job.id)

    assert final.status is JobStatus.done
    assert final.progress.percent == 100.0
    assert final.progress.pages_done == 2
    assert "Invoice 2026-08" in final.text()
    assert (manager.job_dir(job.id) / "result.txt").exists()


def test_progress_is_published_to_subscribers(manager, text_pdf):
    job = manager.submit("text.pdf", text_pdf.read_bytes(), engine="native")
    queue = manager.subscribe(job.id)
    manager.wait(job.id)
    manager.unsubscribe(job.id, queue)
    assert not queue.empty()


def test_max_pages_limits_the_job(tmp_path, text_pdf):
    manager = JobManager(Settings(storage_dir=tmp_path / "s", engine="native", max_pages=1))
    try:
        job = manager.submit("text.pdf", text_pdf.read_bytes(), engine="native")
        assert manager.wait(job.id).progress.pages_total == 1
    finally:
        manager.shutdown()


def test_failed_pages_do_not_fail_the_job(manager, text_pdf, monkeypatch):
    from pdfocr.engines.native import NativeEngine

    def only_page_two(self, request):
        if request.number == 1:
            raise RuntimeError("page 1 exploded")
        return "second page"

    monkeypatch.setattr(NativeEngine, "run", only_page_two)
    job = manager.submit("text.pdf", text_pdf.read_bytes(), engine="native")
    final = manager.wait(job.id)

    assert final.status is JobStatus.done
    assert final.progress.pages_failed == 1
    assert final.pages[0].error == "page 1 exploded"
    assert final.text() == "second page"


def test_bad_upload_is_rejected(manager):
    with pytest.raises(PdfError):
        manager.submit("junk.pdf", b"not a pdf at all")


def test_delete_removes_job_and_files(manager, text_pdf):
    job = manager.submit("text.pdf", text_pdf.read_bytes(), engine="native")
    manager.wait(job.id)
    assert manager.delete(job.id) is True
    assert manager.get(job.id) is None
    assert not manager.job_dir(job.id).exists()
