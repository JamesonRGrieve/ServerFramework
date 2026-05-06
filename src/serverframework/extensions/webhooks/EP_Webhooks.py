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
from serverframework.lib.InboundSecurity import rate_limit
from serverframework.lib.Logging import logger


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
        payload = parse_payload(body_bytes)
        headers = {k.lower(): v for k, v in request.headers.items()}

        handler = lookup_handler(extension, provider, event)
        provider_class = get_provider_class(extension, provider)
        verify_signature = (
            getattr(provider_class, "verify_signature", None)
            if provider_class
            else None
        )

        # Only enforce signature verification when a handler is actually
        # registered for this (extension, provider). Otherwise an upstream
        # probe to a non-existent extension/provider would 401 instead of
        # the more informative "unrecognized event -> 200 + warn" path.
        if handler is not None or has_any_handler(extension, provider):
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
