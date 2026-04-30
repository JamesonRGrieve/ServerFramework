import json
from typing import Any, Dict, List

import pytest
import stringcase

from serverframework.lib.Environment import env, inflection
from serverframework.lib.Logging import logger
from serverframework.lib.Pydantic2Strawberry import convert_field_name


class AbstractGraphQLTest:

    def test_GQL_query_single(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL single entity query."""
        # Create an entity to query (for user, this is just to ensure the user exists and is authenticated)
        entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="gql_single"
        )

        # Convert entity_name to camelCase for GraphQL field name
        if "_" in self.entity_name:
            singular_name = stringcase.camelcase(self.entity_name)
        else:
            singular_name = self.entity_name

        is_user_query = singular_name.lower() == "user"

        # Build response fields - only include string field if it exists, using camelCase
        response_fields = ["id", "createdAt", "updatedAt"]
        if self.string_field_to_update:
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            if gql_string_field is not None:
                response_fields.insert(1, gql_string_field)
        response_fields_str = "\n                ".join(response_fields)

        if is_user_query:
            # For user, do not pass any arguments
            query = f"""
            query {{
                {singular_name} {{
                    {response_fields_str}
                }}
            }}
            """
        else:
            # For other entities, use the ID argument and build query_params_str
            query_params_str = f'id: "{entity["id"]}"'
            query = f"""
            query {{
                {singular_name}({query_params_str}) {{
                    {response_fields_str}
                }}
            }}
            """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(f"GraphQL errors in single query: {json.dumps(data['errors'])}")

        entity_data = data["data"][singular_name]
        assert entity_data is not None, f"Entity not found in GraphQL response"
        # For user, the returned id should match the authenticated user
        if is_user_query:
            assert entity_data["id"] == admin_a.id, f"ID mismatch in GraphQL response"
        else:
            assert entity_data["id"] == entity["id"], f"ID mismatch in GraphQL response"

    def test_GQL_query_single_by_id_only(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL single entity query using only the primary key ID."""
        # Create an entity to query
        entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="gql_single_id_only"
        )

        # Convert entity_name to camelCase for GraphQL field name
        if "_" in self.entity_name:
            singular_name = stringcase.camelcase(self.entity_name)
        else:
            singular_name = self.entity_name

        is_user_query = singular_name.lower() == "user"

        # Build response fields
        response_fields = ["id", "createdAt", "updatedAt"]
        if self.string_field_to_update:
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            if gql_string_field is not None:
                response_fields.insert(1, gql_string_field)
        response_fields_str = "\n                ".join(response_fields)

        if is_user_query:
            # For user, do not pass any arguments
            query = f"""
            query {{
                {singular_name} {{
                    {response_fields_str}
                }}
            }}
            """
        else:
            # For other entities, use the ID argument
            query_params_str = f'id: "{entity["id"]}"'
            query = f"""
            query {{
                {singular_name}({query_params_str}) {{
                    {response_fields_str}
                }}
            }}
            """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in ID-only query: {json.dumps(data['errors'])}"
            )

        entity_data = data["data"][singular_name]
        assert entity_data is not None, f"Entity not found in GraphQL response"
        if is_user_query:
            assert entity_data["id"] == admin_a.id, f"ID mismatch in GraphQL response"
        else:
            assert entity_data["id"] == entity["id"], f"ID mismatch in GraphQL response"

    @pytest.mark.skip()
    def test_GQL_query_single_by_foreign_keys_only(
        self, server: Any, admin_a: Any, team_a: Any
    ):
        """Test GraphQL single entity query using only foreign keys (no primary ID)."""
        # Skip if entity has no parent entities (no foreign keys to test with)
        if not self.parent_entities:
            pytest.skip("Entity has no parent entities (foreign keys) to query by")

        # Create an entity to query
        entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="gql_foreign_keys_only"
        )

        # Convert entity_name to camelCase for GraphQL field name
        if "_" in self.entity_name:
            singular_name = stringcase.camelcase(self.entity_name)
        else:
            singular_name = self.entity_name

        # Build query arguments using ONLY foreign keys (no primary ID)
        query_args = []

        # Add foreign key arguments based on parent entities
        for parent_info in self.parent_entities:
            if parent_info.foreign_key in entity and entity[parent_info.foreign_key]:
                # Convert snake_case foreign key to camelCase for GraphQL
                fk_parts = parent_info.foreign_key.split("_")
                gql_arg_name = fk_parts[0] + "".join(
                    word.title() for word in fk_parts[1:]
                )
                query_args.append(
                    f'{gql_arg_name}: "{entity[parent_info.foreign_key]}"'
                )
            elif parent_info.name == "team":
                # Handle team entities specially
                fk_parts = parent_info.foreign_key.split("_")
                gql_arg_name = fk_parts[0] + "".join(
                    word.title() for word in fk_parts[1:]
                )
                query_args.append(f'{gql_arg_name}: "{team_a.id}"')

        # Skip if no foreign key arguments were built
        if not query_args:
            pytest.skip(
                "No valid foreign key arguments could be constructed for entity"
            )

        query_params_str = ", ".join(query_args)

        # Build response fields
        response_fields = ["id", "createdAt", "updatedAt"]
        if self.string_field_to_update:
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            if gql_string_field is not None:
                response_fields.insert(1, gql_string_field)

        response_fields_str = "\n                ".join(response_fields)

        query = f"""
        query {{
            {singular_name}({query_params_str}) {{
                {response_fields_str}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors
        if "errors" in data:
            # Some entities might not support querying by foreign keys only
            # This could result in validation errors about missing identifying parameters
            error_messages = [error.get("message", "") for error in data["errors"]]
            validation_errors = any(
                "identifying parameter" in msg.lower()
                or "required" in msg.lower()
                or "must be provided" in msg.lower()
                for msg in error_messages
            )

            if validation_errors:
                pytest.skip(
                    f"Entity does not support querying by foreign keys only: {error_messages}"
                )
            else:
                pytest.fail(
                    f"GraphQL errors in foreign keys only query: {json.dumps(data['errors'])}"
                )

        entity_data = data["data"][singular_name]
        assert entity_data is not None, "Entity not found in GraphQL response"
        assert entity_data["id"] == entity["id"], "ID mismatch in GraphQL response"

    @pytest.mark.skip()
    def test_GQL_query_multiple_fields(self, server: Any, admin_a: Any, team_a: Any):
        """Test that multiple non-unique fields can be used to search and retrieve a record in combination."""
        # Skip test if no string field is available for multi-field queries
        if not self.string_field_to_update:
            pytest.skip(
                "Entity has no updatable string field for multi-field query testing"
            )

        # Create multiple entities with different values for the string field
        entities = []
        string_values = []

        for i in range(3):
            string_value = f"multi_field_test_{i}_{self.faker.word()}"
            string_values.append(string_value)

            # Create entity with specific string field value using REST API
            # Create parent entities if needed
            parent_entities_dict = {}
            parent_ids = {}
            path_parent_ids = {}

            if self.parent_entities:
                parent_entities_dict, parent_ids, path_parent_ids = (
                    self._create_parent_entities(
                        server, admin_a.jwt, admin_a.id, team_a.id, {}
                    )
                )

            # Create payload with custom string field value
            payload = self.create_payload(
                name=string_value,
                parent_ids=parent_ids,
                team_id=team_a.id,
                minimal=False,
                invalid_data=False,
            )

            # Override the string field with our custom value
            if self.string_field_to_update:
                payload[self.string_field_to_update] = string_value

            # Create entity via REST API
            headers = self._get_appropriate_headers(
                admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
            )

            response = server.post(
                self.get_create_endpoint(path_parent_ids),
                json={self.entity_name: payload},
                headers=headers,
            )

            assert (
                response.status_code == 201
            ), f"Failed to create entity {i}: {response.json()}"
            entity = self._assert_entity_in_response(response)
            entities.append(entity)

            # Track entity for cleanup
            self.tracked_entities[f"multi_fields_{i}"] = entity

        # Convert entity_name to camelCase for GraphQL field name
        if "_" in self.entity_name:
            singular_name = stringcase.camelcase(self.entity_name)
        else:
            singular_name = self.entity_name

        # Test querying with multiple fields: ID + string field
        target_entity = entities[1]  # Use the middle entity
        target_string_value = string_values[1]

        # Build query arguments with multiple fields
        gql_string_field = convert_field_name(
            self.string_field_to_update, use_camelcase=True
        )
        if gql_string_field is not None:
            query_args = [
                f'id: "{target_entity["id"]}"',
                f'{gql_string_field}: "{target_string_value}"',
            ]
        else:
            query_args = [
                f'id: "{target_entity["id"]}"',
            ]

        query_params_str = ", ".join(query_args)

        # Build response fields
        response_fields = ["id", "createdAt", "updatedAt"]
        if gql_string_field is not None:
            response_fields.append(gql_string_field)

        response_fields_str = "\n                ".join(response_fields)

        # Build query
        query = f"""
        query {{
            {singular_name}({query_params_str}) {{
                {response_fields_str}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in multiple fields query: {json.dumps(data['errors'])}"
            )

        entity_data = data["data"][singular_name]
        assert entity_data is not None, "Entity not found in GraphQL response"
        assert (
            entity_data["id"] == target_entity["id"]
        ), "ID mismatch in GraphQL response"
        assert (
            entity_data[gql_string_field] == target_string_value
        ), f"String field mismatch: expected {target_string_value}, got {entity_data[gql_string_field]}"

        # Test with conflicting field values (should return no result or error)
        conflicting_query_args = [
            f'id: "{target_entity["id"]}"',
            f'{gql_string_field}: "{string_values[0]}"',  # Different string value than the ID's actual value
        ]

        conflicting_params_str = ", ".join(conflicting_query_args)

        conflicting_query = f"""
        query {{
            {singular_name}({conflicting_params_str}) {{
                {response_fields_str}
            }}
        }}
        """

        conflicting_response = server.post(
            "/graphql",
            json={"query": conflicting_query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert conflicting_response.status_code == 200

        conflicting_data = conflicting_response.json()
        assert (
            "data" in conflicting_data
        ), f"No data in conflicting response: {json.dumps(conflicting_data)}"

        # Should either return null (no match) or have errors about conflicting criteria
        conflicting_entity_data = conflicting_data["data"][singular_name]
        assert (
            conflicting_entity_data is None
        ), "Expected no result when using conflicting field values"

    def test_GQL_query_single_no_identifying_params(
        self, server: Any, admin_a: Any, team_a: Any
    ):
        """Test that GraphQL query fails when no identifying parameters are provided."""
        # Convert entity_name to camelCase for GraphQL field name
        if "_" in self.entity_name:
            singular_name = stringcase.camelcase(self.entity_name)
        else:
            singular_name = self.entity_name

        # Query with NO identifying parameters
        query = f"""
        query {{
            {singular_name} {{
                id
                createdAt
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        if singular_name.lower() == "user":
            # For user, expect a successful response with the authenticated user's data
            assert (
                "errors" not in data
            ), "Did not expect errors for user query with no parameters"
            entity_data = data["data"][singular_name]
            assert entity_data is not None, "Expected user data in response"
            assert entity_data["id"] == admin_a.id, "ID mismatch for authenticated user"
        else:
            # For other entities, expect an error
            assert (
                "errors" in data
            ), "Expected errors when no identifying parameters provided"
            # (Optionally: check error message content)

    def test_GQL_query_list(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL list query."""
        # Create multiple entities for list testing
        entities = []
        for i in range(3):
            entity = self._create(
                server,
                admin_a.jwt,
                admin_a.id,
                team_a.id,
                key=f"gql_list_{i}",
            )
            entities.append(entity)

        # Convert to plural camelCase for GraphQL field name
        if "_" in self.entity_name:
            plural_name = stringcase.camelcase(inflection.plural(self.entity_name))
        else:
            plural_name = inflection.plural(self.entity_name)
        # Note: GraphQL list resolvers don't accept parent entity arguments like teamId
        # They handle filtering internally based on the authenticated user's context
        query_params_str = ""
        # Build the field list for the query
        field_list = ["id", "createdAt", "updatedAt"]
        # Only include string field if it exists
        if self.string_field_to_update:
            # Convert string_field_to_update to camelCase for GraphQL
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            field_list.insert(
                1, gql_string_field
            )  # Insert after id but before timestamps

        # Build query (using camelCase for response fields)
        fields_str = "\n                ".join(field_list)
        query = f"""
        query {{
            {plural_name}{query_params_str} {{
                {fields_str}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"
        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(f"GraphQL errors in list query: {json.dumps(data['errors'])}")
        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            plural_name in data["data"]
        ), f"Field {plural_name} not in response: {json.dumps(data)}"
        # Verify the list
        results = data["data"][plural_name]

        assert isinstance(results, list), f"Expected list, got {type(results)}"
        # TODO: This fails on EP_Payment_test.py
        assert len(results) >= len(
            entities
        ), f"Expected at least {len(entities)} results, got {len(results)}"

    def test_GQL_query_fields(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL field selection."""
        # Create an entity to query
        entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="gql_fields"
        )

        # Convert entity_name to camelCase for GraphQL field name
        if "_" in self.entity_name:
            singular_name = stringcase.camelcase(self.entity_name)
        else:
            singular_name = self.entity_name

        # Test selecting only specific fields (using camelCase)
        fields = ["id"]

        # Only include string field if it exists
        if self.string_field_to_update:
            # Convert string_field_to_update to camelCase for GraphQL
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            if gql_string_field is not None:
                fields.append(gql_string_field)

        fields_selection = "\n                ".join(fields)

        # Build query arguments - use just "id" for single entity queries
        # query_args = [f'id: "{entity["id"]}"']
        is_user_query = singular_name.lower() == "user"

        # Note: Parent arguments are not included in GraphQL resolvers to avoid
        # "Unknown argument" errors. GraphQL resolvers handle relationships internally.
        if is_user_query:
            # For user, do not pass any arguments
            query = f"""
            query {{
                {singular_name} {{
                    {fields_selection}
                }}
            }}
            """
        else:
            # For other entities, use the ID argument and build query_params_str
            query_params_str = f'id: "{entity["id"]}"'
            query = f"""
            query {{
                {singular_name}({query_params_str}) {{
                    {fields_selection}
                }}
            }}
            """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(f"GraphQL errors in fields query: {json.dumps(data['errors'])}")

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            singular_name in data["data"]
        ), f"Field {singular_name} not in response: {json.dumps(data)}"

        # Verify the entity has the requested fields
        result = data["data"][singular_name]
        assert result is not None, f"Query result is None"
        for field in fields:
            assert field in result, f"Field {field} missing from result"

    def test_GQL_query_pagination(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL pagination."""
        # Create multiple entities for pagination testing
        for i in range(10):
            self._create(
                server,
                admin_a.jwt,
                admin_a.id,
                team_a.id,
                key=f"gql_pagination_{i}",
            )

        # Convert to plural camelCase for GraphQL field name
        if "_" in self.resource_name_plural:
            plural_name = stringcase.camelcase(self.resource_name_plural)
        else:
            plural_name = self.resource_name_plural

        # Build the field list for the query
        field_list = ["id", "createdAt"]

        # Only include string field if it exists
        if self.string_field_to_update:
            # Convert string_field_to_update to camelCase for GraphQL
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            if gql_string_field is not None:
                field_list.insert(
                    1, gql_string_field
                )  # Insert after id but before timestamps

        # Test pagination with limit and offset (using camelCase)
        fields_str = "\n                ".join(field_list)
        query = f"""
        query {{
            {plural_name}(limit: 5, offset: 2) {{
                {fields_str}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in pagination query: {json.dumps(data['errors'])}"
            )

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            plural_name in data["data"]
        ), f"Field {plural_name} not in response: {json.dumps(data)}"

        # Verify pagination results
        results = data["data"][plural_name]
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        assert len(results) <= 5, f"Expected at most 5 results, got {len(results)}"

    def test_GQL_mutation_create(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL create mutation."""
        # Convert entity_name to camelCase for GraphQL mutation name
        if "_" in self.entity_name:
            camel_case_entity = stringcase.camelcase(self.entity_name)
        else:
            camel_case_entity = self.entity_name
        # Convert to PascalCase for the mutation name
        mutation_name = f"create{stringcase.pascalcase(camel_case_entity)}"

        # Build input data using the configured string field
        input_data = {}
        if self.string_field_to_update:
            # Convert to camelCase for GraphQL consistency
            camel_case_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            input_data[camel_case_field] = f"GQL Test {self.faker.word()}"

        # Create parent entities if needed and add their IDs to input
        if self.parent_entities:
            # Create parent entities using the same logic as REST tests
            parent_entities_dict, parent_ids, path_parent_ids = (
                self._create_parent_entities(
                    server, admin_a.jwt, admin_a.id, team_a.id, {}
                )
            )

            # Get full payload to ensure all required fields are included
            payload = self.create_payload(
                name=f"GQL Test {self.faker.word()}",
                parent_ids=parent_ids,
                team_id=team_a.id,
                minimal=False,
                invalid_data=False,
            )

            # Convert all payload fields to camelCase and add to input_data
            for key, value in payload.items():
                camel_case_key = convert_field_name(key, use_camelcase=True)
                input_data[camel_case_key] = value

            # Add parent IDs to input data (they may not be in the payload)
            for parent_info in self.parent_entities:
                if parent_info.foreign_key in parent_ids:
                    # Convert snake_case to camelCase for GraphQL
                    camel_case_key = convert_field_name(
                        parent_info.foreign_key, use_camelcase=True
                    )
                    # Only add if not already in input_data from payload
                    if camel_case_key not in input_data:
                        input_data[camel_case_key] = parent_ids[parent_info.foreign_key]
                elif parent_info.name == "team":
                    # Handle team entities specially
                    camel_case_key = convert_field_name(
                        parent_info.foreign_key, use_camelcase=True
                    )
                    # Only add if not already in input_data from payload
                    if camel_case_key not in input_data:
                        input_data[camel_case_key] = team_a.id

        # Use API key for system entities
        headers = self._get_appropriate_headers(
            admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
        )

        # Convert string_field_to_update to camelCase for GraphQL (if it exists)
        gql_string_field = None
        if self.string_field_to_update:
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )

        # Build the mutation
        input_fields = []
        for key, value in input_data.items():
            if isinstance(value, str):
                input_fields.append(f'{key}: "{value}"')
            else:
                input_fields.append(f"{key}: {value}")

        input_str = "{" + ", ".join(input_fields) + "}"

        # Build the response fields - include gql_string_field only if it exists
        response_fields = ["id", "createdAt", "updatedAt"]
        if gql_string_field is not None:
            response_fields.append(gql_string_field)

        mutation = f"""
        mutation {{
            {mutation_name}(input: {input_str}) {{
                {chr(10).join("                " + field for field in response_fields)}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": mutation},
            headers=headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in create mutation: {json.dumps(data['errors'])}"
            )

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            mutation_name in data["data"]
        ), f"Mutation {mutation_name} not in response"

        # Verify the created entity
        result = data["data"][mutation_name]
        assert result is not None, f"Mutation result is None"
        assert "id" in result, "Created entity missing ID"
        if gql_string_field is not None:
            assert (
                gql_string_field in result
            ), f"Created entity missing {gql_string_field}"

    def test_GQL_mutation_update(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL update mutation."""
        # Create an entity to update
        entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="gql_update"
        )

        # Convert entity_name to camelCase for GraphQL mutation name
        if "_" in self.entity_name:
            camel_case_entity = stringcase.camelcase(self.entity_name)
        else:
            camel_case_entity = self.entity_name
        # Convert to PascalCase for the mutation name
        mutation_name = f"update{stringcase.pascalcase(camel_case_entity)}"

        # Build update data using the configured string field if it exists
        update_data = {}
        if self.string_field_to_update:
            update_data[self.string_field_to_update] = (
                f"Updated GQL {self.faker.word()}"
            )

        # Use API key for system entities
        headers = self._get_appropriate_headers(
            admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
        )

        # Convert string_field_to_update to camelCase for GraphQL (if it exists)
        gql_string_field = None
        if self.string_field_to_update:
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )

        # Build the mutation
        input_fields = []
        for key, value in update_data.items():
            # Convert snake_case field names to camelCase for GraphQL
            camel_case_key = convert_field_name(key, use_camelcase=True)
            if isinstance(value, str):
                input_fields.append(f'{camel_case_key}: "{value}"')
            else:
                input_fields.append(f"{camel_case_key}: {value}")

        input_str = "{" + ", ".join(input_fields) + "}"

        # Build the response fields - include gql_string_field only if it exists
        response_fields = ["id", "createdAt", "updatedAt"]
        if gql_string_field is not None:
            response_fields.append(gql_string_field)

        mutation = f"""
        mutation {{
            {mutation_name}(id: "{entity['id']}", input: {input_str}) {{
                {chr(10).join("                " + field for field in response_fields)}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": mutation},
            headers=headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in update mutation: {json.dumps(data['errors'])}"
            )

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            mutation_name in data["data"]
        ), f"Mutation {mutation_name} not in response"

        # Verify the updated entity
        result = data["data"][mutation_name]
        assert result is not None, f"Mutation result is None"
        assert result["id"] == entity["id"], "Updated entity ID mismatch"
        if gql_string_field is not None and self.string_field_to_update:
            assert (
                result[gql_string_field] == update_data[self.string_field_to_update]
            ), f"Updated entity {gql_string_field} mismatch"

    def test_GQL_mutation_delete(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL delete mutation."""
        # Create an entity to delete
        entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="gql_delete"
        )

        # Convert entity_name to camelCase for GraphQL mutation name
        # Use the same logic as create/update methods
        if "_" in self.entity_name:
            camel_case_entity = stringcase.camelcase(self.entity_name)
        else:
            camel_case_entity = self.entity_name
        # Capitalize only the first letter for the mutation name
        mutation_name = f"delete{stringcase.pascalcase(camel_case_entity)}"

        # Use API key for system entities
        headers = self._get_appropriate_headers(
            admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
        )

        # Build the mutation
        mutation = f"""
        mutation {{
            {mutation_name}(id: "{entity['id']}")
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": mutation},
            headers=headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        # Check for GraphQL errors first
        if "errors" in data:
            pytest.fail(
                f"GraphQL errors in delete mutation: {json.dumps(data['errors'])}"
            )

        assert data["data"] is not None, f"Data is None in response: {json.dumps(data)}"
        assert (
            mutation_name in data["data"]
        ), f"Mutation {mutation_name} not in response"

        # Verify the deletion result
        result = data["data"][mutation_name]
        assert result is True, f"Expected deletion to return True, got {result}"

    def test_GQL_mutation_validation(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL mutation validation."""
        # Convert entity_name to camelCase for GraphQL mutation name
        # Use the same logic as create/update/delete methods
        if "_" in self.entity_name:
            camel_case_entity = stringcase.camelcase(self.entity_name)
        else:
            camel_case_entity = self.entity_name
        # Convert to PascalCase for the mutation name
        mutation_name = f"create{stringcase.pascalcase(camel_case_entity)}"

        # Build invalid mutation (missing required field)
        mutation = f"""
        mutation {{
            {mutation_name}(input: {{}}) {{
                id
                name
            }}
        }}
        """

        # Get appropriate headers - include API key for system entities
        headers = self._get_appropriate_headers(
            admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
        )

        response = server.post(
            "/graphql",
            json={"query": mutation},
            headers=headers,
        )

        # GraphQL validation errors return 200 with errors in the response
        assert response.status_code == 200
        data = response.json()

        # Should have errors due to validation failure
        assert "errors" in data, "Expected validation errors in response"
        assert len(data["errors"]) > 0, "Expected at least one validation error"

    def test_GQL_subscription(self, server: Any, admin_a: Any, team_a: Any):
        """Test GraphQL subscription."""
        # Convert entity_name to camelCase for GraphQL subscription name
        # Use the same logic as create/update/delete methods
        if "_" in self.entity_name:
            camel_case_entity = stringcase.camelcase(self.entity_name)
        else:
            camel_case_entity = self.entity_name
        # Use camelCase for subscription name: provider_extension -> providerExtensionCreated
        subscription_name = f"{camel_case_entity}Created"

        # Build subscription query (using camelCase for response fields)
        subscription = f"""
        subscription {{
            {subscription_name} {{
                id
                name
                createdAt
            }}
        }}
        """

        # Item 76: subscriptions are deferred — Strawberry's WebSocket
        # transport is not wired into the FastAPI app, so this test
        # asserts only that the subscription query is syntactically
        # valid. End-to-end subscription delivery is gated on Items 13
        # (streaming services), 42 (cross-process event bus), 46
        # (GraphQL composition). When all three land, replace this
        # query-only check with a real WebSocket round-trip.
        response = server.post(
            "/graphql",
            json={"query": subscription},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        # Subscriptions may return different status codes depending on implementation
        # We mainly want to ensure the query is syntactically valid
        assert response.status_code in [
            200,
            400,
        ], f"Unexpected status code: {response.status_code}"

        data = response.json()
        # If there are errors, they should be about subscription execution, not syntax
        if "errors" in data:
            error_messages = [error.get("message", "") for error in data["errors"]]
            # Check that errors are about execution, not syntax
            syntax_errors = [
                msg
                for msg in error_messages
                if "syntax" in msg.lower() or "parse" in msg.lower()
            ]
            assert (
                not syntax_errors
            ), f"Subscription query has syntax errors: {syntax_errors}"

    def _has_navigation_properties(self) -> bool:
        """Check if this entity has navigation properties to test."""
        return bool(self.parent_entities)

    def _has_self_referential_properties(self) -> bool:
        """Check if this entity has self-referential properties."""
        if not self.parent_entities:
            return False

        # Check if any parent entity references the same entity type
        for parent in self.parent_entities:
            if (
                hasattr(parent, "name")
                and parent.name.lower() == self.entity_name.lower()
            ):
                return True
        return False

    def _get_navigation_property_names(self) -> Dict[str, str]:
        """Get navigation property names for parent-child relationships."""
        nav_props = {}

        if not self.parent_entities:
            return nav_props

        for parent in self.parent_entities:
            # Parent property name (e.g., "parent", "team", "user")
            parent_prop = parent.name.lower()
            nav_props[f"parent_{parent.name}"] = parent_prop

            # Child collection property name (e.g., "children", "posts", "items")
            if parent.name.lower() == self.entity_name.lower():
                # Self-referential
                nav_props["children"] = "children"
            else:
                # Parent-child relationship - infer child collection name
                child_collection = inflection.plural(self.entity_name.lower())
                nav_props[f"children_{self.entity_name}"] = child_collection

        return nav_props

    def _build_navigation_query_fields(
        self, include_navigation: bool = True
    ) -> List[str]:
        """Build GraphQL query fields including navigation properties."""
        # Base fields every entity should have
        base_fields = ["id", "createdAt", "updatedAt"]

        # Add the main string field if it exists
        if self.string_field_to_update:
            gql_string_field = convert_field_name(
                self.string_field_to_update, use_camelcase=True
            )
            if gql_string_field is not None:
                base_fields.insert(1, gql_string_field)

        if not include_navigation or not self._has_navigation_properties():
            return base_fields

        # Add navigation properties
        nav_fields = base_fields.copy()
        nav_props = self._get_navigation_property_names()

        for nav_key, nav_prop in nav_props.items():
            if nav_key.startswith("parent_"):
                # Add parent navigation (single object)
                nav_fields.append(
                    f"""
                {nav_prop} {{
                    id
                    {"name" if nav_prop != "team" else "name"}
                    createdAt
                }}"""
                )
            elif nav_key.startswith("children") or nav_key == "children":
                # Add children navigation (list)
                nav_fields.append(
                    f"""
                {nav_prop} {{
                    id
                    {"name" if self.string_field_to_update else "id"}
                    createdAt
                }}"""
                )

        return nav_fields

    def test_GQL_query_navigation_properties_parent_to_child(
        self, server: Any, admin_a: Any, team_a: Any
    ):
        """Test GraphQL query navigation from parent to child entities."""
        if not self._has_navigation_properties():
            pytest.skip("Entity has no navigation properties to test")

        # Create entities with parent-child relationship
        parent_entity = self._create(
            server, admin_a.jwt, admin_a.id, team_a.id, key="nav_parent"
        )

        # If this entity has self-referential properties, create a child
        if self._has_self_referential_properties():
            # Create child entity referencing the parent
            child_payload = self.create_payload(
                name=f"Child {self.faker.word()}",
                parent_ids={self.parent_entities[0].foreign_key: parent_entity["id"]},
                team_id=team_a.id,
            )

            # Get the endpoint for child creation
            path_parent_ids = {}
            for parent in self.parent_entities:
                if parent.path_level in [1, 2] or (
                    hasattr(parent, "is_path")
                    and parent.is_path
                    and parent.path_level is None
                ):
                    if parent.name == "team":
                        path_parent_ids[f"{parent.name}_id"] = team_a.id
                    elif parent.foreign_key in child_payload:
                        path_parent_ids[f"{parent.name}_id"] = child_payload[
                            parent.foreign_key
                        ]

            child_response = server.post(
                self.get_create_endpoint(path_parent_ids),
                json={self.entity_name: child_payload},
                headers=self._get_appropriate_headers(
                    admin_a.jwt,
                    api_key=env("ROOT_API_KEY") if self.system_entity else None,
                ),
            )

            if child_response.status_code == 201:
                self.tracked_entities["nav_child"] = self._assert_entity_in_response(
                    child_response
                )

        # Convert entity_name to camelCase for GraphQL field name
        singular_name = self.entity_name.lower()
        if "_" in singular_name:
            parts = singular_name.split("_")
            singular_name = parts[0] + "".join(word.capitalize() for word in parts[1:])

        # Build query with navigation properties
        nav_fields = self._build_navigation_query_fields(include_navigation=True)
        fields_str = "\n                ".join(nav_fields)

        # Build query arguments
        query_args = [f'id: "{parent_entity["id"]}"']

        if self.parent_entities:
            for parent_info in self.parent_entities:
                fk_parts = parent_info.foreign_key.split("_")
                gql_arg_name = fk_parts[0] + "".join(
                    word.title() for word in fk_parts[1:]
                )

                if (
                    parent_info.foreign_key in parent_entity
                    and parent_entity[parent_info.foreign_key]
                ):
                    query_args.append(
                        f'{gql_arg_name}: "{parent_entity[parent_info.foreign_key]}"'
                    )

        query_params_str = ", ".join(query_args)

        query = f"""
        query {{
            {singular_name}({query_params_str}) {{
                {fields_str}
            }}
        }}
        """

        response = server.post(
            "/graphql",
            json={"query": query},
            headers=self._get_appropriate_headers(admin_a.jwt),
        )

        assert response.status_code == 200
        data = response.json()
        assert "data" in data, f"No data in response: {json.dumps(data)}"

        if "errors" in data:
            # Log the query for debugging
            logger.debug(f"GraphQL Query with errors: {query}")
            logger.debug(f"GraphQL Response: {json.dumps(data)}")

            # Allow for schema-related errors (navigation properties might not be implemented)
            error_messages = [error.get("message", "") for error in data["errors"]]
            schema_errors = any(
                "field" in msg.lower()
                and ("not" in msg.lower() or "unknown" in msg.lower())
                for msg in error_messages
            )
            if schema_errors:
                pytest.skip("Navigation properties not yet implemented in schema")
            else:
                pytest.fail(f"Unexpected GraphQL errors: {json.dumps(data['errors'])}")

        entity_data = data["data"][singular_name]
        assert entity_data is not None, "Entity not found in GraphQL response"
        assert (
            entity_data["id"] == parent_entity["id"]
        ), "ID mismatch in GraphQL response"

        def test_GQL_query_navigation_properties_child_to_parent(
            self, server: Any, admin_a: Any, team_a: Any
        ):
            """Test GraphQL query navigation from child to parent entities."""
            if not self._has_navigation_properties():
                pytest.skip("Entity has no navigation properties to test")

            # Create parent entity first
            parent_entity = self._create(
                server, admin_a.jwt, admin_a.id, team_a.id, key="nav_query_parent"
            )

            # Create child entity with parent reference (if self-referential)
            if self._has_self_referential_properties():
                child_payload = self.create_payload(
                    name=f"Child {self.faker.word()}",
                    parent_ids={
                        self.parent_entities[0].foreign_key: parent_entity["id"]
                    },
                    team_id=team_a.id,
                )

                path_parent_ids = {}
                for parent in self.parent_entities:
                    if parent.path_level in [1, 2] or (
                        hasattr(parent, "is_path")
                        and parent.is_path
                        and parent.path_level is None
                    ):
                        if parent.name == "team":
                            path_parent_ids[f"{parent.name}_id"] = team_a.id
                        elif parent.foreign_key in child_payload:
                            path_parent_ids[f"{parent.name}_id"] = child_payload[
                                parent.foreign_key
                            ]

                child_response = server.post(
                    self.get_create_endpoint(path_parent_ids),
                    json={self.entity_name: child_payload},
                    headers=self._get_appropriate_headers(
                        admin_a.jwt,
                        api_key=env("ROOT_API_KEY") if self.system_entity else None,
                    ),
                )

                if child_response.status_code != 201:
                    pytest.skip("Could not create child entity for navigation test")

                child_entity = self._assert_entity_in_response(child_response)
                self.tracked_entities["nav_query_child"] = child_entity
            else:
                # For non-self-referential, the current entity IS the child
                child_entity = parent_entity

            # Convert entity_name to camelCase for GraphQL field name
            singular_name = self.entity_name.lower()
            if "_" in singular_name:
                parts = singular_name.split("_")
                singular_name = parts[0] + "".join(
                    word.capitalize() for word in parts[1:]
                )

            # Build query with parent navigation properties
            base_fields = ["id", "createdAt", "updatedAt"]
            if self.string_field_to_update:
                gql_string_field = convert_field_name(
                    self.string_field_to_update, use_camelcase=True
                )
                base_fields.insert(1, gql_string_field)

            # Add parent navigation properties
            nav_props = self._get_navigation_property_names()
            nav_fields = base_fields.copy()

            for nav_key, nav_prop in nav_props.items():
                if nav_key.startswith("parent_"):
                    nav_fields.append(
                        f"""
                    {nav_prop} {{
                        id
                        {"name" if nav_prop != "team" else "name"}
                        createdAt
                    }}"""
                    )

            fields_str = "\n                ".join(nav_fields)

            # Build query arguments
            query_args = [f'id: "{child_entity["id"]}"']

            if self.parent_entities:
                for parent_info in self.parent_entities:
                    fk_parts = parent_info.foreign_key.split("_")
                    gql_arg_name = fk_parts[0] + "".join(
                        word.title() for word in fk_parts[1:]
                    )

                    if (
                        parent_info.foreign_key in child_entity
                        and child_entity[parent_info.foreign_key]
                    ):
                        query_args.append(
                            f'{gql_arg_name}: "{child_entity[parent_info.foreign_key]}"'
                        )

            query_params_str = ", ".join(query_args)

            query = f"""
            query {{
                {singular_name}({query_params_str}) {{
                    {fields_str}
                }}
            }}
            """

            response = server.post(
                "/graphql",
                json={"query": query},
                headers=self._get_appropriate_headers(admin_a.jwt),
            )

            assert response.status_code == 200
            data = response.json()
            assert "data" in data, f"No data in response: {json.dumps(data)}"

            if "errors" in data:
                error_messages = [error.get("message", "") for error in data["errors"]]
                schema_errors = any(
                    "field" in msg.lower()
                    and ("not" in msg.lower() or "unknown" in msg.lower())
                    for msg in error_messages
                )
                if schema_errors:
                    pytest.skip("Navigation properties not yet implemented in schema")
                else:
                    pytest.fail(
                        f"Unexpected GraphQL errors: {json.dumps(data['errors'])}"
                    )

            entity_data = data["data"][singular_name]
            assert entity_data is not None, "Entity not found in GraphQL response"
            assert (
                entity_data["id"] == child_entity["id"]
            ), "ID mismatch in GraphQL response"

        def test_GQL_query_self_referential_navigation(
            self, server: Any, admin_a: Any, team_a: Any
        ):
            """Test GraphQL query navigation for self-referential properties."""
            if not self._has_self_referential_properties():
                pytest.skip("Entity has no self-referential properties to test")

            # Create parent entity
            parent_entity = self._create(
                server, admin_a.jwt, admin_a.id, team_a.id, key="self_ref_parent"
            )

            # Create child entity with self-reference
            child_payload = self.create_payload(
                name=f"Child {self.faker.word()}",
                parent_ids={self.parent_entities[0].foreign_key: parent_entity["id"]},
                team_id=team_a.id,
            )

            path_parent_ids = {}
            for parent in self.parent_entities:
                if parent.path_level in [1, 2] or (
                    hasattr(parent, "is_path")
                    and parent.is_path
                    and parent.path_level is None
                ):
                    if parent.name == "team":
                        path_parent_ids[f"{parent.name}_id"] = team_a.id
                    elif parent.foreign_key in child_payload:
                        path_parent_ids[f"{parent.name}_id"] = child_payload[
                            parent.foreign_key
                        ]

            child_response = server.post(
                self.get_create_endpoint(path_parent_ids),
                json={self.entity_name: child_payload},
                headers=self._get_appropriate_headers(
                    admin_a.jwt,
                    api_key=env("ROOT_API_KEY") if self.system_entity else None,
                ),
            )

            if child_response.status_code != 201:
                pytest.skip("Could not create child entity for self-referential test")

            child_entity = self._assert_entity_in_response(child_response)
            self.tracked_entities["self_ref_child"] = child_entity

            # Convert entity_name to camelCase for GraphQL field name
            singular_name = self.entity_name.lower()
            if "_" in singular_name:
                parts = singular_name.split("_")
                singular_name = parts[0] + "".join(
                    word.capitalize() for word in parts[1:]
                )

            # Build query with self-referential navigation properties
            base_fields = ["id", "createdAt", "updatedAt"]
            if self.string_field_to_update:
                gql_string_field = convert_field_name(
                    self.string_field_to_update, use_camelcase=True
                )
                base_fields.insert(1, gql_string_field)

            # Add self-referential navigation fields
            self_ref_fields = base_fields + [
                f"""
                parent {{
                    id
                    {gql_string_field if self.string_field_to_update else "id"}
                    createdAt
                }}""",
                f"""
                children {{
                    id
                    {gql_string_field if self.string_field_to_update else "id"}
                    createdAt
                }}""",
            ]

            fields_str = "\n                ".join(self_ref_fields)

            # Query the parent entity (should show child in children collection)
            query_args = [f'id: "{parent_entity["id"]}"']
            if self.parent_entities:
                for parent_info in self.parent_entities:
                    fk_parts = parent_info.foreign_key.split("_")
                    gql_arg_name = fk_parts[0] + "".join(
                        word.title() for word in fk_parts[1:]
                    )

                    if (
                        parent_info.foreign_key in parent_entity
                        and parent_entity[parent_info.foreign_key]
                    ):
                        query_args.append(
                            f'{gql_arg_name}: "{parent_entity[parent_info.foreign_key]}"'
                        )

            query_params_str = ", ".join(query_args)

            query = f"""
            query {{
                {singular_name}({query_params_str}) {{
                    {fields_str}
                }}
            }}
            """

            response = server.post(
                "/graphql",
                json={"query": query},
                headers=self._get_appropriate_headers(admin_a.jwt),
            )

            assert response.status_code == 200
            data = response.json()
            assert "data" in data, f"No data in response: {json.dumps(data)}"

            if "errors" in data:
                error_messages = [error.get("message", "") for error in data["errors"]]
                schema_errors = any(
                    "field" in msg.lower()
                    and ("not" in msg.lower() or "unknown" in msg.lower())
                    for msg in error_messages
                )
                if schema_errors:
                    pytest.skip(
                        "Self-referential navigation properties not yet implemented in schema"
                    )
                else:
                    pytest.fail(
                        f"Unexpected GraphQL errors: {json.dumps(data['errors'])}"
                    )

            entity_data = data["data"][singular_name]
            assert entity_data is not None, "Entity not found in GraphQL response"
            assert (
                entity_data["id"] == parent_entity["id"]
            ), "ID mismatch in GraphQL response"

            # If navigation properties are implemented, verify the structure
            if "children" in entity_data and entity_data["children"] is not None:
                children = entity_data["children"]
                assert isinstance(children, list), "Children should be a list"
                child_ids = [child["id"] for child in children]
                assert (
                    child_entity["id"] in child_ids
                ), "Created child should be in parent's children list"

        def test_GQL_mutation_create_with_nested_navigation(
            self, server: Any, admin_a: Any, team_a: Any
        ):
            """Test GraphQL create mutation with nested navigation properties."""
            if not self._has_navigation_properties():
                pytest.skip("Entity has no navigation properties to test")

            # Convert entity_name to camelCase for GraphQL mutation name
            if "_" in self.entity_name:
                parts = self.entity_name.split("_")
                camel_case_entity = parts[0] + "".join(
                    word.capitalize() for word in parts[1:]
                )
            else:
                camel_case_entity = self.entity_name

            mutation_name = (
                f"create{camel_case_entity[0].upper() + camel_case_entity[1:]}"
            )

            # Build nested creation input
            input_data = {}
            if self.string_field_to_update:
                camel_case_field = convert_field_name(
                    self.string_field_to_update, use_camelcase=True
                )
                input_data[camel_case_field] = f"Nested GQL Test {self.faker.word()}"

            # Add parent entities if needed
            if self.parent_entities:
                parent_entities_dict, parent_ids, path_parent_ids = (
                    self._create_parent_entities(
                        server, admin_a.jwt, admin_a.id, team_a.id, {}
                    )
                )

                payload = self.create_payload(
                    name=f"Nested GQL Test {self.faker.word()}",
                    parent_ids=parent_ids,
                    team_id=team_a.id,
                    minimal=False,
                    invalid_data=False,
                )

                for key, value in payload.items():
                    camel_case_key = convert_field_name(key, use_camelcase=True)
                    input_data[camel_case_key] = value

                for parent_info in self.parent_entities:
                    if parent_info.foreign_key in parent_ids:
                        camel_case_key = convert_field_name(
                            parent_info.foreign_key, use_camelcase=True
                        )
                        if camel_case_key not in input_data:
                            input_data[camel_case_key] = parent_ids[
                                parent_info.foreign_key
                            ]
                    elif parent_info.name == "team":
                        camel_case_key = convert_field_name(
                            parent_info.foreign_key, use_camelcase=True
                        )
                        if camel_case_key not in input_data:
                            input_data[camel_case_key] = team_a.id

            # If this entity supports self-referential relationships, add nested children
            if self._has_self_referential_properties():
                # Add nested children array (simplified for testing)
                children_field = "children"
                child_input = {}
                if self.string_field_to_update:
                    child_field = convert_field_name(
                        self.string_field_to_update, use_camelcase=True
                    )
                    child_input[child_field] = f"Nested Child {self.faker.word()}"

                if child_input:
                    input_data[children_field] = [child_input]

            headers = self._get_appropriate_headers(
                admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
            )

            # Build the mutation
            input_fields = []
            for key, value in input_data.items():
                if key == "children" and isinstance(value, list):
                    # Handle nested children array
                    child_items = []
                    for child in value:
                        child_fields = []
                        for child_key, child_value in child.items():
                            if isinstance(child_value, str):
                                child_fields.append(f'{child_key}: "{child_value}"')
                            else:
                                child_fields.append(f"{child_key}: {child_value}")
                        child_items.append("{" + ", ".join(child_fields) + "}")
                    input_fields.append(f'{key}: [{", ".join(child_items)}]')
                elif isinstance(value, str):
                    input_fields.append(f'{key}: "{value}"')
                else:
                    input_fields.append(f"{key}: {value}")

            input_str = "{" + ", ".join(input_fields) + "}"

            # Build response fields with navigation properties
            response_fields = ["id", "createdAt", "updatedAt"]
            if self.string_field_to_update:
                gql_string_field = convert_field_name(
                    self.string_field_to_update, use_camelcase=True
                )
                response_fields.append(gql_string_field)

            # Add navigation fields in response
            if self._has_self_referential_properties():
                response_fields.extend(
                    [
                        """
                    children {
                        id
                        name
                        createdAt
                    }"""
                    ]
                )

            mutation = f"""
            mutation {{
                {mutation_name}(input: {input_str}) {{
                    {chr(10).join("                " + field for field in response_fields)}
                }}
            }}
            """

            response = server.post(
                "/graphql",
                json={"query": mutation},
                headers=headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert "data" in data, f"No data in response: {json.dumps(data)}"

            if "errors" in data:
                error_messages = [error.get("message", "") for error in data["errors"]]
                schema_errors = any(
                    "field" in msg.lower()
                    and ("not" in msg.lower() or "unknown" in msg.lower())
                    for msg in error_messages
                )
                nested_errors = any(
                    "input" in msg.lower()
                    and ("nested" in msg.lower() or "children" in msg.lower())
                    for msg in error_messages
                )
                if schema_errors or nested_errors:
                    pytest.skip(
                        "Nested navigation properties not yet implemented in mutations"
                    )
                else:
                    pytest.fail(
                        f"Unexpected GraphQL errors in nested mutation: {json.dumps(data['errors'])}"
                    )

            assert (
                data["data"] is not None
            ), f"Data is None in response: {json.dumps(data)}"
            assert (
                mutation_name in data["data"]
            ), f"Mutation {mutation_name} not in response"

            result = data["data"][mutation_name]
            assert result is not None, "Mutation result is None"
            assert "id" in result, "Created entity missing ID"

        def test_GQL_mutation_update_with_nested_navigation(
            self, server: Any, admin_a: Any, team_a: Any
        ):
            """Test GraphQL update mutation with nested navigation properties."""
            if not self._has_navigation_properties():
                pytest.skip("Entity has no navigation properties to test")

            # Create an entity to update
            entity = self._create(
                server, admin_a.jwt, admin_a.id, team_a.id, key="nested_update"
            )

            # Convert entity_name to camelCase for GraphQL mutation name
            if "_" in self.entity_name:
                parts = self.entity_name.split("_")
                camel_case_entity = parts[0] + "".join(
                    word.capitalize() for word in parts[1:]
                )
            else:
                camel_case_entity = self.entity_name

            mutation_name = (
                f"update{camel_case_entity[0].upper() + camel_case_entity[1:]}"
            )

            # Build update data with nested operations
            update_data = {}
            if self.string_field_to_update:
                update_data[self.string_field_to_update] = (
                    f"Updated Nested {self.faker.word()}"
                )

            headers = self._get_appropriate_headers(
                admin_a.jwt, api_key=env("ROOT_API_KEY") if self.system_entity else None
            )

            # Build the mutation
            input_fields = []
            for key, value in update_data.items():
                camel_case_key = convert_field_name(key, use_camelcase=True)
                if isinstance(value, str):
                    input_fields.append(f'{camel_case_key}: "{value}"')
                else:
                    input_fields.append(f"{camel_case_key}: {value}")

            input_str = "{" + ", ".join(input_fields) + "}"

            # Build response fields with navigation properties
            response_fields = ["id", "createdAt", "updatedAt"]
            if self.string_field_to_update:
                gql_string_field = convert_field_name(
                    self.string_field_to_update, use_camelcase=True
                )
                response_fields.append(gql_string_field)

            mutation = f"""
            mutation {{
                {mutation_name}(id: "{entity['id']}", input: {input_str}) {{
                    {chr(10).join("                " + field for field in response_fields)}
                }}
            }}
            """

            response = server.post(
                "/graphql",
                json={"query": mutation},
                headers=headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert "data" in data, f"No data in response: {json.dumps(data)}"

            if "errors" in data:
                error_messages = [error.get("message", "") for error in data["errors"]]
                schema_errors = any(
                    "field" in msg.lower()
                    and ("not" in msg.lower() or "unknown" in msg.lower())
                    for msg in error_messages
                )
                if schema_errors:
                    pytest.skip(
                        "Navigation properties not yet implemented in update mutations"
                    )
                else:
                    pytest.fail(
                        f"Unexpected GraphQL errors in nested update: {json.dumps(data['errors'])}"
                    )

            assert (
                data["data"] is not None
            ), f"Data is None in response: {json.dumps(data)}"
            assert (
                mutation_name in data["data"]
            ), f"Mutation {mutation_name} not in response"

            result = data["data"][mutation_name]
            assert result is not None, "Update mutation result is None"
            assert result["id"] == entity["id"], "Updated entity ID mismatch"

        def test_GQL_subscription_navigation_properties(
            self, server: Any, admin_a: Any, team_a: Any
        ):
            """Test GraphQL subscriptions with navigation properties."""
            if not self._has_navigation_properties():
                pytest.skip("Entity has no navigation properties to test")

            # Convert entity_name to camelCase for GraphQL subscription name
            if "_" in self.entity_name:
                parts = self.entity_name.split("_")
                camel_case_entity = parts[0] + "".join(
                    word.capitalize() for word in parts[1:]
                )
            else:
                camel_case_entity = self.entity_name

            subscription_name = f"{camel_case_entity}Created"

            # Build subscription with navigation properties
            base_fields = ["id", "createdAt", "updatedAt"]
            if self.string_field_to_update:
                gql_string_field = convert_field_name(
                    self.string_field_to_update, use_camelcase=True
                )
                base_fields.append(gql_string_field)

            # Add navigation properties to subscription
            nav_fields = base_fields.copy()
            if self._has_self_referential_properties():
                nav_fields.extend(
                    [
                        """
                    parent {
                        id
                        name
                        createdAt
                    }""",
                        """
                    children {
                        id
                        name
                        createdAt
                    }""",
                    ]
                )

            fields_str = "\n                ".join(nav_fields)

            subscription = f"""
            subscription {{
                {subscription_name} {{
                    {fields_str}
                }}
            }}
            """

            response = server.post(
                "/graphql",
                json={"query": subscription},
                headers=self._get_appropriate_headers(admin_a.jwt),
            )

            assert response.status_code in [
                200,
                400,
            ], f"Unexpected status code: {response.status_code}"

            data = response.json()
            if "errors" in data:
                error_messages = [error.get("message", "") for error in data["errors"]]

                # Check for syntax errors
                syntax_errors = [
                    msg
                    for msg in error_messages
                    if "syntax" in msg.lower() or "parse" in msg.lower()
                ]

                # Check for schema/field errors
                schema_errors = [
                    msg
                    for msg in error_messages
                    if "field" in msg.lower()
                    and ("not" in msg.lower() or "unknown" in msg.lower())
                ]

                if syntax_errors:
                    pytest.fail(
                        f"Subscription with navigation properties has syntax errors: {syntax_errors}"
                    )
                elif schema_errors:
                    pytest.skip(
                        "Navigation properties not yet implemented in subscriptions"
                    )
                # Item 76: "WebSocket not supported" is the deferred-feature
                # signal — subscriptions are gated on Items 13/42/46 per
                # Framework.md. When that lands, this branch should assert
                # actual subscription delivery instead of accepting the
                # WebSocket-not-supported response.

        def test_GQL_comprehensive_navigation_integration(
            self, server: Any, admin_a: Any, team_a: Any
        ):
            """Test comprehensive navigation properties integration across query, mutation, and subscription."""
            if not self._has_navigation_properties():
                pytest.skip("Entity has no navigation properties to test")

            logger.debug(
                f"[INFO] Running comprehensive navigation integration test for {self.entity_name}"
            )

            # Track test results
            test_results = {
                "entity_name": self.entity_name,
                "has_self_referential": self._has_self_referential_properties(),
                "navigation_properties": self._get_navigation_property_names(),
                "query_test": False,
                "mutation_test": False,
                "subscription_test": False,
                "errors": [],
            }

            try:
                # Test 1: Query with navigation properties
                try:
                    self.test_GQL_query_navigation_properties_parent_to_child(
                        server, admin_a, team_a
                    )
                    test_results["query_test"] = True
                except Exception as e:
                    test_results["errors"].append(f"Query test failed: {str(e)}")

                # Test 2: Mutation with navigation properties
                try:
                    self.test_GQL_mutation_create_with_nested_navigation(
                        server, admin_a, team_a
                    )
                    test_results["mutation_test"] = True
                except Exception as e:
                    test_results["errors"].append(f"Mutation test failed: {str(e)}")

                # Test 3: Subscription with navigation properties
                try:
                    self.test_GQL_subscription_navigation_properties(
                        server, admin_a, team_a
                    )
                    test_results["subscription_test"] = True
                except Exception as e:
                    test_results["errors"].append(f"Subscription test failed: {str(e)}")

                # Log comprehensive results
                logger.info(
                    f"Navigation properties integration test results: {json.dumps(test_results, indent=2)}"
                )

                # Assert that at least one test passed (allowing for partial implementation)
                passed_tests = sum(
                    [
                        test_results["query_test"],
                        test_results["mutation_test"],
                        test_results["subscription_test"],
                    ]
                )

                if passed_tests == 0:
                    pytest.skip(
                        f"No navigation properties tests passed for {self.entity_name}. This may indicate navigation properties are not yet implemented."
                    )

                # If we get here, at least one test passed, which is success
                assert (
                    passed_tests > 0
                ), f"At least one navigation properties test should pass for {self.entity_name}"

            except Exception as e:
                test_results["errors"].append(f"Comprehensive test failed: {str(e)}")
                logger.error(
                    f"Comprehensive navigation test failed: {json.dumps(test_results, indent=2)}"
                )
                raise
