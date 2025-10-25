# Set default test configuration for all test classes

# Set default test configuration for all test classes

from AbstractTest import ClassOfTestsConfig, CategoryOfTest
from logic.AbstractBLLTest import AbstractBLLTest
from extensions.AbstractEXTTest import ExtensionServerMixin
from extensions.email.EXT_EMail import EXT_EMail
from lib.Environment import env

from logic.BLL_Auth import InvitationManager


AbstractBLLTest.test_config = ClassOfTestsConfig(
    categories=[CategoryOfTest.LOGIC, CategoryOfTest.EXTENSION]
)


class TestEmailManager(AbstractBLLTest, ExtensionServerMixin):
    """
    Test class for email-related behavior that integrates with the
    InvitationManager. We run the generic BLL tests (create/list/get/update/etc.)
    against InvitationManager so the full suite of logic tests in this
    extension folder remain active.
    """

    class_under_test = InvitationManager
    extension_class = EXT_EMail

    create_fields = {}
    update_fields = {}
    unique_fields = {}

    def test_send_email_on_invitation(self, admin_a, team_a, model_registry):
        """
        Sends an email when an invitation-invitee is created.

        This test creates an invitation via the InvitationManager which will
        trigger the Email extension's invitation hook. We assert only that
        the invitation was created successfully; the extension hook is run
        as part of the create flow.
        """

        with InvitationManager(
            model_registry=model_registry,
            requester_id=admin_a.id,
        ) as manager:
            # Create an invitation
            invitation = manager.create(
                team_id=team_a.id,
                role_id=env("USER_ROLE_ID"),
                email="dj.ecex@gmail.com",
            )

        assert invitation is not None, "Invitation should be created successfully"
