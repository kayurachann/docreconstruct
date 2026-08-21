from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from docreconstruct.evaluation import (
    BenchmarkCase,
    BenchmarkRunner,
    DocumentRenderResult,
    FidelityScore,
    evaluate,
    evaluate_editability,
    evaluate_layout,
    evaluate_structure,
    evaluate_text,
    evaluate_visual,
    run_benchmark,
    visual_comparison_report,
    visual_diff,
)
from docreconstruct.ir import BBox, Document, Element, ElementType, Page
from docreconstruct.reconstruction.math_omml import append_omml
from docreconstruct.renderers import JSONRenderer


def _document(
    text: str = "Revenue 12,850",
    *,
    bbox: tuple[float, float, float, float] = (10, 10, 200, 40),
    kind: ElementType = ElementType.PARAGRAPH,
    metadata: dict | None = None,
) -> Document:
    return Document(
        id="document",
        pages=[
            Page(
                id="page-1",
                number=1,
                width=600,
                height=800,
                elements=[
                    Element(
                        id="element-1",
                        type=kind,
                        bbox=BBox.from_sequence(bbox),
                        text=text,
                        reading_order=0,
                        metadata=metadata or {},
                    )
                ],
            )
        ],
    )


def test_text_layout_and_structure_metrics_respond_to_errors() -> None:
    reference = _document()
    exact = _document()
    changed = _document("Revenue 12,650", bbox=(300, 500, 500, 540), kind=ElementType.HEADING)

    exact_text = evaluate_text(reference, exact)
    changed_text = evaluate_text(reference, changed)
    exact_layout = evaluate_layout(reference, exact)
    changed_layout = evaluate_layout(reference, changed)

    assert exact_text.score == pytest.approx(1.0)
    assert changed_text.numerical_accuracy == 0.0
    assert changed_text.score < exact_text.score
    assert exact_layout.score == pytest.approx(1.0)
    assert changed_layout.score < exact_layout.score
    assert evaluate_structure(reference, changed).type_f1 == 0.0


def test_editability_and_weighted_profiles() -> None:
    semantic = _document()
    flattened = _document(
        text="",
        kind=ElementType.IMAGE,
        metadata={"flattened": True, "full_page": True},
    )

    assert evaluate_editability(semantic).score == 1.0
    assert evaluate_editability(flattened).score == 0.0

    balanced = FidelityScore(text=1.0, layout=0.5, visual=0.0, profile="balanced")
    visual = FidelityScore(text=1.0, layout=0.5, visual=0.0, profile="pixel-perfect")
    assert visual.overall < balanced.overall
    assert sum(balanced.weights.values()) == pytest.approx(1.0)


def test_visual_similarity_and_diff(tmp_path: Path) -> None:
    reference = Image.new("RGB", (20, 20), "white")
    exact = reference.copy()
    changed = reference.copy()
    changed.putpixel((10, 10), (0, 0, 0))

    assert evaluate_visual(reference, exact).score == pytest.approx(1.0)
    changed_metrics = evaluate_visual(reference, changed)
    assert 0.0 < changed_metrics.score < 1.0
    assert changed_metrics.differing_pixels == 1

    output = tmp_path / "diff.png"
    difference = visual_diff(reference, changed, output)
    assert difference.size == (20, 20)
    assert output.is_file()

    report_path = tmp_path / "comparison.html"
    report = visual_comparison_report(reference, changed, report_path)
    assert report.count("data:image/png;base64,") == 3
    assert "http://" not in report and "https://" not in report
    assert "Pixel similarity" in report
    assert report_path.read_text(encoding="utf-8") == report


def test_high_level_evaluation_reports_available_components(tmp_path: Path) -> None:
    reference = _document()
    candidate = _document("Revenue 12,650")
    report = evaluate(reference, candidate, profile="data")

    assert report.text is not None
    assert report.layout is not None
    assert report.structure is not None
    assert report.visual is None
    assert report.metadata["measured_components"] == [
        "text",
        "layout",
        "structure",
        "editability",
    ]
    assert 0.0 <= report.score <= 1.0

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (3, 3), "white").save(first)
    Image.new("RGB", (3, 3), "white").save(second)
    visual_only = evaluate(first, second)
    assert visual_only.visual is not None
    assert visual_only.fidelity.visual == 1.0


def test_markdown_to_docx_text_comparison_ignores_rendering_syntax(
    tmp_path: Path,
) -> None:
    docx = pytest.importorskip("docx")
    markdown = tmp_path / "content.md"
    markdown.write_text("# Editable title\n\nName:\n\nExact body text.\n", encoding="utf-8")
    document = docx.Document()
    document.add_paragraph("Editable title")
    document.add_paragraph("Name:\ufeff")
    document.add_paragraph("Exact body text.")
    candidate = tmp_path / "candidate.docx"
    document.save(candidate)

    report = evaluate(markdown, candidate)

    assert report.text is not None
    assert report.text.exact_match == 1.0
    assert report.text.character_accuracy == 1.0
    assert report.metadata["reference_kind"] == "markdown"


def test_docx_comparison_is_native_and_never_starts_a_renderer_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = pytest.importorskip("docx")
    reference = tmp_path / "reference.png"
    Image.new("RGB", (24, 24), "white").save(reference)
    document = docx.Document()
    document.add_paragraph("Editable candidate")
    candidate = tmp_path / "candidate.docx"
    document.save(candidate)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("default evaluation must not discover or start an Office renderer")

    monkeypatch.setattr("docreconstruct.evaluation.evaluator.render_docx_pages", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)

    report = evaluate(reference, candidate)

    assert report.visual is None
    assert report.metadata["render_backend"] == "native"
    assert report.metadata["renderer_provenance"] == []
    assert "visual" not in report.metadata["measured_components"]


