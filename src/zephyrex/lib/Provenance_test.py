# SPDX-License-Identifier: AGPL-3.0-or-later
"""Source provenance: what is hashed, how the statuses are decided, and the
headers every response carries."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zephyrex.lib import Provenance
from zephyrex.lib.Provenance import (
    CLEAN,
    DIRTY,
    ERROR,
    MANIFEST_NAME,
    MODIFIED,
    NONE,
    UNVERIFIED,
    VERIFIED,
    SourceHeadersMiddleware,
    build_manifest,
    git_status,
    shipped_files,
    source_digest,
)

_GIT_IDENTITY = ["-c", "user.name=Provenance Test", "-c", "user.email=p@example.com"]


def _package(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").write_text("")
    (root / "core.py").write_text("VALUE = 1\n")
    (root / "data.json").write_text("{}\n")
    (root / "core_test.py").write_text("def test(): pass\n")
    (root / "conftest.py").write_text("")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "core.cpython-311.pyc").write_bytes(b"\0")
    return root


def _git(directory: Path, *arguments: str) -> None:
    """git on a throwaway repository, never the one running the tests (a
    pre-commit hook exports GIT_INDEX_FILE for the commit in progress)."""
    subprocess.run(
        ["git", *_GIT_IDENTITY, "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        env=Provenance._git_environment(),
    )


def test_git_never_answers_for_an_inherited_repository(tmp_path, monkeypatch):
    """Under a git hook, GIT_INDEX_FILE / GIT_DIR name the outer repository;
    following them answered for (and wrote into) the wrong one."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere.git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "elsewhere.index"))
    root = _package(tmp_path / "repo" / "pkg")
    _git(tmp_path / "repo", "init", "-q")
    _git(tmp_path / "repo", "add", ".")
    _git(tmp_path / "repo", "commit", "-q", "-m", "initial")

    assert git_status(root) == CLEAN
    assert not (tmp_path / "elsewhere.index").exists()


@pytest.fixture(autouse=True)
def _fresh_state():
    Provenance.source_state.cache_clear()
    yield
    Provenance.source_state.cache_clear()


def test_only_shipped_files_are_hashed(tmp_path):
    root = _package(tmp_path / "pkg")
    (root / MANIFEST_NAME).write_text("{}")
    names = [p.relative_to(root).as_posix() for p in shipped_files(root)]
    assert names == ["__init__.py", "core.py", "data.json"]


def test_the_digest_follows_content_and_names(tmp_path):
    root = _package(tmp_path / "pkg")
    original = source_digest(root)
    (root / "core_test.py").write_text("changed tests are not shipped\n")
    assert source_digest(root) == original
    (root / "core.py").write_text("VALUE = 2\n")
    changed = source_digest(root)
    assert changed != original
    (root / "core.py").rename(root / "renamed.py")
    assert source_digest(root) not in (original, changed)


def test_without_manifest_or_git_nothing_is_vouched_for(tmp_path):
    state = Provenance.source_state(_package(tmp_path / "pkg"))
    assert (state.hash_status, state.git_status, state.commit) == (
        UNVERIFIED,
        NONE,
        None,
    )


def test_an_installed_release_is_checked_against_its_manifest(tmp_path):
    """No .git: its git state is none; the manifest vouches for the content
    and names the commit it was built from."""
    root = _package(tmp_path / "pkg")
    manifest = {"digest": source_digest(root), "commit": "abc123", "git_status": CLEAN}
    (root / MANIFEST_NAME).write_text(json.dumps(manifest))

    state = Provenance.source_state(root)
    assert (state.hash_status, state.git_status, state.commit) == (
        VERIFIED,
        NONE,
        "abc123",
    )

    (root / "core.py").write_text("VALUE = 'altered'\n")
    Provenance.source_state.cache_clear()
    assert Provenance.source_state(root).hash_status == MODIFIED


def test_build_manifest_records_what_is_shipped(tmp_path):
    root = _package(tmp_path / "pkg")
    manifest = build_manifest(root, tmp_path)
    assert manifest == {
        "digest": source_digest(root),
        "commit": None,
        "git_status": NONE,
    }


def test_a_git_checkout_reports_its_working_tree(tmp_path):
    root = _package(tmp_path / "repo" / "pkg")
    _git(tmp_path / "repo", "init", "-q")
    _git(tmp_path / "repo", "add", ".")
    _git(tmp_path / "repo", "commit", "-q", "-m", "initial")
    assert git_status(root) == CLEAN
    assert Provenance.source_state(root).commit is not None

    (root / "core.py").write_text("VALUE = 3\n")
    assert git_status(root) == DIRTY


def test_a_package_ignored_by_an_enclosing_repo_is_not_its_checkout(tmp_path):
    """A virtualenv inside some project's repository: git says nothing about
    the gitignored package, and that project's commit is not its."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".gitignore").write_text(".venv/\n")
    _git(project, "init", "-q")
    _git(project, "add", ".")
    _git(project, "commit", "-q", "-m", "project")
    root = _package(project / ".venv" / "site-packages" / "pkg")

    state = Provenance.source_state(root)
    assert (state.git_status, state.commit) == (NONE, None)


def test_git_that_cannot_answer_is_an_error(tmp_path, monkeypatch):
    root = _package(tmp_path / "repo" / "pkg")
    _git(tmp_path / "repo", "init", "-q")
    _git(tmp_path / "repo", "add", ".")
    _git(tmp_path / "repo", "commit", "-q", "-m", "initial")
    empty_path = tmp_path / "no-tools"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))  # git is not installed

    state = Provenance.source_state(root)
    assert (state.git_status, state.commit) == (ERROR, None)


def test_every_response_carries_the_source_headers(tmp_path):
    root = _package(tmp_path / "pkg")
    links = iter(["https://example.org/first", "https://example.org/second"])
    app = FastAPI()
    app.add_middleware(SourceHeadersMiddleware, root=root, link=lambda: next(links))

    @app.get("/thing")
    def thing():
        return {}

    client = TestClient(app)
    first = client.get("/thing")
    assert first.headers["source-link"] == "https://example.org/first"
    assert first.headers["source-hash-status"] == UNVERIFIED
    assert first.headers["source-git-status"] == NONE
    # Read per response: a changed APP_REPOSITORY shows at once.
    assert client.get("/missing").headers["source-link"] == "https://example.org/second"


def test_a_consumer_extension_offers_its_own_source(tmp_path, monkeypatch):
    from zephyrex.lib.SourceOffer import source_offer

    framework = _package(tmp_path / "framework")
    extension_dir = tmp_path / "consumer" / "widgets"
    extension_dir.mkdir(parents=True)
    (extension_dir / "manifest.toml").write_text(
        'name = "widgets"\nversion = "1.0.0"\nentry_module = "EXT_Widgets"\n'
        'repository = "https://git.example.org/acme/widgets"\n'
    )
    module_path = extension_dir / "EXT_Widgets.py"
    module_path.write_text("class EXT_Widgets:\n    name = 'widgets'\n")
    spec = importlib.util.spec_from_file_location("ext_widgets_probe", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Loaded extensions are in sys.modules, where inspect finds their file.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    offer = source_offer(
        framework, "https://git.example.org/framework", "1.0", [module.EXT_Widgets]
    )
    (widgets,) = offer["extensions"]
    assert widgets["name"] == "widgets"
    assert widgets["bundled"] is False
    assert widgets["source"] == "https://git.example.org/acme/widgets"
    assert widgets["hash_status"] == UNVERIFIED
