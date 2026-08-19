"""Optional FastAPI integration.

Importing :mod:`docreconstruct.api` does not require the ``api`` extra.  The
extra is checked only when an application is created.
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Create the HTTP application, with an actionable optional-dependency error."""

    try:
        from .app import create_app as _create_app
    except ModuleNotFoundError as exc:
        if exc.name in {"fastapi", "multipart", "uvicorn"}:
            raise RuntimeError(
                "The HTTP API requires optional dependencies. "
                "Install them with `pip install 'docreconstruct[api]'`."
            ) from exc
        raise
    except RuntimeError as exc:
        if "python-multipart" in str(exc):
            raise RuntimeError(
                "Multipart uploads require the API extra. "
                "Install it with `pip install 'docreconstruct[api]'`."
            ) from exc
        raise
    return _create_app(*args, **kwargs)