def test_docx_comparison_renders_only_after_explicit_backend_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docx = pytest.importorskip("docx")
    reference = tmp_path / "reference.png"
    Image.new("RGB", (24, 24), "white").save(reference)
    document = docx.Document()
    document.add_paragraph("Editable candidate")
    candidate = tmp_path / "candidate.docx"
    document.save(candidate)
    renderer = tmp_path / "soffice.exe"
    renderer.write_bytes(b"renderer")
    calls: list[tuple[Path, str, Path | None]] = []

    def fake_render(
        path: Path,
        *,
        backend: str,
        executable: Path | None = None,
        **kwargs: object,
    ) -> DocumentRenderResult:
        calls.append((path, backend, executable))
        return DocumentRenderResult(
            requested_backend=backend,
            used_backend="libreoffice",
            status="rendered",
            pages=(reference.read_bytes(),),
            executable=str(renderer),
            executable_sha256="a" * 64,
            executable_version="LibreOffice test",
            discovery_source="explicit",
            duration_seconds=0.25,
            return_code=0,
            rendered_pdf_sha256="b" * 64,
            page_sha256=("c" * 64,),
            page_sizes_points=((612.0, 792.0),),
        )

    monkeypatch.setattr("docreconstruct.evaluation.evaluator.render_docx_pages", fake_render)

    report = evaluate(
        reference,
        candidate,
        render_backend="auto",
        renderer_path=renderer,
    )

    assert report.visual is not None
    assert report.visual.score == pytest.approx(1.0)
    assert calls == [(candidate, "auto", renderer)]
    assert report.metadata["renderer_provenance"][0]["executable_sha256"] == "a" * 64
    assert report.metadata["renderer_provenance"][0]["page_sizes_points"] == [[612.0, 792.0]]


def test_evaluation_rejects_renderer_path_with_native_backend(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (3, 3), "white").save(first)
    Image.new("RGB", (3, 3), "white").save(second)

    with pytest.raises(ValueError, match="renderer_path requires"):
        evaluate(first, second, renderer_path=tmp_path / "soffice.exe")


def test_markdown_math_comparison_uses_the_same_editable_omml_projection(
    tmp_path: Path,
) -> None:
    docx = pytest.importorskip("docx")
    markdown = tmp_path / "math.md"
    latex = r"\begin{aligned}&=\lim_{x\to0}\frac{\sin x}{x}\\&=1\end{aligned}"
    markdown.write_text(f"方法二\n\n$$ {latex} $$\n", encoding="utf-8")
    document = docx.Document()
    document.add_paragraph("方法二")
    append_omml(document.add_paragraph(), latex)
    candidate = tmp_path / "math.docx"
    document.save(candidate)

    report = evaluate(markdown, candidate)

    assert report.text is not None
    assert report.text.exact_match == 1.0
    assert report.text.character_accuracy == 1.0


def test_benchmark_manifest_is_stable_and_sorted(tmp_path: Path) -> None:
    reference = _document()
    candidate = _document()
    (tmp_path / "reference.json").write_text(JSONRenderer().render(reference), encoding="utf-8")
    (tmp_path / "candidate.json").write_text(JSONRenderer().render(candidate), encoding="utf-8")
    manifest = {
        "configuration": {"engines": ["fixture"]},
        "model_versions": {"fixture": "1.0"},
        "cases": [
            {"id": "z-case", "reference": "reference.json", "candidate": "candidate.json"},
            {"id": "a-case", "reference": "reference.json", "candidate": "candidate.json"},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    first = run_benchmark(tmp_path)
    second = run_benchmark(tmp_path / "manifest.json")

    assert first.to_json() == second.to_json()
    assert [result.case_id for result in first.results] == ["a-case", "z-case"]
    assert first.mean_score == pytest.approx(1.0)
    assert all(result.duration_seconds is None for result in first.results)

    direct = BenchmarkRunner().run([BenchmarkCase("one", reference, candidate)])
    assert direct.successful_cases == 1


def test_crashed_case_drags_the_mean_instead_of_vanishing(tmp_path: Path) -> None:
    """``mean_score`` dropped failed cases from numerator *and* denominator.

    A run where nine of ten cases crashed reported the surviving case's score
    as the headline, so a broken pipeline could look flawless.
    """

    document = Document(
        id="doc",
        pages=[
            Page(
                id="p1",
                number=1,
                width=100,
                height=100,
                elements=[
                    Element(
                        id="e1",
                        type=ElementType.TEXT,
                        bbox=BBox(x0=0, y0=0, x1=50, y1=10),
                        text="hello",
                    )
                ],
            )
        ],
    )
    reference = tmp_path / "reference.json"
    reference.write_text(document.model_dump_json(), encoding="utf-8")
    missing = tmp_path / "does-not-exist.json"

    report = BenchmarkRunner().run(
        [
            BenchmarkCase("ok", reference, reference),
            BenchmarkCase("broken", reference, missing),
        ]
    )

    assert report.successful_cases == 1
    assert report.failed_cases == 1
    # One perfect case and one crash is a half score, not a perfect one.
    assert report.mean_score == pytest.approx(0.5)
    # A component measured on only half the population is not published.
    assert report.component_means["text"] is None
