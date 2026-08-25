# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for meta_sdk_py — opt-in Python SDK generation (issue #217).

Uses real ``RouterMixin``-tagged classes and a real temp directory — no mocks.
"""

from zephyrex.extensions.meta_sdk_py.EXT_MetaSDKPy import EXT_MetaSDKPy
from zephyrex.extensions.meta_sdk_py.PythonSDKEmitter import (
    SDK_PY_OUTPUT_DIR_ENV,
    generate_python_sdk,
)
from zephyrex.pydantic2.fastapi import RouterMixin


class MetaSdkPyFooManager(RouterMixin):
    version = "v1"


class MetaSdkPyBarManager(RouterMixin):
    version = "v1"


def test_generation_is_noop_without_output_dir(monkeypatch, tmp_path):
    """meta_sdk_py enabled but no destination configured writes nothing."""
    monkeypatch.delenv(SDK_PY_OUTPUT_DIR_ENV, raising=False)
    generate_python_sdk(model_registry=[MetaSdkPyFooManager, MetaSdkPyBarManager])
    assert list(tmp_path.iterdir()) == []


def test_generation_emits_handler_per_manager(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_PY_OUTPUT_DIR_ENV, str(tmp_path))
    generate_python_sdk(model_registry=[MetaSdkPyFooManager, MetaSdkPyBarManager])
    generated = sorted(p.name for p in tmp_path.glob("*SDK_generated.py"))
    assert "MetaSdkPyFooSDK_generated.py" in generated
    assert "MetaSdkPyBarSDK_generated.py" in generated


def test_generation_is_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_PY_OUTPUT_DIR_ENV, str(tmp_path))
    generate_python_sdk(model_registry=[MetaSdkPyFooManager])
    first = (tmp_path / "MetaSdkPyFooSDK_generated.py").read_text(encoding="utf-8")
    generate_python_sdk(model_registry=[MetaSdkPyFooManager])
    second = (tmp_path / "MetaSdkPyFooSDK_generated.py").read_text(encoding="utf-8")
    assert first == second


def test_on_load_registers_python_generator():
    """on_load wires the generator into the language-keyed SDK hook registry."""
    from zephyrex.lib.Hooks import _registry_hooks

    _registry_hooks["generate_sdk"].pop("py", None)
    try:
        EXT_MetaSDKPy.on_load()
        assert _registry_hooks["generate_sdk"].get("py") is generate_python_sdk
    finally:
        _registry_hooks["generate_sdk"].pop("py", None)
