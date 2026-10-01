from typing import Any, Callable, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty


class _AbilityDispatchSet(set):
    """A ``Set[str]`` of ability names that also resolves each name to its
    implementing callable via subscription.

    EXT_SMS's ``abilities`` attribute has to satisfy two independent
    contracts at once: the framework's structural contract (``Set[str]``,
    see ``AbstractStaticExtensionSystemComponent.abilities`` /
    ``get_abilities``) and this extension's own dispatch contract
    (``abilities[name]`` resolves to the ability's callable, used by
    ``execute_ability``). Subclassing ``set`` lets both hold true for the
    same object rather than picking one representation and breaking the
    other's callers. Mirrors EXT_Messaging._AbilityDispatchSet.
    """

    def __init__(self, dispatch: Dict[str, Callable]) -> None:
        super().__init__(dispatch.keys())
        self._dispatch = dispatch

    def __getitem__(self, name: str) -> Callable:
        return self._dispatch[name]


class EXT_SMS(AbstractStaticExtension):
    """
    SMS messaging extension for AGInfrastructure.

    Provides SMS messaging capabilities through various SMS providers
    (currently Twilio and Amazon SNS). This extension enables sending SMS
    messages, tracking delivery status, and bulk SMS operations.

    Component loading (DB, BLL, EP) is handled automatically by the import
    system based on file naming conventions.
    """

    # Extension metadata
    name = "sms"
    version = "1.0.0"
    description = "SMS messaging extension"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="labels",
            friendly_name="Labels Extension",
            optional=True,
            reason="Optional labels for SMS categorization",
        )
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="twilio",
            friendly_name="Twilio Python Library",
            optional=True,
            semver=">=8.0.0",
            reason="Twilio SMS provider support",
        ),
        PIP_Dependency(
            name="boto3",
            friendly_name="AWS SDK for Python (boto3)",
            optional=True,
            semver=">=1.26.0",
            reason="Amazon SNS SMS provider support",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "send_sms",
        "sms_delivery_status",
        "sms_management",
        "bulk_sms",
    ]

    # Define database tables
    db_tables: List[Any] = []

    # Meta abilities this extension exposes. Declared as a fresh set (not
    # relying on the base class's shared default) so registering an
    # ability here can never leak into — or be polluted by — a sibling
    # extension's `_abilities`. Mirrors EXT_Messaging's pattern.
    _abilities: ClassVar[Set[str]] = {
        "send_sms",
        "get_sms_status",
        "send_bulk_sms",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

        # Free-form settings bag (provider_type, api_key, per-provider
        # config, ...). Stored per-instance so constructing several
        # extensions with different settings never cross-contaminates.
        self.settings: Dict[str, Any] = dict(kwargs)

        # Provider instance reference
        self.provider: Optional[Any] = None

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can
        # never leak into the class default (and therefore into sibling
        # instances) — mirrors EXT_Automotive/EXT_Messaging.
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """
        Initialize the SMS extension.
        """
        logger.debug("Initializing SMS Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Register capabilities
            for capability in (
                "send_sms",
                "sms_delivery_status",
                "sms_management",
                "bulk_sms",
            ):
                self.register_capability(capability)

            logger.debug("SMS extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize SMS extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """
        Create the appropriate SMS provider instance based on the
        configured ``provider_type`` setting.
        """
        provider_type = self.settings.get("provider_type", "") if self.settings else ""

        # Pull out the settings the provider ABC consumes explicitly so the
        # remainder (account_sid, from_number, aws_region, ...) can be
        # forwarded as **kwargs without a duplicate-keyword collision.
        provider_kwargs = dict(self.settings)
        provider_kwargs.pop("provider_type", None)
        api_key = provider_kwargs.pop("api_key", "")
        conversation_directory = provider_kwargs.pop("conversation_id", "")

        try:
            if provider_type == "twilio":
                from zephyrex.extensions.sms.PRV_Twilio import TwilioProvider

                self.provider = TwilioProvider(
                    api_key=api_key,
                    extension_id=self.name,
                    conversation_directory=conversation_directory,
                    **provider_kwargs,
                )
                logger.debug("SMS provider for twilio created successfully")
            elif provider_type == "amazon":
                from zephyrex.extensions.sms.PRV_Amazon import AmazonSNSProvider

                self.provider = AmazonSNSProvider(
                    api_key=api_key,
                    extension_id=self.name,
                    conversation_directory=conversation_directory,
                    **provider_kwargs,
                )
                logger.debug("SMS provider for amazon created successfully")
            else:
                logger.warning(
                    f"No SMS provider configured for type: {provider_type!r}"
                )
                self.provider = None

        except ImportError as e:
            logger.warning(f"Could not import SMS provider for {provider_type}: {e}")
            self.provider = None
        except Exception as e:
            logger.error(f"Error creating SMS provider: {str(e)}")
            self.provider = None

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    @ability("send_sms")
    async def send_sms(self, phone_number: str, message: str) -> str:
        """
        Send an SMS message to a phone number.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            # Provider methods are synchronous (see PRV_SMS) — no await.
            result = self.provider.send_sms(phone_number, message)
            return f"SMS sent to {phone_number}: {result}"
        except Exception as e:
            return f"Failed to send SMS: {str(e)}"

    @ability("get_sms_status")
    async def get_sms_status(self, message_id: str) -> Dict[str, Any]:
        """
        Get the delivery status of an SMS message.
        """
        if not self.provider:
            return {"error": await self._no_provider_warning()}

        try:
            if hasattr(self.provider, "get_sms_status"):
                return self.provider.get_sms_status(message_id)
            else:
                return {"error": "SMS status checking not supported by this provider"}
        except Exception as e:
            return {"error": f"Failed to get SMS status: {str(e)}"}

    @ability("send_bulk_sms")
    async def send_bulk_sms(
        self, phone_numbers: List[str], message: str
    ) -> List[Dict[str, Any]]:
        """
        Send SMS messages to multiple phone numbers.
        """
        if not self.provider:
            return [{"error": await self._no_provider_warning()}]

        try:
            results = []
            for phone_number in phone_numbers:
                result = await self.send_sms(phone_number, message)
                results.append({"phone_number": phone_number, "status": result})
            return results
        except Exception as e:
            return [{"error": f"Failed to send bulk SMS: {str(e)}"}]

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Return a warning message when no provider is configured."""
        return "SMS provider not configured. Please check your configuration."

    @classproperty
    def abilities(cls) -> _AbilityDispatchSet:
        """Ability name -> implementing callable dispatch table.

        Overrides the framework's Set[str]-based classproperty (see
        AbstractStaticExtensionSystemComponent.abilities): EXT_SMS's
        `execute_ability` needs to resolve a name directly to its
        implementation, while framework internals (`get_abilities`,
        generic extension tests) still expect a `Set[str]`. See
        `_AbilityDispatchSet`.
        """
        return _AbilityDispatchSet(
            {
                "send_sms": cls.send_sms,
                "get_sms_status": cls.get_sms_status,
                "send_bulk_sms": cls.send_bulk_sms,
            }
        )

    def discover_abilities(self) -> None:
        """Abilities are statically declared on this extension; this simply
        (re)affirms the registered ability names, mirroring
        EXT_Messaging.discover_abilities for API parity with
        dynamic-discovery extensions."""
        for ability_name in self.abilities:
            self.register_ability(ability_name)

    def register_ability(self, ability_name: str) -> None:
        """Register a new ability name with this extension."""
        type(self)._abilities.add(ability_name)

    async def execute_ability(
        self, ability_name: str, params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Execute a registered ability by name with the given kwargs."""
        if ability_name not in self.abilities:
            return f"Ability '{ability_name}' not found"

        method = getattr(self, ability_name)
        return await method(**(params or {}))

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "sms:send",
            "sms:status",
            "sms:manage",
            "communication:sms",
        ]

    def on_start(self) -> bool:
        """
        Start the SMS extension.
        """
        try:
            logger.debug("SMS extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start SMS extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the SMS extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("SMS extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping SMS extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        provider_type = self.settings.get("provider_type", "") if self.settings else ""
        if not provider_type:
            issues.append("SMS provider type not specified")

        return issues

    def on_startup(self) -> None:
        """
        Called during application startup.
        """
        logger.debug("SMS extension startup hook called")

    def on_shutdown(self) -> None:
        """
        Called during application shutdown.
        """
        logger.debug("SMS extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
