# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_sdk_py extension — opt-in generation of the typed Python client SDK."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_MetaSDKPy(AbstractStaticExtension):
    """Generate a typed Python client SDK for the installed extensions.

    SDK generation is an integration shape, not universal scaffold, so it is
    opt-in. Enabling this extension registers a Python SDK generator on the core
    ``generate_sdk`` registry hook (``lib/Hooks.register_sdk_generator``);
    ``ModelRegistry.commit`` then drives it over the committed registry, emitting
    ``<Resource>SDK_generated.py`` for every RouterMixin-tagged manager. The
    generator self-gates on ``SDK_PY_OUTPUT_DIR``, so enabling the extension
    without a destination is a no-op — a normal server boot never writes files.

    Sibling extensions ``meta_sdk_ts`` and ``meta_sdk_rs`` emit the same REST
    surface in TypeScript and Rust from the shared ``sdk.SDKModel`` IR.
    """

    name: ClassVar[str] = "meta_sdk_py"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Opt-in generation of the typed Python client SDK from the "
        "committed registry"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {"sdk_generate_python"}
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing meta_sdk_py extension")
        return True

    @classmethod
    def on_load(cls) -> None:
        from zephyrex.extensions.meta_sdk_py.PythonSDKEmitter import (
            generate_python_sdk,
        )
        from zephyrex.lib.Hooks import register_sdk_generator

        register_sdk_generator("py", generate_python_sdk)
