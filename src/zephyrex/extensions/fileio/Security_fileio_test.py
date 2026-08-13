# SPDX-License-Identifier: AGPL-3.0-or-later
"""FileIO security tests — path containment validation (corpus §10, §36).

Uses a real LocalFileSystem instance against a tmp_path base directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zephyrex.extensions.fileio.Local import LocalFileSystem
from zephyrex.extensions.fileio.PRV_FileIO import FileIOPermission


@pytest.fixture
def fs(tmp_path):
    (tmp_path / "test.txt").write_text("safe content")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("nested")
    return LocalFileSystem(
        base_directory=str(tmp_path),
        allowed_permissions={FileIOPermission.READ, FileIOPermission.WRITE, FileIOPermission.DELETE, FileIOPermission.CREATE, FileIOPermission.LIST},
        allowlist_patterns=[str(tmp_path / "*")],
    )


@pytest.mark.security
class TestFileIOPathTraversal:
    @pytest.mark.parametrize(
        "path",
        [
            "../../../etc/passwd",
            "/etc/passwd",
            "subfolder/../../../etc/passwd",
            "subdir/../../etc/passwd",
        ],
    )
    async def test_read_traversal_rejected(self, fs, path):
        result = await fs.read_file(path)
        assert "root:" not in result
        assert "denied" in result.lower() or "not a file" in result.lower() or "failed" in result.lower()

    @pytest.mark.parametrize(
        "path",
        [
            "../../../tmp/evil.txt",
            "/tmp/evil.txt",
        ],
    )
    async def test_write_traversal_rejected(self, fs, path):
        result = await fs.write_file(path, "evil")
        assert "denied" in result.lower() or "error" in result.lower() or "failed" in result.lower()

    async def test_read_inside_base_allowed(self, fs):
        result = await fs.read_file("test.txt")
        assert result == "safe content"

    async def test_symlink_outside_base_rejected(self, fs, tmp_path):
        link = tmp_path / "evil_link"
        try:
            link.symlink_to(Path("/etc/hostname"))
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")
        result = await fs.read_file("evil_link")
        assert "denied" in result.lower() or "error" in result.lower() or "failed" in result.lower()

    async def test_list_traversal_rejected(self, fs):
        result = await fs.list_directory("../../")
        if isinstance(result, str):
            assert "denied" in result.lower() or "error" in result.lower()


@pytest.mark.security
class TestFileIOBlocklist:
    async def test_blocklist_rejects_pattern(self, tmp_path):
        (tmp_path / "secret.key").write_text("private key data")
        provider = LocalFileSystem(
            base_directory=str(tmp_path),
            allowed_permissions={FileIOPermission.READ},
            allowlist_patterns=[str(tmp_path / "*")],
            blocklist_patterns=[str(tmp_path / "*.key")],
        )
        result = await provider.read_file("secret.key")
        assert "private key data" not in result

    async def test_allowlist_rejects_unlisted(self, tmp_path):
        (tmp_path / "unlisted.dat").write_text("secret data")
        provider = LocalFileSystem(
            base_directory=str(tmp_path),
            allowed_permissions={FileIOPermission.READ},
            allowlist_patterns=[str(tmp_path / "*.txt")],
        )
        result = await provider.read_file("unlisted.dat")
        assert "secret data" not in result
