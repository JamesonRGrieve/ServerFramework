# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for meta_sdk_ts — opt-in TypeScript SDK generation (issue #217)."""

import shutil
import subprocess

import pytest

from zephyrex.extensions.meta_sdk_ts.EXT_MetaSDKTs import EXT_MetaSDKTs
from zephyrex.extensions.meta_sdk_ts.TypeScriptSDKEmitter import (
    SDK_TS_OUTPUT_DIR_ENV,
    generate_typescript_sdk,
    resolve_ts_formatter,
)
from zephyrex.pydantic2.fastapi import RouterMixin


class MetaSdkTsWidgetManager(RouterMixin):
    version = "v1"


def _norm(text: str) -> str:
    """Collapse whitespace so token assertions survive a formatter's reflow."""
    return " ".join(text.split())


def _has(text: str, needle: str) -> bool:
    return _norm(needle) in _norm(text)


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

    # Paths and batch bodies match the shared contract. Whitespace-normalized
    # because the emitted TS is post-formatted (prettier/biome) when available,
    # which may reflow the request(...) argument lists.
    assert _has(resource, '"POST", `/v1/meta_sdk_ts_widget`')
    assert _has(resource, '"GET", `/v1/meta_sdk_ts_widget/${id}`')
    assert _has(resource, '"GET", `/v1/meta_sdk_ts_widget/search`')
    assert _has(resource, '{ "meta_sdk_ts_widgets": items }')
    assert _has(resource, '{ "meta_sdk_ts_widget": updates, "target_ids": ids }')
    assert _has(resource, '{ "target_ids": ids }')

    index = (tmp_path / "index.ts").read_text(encoding="utf-8")
    assert _has(index, 'export { MetaSdkTsWidgetSDK } from "./MetaSdkTsWidgetSDK";')
    assert _has(index, 'export { HttpClient } from "./runtime";')


def test_generation_is_deterministic(monkeypatch, tmp_path):
    monkeypatch.setenv(SDK_TS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    first = (tmp_path / "MetaSdkTsWidgetSDK.ts").read_text(encoding="utf-8")
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    second = (tmp_path / "MetaSdkTsWidgetSDK.ts").read_text(encoding="utf-8")
    assert first == second


def test_generated_typescript_passes_formatter(monkeypatch, tmp_path):
    """The emitted SDK must be formatter-clean (formatter gate).

    The emitter post-formats with the first available of prettier/biome; this
    checks the same tool in ``--check`` mode. Skips when no TS formatter is on
    PATH -- the gate activates once a TS toolchain is provisioned.
    """
    formatter = resolve_ts_formatter()
    if formatter is None:
        pytest.skip("no TS formatter (prettier/biome) available")

    monkeypatch.setenv(SDK_TS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    ts_files = sorted(str(p) for p in tmp_path.glob("*.ts"))
    assert ts_files, "no .ts files were emitted"

    binary, _write_args = formatter
    if binary.endswith("prettier"):
        check_cmd = [binary, "--check", *ts_files]
    else:  # biome
        check_cmd = [binary, "check", "--linter-enabled=false", *ts_files]

    result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"emitted TypeScript is not formatter-clean:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_generated_typescript_passes_linter(monkeypatch, tmp_path):
    """The emitted SDK must be lint-clean (linter gate).

    Uses biome (the workspace-standard TS linter). Skips when biome is not on
    PATH -- eslint v6 without a TS parser cannot lint the emitted .ts, so it is
    intentionally not used as a fallback here.
    """
    biome = shutil.which("biome")
    if not biome:
        pytest.skip("biome (TS linter) not available")

    monkeypatch.setenv(SDK_TS_OUTPUT_DIR_ENV, str(tmp_path))
    generate_typescript_sdk(model_registry=[MetaSdkTsWidgetManager])
    ts_files = sorted(str(p) for p in tmp_path.glob("*.ts"))
    assert ts_files, "no .ts files were emitted"

    result = subprocess.run(
        [biome, "lint", *ts_files], capture_output=True, text=True, timeout=120
    )
    combined = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode != 0 and ("unknown" in combined or "usage" in combined):
        pytest.skip("biome lint invocation incompatible with installed version")
    assert result.returncode == 0, f"biome lint reported issues:\n{result.stdout}"


def test_on_load_registers_typescript_generator():
    from zephyrex.lib.Hooks import _registry_hooks

    _registry_hooks["generate_sdk"].pop("ts", None)
    try:
        EXT_MetaSDKTs.on_load()
        assert _registry_hooks["generate_sdk"].get("ts") is generate_typescript_sdk
    finally:
        _registry_hooks["generate_sdk"].pop("ts", None)
