"""Legacy SQLAlchemy direct-ORM stubbed out during normalization.

Original ``DataRegion`` / ``DataResidencyPreference`` models referenced
``Base, BaseMixin, UpdateMixin, UserRefMixin, TeamRefMixin`` on the
long-gone ``database`` package layout. Residency policy is also covered by
``serverframework.extensions.Residency`` (jurisdiction registry); deduplicate
when rewriting.

TODO(audit): rewrite privacy/residency persistence in
``EXT_Auth_Privacy.py`` against ``ApplicationModel`` + ``ModelMeta`` and
reconcile with the existing ``Residency`` primitive.
"""

from typing import Optional

from pydantic import BaseModel


class DataRegion(BaseModel):
    """Stub. Not persisted."""

    name: str
    description: Optional[str] = None
    is_available: bool = True
    compliance_level: Optional[str] = None  # 'gdpr', 'hipaa', etc.


class DataResidencyPreference(BaseModel):
    """Stub. Not persisted."""

    data_type: str
    user_id: Optional[str] = None
    team_id: Optional[str] = None
