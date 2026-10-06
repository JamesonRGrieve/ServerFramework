# SPDX-License-Identifier: AGPL-3.0-or-later
"""Extracted test fixture factories and helpers.

Shared between conftest.py (session-scoped) and ExtensionServerMixin
(module-scoped). Each factory takes the server and any prerequisite
entities, returning the created object.
"""

import base64
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping, Optional

from faker import Faker
from starlette.testclient import TestClient

from zephyrex.lib.Environment import env
from zephyrex.lib.Preconditions import IF_MATCH_HEADER, entity_etag
from zephyrex.logic.BLL_Auth import (
    RoleModel,
    TeamModel,
    UserCredentialManager,
    UserModel,
    UserTeamModel,
)
from zephyrex.logic.BLL_Providers import (
    ProviderInstanceManager,
    ProviderInstanceModel,
    ProviderInstanceSettingManager,
    ProviderManager,
)

# The password test users are created with; it meets the password policy.
TEST_PASSWORD = "testpassword1"


def generate_test_email(prefix="test"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}@example.com"


class UserWithJWT(UserModel):
    jwt: str


def create_user(
    server,
    email=None,
    password=TEST_PASSWORD,
    first_name="Test",
    last_name="User",
):
    if email is None:
        email = generate_test_email()

    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    User = UserModel.DB(model_registry.DB.manager.Base)
    existing_users = User.list(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        filters=[User.email == email],
        return_type="dto",
        override_dto=UserModel,
    )

    if existing_users:
        user = existing_users[0]
        if not isinstance(user, UserModel):
            user = UserModel(**user)
    else:
        user = User.create(
            requester_id=env("SYSTEM_ID"),
            model_registry=model_registry,
            return_type="dto",
            override_dto=UserModel,
            email=email,
            username=email.split("@")[0],
            first_name=first_name,
            last_name=last_name,
            display_name=f"{first_name} {last_name} Display",
        )
    if password:
        with UserCredentialManager(
            requester_id=user.id,
            model_registry=model_registry,
        ) as credential_manager:
            credential_manager.create(user_id=user.id, password=password)

    if hasattr(user, "model_dump"):
        user_dict = user.model_dump()
    elif isinstance(user, dict):
        user_dict = user
    else:
        user_dict = {
            field: getattr(user, field, None) for field in UserModel.model_fields.keys()
        }

    return UserWithJWT(**user_dict, jwt=authorize_user(server, user.email))


# The env names of the internal accounts, which no sign-in may reach.
INTERNAL_ACCOUNTS = ("ROOT_ID", "SYSTEM_ID", "TEMPLATE_ID")


@contextmanager
def internal_account_email(model_registry: Any, user_id: str) -> Iterator[str]:
    """Give the internal account ``user_id`` (ROOT, SYSTEM, the template
    user) a real address while the block runs, and yield it. The test
    APP_URI has no domain, so the seeded ``root@`` is no address; in a
    deployment it is a predictable one an external identity could assert."""
    UserDB = UserModel.DB(model_registry.DB.manager.Base)
    seeded = UserDB.get(
        requester_id=env("ROOT_ID"), model_registry=model_registry, id=user_id
    )["email"]
    email = generate_test_email("internal")
    UserDB.update(
        requester_id=env("ROOT_ID"),
        model_registry=model_registry,
        id=user_id,
        new_properties={"email": email},
    )
    try:
        yield email
    finally:
        UserDB.update(
            requester_id=env("ROOT_ID"),
            model_registry=model_registry,
            id=user_id,
            new_properties={"email": seeded},
        )


def authorize_user(server, email: str, password=TEST_PASSWORD):
    credentials = f"{email}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    response = server.post(
        "/v1/user/authorize", headers={"Authorization": f"Basic {encoded_credentials}"}
    )
    assert "token" in response.json(), "JWT token missing from authorization response."
    return response.json()["token"]


def if_match_of(row: Any) -> Dict[str, str]:
    """The If-Match header a client sends to save (PUT/PATCH/DELETE) a row
    it holds (a serialised row or a model): the row's version,
    ``"<updated_at ?? created_at>"``."""
    etag = entity_etag(row)
    if etag is None:
        raise ValueError(f"the row carries no version: {row!r}")
    return {IF_MATCH_HEADER: etag}


def current_if_match(
    server: TestClient, url: str, headers: Mapping[str, str]
) -> Dict[str, str]:
    """The If-Match header a correct client sends to save the record at
    ``url``: it reads the record (GET, as ``headers``' caller) and names
    the version the read answered with (its ETag)."""
    response = server.get(url, headers=dict(headers))
    assert response.status_code == 200, f"GET {url}: {response.text}"
    etag = response.headers.get("etag")
    assert etag is not None, f"GET {url} answered with no ETag"
    return {IF_MATCH_HEADER: etag}


