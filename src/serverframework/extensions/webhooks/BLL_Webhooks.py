"""Webhook registry, dispatch, and replay protection.

Inbound webhook handler infrastructure (formerly endpoints/Webhook.py — now
lives in the optional ``webhooks`` extension).

Provides a typed, registry-driven mount for upstream webhook deliveries::

    @webhook_handler(EXT_Payment, provider="stripe", event="customer.updated")
    def on_customer_updated(ctx: WebhookContext) -> None:
        ...

Behaviour:
    1. Resolve handler from ``WEBHOOK_REGISTRY``.
    2. If the provider class exposes ``verify_signature(headers, body) -> bool``,
       call it. Reject with 401 on failure.
    3. Replay protection: a provider that exposes ``replay_window_seconds`` and
       ``extract_replay_keys(headers, body) -> (timestamp_epoch, nonce)`` is
       gated against (a) timestamp skew outside the window and (b) re-use of
       the (provider, nonce) tuple within the window. Providers that do not
       expose these hooks fall back to verify_signature only and an explicit
       config-time warning.
    4. Dispatch the handler with a :class:`WebhookContext`.
"""

from __future__ import annotations

import inspect
import json
import time
from threading import Lock
from typing import Any, Callable, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from serverframework.lib.Logging import logger


# ---------------------------------------------------------------------------
# Registry + context model
# ---------------------------------------------------------------------------


# Key: (extension_name, provider_name, event_name_or_None)
WEBHOOK_REGISTRY: Dict[Tuple[str, str, Optional[str]], Callable] = {}

# Track provider classes by (extension, provider) for signature verification.
_PROVIDER_CLASSES: Dict[Tuple[str, str], Any] = {}


class WebhookContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    payload: dict
    headers: Dict[str, str]
    extension_name: str
    provider_name: str
    event_name: Optional[str] = None
    requester_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Replay protection (Item: webhook hardening)
# ---------------------------------------------------------------------------


_DEFAULT_REPLAY_WINDOW_SECONDS = 300


class ReplayCache:
    """In-memory dedup cache keyed by ``(extension, provider, nonce)``.

    Bounded by ``max_entries`` to prevent unbounded growth from junk
    payloads. Production deployments behind multiple workers MUST replace
    this with a distributed cache (Redis / DistributedCounter) — the
    in-memory variant is per-process and a horizontal deployment will
    accept the same nonce on each replica within the window.
    """

    def __init__(self, max_entries: int = 50_000) -> None:
        self._max = max_entries
        self._entries: Dict[Tuple[str, str, str], float] = {}
        self._lock = Lock()

    def remember(self, key: Tuple[str, str, str], expires_at: float) -> bool:
        """Return True if the key was novel; False if already present."""
        now = time.time()
        with self._lock:
            # Sweep at insert time. Cheap relative to network IO.
            if len(self._entries) >= self._max:
                self._evict_expired(now)
                if len(self._entries) >= self._max:
                    # Drop oldest if still full.
                    oldest = min(self._entries, key=self._entries.get)  # type: ignore[arg-type]
                    self._entries.pop(oldest, None)
            existing = self._entries.get(key)
            if existing is not None and existing > now:
                return False
            self._entries[key] = expires_at
            return True

    def _evict_expired(self, now: float) -> None:
        for k in [k for k, exp in self._entries.items() if exp <= now]:
            self._entries.pop(k, None)


_REPLAY_CACHE = ReplayCache()


def reset_replay_cache_for_test() -> None:
    """Test-only hook to clear the dedup cache between tests."""
    _REPLAY_CACHE._entries.clear()


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def _extension_name_of(extension_class: Any) -> str:
    """Best-effort extraction of an extension's canonical name."""
    name = getattr(extension_class, "extension_name", None)
    if name:
        return str(name).lower()
    cls_name = getattr(extension_class, "__name__", str(extension_class))
    if cls_name.startswith("EXT_"):
        cls_name = cls_name[4:]
    return cls_name.lower()


