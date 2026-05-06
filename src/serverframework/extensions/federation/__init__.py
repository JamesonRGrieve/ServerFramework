"""federation extension package."""

from serverframework.extensions.federation.BLL_Federation_Bootstrap import *  # noqa: F401,F403
from serverframework.extensions.federation.BLL_Federation_GQL import *  # noqa: F401,F403
from serverframework.extensions.federation.BLL_Federation_REST import *  # noqa: F401,F403
from serverframework.extensions.federation.EXT_Federation import EXT_Federation

__all__ = ["EXT_Federation"]
