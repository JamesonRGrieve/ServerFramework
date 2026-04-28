"""
Email extension business logic - integrates the configured email providers
(SendGrid, Stalwart, SMTP2go) into the core Provider system.
"""

from lib.Environment import env
from lib.Logging import logger
from logic.AbstractLogicManager import HookTiming, hook_bll
from logic.BLL_Auth import InviteeManager

# Single source of truth for which email providers exist and how to detect
# whether each is configured. Each tuple is (provider_name, friendly_name,
# credential_env_var, from_email_env_var). Adding a new provider requires
# only an entry here plus the corresponding PRV_ class.
_EMAIL_PROVIDER_REGISTRY = (
    ("SendGrid", "SendGrid Email Service", "SENDGRID_API_KEY", "SENDGRID_FROM_EMAIL"),
    ("Stalwart", "Stalwart Mail Server", "STALWART_PASSWORD", "STALWART_FROM_EMAIL"),
    ("SMTP2go", "SMTP2go Email Service", "SMTP2GO_API_KEY", "SMTP2GO_FROM_EMAIL"),
)


def register_email_providers_hook():
    """Hook to register every configured email provider in the core Provider table."""
    providers_to_add = []
    for name, friendly_name, key_var, from_var in _EMAIL_PROVIDER_REGISTRY:
        if env(key_var) and env(from_var):
            providers_to_add.append({"name": name, "friendly_name": friendly_name})
            logger.debug(f"Registering {name} provider via email extension hook")
    return providers_to_add


def register_email_provider_instances_hook():
    """Hook to register a Root_<Provider> instance for every configured provider."""
    instances_to_add = []
    for name, _friendly, key_var, from_var in _EMAIL_PROVIDER_REGISTRY:
        key = env(key_var)
        from_email = env(from_var)
        if key and from_email:
            instances_to_add.append(
                {
                    "name": f"Root_{name}",
                    "_provider_name": name,  # Resolved to provider_id during seeding
                    "api_key": key,
                    "model_name": from_email,  # Carries from_email through the schema
                    "enabled": True,
                }
            )
            logger.debug(
                f"Registering Root_{name} provider instance via email extension hook"
            )
    return instances_to_add

    # @AbstractStaticExtension.db_hook("Seed", "Provider", "inject_seed_data", "before")
    # def inject_sendgrid_provider_hook(seed_list, model_class, session):
    #     """
    #     Inject SendGrid provider into the Provider seed list if SENDGRID_API_KEY is configured.
    #     """
    #     sendgrid_api_key = env("SENDGRID_API_KEY")

    #     if sendgrid_api_key and sendgrid_api_key != "":
    #         # Check if SendGrid provider is already in the seed list
    #         has_sendgrid = any(item.get("name") == "SendGrid" for item in seed_list)

    #         if not has_sendgrid:
    #             sendgrid_provider = {
    #                 "name": "SendGrid",
    #                 "friendly_name": "SendGrid Email Service",
    #                 "agent_settings_json": None,
    #                 "system": True,
    #             }
    #             seed_list.append(sendgrid_provider)
    #             logger.debug("Email extension injected SendGrid provider into seed data")
    #         else:
    #             logger.debug("SendGrid provider already exists in seed data")
    #     else:
    #         logger.debug(
    #             "SENDGRID_API_KEY not configured, skipping SendGrid provider injection"
    #         )

    # @AbstractStaticExtension.db_hook("Seed", "ProviderInstance", "inject_seed_data", "before")
    # def inject_sendgrid_instance_hook(seed_list, model_class, session):
    """
    Inject Root SendGrid provider instance into the ProviderInstance seed list
    if SENDGRID_API_KEY is configured.
    """
    sendgrid_api_key = env("SENDGRID_API_KEY")
    sendgrid_from_email = env("SENDGRID_FROM_EMAIL")

    if sendgrid_api_key and sendgrid_api_key != "":
        # Check if Root SendGrid instance is already in the seed list
        has_root_sendgrid = any(
            item.get("name") == "Root SendGrid"
            and item.get("_provider_name") == "SendGrid"
            for item in seed_list
        )

        if not has_root_sendgrid:
            root_sendgrid_instance = {
                "id": "FFFFFFFF-FFFF-FFFF-EEEE-FFFFFFFFFFFF",  # Consistent ID for Root SendGrid
                "name": "Root SendGrid",
                "_provider_name": "SendGrid",  # Will be resolved to provider_id by seed manager
                "model_name": None,
                "api_key": sendgrid_api_key,
                "enabled": True,
                "user_id": None,  # System-wide instance
                "team_id": None,  # System-wide instance
            }

            # Add from_email as a setting if configured
            if sendgrid_from_email and sendgrid_from_email != "":
                # Note: We'll handle settings in a separate hook since ProviderInstanceSetting
                # depends on ProviderInstance existing first
                pass

            seed_list.append(root_sendgrid_instance)
            logger.debug(
                "Email extension injected Root SendGrid instance into seed data"
            )
        else:
            logger.debug("Root SendGrid instance already exists in seed data")
    else:
        logger.debug(
            "SENDGRID_API_KEY not configured, skipping Root SendGrid instance injection"
        )


# @AbstractStaticExtension.db_hook(
#     "Seed", "ProviderInstanceSetting", "inject_seed_data", "before"
# )
# def inject_sendgrid_settings_hook(seed_list, model_class, session):
#     """
#     Inject SendGrid from_email setting for Root SendGrid instance if configured.
#     """
#     sendgrid_api_key = env("SENDGRID_API_KEY")
#     sendgrid_from_email = env("SENDGRID_FROM_EMAIL")

