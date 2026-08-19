from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from docreconstruct.evaluation.document_rendering import (
    _version_probe_executable,
    render_docx_pages,
)


def test_windows_version_probe_uses_libreoffice_console_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = tmp_path / "soffice.exe"
    console_launcher = tmp_path / "soffice.com"
    renderer.write_bytes(b"gui-launcher")
    console_launcher.write_bytes(b"console-launcher")
    monkeypatch.setattr("docreconstruct.evaluation.document_rendering.os.name", "nt")

    assert _version_probe_executable(renderer) == console_launcher


def test_native_render_backend_never_discovers_or_starts_office(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"project-owned-placeholder")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("native QA must not discover or start an office process")

    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering.find_libreoffice",
        forbidden,
    )
    monkeypatch.setattr("subprocess.run", forbidden)

    result = render_docx_pages(candidate, backend="native")

    assert result.status == "disabled"
    assert not result.rendered
    assert result.pages == ()


def test_strict_libreoffice_backend_reports_missing_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"project-owned-placeholder")
    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering.find_libreoffice",
        lambda explicit=None: None,
    )

    result = render_docx_pages(candidate, backend="libreoffice")

    assert result.status == "unavailable"
    assert result.used_backend is None
    assert not result.rendered


def test_explicit_missing_renderer_never_falls_back_or_starts_a_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"project-owned-placeholder")
    missing = tmp_path / "missing-soffice.exe"

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("an invalid explicit renderer must never fall back or execute")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering._libreoffice_candidates",
        forbidden,
    )

    result = render_docx_pages(candidate, backend="auto", executable=missing)

    assert result.status == "unavailable"
    assert result.discovery_source == "explicit"
    assert result.executable == str(missing.resolve())
    assert not result.rendered


def test_native_backend_rejects_unused_explicit_renderer_without_probing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"project-owned-placeholder")
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"not-run")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("native rendering must never probe a renderer")

    monkeypatch.setattr("subprocess.run", forbidden)

    with pytest.raises(ValueError, match="requires auto or libreoffice"):
        render_docx_pages(candidate, backend="native", executable=renderer)


def test_renderer_identity_probe_rejects_wrong_product_before_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"project-owned-placeholder")
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"wrong-product")
    renderer.chmod(0o755)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="Some Other Office 1.0\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = render_docx_pages(candidate, backend="libreoffice", executable=renderer)

    assert result.status == "error"
    assert result.return_code == 0
    assert result.discovery_source == "explicit"
    assert result.executable_sha256 == hashlib.sha256(b"wrong-product").hexdigest()
    assert "did not identify itself" in (result.diagnostic or "")
    assert calls == [[str(renderer.resolve()), "--version"]]


def test_successful_render_records_reproducible_renderer_and_page_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.docx"
    candidate.write_bytes(b"project-owned-placeholder")
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"trusted-libreoffice-binary")
    renderer.chmod(0o755)
    rendered_pdf = b"%PDF-project-test"
    rendered_page = b"project-page-png"
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="LibreOffice 24.2.7.2 420(Build:2)\n",
                stderr="",
            )
        output_directory = Path(command[command.index("--outdir") + 1])
        (output_directory / "candidate.pdf").write_bytes(rendered_pdf)
        return subprocess.CompletedProcess(command, 0, stdout="convert complete\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(
        "docreconstruct.evaluation.document_rendering._pdf_pages",
        lambda path, target_sizes: ((rendered_page,), ((612.0, 792.0),)),
    )

    result = render_docx_pages(
        candidate,
        backend="libreoffice",
        executable=renderer,
        target_sizes=[(1224, 1584)],
    )

    assert result.rendered
    assert result.executable == str(renderer.resolve())
    assert result.executable_sha256 == hashlib.sha256(renderer.read_bytes()).hexdigest()
    assert result.executable_version == "LibreOffice 24.2.7.2 420(Build:2)"
    assert result.discovery_source == "explicit"
    assert result.return_code == 0
    assert result.duration_seconds is not None and result.duration_seconds >= 0.0
    assert result.rendered_pdf_sha256 == hashlib.sha256(rendered_pdf).hexdigest()
    assert result.page_sha256 == (hashlib.sha256(rendered_page).hexdigest(),)
    assert result.page_sizes_points == ((612.0, 792.0),)
    provenance = result.provenance()
    assert provenance["page_count"] == 1
    assert provenance["page_sha256"] == list(result.page_sha256)
    assert len(calls) == 2
    conversion = calls[1]
    assert conversion[0] == str(renderer.resolve())
    assert "--headless" in conversion
    assert any(item.startswith("-env:UserInstallation=file:") for item in conversion)
