"""
Credential vault primitives for the framework (Item 32, Item 50).

Provides:
- `Secret[T]` — a Pydantic SecretStr re-export (preferred) plus a generic
  wrapper for non-string secret payloads. Both render `***` in str/repr and
  expose a `.reveal()`/`.get_secret_value()` accessor for the cleartext.
- `CredentialRef` — a typed reference to a secret resolvable through the
  documented three-tier order: OpenBao -> environment variable -> encrypted
  database column (Fernet-encrypted using `FRAMEWORK_FERNET_KEY`).
- `RedactingFilter` — a `logging.Filter` that scrubs registered secret
  values from log records before they reach handlers.
- `validate_environment_credentials` — startup-time check enforcing that
  `production` deployments do not resolve any provider credential through
  a `_TEST` env-var (Item 50 enforcement).

The OpenBao tier uses `hvac` if installed; missing module or connection
failure falls through to env-tier without crashing. The env tier honors
the paired-name discriminator (`{NAME}_TEST` vs `{NAME}_LIVE`) keyed on
`APP_ENV`. The encrypted-DB tier is a placeholder that reads a Fernet
ciphertext from the `path` and decrypts it.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, ClassVar, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

T = TypeVar("T")


# ----- Secret wrapper -------------------------------------------------------

# Re-export SecretStr for the common case (string secrets). This gives us
# Pydantic-native validation, `***` repr, and a stable `.get_secret_value()`.
Secret = SecretStr  # type: ignore[assignment]


class SecretValue(Generic[T]):
    """
    Generic secret wrapper for non-string payloads (e.g., dict-shaped JWKs).

    Stringifies as `***`. Use `.reveal()` to retrieve the underlying value.
    Prefer `pydantic.SecretStr` for plain string secrets.
    """

    __slots__ = ("_value",)

    def __init__(self, value: T) -> None:
        self._value = value

    def reveal(self) -> T:
        return self._value

    def get_secret_value(self) -> T:
        # Symmetric API with pydantic.SecretStr.
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "SecretValue('***')"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "***"


# ----- Credential cache + DOWN markers --------------------------------------


class _CredentialCache:
    """Thread-safe in-process cache of resolved credentials.

    Caches by `(tier, path, version, env_suffix)` so cache-bust on auth
    failure is granular. DOWN markers are kept in a separate set; consult
    them via `is_marked_bad`.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._cache: Dict[tuple, str] = {}
        self._bad: set = set()
        self._registered_secrets: set = set()

    def get(self, key: tuple) -> Optional[str]:
        with self._lock:
            return self._cache.get(key)

    def put(self, key: tuple, value: str) -> None:
        with self._lock:
            self._cache[key] = value
            if value:
                self._registered_secrets.add(value)

    def invalidate(self, key: tuple) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def mark_bad(self, key: tuple) -> None:
        with self._lock:
            self._bad.add(key)

    def is_marked_bad(self, key: tuple) -> bool:
        with self._lock:
            return key in self._bad

    def clear_bad(self, key: tuple) -> None:
        with self._lock:
            self._bad.discard(key)

    def register_secret(self, value: str) -> None:
        with self._lock:
            if value:
                self._registered_secrets.add(value)

    def registered_secrets(self) -> List[str]:
        with self._lock:
            return list(self._registered_secrets)

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()
            self._bad.clear()
            self._registered_secrets.clear()


_CACHE = _CredentialCache()


# ----- Tier resolvers -------------------------------------------------------


def _resolve_openbao(path: str, version: Optional[str]) -> Optional[str]:
    """Resolve via OpenBao/Vault HTTP API. Stub — falls through if hvac is
    not installed or the server is unreachable.
    """
    try:
        import hvac  # type: ignore
    except ImportError:
        logging.getLogger(__name__).debug("hvac not installed; skipping OpenBao tier")
        return None
    try:
        client = hvac.Client(
            url=os.environ.get("OPENBAO_ADDR") or os.environ.get("VAULT_ADDR"),
            token=os.environ.get("OPENBAO_TOKEN") or os.environ.get("VAULT_TOKEN"),
        )
        if not client.is_authenticated():
            logging.getLogger(__name__).warning("OpenBao client not authenticated")
            return None
        kwargs: Dict[str, Any] = {"path": path}
        if version:
            kwargs["version"] = int(version)
        resp = client.secrets.kv.v2.read_secret_version(**kwargs)
        data = resp.get("data", {}).get("data", {}) if resp else {}
        # Convention: secret is stored under the "value" key, or the leaf path component.
        if "value" in data:
            return str(data["value"])
        leaf = path.rsplit("/", 1)[-1]
        if leaf in data:
            return str(data[leaf])
        return None
    except Exception as exc:
        logging.getLogger(__name__).debug("OpenBao resolve failed for %s: %s", path, exc)
        return None


