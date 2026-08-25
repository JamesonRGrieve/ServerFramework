# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_sdk_rs extension package — opt-in Rust client SDK generation."""

from zephyrex.extensions.meta_sdk_rs.EXT_MetaSDKRs import EXT_MetaSDKRs
from zephyrex.extensions.meta_sdk_rs.RustSDKEmitter import (
    SDK_RS_OUTPUT_DIR_ENV,
    generate_rust_sdk,
)

__all__ = [
    "EXT_MetaSDKRs",
    "SDK_RS_OUTPUT_DIR_ENV",
    "generate_rust_sdk",
]