def create_team(server, user_id, name="Test Team", parent_id=None):
    faker = Faker()
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    Team = TeamModel.DB(model_registry.DB.manager.Base)
    team = Team.create(
        requester_id=user_id,
        model_registry=model_registry,
        return_type="dto",
        override_dto=TeamModel,
        name=name,
        description=faker.catch_phrase(),
        created_by_user_id=user_id,
        parent_id=parent_id,
    )
    add_user_to_team(server, user_id, team.id, env("ADMIN_ROLE_ID"))
    return team


def create_role(
    server,
    user_id,
    team_id,
    name="mod",
    friendly_name="Moderator",
    parent_id=env("USER_ROLE_ID"),
):
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    Role = RoleModel.DB(model_registry.DB.manager.Base)
    return Role.create(
        requester_id=user_id,
        model_registry=model_registry,
        return_type="dto",
        override_dto=RoleModel,
        name=name,
        friendly_name=friendly_name,
        parent_id=parent_id,
        team_id=team_id,
    )


def add_user_to_team(server, user_id, team_id, role_id, requester_id=env("SYSTEM_ID")):
    model_registry = getattr(server.app.state, "model_registry", None)
    if model_registry is None:
        raise RuntimeError("model_registry not found in server app state")

    UserTeam = UserTeamModel.DB(model_registry.DB.manager.Base)
    existing_membership = UserTeam.list(
        requester_id=requester_id,
        model_registry=model_registry,
        user_id=user_id,
        team_id=team_id,
    )

    if existing_membership:
        from zephyrex.logic.BLL_Auth import UserTeamManager

        user_team_manager = UserTeamManager(
            requester_id=requester_id,
            model_registry=model_registry,
        )
        return user_team_manager.update(
            id=existing_membership[0].id,
            role_id=role_id,
            enabled=True,
        )
    else:
        return UserTeam.create(
            requester_id=user_id,
            model_registry=model_registry,
            return_type="dto",
            override_dto=UserTeamModel,
            user_id=user_id,
            team_id=team_id,
            role_id=role_id,
        )


def provider_instance_as(
    model_registry: Any,
    provider_name: str,
    settings: Optional[Dict[str, str]] = None,
    *,
    requester_id: Optional[str] = None,
    scope: str = "root",
    team_id: Optional[str] = None,
    api_key: Optional[str] = None,
) -> ProviderInstanceModel:
    """A new instance of the provider ``provider_name`` in ``scope``, made
    (and its ``settings`` written) by ``requester_id``, ROOT by default,
    through the managers that check those writes. A user's user-scoped
    instance is theirs; ``team_id`` makes it the team's."""
    author = requester_id or env("ROOT_ID")
    provider = ProviderManager(
        model_registry=model_registry, requester_id=env("ROOT_ID")
    ).get(name=provider_name)
    fields: Dict[str, Any] = {
        "name": f"{provider_name}_{uuid.uuid4().hex}",
        "provider_id": provider.id,
        "scope": scope,
    }
    if requester_id is not None and scope == "user":
        fields["user_id"] = requester_id
    if team_id is not None:
        fields["team_id"] = team_id
    if api_key is not None:
        fields["api_key"] = api_key
    instance = ProviderInstanceModel.model_validate(
        ProviderInstanceManager(
            model_registry=model_registry, requester_id=author
        ).create(**fields),
        from_attributes=True,
    )
    rows = ProviderInstanceSettingManager(
        model_registry=model_registry, requester_id=author
    )
    for key, value in (settings or {}).items():
        rows.create(provider_instance_id=instance.id, key=key, value=value)
    return instance


def bind_test_models(registry, *models):
    for model in models:
        registry.bind(model)


def create_test_extension_server(extension_names):
    from zephyrex.app import instance

    extensions = (
        ",".join(extension_names)
        if isinstance(extension_names, list)
        else extension_names
    )
    first_extension = (
        extension_names[0]
        if isinstance(extension_names, list)
        else extension_names.split(",")[0]
    ).strip()
    db_prefix = f"test.{first_extension}"
    return TestClient(instance(db_prefix=db_prefix, extensions=extensions))


# ---- Shared fixture factory functions ----


def make_admin_a(server):
    return create_user(server, email=generate_test_email("admin_a"), last_name="AdminA")


def make_team_a(server, admin_a):
    return create_team(server, admin_a.id, name="Team A")


def make_admin_b(server):
    return create_user(server, email=generate_test_email("admin_b"), last_name="AdminB")


def make_team_b(server, admin_b):
    return create_team(server, admin_b.id, name="Team B")


def make_user_b(server, team_b):
    user = create_user(server, email=generate_test_email("user_b"), last_name="UserB")
    add_user_to_team(server, user.id, team_b.id, env("USER_ROLE_ID"))
    return user


def make_mod_b_role(server, admin_a, team_b):
    return create_role(
        server,
        admin_a.id,
        team_b.id,
        name="mod_b",
        friendly_name="Moderator B",
        parent_id=env("USER_ROLE_ID"),
    )


def make_mod_b(server, admin_b, team_b, mod_b_role):
    user = create_user(server, email=generate_test_email("mod_b"), last_name="ModB")
    add_user_to_team(server, user.id, team_b.id, mod_b_role.id, requester_id=admin_b.id)
    return user
