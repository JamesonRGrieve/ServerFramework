# import uuid
# import pytest
# from endpoints.AbstractEPTest import AbstractEndpointTest, ParentEntity
# @pytest.mark.ep
# @pytest.mark.auth
# class TestNotificationEndpoints(AbstractEPTest):
#     """Tests for the Notification Management endpoints."""
#     base_endpoint = "notification"
#     entity_name = "notification"
#     required_fields = ["id", "title", "content", "created_at"]
#     string_field_to_update = "title"
#     supports_search = True
#     searchable_fields = ["title", "content"]
#     # Parent entity for team notifications
#     parent_entities = [
#         ParentEntity(
#             name="team",
#             key="team_id",
#             nullable=True,
#             system=False,
#             is_path=False,
#         ),
#     ]
#     # Not a system entity
#     system_entity = False
#     def create_payload(self, name=None, parent_ids=None, team_id=None):
#         """Create a payload for notification creation."""
#         if not name:
#             name = self.generate_name()
#         payload = {"title": name, "content": f"Content for {name}"}
#         # Add team_id if provided
#         if team_id:
#             payload["team_id"] = team_id
#         elif parent_ids and "team_id" in parent_ids:
#             payload["team_id"] = parent_ids["team_id"]
#         return self.nest_payload_in_entity(entity=payload)
#     def create_parent_entities(self, server, jwt_a, team_a):
#         """Create parent entities for notification testing."""
#         # Use the existing team
#         return {"team": team_a}
#     def test_PATCH_200_read(self, server, jwt_a, team_a):
#         """Test marking a notification as read."""
#         # First create a notification
#         notification = self.test_POST_201(server, jwt_a, team_a)
#         # Mark it as read
#         endpoint = f"/v1/notification/{notification['id']}/read"
#         response = server.patch(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 200, "PATCH mark as read", endpoint)
#         # The endpoint returns a user_notification object
#         assert "user_notification" in response.json(), (
#             f"[{self.entity_name}] user_notification not found in read response\n"
#             f"Response: {response.json()}"
#         )
#         user_notification = response.json()["user_notification"]
#         assert "read" in user_notification, (
#             f"[{self.entity_name}] Read field not found in user_notification\n"
#             f"User notification: {user_notification}"
#         )
#         assert user_notification["read"] == True, (
#             f"[{self.entity_name}] Notification not marked as read\n"
#             f"User notification: {user_notification}"
#         )
#         return user_notification
#     def test_PATCH_200_acknowledge(self, server, jwt_a, team_a):
#         """Test acknowledging a notification."""
#         # First create a notification
#         notification = self.test_POST_201(server, jwt_a, team_a)
#         # Acknowledge it
#         endpoint = f"/v1/notification/{notification['id']}/acknowledge"
#         response = server.patch(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 200, "PATCH acknowledge", endpoint)
#         # The endpoint returns a user_notification object
#         assert "user_notification" in response.json(), (
#             f"[{self.entity_name}] user_notification not found in acknowledge response\n"
#             f"Response: {response.json()}"
#         )
#         user_notification = response.json()["user_notification"]
#         assert "acknowledged" in user_notification, (
#             f"[{self.entity_name}] Acknowledged field not found in user_notification\n"
#             f"User notification: {user_notification}"
#         )
#         assert user_notification["acknowledged"] == True, (
#             f"[{self.entity_name}] Notification not acknowledged\n"
#             f"User notification: {user_notification}"
#         )
#         return user_notification
#     def test_POST_201_with_null_team(self, server, jwt_a, team_a):
#         """Test creating a notification with null team_id."""
#         # Create a notification with null team_id
#         title = f"User Notification {uuid.uuid4()}"
#         payload = self.nest_payload_in_entity(
#             entity={"title": title, "content": f"Content for {title}", "team_id": None}
#         )
#         response = server.post(
#             "/v1/notification", json=payload, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#             response, 201, "POST with null team", "/v1/notification", payload
#         )
#         notification = self._assert_entity_in_response(response)
#         assert notification["team_id"] is None, (
#             f"[{self.entity_name}] Team ID should be null\n"
#             f"Got: {notification['team_id']}\n"
#             f"Notification: {notification}"
#         )
#         return notification
