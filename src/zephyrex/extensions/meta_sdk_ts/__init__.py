# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_sdk_ts extension package — opt-in TypeScript client SDK generation."""

from zephyrex.extensions.meta_sdk_ts.EXT_MetaSDKTs import EXT_MetaSDKTs
from zephyrex.extensions.meta_sdk_ts.TypeScriptSDKEmitter import (
    SDK_TS_OUTPUT_DIR_ENV,
    generate_typescript_sdk,
)

__all__ = [
    "EXT_MetaSDKTs",
    "SDK_TS_OUTPUT_DIR_ENV",
    "generate_typescript_sdk",
]
