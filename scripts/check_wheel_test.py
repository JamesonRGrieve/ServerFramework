# SPDX-License-Identifier: AGPL-3.0-or-later
import importlib.util
import json
import zipfile
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_wheel", Path(__file__).with_name("check_wheel.py")
)
assert _SPEC and _SPEC.loader
check_wheel = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_wheel)


def _wheel(tmp_path: Path, files: dict) -> Path:
    path = tmp_path / "zephyrex-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _manifest(commit: str, git_status: str) -> str:
    return json.dumps({"digest": "d", "commit": commit, "git_status": git_status})


def test_a_clean_release_of_this_commit_passes(tmp_path):
    wheel = _wheel(
        tmp_path,
        {"zephyrex/app.py": "", check_wheel.MANIFEST: _manifest("abc", "clean")},
    )
    assert check_wheel.problems(wheel, "abc") == []


def test_each_failure_is_named(tmp_path):
    wheel = _wheel(
        tmp_path,
        {
            "zephyrex/app_test.py": "",
            check_wheel.MANIFEST: _manifest("other", "dirty"),
        },
    )
    found = check_wheel.problems(wheel, "abc")
    assert len(found) == 3
    assert found[0].startswith("ships 1 test modules")
    assert "manifest commit other is not abc" in found
    assert "built from a dirty tree" in found


def test_a_wheel_without_a_manifest_is_refused(tmp_path):
    wheel = _wheel(tmp_path, {"zephyrex/app.py": ""})
    assert check_wheel.problems(wheel, "abc") == [f"has no {check_wheel.MANIFEST}"]