def webhook_handler(
    extension_class: Any,
    provider: str,
    event: Optional[str] = None,
) -> Callable[[Callable], Callable]:
    """Register ``func`` as the handler for ``(extension, provider, event)``.

    If ``event`` is None, the handler matches any event for that provider that
    has no more-specific registration.
    """

    extension_name = _extension_name_of(extension_class)
    provider_norm = provider.lower()

    def decorator(func: Callable) -> Callable:
        key = (extension_name, provider_norm, event)
        if key in WEBHOOK_REGISTRY:
            logger.warning(
                f"Webhook handler for {key} already registered; overwriting."
            )
        WEBHOOK_REGISTRY[key] = func

        provider_class = _resolve_provider_class(extension_class, provider_norm)
        if provider_class is not None:
            _PROVIDER_CLASSES[(extension_name, provider_norm)] = provider_class

        logger.debug(f"Registered webhook handler {func.__name__} for {key}")
        return func

    return decorator


def _resolve_provider_class(extension_class: Any, provider: str) -> Optional[Any]:
    """Best-effort resolution of an extension's provider class for signature checks."""
    candidates = (
        f"PRV_{provider.capitalize()}",
        f"PRV_{provider.upper()}",
        provider,
        provider.capitalize(),
    )
    for name in candidates:
        prv = getattr(extension_class, name, None)
        if prv is not None:
            return prv
    providers_attr = getattr(extension_class, "providers", None)
    if isinstance(providers_attr, dict):
        return providers_attr.get(provider) or providers_attr.get(provider.lower())
    return None


# ---------------------------------------------------------------------------
# Lookup + dispatch helpers
# ---------------------------------------------------------------------------


def lookup_handler(
    extension: str, provider: str, event: Optional[str]
) -> Optional[Callable]:
    extension = extension.lower()
    provider = provider.lower()
    if event is not None:
        handler = WEBHOOK_REGISTRY.get((extension, provider, event))
        if handler is not None:
            return handler
    return WEBHOOK_REGISTRY.get((extension, provider, None))


def has_any_handler(extension: str, provider: str) -> bool:
    """True if any handler is registered for ``(extension, provider, *)``."""
    extension = extension.lower()
    provider = provider.lower()
    for k in WEBHOOK_REGISTRY.keys():
        if k[0] == extension and k[1] == provider:
            return True
    return False


def get_provider_class(extension: str, provider: str) -> Optional[Any]:
    return _PROVIDER_CLASSES.get((extension.lower(), provider.lower()))


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def parse_payload(body_bytes: bytes) -> dict:
    """Coerce a raw webhook body into a uniform dict envelope."""
    try:
        parsed = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": body_bytes.decode("utf-8", errors="replace")}
    if isinstance(parsed, list):
        return {"events": parsed}
    if isinstance(parsed, dict):
        return parsed
    return {"raw": parsed}


def check_replay(
    extension: str,
    provider: str,
    provider_class: Any,
    headers: Dict[str, str],
    body_bytes: bytes,
) -> Optional[str]:
    """Run provider-declared replay protection.

    Returns ``None`` on accept, or a string reason on reject.

    A provider opts in by exposing both:
      * ``replay_window_seconds`` (int, class attribute)
      * ``extract_replay_keys(headers, body) -> (epoch_seconds: int, nonce: str)``

    Without these, the framework cannot enforce replay protection by itself
    (every provider has a different timestamp/nonce convention) and falls
    back to signature verification alone.
    """
    if provider_class is None:
        return None
    extractor = getattr(provider_class, "extract_replay_keys", None)
    window = getattr(provider_class, "replay_window_seconds", None)
    if extractor is None or window is None:
        return None
    try:
        ts, nonce = extractor(headers, body_bytes)
    except Exception as exc:  # pragma: no cover - defensive
        return f"replay-key extraction failed: {exc}"
    if ts is None or nonce is None:
        return "replay timestamp or nonce missing"
    now = time.time()
    if abs(now - float(ts)) > float(window):
        return "replay timestamp outside window"
    expires = now + float(window)
    accepted = _REPLAY_CACHE.remember(
        (extension.lower(), provider.lower(), str(nonce)), expires
    )
    if not accepted:
        return "replay nonce reuse"
    return None
