"""Legacy SQLAlchemy direct-ORM definition stubbed out during normalization.

The original ``APIKey`` model used ``Base, BaseMixin, UpdateMixin,
UserRefMixin.Optional, TeamRefMixin.Optional, RoleRefMixin.Optional`` from a
``database`` package layout that no longer exists. The current framework
expresses persistence via ``ApplicationModel`` + ``ModelMeta`` in BLL files
(see e.g. ``auth_session/BLL_Session.py``).

This file is preserved as a placeholder so legacy imports of ``APIKey`` do
not fail. The class is a Pydantic stub with the public field shape only.

TODO(audit): rewrite the API-key persistence layer in
``BLL_Auth_APIKeys.py`` using ``ApplicationModel`` + ``UpdateMixinModel`` and
delete this file.
"""

from typing import Optional

from pydantic import BaseModel


class APIKey(BaseModel):
    """Stub for the legacy SQLAlchemy ``APIKey`` model.

    Real persistence must be re-introduced in ``BLL_Auth_APIKeys.py`` against
    the current ``ApplicationModel`` pattern. This stub exists so that
    legacy imports parse; it is not registered with the database manager.
    """

    name: str
    key_hash: str
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    role_id: Optional[str] = None


APIKeyRefMixin = None  # legacy create_reference_mixin output; rewrite consumers
