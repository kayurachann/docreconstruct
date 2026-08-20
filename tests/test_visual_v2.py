from __future__ import annotations

import pytest
from PIL import Image, ImageDraw, ImageFilter

from docreconstruct.evaluation.visual import (
    VISUAL_METRIC_VERSION,
    VisualMetrics,
    evaluate_visual,
    visual_comparison_report,
)


def _page(*, size: tuple[int, int] = (300, 300)) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 120, 80), fill="black")
    draw.rectangle((20, 180, 120, 240), fill="black")
    return image


def test_visual_v2_reports_tolerant_foreground_and_versioned_components() -> None:
    reference = Image.new("RGB", (100, 100), "white")
    candidate = Image.new("RGB", reference.size, "white")
    ImageDraw.Draw(reference).line((20, 50, 80, 50), fill="black", width=2)
    ImageDraw.Draw(candidate).line((22, 52, 82, 52), fill="black", width=2)

    strict = evaluate_visual(reference, candidate, distance_tolerance=0)
    tolerant = evaluate_visual(reference, candidate, distance_tolerance=2)
    report = tolerant.to_dict()

    assert tolerant.metric_version == VISUAL_METRIC_VERSION
    assert tolerant.foreground_f1 == pytest.approx(1.0)
    assert tolerant.edge_similarity == pytest.approx(1.0)
    assert tolerant.score > strict.score
    assert report["components"]["foreground_f1"] == pytest.approx(1.0)
    assert report["content_score"] == pytest.approx(tolerant.content_score)
    assert report["geometry_score"] == pytest.approx(1.0)

    html = visual_comparison_report(reference, candidate)
    assert f"<td>{VISUAL_METRIC_VERSION}</td>" in html
    assert "Foreground F1" in html
    assert "Edge spatial similarity" in html


def test_visual_v2_foreground_precision_and_recall_detect_missing_ink() -> None:
    reference = _page()
    candidate = Image.new("RGB", reference.size, "white")
    ImageDraw.Draw(candidate).rectangle((20, 20, 120, 80), fill="black")

    metrics = evaluate_visual(reference, candidate)

    assert metrics.foreground_precision == pytest.approx(1.0)
    assert metrics.foreground_recall == pytest.approx(0.5)
    assert metrics.foreground_f1 == pytest.approx(2.0 / 3.0)
    assert metrics.region_similarity == pytest.approx(0.5)
    assert metrics.score < 0.7


def test_visual_v2_blank_control_never_outscores_real_preview() -> None:
    reference = _page()
    preview = reference.copy()
    blank = Image.new("RGB", reference.size, "white")

    preview_metrics = evaluate_visual(reference, preview)
    blank_metrics = evaluate_visual(reference, blank)

    assert preview_metrics.score == pytest.approx(1.0)
    assert blank_metrics.foreground_f1 == pytest.approx(0.0)
    assert blank_metrics.edge_similarity == pytest.approx(0.0)
    assert blank_metrics.region_similarity == pytest.approx(0.0)
    assert 0.0 <= blank_metrics.score < 0.02
    assert blank_metrics.score < preview_metrics.score


def test_visual_v2_relocated_ink_is_a_spatial_mismatch() -> None:
    reference = Image.new("RGB", (300, 300), "white")
    relocated = Image.new("RGB", reference.size, "white")
    ImageDraw.Draw(reference).rectangle((20, 20, 120, 100), fill="black")
    ImageDraw.Draw(relocated).rectangle((170, 170, 270, 250), fill="black")

    metrics = evaluate_visual(reference, relocated)

    assert metrics.foreground_f1 == pytest.approx(0.0)
    assert metrics.edge_similarity == pytest.approx(0.0)
    assert metrics.region_similarity == pytest.approx(0.0)
    assert metrics.score < 0.02


def test_visual_v2_missing_page_is_zero_in_page_macro() -> None:
    page = _page()

    metrics = evaluate_visual([page, page], [page])

    assert metrics.pages_compared == 2
    assert metrics.reference_page_count == 2
    assert metrics.candidate_page_count == 1
    assert metrics.page_count_similarity == pytest.approx(0.5)
    assert metrics.page_score_macro == pytest.approx(0.5)
    assert metrics.score == pytest.approx(0.5)


