from __future__ import annotations

import os

from .base import NotConnected, Provider, ProviderError
from .files import FileProvider

_registry: dict[str, Provider] = {}


def _build() -> dict[str, Provider]:
    if os.environ.get("PLT_DEMO"):
        from .demo import demo_providers

        reg = demo_providers()
    else:
        from .qobuz import QobuzProvider
        from .spotify import SpotifyProvider

        reg = {"spotify": SpotifyProvider(), "qobuz": QobuzProvider()}
    reg["file"] = FileProvider()
    return reg


def registry() -> dict[str, Provider]:
    if not _registry:
        _registry.update(_build())
    return _registry


def get(name: str) -> Provider:
    try:
        return registry()[name]
    except KeyError:
        raise ProviderError(f"Unknown service '{name}'") from None


def set_registry(reg: dict[str, Provider]) -> None:
    """Used by tests."""
    _registry.clear()
    _registry.update(reg)


__all__ = ["Provider", "ProviderError", "NotConnected", "get", "registry", "set_registry"]
