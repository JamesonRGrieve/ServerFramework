# SPDX-License-Identifier: AGPL-3.0-or-later
"""Model declarations are checked before anything is fetched (allowed
sources, pinned commits, plain relative paths with digests); downloads
from a real local server are kept only when they match their SHA-256,
follow redirects through the SSRF guard and type their failures; and
models load once, are evicted least recently used, and unload."""

import hashlib
import json
import threading
from pathlib import Path

import pytest

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.local_ai.LocalModels import (
    LoadedModels,
    allowed_sources,
    download_verified,
    holds,
    model_spec,
    models_root,
)

REVISION = "0123456789abcdef0123456789abcdef01234567"
BODY = b"weights" * 1000
DIGEST = hashlib.sha256(BODY).hexdigest()


def spec(paths=None, source=None, repo="owner/model", revision=REVISION):
    return model_spec(
        source,
        repo,
        revision,
        json.dumps(paths or {"model.gguf": DIGEST}),
        "test",
    )


class TestDeclarations:
    def test_a_declared_model(self):
        found = spec({"config.json": DIGEST.upper(), "1_Pooling/config.json": DIGEST})
        assert found.source == "https://huggingface.co"
        assert [f.path for f in found.files] == ["1_Pooling/config.json", "config.json"]
        assert found.files[1].sha256 == DIGEST
        assert found.url(found.files[1]) == (
            f"https://huggingface.co/owner/model/resolve/{REVISION}/config.json"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "a/../../b",
            ".git/config",
            "a//b",
            "",
            "dir\\..\\x",
            "a/" * 9 + "b",
        ],
    )
    def test_a_path_that_could_leave_the_model_is_refused(self, path):
        with pytest.raises(PermanentExternalError, match="plain relative"):
            spec({path: DIGEST})

    @pytest.mark.parametrize(
        "repo", ["model", "../owner/model", "owner/model/extra", "owner/..", None]
    )
    def test_a_repo_is_owner_and_name(self, repo):
        with pytest.raises(PermanentExternalError, match="repo"):
            spec(repo=repo)

    @pytest.mark.parametrize("revision", ["main", "0123abc", None, "G" * 40])
    def test_files_are_pinned_to_a_commit(self, revision):
        with pytest.raises(PermanentExternalError, match="revision"):
            spec(revision=revision)

    @pytest.mark.parametrize(
        "declared", ["", "[]", "{}", '{"model.gguf": "abc"}', '{"model.gguf": 1}']
    )
    def test_every_file_has_a_digest(self, declared):
        with pytest.raises(PermanentExternalError):
            model_spec(None, "owner/model", REVISION, declared, "test")

    def test_an_instance_declaring_nothing_is_not_configured(self):
        with pytest.raises(TransientExternalError, match="not configured"):
            model_spec(None, "local_ai_chat", None, None, "test")

    def test_only_allowed_sources(self, set_env):
        with pytest.raises(PermanentExternalError, match="LOCAL_AI_MODEL_SOURCES"):
            spec(source="https://models.example.com")
        set_env("LOCAL_AI_MODEL_SOURCES", "https://models.example.com/, https://b.test")
        assert allowed_sources() == ["https://models.example.com", "https://b.test"]
        assert spec(source="https://models.example.com/").source == (
            "https://models.example.com"
        )
        with pytest.raises(PermanentExternalError):
            spec(source="https://huggingface.co")

    def test_the_models_directory(self, set_env, tmp_path):
        set_env("LOCAL_AI_MODELS_DIR", str(tmp_path))
        assert models_root() == tmp_path
        found = spec({"sub/model.gguf": DIGEST})
        assert found.local_path(tmp_path, found.files[0], "test") == (
            tmp_path / "owner--model" / REVISION / "sub" / "model.gguf"
        )

    def test_a_link_out_of_the_models_directory_is_refused(self, tmp_path):
        root, outside = tmp_path / "models", tmp_path / "elsewhere"
        outside.mkdir()
        (root / "owner--model").mkdir(parents=True)
        (root / "owner--model" / REVISION).symlink_to(outside)
        found = spec()
        with pytest.raises(PermanentExternalError, match="leaves"):
            found.local_path(root, found.files[0], "test")


