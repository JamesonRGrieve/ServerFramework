"""metadata extension.

Owns the unified `metadata` table and the user/team-scoped manager surfaces
extracted from core (Scope #3). The canonical Pydantic class still lives in
`logic/BLL_Auth.py` for now; this extension owns the migration + the EXT
lifecycle so the table can be enabled/disabled per APP_EXTENSIONS.
"""
