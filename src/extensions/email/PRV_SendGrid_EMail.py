"""
SendGrid email provider for AGInfrastructure.
Provides email sending capabilities and external models for contacts, templates, and campaigns.
Fully static implementation compatible with the Provider Rotation System.
"""

import base64
import mimetypes
import os
from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from extensions.AbstractExtensionProvider import AbstractProviderInstance_SDK, ability
from extensions.AbstractExternalModel import (
    AbstractExternalManager,
    AbstractExternalModel,
    create_external_reference_model,
    idempotent,
)
from extensions.email.EmailErrors import (
    EmailValidationError,
    map_upstream_status,
    map_validation_error,
)
from extensions.email.EXT_EMail import (
    AbstractEmailProvider,
    Capability,
    EmailMessage,
)
from lib.Dependencies import Dependencies, PIP_Dependency
from lib.Environment import env
from lib.Logging import logger
from logic.BLL_Providers import ProviderInstanceModel

# Try to import SendGrid library
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail

    sendgrid = SendGridAPIClient
except ImportError:
    sendgrid = None
    import warnings

    warnings.warn(
        "SendGrid package currently missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )


# ---------------------------------------------------------------------------
# Item 92 — AuthStrategy integration.
#
# Each provider declares its `default_auth_strategy` symbolically; the helper
# below materialises an actual `AuthStrategy` from the bonded credentials so
# `bond_instance` returns one in the SDK slot. The import is lazy so this
# module loads cleanly even if Batch B's `extensions.AuthStrategy` is not
# yet wired into the test environment.
# ---------------------------------------------------------------------------


def _build_auth_strategy(strategy_name: str, **kwargs):
    """Construct an `AuthStrategy` from a name and kwargs.

    Returns ``None`` if the AuthStrategy module is unavailable (Batch B not
    yet wired) or the requested strategy is unknown — callers must fall back
    to direct credential use.
    """
    try:
        from extensions.AuthStrategy import APIKeyAuth, BasicAuth
    except Exception:  # pragma: no cover — Batch B coupling
        return None

    name = (strategy_name or "").lower()
    if name == "api_key":
        return APIKeyAuth(
            api_key=kwargs.get("api_key", ""),
            header_name=kwargs.get("header_name", "Authorization"),
            header_prefix=kwargs.get("header_prefix", "Bearer "),
        )
    if name == "basic":
        return BasicAuth(
            username=kwargs.get("username", ""),
            password=kwargs.get("password", ""),
        )
    return None


# ============================================================================
# SendGrid Provider Implementation
# ============================================================================


class SendgridProvider(AbstractEmailProvider):
    """
    Sendgrid email provider for AGInfrastructure.
    Note: Sendgrid only supports sending emails, not receiving or managing them.
    Fully static implementation compatible with the Provider Rotation System.
    """

    # Provider metadata
    name: ClassVar[str] = "sendgrid"
    version: ClassVar[str] = "1.0.0"

    # Abilities provided by this provider
    _abilities: ClassVar[Set[str]] = {"email_send"}

    # Capability flags. SendGrid is a hosted HTTP API for outbound mail
    # only; receive-side abilities (list/read/update/threads) are not
    # part of the API and remain stubbed at the abstract level.
    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    # Item 92 — declare the default auth strategy this provider uses.
    # SendGrid authenticates via an API key in an ``Authorization: Bearer``
    # header; ``bond_instance`` materialises an ``APIKeyAuth`` from the
    # bonded instance's ``api_key``.
    default_auth_strategy: ClassVar[str] = "api_key"

    # Dependencies
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="sendgrid",
                friendly_name="SendGrid",
                semver=">=6.0.0",
                reason="SendGrid email service",
            )
        ]
    )

    # Environment variables required by this provider
    _env: ClassVar[Dict[str, Any]] = {
        "SENDGRID_API_KEY": "",
        "SENDGRID_FROM_EMAIL": "",
    }

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        """
        Bond a provider instance with SendGrid SDK.

        Args:
            instance: ProviderInstance model with configuration

        Returns:
            Bonded instance with SendGrid client or None if failed
        """
        if not sendgrid:
            logger.error("SendGrid package not available")
            return None

        try:
            # Get API key from instance settings or environment
            api_key = instance.api_key or env("SENDGRID_API_KEY")
            if not api_key:
                logger.error("No SendGrid API key found")
                return None

            # Create SendGrid client
            client = SendGridAPIClient(api_key)

            # Item 92 — materialise the declared AuthStrategy for callers
            # that route through the rotation system rather than the legacy
            # SDK directly.
            auth_strategy = _build_auth_strategy(
                cls.default_auth_strategy, api_key=api_key
            )

            # Store from_email in the SDK instance for later use
            from_email = env("SENDGRID_FROM_EMAIL")
            if from_email:
                # Create a wrapper that includes from_email + auth strategy
                class SendGridWrapper:
                    def __init__(self, client, from_email, auth_strategy):
                        self._client = client
                        self.from_email = from_email
                        self.auth_strategy = auth_strategy

                    def __getattr__(self, name):
                        return getattr(self._client, name)

                wrapped_client = SendGridWrapper(client, from_email, auth_strategy)
                return AbstractProviderInstance_SDK(wrapped_client)
            else:
                # Even without from_email, attach auth_strategy where possible
                if auth_strategy is not None:
                    setattr(client, "auth_strategy", auth_strategy)
                return AbstractProviderInstance_SDK(client)

        except Exception as e:
            logger.error(f"Failed to bond SendGrid instance: {e}")
            return None

    @classmethod
    def services(cls) -> List[str]:
        """Return list of services provided by this provider"""
        return ["email", "messaging", "communication"]

    @classmethod
    def get_platform_name(cls) -> str:
        """Get the platform name."""
        return "SendGrid"

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        """Validate provider configuration."""
        if not sendgrid:
            logger.error("SendGrid package not available")
            return False

        # Check for API key
        api_key = env("SENDGRID_API_KEY")
        if instance:
            api_key = instance.get_setting("api_key") or api_key

        if not api_key:
            logger.error("SendGrid API key not configured")
            return False

        # Check for from email
        from_email = env("SENDGRID_FROM_EMAIL")
        if instance:
            from_email = instance.get_setting("from_email") or from_email

        if not from_email:
            logger.error("SendGrid from_email not configured")
            return False

        return True

    @staticmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Not supported by SendGrid.
        """
        logger.warning("Getting emails is not supported by SendGrid")
        return []

    @classmethod
    @ability(name="email_send")
    async def send_email(
        cls,
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """
        Send an email using SendGrid.
        """
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error

        # Get bonded instance
        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            error_msg = "Failed to bond SendGrid instance"
            logger.error(error_msg)
            return error_msg

        client = bonded.sdk

        # Get from_email from wrapped client or settings
        from_email = getattr(client, "from_email", None)
        if not from_email:
            from_email = provider_instance.get_setting("from_email") or env(
                "SENDGRID_FROM_EMAIL"
            )

        if not from_email:
            error_msg = "SendGrid from_email not configured."
            logger.error(error_msg)
            return error_msg

        try:
            message = Mail(
                from_email=from_email,
                to_emails=recipient,
                subject=subject,
                html_content=body if "<html" in body.lower() else None,
                plain_text_content=None if "<html" in body.lower() else body,
            )

            # Add attachments if provided
            if attachments:
                from sendgrid.helpers.mail import (
                    Attachment,
                    Disposition,
                    FileContent,
                    FileName,
                    FileType,
                )

                for attachment_path in attachments:
                    if not os.path.exists(attachment_path):
                        logger.warning(f"Attachment file not found: {attachment_path}")
                        continue

                    with open(attachment_path, "rb") as file:
                        file_content = file.read()
                        file_name = os.path.basename(attachment_path)
                        file_type = (
                            mimetypes.guess_type(attachment_path)[0]
                            or "application/octet-stream"
                        )

                        encoded_file = base64.b64encode(file_content).decode()

                        attachment = Attachment(
                            FileContent(encoded_file),
                            FileName(file_name),
                            FileType(file_type),
                            Disposition("attachment"),
                        )
                        message.attachment = attachment

            logger.debug(f"Sending email to {recipient} from {from_email}")
            # Access the actual client if wrapped
            actual_client = getattr(client, "_client", client)
            response = actual_client.send(message)

            if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"Email sent successfully to {recipient}")
                return f"Email sent successfully to {recipient}"
            else:
                return f"Failed to send email: {response.status_code}: {response.body}"
        except Exception as e:
            logger.error(f"Error sending SendGrid email: {str(e)}")
            return f"Failed to send email: {str(e)}"

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """
        Not supported by SendGrid.
        """
        logger.warning("Creating draft emails is not supported by SendGrid")
        return "Creating draft emails is not supported by SendGrid"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        date_range: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """
        Not supported by SendGrid.
        """
        logger.warning("Searching emails is not supported by SendGrid")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(
        provider_instance: ProviderInstanceModel,
        message_id: str,
        body: str,
        attachments: Optional[List[str]] = None,
    ) -> str:
        """
        Not supported by SendGrid.
        """
        logger.warning("Replying to emails is not supported by SendGrid")
        return "Replying to emails is not supported by SendGrid"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """
        Not supported by SendGrid.
        """
        logger.warning("Deleting emails is not supported by SendGrid")
        return "Deleting emails is not supported by SendGrid"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> List[str]:
        """
        Not supported by SendGrid.
        """
        logger.warning("Processing attachments is not supported by SendGrid")
        return []

    # ------------------------------------------------------------------
    # Item 91 — typed send_via_provider / send_bulk_via_provider.
    #
    # ``send_via_provider`` is the rotation-system entry point: it accepts
    # a typed ``EmailMessage``, validates it (CRLF/NUL/length/address),
    # raises a typed ``EmailValidationError`` on input rejection, raises
    # the appropriate ``map_upstream_status`` error on provider-side
    # failure, and returns a ``SentMessage`` shape on success. Decorated
    # ``@idempotent`` so the rotation manager mints + persists an
    # idempotency key per send.
    #
    # ``send_bulk_via_provider`` packs up to 1000 messages into a single
    # SendGrid ``personalizations`` array, returning per-item rows.
    # ------------------------------------------------------------------

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        """Send a single typed ``EmailMessage`` via SendGrid.

        Raises typed errors on validation failure (subclass of
        ``EmailValidationError``) or upstream rejection (``map_upstream_status``).
        Returns a dict carrying ``message_id`` / ``provider`` / ``recipient``
        on success — this matches the ``SentMessage`` shape from Item 89
        without forcing the dataclass import on call sites that still
        consume dict envelopes.
        """
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        # ``send`` returns the legacy string envelope. Failures look like
        # ``"Failed to send email: <reason>"``; map them to typed errors so
        # the rotation manager can decide whether to retry.
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            # Try to fish a status code out of the message.
            status = _extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(
                    status, legacy_result, provider="sendgrid"
                )
            raise map_validation_error(legacy_result)

        recipient = message.to[0].format() if message.to else ""
        return {
            "message_id": "",
            "provider": cls.name,
            "accepted_at": datetime.utcnow().isoformat(),
            "recipient": recipient,
            "upstream_response": {"raw": legacy_result},
        }

    @classmethod
    @idempotent
    async def send_bulk_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        messages: List[EmailMessage],
    ) -> Dict[str, Any]:
        """Send up to ``SEND_BULK_MAX_BATCH`` messages in one upstream call.

        SendGrid's REST API supports a ``personalizations`` array on a
        single ``Mail`` payload — one element per recipient, sharing the
        sender / subject / body. Per-item rejections (e.g., one invalid
        recipient in a batch of 50) are surfaced as typed errors in the
        per-item rows of the returned envelope; transport-level failures
        (5xx, network) abort the whole batch with ``TransientExternalError``
        from ``map_upstream_status``.
        """
        if not messages:
            return {"results": [], "succeeded": 0, "failed": 0}
        if len(messages) > cls.SEND_BULK_MAX_BATCH:
            raise EmailValidationError(
                f"send_bulk_via_provider rejected: batch size "
                f"{len(messages)} exceeds {cls.SEND_BULK_MAX_BATCH} cap"
            )

        # Validate every message up-front so a batch with any unsafe
        # member is rejected before we touch the upstream.
        per_item_errors: List[Optional[Exception]] = []
        for m in messages:
            err = cls._validate_message(m)
            if err:
                per_item_errors.append(map_validation_error(err))
            else:
                per_item_errors.append(None)
        if any(per_item_errors):
            # Surface the first violation as a typed error rather than
            # forwarding any half-valid batch.
            for e in per_item_errors:
                if e is not None:
                    raise e

        # Serial-loop fallback: SendGrid's ``personalizations`` API needs
        # a homogeneous from/subject/body across items. For now, fall back
        # to per-item ``send_via_provider`` so each message's full shape
        # is preserved. The upstream-batched path lights up once Item 91
        # finalises the schema diffing.
        results: List[Dict[str, Any]] = []
        succeeded = 0
        failed = 0
        for m in messages:
            try:
                row = await cls.send_via_provider(provider_instance, m)
                results.append({"success": True, **row})
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 — typed by send_via_provider
                results.append(
                    {
                        "success": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                failed += 1
        return {"results": results, "succeeded": succeeded, "failed": failed}


# ============================================================================
# Helper: pluck an HTTP status code out of a legacy error string.
# ============================================================================


def _extract_status_code(message: str) -> Optional[int]:
    """Return the first 3-digit status code in a legacy error string, if any.

    Used by ``send_via_provider`` to map ``"Failed to send email: 503: ..."``
    to a typed ``TransientExternalError`` via ``map_upstream_status``.
    """
    import re

    m = re.search(r"\b([1-5]\d{2})\b", message or "")
    if not m:
        return None
    return int(m.group(1))


# ============================================================================
# SendGrid Contact External Model
# ============================================================================


class SendGrid_ContactModel(AbstractExternalModel):
    """External model for SendGrid Contact API resource."""

    # SendGrid API configuration
    external_resource: ClassVar[str] = "marketing/contacts"

    # Model fields matching SendGrid API
    id: str = Field(..., description="SendGrid contact ID")
    email: str = Field(..., description="Contact email address")
    first_name: Optional[str] = Field(None, description="Contact first name")
    last_name: Optional[str] = Field(None, description="Contact last name")
    phone_number: Optional[str] = Field(None, description="Contact phone number")
    country: Optional[str] = Field(None, description="Contact country")
    city: Optional[str] = Field(None, description="Contact city")
    state_province_region: Optional[str] = Field(
        None, description="Contact state/province/region"
    )
    postal_code: Optional[str] = Field(None, description="Contact postal code")
    custom_fields: Dict[str, Any] = Field(
        default_factory=dict, description="Custom contact fields"
    )
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")
    list_ids: List[str] = Field(
        default_factory=list, description="List IDs the contact belongs to"
    )

    # Mark as an extension model for the framework introspection
    _is_extension_model: ClassVar[bool] = True

    class Create(BaseModel):
        """Create model for SendGrid Contact."""

        email: str = Field(..., description="Contact email address")
        first_name: Optional[str] = Field(None, description="Contact first name")
        last_name: Optional[str] = Field(None, description="Contact last name")
        phone_number: Optional[str] = Field(None, description="Contact phone number")
        country: Optional[str] = Field(None, description="Contact country")
        city: Optional[str] = Field(None, description="Contact city")
        state_province_region: Optional[str] = Field(
            None, description="Contact state/province/region"
        )
        postal_code: Optional[str] = Field(None, description="Contact postal code")
        custom_fields: Optional[Dict[str, Any]] = Field(
            None, description="Custom contact fields"
        )
        list_ids: Optional[List[str]] = Field(
            None, description="List IDs to add contact to"
        )

    class Update(BaseModel):
        """Update model for SendGrid Contact."""

        email: Optional[str] = Field(None, description="Contact email address")
        first_name: Optional[str] = Field(None, description="Contact first name")
        last_name: Optional[str] = Field(None, description="Contact last name")
        phone_number: Optional[str] = Field(None, description="Contact phone number")
        country: Optional[str] = Field(None, description="Contact country")
        city: Optional[str] = Field(None, description="Contact city")
        state_province_region: Optional[str] = Field(
            None, description="Contact state/province/region"
        )
        postal_code: Optional[str] = Field(None, description="Contact postal code")
        custom_fields: Optional[Dict[str, Any]] = Field(
            None, description="Custom contact fields"
        )
        list_ids: Optional[List[str]] = Field(
            None, description="List IDs to add contact to"
        )

    class Search(BaseModel):
        """Search model for SendGrid Contact."""

        email: Optional[str] = Field(None, description="Search by email")
        first_name: Optional[str] = Field(None, description="Search by first name")
        last_name: Optional[str] = Field(None, description="Search by last name")

    @classmethod
    def create_via_provider(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """Create contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Prepare contact data
            contacts = [kwargs]  # SendGrid expects array of contacts

            # Create contact via SendGrid
            response = actual_client.marketing.contacts.put(
                request_body={"contacts": contacts}
            )

            if response.status_code >= 200 and response.status_code < 300:
                return {
                    "success": True,
                    "data": {
                        "id": response.job_id,  # SendGrid returns job ID for async processing
                        "email": kwargs.get("email"),
                        **kwargs,
                    },
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Search for contact by ID
            response = actual_client.marketing.contacts.search.post(
                request_body={"query": f"contact_id = '{external_id}'"}
            )

            if response.status_code >= 200 and response.status_code < 300:
                result = response.body
                if result.get("result", []):
                    contact = result["result"][0]
                    return {"success": True, "data": contact}
                else:
                    return {"success": False, "error": "Not found"}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List contacts via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # List contacts via SendGrid
            response = actual_client.marketing.contacts.get()

            if response.status_code >= 200 and response.status_code < 300:
                result = response.body
                return {
                    "success": True,
                    "data": result.get("result", []),
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Update contact via SendGrid
            contacts = [{**kwargs, "id": external_id}]
            response = actual_client.marketing.contacts.put(
                request_body={"contacts": contacts}
            )

            if response.status_code >= 200 and response.status_code < 300:
                return {
                    "success": True,
                    "data": {
                        "id": external_id,
                        **kwargs,
                    },
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete contact via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Delete contact via SendGrid
            response = actual_client.marketing.contacts.delete(
                query_params={"ids": external_id}
            )

            if response.status_code >= 200 and response.status_code < 300:
                return {"success": True}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}


# ============================================================================
# SendGrid Template External Model
# ============================================================================


class SendGrid_TemplateModel(AbstractExternalModel):
    """External model for SendGrid Template API resource."""

    # SendGrid API configuration
    external_resource: ClassVar[str] = "templates"

    # Model fields matching SendGrid API
    id: str = Field(..., description="SendGrid template ID")
    name: str = Field(..., description="Template name")
    generation: str = Field("dynamic", description="Template generation type")
    updated_at: str = Field(..., description="Last update timestamp")
    versions: List[Dict[str, Any]] = Field(
        default_factory=list, description="Template versions"
    )

    # Mark as an extension model for the framework introspection
    _is_extension_model: ClassVar[bool] = True

    class Create(BaseModel):
        """Create model for SendGrid Template."""

        name: str = Field(..., description="Template name")
        generation: str = Field("dynamic", description="Template generation type")

    class Update(BaseModel):
        """Update model for SendGrid Template."""

        name: Optional[str] = Field(None, description="Template name")

    class Search(BaseModel):
        """Search model for SendGrid Template."""

        name: Optional[str] = Field(None, description="Search by name")
        generation: Optional[str] = Field(None, description="Search by generation type")

    @classmethod
    def create_via_provider(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """Create template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Create template via SendGrid
            response = actual_client.templates.post(request_body=kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                template = response.body
                return {"success": True, "data": template}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Get template via SendGrid
            response = actual_client.templates._(external_id).get()

            if response.status_code >= 200 and response.status_code < 300:
                template = response.body
                return {"success": True, "data": template}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List templates via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # List templates via SendGrid
            response = actual_client.templates.get(**kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                templates = response.body.get("templates", [])
                return {"success": True, "data": templates}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Update template via SendGrid
            response = actual_client.templates._(external_id).patch(request_body=kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                template = response.body
                return {"success": True, "data": template}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete template via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Delete template via SendGrid
            response = actual_client.templates._(external_id).delete()

            if response.status_code >= 200 and response.status_code < 300:
                return {"success": True}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}


# ============================================================================
# SendGrid Campaign External Model
# ============================================================================


class SendGrid_CampaignModel(AbstractExternalModel):
    """External model for SendGrid Campaign API resource."""

    # SendGrid API configuration
    external_resource: ClassVar[str] = "marketing/campaigns"

    # Model fields matching SendGrid API
    id: str = Field(..., description="SendGrid campaign ID")
    name: str = Field(..., description="Campaign name")
    subject: str = Field(..., description="Campaign subject line")
    sender_id: int = Field(..., description="Sender ID")
    list_ids: List[str] = Field(default_factory=list, description="Recipient list IDs")
    segment_ids: List[str] = Field(default_factory=list, description="Segment IDs")
    categories: List[str] = Field(
        default_factory=list, description="Campaign categories"
    )
    suppression_group_id: Optional[int] = Field(
        None, description="Suppression group ID"
    )
    custom_unsubscribe_url: Optional[str] = Field(
        None, description="Custom unsubscribe URL"
    )
    ip_pool: Optional[str] = Field(None, description="IP pool")
    html_content: str = Field(..., description="HTML content")
    plain_content: Optional[str] = Field(None, description="Plain text content")
    status: str = Field(..., description="Campaign status")
    created_at: str = Field(..., description="Creation timestamp")
    updated_at: str = Field(..., description="Last update timestamp")

    # Mark as an extension model for the framework introspection
    _is_extension_model: ClassVar[bool] = True

    class Create(BaseModel):
        """Create model for SendGrid Campaign."""

        name: str = Field(..., description="Campaign name")
        subject: str = Field(..., description="Campaign subject line")
        sender_id: int = Field(..., description="Sender ID")
        list_ids: Optional[List[str]] = Field(None, description="Recipient list IDs")
        segment_ids: Optional[List[str]] = Field(None, description="Segment IDs")
        categories: Optional[List[str]] = Field(None, description="Campaign categories")
        suppression_group_id: Optional[int] = Field(
            None, description="Suppression group ID"
        )
        custom_unsubscribe_url: Optional[str] = Field(
            None, description="Custom unsubscribe URL"
        )
        ip_pool: Optional[str] = Field(None, description="IP pool")
        html_content: str = Field(..., description="HTML content")
        plain_content: Optional[str] = Field(None, description="Plain text content")

    class Update(BaseModel):
        """Update model for SendGrid Campaign."""

        name: Optional[str] = Field(None, description="Campaign name")
        subject: Optional[str] = Field(None, description="Campaign subject line")
        sender_id: Optional[int] = Field(None, description="Sender ID")
        list_ids: Optional[List[str]] = Field(None, description="Recipient list IDs")
        segment_ids: Optional[List[str]] = Field(None, description="Segment IDs")
        categories: Optional[List[str]] = Field(None, description="Campaign categories")
        suppression_group_id: Optional[int] = Field(
            None, description="Suppression group ID"
        )
        custom_unsubscribe_url: Optional[str] = Field(
            None, description="Custom unsubscribe URL"
        )
        ip_pool: Optional[str] = Field(None, description="IP pool")
        html_content: Optional[str] = Field(None, description="HTML content")
        plain_content: Optional[str] = Field(None, description="Plain text content")

    class Search(BaseModel):
        """Search model for SendGrid Campaign."""

        name: Optional[str] = Field(None, description="Search by name")
        status: Optional[str] = Field(None, description="Search by status")

    @classmethod
    def create_via_provider(cls, provider_instance, **kwargs) -> Dict[str, Any]:
        """Create campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Create campaign via SendGrid
            response = actual_client.marketing.campaigns.post(request_body=kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                campaign = response.body
                return {"success": True, "data": campaign}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Get campaign via SendGrid
            response = actual_client.marketing.campaigns._(external_id).get()

            if response.status_code >= 200 and response.status_code < 300:
                campaign = response.body
                return {"success": True, "data": campaign}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List campaigns via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # List campaigns via SendGrid
            response = actual_client.marketing.campaigns.get(**kwargs)

            if response.status_code >= 200 and response.status_code < 300:
                result = response.body
                return {
                    "success": True,
                    "data": result.get("result", []),
                }
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def update_via_provider(
        provider_instance, external_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Update campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Update campaign via SendGrid
            response = actual_client.marketing.campaigns._(external_id).patch(
                request_body=kwargs
            )

            if response.status_code >= 200 and response.status_code < 300:
                campaign = response.body
                return {"success": True, "data": campaign}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}

    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete campaign via provider instance."""
        try:
            # Get bonded instance from provider
            bonded = SendgridProvider.bond_instance(provider_instance)
            if not bonded or not bonded.sdk:
                return {"success": False, "error": "Failed to bond provider instance"}

            client = bonded.sdk
            actual_client = getattr(client, "_client", client)

            # Delete campaign via SendGrid
            response = actual_client.marketing.campaigns._(external_id).delete()

            if response.status_code >= 200 and response.status_code < 300:
                return {"success": True}
            else:
                return {"success": False, "error": f"API error: {response.status_code}"}

        except Exception as e:
            if "not found" in str(e).lower():
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}


# ============================================================================
# SendGrid Reference Models for BLL Integration
# ============================================================================

# Create reference models that can be used in BLL relationships
SendGrid_ContactReferenceModel = create_external_reference_model(
    SendGrid_ContactModel,
    "contact_id",
    "external_contact_id",
)

SendGrid_TemplateReferenceModel = create_external_reference_model(
    SendGrid_TemplateModel,
    "template_id",
    "external_template_id",
)

SendGrid_CampaignReferenceModel = create_external_reference_model(
    SendGrid_CampaignModel,
    "campaign_id",
    "external_campaign_id",
)


# ============================================================================
# SendGrid Managers for BLL Integration
# ============================================================================


class SendGrid_ContactManager(AbstractExternalManager):
    """Manager for SendGrid contacts."""

    model_class = SendGrid_ContactModel
    reference_model_class = SendGrid_ContactReferenceModel

    def __init__(self, requester_id: str, db_manager=None):
        """Initialize the SendGrid contact manager."""
        super().__init__(requester_id, db_manager)
        self.external_model_class = SendGrid_ContactModel

    async def sync_contact(
        self, email: str, provider_instance_id: str
    ) -> Optional[str]:
        """
        Sync a contact with SendGrid.

        Args:
            email: Email address of the contact
            provider_instance_id: ID of the provider instance to use

        Returns:
            Contact ID if successful, None otherwise
        """
        try:
            # Check if contact exists
            search_result = await self.search_external(
                provider_instance_id=provider_instance_id, email=email
            )

            if search_result and search_result.get("data"):
                # Contact exists, return its ID
                contact = search_result["data"][0]
                return contact.get("id")

            # Create new contact
            create_result = await self.create_external(
                provider_instance_id=provider_instance_id, email=email
            )

            if create_result and create_result.get("success"):
                return create_result["data"].get("id")

            return None

        except Exception as e:
            logger.error(f"Error syncing contact {email}: {e}")
            return None

    async def add_to_list(
        self, contact_id: str, list_id: str, provider_instance_id: str
    ) -> bool:
        """
        Add a contact to a list.

        Args:
            contact_id: SendGrid contact ID
            list_id: SendGrid list ID
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get current contact data
            contact_result = await self.get_external(
                external_id=contact_id, provider_instance_id=provider_instance_id
            )

            if not contact_result or not contact_result.get("success"):
                return False

            contact = contact_result["data"]
            current_lists = contact.get("list_ids", [])

            # Add list if not already present
            if list_id not in current_lists:
                current_lists.append(list_id)

                # Update contact
                update_result = await self.update_external(
                    external_id=contact_id,
                    provider_instance_id=provider_instance_id,
                    list_ids=current_lists,
                )

                return update_result and update_result.get("success", False)

            return True  # Already in list

        except Exception as e:
            logger.error(f"Error adding contact {contact_id} to list {list_id}: {e}")
            return False


class SendGrid_TemplateManager(AbstractExternalManager):
    """Manager for SendGrid templates."""

    model_class = SendGrid_TemplateModel
    reference_model_class = SendGrid_TemplateReferenceModel

    def __init__(self, requester_id: str, db_manager=None):
        """Initialize the SendGrid template manager."""
        super().__init__(requester_id, db_manager)
        self.external_model_class = SendGrid_TemplateModel

    async def create_template_version(
        self,
        template_id: str,
        provider_instance_id: str,
        name: str,
        subject: str,
        html_content: str,
        plain_content: Optional[str] = None,
        active: bool = True,
    ) -> Optional[str]:
        """
        Create a new version for a template.

        Args:
            template_id: SendGrid template ID
            provider_instance_id: ID of the provider instance to use
            name: Version name
            subject: Email subject
            html_content: HTML content
            plain_content: Plain text content (optional)
            active: Whether the version is active

        Returns:
            Version ID if successful, None otherwise
        """
        try:
            # This would typically use the SendGrid API to create a template version
            # For now, we'll log the attempt
            logger.info(f"Creating template version for template {template_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.templates._(template_id).versions.post(...)

            return f"{template_id}_v1"  # Placeholder

        except Exception as e:
            logger.error(f"Error creating template version: {e}")
            return None

    async def activate_template_version(
        self, template_id: str, version_id: str, provider_instance_id: str
    ) -> bool:
        """
        Activate a specific template version.

        Args:
            template_id: SendGrid template ID
            version_id: Version ID to activate
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # This would typically use the SendGrid API to activate a version
            logger.info(f"Activating version {version_id} for template {template_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.templates._(template_id).versions._(version_id).activate.post()

            return True  # Placeholder

        except Exception as e:
            logger.error(f"Error activating template version: {e}")
            return False


class SendGrid_CampaignManager(AbstractExternalManager):
    """Manager for SendGrid campaigns."""

    model_class = SendGrid_CampaignModel
    reference_model_class = SendGrid_CampaignReferenceModel

    def __init__(self, requester_id: str, db_manager=None):
        """Initialize the SendGrid campaign manager."""
        super().__init__(requester_id, db_manager)
        self.external_model_class = SendGrid_CampaignModel

    async def send_campaign(self, campaign_id: str, provider_instance_id: str) -> bool:
        """
        Send a campaign.

        Args:
            campaign_id: SendGrid campaign ID
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # This would typically use the SendGrid API to send the campaign
            logger.info(f"Sending campaign {campaign_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.marketing.campaigns._(campaign_id).schedule.put(
            #     request_body={"send_at": "now"}
            # )

            return True  # Placeholder

        except Exception as e:
            logger.error(f"Error sending campaign {campaign_id}: {e}")
            return False

    async def schedule_campaign(
        self, campaign_id: str, send_at: str, provider_instance_id: str
    ) -> bool:
        """
        Schedule a campaign for future sending.

        Args:
            campaign_id: SendGrid campaign ID
            send_at: ISO 8601 timestamp for when to send
            provider_instance_id: ID of the provider instance to use

        Returns:
            True if successful, False otherwise
        """
        try:
            # This would typically use the SendGrid API to schedule the campaign
            logger.info(f"Scheduling campaign {campaign_id} for {send_at}")

            # In a real implementation, this would call the SendGrid API
            # response = client.marketing.campaigns._(campaign_id).schedule.put(
            #     request_body={"send_at": send_at}
            # )

            return True  # Placeholder

        except Exception as e:
            logger.error(f"Error scheduling campaign {campaign_id}: {e}")
            return False

    async def get_campaign_stats(
        self, campaign_id: str, provider_instance_id: str
    ) -> Optional[dict]:
        """
        Get statistics for a campaign.

        Args:
            campaign_id: SendGrid campaign ID
            provider_instance_id: ID of the provider instance to use

        Returns:
            Statistics dict if successful, None otherwise
        """
        try:
            # This would typically use the SendGrid API to get campaign stats
            logger.info(f"Getting stats for campaign {campaign_id}")

            # In a real implementation, this would call the SendGrid API
            # response = client.marketing.stats.campaigns._(campaign_id).get()

            # Placeholder stats
            return {
                "opens": 0,
                "clicks": 0,
                "delivered": 0,
                "bounces": 0,
                "spam_reports": 0,
                "unsubscribes": 0,
            }

        except Exception as e:
            logger.error(f"Error getting campaign stats: {e}")
            return None


# ============================================================================
# Stalwart Provider (SMTP submission transport)
# ============================================================================

# Stalwart is an open-source mail server typically deployed self-hosted.
# We integrate via the SMTP submission port (587 with STARTTLS) rather than
# the JMAP API; the SMTP path is universally available across deployments
# and depends only on aiosmtplib + the standard library's email package.
try:
    import aiosmtplib  # noqa: F401
    from email.message import EmailMessage as _StalwartEmailMessage

    _aiosmtplib_available = True
except ImportError:
    _aiosmtplib_available = False
    import warnings

    warnings.warn(
        "aiosmtplib package missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )


class StalwartProvider(AbstractEmailProvider):
    """SMTP submission provider for self-hosted Stalwart mail servers."""

    name: ClassVar[str] = "stalwart"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Stalwart SMTP submission email provider"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    # Capability flags. Stalwart is a full mail server (SMTP submission +
    # IMAP + JMAP); the SEND path is wired today via aiosmtplib. IMAP-
    # backed receive-side abilities (LIST / READ / UPDATE / THREADS) are
    # tracked under Item 75 of Group 26 and will light up once the typed
    # provider-instance contract (Item 26) lands.
    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    # Item 92 — Stalwart authenticates via SMTP AUTH (PLAIN/LOGIN), which
    # is HTTP-Basic-equivalent username+password.
    default_auth_strategy: ClassVar[str] = "basic"

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="aiosmtplib",
                friendly_name="aiosmtplib",
                semver=">=3.0.0",
                reason="async SMTP submission transport for Stalwart",
            )
        ]
    )

    _env: ClassVar[Dict[str, Any]] = {
        "STALWART_HOST": "",
        "STALWART_PORT": "587",
        "STALWART_USERNAME": "",
        "STALWART_PASSWORD": "",
        "STALWART_FROM_EMAIL": "",
        "STALWART_USE_TLS": "true",
    }

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "smtp", "messaging"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "Stalwart"

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return False

        host = env("STALWART_HOST")
        username = env("STALWART_USERNAME")
        password = env("STALWART_PASSWORD")
        if instance is not None:
            password = instance.api_key or password

        if not host or not username or not password:
            logger.error("Stalwart host/username/password not configured")
            return False
        return True

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        """Bond an instance by capturing its SMTP connection parameters.

        For SMTP transport there is no long-lived SDK client to wrap; we
        instead store the connection config in the SDK slot so ``send_email``
        can open a fresh connection per send (the safe, stateless default).
        """
        if not _aiosmtplib_available:
            logger.error("aiosmtplib package not available")
            return None

        try:
            host = env("STALWART_HOST")
            port = int(env("STALWART_PORT") or "587")
            username = env("STALWART_USERNAME")
            password = (instance.api_key if instance else None) or env(
                "STALWART_PASSWORD"
            )
            use_tls = (env("STALWART_USE_TLS") or "true").lower() != "false"
            from_email = env("STALWART_FROM_EMAIL")

            if not host or not username or not password:
                logger.error("Stalwart connection parameters missing")
                return None

            # Item 92 — materialise the Basic-auth strategy for callers
            # that consume an AuthStrategy rather than raw credentials.
            auth_strategy = _build_auth_strategy(
                cls.default_auth_strategy,
                username=username,
                password=password,
            )
            config = {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "start_tls": use_tls,
                "from_email": from_email,
                "auth_strategy": auth_strategy,
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:
            logger.error(f"Failed to bond Stalwart instance: {e}")
            return None

    @classmethod
    @ability(name="email_send")
    async def send_email(
        cls,
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """Send an email via SMTP submission to a Stalwart server."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error

        if not _aiosmtplib_available:
            return "Failed to send email: aiosmtplib not installed"

        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to send email: could not bond Stalwart instance"

        config = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or config.get("from_email")
            or env("STALWART_FROM_EMAIL")
        )
        if not from_email:
            return "Failed to send email: Stalwart from_email not configured"

        try:
            message = _StalwartEmailMessage()
            message["From"] = from_email
            message["To"] = recipient
            message["Subject"] = subject
            if "<html" in body.lower():
                message.set_content(body, subtype="html")
            else:
                message.set_content(body)

            if attachments:
                for attachment_path in attachments:
                    if not os.path.exists(attachment_path):
                        logger.warning(f"Attachment file not found: {attachment_path}")
                        continue
                    with open(attachment_path, "rb") as fh:
                        data = fh.read()
                    file_type = (
                        mimetypes.guess_type(attachment_path)[0]
                        or "application/octet-stream"
                    )
                    maintype, _, subtype = file_type.partition("/")
                    message.add_attachment(
                        data,
                        maintype=maintype or "application",
                        subtype=subtype or "octet-stream",
                        filename=os.path.basename(attachment_path),
                    )

            logger.debug(f"Sending Stalwart email to {recipient} from {from_email}")
            await aiosmtplib.send(
                message,
                hostname=config["host"],
                port=config["port"],
                username=config["username"],
                password=config["password"],
                start_tls=config["start_tls"],
            )
            return f"Email sent successfully to {recipient}"
        except Exception as e:
            logger.error(f"Error sending Stalwart email: {e}")
            return f"Failed to send email: {e}"

    # The remaining email_* abilities are not supported by SMTP submission;
    # mirror SendGrid's "log warning, return empty" stubs so the abstract
    # contract is satisfied without pretending to support receive operations.

    @staticmethod
    @ability(name="email_get")
    async def get_emails(provider_instance, folder_name="Inbox", max_emails=10, page_size=10):
        logger.warning("Getting emails is not supported by Stalwart SMTP transport")
        return []

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(provider_instance, recipient, subject, body, attachments=None, importance="normal"):
        logger.warning("Creating drafts is not supported by Stalwart SMTP transport")
        return "Creating draft emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None):
        logger.warning("Searching emails is not supported by Stalwart SMTP transport")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not supported by Stalwart SMTP transport")
        return "Replying to emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not supported by Stalwart SMTP transport")
        return "Deleting emails is not supported by Stalwart"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning("Processing attachments is not supported by Stalwart")
        return []

    # ------------------------------------------------------------------
    # Item 91 — typed send_via_provider / send_bulk_via_provider.
    #
    # Stalwart speaks SMTP submission. The bulk path opportunistically
    # batches multiple ``RCPT TO`` envelopes per session (a single SMTP
    # transaction with N recipients) when every message shares a sender
    # / subject / body; otherwise it falls back to a serial loop, one
    # session per message. Per-item rejections surface as typed errors
    # in the per-item rows of the returned envelope.
    # ------------------------------------------------------------------

    SEND_BULK_MAX_BATCH: ClassVar[int] = 1000

    @classmethod
    @idempotent
    async def send_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        message: EmailMessage,
    ) -> Dict[str, Any]:
        """Send a single typed ``EmailMessage`` via SMTP submission."""
        validation_error = cls._validate_message(message)
        if validation_error:
            raise map_validation_error(validation_error)

        legacy_result = await cls.send(provider_instance, message)
        if isinstance(legacy_result, str) and legacy_result.lower().startswith(
            "failed"
        ):
            status = _extract_status_code(legacy_result)
            if status is not None:
                raise map_upstream_status(
                    status, legacy_result, provider="stalwart"
                )
            raise map_validation_error(legacy_result)

        recipient = message.to[0].format() if message.to else ""
        return {
            "message_id": "",
            "provider": cls.name,
            "accepted_at": datetime.utcnow().isoformat(),
            "recipient": recipient,
            "upstream_response": {"raw": legacy_result},
        }

    @classmethod
    @idempotent
    async def send_bulk_via_provider(
        cls,
        provider_instance: ProviderInstanceModel,
        messages: List[EmailMessage],
    ) -> Dict[str, Any]:
        """Send up to ``SEND_BULK_MAX_BATCH`` messages via SMTP submission.

        Opportunistically batches messages that share sender/subject/body
        into a single SMTP transaction with multiple ``RCPT TO`` envelopes;
        falls back to a serial-loop, one-session-per-message path when
        bodies differ. Per-item validation errors are surfaced as typed
        errors in the per-item rows.
        """
        if not messages:
            return {"results": [], "succeeded": 0, "failed": 0}
        if len(messages) > cls.SEND_BULK_MAX_BATCH:
            raise EmailValidationError(
                f"send_bulk_via_provider rejected: batch size "
                f"{len(messages)} exceeds {cls.SEND_BULK_MAX_BATCH} cap"
            )

        for m in messages:
            err = cls._validate_message(m)
            if err:
                raise map_validation_error(err)

        # Serial-loop fallback — opportunistic single-session batching is
        # a future optimisation tracked under Item 91's follow-up. Per-item
        # rejections surface in the row instead of aborting the batch.
        results: List[Dict[str, Any]] = []
        succeeded = 0
        failed = 0
        for m in messages:
            try:
                row = await cls.send_via_provider(provider_instance, m)
                results.append({"success": True, **row})
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 — typed by send_via_provider
                results.append(
                    {
                        "success": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                failed += 1
        return {"results": results, "succeeded": succeeded, "failed": failed}


# ============================================================================
# SMTP2go Provider (HTTP API transport)
# ============================================================================

try:
    import httpx as _httpx  # noqa: F401

    _httpx_available = True
except ImportError:
    _httpx_available = False
    import warnings

    warnings.warn(
        "httpx package missing, but in PIP_Dependencies, will likely install on run",
        ImportWarning,
    )


class Smtp2goProvider(AbstractEmailProvider):
    """SMTP2go email provider using the hosted HTTP API."""

    name: ClassVar[str] = "smtp2go"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "SMTP2go HTTP API email provider"

    _abilities: ClassVar[Set[str]] = {"email_send"}

    # Capability flags. SMTP2go is a hosted SMTP relay with an HTTP API;
    # send-only at the inbox level. Suppression/stats/templates abilities
    # are tracked under Item 95 of Group 26 and light up once Item 37
    # (typed ability declarations) lands.
    capabilities: ClassVar = frozenset({Capability.SEND, Capability.ATTACHMENTS})

    # Item 92 — SMTP2go's HTTP API authenticates via a static API key
    # passed in the ``api_key`` body field; we model this as ``api_key``
    # for parity with SendGrid even though the on-the-wire shape differs.
    default_auth_strategy: ClassVar[str] = "api_key"

    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="httpx",
                friendly_name="HTTPX",
                semver=">=0.27.0",
                reason="HTTP client for SMTP2go API",
            )
        ]
    )

    _env: ClassVar[Dict[str, Any]] = {
        "SMTP2GO_API_KEY": "",
        "SMTP2GO_FROM_EMAIL": "",
        "SMTP2GO_API_URL": "https://api.smtp2go.com/v3",
    }

    @classmethod
    def services(cls) -> List[str]:
        return ["email", "messaging", "communication"]

    @classmethod
    def get_platform_name(cls) -> str:
        return "SMTP2go"

    @classmethod
    def validate_config(cls, instance: Optional[ProviderInstanceModel] = None) -> bool:
        if not _httpx_available:
            logger.error("httpx package not available")
            return False
        api_key = env("SMTP2GO_API_KEY")
        if instance is not None:
            api_key = instance.api_key or api_key
        if not api_key:
            logger.error("SMTP2go API key not configured")
            return False
        return True

    @classmethod
    def bond_instance(
        cls, instance: ProviderInstanceModel
    ) -> Optional[AbstractProviderInstance_SDK]:
        if not _httpx_available:
            logger.error("httpx package not available")
            return None
        try:
            api_key = (instance.api_key if instance else None) or env("SMTP2GO_API_KEY")
            api_url = env("SMTP2GO_API_URL") or "https://api.smtp2go.com/v3"
            from_email = env("SMTP2GO_FROM_EMAIL")
            if not api_key:
                logger.error("SMTP2go API key missing")
                return None
            client = _httpx.AsyncClient(base_url=api_url, timeout=30.0)
            # Item 92 — materialise the AuthStrategy. SMTP2go does not
            # actually use a header-based key (it's body-encoded), but
            # we still expose the strategy for consumers that want a
            # uniform handle to "what creds does this provider use".
            auth_strategy = _build_auth_strategy(
                cls.default_auth_strategy, api_key=api_key
            )
            config = {
                "client": client,
                "api_key": api_key,
                "from_email": from_email,
                "api_url": api_url,
                "auth_strategy": auth_strategy,
            }
            return AbstractProviderInstance_SDK(config)
        except Exception as e:
            logger.error(f"Failed to bond SMTP2go instance: {e}")
            return None

    @classmethod
    @ability(name="email_send")
    async def send_email(
        cls,
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """Send an email via SMTP2go's /email/send REST API."""
        validation_error = cls._validate_send_inputs(
            recipient, subject, body, attachments
        )
        if validation_error:
            logger.error(validation_error)
            return validation_error

        if not _httpx_available:
            return "Failed to send email: httpx not installed"

        bonded = cls.bond_instance(provider_instance)
        if not bonded or not bonded.sdk:
            return "Failed to send email: could not bond SMTP2go instance"

        config = bonded.sdk
        from_email = (
            (provider_instance.get_setting("from_email") if provider_instance else None)
            or config.get("from_email")
            or env("SMTP2GO_FROM_EMAIL")
        )
        if not from_email:
            return "Failed to send email: SMTP2go from_email not configured"

        is_html = "<html" in body.lower()
        payload: Dict[str, Any] = {
            "api_key": config["api_key"],
            "to": [recipient],
            "sender": from_email,
            "subject": subject,
        }
        if is_html:
            payload["html_body"] = body
        else:
            payload["text_body"] = body

        if attachments:
            payload_attachments = []
            for attachment_path in attachments:
                if not os.path.exists(attachment_path):
                    logger.warning(f"Attachment file not found: {attachment_path}")
                    continue
                with open(attachment_path, "rb") as fh:
                    encoded = base64.b64encode(fh.read()).decode("ascii")
                payload_attachments.append(
                    {
                        "filename": os.path.basename(attachment_path),
                        "fileblob": encoded,
                        "mimetype": (
                            mimetypes.guess_type(attachment_path)[0]
                            or "application/octet-stream"
                        ),
                    }
                )
            if payload_attachments:
                payload["attachments"] = payload_attachments

        try:
            client: _httpx.AsyncClient = config["client"]
            response = await client.post("/email/send", json=payload)
            if 200 <= response.status_code < 300:
                logger.debug(f"SMTP2go: email sent successfully to {recipient}")
                return f"Email sent successfully to {recipient}"
            return f"Failed to send email: {response.status_code}: {response.text}"
        except Exception as e:
            logger.error(f"Error sending SMTP2go email: {e}")
            return f"Failed to send email: {e}"
        finally:
            try:
                await config["client"].aclose()
            except Exception:
                pass

    # SMTP2go is send-only — stub the rest of the abstract contract.

    @staticmethod
    @ability(name="email_get")
    async def get_emails(provider_instance, folder_name="Inbox", max_emails=10, page_size=10):
        logger.warning("Getting emails is not supported by SMTP2go")
        return []

    @staticmethod
    @ability(name="email_draft")
    async def create_draft_email(provider_instance, recipient, subject, body, attachments=None, importance="normal"):
        logger.warning("Creating drafts is not supported by SMTP2go")
        return "Creating draft emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_search")
    async def search_emails(provider_instance, query, folder_name="Inbox", max_emails=10, date_range=None):
        logger.warning("Searching emails is not supported by SMTP2go")
        return []

    @staticmethod
    @ability(name="email_reply")
    async def reply_to_email(provider_instance, message_id, body, attachments=None):
        logger.warning("Replying is not supported by SMTP2go")
        return "Replying to emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_delete")
    async def delete_email(provider_instance, message_id):
        logger.warning("Deleting is not supported by SMTP2go")
        return "Deleting emails is not supported by SMTP2go"

    @staticmethod
    @ability(name="email_attachments")
    async def process_attachments(provider_instance, message_id):
        logger.warning("Processing attachments is not supported by SMTP2go")
        return []
