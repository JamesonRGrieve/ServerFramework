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
)
from extensions.email.EXT_EMail import AbstractEmailProvider
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

            # Store from_email in the SDK instance for later use
            from_email = env("SENDGRID_FROM_EMAIL")
            if from_email:
                # Create a wrapper that includes from_email
                class SendGridWrapper:
                    def __init__(self, client, from_email):
                        self._client = client
                        self.from_email = from_email

                    def __getattr__(self, name):
                        return getattr(self._client, name)

                wrapped_client = SendGridWrapper(client, from_email)
                return AbstractProviderInstance_SDK(wrapped_client)
            else:
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
