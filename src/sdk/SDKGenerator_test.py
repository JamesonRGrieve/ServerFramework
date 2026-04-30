"""Tests for ``sdk.SDKGenerator`` (Item 25).

Covers the deterministic, overwrite-safe contract: byte-identical regeneration,
``--check`` drift reporting, and importability of generated files.

Tests use real ``RouterMixin``-tagged classes — no mocks required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.Pydantic2FastAPI import RouterMixin
from sdk.SDKGenerator import (
    generate_sdk_handler_for,
    generate_sdk_handlers,
)


class GenTestFooManager(RouterMixin):
    """Stand-in manager class used purely to drive the generator."""

    prefix = "/v1/foo"


class GenTestBarWidgetManager(RouterMixin):
    """Exercises snake_case + plural derivation and version fallback."""

    prefix = None  # exercise the version-fallback path


def _classes() -> list:
    return [GenTestFooManager, GenTestBarWidgetManager]


def test_generate_sdk_handler_for_returns_compileable_source():
    src = generate_sdk_handler_for(GenTestFooManager)
    assert "class GenTestFooSDK(AbstractSDKHandler)" in src
    assert 'endpoint="/v1/foo"' in src
    assert 'name="gen_test_foo"' in src
    assert 'name_plural="gen_test_foos"' in src
    for op in (
        "def list(",
        "def get(",
        "def create(",
        "def update(",
        "def delete(",
        "def batch_create(",
        "def batch_update(",
        "def batch_delete(",
        "def search(",
    ):
        assert op in src, f"missing operation: {op}"
    assert "fields: Optional[List[str]] = None" in src
    assert "next_token: Optional[str] = None" in src
    compile(src, "<generated GenTestFooSDK>", "exec")


def test_generate_sdk_handler_for_rejects_non_router_mixin():
    class Plain:
        prefix = "/v1/plain"

    with pytest.raises(TypeError):
        generate_sdk_handler_for(Plain)


def test_generate_sdk_handler_for_uses_version_fallback_when_no_prefix():
    src = generate_sdk_handler_for(GenTestBarWidgetManager)
    assert "class GenTestBarWidgetSDK(AbstractSDKHandler)" in src
    assert 'name="gen_test_bar_widget"' in src
    assert 'name_plural="gen_test_bar_widgets"' in src
    assert 'endpoint="/v1/gen_test_bar_widget"' in src


def test_generate_sdk_handlers_writes_one_file_per_manager(tmp_path: Path):
    written = generate_sdk_handlers(_classes(), tmp_path)

    assert len(written) == 2
    names = sorted(p.name for p in written)
    assert names == [
        "GenTestBarWidgetSDK_generated.py",
        "GenTestFooSDK_generated.py",
    ]
    for path in written:
        assert path.exists()
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_generate_sdk_handlers_is_byte_identical_across_runs(tmp_path: Path):
    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    paths_a = generate_sdk_handlers(_classes(), out_a)
    paths_b = generate_sdk_handlers(_classes(), out_b)

    assert [p.name for p in paths_a] == [p.name for p in paths_b]
    for pa, pb in zip(paths_a, paths_b):
        assert pa.read_bytes() == pb.read_bytes(), (
            f"non-deterministic output between {pa} and {pb}"
        )

    snapshot = {p.name: p.read_bytes() for p in paths_a}
    generate_sdk_handlers(_classes(), out_a)
    after = {p.name: p.read_bytes() for p in out_a.glob("*SDK_generated.py")}
    assert snapshot == after


def test_generate_sdk_handlers_check_only_clean_when_files_match(tmp_path: Path):
    generate_sdk_handlers(_classes(), tmp_path)
    drifts = generate_sdk_handlers(_classes(), tmp_path, check_only=True)
    assert drifts == []


def test_generate_sdk_handlers_check_only_reports_modified(tmp_path: Path):
    generate_sdk_handlers(_classes(), tmp_path)

    target = tmp_path / "GenTestFooSDK_generated.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# manual edit\n",
        encoding="utf-8",
    )

    drifts = generate_sdk_handlers(_classes(), tmp_path, check_only=True)
    assert len(drifts) == 1
    drift_path, drift_kind = drifts[0]
    assert drift_path == target
    assert drift_kind == "modified"


def test_generate_sdk_handlers_check_only_reports_missing(tmp_path: Path):
    drifts = generate_sdk_handlers(_classes(), tmp_path, check_only=True)
    kinds = {p.name: kind for p, kind in drifts}
    assert kinds == {
        "GenTestFooSDK_generated.py": "missing",
        "GenTestBarWidgetSDK_generated.py": "missing",
    }


def test_generate_sdk_handlers_check_only_does_not_write(tmp_path: Path):
    generate_sdk_handlers(_classes(), tmp_path, check_only=True)
    assert list(tmp_path.iterdir()) == []


def test_generate_sdk_handlers_filters_non_router_mixin_classes(tmp_path: Path):
    class NotARouterManager:
        prefix = "/v1/nope"

    written = generate_sdk_handlers(
        [GenTestFooManager, NotARouterManager], tmp_path
    )
    names = sorted(p.name for p in written)
    assert names == ["GenTestFooSDK_generated.py"]


def test_generate_sdk_handlers_accepts_registry_with_managers_attribute(
    tmp_path: Path,
):
    class FakeRegistry:
        managers = (GenTestFooManager, GenTestBarWidgetManager)

    written = generate_sdk_handlers(FakeRegistry(), tmp_path)
    assert len(written) == 2
