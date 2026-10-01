# import uuid
# from datetime import datetime, timezone, timedelta
# import pytest
# from endpoints.AbstractEPTest import AbstractEndpointTest
# @pytest.mark.tasks
#     base_endpoint = "task"
#     entity_name = "task"
#     required_fields = ["id", "title"]
#     string_field_to_update = "description"
#     searchable_fields = ["title", "description", "label"]
#     # No parent entities for tasks
#     parent_entities = []
#     # Not a system entity
#     system_entity = False
#     def create_payload(self, name=None, parent_ids=None, team_id=None):
#         if not name:
#             name = self.generate_name()
#         due_date = (datetime.now() + timedelta(days=7)).isoformat()
#         payload = {
#             "description": f"Description for {name}",
#             "due_date": due_date,
#             "completed": False,
#             "labels": ["test", "task"],
#         if team_id:
#             payload["team_id"] = team_id
#     def test_POST_201_with_agent(self, server, jwt_a, team_a):
#         """Test creating a task with an associated agent."""
#         # Create an agent first
#         agent_name = f"Test Agent {uuid.uuid4()}"
#         agent_payload = {
#             "agent": {
#                 "name": agent_name,
#                 "provider": "OpenAI",
#                 "model": "gpt-4",
#         }
#             "/v1/agent", json=agent_payload, headers=self._auth_header(jwt_a)
#         )
#         # If agent creation fails, skip this test
#         if agent_response.status_code != 201:
#             pytest.skip("Failed to create agent for task")
#         agent = agent_response.json()["agent"]
#         # Create a task with agent
#         task_title = f"Agent Task {uuid.uuid4()}"
#         due_date = (datetime.now() + timedelta(days=7)).isoformat()
#         task_payload = self.nest_payload_in_entity(
#             entity={
#                 "title": task_title,
#                 "due_date": due_date,
#                 "completed": False,
#                 "agent_id": agent["id"],
#             }
#         )
#         response = server.post(
#         )
#             response, 201, "POST with agent", "/v1/task", task_payload
#         )
#         entity = self._assert_entity_in_response(response)
#             f"[{self.entity_name}] Agent ID mismatch\n"
#             f"Expected: {agent['id']}\n"
#             f"Got: {entity['agent_id']}\n"
#             f"Entity: {entity}"
#         )
#         return entity
#     def test_POST_201_with_schedule(self, server, jwt_a, team_a):
#         """Test creating a task with a schedule."""
#         # Create a task with schedule
#         task_title = f"Scheduled Task {uuid.uuid4()}"
#         start_date = datetime.now().isoformat()
#         task_payload = self.nest_payload_in_entity(
#             entity={
#                 "description": "A task with a schedule",
#                 "due_date": due_date,
#                 "start_date": start_date,
#                 "schedule": {"frequency": "daily", "interval": 1, "time": "09:00:00"},
#                 "labels": ["scheduled", "test"],
#             }
#         )
#         response = server.post(
#             "/v1/task", json=task_payload, headers=self._auth_header(jwt_a)
#         )
#             response, 201, "POST with schedule", "/v1/task", task_payload
#         entity = self._assert_entity_in_response(response)
#         assert "schedule" in entity, (
#             f"[{self.entity_name}] Schedule not found in entity\n" f"Entity: {entity}"
#         )
#         assert entity["schedule"]["frequency"] == "daily", (
#             f"[{self.entity_name}] Schedule frequency mismatch\n"
#             f"Got: {entity['schedule']['frequency']}\n"
#             f"Schedule: {entity['schedule']}"
#         )
#         return entity
#     def test_POST_201_complete(self, server, jwt_a, team_a):
#         """Test marking a task as completed."""
#         # First create a task
#         task = self.test_POST_201(server, jwt_a, team_a)
#         # Mark it as completed
#         endpoint = f"/v1/task/{task['id']}/complete"
#         response = server.post(endpoint, headers=self._auth_header(jwt_a))
#         entity = self._assert_entity_in_response(response)
#         assert entity["completed"] == True, (
#             f"[{self.entity_name}] Task not marked as completed\n" f"Entity: {entity}"
#         assert "completed_at" in entity and entity["completed_at"] is not None, (
#             f"[{self.entity_name}] Task missing completed_at timestamp\n"
#             f"Entity: {entity}"
#         )
#     def test_POST_202_execute(self, server, jwt_a, team_a):
#         """Test executing a task."""
#         # First create a task with an agent
#         # Execute the task
#         endpoint = f"/v1/task/{task['id']}/execute"
#         response = server.post(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 202, "POST execute task", endpoint)
#         json_response = response.json()
#         assert "message" in json_response, (
#             f"Response: {json_response}"
#         assert "task_id" in json_response, (
#             f"[{self.entity_name}] Task ID not found in execute response\n"
#             f"Response: {json_response}"
#         )
#             f"[{self.entity_name}] Task ID mismatch in execute response\n"
#             f"Expected: {task['id']}\n"
#             f"Got: {json_response['task_id']}"
#         assert "status" in json_response, (
#             f"[{self.entity_name}] Status not found in execute response\n"
#         )
#         assert json_response["status"] == "processing", (
#             f"[{self.entity_name}] Status mismatch in execute response\n"
#             f"Got: {json_response['status']}"
#         )
#         return json_response
#     def test_POST_202_plan(self, server, jwt_a, team_a):
#         # Plan a task
#             "user_input": "Create a comprehensive market analysis for our new product launch",
#             "websearch": True,
#             "websearch_depth": 3,
#             "conversation_name": "Market Analysis Planning",
#             "log_output": True,
#             "enable_new_command": True,
#         }
#         response = server.post(
#             endpoint, json=plan_payload, headers=self._auth_header(jwt_a)
#         self._assert_response_status(
#             response, 202, "POST plan task", endpoint, plan_payload
#         )
#         json_response = response.json()
#             f"[{self.entity_name}] Chain name not found in plan response\n"
#             f"Response: {json_response}"
#         )
#         assert "message" in json_response, (
#             f"Response: {json_response}"
#         )
#         assert "tasks" in json_response, (
#             f"[{self.entity_name}] Tasks not found in plan response\n"
#             f"Response: {json_response}"
#         return json_response
#     def test_GET_200_filter(self, server, jwt_a, team_a):
#         """Test filtering tasks by various criteria."""
#         # First create a task
#         # Mark it as completed
#         completed_task = self.test_POST_201_complete(server, jwt_a, team_a)
#         # Create a scheduled task
#         scheduled_task = self.test_POST_201_with_schedule(server, jwt_a, team_a)
#         # Test filtering by completion status
#         response = server.get(endpoint, headers=self._auth_header(jwt_a))
#         json_response = response.json()
#         assert "tasks" in json_response, (
#             f"[{self.entity_name}] Tasks not found in filter response\n"
#             f"Response: {json_response}"
#         )
#         completed_tasks = json_response["tasks"]
#         assert isinstance(completed_tasks, list), (
#             f"[{self.entity_name}] Filtered tasks should be a list\n"
#             f"Tasks: {completed_tasks}"
#         )
#         # All tasks in the result should be completed
#         for task in completed_tasks:
#                 f"[{self.entity_name}] Task in completed filter is not completed\n"
#                 f"Task: {task}"
#             )
#         # Test filtering by scheduled status
#         response = server.get(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 200, "GET filter by scheduled", endpoint)
#         json_response = response.json()
#         assert "tasks" in json_response, (
#             f"Response: {json_response}"
#         )
#         scheduled_tasks = json_response["tasks"]
#         # All tasks in the result should have a schedule
#             assert "schedule" in task, (
#                 f"[{self.entity_name}] Task in scheduled filter has no schedule\n"
#                 f"Task: {task}"
#             )
#     def test_GET_200_includes(self, server, jwt_a, team_a):
#         """Test retrieving a task with included related entities."""
#         # First create a task with an agent
#         task = self.test_POST_201_with_agent(server, jwt_a, team_a)
#         endpoint = f"/v1/task/{task['id']}?include=agent"
#         self._assert_response_status(response, 200, "GET with includes", endpoint)
#         json_response = response.json()
#         assert self.entity_name in json_response, (
#             f"[{self.entity_name}] Entity not found in response\n"
#         )
#         entity = json_response[self.entity_name]
#             f"[{self.entity_name}] Agent not included in response\n" f"Entity: {entity}"
#         )
#             f"[{self.entity_name}] Included agent ID mismatch\n"
#             f"Expected: {task['agent_id']}\n"
#             f"Got: {entity['agent']['id']}\n"
#         )
#         return entity
#         """Test batch creation of tasks."""
#         # Create multiple tasks with batch API
#         batch_size = 3
#         due_date = (datetime.now() + timedelta(days=7)).isoformat()
#         for i in range(batch_size):
#             title = f"Batch Task {i+1} {uuid.uuid4()}"
#             batch.append(
#                 {
#                     "title": title,
#                     "due_date": due_date,
#                     "completed": False,
#                     "labels": ["batch", f"task{i+1}"],
#                 }
#             )
#         payload = {f"{pluralize(self.entity_name)}": batch}
#             f"/v1/{self.base_endpoint}", json=payload, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#         )
#         json_response = response.json()
#             f"[{self.entity_name}] Entities not found in batch creation response\n"
#             f"Response: {json_response}"
#         )
#         entities = json_response[f"{pluralize(self.entity_name)}"]
#             f"[{self.entity_name}] Batch response should be a list\n"
#         )
#         assert len(entities) == batch_size, (
#             f"[{self.entity_name}] Expected {batch_size} entities, got {len(entities)}\n"
#             f"Entities: {entities}"
#         )
#         # Verify each batch item was created properly
#             assert entity["title"] == batch[i]["title"], (
#                 f"Expected: {batch[i]['title']}\n"
#                 f"Got: {entity['title']}"
#             )
#             assert entity["description"] == batch[i]["description"], (
#                 f"Expected: {batch[i]['description']}\n"
#                 f"Got: {entity['description']}"
#             )
