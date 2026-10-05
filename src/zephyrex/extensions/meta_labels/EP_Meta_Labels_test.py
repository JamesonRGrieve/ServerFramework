import json
import uuid
from typing import Any, List

import pytest

from zephyrex.AbstractTest import ParentEntity
from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.meta_labels.BLL_Meta_Labels import LabelModel
from zephyrex.extensions.meta_labels.EXT_Meta_Labels import EXT_Meta_Labels
from zephyrex.lib import Environment
from zephyrex.lib.Preconditions import IF_MATCH_REQUIRED_SETTING
from zephyrex.testing.factories import current_if_match


@pytest.mark.labels
class TestLabelEP(AbstractEPTest, ExtensionServerMixin):
    extension_class = EXT_Meta_Labels
    base_endpoint = "labels"
    entity_name = "label"
    class_under_test = LabelModel
    required_fields = ["id", "name", "created_at", "updated_at"]
    string_field_to_update = "description"
    searchable_fields = ["name"]
    # No parent entities for labels
    parent_entities: List[ParentEntity] = []
    # Not a system entity
    system_entity = False

    create_fields = {
        "name": lambda: f"Test Label {uuid.uuid4()}",
        "description": lambda: f"Description for test label {uuid.uuid4()}",
    }
    update_fields = {
        "description": lambda: f"Updated description {uuid.uuid4()}",
        "color": "#FF0000",
    }
    unique_fields = ["name"]

    def create_payload(
        self,
        name=None,
        parent_ids=None,
        team_id=None,
        minimal=False,
        invalid_data=False,
    ):
        if not name:
            name = f"Test Label {uuid.uuid4()}"
        if invalid_data:
            return {
                "name": 12345,
                "description": True,
            }
        if minimal:
            return {"name": name}
        payload = {
            "name": name,
            "description": f"Description for {name}",
        }
        return payload

    def test_GQL_mutation_create(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL create mutation for labels.

        Overridden because the abstract test only sends string_field_to_update
        as input for parentless entities, but LabelModel.Create requires name.
        """
        mutation_name = "createLabel"
        label_name = f"GQL Test {self.faker.word()} {uuid.uuid4()}"
        description = f"GQL Test {self.faker.word()}"

        mutation = f"""
        mutation {{
            {mutation_name}(input: {{name: "{label_name}", description: "{description}"}}) {{
                id
                name
                description
                createdAt
                updatedAt
            }}
        }}
        """

        headers = self._get_appropriate_headers(admin_a.jwt)
        response = server.post("/graphql", json={"query": mutation}, headers=headers)
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in create mutation: {json.dumps(data['errors'])}"
            )

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            mutation_name in data["data"]
        ), f"Mutation {mutation_name} not in response"

        result = data["data"][mutation_name]
        assert result is not None, "Mutation result is None"
        assert "id" in result, "Created entity missing ID"
        assert result["name"] == label_name
        assert result["description"] == description

    def test_POST_201_create_with_color(self, server, admin_a, team_a):
        """Test creating a label with a specific color."""
        label_name = f"Colored Label {uuid.uuid4()}"
        color = "#3399FF"  # Blue color
        payload = {
            "name": label_name,
            "description": "A label with a specific color",
            "color": color,
        }
        response = server.post(
            f"/v1/{self.base_endpoint}",
            json=payload,
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        self._assert_response_status(
            response, 201, "POST with color", f"/v1/{self.base_endpoint}", payload
        )
        entity = self._assert_entity_in_response(response)
        assert entity["color"] == color, (
            f"[{self.entity_name}] Color mismatch\n"
            f"Expected: {color}\n"
            f"Got: {entity['color']}\n"
            f"Entity: {entity}"
        )

    def test_detach_is_held_to_the_link_version(self, server, admin_a, monkeypatch):
        """Regression: DELETE /v1/labels/{label_id}/attach/{type}/{target_id}
        names the link by its label and target, so nothing bound its
        If-Match and a stale detach (or one naming no version) went
        through. It is held to the link row's version, which a client reads
        at /v1/label-links/{link_id}."""
        monkeypatch.setattr(Environment.settings, IF_MATCH_REQUIRED_SETTING, "true")
        headers = self._get_appropriate_headers(admin_a.jwt)
        label = self._create(server, admin_a.jwt, admin_a.id, key="detach_versioned")
        url = f"/v1/{self.base_endpoint}/{label['id']}/attach/note/{uuid.uuid4()}"
        attached = server.post(url, json={}, headers=headers)
        assert attached.status_code in (200, 201), attached.text
        link_url = f"/v1/label-links/{attached.json()['link_id']}"
        version = current_if_match(server, link_url, headers)

        stale = server.delete(
            url, headers={**headers, "If-Match": '"1970-01-01T00:00:00"'}
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["current"]["id"] == attached.json()["link_id"]
        missing = server.delete(url, headers=headers)
        assert missing.status_code == 428, missing.text
        assert server.get(link_url, headers=headers).status_code == 200

        detached = server.delete(url, headers={**headers, **version})
        assert detached.status_code == 200, detached.text
        assert detached.json()["detached"] is True
        assert server.get(link_url, headers=headers).status_code == 404

    def test_a_label_attaches_and_detaches_as_its_requester_sees_it(
        self, server, admin_a, admin_b
    ):
        """Attach and detach looked the label and its links up as ROOT: anyone
        could attach any label, learn whether a label id existed, and be
        handed another user's link id by attaching where theirs already
        was. They now answer as the requester sees the label and its links."""
        headers_a = self._get_appropriate_headers(admin_a.jwt)
        headers_b = self._get_appropriate_headers(admin_b.jwt)
        label = self._create(server, admin_a.jwt, admin_a.id, key="attach_as_seen")
        linked = f"/v1/{self.base_endpoint}/{label['id']}/attach/note/{uuid.uuid4()}"
        attached = server.post(linked, json={}, headers=headers_a)
        assert attached.status_code in (200, 201), attached.text
        link_id = attached.json()["link_id"]

        onto_theirs = server.post(linked, json={}, headers=headers_b)
        assert onto_theirs.status_code == 404, onto_theirs.text
        assert link_id not in onto_theirs.text
        fresh = f"/v1/{self.base_endpoint}/{label['id']}/attach/note/{uuid.uuid4()}"
        onto_new = server.post(fresh, json={}, headers=headers_b)
        assert onto_new.status_code == 404, onto_new.text
        missing = f"/v1/{self.base_endpoint}/{uuid.uuid4()}/attach/note/{uuid.uuid4()}"
        assert server.post(missing, json={}, headers=headers_b).text == onto_new.text

        detach = server.delete(linked, headers={**headers_b, "If-Match": "*"})
        assert detach.status_code == 200, detach.text
        assert detach.json()["detached"] is False
        link_url = f"/v1/label-links/{link_id}"
        assert server.get(link_url, headers=headers_a).status_code == 200

    @pytest.mark.skip(
        reason="Requires prompts extension which is not loaded in meta_labels test suite"
    )
    def test_POST_204_associate_with_prompt(self, server, admin_a, team_a):
        """Test associating a label with a prompt."""
        pass

    @pytest.mark.skip(
        reason="Requires prompts extension which is not loaded in meta_labels test suite"
    )
    def test_DELETE_204_remove_from_prompt(self, server, admin_a, team_a):
        """Test removing a label from a prompt."""
        pass

    @pytest.mark.skip(
        reason="Requires prompts extension which is not loaded in meta_labels test suite"
    )
    def test_GET_200_includes(self, server, admin_a, team_a, navigation_property):
        """Test GET label with included entities (e.g., prompts)."""
        pass
