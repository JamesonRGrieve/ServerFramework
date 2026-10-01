import pytest

from zephyrex.extensions.conversations.EXT_Conversations import EXT_Conversations


@pytest.mark.conversations
class TestEXTConversations:
    """
    Test suite for EXT_Conversations extension.

    Tests basic extension metadata and functionality.
    This is an internal database extension that uses BLL managers directly.

    Test areas:
    - Extension metadata (name, version, description)
    - Extension class structure and inheritance
    """

    def test_extension_metadata(self):
        """Test extension metadata."""
        assert EXT_Conversations.name == "conversations"
        assert EXT_Conversations.friendly_name == "Conversation Management"
        assert EXT_Conversations.version == "1.0.0"
        assert "conversation" in EXT_Conversations.description.lower()

    def test_extension_class_structure(self):
        """Test extension class structure."""
        # Test that EXT_Conversations is properly defined
        assert hasattr(EXT_Conversations, "name")
        assert hasattr(EXT_Conversations, "friendly_name")
        assert hasattr(EXT_Conversations, "version")
        assert hasattr(EXT_Conversations, "description")

        # Test inheritance
        from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension

        assert issubclass(EXT_Conversations, AbstractStaticExtension)

    def test_extension_is_internal_database_extension(self):
        """Test that this is an internal database extension without providers."""
        # This extension should not have any providers since it's internal
        # The providers property should return an empty list or handle gracefully
        try:
            providers = EXT_Conversations.providers
            # If it's a property, it should return a list
            if callable(providers):
                providers = providers()
            assert isinstance(providers, list)
        except AttributeError:
            # It's fine if providers property doesn't exist for internal extensions
            pass
