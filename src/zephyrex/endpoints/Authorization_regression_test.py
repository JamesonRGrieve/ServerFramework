# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authorization holds on every generic route path.

Three bypasses lived in the generic route/manager layer:

* The entity cache answered ``manager.get`` by id with no access check, so
  once any permitted read cached a row, every other user could read it.
* The UPDATE route retried a 404 (the permission layer hiding the row) as
  ROOT, so any user could write any row they could not see.
* A nested GET ignored its parent path segment, so a child was served under
  any parent's URL.
"""

import os
import uuid
from typing import Any, Dict, Optional

import pytest

from zephyrex.lib.Environment import env
from zephyrex.logic.AbstractLogicManager import manager as manager_module
from zephyrex.logic.AbstractLogicManager import set_entity_cache
from zephyrex.testing.factories import TEST_PASSWORD


class InMemoryEntityCache:
    """The entity-cache interface, held in process: what the managers see."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.index: Dict[str, str] = {}

    async def get_by_id(self, table: str, entity_id: str) -> Optional[dict]:
        return self.rows.get(f"{table}:{entity_id}")

    async def get_by_field(self, table: str, field: str, value: str) -> Optional[str]:
        return self.index.get(f"{table}:{field}:{value}")

    async def get_by_field_full(
        self, table: str, field: str, value: str
    ) -> Optional[dict]:
        entity_id = await self.get_by_field(table, field, value)
        return None if entity_id is None else await self.get_by_id(table, entity_id)

    async def put(
        self,
        table: str,
        entity_id: str,
        dto_dict: dict,
        index_fields: Optional[dict] = None,
    ) -> None:
        self.rows[f"{table}:{entity_id}"] = dto_dict
        for field, value in (index_fields or {}).items():
            if value is not None:
                self.index[f"{table}:{field}:{value}"] = entity_id

    async def invalidate(
        self, table: str, entity_id: str, old_index_values: Optional[dict] = None
    ) -> None:
        self.rows.pop(f"{table}:{entity_id}", None)

    async def invalidate_table(self, table: str) -> None:
        for key in [k for k in self.rows if k.startswith(f"{table}:")]:
            del self.rows[key]


@pytest.fixture
def entity_cache():
    previous = manager_module._entity_cache
    cache = InMemoryEntityCache()
    set_entity_cache(cache)
    yield cache
    set_entity_cache(previous)


def _bearer(jwt: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _root() -> Dict[str, str]:
    return {"X-API-Key": os.environ["ROOT_API_KEY"]}


def _outsider(server) -> Any:
    """A fresh user with no team memberships (the shared admin fixtures can
    gain memberships from other tests in the session)."""
    from conftest import create_user

    return create_user(
        server=server,
        email=f"outsider_{uuid.uuid4().hex[:8]}@example.com",
        password=TEST_PASSWORD,
        first_name="Out",
        last_name="Sider",
    )


def test_cached_team_is_not_served_to_an_outsider(
    server, admin_a, team_a, entity_cache
):
    own = server.get(f"/v1/team/{team_a.id}", headers=_bearer(admin_a.jwt))
    assert own.status_code == 200, own.text
    # Populate the cache the way a permitted root read does.
    root_read = server.get(f"/v1/team/{team_a.id}", headers=_root())
    assert root_read.status_code == 200, root_read.text

    outsider = server.get(
        f"/v1/team/{team_a.id}", headers=_bearer(_outsider(server).jwt)
    )
    assert outsider.status_code in (403, 404), outsider.text


def test_update_of_an_invisible_row_is_not_retried_as_root(server, team_a):
    before = server.get(f"/v1/team/{team_a.id}", headers=_root()).json()["team"]

    response = server.put(
        f"/v1/team/{team_a.id}",
        json={"team": {"name": "taken over"}},
        headers=_bearer(_outsider(server).jwt),
    )
    assert response.status_code in (403, 404), response.text

    after = server.get(f"/v1/team/{team_a.id}", headers=_root()).json()["team"]
    assert after["name"] == before["name"]


def test_nested_get_is_scoped_to_its_parent(server, admin_a, team_a, team_b):
    created = server.post(
        f"/v1/team/{team_a.id}/invitation",
        json={"invitation": {"team_id": team_a.id, "role_id": env("USER_ROLE_ID")}},
        headers=_bearer(admin_a.jwt),
    )
    assert created.status_code == 201, created.text
    invitation_id = created.json()["invitation"]["id"]

    under_own_team = server.get(
        f"/v1/team/{team_a.id}/invitation/{invitation_id}", headers=_root()
    )
    assert under_own_team.status_code == 200, under_own_team.text

    under_other_team = server.get(
        f"/v1/team/{team_b.id}/invitation/{invitation_id}", headers=_root()
    )
    assert under_other_team.status_code == 404, under_other_team.text
