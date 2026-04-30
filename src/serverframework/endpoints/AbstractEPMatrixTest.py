# @pytest.mark.xfail
# @pytest.mark.parametrize(
#     "method,status_code,variant,endpoint_type",
#     [
#         # Create entity test matrix
#         (
#             HttpMethod.POST,
#             StatusCode.CREATED,
#             EntityVariant.VALID,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.CREATED,
#             EntityVariant.MINIMAL,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.CREATED,
#             EntityVariant.NULL_PARENTS,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.UNPROCESSABLE,
#             EntityVariant.INVALID,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.UNPROCESSABLE,
#             EntityVariant.NONEXISTENT_PARENTS,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.UNAUTHORIZED,
#             EntityVariant.VALID,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.FORBIDDEN,
#             EntityVariant.SYSTEM,
#             EndpointType.SINGLE,
#         ),
#         # Batch create test matrix
#         (
#             HttpMethod.POST,
#             StatusCode.CREATED,
#             EntityVariant.VALID,
#             EndpointType.BATCH,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.CREATED,
#             EntityVariant.MINIMAL,
#             EndpointType.BATCH,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.CREATED,
#             EntityVariant.NULL_PARENTS,
#             EndpointType.BATCH,
#         ),
#         (
#             HttpMethod.POST,
#             StatusCode.UNPROCESSABLE,
#             EntityVariant.INVALID,
#             EndpointType.BATCH,
#         ),
#         # Read entity test matrix
#         (HttpMethod.GET, StatusCode.OK, EntityVariant.VALID, EndpointType.SINGLE),
#         (
#             HttpMethod.GET,
#             StatusCode.NOT_FOUND,
#             EntityVariant.OTHER_USER,
#             EndpointType.SINGLE,
#         ),
#         (HttpMethod.GET, StatusCode.NOT_FOUND, None, EndpointType.SINGLE),
#         (
#             HttpMethod.GET,
#             StatusCode.UNAUTHORIZED,
#             EntityVariant.VALID,
#             EndpointType.SINGLE,
#         ),
#         # List entities test matrix
#         (HttpMethod.GET, StatusCode.OK, EntityVariant.VALID, EndpointType.LIST),
#         (
#             HttpMethod.GET,
#             StatusCode.UNAUTHORIZED,
#             EntityVariant.VALID,
#             EndpointType.LIST,
#         ),
#         # Update entity test matrix
#         (HttpMethod.PUT, StatusCode.OK, EntityVariant.VALID, EndpointType.SINGLE),
#         (
#             HttpMethod.PUT,
#             StatusCode.UNPROCESSABLE,
#             EntityVariant.INVALID,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.PUT,
#             StatusCode.NOT_FOUND,
#             EntityVariant.OTHER_USER,
#             EndpointType.SINGLE,
#         ),
#         (HttpMethod.PUT, StatusCode.NOT_FOUND, None, EndpointType.SINGLE),
#         (
#             HttpMethod.PUT,
#             StatusCode.UNAUTHORIZED,
#             EntityVariant.VALID,
#             EndpointType.SINGLE,
#         ),
#         # Batch update test matrix
#         (HttpMethod.PUT, StatusCode.OK, EntityVariant.VALID, EndpointType.BATCH),
#         # Delete entity test matrix
#         (
#             HttpMethod.DELETE,
#             StatusCode.NO_CONTENT,
#             EntityVariant.VALID,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.DELETE,
#             StatusCode.NOT_FOUND,
#             EntityVariant.OTHER_USER,
#             EndpointType.SINGLE,
#         ),
#         (HttpMethod.DELETE, StatusCode.NOT_FOUND, None, EndpointType.SINGLE),
#         (
#             HttpMethod.DELETE,
#             StatusCode.UNAUTHORIZED,
#             EntityVariant.VALID,
#             EndpointType.SINGLE,
#         ),
#         (
#             HttpMethod.DELETE,
#             StatusCode.FORBIDDEN,
#             EntityVariant.SYSTEM,
#             EndpointType.SINGLE,
#         ),
#         # Batch delete test matrix
#         (
#             HttpMethod.DELETE,
#             StatusCode.NO_CONTENT,
#             EntityVariant.VALID,
#             EndpointType.BATCH,
#         ),
#     ],
# )
# def test_endpoint_matrix(
#     self,
#     server: Any,
#     admin_a: Any,
#     admin_b: Any,
#     user_b: Any,
#     team_a: Any,
#     team_b: Any,
#     method: HttpMethod,
#     status_code: StatusCode,
#     variant: Optional[EntityVariant],
#     endpoint_type: EndpointType,
# ):
#     """
#     Matrix test for API endpoints with different methods, status codes, and variants.