def _env_suffix_for(app_env: Optional[str]) -> str:
    """Item 50 paired-name discriminator. Production -> `_LIVE`,
    everything else (development, staging, tests) -> `_TEST`.

    Reads ``os.environ`` directly so test-time monkeypatching of either
    ``ENVIRONMENT`` or ``APP_ENV`` is honoured immediately, without
    forcing a re-validate of the cached ``AppSettings`` singleton.
    Either name is accepted (C-2: the legacy alias is still respected).
    """
    if app_env is None:
        from zephyrex.lib.Environment import is_production
        return "_LIVE" if is_production() else "_TEST"
    return "_LIVE" if (app_env or "").lower() == "production" else "_TEST"


def _resolve_env(path: str, app_env: Optional[str]) -> Optional[str]:
    """Resolve via environment variable, honoring the paired-name suffix.

    First tries `{path}{suffix}` (e.g., `STRIPE_API_KEY_TEST`); falls back
    to bare `{path}` for providers that opt out of the convention.
    """
    suffix = _env_suffix_for(app_env)
    paired = f"{path}{suffix}"
    value = os.environ.get(paired)
    if value:
        return value
    return os.environ.get(path)


def _fernet_key() -> Optional[bytes]:
    raw = os.environ.get("FRAMEWORK_FERNET_KEY")
    return raw.encode("utf-8") if raw else None


def _resolve_encrypted_db(path: str) -> Optional[str]:
    """Resolve via Fernet-decrypting the value at `path`. The `path` is
    treated as a JSON-pointer-shaped reference — for now we support the
    `env:<NAME>` and `inline:<ciphertext>` shapes; broader DB integration
    lands when the credential ORM model is added.
    """
    key = _fernet_key()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        logging.getLogger(__name__).debug("cryptography not installed; skipping encrypted-db tier")
        return None
    if path.startswith("inline:"):
        ciphertext = path[len("inline:"):]
    elif path.startswith("env:"):
        ciphertext = os.environ.get(path[len("env:"):], "")
        if not ciphertext:
            return None
    else:
        return None
    try:
        return Fernet(key).decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        logging.getLogger(__name__).warning("Fernet decrypt failed for %s: %s", path, exc)
        return None


# ----- CredentialRef --------------------------------------------------------


class CredentialRef(BaseModel):
    """Typed reference to a secret resolvable through the documented tier
    order. See module docstring for semantics.
    """

    model_config = ConfigDict(frozen=True)

    tier: Literal["openbao", "env", "encrypted_db"] = "env"
    path: str
    version: Optional[str] = None

    def _cache_key(self, env: Optional[str]) -> tuple:
        return (self.tier, self.path, self.version, _env_suffix_for(env) if self.tier == "env" else "")

    def resolve(self, env: Optional[str] = None) -> str:
        """Resolve the secret. Returns the cleartext string.

        Raises `RuntimeError` if the credential cannot be resolved through
        any tier. Does NOT log the resolved value; callers must register
        it for redaction via `register_secret` if they will pass it to a
        log-emitting subsystem.
        """
        key = self._cache_key(env)
        if _CACHE.is_marked_bad(key):
            raise RuntimeError(f"Credential {self.path} marked bad; rotate before reuse")
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        value: Optional[str] = None
        if self.tier == "openbao":
            value = _resolve_openbao(self.path, self.version)
            if value is None:
                value = _resolve_env(self.path, env)
            if value is None:
                value = _resolve_encrypted_db(self.path)
        elif self.tier == "env":
            value = _resolve_env(self.path, env)
            if value is None:
                value = _resolve_encrypted_db(self.path)
        elif self.tier == "encrypted_db":
            value = _resolve_encrypted_db(self.path)
        if value is None:
            raise RuntimeError(f"Could not resolve credential {self.tier}:{self.path}")
        _CACHE.put(key, value)
        register_secret(value)
        return value


def invalidate(ref: CredentialRef, env: Optional[str] = None) -> None:
    """Bust the resolved-credential cache for `ref`. Use after upstream auth
    rejection so the next resolve hits the source tier."""
    _CACHE.invalidate(ref._cache_key(env))


def mark_bad(ref: CredentialRef, env: Optional[str] = None) -> None:
    """Mark the credential as known-bad. Subsequent resolves raise; the
    rotation system consults `is_marked_bad` to skip this provider."""
    _CACHE.mark_bad(ref._cache_key(env))


