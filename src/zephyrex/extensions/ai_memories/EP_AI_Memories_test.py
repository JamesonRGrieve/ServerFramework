# import uuid
# import pytest
# from endpoints.AbstractEPTest import ParentEntity
# @pytest.mark.memories
#     # These aren't standard CRUD endpoints, so we'll need custom test methods
#     # for most operations instead of using the AbstractEndpointTest framework directly
#     base_endpoint = "memory"
#     entity_name = "memory"
#     string_field_to_update = "text"
#     supports_search = False
#     # Parent entities for memories
#     parent_entities = [
#         ParentEntity(
#             name="agent",
#             key="agent_id",
#             system=False,
#             is_path=True,
#         ),
#     ]
#     def create_payload(self, name=None, parent_ids=None, team_id=None):
#         """Create a payload for memory creation."""
#         parent_ids = parent_ids or {}
#         text = f"Test memory content {uuid.uuid4()}"
#         if name:
#             description = name
#             description = f"Test memory {uuid.uuid4()}"
#         payload = {
#             "text": text,
#             "external_source": "user input",
#         }
#         # Add agent_id if provided
#         if "agent_id" in parent_ids:
#             payload["agent_id"] = parent_ids["agent_id"]
#     def create_parent_entities(self, server, jwt_a, team_a):
#         """Create parent entities for memory testing."""
#         # Create an agent
#         agent_name = f"Test Agent {uuid.uuid4()}"
#         agent_payload = {
#                 "name": agent_name,
#                 "description": "Test agent for memories",
#                 "provider": "OpenAI",
#             }
#         agent_response = server.post(
#             "/v1/agent", json=agent_payload, headers=self._auth_header(jwt_a)
#         )
#         # If agent creation fails, raise an error
#         if agent_response.status_code != 201:
#             pytest.fail(
#                 f"Failed to create agent for memory tests: {agent_response.text}"
#             )
#         agent = agent_response.json()["agent"]
#         return {"agent": agent}
#     def test_POST_201(self, server, jwt_a, team_a):
#         """Test creating a memory for an agent."""
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         collection_id = "0"  # Default collection
#         memory_text = f"Test memory content {uuid.uuid4()}"
#         memory_description = f"Test memory description {uuid.uuid4()}"
#         payload = self.nest_payload_in_entity(
#             entity={
#                 "text": memory_text,
#                 "external_source": "user input",
#         )
#         response = server.post(endpoint, json=payload, headers=self._auth_header(jwt_a))
#         self._assert_response_status(
#             response, 201, "POST create memory", endpoint, payload
#         )
#         entity = self._assert_entity_in_response(response)
#         assert entity["text"] == memory_text, (
#             f"Expected: {memory_text}\n"
#             f"Got: {entity['text']}"
#         )
#             f"[{self.entity_name}] External source mismatch\n"
#             f"Expected: user input\n"
#             f"Got: {entity['external_source_name']}"
#         )
#         return entity
#     def test_POST_201_with_external_source(self, server, jwt_a, team_a):
#         """Test creating a memory with a custom external source."""
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         # Create memory with custom source
#         memory_text = f"Test memory content {uuid.uuid4()}"
#         memory_description = f"Test memory description {uuid.uuid4()}"
#         external_source = f"external_source_{uuid.uuid4()}"
#             entity={
#                 "text": memory_text,
#                 "description": memory_description,
#                 "external_source": external_source,
#             }
#         endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}"
#         response = server.post(endpoint, json=payload, headers=self._auth_header(jwt_a))
#         self._assert_response_status(
#             response, 201, "POST create memory with external source", endpoint, payload
#         )
#         assert entity["external_source_name"] == external_source, (
#             f"Expected: {external_source}\n"
#             f"Got: {entity['external_source_name']}"
#         )
#         return entity
#     def test_GET_200_list(self, server, jwt_a, team_a):
#         """Test retrieving memories for an agent."""
#         memory = self.test_POST_201(server, jwt_a, team_a)
#         # Get parent entities
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         # Get memories
#         endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}"
#         response = server.get(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 200, "GET list memories", endpoint)
#         json_response = response.json()
#         assert "memories" in json_response, (
#             f"[{self.entity_name}] Memories not found in response\n"
#         )
#         memories = json_response["memories"]
#             f"[{self.entity_name}] Memories should be a list\n" f"Memories: {memories}"
#         )
#         # Check if our memory is in the list
#         found = False
#             if mem["id"] == memory["id"]:
#                 found = True
#                 break
#         assert found, (
#             f"[{self.entity_name}] Created memory not found in list\n"
#             f"Memories: {memories}"
#         return memories
#     def test_POST_200_query(self, server, jwt_a, team_a):
#         """Test querying memories based on input text."""
#         # First create a memory with specific text
#         # Create parent entities
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         collection_id = "0"  # Default collection
#         payload = self.nest_payload_in_entity(
#             entity={
#                 "text": memory_text,
#                 "external_source": "user input",
#             }
#         create_endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}"
#         create_response = server.post(
#             create_endpoint, json=payload, headers=self._auth_header(jwt_a)
#         )
#             create_response,
#             201,
#             "POST create memory for query",
#             create_endpoint,
#         )
#         # Query for the memory
#         query_text = "queryable content"
#         query_payload = {"query_text": query_text}
#         query_endpoint = (
#             f"/v1/memory/agent/{agent['name']}/collection/{collection_id}/query"
#         query_response = server.post(
#             query_endpoint, json=query_payload, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#             query_response, 200, "POST query memories", query_endpoint, query_payload
#         json_response = query_response.json()
#             f"[{self.entity_name}] Memories not found in query response\n"
#             f"Response: {json_response}"
#         )
#         memories = json_response["memories"]
#             f"[{self.entity_name}] Query result should be a list\n"
#             f"Result: {memories}"
#         )
#         # Check if our memory is in the results
#         for mem in memories:
#             if memory_text in mem["text"]:
#                 found = True
#                 break
#         assert found, (
#             f"[{self.entity_name}] Created memory not found in query results\n"
#             f"Memory text: {memory_text}\n"
#             f"Query results: {memories}"
#         return memories
#     def test_POST_202_import(self, server, jwt_a, team_a):
#         """Test importing memories from various sources."""
#         # Create parent entities
#         agent = parent_entities["agent"]
#         # Create import payload
#         import_payload = {
#             "items": [
#                 {
#                     "type": "text",
#                     "content": {
#                         "text": f"This is a test import of text content {uuid.uuid4()}",
#                         "external_source": "import test",
#                     },
#             ]
#         }
#         endpoint = f"/v1/memory/agent/{agent['name']}/import"
#         response = server.post(
#             endpoint, json=import_payload, headers=self._auth_header(jwt_a)
#         )
#             response, 202, "POST import memories", endpoint, import_payload
#         )
#         json_response = response.json()
#             f"[{self.entity_name}] Message not found in import response\n"
#             f"Response: {json_response}"
#         )
#         return json_response
#     def test_GET_200_export(self, server, jwt_a, team_a):
#         # First create a memory
#         memory = self.test_POST_201(server, jwt_a, team_a)
#         # Get parent entities
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         endpoint = f"/v1/memory/agent/{agent['name']}/export"
#         response = server.get(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 200, "GET export memories", endpoint)
#         json_response = response.json()
#         assert "memories" in json_response, (
#             f"[{self.entity_name}] Memories not found in export response\n"
#         )
#         memories = json_response["memories"]
#         assert isinstance(memories, list), (
#             f"[{self.entity_name}] Export result should be a list\n"
#             f"Result: {memories}"
#         # Check if at least one collection is in the export
#             f"[{self.entity_name}] No collections found in export\n"
#             f"Export: {memories}"
#         )
#         # Check that a collection has memories
#         for collection in memories:
#                 f"[{self.entity_name}] Collection ID not found in export collection\n"
#                 f"Collection: {collection}"
#             )
#             assert "memories" in collection, (
#                 f"[{self.entity_name}] Memories not found in export collection\n"
#                 f"Collection: {collection}"
#             )
#         return memories
#     def test_GET_200_sources(self, server, jwt_a, team_a):
#         """Test listing external sources."""
#         # First create memories with different sources
#         memory1 = self.test_POST_201(server, jwt_a, team_a)  # user input
#         memory2 = self.test_POST_201_with_external_source(
#         )  # custom source
#         # Get parent entities
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         # Get sources
#         endpoint = (
#             f"/v1/memory/agent/{agent['name']}/collection/{collection_id}/sources"
#         response = server.get(endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(response, 200, "GET sources", endpoint)
#         json_response = response.json()
#         assert "external_sources" in json_response, (
#             f"[{self.entity_name}] External sources not found in response\n"
#         )
#         assert isinstance(sources, list), (
#             f"[{self.entity_name}] Sources should be a list\n" f"Sources: {sources}"
#         )
#         # Check if our sources are in the list
#             f"[{self.entity_name}] 'user input' source not found in sources\n"
#             f"Sources: {sources}"
#         )
#             f"[{self.entity_name}] Custom source not found in sources\n"
#             f"Custom source: {memory2['external_source_name']}\n"
#             f"Sources: {sources}"
#         return sources
#         """Test deleting memories by source."""
#         # First create a memory with a specific source
#         external_source = f"delete_test_source_{uuid.uuid4()}"
#         # Create parent entities
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         collection_id = "0"  # Default collection
#         # Create memory
#         payload = self.nest_payload_in_entity(
#             entity={
#                 "text": f"Test memory for deletion {uuid.uuid4()}",
#                 "external_source": external_source,
#             }
#         )
#         create_endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}"
#         create_response = server.post(
#         )
#         self._assert_response_status(
#             create_response,
#             201,
#             "POST create memory for deletion",
#             create_endpoint,
#         )
#         # Delete memories by source
#         delete_endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}/source/{external_source}"
#         delete_response = server.delete(
#         )
#             delete_response, 200, "DELETE memories by source", delete_endpoint
#         )
#         json_response = delete_response.json()
#         assert "message" in json_response, (
#             f"[{self.entity_name}] Message not found in delete response\n"
#             f"Response: {json_response}"
#         )
#         sources_endpoint = (
#             f"/v1/memory/agent/{agent['name']}/collection/{collection_id}/sources"
#         )
#         sources_response = server.get(
#         )
#         self._assert_response_status(
#             sources_response, 200, "GET sources after deletion", sources_endpoint
#         )
#         sources = sources_response.json()["external_sources"]
#             f"[{self.entity_name}] Deleted source still in sources list\n"
#             f"Sources: {sources}"
#         )
#         return json_response
#     def test_DELETE_200_collection(self, server, jwt_a, team_a):
#         """Test deleting an entire collection."""
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         collection_id = f"test_collection_{uuid.uuid4()}"
#         # Create memory
#             entity={
#                 "text": f"Test memory for collection deletion {uuid.uuid4()}",
#                 "description": "Memory for collection deletion test",
#                 "external_source": "test",
#             }
#         create_endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}"
#         create_response = server.post(
#             create_endpoint, json=payload, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#             201,
#             create_endpoint,
#             payload,
#         )
#         # Delete the collection
#         delete_response = server.delete(
#             delete_endpoint, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#         )
#         json_response = delete_response.json()
#         assert "message" in json_response, (
#             f"[{self.entity_name}] Message not found in delete response\n"
#             f"Response: {json_response}"
#         )
#         # Verify the collection is empty by querying it
#         query_payload = {"query_text": "test"}
#             f"/v1/memory/agent/{agent['name']}/collection/{collection_id}/query"
#         )
#         query_response = server.post(
#             query_endpoint, json=query_payload, headers=self._auth_header(jwt_a)
#         self._assert_response_status(
#             query_response,
#             200,
#             "POST query deleted collection",
#             query_endpoint,
#             query_payload,
#         )
#         assert len(memories) == 0, (
#             f"[{self.entity_name}] Collection not empty after deletion\n"
#             f"Memories: {memories}"
#         )
#         return json_response
#         """Test deleting a specific memory."""
#         # First create a memory
#         memory = self.test_POST_201(server, jwt_a, team_a)
#         parent_entities = self.create_parent_entities(server, jwt_a, team_a)
#         agent = parent_entities["agent"]
#         collection_id = "0"  # Default collection
#         # Delete the memory
#         delete_endpoint = f"/v1/memory/agent/{agent['name']}/collection/{collection_id}/{memory['id']}"
#             delete_endpoint, headers=self._auth_header(jwt_a)
#         )
#         self._assert_response_status(
#             delete_response, 200, "DELETE memory", delete_endpoint
#         )
#         json_response = delete_response.json()
#         assert "message" in json_response, (
#             f"Response: {json_response}"
#         )
#         # Verify the memory is deleted by checking in the memories list
#         list_response = server.get(list_endpoint, headers=self._auth_header(jwt_a))
#         self._assert_response_status(
#             list_response, 200, "GET memories after deletion", list_endpoint
#         )
#         memories = list_response.json()["memories"]
#         memory_ids = [mem["id"] for mem in memories]
#             f"[{self.entity_name}] Deleted memory still in memories list\n"
#             f"Memory IDs: {memory_ids}"
#         )
#         return json_response
