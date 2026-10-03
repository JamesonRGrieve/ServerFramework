# SPDX-License-Identifier: AGPL-3.0-or-later
"""Models that run on this server's own hardware: where their files come
from, where they are kept, and which of them are in memory.

A model is declared, never discovered: a provider instance names the
source it is downloaded from, its repository, the commit its files are
pinned to and every file's SHA-256. Only the sources the operator allows
(``LOCAL_AI_MODEL_SOURCES``) are fetched from, every file is checked
against its digest before it is kept, and a file that does not match is
discarded. Files live under the models directory
(``LOCAL_AI_MODELS_DIR``, else ``$HF_HOME/zephyrex-models``) at
``<owner>--<name>/<revision>/<path>``; a path that would leave it is
refused, so nothing a caller or a setting says can read or write
elsewhere.

A model is loaded the first time it is used and kept until it is
unloaded, or until loading another would exceed
``LOCAL_AI_MAX_LOADED_MODELS``, when the least recently used idle one is
unloaded first.
"""

import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin

import httpx

from zephyrex.extensions.ExternalErrors import (
    AuthExternalError,
    BaseExternalError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.lib.Environment import env
from zephyrex.lib.ProviderHTTPClient import (
    ClientPolicy,
    SSRFGuardError,
    get_async_client,
    user_agent,
    validate_outbound_url,
)

DEFAULT_SOURCE = "https://huggingface.co"
DEFAULT_MAX_LOADED_MODELS = 2
DEFAULT_MAX_DOWNLOAD_BYTES = 64 * 1024**3
DOWNLOAD_TIMEOUT_SECONDS = 60.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_DOWNLOAD_REDIRECTS = 5
MAX_MODEL_FILES = 64
HASH_CHUNK_BYTES = 4 * 1024 * 1024

_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# A path segment: no leading dot, so never ``.``, ``..`` or a hidden file
# (download scratch files are hidden, so no model file can collide with one).
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_MAX_SEGMENTS = 8


def models_root() -> Path:
    """Where model files are kept."""
    configured = env("LOCAL_AI_MODELS_DIR")
    if configured:
        return Path(configured)
    hf_home = env("HF_HOME")
    if hf_home:
        return Path(hf_home) / "zephyrex-models"
    return Path.home() / ".cache" / "zephyrex-models"


def allowed_sources() -> List[str]:
    """The sources models may be downloaded from."""
    raw = env("LOCAL_AI_MODEL_SOURCES") or DEFAULT_SOURCE
    return [s.strip().rstrip("/") for s in raw.split(",") if s.strip()]


def misconfigured(what: str, provider: str) -> PermanentExternalError:
    return PermanentExternalError(f"model misconfigured: {what}", provider=provider)


def checked_relative_path(path: str, provider: str) -> str:
    """``path`` as a file inside a model's directory, or refused."""
    segments = path.split("/")
    if len(segments) > _MAX_SEGMENTS or not all(_SEGMENT.match(s) for s in segments):
        raise misconfigured(f"{path!r} is not a plain relative file path", provider)
    return path


def inside(root: Path, path: Path, provider: str) -> Path:
    """``path``, which must resolve inside ``root``."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise misconfigured(f"{path} leaves the models directory", provider)
    return resolved


@dataclass(frozen=True)
class ModelFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class ModelSpec:
    """A model's files: where they come from and what they must hash to."""

    source: str
    repo: str
    revision: str
    files: Tuple[ModelFile, ...]

    def directory(self, root: Path, provider: str) -> Path:
        owner, name = self.repo.split("/")
        return inside(root, root / f"{owner}--{name}" / self.revision, provider)

    def local_path(self, root: Path, file: ModelFile, provider: str) -> Path:
        directory = self.directory(root, provider)
        return inside(directory, directory / file.path, provider)

    def url(self, file: ModelFile) -> str:
        return f"{self.source}/{self.repo}/resolve/{self.revision}/{quote(file.path)}"


def model_spec(
    source: Optional[str],
    repo: Optional[str],
    revision: Optional[str],
    files: Optional[str],
    provider: str,
) -> ModelSpec:
    """A model's declaration from its settings, checked: an allowed source,
    an ``owner/name`` repository, a pinned commit, and plain relative file
    paths each with a SHA-256.

    An instance that declares no files and no revision is not configured
    (as the instance seeded for each provider is not): a transient error,
    so a rotation moves on to the next instance. One declared wrongly is a
    permanent one."""
    if not files and not revision:
        raise TransientExternalError("model not configured", provider=provider)
    chosen = (source or DEFAULT_SOURCE).rstrip("/")
    if chosen not in allowed_sources():
        raise misconfigured(
            f"source {chosen!r} is not in LOCAL_AI_MODEL_SOURCES", provider
        )
    if not repo or not _REPO.match(repo):
        raise misconfigured("repo is owner/name", provider)
    if not revision or not _REVISION.match(revision):
        raise misconfigured("revision is a 40-character commit id", provider)
    try:
        declared = json.loads(files or "")
    except json.JSONDecodeError as exc:
        raise misconfigured("files is a JSON object of path: sha256", provider) from exc
    if not isinstance(declared, dict) or not declared:
        raise misconfigured("files is a JSON object of path: sha256", provider)
    if len(declared) > MAX_MODEL_FILES:
        raise misconfigured(f"at most {MAX_MODEL_FILES} files", provider)
    checked: List[ModelFile] = []
    for path, digest in sorted(declared.items()):
        if not isinstance(digest, str) or not _SHA256.match(digest.lower()):
            raise misconfigured(f"{path!r} needs a SHA-256 hex digest", provider)
        checked.append(ModelFile(checked_relative_path(path, provider), digest.lower()))
    return ModelSpec(chosen, repo, revision, tuple(checked))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Files already checked in this process, by path, size and change time: a
# model loads without rehashing gigabytes each time.
_verified: Dict[Tuple[str, int, int], str] = {}
_verified_lock = threading.Lock()


def holds(path: Path, sha256: str) -> bool:
    """Whether ``path`` exists and hashes to ``sha256``."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _verified_lock:
        known = _verified.get(key)
    if known is None:
        known = file_sha256(path)
        with _verified_lock:
            _verified[key] = known
    return hmac.compare_digest(known, sha256)


def _status_error(status: int, where: str, provider: str) -> BaseExternalError:
    if status in (401, 403):
        return AuthExternalError(
            f"{where} refused the download", provider=provider, upstream_status=status
        )
    if status == 429 or status >= 500:
        return TransientExternalError(
            f"{where} answered {status}", provider=provider, upstream_status=status
        )
    return PermanentExternalError(
        f"{where} answered {status}: the file is not there",
        provider=provider,
        upstream_status=status,
    )


async def download_verified(
    url: str,
    target: Path,
    sha256: str,
    *,
    provider: str,
    token: Optional[str] = None,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> None:
    """Stream ``url`` to ``target``, keeping it only when it hashes to
    ``sha256``. Each redirect is checked by the SSRF guard as the first
    address is; the bearer ``token`` goes to the first address only."""
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    client = get_async_client(ClientPolicy(timeout=DOWNLOAD_TIMEOUT_SECONDS))
    headers = {"User-Agent": user_agent()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    current, digest, size = url, hashlib.sha256(), 0
    try:
        for _ in range(MAX_DOWNLOAD_REDIRECTS + 1):
            where = httpx.URL(current).host
            try:
                validate_outbound_url(current)
            except SSRFGuardError as exc:
                raise PermanentExternalError(
                    f"download refused by the SSRF guard: {where}", provider=provider
                ) from exc
            async with client.stream("GET", current, headers=headers) as response:
                if response.is_redirect and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    headers.pop("Authorization", None)
                    continue
                if not 200 <= response.status_code < 300:
                    raise _status_error(response.status_code, where, provider)
                with scratch.open("wb") as out:
                    async for chunk in response.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                        size += len(chunk)
                        if size > max_bytes:
                            raise PermanentExternalError(
                                f"{target.name} is larger than {max_bytes} bytes",
                                provider=provider,
                            )
                        digest.update(chunk)
                        out.write(chunk)
            break
        else:
            raise PermanentExternalError(
                f"{url} redirected more than {MAX_DOWNLOAD_REDIRECTS} times",
                provider=provider,
            )
        if not hmac.compare_digest(digest.hexdigest(), sha256):
            raise PermanentExternalError(
                f"{target.name} does not match its SHA-256; discarded",
                provider=provider,
            )
        os.replace(scratch, target)
    except httpx.TimeoutException as exc:
        raise TransientExternalError(
            f"timed out downloading {target.name}", provider=provider, cause=exc
        ) from exc
    except httpx.RequestError as exc:
        raise TransientExternalError(
            f"network error downloading {target.name}", provider=provider, cause=exc
        ) from exc
    finally:
        scratch.unlink(missing_ok=True)


@dataclass
class LoadedModel:
    """A model in memory. ``lock`` serializes inference on it: the
    libraries' model objects are not safe to use from two threads."""

    key: str
    name: str
    provider: str
    value: Any
    close: Callable[[Any], None]
    loaded_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    uses: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.key,
            "name": self.name,
            "provider": self.provider,
            "loaded_at": self.loaded_at,
            "last_used": self.last_used,
            "uses": self.uses,
        }


