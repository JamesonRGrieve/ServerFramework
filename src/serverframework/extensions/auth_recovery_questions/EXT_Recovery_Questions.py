"""auth_recovery_questions extension definition."""

from typing import ClassVar, List, Type

from serverframework.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
)


class AuthRecoveryQuestionsExtension(AbstractStaticExtension):
    name: ClassVar[str] = "auth_recovery_questions"
    description: ClassVar[str] = (
        "Per-user security questions for account recovery (extracted from core)"
    )
    extension_dependencies: ClassVar[List[str]] = []

    @classmethod
    def models(cls) -> List[Type]:
        from serverframework.extensions.auth_recovery_questions.BLL_Recovery_Questions import (
            UserRecoveryQuestionModel,
        )

        return [UserRecoveryQuestionModel]
