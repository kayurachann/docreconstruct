"""Pillow-based raster similarity and human-readable visual diffs."""

from __future__ import annotations

import base64
import dataclasses
import html
import io
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VISUAL_METRIC_VERSION = "2.1"
DEFAULT_RENDERED_VISUAL_MIN_SCORE = 0.05


class VisualDependencyError(ImportError):
    pass


def _pillow() -> dict[str, Any]:
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
    except (ImportError, ModuleNotFoundError) as exc:
        raise VisualDependencyError(
            "Visual evaluation requires Pillow. Install `docreconstruct` with its "
            "core dependencies or run `pip install Pillow`."
        ) from exc
    return {
        "Image": Image,
        "ImageChops": ImageChops,
        "ImageFilter": ImageFilter,
        "ImageOps": ImageOps,
        "ImageStat": ImageStat,
    }


def _load_image(source: Any, api: dict[str, Any]) -> Any:
    Image = api["Image"]
    if isinstance(source, Image.Image):
        return source.copy()
    if isinstance(source, (bytes, bytearray, memoryview)):
        with Image.open(io.BytesIO(bytes(source))) as opened:
            return opened.copy()
    if isinstance(source, (str, Path)):
        with Image.open(source) as opened:
            return opened.copy()
    if hasattr(source, "read"):
        with Image.open(source) as opened:
            return opened.copy()
    raise TypeError(f"unsupported image source: {type(source).__name__}")


def _opaque_rgb(image: Any, api: dict[str, Any]) -> Any:
    Image = api["Image"]
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return image.convert("RGB")


def _common_canvas(reference: Any, candidate: Any, api: dict[str, Any]) -> tuple[Any, Any]:
    Image = api["Image"]
    reference = _opaque_rgb(reference, api)
    candidate = _opaque_rgb(candidate, api)
    if candidate.size != reference.size:
        # Page rasters produced at different DPI describe the same physical
        # canvas.  Normalize resolution for pixel scoring; the separate
        # dimension metric still records the original-size mismatch.
        candidate = candidate.resize(reference.size, Image.Resampling.LANCZOS)
    width = max(reference.width, candidate.width)
    height = max(reference.height, candidate.height)
    ref_canvas = Image.new("RGB", (width, height), "white")
    cand_canvas = Image.new("RGB", (width, height), "white")
    ref_canvas.paste(reference, (0, 0))
    cand_canvas.paste(candidate, (0, 0))
    return ref_canvas, cand_canvas


def _document_foreground(
    image: Any,
    api: dict[str, Any],
    *,
    threshold: int = 16,
    adaptive: bool = True,
) -> Any:
    """Normalize photographed page illumination to a black-on-white ink map."""

    mask = _foreground_mask(image, api, threshold=threshold, adaptive=adaptive)
    return api["ImageOps"].invert(mask).convert("RGB")


