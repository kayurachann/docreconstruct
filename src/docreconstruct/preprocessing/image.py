"""Lossless image ingestion used when no live OCR provider is configured."""

from __future__ import annotations

import base64
import hashlib
import io
from contextlib import suppress
from pathlib import Path

from docreconstruct.exceptions import UnsupportedInputError
from docreconstruct.ir import (
    BBox,
    Document,
    Element,
    ElementType,
    Page,
    Provenance,
    SourceType,
)


def _content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def image_to_document(source: str | Path) -> Document:
    """Represent an image as exact page evidence without inventing OCR text.

    The raster remains an editable image object in downstream formats. Text is
    intentionally absent until an OCR provider contributes observations.
    """

    from PIL import Image, ImageOps, UnidentifiedImageError

    path = Path(source).expanduser().resolve()
    digest = _content_digest(path)
    pages: list[Page] = []
    try:
        opened = Image.open(path)
    except (UnidentifiedImageError, OSError) as exc:
        # A raster Pillow cannot decode is ordinary bad input, not a server
        # fault. Letting the OSError escape reached the API's unhandled branch
        # and answered 500 with a logged traceback, mirroring the PDF path
        # before it raised UnsupportedInputError.
        raise UnsupportedInputError(f"could not open image: {path.name}") from exc
    with opened as image:
        frames = getattr(image, "n_frames", 1)
        for index in range(frames):
            if frames > 1:
                image.seek(index)
            oriented = ImageOps.exif_transpose(image)
            width, height = oriented.size
            page_number = index + 1
            page_id = f"page_{page_number:04d}"
            exif_orientation = None
            with suppress(AttributeError, TypeError, ValueError):
                exif_orientation = image.getexif().get(274)
            if frames > 1 or exif_orientation not in (None, 1):
                frame_buffer = io.BytesIO()
                oriented.save(frame_buffer, format="PNG")
                image_reference = {
                    "data": base64.b64encode(frame_buffer.getvalue()).decode("ascii"),
                    "mime_type": "image/png",
                }
            else:
                mime_type = Image.MIME.get(str(image.format), "application/octet-stream")
                image_reference = {
                    "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                    "mime_type": mime_type,
                }
            pages.append(
                Page(
                    id=page_id,
                    number=page_number,
                    width=float(width),
                    height=float(height),
                    source_type=SourceType.IMAGE,
                    metadata={
                        "dpi": image.info.get("dpi"),
                        "source_exif_orientation": exif_orientation,
                    },
                    elements=[
                        Element(
                            id=f"{page_id}_image_0001",
                            type=ElementType.IMAGE,
                            bbox=BBox(x0=0, y0=0, x1=width, y1=height),
                            reading_order=1,
                            confidence=1.0,
                            provenance=Provenance(
                                engine="source_image",
                                source_id=f"frame:{index}",
                                layout_confidence=1.0,
                            ),
                            metadata={
                                "image": image_reference,
                                "frame": index,
                                "sha256": digest,
                            },
                        )
                    ],
                )
            )
    return Document(
        id=f"doc_{digest[:16]}",
        source=str(path),
        pages=pages,
        metadata={
            "sha256": digest,
            "ingestion": "lossless-image",
            "warning": "No OCR text was invented; configure an OCR provider to extract text.",
        },
    )
