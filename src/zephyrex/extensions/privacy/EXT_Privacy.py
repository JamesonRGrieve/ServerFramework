"""Privacy extension — PII classification, redaction, right-to-erasure."""

from typing import Any, ClassVar, Dict, List, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.lib.Logging import logger


class EXT_Privacy(AbstractStaticExtension):
    """PII classification, log redaction, GDPR right-to-erasure orchestration.

    Privacy/PII handling is a compliance concern, not a framework
    primitive. Deployments without PII obligations don't carry the
    classification metadata; this extension makes it opt-in.
    """

    name: ClassVar[str] = "privacy"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = (
        "PII classification metadata, redaction helpers, right-to-erasure "
        "and data-export orchestration for GDPR/CCPA compliance"
    )

    # Days a deleted row is kept before the daily purge removes it for good;
    # unset, deletions stay soft for ever (see Purge).
    _env: ClassVar[Dict[str, Any]] = {"PRIVACY_PURGE_DELETED_AFTER_DAYS": ""}
    _abilities: ClassVar[Set[str]] = {
        "pii_classify",
        "pii_redact",
        "right_to_erasure",
        "data_export",
    }
    _providers: ClassVar[List] = []

    @classmethod
    def register_services(cls, model_registry: Any, requester_id: str) -> List[Any]:
        """The daily purge of what was deleted, when the app turned it on."""
        from zephyrex.extensions.privacy.Purge import purge_services

        return purge_services(model_registry, requester_id)

    @classmethod
    def on_initialize(cls) -> bool:
        logger.debug("Initializing privacy extension")
        # An account deleting itself first erases what extensions hold about it.
        from zephyrex.extensions.privacy.Erasure import hook_account_deletion

        hook_account_deletion()
        return True
