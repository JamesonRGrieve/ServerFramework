"""
Business Logic Layer for SendGrid email resources.
Provides managers for contacts, templates, and campaigns.
"""

from typing import Optional

from serverframework.extensions.AbstractExternalModel import AbstractExternalManager
from serverframework.extensions.email.PRV_SendGrid_EMail import (
    SendGrid_CampaignModel,
    SendGrid_CampaignReferenceModel,
    SendGrid_ContactModel,
    SendGrid_ContactReferenceModel,
    SendGrid_TemplateModel,
    SendGrid_TemplateReferenceModel,
)
from serverframework.lib.Logging import logger


# ============================================================================
# SendGrid Contact Manager
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


# ============================================================================
# SendGrid Template Manager
# ============================================================================


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


# ============================================================================
# SendGrid Campaign Manager
# ============================================================================


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
