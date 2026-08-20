from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from docreconstruct.providers import ProviderContext, ProviderDependencyError
from docreconstruct.providers.tesseract_local import TesseractLocalProvider

_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
    "left\ttop\twidth\theight\tconf\ttext\n"
    """
1\t1\t0\t0\t0\t0\t0\t0\t100\t50\t-1\t
5\t1\t1\t1\t1\t1\t10\t5\t20\t10\t90\tHello
5\t1\t1\t1\t1\t2\t35\t5\t25\t10\t80\tworld
5\t1\t1\t1\t2\t1\t10\t25\t30\t10\t70\tSecond
"""
)


def test_tesseract_provider_runs_argv_without_shell_and_preserves_line_geometry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (100, 50), "white").save(source)
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"test")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        Path(command[2]).with_suffix(".tsv").write_text(_TSV, encoding="utf-8")
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 7.0
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("docreconstruct.providers.tesseract_local.subprocess.run", fake_run)
    result = TesseractLocalProvider().parse(
        source,
        context=ProviderContext(
            source=str(source),
            options={"executable": str(executable), "timeout_seconds": 7},
            metadata={"languages": ["en"]},
        ),
    )

    assert len(commands) == 1
    assert commands[0][0] == str(executable.resolve())
    assert commands[0][-1] == "tsv"
    page = result.document.pages[0]
    assert (page.width, page.height) == (100, 50)
    assert [element.text for element in page.elements] == ["Hello world", "Second"]
    assert page.elements[0].bbox.model_dump() == {"x0": 10.0, "y0": 5.0, "x1": 60.0, "y1": 15.0}
    assert page.elements[0].confidence == pytest.approx((90 * 5 + 80 * 5) / 10 / 100)
    assert page.elements[1].reading_order == 1


def test_tesseract_provider_rejects_missing_traineddata_before_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (10, 10), "white").save(source)
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"test")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").write_bytes(b"test")
    monkeypatch.setattr(
        "docreconstruct.providers.tesseract_local.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("process must not start"),
    )

    with pytest.raises(ProviderDependencyError, match="chi_sim"):
        TesseractLocalProvider().parse(
            source,
            context=ProviderContext(
                source=str(source),
                options={"executable": str(executable), "tessdata_dir": str(tessdata)},
                metadata={"languages": ["eng", "chi_sim"]},
            ),
        )


def test_tesseract_provider_rejects_language_injection(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (10, 10), "white").save(source)
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"test")

    with pytest.raises(ValueError, match="invalid Tesseract language"):
        TesseractLocalProvider().parse(
            source,
            context=ProviderContext(
                source=str(source),
                options={"executable": str(executable), "language": "eng;whoami"},
            ),
        )
