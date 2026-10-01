from typing import Any, Callable, ClassVar, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    AbstractStaticExtensionMeta,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger
from zephyrex.pydantic2.registry import classproperty


class _EXTMessagingMeta(AbstractStaticExtensionMeta):
    """Metaclass for EXT_Messaging that makes clearing the inherited
    ``_root_rotation_cache`` idempotent.

    ``AbstractStaticExtension.root`` (AbstractExtensionProvider.py)
    declares ``_root_rotation_cache = None`` on *itself* and only ever
    writes to a subclass's own ``__dict__`` on a *successful* rotation
    lookup (``cls._root_rotation_cache = rotation_manager``). Until that
    first success — which never happens in an isolated extension test
    environment with no persisted ``Root_messaging`` rotation row —
    ``EXT_Messaging`` never gets its own ``_root_rotation_cache`` entry.

    The shared ``AbstractEXTTest`` framework's performance tests clear
    the cache with a guard-then-delete: ``hasattr(cls,
    "_root_rotation_cache")`` (True, resolved via inheritance) followed
    by ``delattr(cls, "_root_rotation_cache")``. Class attribute deletion
    is dispatched through the metaclass and only ever touches the
    class's *own* ``__dict__``, so it raises ``AttributeError`` whenever
    the attribute lives on a base class instead. That crash is
    unconditional and would recur on every clear-cache call for as long
    as the lookup keeps failing (i.e. for the whole test run) — a single
    class-body default only survives one such call before it, too, is
    deleted away.

    Deleting an attribute that is absent from this class's own
    namespace is a no-op here, matching the guard's intent ("clear the
    cache if present") without touching AbstractEXTTest.py or
    AbstractExtensionProvider.py, both outside extensions/messaging/.
    """

    def __delattr__(cls, name: str) -> None:
        if name == "_root_rotation_cache" and name not in cls.__dict__:
            return
        super().__delattr__(name)


class _AbilityDispatchSet(set):
    """A ``Set[str]`` of ability names that also resolves each name to its
    implementing callable via subscription.

    EXT_Messaging's ``abilities`` attribute has to satisfy two independent
    contracts at once: the framework's structural contract (``Set[str]``,
    see ``AbstractStaticExtensionSystemComponent.abilities`` /
    ``get_abilities``) and this extension's own dispatch contract
    (``abilities[name]`` resolves to the ability's callable, used by
    ``execute_ability``). Subclassing ``set`` lets both hold true for the
    same object rather than picking one representation and breaking the
    other's callers.
    """

    def __init__(self, dispatch: Dict[str, Callable]) -> None:
        super().__init__(dispatch.keys())
        self._dispatch = dispatch

    def __getitem__(self, name: str) -> Callable:
        return self._dispatch[name]