def test_visual_v2_dimension_mismatch_caps_scaled_exact_content() -> None:
    reference = _page()
    scaled = reference.resize((600, 600), Image.Resampling.NEAREST)

    metrics = evaluate_visual(reference, scaled)

    assert metrics.foreground_f1 == pytest.approx(1.0)
    assert metrics.edge_similarity == pytest.approx(1.0)
    assert metrics.dimension_similarity == pytest.approx(0.5)
    assert metrics.dimensions_match is False
    assert metrics.geometry_score == pytest.approx(0.5)
    assert metrics.score == pytest.approx(0.5, abs=0.001)


def test_visual_v2_rejects_invalid_region_grid() -> None:
    image = _page()

    with pytest.raises(ValueError, match="region_grid"):
        evaluate_visual(image, image, region_grid=(0, 4))


def test_visual_metrics_legacy_positional_constructor_keeps_original_score() -> None:
    metrics = VisualMetrics(0.8, 0.6, 0.5, 12, 100)

    assert metrics.score == pytest.approx(0.75 * 0.8 + 0.15 * 0.6 + 0.10 * 0.5)
    assert metrics.to_dict()["pixel_similarity"] == pytest.approx(0.8)


@pytest.mark.parametrize("contrast", [1, 4, 8, 12, 20])
def test_visual_v2_faint_perceptible_ink_is_not_treated_as_blank(contrast: int) -> None:
    reference = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(reference)
    ink = (255 - contrast,) * 3
    for top, length in ((45, 220), (75, 180), (105, 240), (150, 150)):
        draw.rectangle((35, top, 35 + length, top + 2), fill=ink)
    blank = Image.new("RGB", reference.size, "white")

    metrics = evaluate_visual(reference, blank)

    assert metrics.metric_version == VISUAL_METRIC_VERSION
    assert metrics.foreground_detection == "adaptive-local-contrast"
    assert metrics.reference_foreground_pixels > 0
    assert metrics.candidate_foreground_pixels == 0
    assert metrics.foreground_recall == pytest.approx(0.0)
    assert metrics.score < 0.02


def test_visual_v2_low_dpi_render_preserves_content_but_keeps_geometry_penalty() -> None:
    reference = _page(size=(600, 600))
    low_dpi = reference.resize((300, 300), Image.Resampling.LANCZOS)

    metrics = evaluate_visual(reference, low_dpi, distance_tolerance=3)

    assert metrics.content_score > 0.80
    assert metrics.dimension_similarity == pytest.approx(0.5)
    assert 0.40 < metrics.score <= 0.5


def test_visual_v2_mild_blur_beats_blank_negative_control() -> None:
    reference = _page()
    blurred = reference.filter(ImageFilter.GaussianBlur(radius=1.1))
    blank = Image.new("RGB", reference.size, "white")

    blurred_metrics = evaluate_visual(reference, blurred, distance_tolerance=3)
    blank_metrics = evaluate_visual(reference, blank, distance_tolerance=3)

    assert blurred_metrics.score > 0.70
    assert blurred_metrics.score > blank_metrics.score + 0.65


def test_visual_v2_smooth_photo_shadow_does_not_hide_matching_ink() -> None:
    clean = _page()
    photographed = Image.new("RGB", clean.size, "white")
    shadow = ImageDraw.Draw(photographed)
    for x in range(photographed.width):
        shade = 220 + round(35 * x / (photographed.width - 1))
        shadow.line((x, 0, x, photographed.height), fill=(shade, shade, shade))
    photographed.paste((0, 0, 0), (20, 20, 121, 81))
    photographed.paste((0, 0, 0), (20, 180, 121, 241))

    metrics = evaluate_visual(photographed, clean, distance_tolerance=2)

    assert metrics.foreground_f1 == pytest.approx(1.0)
    assert metrics.edge_similarity == pytest.approx(1.0)
    assert metrics.score > 0.98
