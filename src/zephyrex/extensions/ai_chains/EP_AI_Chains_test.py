# import uuid
# import pytest
# from endpoints.AbstractEPTest import AbstractEndpointTest
# @pytest.mark.chains
#     base_endpoint = "chain"
#     entity_name = "chain"
#     required_fields = ["id", "name"]
#     string_field_to_update = "name"
#     searchable_fields = ["name"]
#     # No parent entities for chains
#     parent_entities = []
#     # Not a system entity
#     system_entity = False
#     def create_payload(self, name=None, parent_ids=None, team_id=None):
#         if not name:
#             name = testdata.get_name(1)
#         if team_id:
#             payload["team_id"] = team_id
#     def test_POST_201_import_chain(self, server, admin_a_jwt, team_a):
#         """Test importing a chain with steps."""
#         # Create an agent first if needed for chain steps
#         agent_name = f"Test Agent {uuid.uuid4()}"
#             "agent": {
#                 "description": "Test agent for chain steps",
#                 "provider": "OpenAI",
#             }
#         agent_response = server.post(
#             "/v1/agent", json=agent_payload, headers=self._auth_header(admin_a_jwt)
#         )
#         # If agent creation fails, skip this test
#         if agent_response.status_code != 201:
#             pytest.skip("Failed to create agent for chain steps")
#         agent = agent_response.json()["agent"]
#         # Create a chain with import
#         chain_name = f"Imported Chain {uuid.uuid4()}"
#         import_payload = {
#             "chain": {
#                 "chain_name": chain_name,
#                     "steps": [
#                         {
#                             "step": 1,
#                             "prompt_type": "prompt",
#                             "prompt": {
#                                 "prompt_name": "Think About It",
#                             },
#                         {
#                             "step": 2,
#                             "agent_name": agent_name,
#                             "prompt_type": "prompt",
#                             "prompt": {
#                                 "prompt_name": "Problem Solver",
#                                 "prompt_category": "Default",
#                             },
#                         },
#                     ]
#                 },
#             }
#         }
#         endpoint = "/v1/chain/import"
#         response = server.post(
#             endpoint, json=import_payload, headers=self._auth_header(admin_a_jwt)
#         )
#         self._assert_response_status(
#             response, 201, "POST import chain", endpoint, import_payload
#         )
#         # The import endpoint returns a message
#         assert "message" in response.json(), (
#             f"[{self.entity_name}] Message not found in import response\n"
#             f"Response: {response.json()}"
#         )
#         # Get the chain to verify it was created
#         get_endpoint = f"/v1/chain/{chain_name}"
#         get_response = server.get(get_endpoint, headers=self._auth_header(admin_a_jwt))
#         self._assert_response_status(
#         )
#         chain = self._assert_entity_in_response(get_response)
#         assert chain["name"] == chain_name, (
#             f"[{self.entity_name}] Chain name mismatch\n"
#             f"Got: {chain['name']}"
#         )
#         # Check if steps were imported
#             f"[{self.entity_name}] Steps not found in imported chain\n"
#             f"Chain: {chain}"
#         )
#         assert len(chain["steps"]) == 2, (
#             f"[{self.entity_name}] Expected 2 steps, got {len(chain['steps'])}\n"
#         )
#         return chain
#     def test_POST_201_add_step(self, server, admin_a_jwt, team_a):
#         # First create a chain
#         chain = self.test_POST_201(server, admin_a_jwt, team_a)
#         # Create an agent for the step
#         agent_payload = {
#             "agent": {
#                 "name": agent_name,
#                 "description": "Test agent for chain steps",
#                 "provider": "OpenAI",
#                 "model": "gpt-4",
#         }
#         agent_response = server.post(
#             "/v1/agent", json=agent_payload, headers=self._auth_header(admin_a_jwt)
#         )
#         # If agent creation fails, skip this test
#             pytest.skip("Failed to create agent for chain steps")
#         # Add a step to the chain
#         step_payload = {
#             "step_number": 1,
#             "prompt_type": "prompt",
#         }
#         endpoint = f"/v1/chain/{chain['name']}/step"
#         response = server.post(
#             endpoint, json=step_payload, headers=self._auth_header(admin_a_jwt)
#         self._assert_response_status(
#             response, 201, "POST add step", endpoint, step_payload
#         )
#         # The add step endpoint returns a message
#         assert "message" in response.json(), (
#             f"[{self.entity_name}] Message not found in add step response\n"
#             f"Response: {response.json()}"
#         )
#         # Get the chain to verify the step was added
#         get_endpoint = f"/v1/chain/{chain['name']}"
#         self._assert_response_status(
#             get_response, 200, "GET chain with step", get_endpoint
#         )
#         assert "steps" in updated_chain, (
#             f"[{self.entity_name}] Steps not found in chain\n" f"Chain: {updated_chain}"
#         )
#             f"[{self.entity_name}] No steps found in chain\n" f"Chain: {updated_chain}"
#         )
#         # Check if our step is in the list
#         step = updated_chain["steps"][0]
#         assert step["step"] == 1, (
#             f"[{self.entity_name}] Step number mismatch\n"
#             f"Expected: 1\n"
#         )
#         assert step["agent_name"] == agent_name, (
#             f"[{self.entity_name}] Agent name mismatch\n"
#             f"Expected: {agent_name}\n"
#         )
#         return updated_chain
#     def test_PUT_200_update_step(self, server, admin_a_jwt, team_a):
#         # First create a chain with a step
#         chain = self.test_POST_201_add_step(server, admin_a_jwt, team_a)
#         # Create another agent for the updated step
#         agent_name = f"Updated Agent {uuid.uuid4()}"
#         agent_payload = {
#                 "name": agent_name,
#                 "description": "Updated agent for chain steps",
#                 "provider": "OpenAI",
#             }
#         }
#         agent_response = server.post(
#         )
#         # If agent creation fails, skip this test
#         if agent_response.status_code != 201:
#             pytest.skip("Failed to create agent for updated step")
#         update_payload = {
#             "step_number": 1,
#             "agent_name": agent_name,
#             "prompt": {"prompt_name": "Problem Solver", "prompt_category": "Default"},
#         }
#         endpoint = f"/v1/chain/{chain['name']}/step/1"
#         response = server.put(
#             endpoint, json=update_payload, headers=self._auth_header(admin_a_jwt)
#         )
#         self._assert_response_status(
#         )
#         # The update step endpoint returns a message
#         assert "message" in response.json(), (
#             f"[{self.entity_name}] Message not found in update step response\n"
#             f"Response: {response.json()}"
#         # Get the chain to verify the step was updated
#         get_response = server.get(get_endpoint, headers=self._auth_header(admin_a_jwt))
#         self._assert_response_status(
#             get_response, 200, "GET chain with updated step", get_endpoint
#         )
#         # Check if our step was updated
#         step = updated_chain["steps"][0]
#         assert step["agent_name"] == agent_name, (
#             f"[{self.entity_name}] Agent name not updated\n"
#             f"Expected: {agent_name}\n"
#             f"Got: {step['agent_name']}"
#         )
#         assert step["prompt"]["prompt_name"] == "Problem Solver", (
#             f"[{self.entity_name}] Prompt name not updated\n"
#             f"Expected: Problem Solver\n"
#         )
#         return updated_chain
#     def test_PATCH_200_move_step(self, server, admin_a_jwt, team_a):
#         # First create a chain with at least two steps
#         chain = self.test_POST_201_import_chain(server, admin_a_jwt, team_a)
#         # Move the first step to position 2
#         endpoint = f"/v1/chain/{chain['name']}/step/move"
#         response = server.patch(
#             endpoint, json=move_payload, headers=self._auth_header(admin_a_jwt)
#         )
#         self._assert_response_status(
#             response, 200, "PATCH move step", endpoint, move_payload
#         )
#         assert "message" in response.json(), (
#             f"[{self.entity_name}] Message not found in move step response\n"
#             f"Response: {response.json()}"
#         )
#         get_endpoint = f"/v1/chain/{chain['name']}"
#         get_response = server.get(get_endpoint, headers=self._auth_header(admin_a_jwt))
#         self._assert_response_status(
#         )
#         updated_chain = self._assert_entity_in_response(get_response)
#         # Check if steps were reordered correctly
#         first_step = updated_chain["steps"][0]
#         second_step = updated_chain["steps"][1]
#             f"[{self.entity_name}] First step number mismatch\n"
#             f"Expected: 1\n"
#             f"Got: {first_step['step']}"
#         assert second_step["step"] == 2, (
#             f"[{self.entity_name}] Second step number mismatch\n"
#             f"Expected: 2\n"
#         )
#         assert second_step["prompt"]["prompt_name"] == "Think About It", (
#             f"[{self.entity_name}] Step was not moved correctly\n"
#             f"Expected prompt name: Think About It\n"
#             f"Got: {second_step['prompt']['prompt_name']}"
#         )
#         return updated_chain
#     def test_DELETE_200_step(self, server, admin_a_jwt, team_a):
#         # First create a chain with a step
#         chain = self.test_POST_201_add_step(server, admin_a_jwt, team_a)
#         # Delete the step
#         endpoint = f"/v1/chain/{chain['name']}/step/1"
#         response = server.delete(endpoint, headers=self._auth_header(admin_a_jwt))
#         # The delete step endpoint returns a message
#             f"[{self.entity_name}] Message not found in delete step response\n"
#             f"Response: {response.json()}"
#         )
#         # Get the chain to verify the step was deleted
#         get_response = server.get(get_endpoint, headers=self._auth_header(admin_a_jwt))
#         self._assert_response_status(
#         )
#         updated_chain = self._assert_entity_in_response(get_response)
#         # Check if the step was deleted
#         assert len(updated_chain["steps"]) == 0, (
#             f"Steps: {updated_chain['steps']}"
#         )
#         return updated_chain
#         """Test retrieving chain arguments."""
#         # First create a chain with steps
#         chain = self.test_POST_201_import_chain(server, admin_a_jwt, team_a)
#         # Get the chain arguments
#         endpoint = f"/v1/chain/{chain['name']}/args"
#         self._assert_response_status(response, 200, "GET chain args", endpoint)
#         json_response = response.json()
#         assert "chain_args" in json_response, (
#             f"Response: {json_response}"
#         )
#         chain_args = json_response["chain_args"]
#             f"[{self.entity_name}] Chain args should be a list\n"
#         )
#         return chain_args
