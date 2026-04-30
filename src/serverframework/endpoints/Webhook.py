"""Inbound webhook handler infrastructure (Item 5).

Provides a typed, registry-driven mount for upstream webhook deliveries::

    @webhook_handler(EXT_Payment, provider="stripe", event="customer.updated")
    def on_customer_updated(ctx: WebhookContext) -> None:
        ...

Mounts:
    POST /webhook/{extension}/{provider}
    POST /webhook/{extension}/{provider}/{event}

Behavior:
    1. Resolve handler from ``WEBHOOK_REGISTRY``.
    2. If the provider class exposes ``verify_signature(headers, body) -> bool``,
       call it. Reject with 401 on failure.
    3. Dispatch the handler with a :class:`WebhookContext`.
    4. An unrecognized event logs a warning and returns 200 (so upstream does
       not retry an event we deliberately ignore).
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from serverframework.lib.InboundSecurity import rate_limit
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

        # Stash provider class for signature verification, if discoverable.
        provider_class = _resolve_provider_class(extension_class, provider_norm)
        if provider_class is not None:
            _PROVIDER_CLASSES[(extension_name, provider_norm)] = provider_class

        logger.debug(
            f"Registered webhook handler {func.__name__} for {key}"
        )
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
# Router factory
# ---------------------------------------------------------------------------


def _lookup_handler(
    extension: str, provider: str, event: Optional[str]
) -> Optional[Callable]:
    extension = extension.lower()
    provider = provider.lower()
    if event is not None:
        handler = WEBHOOK_REGISTRY.get((extension, provider, event))
        if handler is not None:
            return handler
    # Fall back to a wildcard handler for the provider.
    return WEBHOOK_REGISTRY.get((extension, provider, None))


def _has_any_handler(extension: str, provider: str) -> bool:
    """True if any handler is registered for ``(extension, provider, *)``."""
    extension = extension.lower()
    provider = provider.lower()
    for k in WEBHOOK_REGISTRY.keys():
        if k[0] == extension and k[1] == provider:
            return True
    return False


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def create_webhook_router() -> APIRouter:
    """Construct the public webhook ``APIRouter`` with the canonical mount."""

    router = APIRouter(prefix="/webhook", tags=["webhook"])

    async def _dispatch(
        extension: str,
        provider: str,
        event: Optional[str],
        request: Request,
    ) -> Response:
        body_bytes = await request.body()
        try:
            parsed = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {"raw": body_bytes.decode("utf-8", errors="replace")}
        # `WebhookContext.payload` is a dict; wrap top-level JSON arrays
        # (e.g. SendGrid Event Webhook deliveries) in `{"events": [...]}`
        # so handlers receive a uniform dict envelope. Handlers that
        # need the raw list inspect `ctx.payload["events"]`.
        if isinstance(parsed, list):
            payload = {"events": parsed}
        elif isinstance(parsed, dict):
            payload = parsed
        else:
            payload = {"raw": parsed}

        headers = {k.lower(): v for k, v in request.headers.items()}

        # Signature verification is MANDATORY per Item 5: if no
        # ``verify_signature`` is registered for the provider, reject with
        # 401. The webhook mount is public, so unverified delivery would
        # let any caller fire arbitrary events at the framework's hook bus.
        handler = _lookup_handler(extension, provider, event)
        provider_class = _PROVIDER_CLASSES.get(
            (extension.lower(), provider.lower())
        )
        verify_signature = (
            getattr(provider_class, "verify_signature", None)
            if provider_class
            else None
        )
        # Only enforce signature verification when a handler is actually
        # registered for this (extension, provider). Otherwise an upstream
        # probe to a non-existent extension/provider would 401 instead of
        # the more informative "unrecognized event -> 200 + warn" path.
        if handler is not None or _has_any_handler(extension, provider):
            if verify_signature is None:
                logger.warning(
                    f"Webhook for {extension}/{provider} rejected: no "
                    f"verify_signature registered on provider class."
                )
                raise HTTPException(
                    status_code=401,
                    detail="Signature verification not configured for this provider",
                )
            try:
                ok = await _maybe_await(verify_signature(headers, body_bytes))
            except Exception as e:
                logger.warning(
                    f"Signature verification raised for {extension}/{provider}: {e}"
                )
                raise HTTPException(
                    status_code=401, detail="Signature verification failed"
                )
            if not ok:
                raise HTTPException(
                    status_code=401, detail="Signature verification failed"
                )
        if handler is None:
            logger.warning(
                f"Unrecognized webhook event for {extension}/{provider}/{event};"
                f" returning 200 without dispatch."
            )
            return Response(status_code=200)

        ctx = WebhookContext(
            payload=payload,
            headers=headers,
            extension_name=extension.lower(),
            provider_name=provider.lower(),
            event_name=event,
        )

        try:
            result = handler(ctx)
            await _maybe_await(result)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Webhook handler {handler.__name__} raised: {e}"
            )
            raise HTTPException(status_code=500, detail="Webhook handler error")

        return Response(status_code=200)

    @router.post("/{extension}/{provider}")
    @rate_limit("100/min", scope="ip")
    async def _no_event(extension: str, provider: str, request: Request) -> Response:
        return await _dispatch(extension, provider, None, request)

    @router.post("/{extension}/{provider}/{event}")
    @rate_limit("100/min", scope="ip")
    async def _with_event(
        extension: str, provider: str, event: str, request: Request
    ) -> Response:
        return await _dispatch(extension, provider, event, request)

    return router
