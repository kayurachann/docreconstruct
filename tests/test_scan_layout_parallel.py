from __future__ import annotations

import io
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from PIL import Image, ImageDraw

from docreconstruct.reconstruction import scan_layout
from docreconstruct.reconstruction.scan_layout import PixelBox, ScanPageLayout


def _page(number: int, image: Image.Image) -> ScanPageLayout:
    return ScanPageLayout(
        number=number,
        width=image.width,
        height=image.height,
        pdf_width=595.0,
        pdf_height=842.0,
        content_bbox=PixelBox(x0=1, y0=1, x1=image.width, y1=image.height),
        line_pitch=12.0,
        metadata={"source_kind": "pdf", "rectified": False},
        image=image,
    )


def _pdf_path(tmp_path: Path) -> Path:
    path = tmp_path / "layout.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    return path


def test_scan_page_worker_count_is_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan_layout.os, "cpu_count", lambda: 16)

    assert scan_layout._scan_page_worker_count(0, None) == 0
    assert scan_layout._scan_page_worker_count(3, None) == 3
    assert scan_layout._scan_page_worker_count(12, None) == 4
    assert scan_layout._scan_page_worker_count(12, 6) == 6
    assert scan_layout._scan_page_worker_count(12, 100) == 8

    monkeypatch.setattr(scan_layout.os, "cpu_count", lambda: 2)
    assert scan_layout._scan_page_worker_count(12, None) == 2
    assert scan_layout._scan_page_worker_count(12, 8) == 2

    for invalid in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            scan_layout._scan_page_worker_count(4, invalid)  # type: ignore[arg-type]


def _rotated_scan_pdf(path: Path, rotation: int) -> None:
    """Write a one-raster scan page carrying ``/Rotate``, as scanners emit."""

    pymupdf = pytest.importorskip("pymupdf")
    image = Image.new("RGB", (612, 792), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 120, 70), fill="black")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    document = pymupdf.open()
    try:
        page = document.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=stream.getvalue())
        if rotation:
            page.set_rotation(rotation)
        document.save(path)
    finally:
        document.close()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_page_rotation_is_applied_by_both_pdf_extractors(tmp_path: Path, rotation: int) -> None:
    """A `/Rotate` page must reach analysis in display orientation.

    The MediaBox and the stored raster are both in unrotated page space. The
    pypdf extractor is tried first and used to report the raw pair, so a
    landscape scan saved as a portrait page with `/Rotate 90` was analyzed
    sideways against a portrait page size, while the PyMuPDF fallback called
    the same file landscape.
    """

    path = tmp_path / f"rotate-{rotation}.pdf"
    _rotated_scan_pdf(path, rotation)

    image, pdf_width, pdf_height = scan_layout._extract_with_pypdf(path)[0]
    _, fallback_width, fallback_height = scan_layout._extract_with_pymupdf(path, 96)[0]

    # Both backends must describe the same physical page.
    assert (pdf_width, pdf_height) == (fallback_width, fallback_height)
    quarter_turn = bool(rotation % 180)
    assert (pdf_width > pdf_height) is quarter_turn
    # The raster must be turned with it, not left in stored orientation.
    assert (image.width > image.height) is quarter_turn


def test_page_workers_are_only_used_when_they_repay_their_start_up() -> None:
    startup = scan_layout._PAGE_POOL_STARTUP_SECONDS

    # A pool that cannot outrun its own start-up must not be created.
    assert not scan_layout._page_workers_are_worthwhile(0, 10.0, 4)
    assert not scan_layout._page_workers_are_worthwhile(3, 0.05, 4)
    assert not scan_layout._page_workers_are_worthwhile(11, 0.1, 4)
    # One remaining page can never be spread across workers.
    assert not scan_layout._page_workers_are_worthwhile(1, 60.0, 4)
    # Neither can a single worker.
    assert not scan_layout._page_workers_are_worthwhile(8, 60.0, 1)

    # Genuinely slow pages still get the pool.
    assert scan_layout._page_workers_are_worthwhile(3, 0.9, 4)
    assert scan_layout._page_workers_are_worthwhile(11, 0.8, 4)

    # The boundary follows the saving, not the page count.
    assert not scan_layout._page_workers_are_worthwhile(2, startup, 2)
    assert scan_layout._page_workers_are_worthwhile(2, startup * 1.01 + 0.01, 2)


