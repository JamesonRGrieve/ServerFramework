# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /source: every deployment offers its users the source it runs
(AGPL-3.0 section 13), defaulting to the canonical repository."""

from zephyrex import get_framework_version
from zephyrex.lib.Environment import env, refresh_settings
from zephyrex.lib.Provenance import CLEAN, DIRTY, ERROR, NONE

CANONICAL = "https://git.zephyrex.dev/ZephyrexTechnologies/ServerFramework"
GIT_STATUSES = (CLEAN, DIRTY, NONE, ERROR)


def test_anyone_can_read_where_the_source_is(server):
    response = server.get("/source")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == env("APP_REPOSITORY")
    assert body["version"] == get_framework_version()
    assert body["license"] == "AGPL-3.0-or-later"
    assert body["hash_status"] in ("verified", "modified", "unverified")
    assert body["git_status"] in GIT_STATUSES
    assert len(body["digest"]) == 64
    assert body["extensions"], "the loaded extensions are listed"
    assert all(
        e["bundled"] and e["source"] == body["source"] for e in body["extensions"]
    )


def test_every_response_says_where_its_source_is(server):
    for path in ("/health", "/v1/no-such-route"):
        headers = server.get(path).headers
        assert headers["source-link"] == env("APP_REPOSITORY")
        assert headers["source-hash-status"] in ("verified", "modified", "unverified")
        assert headers["source-git-status"] in GIT_STATUSES


def test_the_default_is_the_canonical_repository(monkeypatch):
    monkeypatch.delenv("APP_REPOSITORY", raising=False)
    refresh_settings()
    try:
        assert env("APP_REPOSITORY") == CANONICAL
    finally:
        monkeypatch.undo()
        refresh_settings()


def test_a_modified_deployment_offers_its_own_source(server, monkeypatch):
    fork = "https://git.example.org/acme/zephyrex-fork"
    monkeypatch.setenv("APP_REPOSITORY", fork)
    refresh_settings()
    try:
        assert server.get("/source").json()["source"] == fork
    finally:
        monkeypatch.undo()
        refresh_settings()
