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


def _document_foreground(image: Any, api: dict[str, Any]) -> Any:
    """Normalize photographed page illumination to a black-on-white ink map."""

    grayscale = api["ImageOps"].grayscale(_opaque_rgb(image, api))
    radius = max(5.0, min(grayscale.size) / 45.0)
    background = grayscale.filter(api["ImageFilter"].GaussianBlur(radius=radius))
    contrast = api["ImageChops"].subtract(background, grayscale)
    return contrast.point(lambda value: 0 if value >= 16 else 255).convert("RGB")


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

    @property
    def score(self) -> float:
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
        return {**dataclasses.asdict(self), "score": self.score}


def _single_visual_metrics(
    reference: Any,
    candidate: Any,
    *,
    threshold: int = 0,
    normalize_illumination: bool = False,
) -> VisualMetrics:
    api = _pillow()
    ref_image = _load_image(reference, api)
    cand_image = _load_image(candidate, api)
    dimension_similarity = (
        _ratio(ref_image.width, cand_image.width) + _ratio(ref_image.height, cand_image.height)
    ) / 2
    if normalize_illumination:
        ref_image = _document_foreground(ref_image, api)
        cand_image = _document_foreground(cand_image, api)
    ref_canvas, cand_canvas = _common_canvas(ref_image, cand_image, api)
    difference = api["ImageChops"].difference(ref_canvas, cand_canvas)
    statistics = api["ImageStat"].Stat(difference)
    pixel_similarity = 1.0 - sum(statistics.mean) / (len(statistics.mean) * 255.0)
    rms_similarity = 1.0 - sum(statistics.rms) / (len(statistics.rms) * 255.0)
    grayscale = api["ImageOps"].grayscale(difference)
    histogram = grayscale.histogram()
    threshold = max(0, min(254, int(threshold)))
    differing_pixels = sum(histogram[threshold + 1 :])
    return VisualMetrics(
        pixel_similarity=max(0.0, min(1.0, pixel_similarity)),
        rms_similarity=max(0.0, min(1.0, rms_similarity)),
        dimension_similarity=dimension_similarity,
        differing_pixels=differing_pixels,
        total_pixels=ref_canvas.width * ref_canvas.height,
    )


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
) -> VisualMetrics:
    """Compare one image or two ordered page-image sequences."""

    if _is_image_sequence(reference) or _is_image_sequence(candidate):
        references = list(reference) if _is_image_sequence(reference) else [reference]
        candidates = list(candidate) if _is_image_sequence(candidate) else [candidate]
        page_count = max(len(references), len(candidates))
        if page_count == 0:
            return VisualMetrics(1.0, 1.0, 1.0, 0, 0, pages_compared=0)
        page_metrics: list[VisualMetrics] = []
        for index in range(min(len(references), len(candidates))):
            page_metrics.append(
                _single_visual_metrics(
                    references[index],
                    candidates[index],
                    threshold=threshold,
                    normalize_illumination=normalize_illumination,
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
        return VisualMetrics(
            pixel, rms, dimensions, differing, total_pixels, pages_compared=page_count
        )
    return _single_visual_metrics(
        reference,
        candidate,
        threshold=threshold,
        normalize_illumination=normalize_illumination,
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
<table><tbody>{metric_rows}<tr><th>Differing pixels</th>
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
