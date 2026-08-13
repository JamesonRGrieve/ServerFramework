import json
import uuid
from typing import Any

import pytest

from zephyrex.endpoints.AbstractEPTest import AbstractEPTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.meta_labels.BLL_Meta_Labels import LabelModel
from zephyrex.extensions.meta_labels.EXT_Meta_Labels import EXT_Meta_Labels


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
    parent_entities: list[str] = []
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

    def create_payload(self, name=None, parent_ids=None, team_id=None, minimal=False, invalid_data=False):
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

        mutation = f'''
        mutation {{
            {mutation_name}(input: {{name: "{label_name}", description: "{description}"}}) {{
                id
                name
                description
                createdAt
                updatedAt
            }}
        }}
        '''

        headers = self._get_appropriate_headers(admin_a.jwt)
        response = server.post("/graphql", json={"query": mutation}, headers=headers)
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"
        if "errors" in data:
            pytest.fail(f"GraphQL errors in create mutation: {json.dumps(data['errors'])}")

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert mutation_name in data["data"], f"Mutation {mutation_name} not in response"

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
            f"/v1/{self.base_endpoint}", json=payload, headers=self._get_appropriate_headers(admin_a.jwt)
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

    @pytest.mark.skip(reason="Requires prompts extension which is not loaded in meta_labels test suite")
    def test_POST_204_associate_with_prompt(self, server, admin_a, team_a):
        """Test associating a label with a prompt."""
        pass

    @pytest.mark.skip(reason="Requires prompts extension which is not loaded in meta_labels test suite")
    def test_DELETE_204_remove_from_prompt(self, server, admin_a, team_a):
        """Test removing a label from a prompt."""
        pass

    @pytest.mark.skip(reason="Requires prompts extension which is not loaded in meta_labels test suite")
    def test_GET_200_includes(self, server, admin_a, team_a, navigation_property):
        """Test GET label with included entities (e.g., prompts)."""
        pass
