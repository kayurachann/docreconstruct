"""Small dependency-free provider registry."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import metadata as importlib_metadata
from typing import TypeAlias

from .base import Provider, ProviderCapabilities

ProviderRegistration: TypeAlias = Provider | type[Provider]


class ProviderRegistry:
    """Register provider classes/instances by stable, case-insensitive names."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderRegistration] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}

    @staticmethod
    def _key(name: str) -> str:
        key = name.strip().lower().replace("-", "_")
        if not key:
            raise ValueError("provider name must not be blank")
        return key

    def register(
        self,
        provider: ProviderRegistration,
        *,
        name: str | None = None,
        replace: bool = False,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderRegistration:
        provider_name = name or getattr(provider, "name", None)
        if not provider_name:
            raise ValueError("provider registrations require a name")
        key = self._key(provider_name)
        if key in self._providers and not replace:
            raise ValueError(f"provider {provider_name!r} is already registered")
        self._providers[key] = provider
        declared = capabilities or self._declared_capabilities(provider)
        if declared is not None:
            self._capabilities[key] = declared
        elif replace:
            self._capabilities.pop(key, None)
        return provider

    @staticmethod
    def _declared_capabilities(
        provider: ProviderRegistration,
    ) -> ProviderCapabilities | None:
        """Read declarations without constructing a provider class."""

        if isinstance(provider, Provider):
            return provider.capabilities
        declared = getattr(provider, "_capabilities", None)
        return declared if isinstance(declared, ProviderCapabilities) else None

    def unregister(self, name: str) -> ProviderRegistration:
        key = self._key(name)
        try:
            registration = self._providers.pop(key)
        except KeyError as exc:
            raise KeyError(f"unknown provider {name!r}") from exc
        self._capabilities.pop(key, None)
        return registration

    def get(self, name: str) -> Provider:
        key = self._key(name)
        try:
            registration = self._providers[key]
        except KeyError as exc:
            available = ", ".join(self.names()) or "none"
            raise KeyError(f"unknown provider {name!r}; available: {available}") from exc
        return registration() if isinstance(registration, type) else registration

    create = get

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def get_capabilities(self, name: str) -> ProviderCapabilities | None:
        """Return declared capabilities without instantiating the provider."""

        key = self._key(name)
        if key not in self._providers:
            raise KeyError(f"unknown provider {name!r}")
        return self._capabilities.get(key)

    def capability_items(self) -> tuple[tuple[str, ProviderCapabilities], ...]:
        """Return stable capability declarations for dependency-free routing."""

        return tuple((name, self._capabilities[name]) for name in sorted(self._capabilities))

    def load_entry_points(
        self,
        group: str = "docreconstruct.providers",
        *,
        replace: bool = False,
    ) -> tuple[str, ...]:
        """Explicitly load third-party provider plugins from entry points.

        Discovery is opt-in so importing the core package never executes
        untrusted or heavyweight plugin code as a side effect.
        """

        entry_points = importlib_metadata.entry_points(group=group)
        loaded: list[str] = []
        for entry_point in entry_points:
            registration = entry_point.load()
            self.register(registration, name=entry_point.name, replace=replace)
            loaded.append(self._key(entry_point.name))
        return tuple(loaded)

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str) or not name.strip():
            return False
        return self._key(name) in self._providers

    def __iter__(self) -> Iterator[str]:
        return iter(self.names())

    def __len__(self) -> int:
        return len(self._providers)


_REGISTRY = ProviderRegistry()


def get_registry() -> ProviderRegistry:
    """Return the process-wide registry populated by ``providers.__init__``."""

    return _REGISTRY
