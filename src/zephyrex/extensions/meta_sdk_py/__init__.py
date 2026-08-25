# SPDX-License-Identifier: AGPL-3.0-or-later
"""meta_sdk_py extension package — opt-in Python client SDK generation."""

from zephyrex.extensions.meta_sdk_py.EXT_MetaSDKPy import EXT_MetaSDKPy
from zephyrex.extensions.meta_sdk_py.PythonSDKEmitter import (
    SDK_PY_OUTPUT_DIR_ENV,
    generate_python_sdk,
)

__all__ = [
    "EXT_MetaSDKPy",
    "SDK_PY_OUTPUT_DIR_ENV",
    "generate_python_sdk",
]