class EXT_Messaging(AbstractStaticExtension, metaclass=_EXTMessagingMeta):
    """
    Messaging extension for AGInfrastructure.

    Provides functionality for sending, receiving, and managing messages across
    various messaging platforms. This extension supports multiple messaging
    providers and protocols.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "messaging"
    version = "1.0.0"
    description = "Messaging functionality for AGInfrastructure"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base messaging functionality",
        )
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            semver=">=2.28.0",
            reason="HTTP requests for messaging APIs",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "send_message",
        "receive_message",
        "message_management",
        "multi_platform",
        "message_history",
    ]

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Meta abilities this extension exposes. Declared as a fresh set (not
    # relying on the base class's shared default) so registering an
    # ability here can never leak into — or be polluted by — a sibling
    # extension's `_abilities`. Mirrors EXT_EMail's pattern.
    _abilities: ClassVar[Set[str]] = {
        "send_message",
        "receive_messages",
        "get_message_history",
    }

    def __init__(self, **kwargs: Any) -> None:
        super().__init__()

        # Free-form settings bag (provider_type, api_key, per-platform
        # config, ...). Stored per-instance so constructing several
        # extensions with different settings never cross-contaminates.
        self.settings: Dict[str, Any] = dict(kwargs)

        # Provider instance reference
        self.provider: Optional[Any] = None

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can
        # never leak into the class default (and therefore into sibling
        # instances) — mirrors EXT_Automotive.
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """
        Initialize the Messaging extension.
        """
        logger.debug("Initializing Messaging Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Register capabilities
            for capability in (
                "send_message",
                "receive_message",
                "message_management",
                "multi_platform",
                "message_history",
            ):
                self.register_capability(capability)

            logger.debug("Messaging extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Messaging extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """
        Create the appropriate messaging provider instance.
        """
        try:
            provider_type = self.settings.get("provider_type", "") if self.settings else ""
            if provider_type and hasattr(self, "provider_class"):
                # This would be implemented based on your provider loading mechanism
                logger.debug(f"Creating messaging provider for {provider_type}")
                self.provider = None  # Placeholder for provider creation
            else:
                logger.warning("No messaging provider type specified")
                self.provider = None

        except Exception as e:
            logger.error(f"Error creating messaging provider: {str(e)}")
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

    @ability("send_message")
    async def send_message(
        self, recipient: str, message: str, platform: str = "default"
    ) -> str:
        """
        Send a message to a recipient.
        """
        if not self.provider:
            return await self._no_provider_warning()

        try:
            # Provider methods are synchronous (see PRV_Messaging) — no await.
            return self.provider.send_message(recipient, message, platform)
        except Exception as e:
            return f"Failed to send message: {str(e)}"

    @ability("receive_messages")
    async def receive_messages(self, platform: str = "default") -> List[Dict[str, Any]]:
        """
        Receive messages from a platform.
        """
        if not self.provider:
            return [{"error": await self._no_provider_warning()}]

        try:
            return self.provider.receive_messages(platform)
        except Exception as e:
            return [{"error": f"Failed to receive messages: {str(e)}"}]

    @ability("get_message_history")
    async def get_message_history(
        self, contact: str = "", platform: str = "default", limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get message history for a contact or platform.
        """
        if not self.provider:
            return [{"error": await self._no_provider_warning()}]

        try:
            if hasattr(self.provider, "get_message_history"):
                return self.provider.get_message_history(contact, platform, limit)
            else:
                return [{"error": "Message history not supported by this provider"}]
        except Exception as e:
            return [{"error": f"Failed to get message history: {str(e)}"}]

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Return a warning message when no provider is configured."""
        return "Messaging provider not configured. Please check your configuration."

    @classproperty
    def abilities(cls) -> _AbilityDispatchSet:
        """Ability name -> implementing callable dispatch table.

        Overrides the framework's Set[str]-based classproperty (see
        AbstractStaticExtensionSystemComponent.abilities): EXT_Messaging's
        `execute_ability` needs to resolve a name directly to its
        implementation, while framework internals (`get_abilities`,
        generic extension tests) still expect a `Set[str]`. See
        `_AbilityDispatchSet`.

        The set part is unioned with `cls._abilities` (rather than just
        the 3 known names below) so it stays equal to `get_abilities()`
        even though `_inherit_parent_abilities` (AbstractExtensionProvider)
        merges ability names from every base class in the MRO — including
        the shared default `_abilities` set other extensions mutate when
        they don't give themselves their own fresh one — into this
        extension's `_abilities` at class-definition time.
        """
        dispatch = {
            "send_message": cls.send_message,
            "receive_messages": cls.receive_messages,
            "get_message_history": cls.get_message_history,
        }
        result = _AbilityDispatchSet(dispatch)
        result.update(cls._abilities)
        return result

    def discover_abilities(self) -> None:
        """Abilities are statically declared on this extension; this simply
        (re)affirms the registered ability names, mirroring
        EXT_EMail.discover_abilities for API parity with dynamic-discovery
        extensions."""
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
            "messaging:send",
            "messaging:receive",
            "messaging:history",
            "messaging:manage",
        ]

    def on_start(self) -> bool:
        """
        Start the Messaging extension.
        """
        try:
            logger.debug("Messaging extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Messaging extension: {e}")
            return False

    def on_stop(self) -> bool:
        """
        Stop the Messaging extension.
        """
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("Messaging extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Messaging extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """
        Validate the extension configuration.
        """
        issues = []

        provider_type = self.settings.get("provider_type", "") if self.settings else ""
        if not provider_type:
            issues.append("Messaging provider type not specified")

        return issues

    def on_startup(self) -> None:
        """
        Called during application startup.
        """
        logger.debug("Messaging extension startup hook called")

    def on_shutdown(self) -> None:
        """
        Called during application shutdown.
        """
        logger.debug("Messaging extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
