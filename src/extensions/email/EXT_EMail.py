"""
Email extension for AGInfrastructure.

Provides email abilities including SendGrid, Gmail, Microsoft Outlook, Mailgun,
Yahoo, POP3, and IMAP support. This extension provides static functionality and
metadata to organize email-related components and manage email provider instances.

The extension focuses on:
- Email sending and receiving abilities
- Email template management
- Email tracking and delivery status
- Provider instance management for email services
- Integration hooks for authentication workflows

Component loading (DB, BLL, EP) is handled automatically by the import system
based on file naming conventions.
"""

from abc import abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set, Type

from extensions.AbstractExtensionProvider import (
    AbstractProviderInstance,
    AbstractStaticExtension,
    AbstractStaticProvider,
    ability,
)
from lib.Dependencies import Dependencies, PIP_Dependency
from lib.Environment import env
from lib.Logging import logger
from lib.Pydantic import classproperty
from logic.BLL_Providers import ProviderInstanceModel


class AbstractEmailProvider(AbstractStaticProvider):
    """Abstract base class for email service providers."""

    extension: ClassVar[Optional[Type[AbstractStaticExtension]]] = None
    extension_type: ClassVar[str] = "email"

    @classmethod
    @abstractmethod
    def services(cls) -> List[str]:
        """Return a list of services provided by this provider."""

    @classmethod
    def get_extension_info(cls) -> Dict[str, Any]:
        """Get information about the email extension."""

        friendly_name = (
            getattr(cls.extension, "friendly_name", "Email")
            if cls.extension
            else "Email"
        )
        return {
            "name": friendly_name,
            "description": f"Email extension for {cls.get_platform_name()}",
        }

    @staticmethod
    @abstractmethod
    @ability(name="email_get")
    async def get_emails(
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        page_size: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get emails from a specified folder."""

    @classmethod
    @abstractmethod
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
        """Send an email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_draft")
    async def create_draft_email(
        provider_instance: ProviderInstanceModel,
        recipient: str,
        subject: str,
        body: str,
        attachments: Optional[List[str]] = None,
        importance: str = "normal",
    ) -> str:
        """Create a draft email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_search")
    async def search_emails(
        provider_instance: ProviderInstanceModel,
        query: str,
        folder_name: str = "Inbox",
        max_emails: int = 10,
        date_range: Optional[tuple] = None,
    ) -> List[Dict[str, Any]]:
        """Search for emails in a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_reply")
    async def reply_to_email(
        provider_instance: ProviderInstanceModel,
        message_id: str,
        body: str,
        attachments: Optional[List[str]] = None,
    ) -> str:
        """Reply to a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_delete")
    async def delete_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Delete a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_move")
    async def move_email(
        provider_instance: ProviderInstanceModel, message_id: str, folder_name: str
    ) -> str:
        """Move a specific email to a different folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_mark_read")
    async def mark_email_as_read(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Mark a specific email as read."""

    @staticmethod
    @abstractmethod
    @ability(name="email_mark_unread")
    async def mark_email_as_unread(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Mark a specific email as unread."""

    @staticmethod
    @abstractmethod
    @ability(name="email_flag")
    async def flag_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Flag a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_unflag")
    async def unflag_email(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> str:
        """Remove flag from a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_threads")
    async def get_email_threads(
        provider_instance: ProviderInstanceModel,
        folder_name: str = "Inbox",
        max_threads: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get email threads from a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_thread_messages")
    async def get_thread_messages(
        provider_instance: ProviderInstanceModel, thread_id: str
    ) -> List[Dict[str, Any]]:
        """Get all messages from a specific email thread."""

    @staticmethod
    @abstractmethod
    @ability(name="email_latest")
    async def get_latest_email(
        provider_instance: ProviderInstanceModel, folder_name: str = "Inbox"
    ) -> Dict[str, Any]:
        """Get the latest email from a specified folder."""

    @staticmethod
    @abstractmethod
    @ability(name="email_attachment")
    async def download_attachment(
        provider_instance: ProviderInstanceModel, message_id: str, attachment_id: str
    ) -> bytes:
        """Download an attachment from a specific email."""

    @staticmethod
    @abstractmethod
    @ability(name="email_attachments")
    async def process_attachments(
        provider_instance: ProviderInstanceModel, message_id: str
    ) -> List[str]:
        """Download attachments from a specific email."""

    @classmethod
    @abstractmethod
    def get_platform_name(cls) -> str:
        """Get the name of the email platform this provider interacts with."""


class EXT_EMail(AbstractStaticExtension):
    """
    Email extension for AGInfrastructure.

    This extension provides:
    - Meta abilities for email service management
    - Abstract provider interface defining email operations
    - Integration with various email service providers
    """

    # Extension metadata
    name: ClassVar[str] = "email"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Email extension for interacting with various email providers"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "SENDGRID_API_KEY": "",
        "SENDGRID_FROM_EMAIL": "",
        "EMAIL_PROVIDER": "sendgrid",
        "SMTP_SERVER": "",
        "SMTP_PORT": "587",
        "IMAP_SERVER": "",
        "IMAP_PORT": "993",
    }

    # Unified dependencies using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="sendgrid",
                friendly_name="SendGrid Python Library",
                semver=">=6.10.0",
                reason="SendGrid email provider support",
            )
        ]
    )

    # Meta abilities - extension-level abilities that work regardless of provider
    _abilities: ClassVar[Set[str]] = {
        "email_status",  # Meta ability to check email service status
        "email_config",  # Meta ability to manage email configuration
        "email_send",  # Send email ability
        "email_receive",  # Receive email ability
        "email_templates",  # Email template management
        "email_tracking",  # Email tracking and delivery status
    }

    @classproperty
    def pip_dependencies(cls):
        """Get PIP dependencies for backward compatibility."""
        return cls.dependencies.pip

    @classproperty
    def ext_dependencies(cls):
        """Get extension dependencies for backward compatibility."""
        return cls.dependencies.ext

    @classproperty
    def sys_dependencies(cls):
        """Get system dependencies for backward compatibility."""
        return cls.dependencies.sys

    @classmethod
    def get_abilities(cls) -> Set[str]:
        """Return the abilities this extension provides."""
        abilities = cls._abilities.copy()

        # Add provider-specific abilities
        for provider_class in cls.providers:
            if hasattr(provider_class, "_abilities"):
                abilities.update(provider_class._abilities)

        return abilities

    @classmethod
    def has_ability(cls, ability: str) -> bool:
        """Check if this extension has a specific ability."""
        return ability in cls.get_abilities()

    @classmethod
    def register_ability(cls, ability: str):
        """Register a new ability with this extension."""
        cls._abilities.add(ability)

    @classmethod
    def get_registered_abilities(cls) -> Set[str]:
        """Get all registered abilities."""
        return cls._abilities.copy()

    @classmethod
    def get_providers(cls) -> Set[str]:
        """Get available email providers."""
        return {"sendgrid", "gmail", "outlook"}

    @classmethod
    def get_provider_class(cls, provider_name: str):
        """Get provider class by name."""
        if provider_name == "sendgrid":
            from extensions.email.PRV_SendGrid_EMail import SendgridProvider

            return SendgridProvider
        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    @classmethod
    def discover_abilities(cls):
        """Discover abilities from provider classes."""
        # This method would scan for abilities in provider classes
        # For now, we'll just ensure basic abilities are registered
        cls.register_ability("send_email")
        cls.register_ability("send_invitation_email")
        cls.register_ability("get_emails")
        cls.register_ability("create_draft_email")
        cls.register_ability("search_emails")
        cls.register_ability("reply_to_email")
        cls.register_ability("delete_email")
        cls.register_ability("process_attachments")

    @classmethod
    async def execute_ability(cls, ability_name: str, params: dict = None) -> str:
        """Execute an ability by name."""
        if ability_name not in cls.get_abilities():
            return f"Ability '{ability_name}' not found"

        # This would route to the appropriate provider method
        # For now, return a placeholder response
        return f"Executed ability: {ability_name}"

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate extension configuration and return list of issues."""
        issues = []

        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        if not _env("SENDGRID_API_KEY"):
            issues.append("SENDGRID_API_KEY environment variable not set")

        if not _env("SENDGRID_FROM_EMAIL"):
            issues.append("SENDGRID_FROM_EMAIL environment variable not set")

        return issues

    @classmethod
    def get_required_permissions(cls) -> List[str]:
        """Get required permissions for this extension."""
        return [
            "email:send",
            "email:receive",
            "email:manage_templates",
            "email:track_delivery",
        ]

    # Abstract provider is defined in AbstractProvider_EMail.py with extension_type = "email"

    @classmethod
    @ability(name="email_status")
    def get_extension_status(cls) -> Dict[str, Any]:
        """
        Get the current status of the email extension.
        This is a meta ability that works regardless of provider.
        """
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        return {
            "extension": cls.name,
            "version": cls.version,
            "providers_available": len(cls.providers),
            "configured": bool(_env("SENDGRID_API_KEY")),
            "default_provider": _env("EMAIL_PROVIDER") or "sendgrid",
        }

    @classmethod
    @ability(name="email_config")
    def get_configuration(cls) -> Dict[str, Any]:
        """
        Get the current email configuration.
        This is a meta ability that works regardless of provider.
        """
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        return {
            "email_provider": _env("EMAIL_PROVIDER") or "sendgrid",
            "smtp_server": _env("SMTP_SERVER"),
            "smtp_port": _env("SMTP_PORT") or "587",
            "imap_server": _env("IMAP_SERVER"),
            "imap_port": _env("IMAP_PORT") or "993",
            "from_email_configured": bool(_env("SENDGRID_FROM_EMAIL")),
        }

    @classmethod
    def validate_configuration(cls) -> bool:
        """
        Validate that the email extension is properly configured.
        """
        # Check for required environment variables
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        email_provider = _env("EMAIL_PROVIDER") or "sendgrid"

        if email_provider == "sendgrid":
            return bool(_env("SENDGRID_API_KEY") and _env("SENDGRID_FROM_EMAIL"))
        elif email_provider in ["smtp", "imap"]:
            return bool(_env("SMTP_SERVER") or _env("IMAP_SERVER"))

        return False

    @classmethod
    def get_default_provider_name(cls) -> str:
        """Get the default email provider name from configuration."""
        # Import env at call time so tests that patch lib.Environment.env are respected
        from lib.Environment import env as _env

        return _env("EMAIL_PROVIDER") or "sendgrid"

    @classmethod
    def get_provider_names(cls) -> List[str]:
        """Get list of provider names."""
        return [
            provider.name for provider in cls.providers if hasattr(provider, "name")
        ]

    @classproperty
    def env(cls) -> Dict[str, Any]:
        """Get environment variables."""
        return cls._env

    # Static methods for rotation system integration
    @classmethod
    async def send_email(cls, recipient: str, subject: str, body: str, **kwargs) -> str:
        """Send email via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.send_email,
                recipient=recipient,
                subject=subject,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def get_emails(
        cls, folder_name: str = "Inbox", max_emails: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Get emails via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.get_emails,
                folder_name=folder_name,
                max_emails=max_emails,
                **kwargs,
            )
        return []

    @classmethod
    async def create_draft_email(
        cls, recipient: str, subject: str, body: str, **kwargs
    ) -> str:
        """Create draft email via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.create_draft_email,
                recipient=recipient,
                subject=subject,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def search_emails(
        cls, query: str, folder_name: str = "Inbox", max_emails: int = 10, **kwargs
    ) -> List[Dict[str, Any]]:
        """Search emails via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.search_emails,
                query=query,
                folder_name=folder_name,
                max_emails=max_emails,
                **kwargs,
            )
        return []

    @classmethod
    async def reply_to_email(cls, email_id: str, body: str, **kwargs) -> str:
        """Reply to email via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.reply_to_email,
                email_id=email_id,
                body=body,
                **kwargs,
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def delete_email(cls, email_id: str, **kwargs) -> str:
        """Delete email via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.delete_email, email_id=email_id, **kwargs
            )
        return "Email extension not configured for rotation"

    @classmethod
    async def process_attachments(cls, email_id: str, **kwargs) -> List[Dict[str, Any]]:
        """Process email attachments via rotation system."""
        if cls.root:
            return await cls.root.rotate(
                AbstractEmailProvider.process_attachments, email_id=email_id, **kwargs
            )
        return []

    @classmethod
    @AbstractStaticExtension.hook("bll", "invitations", "invitation", "create", "after")
    def send_invitation_email(cls, entity, **kwargs):
        """
        Hook to send invitation email after invitation is created.
        This demonstrates how extensions can hook into core functionality.
        """
        try:
            # Use the rotation system to send the email
            if cls.root:
                result = cls.root.rotate(
                    provider_callback,
                    recipient=entity.email,
                    subject=f"You've been invited to {entity.invitation.team.name}",
                    body=f"""You've been invited to join {entity.invitation.team.name}.
                    
                    Click here to accept: {env('FRONTEND_URL')}/accept-invitation?code={entity.invitation.code}&email={entity.email}&team={entity.invitation.team.name}
                    
                    This invitation expires in 7 days.""",
                )
                logger.info(f"Invitation email sent to {entity.email}")
                return result
        except Exception as e:
            logger.error(f"Failed to send invitation email: {e}")
            # Don't fail the invitation creation if email fails
            pass


def provider_callback(provider_instance, **kwargs):
    """
    Callback method for provider instances.
    This can be used to handle provider-specific logic.
    """

    providers = EXT_EMail.providers
    for provider in providers:
        if provider.name.lower() == provider_instance.model_name.lower():
            coro = provider.send_email(provider_instance, **kwargs)

            call_async_without_waiting(coro)

    logger.debug(f"Provider callback called for {provider_instance.name}")
    # Implement provider-specific logic here
    return {"status": "success", "message": "Callback executed successfully"}


def run_async_in_thread(coroutine):
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coroutine)


def call_async_without_waiting(async_function):
    import threading

    thread = threading.Thread(target=run_async_in_thread, args=(async_function,))
    thread.daemon = (
        True  # Allows the program to exit even if the thread is still running
    )
    thread.start()


AbstractEmailProvider.extension = EXT_EMail