def test_pdf_page_analysis_is_parallel_but_returns_source_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _pdf_path(tmp_path)
    images = [Image.new("RGB", (40, 60), "white") for _ in range(4)]
    monkeypatch.setattr(
        scan_layout,
        "_extract_with_pypdf",
        lambda _path: [(image, 595.0, 842.0) for image in images],
    )
    monkeypatch.setattr(scan_layout.os, "cpu_count", lambda: 8)
    # The stub analyzer below is instant, so force the pool the production
    # heuristic would correctly decline for work this cheap.
    monkeypatch.setattr(scan_layout, "_PAGE_POOL_STARTUP_SECONDS", -1.0)

    class ThreadedExecutor:
        def __init__(self, *, max_workers: int) -> None:
            self.executor = ThreadPoolExecutor(max_workers=max_workers)

        def __enter__(self) -> ThreadedExecutor:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            self.executor.shutdown(wait=True)

        def map(
            self,
            function: Callable[[Any], ScanPageLayout],
            items: Iterable[Any],
        ) -> Iterable[ScanPageLayout]:
            return self.executor.map(function, items)

    monkeypatch.setattr(scan_layout, "ProcessPoolExecutor", ThreadedExecutor)

    lock = threading.Lock()
    pooled_started = threading.Event()
    finished = {number: threading.Event() for number in range(1, 5)}
    started_numbers: list[int] = []
    completion_order: list[int] = []
    thread_ids: set[int] = set()
    caller = threading.get_ident()

    def analyze(work_item: tuple[int, Image.Image, float, float]) -> ScanPageLayout:
        number, image, _pdf_width, _pdf_height = work_item
        with lock:
            started_numbers.append(number)
            thread_ids.add(threading.get_ident())
            # Page one is the timed probe and runs before the pool exists; the
            # remaining pages must all be in flight together.
            if len(started_numbers) == 4:
                pooled_started.set()
        if number > 1:
            assert pooled_started.wait(timeout=3.0)
            if number < 4:
                assert finished[number + 1].wait(timeout=3.0)
        with lock:
            completion_order.append(number)
        finished[number].set()
        return _page(number, image)

    monkeypatch.setattr(scan_layout, "_analyze_extracted_pdf_page", analyze)

    document = scan_layout.analyze_scan_source(path)

    # The probe page runs on the calling thread and the other three overlap.
    assert started_numbers[0] == 1
    assert caller in thread_ids
    assert len(thread_ids) == 4
    assert completion_order == [1, 4, 3, 2]
    assert [page.number for page in document.pages] == [1, 2, 3, 4]


def test_parallel_and_serial_page_analysis_have_identical_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    path = _pdf_path(tmp_path)
    images: list[Image.Image] = []
    for page_number in range(1, 5):
        image = Image.new("RGB", (180, 240), "white")
        draw = ImageDraw.Draw(image)
        for top in range(24 + page_number, 216, 18):
            draw.rectangle((16, top, 164 - page_number, top + 5), fill="black")
        images.append(image)
    extracted = [(image, 595.0, 842.0) for image in images]
    monkeypatch.setattr(scan_layout, "_extract_with_pypdf", lambda _path: extracted)
    monkeypatch.setattr(scan_layout.os, "cpu_count", lambda: 8)

    serial = scan_layout.analyze_scan_pdf(path, maximum_workers=1)
    # These pages are far too cheap for the heuristic to fund a pool, so force
    # one to keep this an actual cross-process comparison.
    monkeypatch.setattr(scan_layout, "_PAGE_POOL_STARTUP_SECONDS", -1.0)
    parallel = scan_layout.analyze_scan_pdf(path, maximum_workers=4)

    assert [page.model_dump() for page in parallel.pages] == [
        page.model_dump() for page in serial.pages
    ]


def test_single_page_analysis_stays_on_calling_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _pdf_path(tmp_path)
    image = Image.new("RGB", (40, 60), "white")
    monkeypatch.setattr(
        scan_layout,
        "_extract_with_pypdf",
        lambda _path: [(image, 595.0, 842.0)],
    )
    caller = threading.get_ident()
    observed_threads: list[int] = []

    def analyze(work_item: tuple[int, Image.Image, float, float]) -> ScanPageLayout:
        number, extracted_image, _pdf_width, _pdf_height = work_item
        observed_threads.append(threading.get_ident())
        return _page(number, extracted_image)

    monkeypatch.setattr(scan_layout, "_analyze_extracted_pdf_page", analyze)

    document = scan_layout.analyze_scan_pdf(path, maximum_workers=8)

    assert [page.number for page in document.pages] == [1]
    assert observed_threads == [caller]
