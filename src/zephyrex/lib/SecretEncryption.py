"""Shared encryption-at-rest primitive used by extensions that persist
opaque secrets (TOTP seeds, IdP refresh tokens, etc.).

Storage shape: stored values are tagged with a ``fernet:`` prefix. This lets
extensions distinguish encrypted blobs from legacy plaintext rows during a
no-downtime rollout, and lets the strict-mode validator confirm that every
row is encrypted.

Operational policy:
- ``FRAMEWORK_FERNET_KEY`` is the canonical key.
- An empty key is permitted only when ``ENVIRONMENT in {local, ci, development}``
  *and* ``ALLOW_PLAINTEXT_SECRETS=true``. Both must be true. Any other
  combination raises ``MissingFernetKeyError`` at the call site so the boot
  sequence aborts on first encrypt/decrypt rather than silently writing
  plaintext.
- The ``FRAMEWORK_FERNET_KEY`` check participates in
  :meth:`EnvironmentSettings._enforce_production_fail_closed` so a
  production deployment cannot accidentally boot without one.
"""

from __future__ import annotations

from typing import Optional

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


FERNET_PREFIX = "fernet:"


class MissingFernetKeyError(RuntimeError):
    """``FRAMEWORK_FERNET_KEY`` is required but not configured."""


def _fernet():
    """Return a ``cryptography.fernet.Fernet`` instance or ``None``."""
    key = env("FRAMEWORK_FERNET_KEY") or env("MFA_FERNET_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.error(
            "cryptography package is required for encryption-at-rest "
            "but is not installed"
        )
        return None
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception as exc:
        logger.error(f"FRAMEWORK_FERNET_KEY rejected by Fernet: {exc}")
        return None


def _plaintext_fallback_permitted() -> bool:
    """Plaintext secrets are only acceptable in dev-style environments and
    only when the operator opts in explicitly."""
    environment = (env("ENVIRONMENT") or "local").lower()
    if environment not in ("local", "ci", "development"):
        return False
    return (env("ALLOW_PLAINTEXT_SECRETS") or "").lower() == "true"


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    """Encrypt ``plaintext`` for storage. ``None``/empty is returned unchanged.

    Raises ``MissingFernetKeyError`` when no key is configured AND the
    deployment is not explicitly running in plaintext-permitted mode.
    """
    if not plaintext:
        return plaintext
    if plaintext.startswith(FERNET_PREFIX):
        return plaintext
    f = _fernet()
    if f is None:
        if _plaintext_fallback_permitted():
            logger.warning(
                "Storing secret in plaintext: FRAMEWORK_FERNET_KEY unset and "
                "ALLOW_PLAINTEXT_SECRETS=true (dev/ci only)"
            )
            return plaintext
        raise MissingFernetKeyError(
            "FRAMEWORK_FERNET_KEY is required to persist this secret. "
            "Set it, or set ALLOW_PLAINTEXT_SECRETS=true in a dev/ci environment."
        )
    return FERNET_PREFIX + f.encrypt(plaintext.encode("utf-8")).decode("ascii")  # type: ignore[no-any-return]


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    """Reverse of :func:`encrypt_secret`.

    Plaintext (untagged) rows pass through unchanged so an in-place rollout
    is non-disruptive: a deployment can set the key, restart, and rewrite
    rows lazily on next access.

    Raises ``MissingFernetKeyError`` when an encrypted row is encountered
    but no key is configured.
    """
    if not stored:
        return stored
    if not stored.startswith(FERNET_PREFIX):
        return stored
    f = _fernet()
    if f is None:
        raise MissingFernetKeyError(
            "Encrypted secret encountered but FRAMEWORK_FERNET_KEY is unset"
        )
    token = stored[len(FERNET_PREFIX):].encode("ascii")
    try:
        return f.decrypt(token).decode("utf-8")  # type: ignore[no-any-return]
    except Exception as exc:
        raise RuntimeError(f"Decrypt failed: {exc}") from exc


def assert_encryption_available() -> None:
    """Raise ``MissingFernetKeyError`` if neither a key nor an explicit
    plaintext-fallback opt-in is configured. Call from extension
    ``validate_config()`` so misconfigurations surface at boot."""
    if _fernet() is not None:
        return
    if _plaintext_fallback_permitted():
        return
    raise MissingFernetKeyError(
        "FRAMEWORK_FERNET_KEY is required and ALLOW_PLAINTEXT_SECRETS is not set"
    )
