"""Renderer contract, output helpers, and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

Rendered = TypeVar("Rendered", str, bytes)


class RendererError(RuntimeError):
    """Base exception for deterministic reconstruction renderers."""


class OptionalDependencyError(RendererError, ImportError):
    """Raised only when a requested optional rendering feature is unavailable."""


class Renderer(ABC, Generic[Rendered]):
    """Abstract renderer for a canonical document IR.

    ``render`` is side-effect free.  ``write`` is the shared filesystem helper
    and returns the resolved output path for ergonomic CLI/API integration.
    """

    format: ClassVar[str]
    extension: ClassVar[str]
    media_type: ClassVar[str]

    @abstractmethod
    def render(self, document: Any) -> Rendered:
        """Render *document* into text or bytes without writing to disk."""

    def write(self, document: Any, destination: str | Path) -> Path:
        destination = Path(destination)
        payload = self.render(document)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            destination.write_bytes(payload)
        else:
            destination.write_text(payload, encoding="utf-8", newline="\n")
        return destination.resolve()

    @classmethod
    def is_available(cls) -> bool:
        return True


# A compatibility name commonly used by plugin authors.
BaseRenderer = Renderer


class RendererRegistry:
    """Case-insensitive registry for renderer classes.

    Registering classes instead of singleton instances prevents option leakage
    across concurrent jobs.  A factory may also be registered for plugins.
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Renderer[Any]]] = {}

    @staticmethod
    def _key(name: str) -> str:
        key = str(name).strip().lower().lstrip(".")
        if not key:
            raise ValueError("renderer name must not be empty")
        return key

    def register(
        self,
        name: str | type[Renderer[Any]] | None = None,
        renderer: Callable[..., Renderer[Any]] | None = None,
        *,
        replace: bool = False,
    ) -> Callable[..., Renderer[Any]] | Callable[[type[Renderer[Any]]], type[Renderer[Any]]]:
        """Register directly or use as ``@registry.register()`` decorator."""

        if isinstance(name, type) and issubclass(name, Renderer):
            renderer_class = name
            self.register(renderer_class.format, renderer_class, replace=replace)
            return renderer_class

        if renderer is None:
            explicit_name = name

            def decorator(renderer_class: type[Renderer[Any]]) -> type[Renderer[Any]]:
                registered_name = explicit_name or renderer_class.format
                self.register(str(registered_name), renderer_class, replace=replace)
                return renderer_class

            return decorator

        key = self._key(str(name))
        if key in self._factories and not replace:
            raise ValueError(f"renderer already registered: {key}")
        self._factories[key] = renderer
        return renderer

    def unregister(self, name: str) -> None:
        self._factories.pop(self._key(name), None)

    def create(self, name: str, **options: Any) -> Renderer[Any]:
        key = self._key(name)
        try:
            factory = self._factories[key]
        except KeyError as exc:
            available = ", ".join(self.formats()) or "none"
            raise KeyError(f"unknown renderer {name!r}; available: {available}") from exc
        return factory(**options)

    def get(self, name: str, **options: Any) -> Renderer[Any]:
        return self.create(name, **options)

    def formats(self, *, available_only: bool = False) -> tuple[str, ...]:
        names: list[str] = []
        for name in sorted(self._factories):
            if not available_only:
                names.append(name)
                continue
            factory = self._factories[name]
            available = getattr(factory, "is_available", lambda: True)()
            if available:
                names.append(name)
        return tuple(names)

    def names(self, *, available_only: bool = False) -> tuple[str, ...]:
        """Alias used by discovery-oriented CLI and API code."""

        return self.formats(available_only=available_only)

    def load_entry_points(
        self,
        group: str = "docreconstruct.renderers",
        *,
        replace: bool = False,
    ) -> tuple[str, ...]:
        """Load opt-in renderer factories advertised by installed packages."""

        loaded: list[str] = []
        entry_points = metadata.entry_points()
        selected = entry_points.select(group=group)
        for entry_point in sorted(selected, key=lambda item: item.name):
            factory = entry_point.load()
            self.register(entry_point.name, factory, replace=replace)
            loaded.append(self._key(entry_point.name))
        return tuple(loaded)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self._key(name) in self._factories

    def __iter__(self) -> Iterator[str]:
        return iter(self.formats())

    def __len__(self) -> int:
        return len(self._factories)


registry = RendererRegistry()


def get_renderer(name: str, **options: Any) -> Renderer[Any]:
    return registry.create(name, **options)


def render_document(document: Any, output_format: str, **options: Any) -> str | bytes:
    return get_renderer(output_format, **options).render(document)
