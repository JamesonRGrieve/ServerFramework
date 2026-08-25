# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_sdk_rs extension — opt-in generation of the typed Rust client SDK."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_MetaSDKRs(AbstractStaticExtension):
    """Generate a typed Rust client SDK for the installed extensions.

    Opt-in sibling of ``meta_sdk_py`` / ``meta_sdk_ts``. Enabling this extension
    registers a Rust generator on the core ``generate_sdk`` registry hook;
    ``ModelRegistry.commit`` drives it over the committed registry, rendering a
    shared ``client.rs`` (a ``reqwest`` transport) plus one resource-client
    struct per RouterMixin-tagged manager. It self-gates on ``SDK_RS_OUTPUT_DIR``
    so enabling it without a destination is a no-op. The REST surface is taken
    from the shared ``sdk.SDKModel`` IR, so the Rust, Python, and TypeScript
    SDKs cannot drift.
    """

    name: ClassVar[str] = "meta_sdk_rs"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Opt-in generation of a typed Rust client SDK from the " "committed registry"
    )

    _env: ClassVar[Dict[str, Any]] = {}
    _abilities: ClassVar[Set[str]] = {"sdk_generate_rust"}
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing meta_sdk_rs extension")
        return True

    @classmethod
    def on_load(cls) -> None:
        from zephyrex.extensions.meta_sdk_rs.RustSDKEmitter import generate_rust_sdk
        from zephyrex.lib.Hooks import register_sdk_generator

        register_sdk_generator("rs", generate_rust_sdk)
