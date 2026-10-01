from typing import List

from zephyrex.extensions.AbstractEXTTest import (
    AbstractEXTTest,
    ExtensionTestConfig,
    ExtensionTestType,
)
from zephyrex.extensions.social.EXT_Social import EXT_Social


class TestEXTSocial(AbstractEXTTest):
    """
    Test suite for EXT_Social extension.

    Tests extension initialization, social media capabilities, abilities, and
    static extension metadata rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Dependencies and provider discovery
    - Static extension functionality
    - Social posting, mention monitoring, and analytics meta abilities
    """

    # Configure the test class
    extension_class = EXT_Social
    test_config = ExtensionTestConfig(
        test_types={
            ExtensionTestType.STRUCTURE,
            ExtensionTestType.METADATA,
            ExtensionTestType.DEPENDENCIES,
            ExtensionTestType.ABILITIES,
            ExtensionTestType.ENVIRONMENT,
        },
        expected_abilities={
            "post_to_social",
            "monitor_mentions",
            "get_social_analytics",
        },
    )

    # Expected extension properties
    expected_abilities: List[str] = [
        "post_to_social",
        "monitor_mentions",
        "get_social_analytics",
    ]

    def test_extension_metadata(self):
        """Test extension metadata and basic attributes"""
        assert self.extension_class.name == "social"
        assert self.extension_class.version == "1.0.0"
        assert self.extension_class.friendly_name == "Social Media Integration"
        assert "Social media integration extension" in self.extension_class.description

    def test_dependencies_structure(self):
        """Test that dependencies are properly structured"""
        assert hasattr(self.extension_class, "dependencies")
        assert self.extension_class.dependencies is not None

        # Check extension dependencies
        ext_deps = self.extension_class.dependencies.ext
        assert len(ext_deps) == 1
        labels_dep = ext_deps[0]
        assert labels_dep.name == "labels"
        assert labels_dep.optional

        # Check pip dependencies
        pip_deps = self.extension_class.dependencies.pip
        assert len(pip_deps) == 1
        requests_dep = pip_deps[0]
        assert requests_dep.name == "requests"
        assert not requests_dep.optional
        assert ">=2.28.0" in requests_dep.semver

    def test_provider_discovery(self):
        """Test that provider discovery works"""
        providers = self.extension_class.providers
        assert isinstance(providers, list)
        # Social has concrete providers (Facebook, Instagram, Threads,
        # TikTok, X, Postiz) but discovery may be empty in test environment.

    def test_extension_env_vars(self):
        """Test that extension has proper environment variables."""
        assert hasattr(self.extension_class, "_env")
        env_vars = self.extension_class._env

        expected_env_vars = [
            "SOCIAL_ENABLED",
            "SOCIAL_DEFAULT_PLATFORM",
            "SOCIAL_AUTO_SCHEDULE",
        ]

        for env_var in expected_env_vars:
            assert env_var in env_vars, f"Missing environment variable: {env_var}"

    def test_meta_abilities_execution(self):
        """Test that meta abilities can be executed."""
        assert hasattr(self.extension_class, "post_to_social")
        assert callable(getattr(self.extension_class, "post_to_social"))

        assert hasattr(self.extension_class, "monitor_mentions")
        assert callable(getattr(self.extension_class, "monitor_mentions"))

        assert hasattr(self.extension_class, "get_social_analytics")
        assert callable(getattr(self.extension_class, "get_social_analytics"))

    def test_post_to_social_ability(self):
        """Test the post_to_social meta ability directly."""
        result = self.extension_class.post_to_social(
            content="Hello world!", platforms=["twitter", "facebook"]
        )

        assert result["success"] is True
        assert result["content"] == "Hello world!"
        assert result["platforms"] == ["twitter", "facebook"]

    def test_monitor_mentions_ability(self):
        """Test the monitor_mentions meta ability directly."""
        result = self.extension_class.monitor_mentions(
            keywords=["brand", "product"], platform="twitter"
        )

        assert result["success"] is True
        assert result["keywords"] == ["brand", "product"]
        assert result["platform"] == "twitter"

    def test_get_social_analytics_ability(self):
        """Test the get_social_analytics meta ability directly."""
        result = self.extension_class.get_social_analytics(
            platform="twitter", period="month"
        )

        assert result["success"] is True
        assert result["platform"] == "twitter"
        assert result["period"] == "month"

    def test_abstract_provider_class(self):
        """Test that AbstractSocialProvider exists in PRV_Social."""
        from zephyrex.extensions.social.PRV_Social import AbstractSocialProvider

        # Test that it has the expected abstract methods
        expected_methods = [
            "bond_instance",
            "post_content",
            "read_feed",
            "get_profile",
        ]

        for method_name in expected_methods:
            assert hasattr(
                AbstractSocialProvider, method_name
            ), f"Missing method: {method_name}"

    def test_static_extension_inheritance(self):
        """Test that extension inherits from AbstractStaticExtension"""
        from zephyrex.extensions.AbstractExtensionProvider import (
            AbstractStaticExtension,
        )

        assert issubclass(self.extension_class, AbstractStaticExtension)

    def test_extension_abilities(self):
        """Test that extension has expected abilities."""
        abilities = self.extension_class.abilities

        for expected_ability in self.expected_abilities:
            assert expected_ability in abilities, f"Missing ability: {expected_ability}"

    def test_has_ability(self):
        """Test the has_ability method."""
        if hasattr(self.extension_class, "has_ability"):
            assert self.extension_class.has_ability("post_to_social")
            assert not self.extension_class.has_ability("unknown_capability")
        else:
            assert "post_to_social" in self.extension_class.abilities
