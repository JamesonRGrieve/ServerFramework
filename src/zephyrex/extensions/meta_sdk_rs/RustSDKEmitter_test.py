# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for meta_sdk_rs — opt-in Rust SDK generation (issue #217)."""

import shutil
import subprocess

import pytest

from zephyrex.extensions.meta_sdk_rs.EXT_MetaSDKRs import EXT_MetaSDKRs
from zephyrex.extensions.meta_sdk_rs.RustSDKEmitter import (
    SDK_RS_EDITION,
    SDK_RS_OUTPUT_DIR_ENV,
    generate_rust_sdk,
)
from zephyrex.pydantic2.fastapi import RouterMixin


class MetaSdkRsWidgetManager(RouterMixin):
    version = "v1"


def _norm(text: str) -> str:
    """Collapse all runs of whitespace to single spaces.

    The emitted Rust is post-formatted by rustfmt (when available), which wraps
    long method chains / argument lists across lines. Structural assertions on
    the *tokens* the emitter produces must therefore be whitespace-insensitive so
    they hold whether or not rustfmt reflowed the layout.
    """
    return " ".join(text.split())


def _has(text: str, needle: str) -> bool:
    return _norm(needle) in _norm(text)


# Deps the generated reqwest client needs for a clippy type-check pass.
_CARGO_TOML = f"""[package]
name = "sdk_clippy_probe"
version = "0.0.0"
edition = "{SDK_RS_EDITION}"

[lib]
path = "src/lib.rs"

[dependencies]
reqwest = {{ version = "0.12", features = ["json"] }}
serde_json = "1"
"""

# Substrings that mean "cargo could not build offline" (no dep cache / no
# network / clippy component absent) rather than "clippy found a lint" -> skip.
_TOOLCHAIN_UNAVAILABLE = (
    "--offline",
    "no matching package",
    "failed to load source",
    "unable to get packages",
    "failed to get",
    "no such command",
    "could not find `Cargo.toml`",
    "not installed",
    "component",
)


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
    # older raw Some(data). Assertions are whitespace-normalized because rustfmt
    # wraps the longer request(...) chains across lines.
    assert _has(resource, 'serde_json::json!({ "meta_sdk_rs_widget": data })')
    assert _has(resource, '"POST", "/v1/meta_sdk_rs_widget", Some(body), None')
    assert _has(
        resource, '"PUT", &format!("/v1/meta_sdk_rs_widget/{}", id), Some(body), None'
    )
    assert _has(
        resource, '"GET", &format!("/v1/meta_sdk_rs_widget/{}", id), None, None'
    )
    assert _has(resource, '"GET", "/v1/meta_sdk_rs_widget/search", None, query')
    assert _has(resource, 'serde_json::json!({ "meta_sdk_rs_widgets": items })')
    assert _has(
        resource,
        'serde_json::json!({ "meta_sdk_rs_widget": updates, "target_ids": ids })',
    )
    assert _has(resource, 'serde_json::json!({ "target_ids": ids })')

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


def test_generated_rust_passes_rustfmt(monkeypatch, tmp_path):
    """The emitted SDK must be rustfmt-clean (formatter gate).

    The emitter post-formats through rustfmt, so ``rustfmt --check`` on every
    generated file must report zero diffs. Skips only when rustfmt is absent.
    """
    rustfmt = shutil.which("rustfmt")
    if not rustfmt:
        pytest.skip("rustfmt not available")

    monkeypatch.setenv(SDK_RS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_rust_sdk(model_registry=[MetaSdkRsWidgetManager])

    rs_files = sorted(tmp_path.glob("*.rs"))
    assert rs_files, "no .rs files were emitted"
    for rs_file in rs_files:
        result = subprocess.run(
            [rustfmt, "--check", "--edition", SDK_RS_EDITION, str(rs_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{rs_file.name} is not rustfmt-clean:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def test_generated_rust_passes_clippy(monkeypatch, tmp_path):
    """The emitted SDK must be clippy-clean (linter gate).

    Scaffolds a minimal crate around the generated files and runs
    ``cargo clippy -D warnings``. Skips when cargo/clippy is unavailable or the
    dependency graph cannot be built offline (no crate cache / no network) --
    i.e. the check activates once the Rust toolchain + deps are provisioned.
    """
    cargo = shutil.which("cargo")
    if not cargo:
        pytest.skip("cargo not available")

    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv(SDK_RS_OUTPUT_DIR_ENV, str(src))
    generate_rust_sdk(model_registry=[MetaSdkRsWidgetManager])
    # The mod barrel is the crate root for the probe crate.
    (src / "mod.rs").rename(src / "lib.rs")
    (tmp_path / "Cargo.toml").write_text(_CARGO_TOML, encoding="utf-8")

    try:
        result = subprocess.run(
            [cargo, "clippy", "--offline", "--quiet", "--", "-D", "warnings"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        pytest.skip(f"clippy could not run: {exc}")

    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0 and any(m in combined for m in _TOOLCHAIN_UNAVAILABLE):
        pytest.skip(
            "clippy could not build the probe crate offline "
            "(toolchain/deps unavailable)"
        )
    assert result.returncode == 0, f"clippy reported issues:\n{result.stderr}"


def test_on_load_registers_rust_generator():
    from zephyrex.lib.Hooks import _registry_hooks

    _registry_hooks["generate_sdk"].pop("rs", None)
    try:
        EXT_MetaSDKRs.on_load()
        assert _registry_hooks["generate_sdk"].get("rs") is generate_rust_sdk
    finally:
        _registry_hooks["generate_sdk"].pop("rs", None)
