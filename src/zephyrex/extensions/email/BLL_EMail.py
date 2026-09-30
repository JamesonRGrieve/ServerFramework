# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Email extension business logic - integrates the configured email providers
(SendGrid, Stalwart, SMTP2go) into the core Provider system.
"""

import os

from zephyrex.lib.Environment import env
from zephyrex.lib.Logging import logger


# Item 90 — provider discovery is now driven by each PRV_ class's typed
# ``Settings`` inner model rather than a hardcoded tuple in this module.
# We resolve provider classes lazily so the hooks remain importable even
# before the email extension's provider modules have been loaded.
def _email_provider_classes():
    """Return loaded email-provider classes (lazy import to avoid cycles)."""
    try:
        from zephyrex.extensions.email.EXT_EMail import EXT_EMail
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug(f"EXT_EMail import failed in BLL_EMail: {exc}")
        return []
    return list(EXT_EMail.providers)


def _provider_friendly_name(provider_cls) -> str:
    return (  # type: ignore[no-any-return]
        getattr(provider_cls, "description", None)
        or getattr(provider_cls, "friendly_name", None)
        or getattr(provider_cls, "name", provider_cls.__name__)
    )


def _provider_display_name(provider_cls) -> str:
    """Stable, human-readable display name used as the seed-row name."""
    name = getattr(provider_cls, "name", provider_cls.__name__)
    aliases = {"sendgrid": "SendGrid", "stalwart": "Stalwart", "smtp2go": "SMTP2go"}
    return aliases.get(str(name).lower(), str(name))


def register_email_providers_hook():
    """Hook to register every configured email provider in the core Provider table.

    Iterates loaded provider classes whose ``Settings.is_configured(os.environ)``
    is True; replaces the legacy hardcoded ``_EMAIL_PROVIDER_REGISTRY`` tuple.
    """
    providers_to_add = []
    env_map = os.environ
    for provider_cls in _email_provider_classes():
        settings_cls = getattr(provider_cls, "Settings", None)
        if settings_cls is None:
            continue
        try:
            ok = settings_cls.is_configured(env_map)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Settings.is_configured raised: {exc}")
            continue
        if not ok:
            continue
        display = _provider_display_name(provider_cls)
        providers_to_add.append(
            {
                "name": display,
                "friendly_name": _provider_friendly_name(provider_cls),
            }
        )
        logger.debug(f"Registering {display} provider via email extension hook")
    return providers_to_add


def register_email_provider_instances_hook():
    """Hook to register a Root_<Provider> instance for every configured provider."""
    instances_to_add = []
    env_map = os.environ
    for provider_cls in _email_provider_classes():
        settings_cls = getattr(provider_cls, "Settings", None)
        if settings_cls is None:
            continue
        try:
            if not settings_cls.is_configured(env_map):
                continue
            settings = settings_cls.from_env(env_map)
        except Exception as exc:  # noqa: BLE001 — surfaced in startup banner
            logger.debug(f"Could not build Settings for {provider_cls.__name__}: {exc}")
            continue

        # Use SecretStr's get_secret_value when available; the legacy seed
        # row carried the raw API key in the `api_key` column.
        secret = None
        for cred_field in ("api_key", "password"):
            value = getattr(settings, cred_field, None)
            if value is None:
                continue
            secret = (
                value.get_secret_value()
                if hasattr(value, "get_secret_value")
                else value
            )
            break
        if secret is None:
            # Fall back to legacy env-var lookup so providers without an
            # api_key/password (none of the current ones) still seed.
            secret = env(getattr(settings_cls, "_env_field_map", {}).get("api_key", ""))

        from_email = str(getattr(settings, "from_email", ""))
        display = _provider_display_name(provider_cls)
        instances_to_add.append(
            {
                "name": f"Root_{display}",
                "_provider_name": display,
                "api_key": secret,
                "model_name": from_email,
                "enabled": True,
            }
        )
        logger.debug(
            f"Registering Root_{display} provider instance via email extension hook"
        )
    return instances_to_add


def _is_sendgrid_configured() -> bool:
    """Check if SendGrid is properly configured."""
    api_key = env("SENDGRID_API_KEY")
    from_email = env("SENDGRID_FROM_EMAIL")
    return bool(api_key and api_key != "" and from_email and from_email != "")


def send_invitation_email_hook(manager, entity):
    """
    Send the invitation email for a newly added invitee; called by
    ``InvitationManager.add_invitee``. Only sends when SendGrid is configured.
    """
    try:
        # Check if SendGrid is configured
        if not _is_sendgrid_configured():
            logger.debug("SendGrid not configured, skipping invitation email")
            return

        # Extract invitation data
        if hasattr(entity, "invitation") and entity.invitation.code:
            try:
                if hasattr(entity, "email") and entity.email:
                    from zephyrex.extensions.email.EXT_EMail import EXT_EMail

                    ext_instance = EXT_EMail()

                    base_url = env("APP_URI")
                    invitation_link = (
                        f"{base_url}?code={entity.invitation.code}&email={entity.email}"
                    )
                    logger.debug(
                        "Initialized EXT_EMail instance for invitation email"
                        f" link : {invitation_link}"
                    )

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
