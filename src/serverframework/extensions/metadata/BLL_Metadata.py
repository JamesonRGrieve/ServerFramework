"""Re-export shim for the metadata models extracted from core (Scope #3).

The canonical Pydantic classes still live in `logic/BLL_Auth.py`; this module
provides the clean import path so consumer code can write:

    from serverframework.extensions.metadata.BLL_Metadata import MetadataModel

…rather than reaching into core. A follow-up will physically move the
definitions; until then, the names resolve through this re-export.
"""

# Use BLL_Auth's PEP 562 __getattr__ to pull through the canonical types.
from serverframework.logic import BLL_Auth as _core


def __getattr__(name: str):
    if name in {
        "MetadataModel",
        "MetadataManager",
        "UserMetadataManager",
        "TeamMetadataManager",
    }:
        return getattr(_core, name)
    raise AttributeError(name)


__all__ = [
    "MetadataModel",
    "MetadataManager",
    "UserMetadataManager",
    "TeamMetadataManager",
]
