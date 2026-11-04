# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bootstrap resolves the checkout root, where ``pyproject.toml`` lives,
and puts only the directory holding the package on ``sys.path``."""

import sys
from pathlib import Path

import pytest

from zephyrex import bootstrap


def test_the_root_is_the_checkout_holding_pyproject() -> None:
    """The root was resolved as ``src/``, so the bootstrap looked for
    ``src/zephyrex/pyproject.toml``, found nothing, and installed nothing."""
    package_dir = Path(__file__).resolve().parent  # this test sits in the package

    assert bootstrap._SRC_DIR == package_dir.parent
    assert bootstrap._ROOT_DIR == package_dir.parent.parent
    assert (bootstrap._ROOT_DIR / "pyproject.toml").is_file()


def test_setup_python_path_does_not_expose_the_package_internals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the package directory on ``sys.path`` a top-level ``extensions``
    resolved to the framework's own, shadowing a consumer's."""
    monkeypatch.setattr(sys, "path", [])

    bootstrap.setup_python_path()

    assert sys.path == [str(bootstrap._SRC_DIR)]
