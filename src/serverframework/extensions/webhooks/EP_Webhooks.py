"""Webhook router factory.

Mounts:
    POST /webhook/{extension}/{provider}
    POST /webhook/{extension}/{provider}/{event}

Behaviour identical to the legacy ``endpoints/Webhook.create_webhook_router``
with two additions:
  * Mandatory signature verification (existing).
  * Optional replay protection via ``BLL_Webhooks.check_replay`` (new).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response

from serverframework.extensions.webhooks.BLL_Webhooks import (
    WebhookContext,
    check_replay,
    get_provider_class,
    has_any_handler,
    lookup_handler,
    maybe_await,
    parse_payload,
)
from serverframework.lib.InboundSecurity import (
    _get_counter,
    parse_rate_spec,
    rate_limit,
)
from serverframework.lib.Logging import logger


# M-3 — webhook rate limiting is keyed on `(extension, provider)` rather than
# the global per-IP scope `@rate_limit` produces. A single misbehaving
# provider can no longer exhaust every other provider's budget. Per-provider
# per-IP scope keeps per-provider budgets, while still rejecting an IP that
# spams a single provider.
_WEBHOOK_RATE = "300/min"
_WEBHOOK_COUNT, _WEBHOOK_WINDOW = parse_rate_spec(_WEBHOOK_RATE)


def _enforce_webhook_rate(extension: str, provider: str, peer: Optional[str]) -> bool:
    """Returns True when the request is within budget."""
    counter = _get_counter()
    key = f"webhook:{extension}:{provider}:{peer or '?'}"
    try:
        return counter.incr(key, _WEBHOOK_WINDOW) <= _WEBHOOK_COUNT
    except Exception:
        # Fail open on counter outage; identical contract as RateLimitMiddleware.
        return True


def create_webhook_router() -> APIRouter:
    """Construct the public webhook ``APIRouter`` with the canonical mount."""

    router = APIRouter(prefix="/webhook", tags=["webhook"])

    async def _dispatch(
        extension: str,
        provider: str,
        event: Optional[str],
        request: Request,
    ) -> Response:
        # M-3 — per-(extension, provider, peer) budget. The per-IP global
        # `@rate_limit` is retained as an outer DoS gate but no longer
        # exhausts a provider's budget when a sibling provider is misbehaving.
        from serverframework.lib.InboundSecurity import resolve_client_ip

        peer = resolve_client_ip(request)
        if not _enforce_webhook_rate(extension, provider, peer):
            raise HTTPException(
                status_code=429,
                detail=f"Webhook rate limit exceeded for {extension}/{provider}",
                headers={"Retry-After": str(_WEBHOOK_WINDOW)},
            )

        body_bytes = await request.body()
        payload = parse_payload(body_bytes)
        headers = {k.lower(): v for k, v in request.headers.items()}

        handler = lookup_handler(extension, provider, event)
        provider_class = get_provider_class(extension, provider)
        verify_signature = (
            getattr(provider_class, "verify_signature", None)
            if provider_class
            else None
        )

        # Unknown extension/provider must 404 — not 200. Returning 200 to
        # unauthenticated callers turns this endpoint into an extension
        # discovery oracle and lets attackers map the deployment without
        # ever crossing a signature check.
        if provider_class is None:
            logger.warning(
                f"Webhook rejected: unknown extension/provider "
                f"{extension}/{provider}"
            )
            raise HTTPException(
                status_code=404, detail="Unknown webhook extension/provider"
            )

        # Signature verification is REQUIRED on every accepted route. A
        # provider that does not register ``verify_signature`` is a
        # configuration error: 503 (not 401) so operators see "the
        # provider is not safe to receive webhooks" rather than "the
        # signature failed".
        if verify_signature is None:
            logger.warning(
                f"Webhook for {extension}/{provider} rejected: provider "
                f"class does not register verify_signature."
            )
            raise HTTPException(
                status_code=503,
                detail="Signature verification not configured for this provider",
            )
        try:
            ok = await maybe_await(verify_signature(headers, body_bytes))
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
        replay_reason = check_replay(
            extension, provider, provider_class, headers, body_bytes
        )
        if replay_reason is not None:
            logger.warning(
                f"Webhook replay rejected for {extension}/{provider}: "
                f"{replay_reason}"
            )
            raise HTTPException(
                status_code=401, detail=f"Replay rejected: {replay_reason}"
            )
        if handler is None:
            # Signature passed; the handler list just doesn't include this
            # event name. Acknowledge so the upstream stops retrying, but
            # log so operators can see drift.
            logger.warning(
                f"Unrecognized webhook event for {extension}/{provider}/{event};"
                f" signature OK, returning 200 without dispatch."
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
            await maybe_await(result)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Webhook handler {handler.__name__} raised: {e}")
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
