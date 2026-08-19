"""Run the optional API with ``python -m docreconstruct.api``."""

from __future__ import annotations

import os


def main() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "The HTTP API is not installed. Run `pip install 'docreconstruct[api]'`."
        ) from exc

    uvicorn.run(
        "docreconstruct.api.app:app",
        host=os.getenv("DOCRECONSTRUCT_HOST", "0.0.0.0"),
        port=int(os.getenv("DOCRECONSTRUCT_PORT", "8000")),
        reload=os.getenv("DOCRECONSTRUCT_RELOAD", "").lower() in {"1", "true", "yes"},
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the console entry point
    main()
