import uuid

import pytest

from serverframework.endpoints.AbstractEPTest import AbstractEPTest


@pytest.mark.labels
class TestLabelEP(AbstractEPTest):
    base_endpoint = "label"
    entity_name = "label"
    required_fields = ["id", "name", "created_at", "updated_at"]
    string_field_to_update = "name"
    searchable_fields = ["name"]
    # No parent entities for labels
    parent_entities = []
    # Not a system entity
    system_entity = False

    def create_payload(self, name=None, parent_ids=None, team_id=None):
        if not name:
            name = self.generate_name()
        payload = {
            "name": name,
            "description": f"Description for {name}",
        }
        if team_id:
            payload["team_id"] = team_id
        return self.nest_payload_in_entity(entity=payload)

    def test_POST_201_create_with_color(self, server, jwt_a, team_a):
        """Test creating a label with a specific color."""
        label_name = f"Colored Label {uuid.uuid4()}"
        color = "#3399FF"  # Blue color
        payload = self.nest_payload_in_entity(
            entity={
                "name": label_name,
                "description": f"A label with a specific color",
                "color": color,
            }
        )
        response = server.post(
            f"/v1/{self.base_endpoint}", json=payload, headers=self._auth_header(jwt_a)
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
        return entity

    def _create_prompt(self, server, jwt_a):
        prompt_name = f"Test Prompt {uuid.uuid4()}"
        from extensions.prompts.test_ep_prompts import TestPromptEP

        prompt_ep_test = TestPromptEP()
        prompt_payload = prompt_ep_test.create_payload(name=prompt_name)
        response = server.post(
            "/v1/prompt", json=prompt_payload, headers=self._auth_header(jwt_a)
        )
        prompt_ep_test._assert_response_status(
            response, 201, "POST create prompt helper", "/v1/prompt"
        )
        return prompt_ep_test._assert_entity_in_response(response)

    def test_POST_204_associate_with_prompt(self, server, jwt_a, team_a):
        """Test associating a label with a prompt."""
        # First create a label
        label_payload = self.create_payload()
        label_response = server.post(
            f"/v1/{self.base_endpoint}",
            json=label_payload,
            headers=self._auth_header(jwt_a),
        )
        self._assert_response_status(
            label_response, 201, "POST create label for association"
        )
        label = self._assert_entity_in_response(label_response)

        # Then create a prompt
        prompt = self._create_prompt(server, jwt_a)

        # Now associate the label with the prompt
        endpoint = f"/v1/{self.base_endpoint}/{label['id']}/prompt/{prompt['id']}"
        associate_response = server.post(endpoint, headers=self._auth_header(jwt_a))
        self._assert_response_status(
            associate_response, 204, "POST associate label with prompt", endpoint
        )

        # Verify the association by getting the prompt with includes
        get_endpoint = f"/v1/prompt/{prompt['id']}?include=labels"
        get_response = server.get(get_endpoint, headers=self._auth_header(jwt_a))
        self._assert_response_status(
            get_response, 200, "GET prompt with labels", get_endpoint
        )
        prompt_with_labels = self._assert_entity_in_response(get_response)
        assert "labels" in prompt_with_labels, (
            f"[{self.entity_name}] Labels not included in prompt response\n"
            f"Prompt: {prompt_with_labels}"
        )
        assert isinstance(prompt_with_labels["labels"], list)
        label_ids_in_prompt = [lbl["id"] for lbl in prompt_with_labels["labels"]]
        assert label["id"] in label_ids_in_prompt, (
            f"[{self.entity_name}] Associated Label ID mismatch\n"
            f"Expected: {label['id']}\n"
            f"Found: {label_ids_in_prompt}"
        )
        return {"label": label, "prompt": prompt}

    def test_DELETE_204_remove_from_prompt(self, server, jwt_a, team_a):
        """Test removing a label from a prompt."""
        # First associate a label with a prompt
        result = self.test_POST_204_associate_with_prompt(server, jwt_a, team_a)
        label = result["label"]
        prompt = result["prompt"]

        # Then remove the association
        endpoint = f"/v1/{self.base_endpoint}/{label['id']}/prompt/{prompt['id']}"
        response = server.delete(endpoint, headers=self._auth_header(jwt_a))
        self._assert_response_status(
            response, 204, "DELETE remove label from prompt", endpoint
        )

        # Verify the association was removed
        get_endpoint = f"/v1/prompt/{prompt['id']}?include=labels"
        get_response = server.get(get_endpoint, headers=self._auth_header(jwt_a))
        self._assert_response_status(
            get_response, 200, "GET prompt after removing label", get_endpoint
        )
        prompt_without_label = self._assert_entity_in_response(get_response)
        assert "labels" not in prompt_without_label or not any(
            lbl["id"] == label["id"] for lbl in prompt_without_label.get("labels", [])
        ), (
            f"[{self.entity_name}] Label still associated with prompt after removal\n"
            f"Label ID: {label['id']}\n"
            f"Prompt: {prompt_without_label}"
        )
        return {"label": label, "prompt": prompt}

    def test_GET_200_includes(self, server, jwt_a, team_a):
        """Test GET label with included entities (e.g., prompts)."""
        # First associate a label with a prompt
        result = self.test_POST_204_associate_with_prompt(server, jwt_a, team_a)
        label = result["label"]

        # Get the label with prompts included
        endpoint = f"/v1/{self.base_endpoint}/{label['id']}?include=prompts"
        response = server.get(endpoint, headers=self._auth_header(jwt_a))
        self._assert_response_status(response, 200, "GET with includes", endpoint)
        json_response = response.json()
        entity = self._assert_entity_in_response(json_response)
        assert "prompts" in entity, (
            f"[{self.entity_name}] Prompts not included in response\n"
            f"Entity: {entity}"
        )
        assert isinstance(entity["prompts"], list), (
            f"[{self.entity_name}] Prompts should be a list\n"
            f"Prompts: {entity['prompts']}"
        )
        assert len(entity["prompts"]) > 0, (
            f"[{self.entity_name}] No prompts found in included prompts\n"
            f"Entity: {entity}"
        )

        # Check if our prompt is in the list
        prompt_ids = [p["id"] for p in entity["prompts"]]
        assert result["prompt"]["id"] in prompt_ids, (
            f"[{self.entity_name}] Associated prompt not found in included prompts\n"
            f"Expected prompt ID: {result['prompt']['id']}\n"
            f"Found prompt IDs: {prompt_ids}"
        )
