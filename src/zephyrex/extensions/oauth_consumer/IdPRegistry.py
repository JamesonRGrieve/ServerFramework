"""Process-global registry of IdP provider classes keyed by name.

Concrete provider modules call :func:`register_idp` at import time. The
``oauth_consumer`` BLL queries the registry at callback time to dispatch
``code`` exchange to the correct implementation.
"""

from typing import Dict, List, Type

from zephyrex.extensions.oauth_consumer.PRV_AbstractIdP import AbstractIdPProvider


_REGISTRY: Dict[str, Type[AbstractIdPProvider]] = {}


def register_idp(name: str, provider_class: Type[AbstractIdPProvider]) -> None:
    _REGISTRY[name.lower()] = provider_class


def get_idp(name: str) -> Type[AbstractIdPProvider]:
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        raise ValueError(f"Unsupported IdP: {name}")
    return cls


def list_idps() -> List[str]:
    return sorted(_REGISTRY.keys())
