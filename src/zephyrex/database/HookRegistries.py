# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extension hook registries for the database layer.

These registries decouple core code from the optional extensions that own
the ACL and invitation entities. They live here — at the database layer,
at/below their ``StaticPermissions`` consumer — rather than in
``logic/BLL_Auth`` (a layer *above* the consumer), so the permission engine
never imports upward into ``logic`` to resolve a hook (issue #222).

The ``acl_rbac`` and ``auth_invitations`` extensions register their concrete
implementations at load time via ``register_acl_hooks`` /
``register_invitation_hooks``. When an extension is absent its hooks stay
``None`` and the consuming code fails closed. ``logic/BLL_Auth`` re-exports
these names for backward compatibility.
"""


# auth_invitations extension hooks — populated by `auth_invitations.on_load`
# (Scope #4). Encapsulate every place core used to reach into
# InvitationModel / InviteeModel / InvitationManager / InviteeManager.
_invitation_hooks: dict = {
    "lookup_by_id": None,  # (invitation_id, model_registry) -> dict | None
    "lookup_by_code": None,  # (code, model_registry) -> dict | None
    "apply_to_user": None,  # (invitation_dict, user_id, model_registry) -> None
    "invitation_manager_factory": None,  # (requester_id, target_team_id, model_registry, **kw) -> manager
    "invitee_manager_factory": None,  # (requester_id, target_id, model_registry, **kw) -> manager
    "list_invitees_for_user": None,  # (user_id, email, model_registry) -> List[dict]
    "invitation_db_class": None,  # (declarative_base) -> SA model
    "invitee_db_class": None,  # (declarative_base) -> SA model
}


def register_invitation_hooks(
    *,
    lookup_by_id=None,
    lookup_by_code=None,
    apply_to_user=None,
    invitation_manager_factory=None,
    invitee_manager_factory=None,
    list_invitees_for_user=None,
    invitation_db_class=None,
    invitee_db_class=None,
) -> None:
    for name, fn in (
        ("lookup_by_id", lookup_by_id),
        ("lookup_by_code", lookup_by_code),
        ("apply_to_user", apply_to_user),
        ("invitation_manager_factory", invitation_manager_factory),
        ("invitee_manager_factory", invitee_manager_factory),
        ("list_invitees_for_user", list_invitees_for_user),
        ("invitation_db_class", invitation_db_class),
        ("invitee_db_class", invitee_db_class),
    ):
        if fn is not None:
            _invitation_hooks[name] = fn


# acl_rbac extension hooks — populated by `acl_rbac.on_load` (Scope #5).
_acl_hooks: dict = {
    "permission_db_class": None,  # (declarative_base) -> SA model
    "create_permission": None,  # (resource_type, resource_id, user_id, can_*, model_registry, **kw) -> Any
}


def register_acl_hooks(
    *,
    permission_db_class=None,
    create_permission=None,
) -> None:
    for name, fn in (
        ("permission_db_class", permission_db_class),
        ("create_permission", create_permission),
    ):
        if fn is not None:
            _acl_hooks[name] = fn
