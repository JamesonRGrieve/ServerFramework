"""Legacy SQLAlchemy direct-ORM stubbed out during normalization.

Original definition tracked ``UserMerge`` rows linking an initiating user to
a target user (account consolidation). It used ``Base, BaseMixin, UpdateMixin``
and explicit ``declared_attr`` foreign keys on the long-gone ``database``
package.

TODO(audit): rewrite ``UserMerge`` (and the planned ``TeamMerge``) in
``BLL_Auth_Merge.py`` against the current ``ApplicationModel`` +
``ModelMeta`` pattern, with ``UserModel.Reference`` for FKs.
"""

from typing import Optional

from pydantic import BaseModel


class UserMerge(BaseModel):
    """Stub for the legacy ``UserMerge`` model. Not persisted."""

    initiating_user_id: str
    target_user_id: str
    completed_at: Optional[str] = None
