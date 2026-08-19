FROM python:3.11-slim

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