def _foreground_mask(
    image: Any,
    api: dict[str, Any],
    *,
    threshold: int = 16,
    adaptive: bool = True,
) -> Any:
    """Return a binary local-contrast ink mask (white ink on black background).

    Comparing document foreground rather than all page pixels prevents a blank
    white candidate from receiving a high score merely because page background
    dominates the raster.  Local background subtraction also makes the mask
    useful for photographs with shadows or off-white paper.
    """

    grayscale = api["ImageOps"].grayscale(_opaque_rgb(image, api))
    radius = max(5.0, min(grayscale.size) / 45.0)
    background = grayscale.filter(api["ImageFilter"].GaussianBlur(radius=radius))
    contrast = api["ImageChops"].subtract(background, grayscale)
    cutoff = max(1, min(254, int(threshold)))
    mask = contrast.point(lambda value: 255 if value >= cutoff else 0, mode="L")

    # Thin antialiased glyphs can have less than 16 levels of contrast after a
    # low-DPI render or lossy scan.  Treating those pages as genuinely empty is
    # dangerous: a blank candidate would then receive perfect foreground,
    # edge, and region scores.  When the strong mask is empty, derive a second
    # threshold from the page's *observed* local contrast.  The minimum support
    # rejects isolated codec dust while preserving one-pixel rules and glyphs.
    # This fallback is deliberately inactive as soon as strong ink exists, so
    # normal photographed pages do not suddenly score their paper texture.
    if adaptive and _foreground_count(mask) == 0:
        contrast_maximum = int(contrast.getextrema()[1])
        contrast_histogram = contrast.histogram()
        nonzero_contrast = sum(contrast_histogram[1:])
        minimum_support = max(4, grayscale.width * grayscale.height // 250_000)
        if contrast_maximum > 0 and nonzero_contrast >= minimum_support:
            adaptive_cutoff = max(1, min(cutoff, (contrast_maximum + 2) // 3))
            adaptive_mask = contrast.point(
                lambda value: 255 if value >= adaptive_cutoff else 0,
                mode="L",
            )
            if _foreground_count(adaptive_mask) >= minimum_support:
                mask = adaptive_mask

    # A uniformly dark synthetic region has no local contrast.  This fallback
    # is intentionally narrow so ordinary photographed paper is not classified
    # as foreground.
    extrema = grayscale.getextrema()
    if extrema[1] - extrema[0] < cutoff and extrema[1] < 224:
        return api["Image"].new("L", grayscale.size, 255)
    return mask


def _ratio(left: int, right: int) -> float:
    low, high = sorted((max(0, left), max(0, right)))
    return 1.0 if high == 0 else low / high


@dataclass(frozen=True)
class VisualMetrics:
    pixel_similarity: float
    rms_similarity: float
    dimension_similarity: float
    differing_pixels: int
    total_pixels: int
    pages_compared: int = 1
    foreground_precision: float | None = None
    foreground_recall: float | None = None
    foreground_f1: float | None = None
    edge_similarity: float | None = None
    region_similarity: float | None = None
    page_count_similarity: float = 1.0
    dimensions_match: bool = True
    reference_page_count: int = 1
    candidate_page_count: int = 1
    reference_foreground_pixels: int = 0
    candidate_foreground_pixels: int = 0
    regions_compared: int = 0
    distance_tolerance: int = 0
    metric_version: str = VISUAL_METRIC_VERSION
    page_score_macro: float | None = None
    foreground_threshold: int = 16
    foreground_detection: str = "adaptive-local-contrast"

    @property
    def content_score(self) -> float:
        """Foreground-aware content score before geometry penalties."""

        if (
            self.foreground_f1 is None
            or self.edge_similarity is None
            or self.region_similarity is None
        ):
            # Compatibility for callers that still construct VisualMetrics
            # with the original five positional fields.
            return max(
                0.0,
                min(1.0, 0.75 * self.pixel_similarity + 0.15 * self.rms_similarity + 0.10),
            )
        return max(
            0.0,
            min(
                1.0,
                0.49 * self.foreground_f1
                + 0.30 * self.edge_similarity
                + 0.20 * self.region_similarity
                + 0.01 * self.pixel_similarity,
            ),
        )

    @property
    def geometry_score(self) -> float:
        """Explicit page-count and raster-dimension agreement."""

        return max(
            0.0,
            min(1.0, self.dimension_similarity * self.page_count_similarity),
        )

    @property
    def score(self) -> float:
        if self.page_score_macro is not None:
            return max(0.0, min(1.0, self.page_score_macro))
        if self.foreground_f1 is not None:
            return max(0.0, min(1.0, self.content_score * self.dimension_similarity))
        # Original scoring remains available for manually-created legacy
        # VisualMetrics objects.
        return max(
            0.0,
            min(
                1.0,
                0.75 * self.pixel_similarity
                + 0.15 * self.rms_similarity
                + 0.10 * self.dimension_similarity,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **dataclasses.asdict(self),
            "score": self.score,
            "content_score": self.content_score,
            "geometry_score": self.geometry_score,
            "components": {
                "foreground_precision": self.foreground_precision,
                "foreground_recall": self.foreground_recall,
                "foreground_f1": self.foreground_f1,
                "edge_similarity": self.edge_similarity,
                "region_similarity": self.region_similarity,
                "dimension_similarity": self.dimension_similarity,
                "page_count_similarity": self.page_count_similarity,
            },
        }


def _foreground_count(mask: Any) -> int:
    return int(mask.histogram()[255])


def _safe_radius(mask: Any, radius: int) -> int:
    return max(0, min(int(radius), (min(mask.size) - 1) // 2))


def _dilate(mask: Any, radius: int, api: dict[str, Any]) -> Any:
    radius = _safe_radius(mask, radius)
    if radius == 0:
        return mask.copy()
    return mask.filter(api["ImageFilter"].MaxFilter(2 * radius + 1))


def _binary_prf(
    reference_mask: Any,
    candidate_mask: Any,
    *,
    tolerance: int,
    api: dict[str, Any],
) -> tuple[float, float, float, Any, Any, int, int]:
    """Return tolerance-aware foreground precision, recall, and F1."""

    reference_count = _foreground_count(reference_mask)
    candidate_count = _foreground_count(candidate_mask)
    if reference_count == 0 and candidate_count == 0:
        empty = api["Image"].new("L", reference_mask.size, 0)
        return 1.0, 1.0, 1.0, empty, empty.copy(), 0, 0
    if reference_count == 0 or candidate_count == 0:
        empty = api["Image"].new("L", reference_mask.size, 0)
        return 0.0, 0.0, 0.0, empty, empty.copy(), reference_count, candidate_count

    reference_near = _dilate(reference_mask, tolerance, api)
    candidate_near = _dilate(candidate_mask, tolerance, api)
    candidate_matches = api["ImageChops"].multiply(candidate_mask, reference_near)
    reference_matches = api["ImageChops"].multiply(reference_mask, candidate_near)
    precision = _foreground_count(candidate_matches) / candidate_count
    recall = _foreground_count(reference_matches) / reference_count
    f1 = 0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
    return (
        precision,
        recall,
        f1,
        reference_matches,
        candidate_matches,
        reference_count,
        candidate_count,
    )


def _edge_mask(mask: Any, api: dict[str, Any]) -> Any:
    if min(mask.size) < 3:
        return mask.copy()
    eroded = mask.filter(api["ImageFilter"].MinFilter(3))
    return api["ImageChops"].subtract(mask, eroded)


def _edge_spatial_similarity(
    reference_mask: Any,
    candidate_mask: Any,
    *,
    tolerance: int,
    api: dict[str, Any],
) -> float:
    """Approximate symmetric chamfer similarity at several distance bands."""

    max_dimension = max(reference_mask.size)
    scale = min(1.0, 512.0 / max_dimension)
    if scale < 1.0:
        resized = (
            max(1, round(reference_mask.width * scale)),
            max(1, round(reference_mask.height * scale)),
        )
        resampling = api["Image"].Resampling.NEAREST
        reference_mask = reference_mask.resize(resized, resampling)
        candidate_mask = candidate_mask.resize(resized, resampling)

    reference_edges = _edge_mask(reference_mask, api)
    candidate_edges = _edge_mask(candidate_mask, api)
    reference_count = _foreground_count(reference_edges)
    candidate_count = _foreground_count(candidate_edges)
    if reference_count == 0 and candidate_count == 0:
        return 1.0
    if reference_count == 0 or candidate_count == 0:
        return 0.0

    base = max(1, round(max(1, int(tolerance)) * scale))
    maximum_radius = _safe_radius(reference_edges, 8 * base)
    radii = tuple(
        dict.fromkeys(
            min(maximum_radius, radius) for radius in (base, 2 * base, 4 * base, 8 * base)
        )
    )
    weights = (0.50, 0.25, 0.15, 0.10)
    weighted = 0.0
    weight_total = 0.0
    reference_near = reference_edges.copy()
    candidate_near = candidate_edges.copy()
    radius_weights = dict(zip(radii, weights, strict=False))
    for radius in range(1, maximum_radius + 1):
        reference_near = reference_near.filter(api["ImageFilter"].MaxFilter(3))
        candidate_near = candidate_near.filter(api["ImageFilter"].MaxFilter(3))
        weight = radius_weights.get(radius)
        if weight is None:
            continue
        candidate_matches = api["ImageChops"].multiply(candidate_edges, reference_near)
        reference_matches = api["ImageChops"].multiply(reference_edges, candidate_near)
        precision = _foreground_count(candidate_matches) / candidate_count
        recall = _foreground_count(reference_matches) / reference_count
        f1 = 0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
        weighted += weight * f1
        weight_total += weight
    return 1.0 if weight_total == 0 else weighted / weight_total


def _region_macro_f1(
    reference_mask: Any,
    candidate_mask: Any,
    reference_matches: Any,
    candidate_matches: Any,
    *,
    grid: tuple[int, int],
) -> tuple[float, int]:
    """Macro-average foreground F1 over non-empty page regions."""

    rows, columns = grid
    width, height = reference_mask.size
    scores: list[float] = []
    for row in range(rows):
        top = height * row // rows
        bottom = height * (row + 1) // rows
        for column in range(columns):
            left = width * column // columns
            right = width * (column + 1) // columns
            box = (left, top, right, bottom)
            reference_count = _foreground_count(reference_mask.crop(box))
            candidate_count = _foreground_count(candidate_mask.crop(box))
            if reference_count == 0 and candidate_count == 0:
                continue
            if reference_count == 0 or candidate_count == 0:
                scores.append(0.0)
                continue
            precision = _foreground_count(candidate_matches.crop(box)) / candidate_count
            recall = _foreground_count(reference_matches.crop(box)) / reference_count
            scores.append(
                0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
            )
    if not scores:
        return 1.0, 0
    return sum(scores) / len(scores), len(scores)


def _single_visual_metrics(
    reference: Any,
    candidate: Any,
    *,
    threshold: int = 0,
    normalize_illumination: bool = False,
    distance_tolerance: int = 2,
    foreground_threshold: int = 16,
    adaptive_foreground: bool = True,
    region_grid: tuple[int, int] = (4, 4),
) -> VisualMetrics:
    api = _pillow()
    ref_image = _opaque_rgb(_load_image(reference, api), api)
    cand_image = _opaque_rgb(_load_image(candidate, api), api)
    dimension_similarity = (
        _ratio(ref_image.width, cand_image.width) + _ratio(ref_image.height, cand_image.height)
    ) / 2
    dimensions_match = ref_image.size == cand_image.size
    ref_canvas, cand_canvas = _common_canvas(ref_image, cand_image, api)

    tolerance = max(0, int(distance_tolerance))
    foreground_ref = _foreground_mask(
        ref_canvas,
        api,
        threshold=foreground_threshold,
        adaptive=adaptive_foreground,
    )
    foreground_cand = _foreground_mask(
        cand_canvas,
        api,
        threshold=foreground_threshold,
        adaptive=adaptive_foreground,
    )
    (
        foreground_precision,
        foreground_recall,
        foreground_f1,
        reference_matches,
        candidate_matches,
        reference_foreground_pixels,
        candidate_foreground_pixels,
    ) = _binary_prf(
        foreground_ref,
        foreground_cand,
        tolerance=tolerance,
        api=api,
    )
    edge_similarity = _edge_spatial_similarity(
        foreground_ref,
        foreground_cand,
        tolerance=tolerance,
        api=api,
    )
    region_similarity, regions_compared = _region_macro_f1(
        foreground_ref,
        foreground_cand,
        reference_matches,
        candidate_matches,
        grid=region_grid,
    )

    pixel_ref = ref_canvas
    pixel_cand = cand_canvas
    if normalize_illumination:
        pixel_ref = _document_foreground(
            ref_canvas,
            api,
            threshold=foreground_threshold,
            adaptive=adaptive_foreground,
        )
        pixel_cand = _document_foreground(
            cand_canvas,
            api,
            threshold=foreground_threshold,
            adaptive=adaptive_foreground,
        )
    difference = api["ImageChops"].difference(pixel_ref, pixel_cand)
    statistics = api["ImageStat"].Stat(difference)
    pixel_similarity = 1.0 - sum(statistics.mean) / (len(statistics.mean) * 255.0)
    rms_similarity = 1.0 - sum(statistics.rms) / (len(statistics.rms) * 255.0)
    grayscale = api["ImageOps"].grayscale(difference)
    histogram = grayscale.histogram()
    threshold = max(0, min(254, int(threshold)))
    differing_pixels = sum(histogram[threshold + 1 :])
    metrics = VisualMetrics(
        pixel_similarity=max(0.0, min(1.0, pixel_similarity)),
        rms_similarity=max(0.0, min(1.0, rms_similarity)),
        dimension_similarity=dimension_similarity,
        differing_pixels=differing_pixels,
        total_pixels=ref_canvas.width * ref_canvas.height,
        foreground_precision=foreground_precision,
        foreground_recall=foreground_recall,
        foreground_f1=foreground_f1,
        edge_similarity=edge_similarity,
        region_similarity=region_similarity,
        dimensions_match=dimensions_match,
        reference_foreground_pixels=reference_foreground_pixels,
        candidate_foreground_pixels=candidate_foreground_pixels,
        regions_compared=regions_compared,
        distance_tolerance=tolerance,
        metric_version=VISUAL_METRIC_VERSION,
        foreground_threshold=max(1, min(254, int(foreground_threshold))),
        foreground_detection=(
            "adaptive-local-contrast" if adaptive_foreground else "fixed-local-contrast"
        ),
    )
    return dataclasses.replace(metrics, page_score_macro=metrics.score)


def _is_image_sequence(source: Any) -> bool:
    return isinstance(source, Sequence) and not isinstance(
        source, (str, bytes, bytearray, memoryview, Path)
    )


def evaluate_visual(
    reference: Any,
    candidate: Any,
    *,
    threshold: int = 0,
    normalize_illumination: bool = False,
    distance_tolerance: int = 2,
    foreground_threshold: int = 16,
    adaptive_foreground: bool = True,
    region_grid: tuple[int, int] = (4, 4),
) -> VisualMetrics:
    """Compare one image or two ordered page-image sequences.

    Version 2 scores document foreground with a small spatial tolerance and
    macro-averages active regions/pages.  The adaptive local-contrast fallback
    keeps perceptible low-contrast ink from being mistaken for an empty page;
    pass ``adaptive_foreground=False`` for the original fixed-cutoff behavior.
    Legacy pixel/RMS values remain in the report for API compatibility but do
    not allow white background to dominate the overall score.
    """

    if len(region_grid) != 2:
        raise ValueError("region_grid must contain two positive integers")
    normalized_grid = (int(region_grid[0]), int(region_grid[1]))
    if normalized_grid[0] <= 0 or normalized_grid[1] <= 0:
        raise ValueError("region_grid must contain two positive integers")

    if _is_image_sequence(reference) or _is_image_sequence(candidate):
        references = list(reference) if _is_image_sequence(reference) else [reference]
        candidates = list(candidate) if _is_image_sequence(candidate) else [candidate]
        page_count = max(len(references), len(candidates))
        if page_count == 0:
            return VisualMetrics(
                1.0,
                1.0,
                1.0,
                0,
                0,
                pages_compared=0,
                foreground_precision=1.0,
                foreground_recall=1.0,
                foreground_f1=1.0,
                edge_similarity=1.0,
                region_similarity=1.0,
                reference_page_count=0,
                candidate_page_count=0,
                regions_compared=0,
                distance_tolerance=max(0, int(distance_tolerance)),
                metric_version=VISUAL_METRIC_VERSION,
                page_score_macro=1.0,
                foreground_threshold=max(1, min(254, int(foreground_threshold))),
                foreground_detection=(
                    "adaptive-local-contrast" if adaptive_foreground else "fixed-local-contrast"
                ),
            )
        page_metrics: list[VisualMetrics] = []
        for index in range(min(len(references), len(candidates))):
            page_metrics.append(
                _single_visual_metrics(
                    references[index],
                    candidates[index],
                    threshold=threshold,
                    normalize_illumination=normalize_illumination,
                    distance_tolerance=distance_tolerance,
                    foreground_threshold=foreground_threshold,
                    adaptive_foreground=adaptive_foreground,
                    region_grid=normalized_grid,
                )
            )
        missing = page_count - len(page_metrics)
        # Missing pages are complete visual mismatches without inventing a
        # synthetic pixel count for them.
        pixel = (sum(item.pixel_similarity for item in page_metrics)) / page_count
        rms = (sum(item.rms_similarity for item in page_metrics)) / page_count
        dimensions = (sum(item.dimension_similarity for item in page_metrics)) / page_count
        total_pixels = sum(item.total_pixels for item in page_metrics)
        differing = sum(item.differing_pixels for item in page_metrics)
        if missing and total_pixels:
            differing = total_pixels
        foreground_precision = (
            sum(item.foreground_precision or 0.0 for item in page_metrics) / page_count
        )
        foreground_recall = sum(item.foreground_recall or 0.0 for item in page_metrics) / page_count
        foreground_f1 = sum(item.foreground_f1 or 0.0 for item in page_metrics) / page_count
        edge_similarity = sum(item.edge_similarity or 0.0 for item in page_metrics) / page_count
        region_similarity = sum(item.region_similarity or 0.0 for item in page_metrics) / page_count
        page_count_similarity = _ratio(len(references), len(candidates))
        page_score_macro = sum(item.score for item in page_metrics) / page_count
        return VisualMetrics(
            pixel,
            rms,
            dimensions,
            differing,
            total_pixels,
            pages_compared=page_count,
            foreground_precision=foreground_precision,
            foreground_recall=foreground_recall,
            foreground_f1=foreground_f1,
            edge_similarity=edge_similarity,
            region_similarity=region_similarity,
            page_count_similarity=page_count_similarity,
            dimensions_match=(not missing and all(item.dimensions_match for item in page_metrics)),
            reference_page_count=len(references),
            candidate_page_count=len(candidates),
            reference_foreground_pixels=sum(
                item.reference_foreground_pixels for item in page_metrics
            ),
            candidate_foreground_pixels=sum(
                item.candidate_foreground_pixels for item in page_metrics
            ),
            regions_compared=sum(item.regions_compared for item in page_metrics),
            distance_tolerance=max(0, int(distance_tolerance)),
            metric_version=VISUAL_METRIC_VERSION,
            page_score_macro=page_score_macro,
            foreground_threshold=max(1, min(254, int(foreground_threshold))),
            foreground_detection=(
                "adaptive-local-contrast" if adaptive_foreground else "fixed-local-contrast"
            ),
        )
    return _single_visual_metrics(
        reference,
        candidate,
        threshold=threshold,
        normalize_illumination=normalize_illumination,
        distance_tolerance=distance_tolerance,
        foreground_threshold=foreground_threshold,
        adaptive_foreground=adaptive_foreground,
        region_grid=normalized_grid,
    )


def visual_diff(
    reference: Any,
    candidate: Any,
    output_path: str | Path | None = None,
    *,
    amplify: float = 4.0,
    threshold: int = 0,
) -> Any:
    """Create a red-overlay difference image and optionally save it."""

    api = _pillow()
    Image = api["Image"]
    ref_image = _load_image(reference, api)
    cand_image = _load_image(candidate, api)
    ref_canvas, cand_canvas = _common_canvas(ref_image, cand_image, api)
    difference = api["ImageChops"].difference(ref_canvas, cand_canvas)
    grayscale = api["ImageOps"].grayscale(difference)
    threshold = max(0, min(254, int(threshold)))
    factor = max(0.0, float(amplify))
    alpha = grayscale.point(
        lambda pixel: 0 if pixel <= threshold else min(255, int(pixel * factor))
    )
    base = ref_canvas.convert("RGBA")
    # Muting the original makes both missing and displaced content legible.
    white = Image.new("RGBA", base.size, (255, 255, 255, 255))
    base = Image.blend(base, white, 0.55)
    overlay = Image.new("RGBA", base.size, (220, 24, 24, 0))
    overlay.putalpha(alpha)
    result = Image.alpha_composite(base, overlay).convert("RGB")
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        result.save(destination)
    return result


def _png_data_uri(source: Any, api: dict[str, Any]) -> str:
    image = _opaque_rgb(_load_image(source, api), api)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def visual_comparison_report(
    reference: Any,
    candidate: Any,
    output_path: str | Path | None = None,
    *,
    title: str = "Document visual comparison",
    threshold: int = 0,
    amplify: float = 4.0,
) -> str:
    """Return a self-contained reference/candidate/diff HTML report.

    Raster inputs and all styling are embedded; the generated report performs
    no network or local-file requests when opened.
    """

    if _is_image_sequence(reference) or _is_image_sequence(candidate):
        raise TypeError("visual_comparison_report accepts one reference and candidate image")
    api = _pillow()
    metrics = evaluate_visual(reference, candidate, threshold=threshold)
    difference = visual_diff(
        reference,
        candidate,
        amplify=amplify,
        threshold=threshold,
    )
    images = (
        ("Reference", _png_data_uri(reference, api)),
        ("Candidate", _png_data_uri(candidate, api)),
        ("Red diff", _png_data_uri(difference, api)),
    )
    figures = "".join(
        f"<figure><figcaption>{html.escape(label)}</figcaption>"
        f'<img src="{uri}" alt="{html.escape(label, quote=True)}"></figure>'
        for label, uri in images
    )
    metric_rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{value:.4f}</td></tr>"
        for label, value in (
            ("Overall visual score", metrics.score),
            ("Foreground precision", metrics.foreground_precision or 0.0),
            ("Foreground recall", metrics.foreground_recall or 0.0),
            ("Foreground F1", metrics.foreground_f1 or 0.0),
            ("Edge spatial similarity", metrics.edge_similarity or 0.0),
            ("Region macro similarity", metrics.region_similarity or 0.0),
            ("Pixel similarity", metrics.pixel_similarity),
            ("RMS similarity", metrics.rms_similarity),
            ("Dimension similarity", metrics.dimension_similarity),
        )
    )
    report = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;padding:24px;background:#f3f4f6;color:#111827;
font:14px system-ui,sans-serif}}h1{{margin-top:0}}main{{display:grid;
grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}figure{{margin:0;padding:12px;
background:#fff;border:1px solid #d1d5db;border-radius:6px}}figcaption{{font-weight:700;
margin-bottom:8px}}img{{display:block;width:100%;height:auto;background:#fff}}table{{margin-top:20px;
border-collapse:collapse;background:#fff}}th,td{{padding:7px 10px;border:1px solid #d1d5db;
text-align:left}}@media(max-width:800px){{main{{grid-template-columns:1fr}}}}
</style></head><body><h1>{html.escape(title)}</h1><main>{figures}</main>
<table><tbody><tr><th>Metric version</th><td>{metrics.metric_version}</td></tr>
<tr><th>Foreground detection</th><td>{metrics.foreground_detection}</td></tr>
<tr><th>Foreground strong threshold</th><td>{metrics.foreground_threshold}</td></tr>
{metric_rows}<tr><th>Differing pixels</th>
<td>{metrics.differing_pixels} / {metrics.total_pixels}</td></tr></tbody></table>
</body></html>
"""
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report, encoding="utf-8", newline="\n")
    return report


visual_similarity = evaluate_visual
compare_images = evaluate_visual
comparison_report = visual_comparison_report
