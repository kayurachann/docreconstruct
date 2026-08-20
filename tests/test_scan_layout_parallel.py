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

from docreconstruct.exceptions import LayoutBudgetExceededError, UnsupportedInputError
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


def _budget_pdf(path: Path, pages: int) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    image = Image.new("RGB", (400, 560), "white")
    draw = ImageDraw.Draw(image)
    for index in range(8):
        top = 40 + index * 60
        draw.rectangle((30, top, 360, top + 18), fill="black")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    document = pymupdf.open()
    try:
        for _ in range(pages):
            page = document.new_page(width=306, height=396)
            page.insert_image(page.rect, stream=stream.getvalue())
        document.save(path)
    finally:
        document.close()


def test_a_layout_pdf_over_budget_is_refused_instead_of_decoded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Page rasters are held in memory, so the document has to be bounded.

    A compressed PDF says nothing about how much it decodes to — a few
    megabytes of scan expands to hundreds of megabytes of RGB — so an unbounded
    upload was an out-of-memory risk with no ceiling of any kind.
    """

    path = tmp_path / "many.pdf"
    _budget_pdf(path, 6)

    assert len(scan_layout.analyze_scan_pdf(path, maximum_workers=1).pages) == 6

    monkeypatch.setenv("DOCRECONSTRUCT_MAX_LAYOUT_PAGES", "3")
    with pytest.raises(LayoutBudgetExceededError, match="3-page limit"):
        scan_layout.analyze_scan_pdf(path, maximum_workers=1)
    monkeypatch.delenv("DOCRECONSTRUCT_MAX_LAYOUT_PAGES")

    monkeypatch.setenv("DOCRECONSTRUCT_MAX_LAYOUT_PIXELS", "100000")
    with pytest.raises(LayoutBudgetExceededError, match="100000 pixels"):
        scan_layout.analyze_scan_pdf(path, maximum_workers=1)


def test_layout_budget_error_is_not_retried_through_the_other_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document over budget must be refused, not re-decoded by the fallback."""

    path = tmp_path / "many.pdf"
    _budget_pdf(path, 6)
    monkeypatch.setenv("DOCRECONSTRUCT_MAX_LAYOUT_PAGES", "3")
    monkeypatch.setattr(
        scan_layout,
        "_extract_with_pymupdf",
        lambda *_args, **_kwargs: pytest.fail("the fallback must not run"),
    )

    with pytest.raises(LayoutBudgetExceededError):
        scan_layout.analyze_scan_pdf(path, maximum_workers=1)


def test_page_workers_are_capped_across_concurrent_documents() -> None:
    """Permits bound the page workers this process runs at once.

    Every request used to build its own pool, so N concurrent documents meant
    N x workers processes with nothing coordinating them.
    """

    total = scan_layout._ABSOLUTE_MAX_PAGE_WORKERS
    with scan_layout._reserved_page_workers(total) as first:
        assert first == total
        with scan_layout._reserved_page_workers(4) as second:
            # Nothing left: the caller falls back to analyzing serially.
            assert second == 0
    # Permits are returned once the pool is gone.
    with scan_layout._reserved_page_workers(total) as again:
        assert again == total


def test_unqualified_pdf_is_rejected_before_any_raster_is_decoded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast path must not decode pages it is about to discard.

    Taking the length of `page.images` only walks the resource dictionary, but
    indexing it decodes the raster. Checking one page at a time meant a document
    that fails on its last page decoded every earlier page, held them all in
    memory, then threw them away for the PyMuPDF fallback to rasterize again.
    """

    decoded: list[int] = []

    class _Images:
        def __init__(self, page_index: int, count: int) -> None:
            self._page_index = page_index
            self._count = count

        def __len__(self) -> int:
            return self._count

        def __getitem__(self, index: int) -> Any:
            decoded.append(self._page_index)
            raise AssertionError("no raster should be decoded for a rejected document")

    class _Box:
        width = 612.0
        height = 792.0

    class _Page:
        def __init__(self, page_index: int, count: int) -> None:
            self.mediabox = _Box()
            self.images = _Images(page_index, count)
            self.rotation = 0

    class _Reader:
        def __init__(self, _path: str) -> None:
            # Nineteen good pages, then one carrying a second XObject.
            self.pages = [_Page(index, 1) for index in range(19)] + [_Page(19, 2)]

    monkeypatch.setattr("pypdf.PdfReader", _Reader)
    path = tmp_path / "mixed.pdf"
    path.write_bytes(b"%PDF-1.7\n")

    with pytest.raises(ValueError, match="unambiguous full-page raster"):
        scan_layout._extract_with_pypdf(path)

    assert decoded == []


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
        def __init__(self, *, max_workers: int, mp_context: object | None = None) -> None:
            # The production call pins a start method that never forks a live
            # interpreter; threads ignore it.
            assert mp_context is not None
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


def _pdf_with_truncated_inline_image(path: Path) -> None:
    """Write a PDF whose content stream ends mid inline image.

    pypdf rejects this; PyMuPDF reads it. That is exactly the split the
    fallback exists for.
    """

    content = b"q 100 0 0 100 0 0 cm\nBI /W 4 /H 4 /BPC 8 /CS /G ID \x00\x01\x02\x03"
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n".encode()
    out += b"%%EOF\n"
    path.write_bytes(bytes(out))


def test_a_pdf_pypdf_rejects_still_reaches_the_pymupdf_fallback(tmp_path: Path) -> None:
    """pypdf reports an unreadable file with its own error type.

    That type was not in the fallback's catch list, so a document the renderer
    parses perfectly well was rejected outright — and the API answered 500,
    because a malformed upload reached the unhandled-exception branch.
    """

    pytest.importorskip("pymupdf")
    path = tmp_path / "truncated-inline-image.pdf"
    _pdf_with_truncated_inline_image(path)

    # The fast path genuinely cannot read it.
    with pytest.raises(ValueError, match="pypdf could not read"):
        scan_layout._extract_with_pypdf(path)

    # The document still reconstructs, through the fallback.
    layout = scan_layout.analyze_scan_pdf(path, maximum_workers=1)
    assert len(layout.pages) == 1
    assert layout.pages[0].width > 0


def test_a_pdf_neither_backend_can_read_is_unsupported_input(tmp_path: Path) -> None:
    """An unreadable upload is a property of the input, not an internal error."""

    path = tmp_path / "garbage.pdf"
    path.write_bytes(b"%PDF-1.7\nthis is not a pdf at all\n%%EOF\n")

    with pytest.raises(UnsupportedInputError, match="could not be read"):
        scan_layout.analyze_scan_pdf(path, maximum_workers=1)
