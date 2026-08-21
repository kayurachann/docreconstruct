FROM python:3.11-slim AS runtime

ARG DOCRECONSTRUCT_EXTRAS=api,pdf,docx

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOCRECONSTRUCT_HOST=0.0.0.0 \
    DOCRECONSTRUCT_PORT=8000

WORKDIR /app

RUN groupadd --system docreconstruct \
    && useradd --system --gid docreconstruct --create-home docreconstruct

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir ".[${DOCRECONSTRUCT_EXTRAS}]"

USER docreconstruct

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "docreconstruct.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------------------
# `full`: everything a self-contained public deployment needs, with no cloud
# credential. Tesseract supplies free server-side OCR evidence (opt in with
# DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=tesseract_local) and LibreOffice enables
# render QA and `verified` quality (point DOCRECONSTRUCT_LIBREOFFICE_PATH at
# /usr/bin/soffice). Build with: docker build --target full .
# ---------------------------------------------------------------------------
FROM runtime AS full

USER root
RUN apt-get update && apt-get install -y --no-install-recommends         tesseract-ocr         tesseract-ocr-eng         tesseract-ocr-vie         tesseract-ocr-chi-sim         tesseract-ocr-osd         libreoffice-writer         fonts-liberation         fonts-dejavu         fonts-noto-cjk     && rm -rf /var/lib/apt/lists/*

ENV DOCRECONSTRUCT_PUBLIC_OCR_PROVIDERS=tesseract_local     DOCRECONSTRUCT_LIBREOFFICE_PATH=/usr/bin/soffice

USER docreconstruct
