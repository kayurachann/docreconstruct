"""Optional project-owned DOCX page rendering backends for visual QA."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentRenderResult:
    requested_backend: str
    used_backend: str | None
    status: str
    pages: tuple[bytes, ...] = ()
    executable: str | None = None
    diagnostic: str | None = None
    executable_sha256: str | None = None
    executable_version: str | None = None
    discovery_source: str | None = None
    duration_seconds: float | None = None
    return_code: int | None = None
    rendered_pdf_sha256: str | None = None
    page_sha256: tuple[str, ...] = ()
    page_sizes_points: tuple[tuple[float, float], ...] = ()

    @property
    def rendered(self) -> bool:
        return self.status == "rendered" and bool(self.pages)

    def provenance(self) -> dict[str, object]:
        """Return JSON-safe renderer provenance without embedding page payloads."""

        return {
            "requested_backend": self.requested_backend,
            "used_backend": self.used_backend,
            "status": self.status,
            "executable": self.executable,
            "executable_sha256": self.executable_sha256,
            "executable_version": self.executable_version,
            "discovery_source": self.discovery_source,
            "duration_seconds": self.duration_seconds,
            "return_code": self.return_code,
            "rendered_pdf_sha256": self.rendered_pdf_sha256,
            "page_sha256": list(self.page_sha256),
            "page_sizes_points": [list(size) for size in self.page_sizes_points],
            "page_count": len(self.pages) if self.rendered else None,
            "diagnostic": self.diagnostic,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _usable_executable(path: Path) -> bool:
    if not path.is_file():
        return False
    # Windows determines executability from the file association/suffix and
    # os.access(X_OK) is not a reliable discriminator there.  The subsequent
    # product/version probe is the authoritative identity check on all hosts.
    return os.name == "nt" or os.access(path, os.X_OK)


def _libreoffice_candidates(explicit: str | Path | None = None) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())
    configured = os.environ.get("DOCRECONSTRUCT_LIBREOFFICE")
    if configured:
        candidates.append(Path(configured).expanduser())
    for name in ("soffice", "libreoffice"):
        discovered = shutil.which(name)
        if discovered:
            candidates.append(Path(discovered))
    candidates.extend(
        [
            Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
            Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
            Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
            Path("/usr/bin/libreoffice"),
            Path("/usr/bin/soffice"),
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _discovery_source(path: Path, explicit: str | Path | None) -> str:
    if explicit is not None:
        return "explicit"
    configured = os.environ.get("DOCRECONSTRUCT_LIBREOFFICE")
    if configured:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file() and configured_path.resolve() == path:
            return "environment"
    for name in ("soffice", "libreoffice"):
        discovered = shutil.which(name)
        if discovered and Path(discovered).resolve() == path:
            return f"path:{name}"
    return "well-known"


def find_libreoffice(explicit: str | Path | None = None) -> Path | None:
    """Find a declared/system LibreOffice binary without changing project state."""

    if explicit is not None:
        candidate = Path(explicit).expanduser()
        return candidate.resolve() if _usable_executable(candidate) else None
    return next(
        (
            candidate.resolve()
            for candidate in _libreoffice_candidates(explicit)
            if _usable_executable(candidate)
        ),
        None,
    )


def _pixmap_image(image_module: Any, pixmap: Any) -> Any:
    """Wrap a PyMuPDF pixmap as a Pillow image without a PNG round-trip."""

    if pixmap.n == 3 and not pixmap.alpha and pixmap.stride == pixmap.width * 3:
        return image_module.frombytes(
            "RGB",
            (pixmap.width, pixmap.height),
            pixmap.samples,
        )
    import io

    return image_module.open(io.BytesIO(pixmap.tobytes("png")))


def _pdf_pages(
    path: Path,
    target_sizes: list[tuple[int, int]] | None,
) -> tuple[tuple[bytes, ...], tuple[tuple[float, float], ...]]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Rendered visual QA requires PyMuPDF (`docreconstruct[pdf]`).") from exc
    from PIL import Image

    pages: list[bytes] = []
    page_sizes_points: list[tuple[float, float]] = []
    with pymupdf.open(path) as document:
        for index, page in enumerate(document):
            page_sizes_points.append((float(page.rect.width), float(page.rect.height)))
            target = (
                target_sizes[index]
                if target_sizes is not None and index < len(target_sizes)
                else None
            )
            if target is None:
                matrix = pymupdf.Matrix(2.0, 2.0)
            else:
                matrix = pymupdf.Matrix(
                    target[0] / max(1.0, float(page.rect.width)),
                    target[1] / max(1.0, float(page.rect.height)),
                )
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            if target is not None and (pixmap.width, pixmap.height) != target:
                # Resample straight from the pixmap buffer.  Encoding the
                # full-size page to PNG only to decode it again before resizing
                # doubled the codec work on every rendered page and threw the
                # result away.
                import io

                with _pixmap_image(Image, pixmap) as opened:
                    resized = opened.resize(target, Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    resized.save(output, format="PNG")
                    payload = output.getvalue()
            else:
                payload = pixmap.tobytes("png")
            pages.append(payload)
    return tuple(pages), tuple(page_sizes_points)


def _process_output(completed: subprocess.CompletedProcess[str]) -> str | None:
    diagnostic = "\n".join(
        value.strip() for value in (completed.stdout, completed.stderr) if value and value.strip()
    )
    return diagnostic or None


def _version_probe_executable(executable: Path) -> Path:
    """Use LibreOffice's console launcher for observable version output on Windows."""

    if os.name == "nt" and executable.name.casefold() == "soffice.exe":
        console_launcher = executable.with_suffix(".com")
        if _usable_executable(console_launcher):
            return console_launcher
    return executable


