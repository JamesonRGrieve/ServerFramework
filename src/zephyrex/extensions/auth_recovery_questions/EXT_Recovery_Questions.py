"""auth_recovery_questions extension definition."""

from typing import ClassVar

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AuthRecoveryQuestionsExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_recovery_questions"
    description: ClassVar[str] = (
        "Per-user security questions for account recovery (extracted from core)"
    )

    @classmethod
    def on_initialize(cls) -> bool:
        from zephyrex.extensions.auth_recovery_questions.BLL_Recovery_Questions import (
            register_merge_participation,
        )

        register_merge_participation()
        return True