class LoadedModels:
    """The models this process holds in memory, by provider instance id."""

    def __init__(self) -> None:
        self._models: "OrderedDict[str, LoadedModel]" = OrderedDict()
        self._lock = threading.Lock()
        self._loading: Dict[str, threading.Lock] = {}

    @staticmethod
    def capacity() -> int:
        raw = env("LOCAL_AI_MAX_LOADED_MODELS")
        return max(1, int(raw)) if raw.isdigit() else DEFAULT_MAX_LOADED_MODELS

    def get(self, key: str) -> Optional[LoadedModel]:
        with self._lock:
            return self._models.get(key)

    def load(
        self,
        key: str,
        name: str,
        provider: str,
        load: Callable[[], Any],
        close: Callable[[Any], None],
    ) -> LoadedModel:
        """The model loaded for ``key``, loading it with ``load`` if it is
        not in memory yet (once, however many callers ask at a time)."""
        with self._lock:
            gate = self._loading.setdefault(key, threading.Lock())
        with gate:
            with self._lock:
                found = self._models.get(key)
                if found is not None:
                    self._models.move_to_end(key)
                    return found
            self._make_room()
            loaded = LoadedModel(key, name, provider, load(), close)
            with self._lock:
                self._models[key] = loaded
            return loaded

    def _make_room(self) -> None:
        """Unload least recently used idle models until one more fits."""
        while True:
            with self._lock:
                if len(self._models) < self.capacity():
                    return
                idle = next(
                    (m for m in self._models.values() if not m.lock.locked()), None
                )
            if idle is None or not self.unload(idle.key):
                return

    def touch(self, loaded: LoadedModel) -> None:
        with self._lock:
            loaded.uses += 1
            loaded.last_used = time.time()
            if loaded.key in self._models:
                self._models.move_to_end(loaded.key)

    def unload(self, key: str) -> bool:
        """Unload ``key``'s model, after any inference on it finishes;
        whether it was loaded."""
        with self._lock:
            loaded = self._models.pop(key, None)
        if loaded is None:
            return False
        with loaded.lock:
            loaded.close(loaded.value)
        return True

    def summaries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [m.summary() for m in self._models.values()]


LOADED_MODELS = LoadedModels()
