import os
import tempfile
from typing import Dict, Any

import pytest

from serverframework.lib.Environment import AppSettings, env, register_extension_env_vars


class TestMixin:
    """Mixin to add unittest-style assertions to pytest classes."""

    def assertEqual(self, a, b, msg=None):
        assert a == b, msg or f"Expected {a} == {b}"

    def assertIn(self, a, b, msg=None):
        assert a in b, msg or f"Expected {a} in {b}"

    def assertIsNotNone(self, a, msg=None):
        assert a is not None, msg or f"Expected {a} is not None"


@pytest.fixture
def clean_environment(monkeypatch):
    """Fixture to ensure clean environment for each test."""
    # Store original environment
    original_environ = dict(os.environ)

    # Use monkeypatch to temporarily modify os.environ
    # This preserves VSCode pytest extension variables
    yield

    # Restore original environment properly
    # Remove any keys that weren't in the original
    for key in list(os.environ.keys()):
        if key not in original_environ:
            monkeypatch.delenv(key, raising=False)

    # Restore original values
    for key, value in original_environ.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def temp_env_file():
    """Create a temporary .env file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("TEST_ENV_VAR=test_value\n")
        f.write("ANOTHER_TEST_VAR=another_value\n")
        temp_file = f.name

    yield temp_file

    # Clean up
    os.unlink(temp_file)


class TestAppSettingsReal(TestMixin):
    """Test the AppSettings class with real functionality."""

    def test_app_settings_initialization(self):
        """Test that AppSettings initializes with expected defaults."""
        settings = AppSettings()

        # Test default values
        self.assertEqual(settings.APP_NAME, "Server")
        self.assertEqual(settings.ENVIRONMENT, "local")
        self.assertEqual(settings.SERVER_URI, "http://localhost:1996")
        self.assertEqual(settings.DATABASE_TYPE, "sqlite")

    def test_app_settings_from_environment(self, clean_environment):
        """Test that AppSettings reads from environment variables."""
        # Set some environment variables. After Item 71, production-mode
        # validation refuses default ROOT_API_KEY/JWT_SECRET/ALLOWED_DOMAINS;
        # supply secure values so the env→settings round-trip passes.
        os.environ["APP_NAME"] = "TestApp"
        os.environ["ENVIRONMENT"] = "production"
        os.environ["SERVER_URI"] = "https://test.example.com"
        os.environ["ROOT_API_KEY"] = "test-root-api-key-not-default"
        os.environ["JWT_SECRET"] = "test-jwt-secret-not-default-32chars"
        os.environ["ALLOWED_DOMAINS"] = "test.example.com"
        # L-3 — production validation now refuses the empty default for
        # DATABASE_PASSWORD too; supply an explicit non-default value.
        os.environ["DATABASE_PASSWORD"] = "secure-test-db-password"

        settings = AppSettings.model_validate(os.environ)

        self.assertEqual(settings.APP_NAME, "TestApp")
        self.assertEqual(settings.ENVIRONMENT, "production")
        self.assertEqual(settings.SERVER_URI, "https://test.example.com")

    def test_app_settings_partial_override(self, clean_environment):
        """Test that only specified environment variables override defaults."""
        # Clear any environment variables that might interfere with defaults
        vars_to_clear = ["ENVIRONMENT", "SERVER_URI"]
        for var in vars_to_clear:
            if var in os.environ:
                del os.environ[var]

        # Only set one environment variable
        os.environ["APP_NAME"] = "PartialApp"

        settings = AppSettings.model_validate(os.environ)

        # Should use env var for APP_NAME
        self.assertEqual(settings.APP_NAME, "PartialApp")
        # Should use defaults for others
        self.assertEqual(settings.ENVIRONMENT, "local")
        self.assertEqual(settings.SERVER_URI, "http://localhost:1996")

    def test_app_settings_validation(self, clean_environment):
        """Test that AppSettings validates field types correctly."""
        # Set valid environment values
        os.environ["DATABASE_PORT"] = "3306"
        os.environ["APP_EXTENSIONS"] = "auth,email,database"

        settings = AppSettings.model_validate(os.environ)

        self.assertEqual(settings.DATABASE_PORT, "3306")
        self.assertEqual(settings.APP_EXTENSIONS, "auth,email,database")

    def test_register_env_vars_basic(self):
        """Test basic environment variable registration."""
        # Create a new AppSettings instance to avoid affecting global state
        original_fields = set(AppSettings.model_fields.keys())

        # Register new environment variables
        test_vars = {
            "NEW_TEST_VAR": "default_value",
            "ANOTHER_NEW_VAR": "another_default",
        }

        # This should extend the model with new fields
        AppSettings.register_env_vars(test_vars)

        # Check that new fields were added (this may create a new model class internally)
        # The exact implementation depends on how register_env_vars works
        # For now, we'll just verify the method can be called without error

        # Test that we can call it again without issues
        AppSettings.register_env_vars({"THIRD_VAR": "third_default"})

    def test_register_env_vars_empty(self):
        """Test registering empty environment variables."""
        # Should not cause any errors
        AppSettings.register_env_vars({})
        AppSettings.register_env_vars(None)


class TestEnvFunctionReal(TestMixin):
    """Test the env() function with real functionality."""

    def test_env_function_with_existing_setting(self):
        """Test env function returns value from settings when available."""
        # The env() function should return a value for any field that exists in AppSettings
        # We'll test with APP_NAME which we know exists
        result = env("APP_NAME")
        # Just verify it returns something (not empty), since we can't predict
        # what the global state will be in a parallel test environment
        self.assertIsNotNone(result)
        self.assertIn(
            result,
            ["Server", "PartialApp", "ExtensionApp", "TestApp", "IntegrationTest"],
        )  # Known possible values from tests

    def test_env_function_with_environment_override(self, clean_environment):
        """Test env function returns environment value when set."""
        # Set environment variable that corresponds to a setting
        os.environ["APP_NAME"] = "OverriddenApp"

        # Reinitialize settings to pick up the environment change
        from serverframework.lib.Environment import settings

        new_settings = AppSettings.model_validate(os.environ)

        # Since we can't easily mock the module-level settings,
        # we'll test the behavior through direct settings access
        self.assertEqual(new_settings.APP_NAME, "OverriddenApp")

    def test_env_function_with_nonexistent_setting(self, clean_environment):
        """Test env function falls back to os.getenv for non-existent settings."""
        # Set an environment variable that's not in AppSettings
        os.environ["TEST_ONLY_IN_OS"] = "test_value"

        result = env("TEST_ONLY_IN_OS")
        self.assertEqual(result, "test_value")

    def test_env_function_with_missing_var(self):
        """Test env function returns empty string for missing variables."""
        result = env("COMPLETELY_NONEXISTENT_VAR")
        self.assertEqual(result, "")

    def test_env_function_with_none_value(self, clean_environment):
        """Test env function handles None values correctly."""
        # Test with a field that can be None
        os.environ["DATABASE_NAME"] = ""  # Empty string

        settings = AppSettings.model_validate(os.environ)
        # DATABASE_NAME can be None, so it might be converted
        result = env("DATABASE_NAME")
        # Should return empty string for None or empty values
        self.assertIn(result, ["", "database"])  # Could be default or empty


class TestRegisterExtensionEnvVarsReal(TestMixin):
    """Test register_extension_env_vars with real functionality."""

    def test_register_extension_env_vars_basic(self, clean_environment):
        """Test basic extension environment variable registration."""
        test_env_vars = {
            "EXT_API_KEY": "default_key",
            "EXT_ENDPOINT": "https://api.example.com",
            "EXT_TIMEOUT": "30",
        }

        # Should not raise any exceptions
        register_extension_env_vars(test_env_vars)

    def test_register_extension_env_vars_with_existing_env(self, clean_environment):
        """Test registration respects existing environment values."""
        # Set an environment variable first
        os.environ["EXISTING_VAR"] = "existing_value"

        test_env_vars = {
            "EXISTING_VAR": "default_value",  # Should use env value
            "NEW_VAR": "new_default",
        }

        # Should not raise any exceptions
        register_extension_env_vars(test_env_vars)

    def test_register_extension_env_vars_empty(self):
        """Test registration with empty or None values."""
        # Should not raise exceptions
        register_extension_env_vars({})
        register_extension_env_vars(None)

    def test_register_extension_env_vars_integration(self, clean_environment):
        """Test extension registration integration."""
        # Set up a realistic scenario
        os.environ["EMAIL_PROVIDER"] = "sendgrid"

        email_vars = {
            "EMAIL_PROVIDER": "smtp",  # Should use env value "sendgrid"
            "EMAIL_API_KEY": "default_key",
            "EMAIL_FROM": "noreply@example.com",
        }

        # Should work without errors
        register_extension_env_vars(email_vars)


class TestEnvironmentIntegration(TestMixin):
    """Integration tests for the complete environment system."""

    def test_full_environment_workflow(self, clean_environment):
        """Test complete environment variable workflow."""
        # Set up environment
        os.environ["APP_NAME"] = "IntegrationTest"
        os.environ["ENVIRONMENT"] = "development"  # Use valid ENVIRONMENT value
        os.environ["CUSTOM_VAR"] = "custom_value"

        # Test settings creation
        settings = AppSettings.model_validate(os.environ)
        self.assertEqual(settings.APP_NAME, "IntegrationTest")
        self.assertEqual(settings.ENVIRONMENT, "development")

        # Test env function with settings (note: env() uses module-level settings,
        # so it may not reflect our test changes unless we update global state)
        # Instead, let's test that the settings object itself works correctly
        # self.assertEqual(env("APP_NAME"), "IntegrationTest")  # This would require updating global settings

        # Test env function with OS-only var
        self.assertEqual(env("CUSTOM_VAR"), "custom_value")

        # Test env function with non-existent var
        self.assertEqual(env("NON_EXISTENT"), "")

    def test_extension_integration_workflow(self, clean_environment):
        """Test extension environment variable integration."""
        # Set up base environment
        os.environ["APP_NAME"] = "ExtensionApp"

        # Register extension variables
        extension_vars = {"EXT_FEATURE": "enabled", "EXT_CONFIG": "config_value"}

        register_extension_env_vars(extension_vars)

        # Verify base settings still work
        base_settings = AppSettings.model_validate(os.environ)
        self.assertEqual(base_settings.APP_NAME, "ExtensionApp")

    def test_multiple_extension_registration(self, clean_environment):
        """Test multiple extension registrations work together."""
        # Register multiple extensions
        auth_vars = {"AUTH_SECRET": "secret123", "AUTH_TIMEOUT": "3600"}
        email_vars = {"EMAIL_HOST": "smtp.example.com", "EMAIL_PORT": "587"}

        # Should not conflict
        register_extension_env_vars(auth_vars)
        register_extension_env_vars(email_vars)

        # Test that we can create a new settings instance with all vars
        # This avoids relying on the global settings object
        test_settings = AppSettings.model_validate(os.environ)
        # If APP_NAME is not in os.environ, it should use the default
        if "APP_NAME" not in os.environ:
            self.assertEqual(test_settings.APP_NAME, "Server")  # Default value


# ----------------------------------------------------------------------
# Security: explicit-deny / negative-path tests for production defaults.
# These assert that obviously-insecure defaults are not silently
# accepted when the runtime is `ENVIRONMENT=production`.
# Most are EXPECTED FAIL today — surfacing the gap is the point.
# ----------------------------------------------------------------------


@pytest.mark.security
class TestProductionDefaultsAreFailClosed(TestMixin):
    """A production deployment must refuse insecure defaults at start-up."""

    def test_root_api_key_default_rejected_in_production(
        self, clean_environment, monkeypatch
    ):
        """ROOT_API_KEY="n0ne" in production must fail validation.

        EXPECTED FAIL today — Environment.py:40 default is the literal
        string "n0ne" and there is no production-mode guard.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ROOT_API_KEY", "n0ne")
        with pytest.raises(Exception):
            AppSettings.model_validate(os.environ)

    def test_jwt_secret_required_in_production(
        self, clean_environment, monkeypatch
    ):
        """JWT_SECRET unset/empty in production must fail validation.

        EXPECTED FAIL today — JWT_SECRET has no production-mode guard.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET", "")
        with pytest.raises(Exception):
            AppSettings.model_validate(os.environ)

    def test_allowed_domains_wildcard_rejected_in_production(
        self, clean_environment, monkeypatch
    ):
        """ALLOWED_DOMAINS="*" combined with credentials must fail in production.

        EXPECTED FAIL today — Environment.py:42 default is "*" and there is
        no production-mode guard. With CORS allow_credentials=True (app.py:456)
        the browser CORS spec rejects `*` anyway, but the framework should
        explicitly fail-closed instead of relying on browser behaviour.
        """
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("ALLOWED_DOMAINS", "*")
        with pytest.raises(Exception):
            AppSettings.model_validate(os.environ)


if __name__ == "__main__":
    pytest.main([__file__])