#     Args:
#         server: Test client
#         admin_a, admin_b, user_b: Test users
#         team_a, team_b: Test teams
#         method: HTTP method to test
#         status_code: Expected HTTP status code
#         variant: Entity variant to use in the test
#         endpoint_type: Type of endpoint to test (single, list, batch)
#     """
#     # Skip tests based on entity configuration
#     if variant == EntityVariant.SYSTEM and not self.system_entity:
#         pytest.skip("Not a system entity")

#     if variant == EntityVariant.NULL_PARENTS and (
#         not self.has_parent_entities()
#         or not any(p.nullable for p in self.parent_entities)
#     ):
#         pytest.skip("No nullable parent entities for this entity")

#     if variant == EntityVariant.NONEXISTENT_PARENTS and (
#         not self.has_parent_entities()
#         or not any(not p.nullable for p in self.parent_entities)
#     ):
#         pytest.skip("No non-nullable parent entities for this entity")

#     if (
#         status_code in [StatusCode.NOT_FOUND, StatusCode.FORBIDDEN]
#         and variant == EntityVariant.OTHER_USER
#         and (not hasattr(self, "user_scoped") or not self.user_scoped)
#     ):
#         pytest.skip("Entity not user-scoped")

#     # Process based on method and expected status code
#     key = (
#         f"{method.value}_{status_code}_{variant}_{endpoint_type}"
#         if variant
#         else f"{method.value}_{status_code}_{endpoint_type}"
#     )

#     # Step 1: Setup test - create parent entities, prepare payloads, etc.
#     parent_ids = {}
#     path_parent_ids = {}

#     # Create minimal values for headers to avoid possible None errors
#     jwt_token = admin_a.jwt
#     api_key = None
#     user_id = admin_a.id
#     team_id = team_a.id

#     # -- Handle auth variants --
#     if status_code == StatusCode.UNAUTHORIZED:
#         jwt_token = None

#     if variant == EntityVariant.SYSTEM and status_code == StatusCode.FORBIDDEN:
#         # System entity without API key
#         pass
#     elif variant == EntityVariant.SYSTEM and method in [
#         HttpMethod.POST,
#         HttpMethod.PUT,
#         HttpMethod.DELETE,
#     ]:
#         # System entity with API key
#         api_key = env("API_KEY")

#     if variant == EntityVariant.OTHER_USER:
#         # Setup for other user tests
#         # First create entity as admin_a
#         entity = self._create(
#             server, admin_a.jwt, admin_a.id, team_a.id, key=f"{key}_prep"
#         )
#         # Then access it as user_b
#         jwt_token = user_b.jwt
#         user_id = user_b.id

