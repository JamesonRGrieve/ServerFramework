"""acl_rbac extension.

Owns the per-record ACL grant table (`PermissionModel`) and the share/grant
manager surface extracted from core (Scope #5). The `check_permission`
predicate stays in core (`database/StaticPermissions.py`) because it is the
authorization gate consumed by every BLL operation; this extension is the
opinionated 6-verb (VIEW/EXECUTE/COPY/EDIT/DELETE/SHARE) ACL shape.

Apps that want a different ACL model (capability-based, attribute-based,
team-only) omit this extension and inject their own predicate via the
permission-backend hook in `StaticPermissions`.
"""
