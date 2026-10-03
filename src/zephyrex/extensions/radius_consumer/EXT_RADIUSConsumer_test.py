# SPDX-License-Identifier: AGPL-3.0-or-later
"""The radius_consumer extension's declaration and configuration checks."""

from zephyrex.extensions.radius_consumer.BLL_RADIUSConsumer import ALLOW_UDP_ENV
from zephyrex.extensions.radius_consumer.EXT_RADIUSConsumer import (
    EXT_RADIUSConsumer,
)
from zephyrex.lib.Dependencies import EXT_Dependency


class TestDeclaration:
    def test_pyrad_is_declared(self):
        assert EXT_RADIUSConsumer.pip_requirements() == ["pyrad>=2.5.4"]

    def test_sessions_are_an_optional_dependency(self):
        found = [
            (dep.name, dep.optional)
            for dep in EXT_RADIUSConsumer.dependencies
            if isinstance(dep, EXT_Dependency)
        ]
        assert found == [("auth_session", True)]

    def test_it_offers_no_abilities(self):
        """Signing in is a route for people, not an ability for agents."""
        assert EXT_RADIUSConsumer.get_abilities() == set()


class TestConfigurationCheck:
    def test_quiet_while_udp_is_off(self, set_env):
        set_env(ALLOW_UDP_ENV, "false")
        assert EXT_RADIUSConsumer.validate_config() == []

    def test_warns_while_udp_is_on(self, set_env):
        set_env(ALLOW_UDP_ENV, "true")
        [warning] = EXT_RADIUSConsumer.validate_config()
        assert ALLOW_UDP_ENV in warning and "RadSec" in warning
