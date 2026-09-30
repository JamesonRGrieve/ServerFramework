# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /v1/sdk and /v1/sdk/{language}/download: the generated client SDKs of
the loaded meta_sdk_* extensions, for any signed-in user.

The output directories are filled by hand here; generation itself is
covered by each emitter's own tests.
"""

import hashlib
import io
import zipfile
from pathlib import Path
from typing import Any, Dict

import pytest

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.meta_sdk_py.EXT_MetaSDKPy import EXT_MetaSDKPy
from zephyrex.extensions.meta_sdk_py.PythonSDKEmitter import SDK_PY_OUTPUT_DIR_ENV
from zephyrex.extensions.meta_sdk_ts.TypeScriptSDKEmitter import (
    SDK_TS_OUTPUT_DIR_ENV,
)

SDK = "/v1/sdk"


@pytest.fixture
def python_sdk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A generated Python SDK in the directory SDK_PY_OUTPUT_DIR names."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "UserSDK_generated.py").write_text("class UserSDK: ...\n")
    (tmp_path / "README.md").write_text("# client\n")
    monkeypatch.setenv(SDK_PY_OUTPUT_DIR_ENV, str(tmp_path))
    return tmp_path


class TestSDKDownload(ExtensionServerMixin):
    extension_class = EXT_MetaSDKPy

    def _headers(self, user: Any) -> Dict[str, str]:
        return {"Authorization": f"Bearer {user.jwt}"}

    def test_signing_in_is_required(self, server):
        assert server.get(SDK).status_code == 401
        assert server.get(f"{SDK}/python/download").status_code == 401

    def test_nothing_generated_lists_nothing(self, server, admin_a, monkeypatch):
        monkeypatch.delenv(SDK_PY_OUTPUT_DIR_ENV, raising=False)
        assert server.get(SDK, headers=self._headers(admin_a)).json() == {"sdks": []}
        download = server.get(f"{SDK}/python/download", headers=self._headers(admin_a))
        assert download.status_code == 404

    def test_a_generated_sdk_is_listed_and_downloads(self, server, admin_a, python_sdk):
        from zephyrex import get_framework_version

        (listed,) = server.get(SDK, headers=self._headers(admin_a)).json()["sdks"]
        download = server.get(f"{SDK}/python/download", headers=self._headers(admin_a))

        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == "application/zip"
        assert download.headers["content-disposition"] == (
            'attachment; filename="zephyrex-sdk-python.zip"'
        )
        assert listed == {
            "language": "python",
            "extension": "meta_sdk_py",
            "version": get_framework_version(),
            "filename": "zephyrex-sdk-python.zip",
            "size": len(download.content),
            "sha256": hashlib.sha256(download.content).hexdigest(),
        }
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert archive.namelist() == ["README.md", "pkg/UserSDK_generated.py"]
            assert archive.read("README.md") == b"# client\n"

    def test_the_archive_changes_only_with_the_sdk(self, server, admin_a, python_sdk):
        import os

        first = server.get(f"{SDK}/python/download", headers=self._headers(admin_a))
        os.utime(python_sdk / "README.md", (0, 0))
        second = server.get(f"{SDK}/python/download", headers=self._headers(admin_a))
        assert first.content == second.content

        (python_sdk / "README.md").write_text("# client v2\n")
        third = server.get(f"{SDK}/python/download", headers=self._headers(admin_a))
        assert third.content != first.content

    def test_a_symlink_never_pulls_in_outside_files(
        self, server, admin_a, python_sdk, tmp_path_factory
    ):
        outside = tmp_path_factory.mktemp("outside") / "server-secret.txt"
        outside.write_text("not part of the SDK")
        (python_sdk / "leak.txt").symlink_to(outside)

        download = server.get(f"{SDK}/python/download", headers=self._headers(admin_a))
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            assert "leak.txt" not in archive.namelist()
        assert b"not part of the SDK" not in download.content

    def test_a_language_whose_extension_is_not_loaded_is_404(
        self, server, admin_a, tmp_path, monkeypatch
    ):
        (tmp_path / "index.ts").write_text("export {}\n")
        monkeypatch.setenv(SDK_TS_OUTPUT_DIR_ENV, str(tmp_path))

        listed = server.get(SDK, headers=self._headers(admin_a)).json()["sdks"]
        assert "typescript" not in {sdk["language"] for sdk in listed}
        download = server.get(
            f"{SDK}/typescript/download", headers=self._headers(admin_a)
        )
        assert download.status_code == 404
        assert (
            server.get(
                f"{SDK}/cobol/download", headers=self._headers(admin_a)
            ).status_code
            == 404
        )
