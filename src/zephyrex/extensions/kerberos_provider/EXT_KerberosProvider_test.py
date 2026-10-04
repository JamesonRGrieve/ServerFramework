# SPDX-License-Identifier: AGPL-3.0-or-later
"""kerberos_provider is parked: it must offer nothing until a real KDC exists.

It used to advertise ticket issuance, ticket validation and principal
management abilities, a KDC config table and principal table, environment
settings and a gssapi requirement, none of which did anything. These tests
pin the honest shape so a fake cannot creep back in."""

from pathlib import Path

from zephyrex.extensions.kerberos_provider.EXT_KerberosProvider import (
    EXT_KerberosProvider,
)
from zephyrex.extensions.Manifest import load_manifest

_FOLDER = Path(__file__).resolve().parent
_FORMER_FAKE_ABILITIES = {
    "kerberos_provider_issue_ticket",
    "kerberos_provider_validate_ticket",
    "kerberos_provider_manage_principals",
}


class TestParkedMetadata:
    def test_identity(self) -> None:
        assert EXT_KerberosProvider.name == "kerberos_provider"
        assert EXT_KerberosProvider.version == "2.0.0"

    def test_description_says_parked_and_points_to_the_consumer(self) -> None:
        description = EXT_KerberosProvider.description
        assert description.startswith("Parked, not built:")
        assert "KDC" in description
        assert "kerberos_consumer" in description

    def test_docstring_says_parked(self) -> None:
        import zephyrex.extensions.kerberos_provider.EXT_KerberosProvider as module

        assert module.__doc__ is not None
        assert module.__doc__.startswith("Parked:")

    def test_manifest_matches(self) -> None:
        manifest = load_manifest(_FOLDER / "manifest.toml")
        assert manifest.description == EXT_KerberosProvider.description
        assert manifest.pip_dependencies == []
        assert manifest.system_dependencies == []
        assert manifest.extension_dependencies == []


class TestOffersNothing:
    def test_no_abilities(self) -> None:
        assert EXT_KerberosProvider.get_abilities() == set()
        assert not _FORMER_FAKE_ABILITIES & EXT_KerberosProvider.get_abilities()

    def test_no_dependencies(self) -> None:
        assert list(EXT_KerberosProvider.dependencies) == []
        assert EXT_KerberosProvider.pip_requirements() == []

    def test_no_settings_or_config_checks(self) -> None:
        assert EXT_KerberosProvider._env == {}
        assert EXT_KerberosProvider.validate_config() == []

    def test_no_models_routes_or_providers(self) -> None:
        modules = {
            path.name
            for path in _FOLDER.glob("*.py")
            if not path.stem.endswith("_test")
        }
        assert modules == {"__init__.py", "EXT_KerberosProvider.py"}
        assert not (_FOLDER / "migrations").exists()