def is_marked_bad(ref: CredentialRef, env: Optional[str] = None) -> bool:
    return _CACHE.is_marked_bad(ref._cache_key(env))


def clear_bad(ref: CredentialRef, env: Optional[str] = None) -> None:
    _CACHE.clear_bad(ref._cache_key(env))


def cache_bust_on_auth_rejection(
    ref: CredentialRef, env: Optional[str] = None
) -> str:
    """Item 32 — credential cache-bust on upstream auth rejection.

    Invalidates the cached value and re-resolves from the source tier.
    If the freshly-resolved credential is byte-identical to the cached
    value (i.e. the source did NOT rotate), marks the credential as
    actually-bad and re-raises a RuntimeError so the rotation system
    advances per Item 2's auth policy.

    Returns the freshly-resolved credential when it differs from the
    cached value; raises RuntimeError when it is identical.
    """
    key = ref._cache_key(env)
    previous = _CACHE.get(key)
    _CACHE.invalidate(key)
    fresh = ref.resolve(env=env)
    if previous is not None and fresh == previous:
        _CACHE.mark_bad(key)
        raise RuntimeError(
            f"Credential {ref.tier}:{ref.path} re-resolved identical after "
            "auth rejection; marking bad. Rotate the source-tier credential "
            "before retrying."
        )
    return fresh


# ----- Redaction ------------------------------------------------------------


def register_secret(value: str) -> None:
    """Register a secret value for redaction in log output. Idempotent."""
    _CACHE.register_secret(value)


def registered_secrets() -> List[str]:
    return _CACHE.registered_secrets()


def redact(text: str) -> str:
    """Replace any registered secret values found in `text` with `***`."""
    if not text:
        return text
    out = text
    for s in registered_secrets():
        if s and s in out:
            out = out.replace(s, "***")
    return out


class RedactingFilter(logging.Filter):
    """`logging.Filter` that scrubs registered secret values from each
    record. Attach to handlers (not loggers) so the scrub runs late.

    Scope #10 — when the `privacy` extension is loaded its richer
    `PIILogFilter` (email/bearer/cloud-key patterns + secrets registry)
    runs in addition to this scrubber. Both filters are idempotent so the
    order they attach in does not matter.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if any(s and s in msg for s in registered_secrets()):
                record.msg = redact(msg)
                record.args = ()
            # Delegate richer PII pattern coverage to the ``privacy``
            # extension via ``_pii_hooks["log_filter"]``. Core never
            # imports from the extension; the privacy extension's
            # ``on_load`` registers a record-mutating callable. Without
            # the extension, only the registered-secret scrubbing above
            # fires.
            from zephyrex.logic.BLL_Auth import _pii_hooks

            pii_filter = _pii_hooks["log_filter"]
            if pii_filter is not None:
                try:
                    pii_filter(record)
                except Exception:
                    # An extension bug must not drop the log record.
                    pass
        except Exception:
            return True
        return True


# ----- Sandbox/live discriminator validation (Item 50) ----------------------


def validate_environment_credentials(
    app_env: str, configured_creds: Dict[str, CredentialRef]
) -> None:
    """Refuse to start a `production` deployment that resolves any provider
    credential through a `_TEST_` env-var.

    Args:
        app_env: The deployment environment (`development`, `staging`, `production`).
        configured_creds: Mapping of provider-or-setting label -> CredentialRef.

    Raises:
        RuntimeError: When `app_env == 'production'` and one or more refs
            resolve via the env tier with a `_TEST` suffix-only fallback.
    """
    if (app_env or "").lower() != "production":
        return
    offenders: List[str] = []
    for label, ref in configured_creds.items():
        if ref.tier != "env":
            continue
        # Production requires a _LIVE_ value; if the only set var is _TEST_, refuse.
        live = os.environ.get(f"{ref.path}_LIVE")
        test = os.environ.get(f"{ref.path}_TEST")
        bare = os.environ.get(ref.path)
        if not live and not bare and test:
            offenders.append(f"{label}={ref.path}")
    if offenders:
        raise RuntimeError(
            "Production deployment refuses _TEST_ credentials: " + ", ".join(offenders)
        )


__all__ = [
    "Secret",
    "SecretValue",
    "CredentialRef",
    "invalidate",
    "mark_bad",
    "is_marked_bad",
    "clear_bad",
    "cache_bust_on_auth_rejection",
    "register_secret",
    "registered_secrets",
    "redact",
    "RedactingFilter",
    "validate_environment_credentials",
]
