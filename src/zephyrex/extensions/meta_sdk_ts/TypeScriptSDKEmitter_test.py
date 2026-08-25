# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for meta_sdk_ts — opt-in TypeScript SDK generation (issue #217)."""

from zephyrex.extensions.meta_sdk_ts.EXT_MetaSDKTs import EXT_MetaSDKTs
from zephyrex.extensions.meta_sdk_ts.TypeScriptSDKEmitter import (
    SDK_TS_OUTPUT_DIR_ENV,
    generate_typescript_sdk,
)
from zephyrex.lib.Pydantic2FastAPI import RouterMixin


class MetaSdkTsWidgetManager(RouterMixin):
    version = "v1"


def test_generation_is_noop_without_output_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(SDK_TS_OUTPUT_DIR_ENV, raising=False)
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    assert list(tmp_path.iterdir()) == []


def test_generation_emits_runtime_index_and_resource(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_TS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])

    names = {p.name for p in tmp_path.iterdir()}
    assert "runtime.ts" in names
    assert "index.ts" in names
    assert "MetaSdkTsWidgetSDK.ts" in names

    resource = (tmp_path / "MetaSdkTsWidgetSDK.ts").read_text(encoding="utf-8")
    # Class + every operation method the REST contract defines.
    assert "export class MetaSdkTsWidgetSDK" in resource
    for method in (
        "create(",
        "get(",
        "list(",
        "update(",
        "delete(",
        "search(",
        "batchCreate(",
        "batchUpdate(",
        "batchDelete(",
    ):
        assert method in resource, method

    # Paths and batch bodies match the shared contract byte-for-byte.
    assert '"POST", `/v1/meta_sdk_ts_widget`' in resource
    assert '"GET", `/v1/meta_sdk_ts_widget/${id}`' in resource
    assert '"GET", `/v1/meta_sdk_ts_widget/search`' in resource
    assert '{ "meta_sdk_ts_widgets": items }' in resource
    assert '{ "meta_sdk_ts_widget": updates, "target_ids": ids }' in resource
    assert '{ "target_ids": ids }' in resource

    index = (tmp_path / "index.ts").read_text(encoding="utf-8")
    assert 'export { MetaSdkTsWidgetSDK } from "./MetaSdkTsWidgetSDK";' in index
    assert 'export { HttpClient } from "./runtime";' in index


def test_generation_is_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_TS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    first = (tmp_path / "MetaSdkTsWidgetSDK.ts").read_text(encoding="utf-8")
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    second = (tmp_path / "MetaSdkTsWidgetSDK.ts").read_text(encoding="utf-8")
    assert first == second


def test_on_load_registers_typescript_generator():
    from zephyrex.lib.Hooks import _registry_hooks

    _registry_hooks["generate_sdk"].pop("ts", None)
    try:
        EXT_MetaSDKTs.on_load()
        assert _registry_hooks["generate_sdk"].get("ts") is generate_typescript_sdk
    finally:
        _registry_hooks["generate_sdk"].pop("ts", None)
