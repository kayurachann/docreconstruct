"""Resolve Markdown image evidence and locate it in source page rasters."""

from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import os
import re
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from docreconstruct.reconstruction.markdown_content import MarkdownBlock, MarkdownContent
from docreconstruct.reconstruction.scan_layout import PixelBox, ScanDocumentLayout

_PROVIDER_CROP_HINT = re.compile(
    r"(?:img_in_image_box|image_box|crop)[_-]"
    r"(?P<x0>\d+)[_-](?P<y0>\d+)[_-](?P<x1>\d+)[_-](?P<y1>\d+)",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolvedAsset:
    """Validated local bytes for one Markdown image reference."""

    source: str
    data: bytes
    media_type: str
    image: Image.Image


class AssetMatch(BaseModel):
    """A Markdown image aligned to original pixels in the layout PDF."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    source: str
    page_number: int = Field(ge=1)
    bbox: PixelBox
    score: float = Field(ge=-1.0, le=1.0)
    resolved: bool = True


def _read_limited(stream: Any, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, maximum_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_bytes:
            raise ValueError(f"image asset exceeds the {maximum_bytes}-byte safety limit")
        chunks.append(chunk)
    return b"".join(chunks)


@lru_cache(maxsize=128)
def _download_remote(source: str, timeout: float, maximum_bytes: int) -> tuple[bytes, str]:
    cache_root = Path(
        os.environ.get(
            "DOCRECONSTRUCT_ASSET_CACHE",
            Path(tempfile.gettempdir()) / "docreconstruct" / "assets",
        )
    )
    cache_key = hashlib.sha256(source.encode("utf-8")).hexdigest()
    payload_path = cache_root / f"{cache_key}.bin"
    media_type_path = cache_root / f"{cache_key}.type"
    if payload_path.is_file():
        data = payload_path.read_bytes()
        if len(data) <= maximum_bytes:
            media_type = (
                media_type_path.read_text(encoding="ascii").strip()
                if media_type_path.is_file()
                else "application/octet-stream"
            )
            return data, media_type
    request = urllib.request.Request(
        source,
        headers={"User-Agent": "docreconstruct/0.1 hybrid-layout-matcher"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        data = _read_limited(response, maximum_bytes)
        media_type = response.headers.get_content_type() or "application/octet-stream"
    cache_root.mkdir(parents=True, exist_ok=True)
    temporary_payload = payload_path.with_suffix(".bin.tmp")
    temporary_media_type = media_type_path.with_suffix(".type.tmp")
    temporary_payload.write_bytes(data)
    temporary_media_type.write_text(media_type, encoding="ascii")
    temporary_payload.replace(payload_path)
    temporary_media_type.replace(media_type_path)
    return data, media_type


def resolve_markdown_asset(
    block: MarkdownBlock,
    *,
    markdown_directory: Path,
    allow_remote: bool = True,
    timeout: float = 20.0,
    maximum_bytes: int = 25 * 1024 * 1024,
) -> ResolvedAsset | None:
    """Resolve data/local/HTTPS image references without invoking an AI service."""

    source = (block.source or "").strip()
    if not source:
        return None
    media_type = "application/octet-stream"
    data: bytes
    if source.startswith("data:"):
        header, separator, payload = source.partition(",")
        if not separator:
            raise ValueError(f"invalid data URI in {block.id}")
        media_type = header[5:].split(";", 1)[0] or media_type
        data = (
            base64.b64decode(payload)
            if ";base64" in header
            else urllib.parse.unquote_to_bytes(payload)
        )
    elif source.startswith(("https://", "http://")):
        if not allow_remote:
            return None
        if not source.startswith("https://"):
            raise ValueError(f"remote Markdown images must use HTTPS: {source}")
        data, media_type = _download_remote(source, timeout, maximum_bytes)
    else:
        parsed = urllib.parse.urlparse(source)
        relative = urllib.parse.unquote(parsed.path)
        candidate = Path(relative)
        if not candidate.is_absolute():
            candidate = markdown_directory / candidate
        candidate = candidate.expanduser().resolve()
        if not candidate.is_file():
            return None
        data = candidate.read_bytes()
        if len(data) > maximum_bytes:
            raise ValueError(f"image asset exceeds the {maximum_bytes}-byte safety limit")
        media_type = mimetypes.guess_type(candidate.name)[0] or media_type
    try:
        opened = Image.open(io.BytesIO(data))
        detected_format = opened.format
        image = opened.convert("RGB")
        image.load()
    except (OSError, ValueError) as exc:
        raise ValueError(f"Markdown asset is not a readable raster image: {source}") from exc
    if not media_type.startswith("image/"):
        media_type = Image.MIME.get(detected_format or "", "image/png")
    return ResolvedAsset(source=source, data=data, media_type=media_type, image=image)


def _integral(array: Any) -> Any:
    np = _require_numpy()
    stable = array.astype(np.float64, copy=False)
    return np.pad(stable.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))


def _window_sums(integral: Any, height: int, width: int) -> Any:
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("asset matching requires `docreconstruct[hybrid]`") from exc
    return np


def _ncc_map(page: Any, template: Any) -> Any:
    """FFT-backed normalized cross correlation for every valid placement."""

    np = _require_numpy()
    page = page.astype(np.float64, copy=False)
    template = template.astype(np.float64, copy=False)
    page_height, page_width = page.shape
    template_height, template_width = template.shape
    if template_height > page_height or template_width > page_width:
        return np.empty((0, 0), dtype=np.float64)
    centered = template - float(template.mean())
    template_norm = float(np.sqrt(np.square(centered).sum()))
    if template_norm < 1e-6:
        return np.empty((0, 0), dtype=np.float64)
    fft_shape = (page_height + template_height - 1, page_width + template_width - 1)
    convolution = np.fft.irfft2(
        np.fft.rfft2(page, fft_shape) * np.fft.rfft2(np.flip(centered, axis=(0, 1)), fft_shape),
        fft_shape,
    )
    numerator = convolution[
        template_height - 1 : page_height,
        template_width - 1 : page_width,
    ]
    integral = _integral(page)
    integral_square = _integral(np.square(page))
    patch_sum = _window_sums(integral, template_height, template_width)
    patch_square = _window_sums(integral_square, template_height, template_width)
    count = float(template_height * template_width)
    variance = np.maximum(patch_square - np.square(patch_sum) / count, 0.0)
    denominator = np.sqrt(variance) * template_norm
    valid = variance >= max(1e-6, template_norm * template_norm * 1e-4)
    scores = np.full_like(numerator, -1.0)
    np.divide(numerator, denominator, out=scores, where=valid & (denominator > 0))
    return np.clip(scores, -1.0, 1.0)


def _gray_array(image: Image.Image, *, width: int | None = None, height: int | None = None) -> Any:
    np = _require_numpy()
    if width is not None and height is not None:
        image = image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    return np.asarray(image.convert("L"), dtype=np.float32)


def _match_asset_to_pages(
    asset: ResolvedAsset,
    layout: ScanDocumentLayout,
    *,
    first_page: int,
) -> tuple[int, PixelBox, float] | None:
    np = _require_numpy()
    best: tuple[int, PixelBox, float] | None = None
    scales = (0.72, 0.80, 0.88, 0.94, 1.0, 1.06, 1.14, 1.24)
    for page in layout.pages:
        if page.number < first_page:
            continue
        reduction = max(1.0, page.width / 520.0)
        reduced_width = max(1, int(round(page.width / reduction)))
        reduced_height = max(1, int(round(page.height / reduction)))
        page_array = _gray_array(page.image, width=reduced_width, height=reduced_height)
        for scale in scales:
            template_width = int(round(asset.image.width * scale / reduction))
            template_height = int(round(asset.image.height * scale / reduction))
            if template_width < 12 or template_height < 12:
                continue
            if template_width >= reduced_width or template_height >= reduced_height:
                continue
            template_array = _gray_array(
                asset.image,
                width=template_width,
                height=template_height,
            )
            scores = _ncc_map(page_array, template_array)
            if not scores.size:
                continue
            flat_index = int(np.argmax(scores))
            y, x = np.unravel_index(flat_index, scores.shape)
            score = float(scores[y, x])
            if best is not None and score <= best[2]:
                continue
            x0 = max(0, int(round(x * reduction)))
            y0 = max(0, int(round(y * reduction)))
            x1 = min(page.width, int(round((x + template_width) * reduction)))
            y1 = min(page.height, int(round((y + template_height) * reduction)))
            if x1 <= x0 or y1 <= y0:
                continue
            best = (page.number, PixelBox(x0=x0, y0=y0, x1=x1, y1=y1), score)
    return best


def _source_bbox_hint(
    source: str,
    layout: ScanDocumentLayout,
    *,
    first_page: int,
) -> tuple[int, PixelBox] | None:
    """Read a provider-exported crop box from an image filename.

    PaddleOCR and several compatible exporters retain the source crop as four
    integer coordinates in asset filenames.  This is provenance, not OCR: it
    lets an expired or offline URL fall back to the exact original scan crop.
    Hints are accepted only for an unrectified raster with matching bounds.
    """

    path = urllib.parse.unquote(urllib.parse.urlparse(source).path)
    match = _PROVIDER_CROP_HINT.search(Path(path).name)
    if match is None:
        return None
    x0, y0, x1, y1 = (int(match.group(name)) for name in ("x0", "y0", "x1", "y1"))
    if x1 <= x0 or y1 <= y0:
        return None
    for page in layout.pages:
        if page.number < first_page or bool(page.metadata.get("rectified")):
            continue
        if 0 <= x0 < x1 <= page.width and 0 <= y0 < y1 <= page.height:
            return page.number, PixelBox(x0=x0, y0=y0, x1=x1, y1=y1)
    return None


def match_markdown_assets(
    content: MarkdownContent,
    layout: ScanDocumentLayout,
    *,
    allow_remote: bool = True,
    minimum_score: float = 0.42,
) -> list[AssetMatch]:
    """Locate Markdown images monotonically in the original PDF page rasters."""

    directory = Path(content.source).parent
    matches: list[AssetMatch] = []
    first_page = 1
    for block in content.image_blocks:
        source = (block.source or "").strip()
        hint = _source_bbox_hint(source, layout, first_page=first_page)
        try:
            asset = resolve_markdown_asset(
                block,
                markdown_directory=directory,
                allow_remote=allow_remote,
            )
        except (OSError, TimeoutError, ValueError):
            if not source.startswith(("https://", "http://")):
                raise
            asset = None
        matched = (
            _match_asset_to_pages(asset, layout, first_page=first_page)
            if asset is not None
            else None
        )
        if matched is not None and matched[2] >= minimum_score:
            page_number, bbox, score = matched
            resolved = True
        elif hint is not None:
            page_number, bbox = hint
            score = 0.82
            resolved = False
        else:
            continue
        matches.append(
            AssetMatch(
                block_id=block.id,
                source=asset.source if asset is not None else source,
                page_number=page_number,
                bbox=bbox,
                score=score,
                resolved=resolved,
            )
        )
        first_page = page_number
    return matches


__all__ = [
    "AssetMatch",
    "ResolvedAsset",
    "match_markdown_assets",
    "resolve_markdown_asset",
]