class TestDownloads:
    async def test_a_matching_file_is_kept(self, local_http_server, tmp_path):
        server = local_http_server({"/m": (200, {}, BODY)})
        target = tmp_path / "a" / "model.gguf"
        await download_verified(f"{server.base_url}/m", target, DIGEST, provider="t")
        assert target.read_bytes() == BODY
        assert holds(target, DIGEST) and not holds(target, "0" * 64)
        assert list(target.parent.iterdir()) == [target]

    async def test_a_file_that_does_not_match_is_discarded(
        self, local_http_server, tmp_path
    ):
        server = local_http_server({"/m": (200, {}, b"tampered" + BODY)})
        target = tmp_path / "model.gguf"
        with pytest.raises(PermanentExternalError, match="SHA-256"):
            await download_verified(
                f"{server.base_url}/m", target, DIGEST, provider="t"
            )
        assert list(tmp_path.iterdir()) == []

    async def test_a_kept_file_survives_a_bad_download(
        self, local_http_server, tmp_path
    ):
        server = local_http_server({"/m": (200, {}, b"tampered")})
        target = tmp_path / "model.gguf"
        target.write_bytes(BODY)
        with pytest.raises(PermanentExternalError):
            await download_verified(
                f"{server.base_url}/m", target, DIGEST, provider="t"
            )
        assert target.read_bytes() == BODY

    async def test_redirects_are_followed_without_the_token(
        self, local_http_server, tmp_path
    ):
        server = local_http_server(
            {"/m": (302, {"Location": "/cdn/m"}, b""), "/cdn/m": (200, {}, BODY)}
        )
        target = tmp_path / "model.gguf"
        await download_verified(
            f"{server.base_url}/m", target, DIGEST, provider="t", token="hf_secret"
        )
        assert target.read_bytes() == BODY
        first, second = server.requests
        assert first.headers["authorization"] == "Bearer hf_secret"
        assert "authorization" not in second.headers

    async def test_the_ssrf_guard_applies(self, local_http_server, tmp_path):
        server = local_http_server({"/m": (200, {}, BODY)}, allow=False)
        with pytest.raises(PermanentExternalError, match="SSRF"):
            await download_verified(
                f"{server.base_url}/m", tmp_path / "x", DIGEST, provider="t"
            )
        assert server.requests == []

    async def test_a_size_cap(self, local_http_server, tmp_path):
        server = local_http_server({"/m": (200, {}, BODY)})
        with pytest.raises(PermanentExternalError, match="larger"):
            await download_verified(
                f"{server.base_url}/m",
                tmp_path / "x",
                DIGEST,
                provider="t",
                max_bytes=100,
            )
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        "status, error",
        [
            (404, PermanentExternalError),
            (401, AuthExternalError),
            (503, TransientExternalError),
            (429, TransientExternalError),
        ],
    )
    async def test_failures_are_typed(self, local_http_server, tmp_path, status, error):
        server = local_http_server({"/m": (status, {}, b"no")})
        with pytest.raises(error):
            await download_verified(
                f"{server.base_url}/m", tmp_path / "x", DIGEST, provider="t"
            )

    async def test_an_unreachable_source_is_transient(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", "127.0.0.1:1")
        with pytest.raises(TransientExternalError):
            await download_verified(
                "http://127.0.0.1:1/m", tmp_path / "x", DIGEST, provider="t"
            )


class Closer:
    def __init__(self):
        self.closed = []

    def __call__(self, value):
        self.closed.append(value)


class TestLoadedModels:
    def test_a_model_loads_once_however_many_ask(self):
        models, loads, closer = LoadedModels(), [], Closer()
        gate = threading.Event()

        def load():
            gate.wait(5)
            loads.append(1)
            return object()

        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(models.load("a", "A", "p", load, closer))
            )
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        gate.set()
        for thread in threads:
            thread.join(10)
        assert loads == [1]
        assert len({id(r.value) for r in results}) == 1

    def test_the_least_recently_used_idle_model_makes_room(self, set_env):
        set_env("LOCAL_AI_MAX_LOADED_MODELS", "2")
        models, closer = LoadedModels(), Closer()
        a = models.load("a", "A", "p", lambda: "model-a", closer)
        models.load("b", "B", "p", lambda: "model-b", closer)
        models.touch(a)
        models.load("c", "C", "p", lambda: "model-c", closer)
        assert closer.closed == ["model-b"]
        assert [s["id"] for s in models.summaries()] == ["a", "c"]
        assert models.get("a").uses == 1

    def test_a_model_in_use_is_not_evicted(self, set_env):
        set_env("LOCAL_AI_MAX_LOADED_MODELS", "1")
        models, closer = LoadedModels(), Closer()
        busy = models.load("a", "A", "p", lambda: "model-a", closer)
        with busy.lock:
            models.load("b", "B", "p", lambda: "model-b", closer)
        assert closer.closed == []
        assert {s["id"] for s in models.summaries()} == {"a", "b"}

    def test_unload(self):
        models, closer = LoadedModels(), Closer()
        models.load("a", "A", "p", lambda: "model-a", closer)
        assert models.unload("a") and not models.unload("a")
        assert closer.closed == ["model-a"] and models.get("a") is None
