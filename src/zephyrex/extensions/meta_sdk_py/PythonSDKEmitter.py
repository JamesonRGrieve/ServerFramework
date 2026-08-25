# SPDX-License-Identifier: AGPL-3.0-or-later
"""Python SDK emitter — drives the introspection generator, opt-in by env.

The Python SDK already has a mature emitter (``sdk.SDKGenerator``); this module
is the thin opt-in wiring that runs it over a committed registry when — and only
when — a destination is configured. TypeScript and Rust have no such existing
emitter, so ``meta_sdk_ts`` / ``meta_sdk_rs`` render from ``sdk.SDKModel``
directly; all three share the same resource discovery and REST contract.
"""

import os
from pathlib import Path
from typing import Any

from zephyrex.lib.Logging import logger

# Destination for the generated Python SDK. Unset -> generation is a no-op, so a
# normal boot with meta_sdk_py enabled writes nothing; a build/CI step sets it.
SDK_PY_OUTPUT_DIR_ENV = "SDK_PY_OUTPUT_DIR"


def generate_python_sdk(*, model_registry: Any) -> None:
    """Emit ``<Resource>SDK_generated.py`` for every manager in the registry.

    Self-gates on ``SDK_PY_OUTPUT_DIR``; returns immediately when unset.
    """
    output = os.getenv(SDK_PY_OUTPUT_DIR_ENV)
    if not output:
        return

    from zephyrex.sdk.SDKGenerator import generate_sdk_handlers

    written = generate_sdk_handlers(model_registry, Path(output))
    logger.info(
        f"meta_sdk_py: generated {len(written)} Python SDK handler(s) into {output}"
    )
