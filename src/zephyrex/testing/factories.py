"""Extracted test fixture factories and helpers.

Shared between conftest.py (session-scoped) and ExtensionServerMixin
(module-scoped). Each factory takes the server and any prerequisite
entities, returning the created object.
"""

import base64
import uuid

import pytest
from faker import Faker
from starlette.testclient import TestClient

from zephyrex.lib.Environment import env
from zephyrex.logic.BLL_Auth import (
    RoleModel,
    TeamModel,
    UserCredentialManager,
    UserModel,
    UserTeamModel,
)


def generate_test_email(prefix="test"):
    return f"{prefix}_{uuid.uuid4().hex[:8]}@example.com"


class UserWithJWT(UserModel):
    jwt: str


def create_user(
    server,
    email=None,
    password="testpassword",
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


def authorize_user(server, email: str, password="testpassword"):
    credentials = f"{email}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    response = server.post(
        "/v1/user/authorize", headers={"Authorization": f"Basic {encoded_credentials}"}
    )
    assert "token" in response.json(), "JWT token missing from authorization response."
    return response.json()["token"]


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
        encryption_salt=faker.uuid4(),
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
        server, admin_a.id, team_b.id,
        name="mod_b", friendly_name="Moderator B",
        parent_id=env("USER_ROLE_ID"),
    )


def make_mod_b(server, admin_b, team_b, mod_b_role):
    user = create_user(server, email=generate_test_email("mod_b"), last_name="ModB")
    add_user_to_team(server, user.id, team_b.id, mod_b_role.id, requester_id=admin_b.id)
    return user