#     # -- Handle parent entity variants --
#     if variant == EntityVariant.NULL_PARENTS:
#         parent_ids, path_parent_ids, _ = self._handle_nullable_parents(
#             server, jwt_token, team_id
#         )
#     elif variant == EntityVariant.NONEXISTENT_PARENTS:
#         for parent in self.parent_entities:
#             if not parent.nullable:
#                 fake_id = str(uuid.uuid4())
#                 parent_ids[parent.foreign_key] = fake_id
#                 if parent.path_level in [1, 2] or (
#                     parent.is_path and parent.path_level is None
#                 ):
#                     path_parent_ids[f"{parent.name}_id"] = fake_id
#     else:
#         # For all other cases, create real parent entities
#         if status_code not in [StatusCode.UNAUTHORIZED]:
#             # Skip for unauthorized tests since we can't create parent entities
#             if not (
#                 variant == EntityVariant.OTHER_USER and method != HttpMethod.POST
#             ):
#                 # Skip for OTHER_USER tests except for POST, since we already created entity
#                 parent_entities_dict, parent_ids, path_parent_ids = (
#                     self._create_parent_entities(server, jwt_token, team_id)
#                 )

#     # -- Create entities for non-POST tests --
#     if (
#         method != HttpMethod.POST
#         and status_code not in [StatusCode.UNAUTHORIZED, StatusCode.NOT_FOUND]
#         and variant != EntityVariant.OTHER_USER
#     ):
#         # Create entity to operate on
#         if endpoint_type == EndpointType.BATCH:
#             self._batch_create(
#                 server, admin_a.jwt, admin_a.id, team_a.id, count=3, save_key=key
#             )
#         else:
#             self._create(server, admin_a.jwt, admin_a.id, team_a.id, key=key)

#     # Step 2: Execute request based on HTTP method and endpoint type
#     invalid_data = variant == EntityVariant.INVALID
#     minimal = variant == EntityVariant.MINIMAL
#     use_nullable_parents = variant == EntityVariant.NULL_PARENTS
#     nonexistent_entity_id = str(uuid.uuid4())

#     # Build headers
#     headers = self._get_appropriate_headers(jwt_token, api_key)

#     # Matrix of operations
#     if method == HttpMethod.POST:
#         # Handle POST requests
#         if endpoint_type == EndpointType.BATCH:
#             # Batch create
#             count = 3
#             entities = []

#             for i in range(count):
#                 entity = self.create_payload(
#                     name=f"Test Batch {i} {self.faker.word()}",
#                     parent_ids=parent_ids,
#                     team_id=team_id,
#                     minimal=minimal,
#                     invalid_data=invalid_data,
#                 )
#                 entities.append(entity)

#             payload = {self.resource_name_plural: entities}
#             url = self.get_create_endpoint(path_parent_ids)

#             # Make request
#             response = server.post(url, json=payload, headers=headers)

#         else:
#             # Single create
#             payload = {
#                 self.entity_name: self.create_payload(
#                     name=f"Test {self.faker.word()}",
#                     parent_ids=parent_ids,
#                     team_id=team_id,
#                     minimal=minimal,
#                     invalid_data=invalid_data,
#                 )
#             }
#             url = self.get_create_endpoint(path_parent_ids)

#             # Make request
#             response = server.post(url, json=payload, headers=headers)

#     elif method == HttpMethod.GET:
#         # Handle GET requests
#         if endpoint_type == EndpointType.LIST:
#             # List entities
#             url = self.get_list_endpoint(path_parent_ids)
#             response = server.get(url, headers=headers)

#         elif endpoint_type == EndpointType.SINGLE:
#             # Get single entity
#             if (
#                 status_code == StatusCode.NOT_FOUND
#                 and variant != EntityVariant.OTHER_USER
#             ):
#                 # Use nonexistent ID for 404 test
#                 url = self.get_detail_endpoint(
#                     nonexistent_entity_id, path_parent_ids
#                 )
#             elif variant == EntityVariant.OTHER_USER:
#                 # Use existing entity created by another user
#                 url = self.get_detail_endpoint(
#                     self.tracked_entities[f"{key}_prep"]["id"], path_parent_ids
#                 )
#             else:
#                 # Use existing entity
#                 url = self.get_detail_endpoint(
#                     self.tracked_entities[key]["id"], path_parent_ids
#                 )

#             response = server.get(url, headers=headers)

