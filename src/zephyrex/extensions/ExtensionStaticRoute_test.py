# SPDX-License-Identifier: AGPL-3.0-or-later
"""An extension class cannot declare a route it will never serve.

@static_route on an extension class was collected into a registry nothing
mounted (#241): the route silently did not exist. Defining one now fails.
"""

import pytest

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension
from zephyrex.pydantic2.fastapi.types import AuthType, HTTPMethod, static_route


def test_a_static_route_on_an_extension_class_is_refused():
    with pytest.raises(TypeError, match="put them on a RouterMixin manager"):

        class EXT_RouteProbe(AbstractStaticExtension):
            name = "route_probe"

            @staticmethod
            @static_route("/probe", method=HTTPMethod.GET, auth_type=AuthType.NONE)
            def probe():
                return {}


def test_an_extension_class_without_routes_is_fine():
    class EXT_Quiet(AbstractStaticExtension):
        name = "quiet_probe"

    assert EXT_Quiet.name == "quiet_probe"