#     if (
#         sendgrid_api_key
#         and sendgrid_api_key != ""
#         and sendgrid_from_email
#         and sendgrid_from_email != ""
#     ):

#         # Check if from_email setting is already in the seed list for Root SendGrid
#         has_from_email_setting = any(
#             item.get("key") == "from_email"
#             and item.get("provider_instance_id")
#             == "FFFFFFFF-FFFF-FFFF-EEEE-FFFFFFFFFFFF"
#             for item in seed_list
#         )

#         if not has_from_email_setting:
#             from_email_setting = {
#                 "provider_instance_id": "FFFFFFFF-FFFF-FFFF-EEEE-FFFFFFFFFFFF",  # Root SendGrid ID
#                 "key": "from_email",
#                 "value": sendgrid_from_email,
#             }
#             seed_list.append(from_email_setting)
#             logger.debug(
#                 "Email extension injected SendGrid from_email setting into seed data"
#             )
#         else:
#             logger.debug("SendGrid from_email setting already exists in seed data")
#     else:
#         logger.debug(
#             "SENDGRID_FROM_EMAIL not configured, skipping from_email setting injection"
#         )


# def register_email_provider_extension_hook():
#     """
#     Register SendGrid as an extension provider after all base seeding is complete.
#     This ensures the email extension is properly linked to available extensions.
#     """

#     @AbstractStaticExtension.db_hook(
#         "Seed", "ProviderExtension", "inject_seed_data", "before"
#     )
#     def inject_sendgrid_provider_extension_hook(seed_list, model_class, session):
#         """
#         Inject SendGrid provider extension link to email extension.
#         """
#         sendgrid_api_key = env("SENDGRID_API_KEY")

#         if sendgrid_api_key and sendgrid_api_key != "":
#             # We need to find the extension ID for email extension
#             # For now, we'll use a known ID pattern or wait for the extension to be seeded

#             # Check if SendGrid provider extension is already in seed list
#             has_sendgrid_ext = any(
#                 item.get("provider_id") and item.get("extension_id")
#                 for item in seed_list
#                 # We'll need to implement proper ID resolution later
#             )

#             logger.debug(
#                 "SendGrid provider extension injection - extension registration needed"
#             )
#             # This will be implemented when we have extension IDs available

#     return inject_sendgrid_provider_extension_hook


# # Initialize hooks
# register_email_provider_extension_hook()


def _is_sendgrid_configured() -> bool:
    """Check if SendGrid is properly configured."""
    api_key = env("SENDGRID_API_KEY")
    from_email = env("SENDGRID_FROM_EMAIL")
    return bool(api_key and api_key != "" and from_email and from_email != "")


def send_invitation_email_hook(manager, entity, create_args=None):
    """
    Hook to send invitation emails after an invitation is created.
    Only sends email if email extension is available and SendGrid is configured.
    """
    try:
        # Check if SendGrid is configured
        if not _is_sendgrid_configured():
            logger.debug("SendGrid not configured, skipping invitation email")
            return

        # Extract invitation data
        if hasattr(entity, "invitation") and entity.invitation.code:
            # This is a code-based invitation
            # invitation_link = (
            #     f"{env('APP_URI')}/?invitation_code={entity.code}?email={entity.email}"
            # )

            # Look for invitation invitees to get email addresses
            # if hasattr(manager, "Invitee_manager"):
            # invitee_manager = manager.Invitee_manager
            try:

                # invitees = manager.list(invitation_id=entity.id)

                # for invitee in invitees:
                if hasattr(entity, "email") and entity.email:
                    # Send invitation email asynchronously
                    import asyncio

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    from extensions.email.EXT_EMail import EXT_EMail

                    ext_instance = EXT_EMail()

                    base_url = env("APP_URI")
                    invitation_link = (
                        f"{base_url}?code={entity.invitation.code}&email={entity.email}"
                    )
                    logger.debug(
                        "Initialized EXT_EMail instance for invitation email"
                        f" link : {invitation_link}"
                    )

                    # get rotation manager for the email's specific rotation
                    try:
                        from logic.BLL_Providers import RotationManager

                        email_rotation_manager = RotationManager(
                            requester_id=manager.requester.id,
                            model_registry=getattr(manager, "model_registry", None),
                        )

                        rotation = email_rotation_manager.get(name="Root_Email")
                        email_rotation_manager.target_id = rotation.id

                        EXT_EMail.root = email_rotation_manager
                    except Exception as e:
                        logger.error(f"Failed to set up email rotation manager: {e}")

                    ext_instance.send_invitation_email(
                        entity=entity,
                        email=entity.email,
                        invitation_link=invitation_link,
                        team_name=(
                            getattr(entity.invitation, "team", {}).name
                            if hasattr(entity.invitation, "team")
                            else "Team"
                        ),
                        inviter_name="Team Administrator",
                    )
                    logger.debug(f"Invitation email sent to {entity.email}")

            except Exception as e:
                logger.error(f"Error sending invitation emails: {e}")

    except Exception as e:
        logger.error(f"Error in invitation email hook: {e}")


hook_bll(InviteeManager.create, timing=HookTiming.AFTER, priority=5)(
    send_invitation_email_hook
)