#     elif method == HttpMethod.PUT:
#         # Handle PUT requests
#         if endpoint_type == EndpointType.BATCH:
#             # Batch update
#             target_ids = [e["id"] for e in self.tracked_entities[key]]
#             update_data = self._batch_update_data()
#             payload = {"target_ids": target_ids, self.entity_name: update_data}
#             url = self.get_list_endpoint(path_parent_ids)

#             response = server.put(url, json=payload, headers=headers)

#         elif endpoint_type == EndpointType.SINGLE:
#             # Single update
#             update_data = {}
#             if self.string_field_to_update:
#                 if invalid_data:
#                     update_data[self.string_field_to_update] = 12345  # Invalid type
#                 else:
#                     update_data[self.string_field_to_update] = (
#                         f"Updated {self.faker.word()}"
#                     )

#             if (
#                 status_code == StatusCode.NOT_FOUND
#                 and variant != EntityVariant.OTHER_USER
#             ):
#                 # Use nonexistent ID for 404 test
#                 url = self.get_update_endpoint(
#                     nonexistent_entity_id, path_parent_ids
#                 )
#             elif variant == EntityVariant.OTHER_USER:
#                 # Use existing entity created by another user
#                 url = self.get_update_endpoint(
#                     self.tracked_entities[f"{key}_prep"]["id"], path_parent_ids
#                 )
#             else:
#                 # Use existing entity
#                 url = self.get_update_endpoint(
#                     self.tracked_entities[key]["id"], path_parent_ids
#                 )

#             payload = {"entity": update_data}
#             response = server.put(url, json=payload, headers=headers)

#     elif method == HttpMethod.DELETE:
#         # Handle DELETE requests
#         if endpoint_type == EndpointType.BATCH:
#             # Batch delete
#             target_ids = [e["id"] for e in self.tracked_entities[key]]
#             target_ids_str = ",".join(target_ids)
#             url = f"{self.get_list_endpoint(path_parent_ids)}?target_ids={target_ids_str}"

#             response = server.delete(url, headers=headers)

#         elif endpoint_type == EndpointType.SINGLE:
#             # Single delete
#             if (
#                 status_code == StatusCode.NOT_FOUND
#                 and variant != EntityVariant.OTHER_USER
#             ):
#                 # Use nonexistent ID for 404 test
#                 url = self.get_delete_endpoint(
#                     nonexistent_entity_id, path_parent_ids
#                 )
#             elif variant == EntityVariant.OTHER_USER:
#                 # Use existing entity created by another user
#                 url = self.get_delete_endpoint(
#                     self.tracked_entities[f"{key}_prep"]["id"], path_parent_ids
#                 )
#             else:
#                 # Use existing entity
#                 url = self.get_delete_endpoint(
#                     self.tracked_entities[key]["id"], path_parent_ids
#                 )

#             response = server.delete(url, headers=headers)

#     # Step 3: Verify response
#     assert (
#         response.status_code == status_code
#     ), f"Expected status code {status_code}, got {response.status_code} for {method} {endpoint_type} with variant {variant}"

#     # Step 4: Additional assertions for successful operations
#     if status_code in [StatusCode.OK, StatusCode.CREATED]:
#         if endpoint_type == EndpointType.BATCH:
#             # Check batch response
#             try:
#                 data = response.json()
#                 if method == HttpMethod.POST:
#                     assert (
#                         self.resource_name_plural in data
#                     ), f"Response missing {self.resource_name_plural} key"
#                     entities = data[self.resource_name_plural]
#                     assert isinstance(entities, list), "Response is not a list"
#                     assert len(entities) > 0, "Response list is empty"
#                     for entity in entities:
#                         assert "id" in entity, "Entity missing ID field"
#                 elif method == HttpMethod.PUT:
#                     assert (
#                         self.resource_name_plural in data
#                     ), f"Response missing {self.resource_name_plural} key"
#                     entities = data[self.resource_name_plural]
#                     assert isinstance(entities, list), "Response is not a list"
#                     assert len(entities) > 0, "Response list is empty"
#                     if self.string_field_to_update:
#                         for entity in entities:
#                             assert entity[self.string_field_to_update].startswith(
#                                 "Batch Updated"
#                             ), f"Field {self.string_field_to_update} not updated correctly"
#             except Exception as e:
#                 assert False, f"Failed to process response: {str(e)}"
#         elif method == HttpMethod.GET and endpoint_type == EndpointType.LIST:
#             # Check list response
#             try:
#                 data = response.json()
#                 entities = None
#                 if self.resource_name_plural in data:
#                     entities = data[self.resource_name_plural]
#                 elif isinstance(data, list):
#                     entities = data
#                 elif "items" in data:
#                     entities = data["items"]

