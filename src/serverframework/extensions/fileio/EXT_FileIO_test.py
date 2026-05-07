"""Tests for the fileio extension.

Covers the path-containment fix in PRV_FileIO and the canonical EXT shape.
"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "x" * 32)
os.environ.setdefault("PYTEST_CURRENT_TEST", "fileio_test")

import pytest

from serverframework.extensions.fileio.EXT_FileIO import EXT_FileIO


class TestExtensionMetadata:
    def test_name(self):
        assert EXT_FileIO.name == "fileio"

    def test_abilities(self):
        abilities = EXT_FileIO.get_abilities()
        for expected in (
            "fileio_read",
            "fileio_write",
            "fileio_list",
            "fileio_delete",
        ):
            assert expected in abilities


class TestPathContainment:
    """Verify the Path.relative_to fix rejects sibling-prefix attacks."""

    def setup_method(self):
        # Create a base + sibling structure on a real filesystem so the
        # check exercises both the resolve() and relative_to() paths.
        self._tmp = tempfile.mkdtemp()
        self.base = Path(self._tmp) / "data"
        self.base.mkdir()
        self.sibling = Path(self._tmp) / "datacopy"
        self.sibling.mkdir()
        (self.base / "ok.txt").write_text("ok")
        (self.sibling / "leak.txt").write_text("leak")

    def teardown_method(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _provider(self):
        from serverframework.extensions.fileio.PRV_FileIO import (
            AbstractFileIOProvider,
            FileIOPermission,
        )

        # Build a minimal concrete subclass (the abstract methods aren't
        # exercised by the containment test).
        class _Concrete(AbstractFileIOProvider):
            async def get_filesystem_info(self):
                return {}

        provider = _Concrete.__new__(_Concrete)
        provider.base_directory = self.base.resolve()
        provider.allowed_permissions = {FileIOPermission.READ}
        provider.allowlist_patterns = ["*"]
        provider.blocklist_patterns = []
        return provider

    def test_in_bounds_path_allowed(self):
        provider = self._provider()
        resolved, allowed = provider._check_path_permissions(
            str(self.base / "ok.txt"), check_exists=True
        )
        assert allowed is True

    def test_sibling_prefix_rejected(self):
        provider = self._provider()
        # /tmp/.../datacopy/leak.txt shares a prefix with /tmp/.../data
        # under the old startswith check; the new is_relative_to check
        # rejects it.
        _, allowed = provider._check_path_permissions(
            str(self.sibling / "leak.txt"), check_exists=True
        )
        assert allowed is False
