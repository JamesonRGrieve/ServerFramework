from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)
from zephyrex.lib.Dependencies import Dependencies, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_Auth_MFA(AbstractStaticExtension):
    """
    Multi-Factor Authentication extension for AGInfrastructure.

    Provides comprehensive MFA capabilities including TOTP, Email, and SMS-based
    multi-factor authentication. This extension integrates with the authentication
    system to add additional security layers.

    The extension provides:
    - TOTP (Time-based One-Time Password) generation and verification
    - Email-based MFA code delivery
    - SMS-based MFA (when SMS provider available)
    - Recovery code generation and management
    - MFA method management per user
    - Integration hooks for authentication workflows

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name: ClassVar[str] = "auth_mfa"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "Multi-Factor Authentication extension with TOTP, Email, and SMS support"
    )

    # Environment variables that this extension needs
    _env: ClassVar[Dict[str, Any]] = {
        "MFA_ENABLED": "true",
        "MFA_ISSUER_NAME": "AGInfrastructure",
        "MFA_RECOVERY_CODES_COUNT": "10",
        "MFA_TOTP_WINDOW": "1",  # Number of time windows to check for TOTP
    }

    # Unified dependencies using the Dependencies class
    dependencies: ClassVar[Dependencies] = Dependencies(
        [
            PIP_Dependency(
                name="pyotp",
                friendly_name="PyOTP - Python One-Time Password Library",
                optional=False,
                reason="Required for TOTP (Time-based One-Time Password) functionality",
                semver=">=2.8.0",
            ),
            PIP_Dependency(
                name="qrcode[pil]",
                friendly_name="QRCode Python Library with PIL support",
                optional=True,
                semver=">=7.4.0",
                reason="QR code generation for TOTP setup",
            ),
        ]
    )

    # Static abilities provided by this extension
    _abilities: ClassVar[Set[str]] = {
        "mfa_totp",
        "mfa_email",
        "mfa_sms",
        "mfa_recovery_codes",
    }

    # No __init__ needed for static extension

    # MFA doesn't use external providers - override providers property
    _providers: ClassVar[List] = []

    @classmethod
    def on_initialize(cls) -> bool:
        """Initialize the MFA extension.

        Refuses to come up if encryption-at-rest is not configured. The TOTP
        seed is the keystone of the second factor; persisting it in the
        clear would silently invalidate every MFA guarantee. Operators in
        local/CI/dev who need to skip this step can set
        ``ALLOW_PLAINTEXT_SECRETS=true``; production/staging cannot.
        """
        logger.debug("Initializing MFA Extension...")

        from zephyrex.lib.SecretEncryption import (
            MissingFernetKeyError,
            assert_encryption_available,
        )

        try:
            assert_encryption_available()
        except MissingFernetKeyError as e:
            logger.error(f"MFA refusing to initialize: {e}")
            return False

        try:
            import pyotp  # noqa: F401
        except ImportError:
            logger.error("PyOTP library is required for MFA but is not installed")
            return False

        logger.debug("MFA extension initialized successfully")
        return True

    @classmethod
    def on_start(cls) -> bool:
        """Start the MFA extension."""
        try:
            logger.debug("MFA extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start MFA extension: {e}")
            return False

    @classmethod
    def on_stop(cls) -> bool:
        """Stop the MFA extension."""
        try:
            logger.debug("MFA extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Error stopping MFA extension: {e}")
            return False

    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate the extension configuration."""
        issues: List[str] = []

        try:
            import pyotp  # noqa: F401
        except ImportError:
            issues.append(
                "PyOTP library not installed - TOTP functionality will not work. "
                "Run: pip install pyotp"
            )

        from zephyrex.lib.SecretEncryption import (
            MissingFernetKeyError,
            assert_encryption_available,
        )

        try:
            assert_encryption_available()
        except MissingFernetKeyError as e:
            issues.append(str(e))

        import os

        if not os.getenv("MFA_ISSUER_NAME"):
            issues.append(
                "MFA_ISSUER_NAME environment variable not set - using default 'AGInfrastructure'"
            )

        return issues