#                 assert entities is not None, "Failed to find entities in response"
#                 assert isinstance(entities, list), "Response is not a list"
#             except Exception as e:
#                 assert False, f"Failed to process response: {str(e)}"
#         elif endpoint_type == EndpointType.SINGLE:
#             # Check single entity response
#             if method != HttpMethod.DELETE:  # DELETE doesn't return entity
#                 try:
#                     data = response.json()
#                     entity = None
#                     if self.entity_name in data:
#                         entity = data[self.entity_name]
#                     elif "id" in data:
#                         entity = data

#                     assert entity is not None, "Failed to find entity in response"
#                     assert "id" in entity, "Entity missing ID field"

#                     if method == HttpMethod.PUT and self.string_field_to_update:
#                         assert entity[self.string_field_to_update].startswith(
#                             "Updated"
#                         ), f"Field {self.string_field_to_update} not updated correctly"
#                 except Exception as e:
#                     assert False, f"Failed to process response: {str(e)}"

#     # Step 5: Verify DELETE operations removed the entity
#     if method == HttpMethod.DELETE and status_code == StatusCode.NO_CONTENT:
#         if endpoint_type == EndpointType.SINGLE:
#             # Verify entity was deleted
#             check_response = server.get(
#                 self.get_detail_endpoint(
#                     self.tracked_entities[key]["id"], path_parent_ids
#                 ),
#                 headers=self._get_appropriate_headers(admin_a.jwt),
#             )
#             assert (
#                 check_response.status_code == 404
#             ), "Entity still exists after deletion"
#         elif endpoint_type == EndpointType.BATCH:
#             # Verify all entities were deleted
#             for entity in self.tracked_entities[key]:
#                 check_response = server.get(
#                     self.get_detail_endpoint(entity["id"], path_parent_ids),
#                     headers=self._get_appropriate_headers(admin_a.jwt),
#                 )
#                 assert (
#                     check_response.status_code == 404
#                 ), f"Entity {entity['id']} still exists after batch deletion"

# @pytest.mark.xfail
# @pytest.mark.parametrize(
#     "feature,expected",
#     [
#         # Test various features with expected status codes
#         ("pagination", StatusCode.OK),
#         ("fields", StatusCode.OK),
#         ("includes", StatusCode.OK),
#         ("search", StatusCode.OK),
#         ("filter", StatusCode.OK),
#     ],
# )
# def test_feature_matrix(
#     self, server: Any, admin_a: Any, team_a: Any, feature: str, expected: StatusCode
# ):
#     """
#     Matrix test for API features like pagination, fields, includes, etc.

#     Args:
#         server: Test client
#         admin_a: Admin user
#         team_a: Team
#         feature: Feature to test
#         expected: Expected status code
#     """
#     # Skip tests based on entity configuration
#     if feature == "includes" and not self.related_entities:
#         pytest.skip("No related entities defined for includes test")

#     if feature == "search" and not self.supports_search:
#         pytest.skip("Search not supported for this entity")

#     # Create test entities
#     if feature in ["pagination", "search", "filter"]:
#         # Create multiple entities for pagination/search/filter
#         entities = self._batch_create(
#             server,
#             admin_a.jwt,
#             admin_a.id,
#             team_a.id,
#             count=5,
#             save_key=f"{feature}_entities",
#         )
#     else:
#         # Create single entity for get with fields/includes
#         entity = self._create(
#             server, admin_a.jwt, admin_a.id, team_a.id, key=f"{feature}_entity"
#         )

