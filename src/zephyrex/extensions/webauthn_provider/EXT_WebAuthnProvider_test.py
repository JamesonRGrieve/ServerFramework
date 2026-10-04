# SPDX-License-Identifier: AGPL-3.0-or-later
"""webauthn_provider is parked: it must offer nothing until a real hosted
passkey service exists.

It used to advertise register, authenticate and relying-party management
abilities, a relying-party table and a credential table, environment
settings and a fido2 requirement, none of which did anything. These tests pin
the honest shape so a fake cannot creep back in."""

from pathlib import Path

from zephyrex.extensions.Manifest import load_manifest
from zephyrex.extensions.webauthn_provider.EXT_WebAuthnProvider import (
    EXT_WebAuthnProvider,
)

_FOLDER = Path(__file__).resolve().parent
_FORMER_FAKE_ABILITIES = {
    "webauthn_provider_register",
    "webauthn_provider_authenticate",
    "webauthn_provider_manage_rp",
}


class TestParkedMetadata:
    def test_identity(self) -> None:
        assert EXT_WebAuthnProvider.name == "webauthn_provider"
        assert EXT_WebAuthnProvider.version == "2.0.0"

    def test_description_says_parked_and_points_to_the_consumer(self) -> None:
        description = EXT_WebAuthnProvider.description
        assert description.startswith("Parked, not built:")
        assert "passkey" in description
        assert "webauthn_consumer" in description

    def test_docstring_says_parked(self) -> None:
        import zephyrex.extensions.webauthn_provider.EXT_WebAuthnProvider as module

        assert module.__doc__ is not None
        assert module.__doc__.startswith("Parked:")

    def test_manifest_matches(self) -> None:
        manifest = load_manifest(_FOLDER / "manifest.toml")
        assert manifest.description == EXT_WebAuthnProvider.description
        assert manifest.pip_dependencies == []
        assert manifest.system_dependencies == []
        assert manifest.extension_dependencies == []


class TestOffersNothing:
    def test_no_abilities(self) -> None:
        assert EXT_WebAuthnProvider.get_abilities() == set()
        assert not _FORMER_FAKE_ABILITIES & EXT_WebAuthnProvider.get_abilities()

    def test_no_dependencies(self) -> None:
        assert list(EXT_WebAuthnProvider.dependencies) == []
        assert EXT_WebAuthnProvider.pip_requirements() == []

    def test_no_settings_or_config_checks(self) -> None:
        assert EXT_WebAuthnProvider._env == {}
        assert EXT_WebAuthnProvider.validate_config() == []

    def test_no_models_routes_or_providers(self) -> None:
        modules = {
            path.name
            for path in _FOLDER.glob("*.py")
            if not path.stem.endswith("_test")
        }
        assert modules == {"__init__.py", "EXT_WebAuthnProvider.py"}
        assert not (_FOLDER / "migrations").exists()
