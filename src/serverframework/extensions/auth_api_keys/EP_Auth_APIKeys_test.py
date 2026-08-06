# import uuid
# import pytest
# from endpoints.AbstractEPTest import AbstractEndpointTest, ParentEntity
# @pytest.mark.ep
# @pytest.mark.auth
# class TestKeyEndpoints(AbstractEPTest):
#     """Tests for the API Key Management endpoints."""
#     base_endpoint = "key"
#     entity_name = "api_key"
#     required_fields = ["id", "name", "key_hash", "created_at"]
#     string_field_to_update = "name"
#     # Parent entities
#     parent_entities = [
#         ParentEntity(
#             name="user",
#             key="user_id",
#             nullable=True,
#             system=False,
#             is_path=False,
#         ),
#         ParentEntity(
#             name="team",
#             key="team_id",
#             nullable=True,
#             system=False,
#             is_path=False,
#         ),
#     ]
#     def create_parent_entities(self, server, jwt_a, team_a):
#         """Create parent entities for API key testing."""
#         # Get current user
#         user_response = server.get("/v1/user/me", headers=self._auth_header(jwt_a))
#         user = user_response.json()["user"]
#         return {"user": user, "team": team_a}
#     def create_payload(self, name=None, parent_ids=None, team_id=None):
#         """Create a payload for API key creation."""
#         if not name:
#             name = f"Test API Key {uuid.uuid4()}"
#         payload = {"name": name}
#         # Add user_id if provided
#         if parent_ids and "user_id" in parent_ids:
#             payload["user_id"] = parent_ids["user_id"]
#         # Add team_id if provided (but not both user_id and team_id)
#         if parent_ids and "team_id" in parent_ids and "user_id" not in payload:
#             payload["team_id"] = parent_ids["team_id"]
#         return self.nest_payload_in_entity(entity=payload)
#     def test_POST_201_generate_key(self, server, jwt_a, team_a):
#         """Test generating an API key."""
#         name = f"Generated API Key {uuid.uuid4()}"
#         # Get current user
#         user_response = server.get("/v1/user/me", headers=self._auth_header(jwt_a))
#         user_id = user_response.json()["user"]["id"]
#         payload = {"name": name, "user_id": user_id}
#         response = server.post(
#             "/v1/key/generate", json=payload, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#             response, 201, "POST generate key", "/v1/key/generate", payload
#         )
#         result = response.json()
#         assert "name" in result, "Missing name in response"
#         assert "raw_key" in result, "Missing raw_key in response"
#         assert result["name"] == name, "Name mismatch in response"
#         return result
#     def test_POST_200_validate_key(self, server, jwt_a, team_a):
#         """Test validating an API key."""
#         # First generate an API key
#         api_key_result = self.test_POST_201_generate_key(server, jwt_a, team_a)
#         raw_key = api_key_result["raw_key"]
#         # Validate the key
#         payload = {"api_key": raw_key}
#         response = server.post(
#             "/v1/api/validate", json=payload, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#             response, 200, "POST validate key", "/v1/api/validate", payload
#         )
#         result = response.json()
#         assert "valid" in result, "Missing valid in response"
#         assert result["valid"] == True, "API key validation failed"
#         return result