#     # Parent entities
#     parent_entities_dict, parent_ids, path_parent_ids = (
#         self._create_parent_entities(server, admin_a.jwt, team_a.id)
#     )

#     # Headers
#     headers = self._get_appropriate_headers(admin_a.jwt)

#     # Test specific feature
#     if feature == "pagination":
#         # Test pagination
#         url = f"{self.get_list_endpoint(path_parent_ids)}?limit=2&offset=0"
#         response = server.get(url, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         entities = self._extract_entities_from_response(data)
#         assert len(entities) == 2, "First page should have 2 items"

#         # Get second page
#         url = f"{self.get_list_endpoint(path_parent_ids)}?limit=2&offset=2"
#         response = server.get(url, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         entities = self._extract_entities_from_response(data)
#         assert len(entities) == 2, "Second page should have 2 items"

#     elif feature == "fields":
#         # Test fields parameter
#         fields = "id,name"
#         url = f"{self.get_detail_endpoint(self.tracked_entities[f'{feature}_entity']['id'], path_parent_ids)}?fields={fields}"
#         response = server.get(url, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         entity = self._extract_entity_from_response(data)

#         # Verify only requested fields are present
#         fields_list = fields.split(",")
#         for field in fields_list:
#             assert field in entity, f"Field {field} missing from response"

#         # Check other fields are not present
#         other_fields = [
#             f
#             for f in self.create_fields.keys()
#             if f not in fields_list and f != "id"
#         ]
#         if other_fields:
#             for field in other_fields:
#                 if field in entity:
#                     assert False, f"Field {field} should not be in response"

#     elif feature == "includes":
#         # Test includes parameter
#         includes = ",".join(self.related_entities)
#         url = f"{self.get_detail_endpoint(self.tracked_entities[f'{feature}_entity']['id'], path_parent_ids)}?includes={includes}"
#         response = server.get(url, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         entity = self._extract_entity_from_response(data)

#         # Verify included entities are present
#         for related in self.related_entities:
#             assert related in entity, f"Related entity {related} should be included"

#     elif feature == "search":
#         # Create entity with specific search term
#         search_term = f"Searchable_{self.faker.word()}"
#         search_entity = self._create(
#             server,
#             admin_a.jwt,
#             admin_a.id,
#             team_a.id,
#             key=f"{feature}_target",
#             search_term=search_term,
#         )

#         # Test search
#         search_field = (
#             self.searchable_fields[0] if self.searchable_fields else "name"
#         )
#         payload = {search_field: {"inc": search_term}}
#         url = self.get_search_endpoint(path_parent_ids)

#         response = server.post(url, json=payload, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         entities = self._extract_entities_from_response(data)

#         # Verify search result contains the target entity
#         entity_ids = [e["id"] for e in entities]
#         assert (
#             search_entity["id"] in entity_ids
#         ), "Search target entity not found in results"

#     elif feature == "filter":
#         # Test filter
#         filter_entity = self._create(
#             server, admin_a.jwt, admin_a.id, team_a.id, key=f"{feature}_target"
#         )

#         # Use first required field for filtering
#         filter_field = self.required_fields[0]
#         filter_value = filter_entity[filter_field]
#         url = f"{self.get_list_endpoint(path_parent_ids)}?{filter_field}={filter_value}"

#         response = server.get(url, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         entities = self._extract_entities_from_response(data)

#         # Verify filter result contains the target entity
#         entity_ids = [e["id"] for e in entities]
#         assert (
#             filter_entity["id"] in entity_ids
#         ), "Filter target entity not found in results"

