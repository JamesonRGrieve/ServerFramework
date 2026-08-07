"""Abstract provider template for notification fan-out (Item 43).

Reference targets: Firebase Cloud Messaging, Apple Push Notification
service, AWS SNS, Twilio (SMS), Vonage / Nexmo, OneSignal, Pusher
Beams, in-app delivery via WebSockets.

This module ships the contract only — concrete implementations live in
separate extensions (a follow-up item). Concrete subclasses declare
their typed configuration via the `Settings` inner Pydantic class
(Item 37) and implement each ability marked `@abstractmethod`.

Email coordination
------------------
Email is coordinated alongside this category but is NOT one of the
channels declared here — the canonical email surface is `EXT_Email`
(`AbstractEmailProvider`). When a fan-out needs to include email,
callers route the email portion through `EXT_Email` and the
push/sms/in_app portion through this provider.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Literal, Optional

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticProvider


class AbstractNotificationProvider(AbstractStaticProvider):
    """Abstract notification fan-out provider.

    Concrete implementations declare their settings via the typed
    `Settings` inner Pydantic class (Item 37) and override each ability
    method declared as `@abstractmethod`. The framework discovers the
    abilities for the rotation system through the standard
    `AbstractStaticProvider` surface.
    """

    category: ClassVar[str] = "notification"

    @classmethod
    @abstractmethod
    async def notify(
        cls,
        provider_instance: Any,
        recipient: str,
        channel: Literal["push", "sms", "in_app"],
        title: str,
        body: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Send a single notification to `recipient` over `channel`.

        For ``"push"`` `recipient` is a device token; for ``"sms"`` a
        phone number in E.164; for ``"in_app"`` a user identifier the
        in-app delivery channel resolves to a connected client.
        `payload` is the optional structured data payload alongside
        the user-facing `title` / `body`. Returns the
        provider-assigned `message_id`.
        """
        ...

    @classmethod
    @abstractmethod
    async def notify_multi(
        cls,
        provider_instance: Any,
        recipients: List[str],
        channels: List[str],
        title: str,
        body: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Fan-out a notification to multiple recipients across
        multiple channels in one upstream round trip.

        `recipients` and `channels` are aligned positionally — entry
        `i` is recipient `recipients[i]` over channel `channels[i]`.
        Returns a mapping of `recipient` → `message_id` for entries
        that were accepted by the upstream; failures are logged but
        do not abort the batch.
        """
        ...