def render_docx_pages(
    path: str | Path,
    *,
    backend: str = "native",
    executable: str | Path | None = None,
    target_sizes: list[tuple[int, int]] | None = None,
    timeout: int = 120,
) -> DocumentRenderResult:
    """Render DOCX pages through an explicitly selected project backend.

    ``native`` performs no process discovery or rendering. ``auto`` falls back
    cleanly when LibreOffice is unavailable. ``libreoffice`` is strict and
    reports an unavailable/error status for QA gates to reject.
    """

    requested = backend.strip().casefold()
    if requested not in {"native", "auto", "libreoffice"}:
        raise ValueError("render backend must be native, auto, or libreoffice")
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if requested == "native":
        if executable is not None:
            raise ValueError("an explicit renderer executable requires auto or libreoffice backend")
        return DocumentRenderResult(requested, None, "disabled")
    office = find_libreoffice(executable)
    if office is None:
        declared = str(Path(executable).expanduser().resolve()) if executable is not None else None
        return DocumentRenderResult(
            requested,
            None,
            ("fallback-native" if requested == "auto" and executable is None else "unavailable"),
            executable=declared,
            discovery_source="explicit" if executable is not None else None,
            diagnostic=(
                "The explicitly selected LibreOffice executable is not an executable file."
                if executable is not None
                else "LibreOffice executable was not found."
            ),
        )
    discovery_source = _discovery_source(office, executable)
    started = time.perf_counter()
    try:
        executable_sha256 = _sha256(office)
    except OSError as exc:
        return DocumentRenderResult(
            requested,
            "libreoffice",
            "error",
            executable=str(office),
            discovery_source=discovery_source,
            duration_seconds=time.perf_counter() - started,
            diagnostic=f"Could not fingerprint LibreOffice executable: {exc}",
        )

    timeout_seconds = max(10, int(timeout))
    try:
        version_result = subprocess.run(
            [str(_version_probe_executable(office)), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return DocumentRenderResult(
            requested,
            "libreoffice",
            "error",
            executable=str(office),
            executable_sha256=executable_sha256,
            discovery_source=discovery_source,
            duration_seconds=time.perf_counter() - started,
            diagnostic=f"LibreOffice identity probe failed: {exc}",
        )
    version_output = _process_output(version_result)
    if (
        version_result.returncode != 0
        or not version_output
        or "libreoffice" not in version_output.casefold()
    ):
        return DocumentRenderResult(
            requested,
            "libreoffice",
            "error",
            executable=str(office),
            executable_sha256=executable_sha256,
            discovery_source=discovery_source,
            duration_seconds=time.perf_counter() - started,
            return_code=version_result.returncode,
            diagnostic=(
                "Selected executable did not identify itself as LibreOffice."
                + (f" Probe output: {version_output}" if version_output else "")
            ),
        )
    executable_version = version_output.splitlines()[0].strip()
    with tempfile.TemporaryDirectory(prefix="docreconstruct-render-") as directory:
        workspace = Path(directory)
        output_directory = workspace / "output"
        profile_directory = workspace / "profile"
        output_directory.mkdir()
        profile_directory.mkdir()
        command = [
            str(office),
            "--headless",
            "--norestore",
            "--nodefault",
            "--nolockcheck",
            f"-env:UserInstallation={profile_directory.resolve().as_uri()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_directory),
            str(source),
        ]
        completed: subprocess.CompletedProcess[str] | None = None
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice conversion failed with exit code {completed.returncode}: "
                    f"{_process_output(completed) or 'no diagnostic output'}"
                )
            pdf = output_directory / f"{source.stem}.pdf"
            if not pdf.is_file():
                raise RuntimeError("LibreOffice completed without producing a PDF")
            rendered_pdf_sha256 = _sha256(pdf)
            pages, page_sizes_points = _pdf_pages(pdf, target_sizes)
            if not pages:
                raise RuntimeError("rendered PDF contains no pages")
        except (OSError, RuntimeError, subprocess.SubprocessError, ImportError) as exc:
            return DocumentRenderResult(
                requested,
                "libreoffice",
                "error",
                executable=str(office),
                executable_sha256=executable_sha256,
                executable_version=executable_version,
                discovery_source=discovery_source,
                duration_seconds=time.perf_counter() - started,
                return_code=completed.returncode if completed is not None else None,
                diagnostic=str(exc),
            )
    assert completed is not None  # successful conversion path
    diagnostic = _process_output(completed)
    return DocumentRenderResult(
        requested,
        "libreoffice",
        "rendered",
        pages=pages,
        executable=str(office),
        diagnostic=diagnostic,
        executable_sha256=executable_sha256,
        executable_version=executable_version,
        discovery_source=discovery_source,
        duration_seconds=time.perf_counter() - started,
        return_code=completed.returncode,
        rendered_pdf_sha256=rendered_pdf_sha256,
        page_sha256=tuple(hashlib.sha256(page).hexdigest() for page in pages),
        page_sizes_points=page_sizes_points,
    )


__all__ = ["DocumentRenderResult", "find_libreoffice", "render_docx_pages"]