# @pytest.mark.xfail
# @pytest.mark.parametrize(
#     "operation,expected",
#     [
#         # GraphQL test matrix
#         ("query_single", StatusCode.OK),
#         ("query_list", StatusCode.OK),
#         ("query_fields", StatusCode.OK),
#         ("query_pagination", StatusCode.OK),
#         (
#             "mutation_validation",
#             StatusCode.OK,
#         ),  # GraphQL always returns 200, even with errors
#     ],
# )
# def test_graphql_matrix(
#     self,
#     server: Any,
#     admin_a: Any,
#     team_a: Any,
#     operation: str,
#     expected: StatusCode,
# ):
#     """
#     Matrix test for GraphQL operations.

#     Args:
#         server: Test client
#         admin_a: Admin user
#         team_a: Team
#         operation: GraphQL operation to test
#         expected: Expected status code
#     """
#     # Create test entity
#     entity = self._create(
#         server, admin_a.jwt, admin_a.id, team_a.id, key=f"gql_{operation}"
#     )

#     # For pagination test, create multiple entities
#     if operation == "query_pagination":
#         self._batch_create(
#             server,
#             admin_a.jwt,
#             admin_a.id,
#             team_a.id,
#             count=5,
#             save_key=f"gql_{operation}_batch",
#         )

#     # Headers
#     headers = self._get_appropriate_headers(admin_a.jwt)

#     # Execute GraphQL operation
#     if operation == "query_single":
#         # Query single entity
#         query = f"""
#         query {{
#             {self.entity_name}(id: "{entity['id']}") {{
#                 id
#                 name
#             }}
#         }}
#         """

#         response = server.post("/graphql", json={"query": query}, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         assert "data" in data
#         assert self.entity_name in data["data"]
#         assert data["data"][self.entity_name]["id"] == entity["id"]

#     elif operation == "query_list":
#         # Query entity list
#         query = f"""
#         query {{
#             {self.resource_name_plural} {{
#                 id
#                 name
#             }}
#         }}
#         """

#         response = server.post("/graphql", json={"query": query}, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         assert "data" in data
#         assert self.resource_name_plural in data["data"]
#         assert len(data["data"][self.resource_name_plural]) > 0

#     elif operation == "query_fields":
#         # Query specific fields
#         fields = ["id"]
#         if self.string_field_to_update:
#             fields.append(self.string_field_to_update)

#         fields_selection = "\n                ".join(fields)

#         query = f"""
#         query {{
#             {self.entity_name}(id: "{entity['id']}") {{
#                 {fields_selection}
#             }}
#         }}
#         """

#         response = server.post("/graphql", json={"query": query}, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         assert "data" in data
#         assert self.entity_name in data["data"]

#         # Verify only requested fields are present
#         result = data["data"][self.entity_name]
#         assert len(result.keys()) == len(fields)
#         for field in fields:
#             assert field in result

#     elif operation == "query_pagination":
#         # Test pagination in GraphQL
#         query = f"""
#         query {{
#             {self.resource_name_plural}(first: 2) {{
#                 id
#                 name
#             }}
#         }}
#         """

#         response = server.post("/graphql", json={"query": query}, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         assert "data" in data
#         assert self.resource_name_plural in data["data"]
#         assert len(data["data"][self.resource_name_plural]) == 2

#         # Get second page
#         query = f"""
#         query {{
#             {self.resource_name_plural}(first: 2, skip: 2) {{
#                 id
#                 name
#             }}
#         }}
#         """

#         response = server.post("/graphql", json={"query": query}, headers=headers)

#         assert response.status_code == expected
#         data = response.json()
#         assert "data" in data
#         assert self.resource_name_plural in data["data"]
#         assert len(data["data"][self.resource_name_plural]) == 2

#     elif operation == "mutation_validation":
#         # Test validation in mutations
#         mutation = f"""
#         mutation {{
#             create{self.entity_name}(
#                 input: {{
#                     name: 12345  # Number instead of string
#                 }}
#             ) {{
#                 id
#                 name
#             }}
#         }}
#         """

#         response = server.post(
#             "/graphql", json={"query": mutation}, headers=headers
#         )

#         assert response.status_code == expected
#         data = response.json()
#         assert "errors" in data
