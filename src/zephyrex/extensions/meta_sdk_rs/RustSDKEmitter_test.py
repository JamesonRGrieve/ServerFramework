# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for meta_sdk_rs — opt-in Rust SDK generation (issue #217)."""

from zephyrex.extensions.meta_sdk_rs.EXT_MetaSDKRs import EXT_MetaSDKRs
from zephyrex.extensions.meta_sdk_rs.RustSDKEmitter import (
    SDK_RS_OUTPUT_DIR_ENV,
    generate_rust_sdk,
)
from zephyrex.pydantic2.fastapi import RouterMixin


class MetaSdkRsWidgetManager(RouterMixin):
    version = "v1"


def test_generation_is_noop_without_output_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(SDK_RS_OUTPUT_DIR_ENV, raising=False)
    generate_rust_sdk(model_registry=[MetaSdkRsWidgetManager])
    assert list(tmp_path.iterdir()) == []


def test_generation_emits_client_mod_and_resource(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_RS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_rust_sdk(model_registry=[MetaSdkRsWidgetManager])

    names = {p.name for p in tmp_path.iterdir()}
    assert "client.rs" in names
    assert "mod.rs" in names
    assert "meta_sdk_rs_widget.rs" in names

    resource = (tmp_path / "meta_sdk_rs_widget.rs").read_text(encoding="utf-8")
    assert "pub struct MetaSdkRsWidgetSdk<'a>" in resource
    for method in (
        "pub async fn create(",
        "pub async fn get(",
        "pub async fn list(",
        "pub async fn update(",
        "pub async fn delete(",
        "pub async fn search(",
        "pub async fn batch_create(",
        "pub async fn batch_update(",
        "pub async fn batch_delete(",
    ):
        assert method in resource, method

    # Paths and bodies match the shared SDK contract. create/update wrap the
    # payload under the resource key ({resource: data}) to match the handler
    # (aligned in #231), so both emit Some(body) off a json! prelude -- not the
    # older raw Some(data).
    assert 'serde_json::json!({ "meta_sdk_rs_widget": data })' in resource
    assert '"POST", "/v1/meta_sdk_rs_widget", Some(body), None' in resource
    assert (
        '"PUT", &format!("/v1/meta_sdk_rs_widget/{}", id), Some(body), None' in resource
    )
    assert '"GET", &format!("/v1/meta_sdk_rs_widget/{}", id), None, None' in resource
    assert '"GET", "/v1/meta_sdk_rs_widget/search", None, query' in resource
    assert 'serde_json::json!({ "meta_sdk_rs_widgets": items })' in resource
    assert (
        'serde_json::json!({ "meta_sdk_rs_widget": updates, "target_ids": ids })'
        in resource
    )
    assert 'serde_json::json!({ "target_ids": ids })' in resource

    mod = (tmp_path / "mod.rs").read_text(encoding="utf-8")
    assert "pub mod meta_sdk_rs_widget;" in mod
    assert "pub use meta_sdk_rs_widget::MetaSdkRsWidgetSdk;" in mod
    assert "pub use client::{HttpClient, SdkError};" in mod


def test_generation_is_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_RS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_rust_sdk(model_registry=[MetaSdkRsWidgetManager])
    first = (tmp_path / "meta_sdk_rs_widget.rs").read_text(encoding="utf-8")
    generate_rust_sdk(model_registry=[MetaSdkRsWidgetManager])
    second = (tmp_path / "meta_sdk_rs_widget.rs").read_text(encoding="utf-8")
    assert first == second


def test_on_load_registers_rust_generator():
    from zephyrex.lib.Hooks import _registry_hooks

    _registry_hooks["generate_sdk"].pop("rs", None)
    try:
        EXT_MetaSDKRs.on_load()
        assert _registry_hooks["generate_sdk"].get("rs") is generate_rust_sdk
    finally:
        _registry_hooks["generate_sdk"].pop("rs", None)
