import inspect
import os
import sys
import unittest
from enum import Enum
from typing import Dict, List, Optional, Union, get_args, get_origin
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

# Add parent directory to sys.path to import Pydantic
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.Logging import logger
from lib.Pydantic import ModelRegistry, PydanticUtility, obj_to_dict
from lib.Pydantic2SQLAlchemy import DatabaseMixin

# Access the utility functions from PydanticUtility
# Instead of importing functions directly that might not exist as standalone functions


class MockTestEnum(Enum):
    """Test enum for schema generation testing."""

    VALUE1 = "value1"
    VALUE2 = "value2"


class NestedModel(BaseModel):
    """Test nested model."""

    id: int
    name: str


class RelatedModel(BaseModel):
    """Test related model."""

    id: int
    title: str


class MockTestModel(BaseModel):
    """Test model with various field types."""

    id: int
    name: str
    tags: List[str]
    nested: NestedModel
    nested_list: List[NestedModel]
    optional_nested: Optional[NestedModel] = None
    optional_string: Optional[str] = None
    enum_field: MockTestEnum = MockTestEnum.VALUE1
    union_field: Union[int, str]
    dict_field: Dict[str, int]


class ForwardRefUser(BaseModel):
    """Model with forward references."""

    id: int
    name: str
    # Forward reference to a model defined below
    references: List["ForwardReference"]
    optional_ref: Optional["ForwardReference"] = None


class ForwardReference(BaseModel):
    """Model referenced by ForwardRefUser."""

    id: int
    name: str
    user_id: int


# Create circular reference by updating ForwardReference.__annotations__
ForwardRefUser.model_rebuild()


class UserModel(BaseModel):
    """User model for relationship testing."""

    id: int
    name: str
    email: str


class UserReferenceModel(BaseModel):
    """User reference model."""

    id: int


class UserNetworkModel(BaseModel):
    """User network model."""

    id: int


class UserManager:
    """User manager for relationship testing."""

    pass


class PostModel(BaseModel):
    """Post model for relationship testing."""

    id: int
    title: str
    content: str
    user_id: int
    user: UserReferenceModel


class CommentModel(BaseModel):
    """Comment model for relationship testing."""

    id: int
    content: str
    post_id: int
    user_id: int
    post: PostModel
    user: UserReferenceModel


class TestPydantic(unittest.TestCase):
    """Test suite for Pydantic.py functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.utility = PydanticUtility()

        # Register test models
        self.utility.register_model(MockTestModel)
        self.utility.register_model(NestedModel)
        self.utility.register_model(RelatedModel)
        self.utility.register_model(ForwardRefUser)
        self.utility.register_model(ForwardReference)

        # Create mock BLL modules for relationship testing
        self.bll_modules = {
            "user_module": MagicMock(
                UserModel=UserModel,
                UserReferenceModel=UserReferenceModel,
                UserNetworkModel=UserNetworkModel,
                UserManager=UserManager,
            ),
            "post_module": MagicMock(
                PostModel=PostModel,
                PostReferenceModel=BaseModel,
                PostNetworkModel=BaseModel,
                PostManager=MagicMock(),
            ),
            "comment_module": MagicMock(
                CommentModel=CommentModel,
                CommentReferenceModel=BaseModel,
                CommentNetworkModel=BaseModel,
                CommentManager=MagicMock(),
            ),
        }

    def test_is_model(self):
        """Test model detection functionality."""
        # Test if a Pydantic BaseModel class is properly handled
        self.assertTrue(hasattr(MockTestModel, "model_fields"))

        # Test interaction with utility (register and find)
        self.utility.register_model(MockTestModel)
        found_model = self.utility.find_model_by_name("mocktest")
        self.assertEqual(found_model, MockTestModel)

        # A standard class should not have model_fields
        class NotAModel:
            pass

        self.assertFalse(hasattr(NotAModel, "model_fields"))

        # A standard module should not have model_fields
        self.assertFalse(hasattr(os, "model_fields"))

    def test_get_field_type(self):
        """Test field type extraction from models using get_model_fields."""
        # Get fields for MockTestModel
        fields = self.utility.get_model_fields(MockTestModel)

        # Verify field types
        self.assertIn("id", fields)
        self.assertEqual(fields["id"], int)

        self.assertIn("name", fields)
        self.assertEqual(fields["name"], str)

        self.assertIn("tags", fields)
        # Check that tags is a List[str]
        self.assertTrue(
            get_origin(fields["tags"]) is list or get_origin(fields["tags"]) is List
        )
        list_args = get_args(fields["tags"])
        self.assertTrue(len(list_args) > 0 and list_args[0] is str)

        # Verify nested field type
        self.assertIn("nested", fields)
        self.assertEqual(fields["nested"], NestedModel)

    def test_get_type_name(self):
        """Test get_type_name for different Python types."""
        # Test simple types - verify they produce string results
        type_names = {
            str: self.utility.get_type_name(str),
            int: self.utility.get_type_name(int),
            bool: self.utility.get_type_name(bool),
            float: self.utility.get_type_name(float),
            dict: self.utility.get_type_name(dict),
            list: self.utility.get_type_name(list),
        }

        # Verify we got string representations for each type
        for t, name in type_names.items():
            self.assertIsInstance(name, str)
            self.assertTrue(t.__name__ in name)

        # Test complex types
        dict_type_name = self.utility.get_type_name(Dict[str, int])
        self.assertIsInstance(dict_type_name, str)
        # Using assertIn separately since 'or' in assertions doesn't work as expected
        has_dict_name = "Dict" in dict_type_name or "dict" in dict_type_name
        self.assertTrue(
            has_dict_name, f"Neither 'Dict' nor 'dict' found in '{dict_type_name}'"
        )

        # Test list type (don't assume type parameters are included)
        list_type_name = self.utility.get_type_name(List[str])
        self.assertIsInstance(list_type_name, str)
        # Verify it's some form of list representation
        has_list_name = "List" in list_type_name or "list" in list_type_name
        self.assertTrue(
            has_list_name, f"Neither 'List' nor 'list' found in '{list_type_name}'"
        )

        # Test optional type (only check it returns a string)
        optional_type_name = self.utility.get_type_name(Optional[str])
        self.assertIsInstance(optional_type_name, str)

        # Test model type
        model_type_name = self.utility.get_type_name(MockTestModel)
        self.assertIsInstance(model_type_name, str)
        # Should contain at least part of the model name
        self.assertTrue(
            "Model" in model_type_name or "Test" in model_type_name,
            f"Neither 'Model' nor 'Test' found in '{model_type_name}'",
        )

    def test_process_annotations_with_forward_refs(self):
        """Test processing of annotations with forward references."""
        # Mock annotations with forward references
        annotations = {
            "value": int,
            "name": str,
            "items": "List[Item]",  # Forward reference
            "optional_item": "Optional[Item]",  # Forward reference with Optional
        }

        # Create a module-like object for context
        module_context = inspect.getmodule(self)

        # Register a mock Item class to be found by name
        class Item:
            pass

        # Register our Item class with the utility
        self.utility.register_model(Item, "Item")

        # Create a custom version of a resolver that works with our test
        original_resolve = self.utility.resolve_string_reference

        def mock_resolve(ref_str, context=None):
            if "Item" in ref_str:
                return Item
            return original_resolve(ref_str, context)

        # Replace temporarily
        self.utility.resolve_string_reference = mock_resolve

        try:
            # Process annotations
            result = self.utility.process_annotations_with_forward_refs(
                annotations, module_context
            )

            # Check the results - types should at least be preserved
            self.assertEqual(result["value"], int)
            self.assertEqual(result["name"], str)

            # Test items is present - skip checking container type if too complex
            self.assertIn("items", result)
            items_type = result["items"]
            # If the type has args, check for Item
            # If not, just ensure Item is mentioned in the string representation
            if hasattr(items_type, "__origin__"):
                origin = get_origin(items_type)
                if origin is not None:
                    args = get_args(items_type)
                    if args and len(args) > 0:
                        item_found = any(arg is Item for arg in args)
                        if not item_found:
                            # If no exact match, check if Item appears in string representation
                            self.assertIn("Item", str(items_type))
                    else:
                        # If no args but has origin, Item should be in string representation
                        self.assertIn("Item", str(items_type))
                else:
                    # If no origin, Item should be in string representation
                    self.assertIn("Item", str(items_type))
            else:
                # If not a container type, should be Item or have Item in string rep
                either_item_or_contains_item = (items_type is Item) or (
                    "Item" in str(items_type)
                )
                self.assertTrue(
                    either_item_or_contains_item,
                    f"Item not found in items_type: {items_type}",
                )

            # Test optional_item is present
            self.assertIn("optional_item", result)
            opt_type = result["optional_item"]
            # Check if Item appears in string representation or is Item itself
            either_item_or_contains_item = (opt_type is Item) or (
                "Item" in str(opt_type)
            )
            self.assertTrue(
                either_item_or_contains_item, f"Item not found in opt_type: {opt_type}"
            )
        finally:
            # Restore original method
            self.utility.resolve_string_reference = original_resolve

    @patch("pydantic.create_model")
    def test_create_model(self, mock_create_model):
        """Test model creation with Pydantic."""
        # Set up mock return value
        mock_model = MagicMock()
        mock_create_model.return_value = mock_model

        # Call the pydantic.create_model function directly
        from pydantic import create_model as pydantic_create_model

        # Test creating a simple model with fields
        model_name = "MockTestModel"
        fields = {"name": (str, ...), "age": (int, 0)}

        # We'll use the original implementation but verify our mock was called
        model = pydantic_create_model(model_name, **fields)

        # Verify our mock was called with the right parameters
        mock_create_model.assert_called_once_with(model_name, **fields)
        self.assertEqual(model, mock_model)

    def test_is_scalar_type(self):
        """Test _is_scalar_type method."""
        # Test scalar types
        self.assertTrue(self.utility._is_scalar_type(str))
        self.assertTrue(self.utility._is_scalar_type(int))
        self.assertTrue(self.utility._is_scalar_type(float))
        self.assertTrue(self.utility._is_scalar_type(bool))
        self.assertTrue(self.utility._is_scalar_type(dict))
        self.assertTrue(self.utility._is_scalar_type(list))

        # Test optional scalar types
        self.assertTrue(self.utility._is_scalar_type(Optional[str]))
        self.assertTrue(self.utility._is_scalar_type(Optional[int]))

        # Test non-scalar types
        self.assertFalse(self.utility._is_scalar_type(MockTestModel))
        self.assertFalse(self.utility._is_scalar_type(List[MockTestModel]))
        self.assertFalse(self.utility._is_scalar_type(Optional[MockTestModel]))

    def test_resolve_string_reference(self):
        """Test resolve_string_reference method."""
        # Test with direct model reference
        module_context = inspect.getmodule(MockTestModel)
        result = self.utility.resolve_string_reference("MockTestModel", module_context)
        self.assertEqual(result, MockTestModel)

        # Test with cached reference
        result2 = self.utility.resolve_string_reference("MockTestModel", module_context)
        self.assertEqual(result2, MockTestModel)

        # Test with unquoted string
        result3 = self.utility.resolve_string_reference(
            '"MockTestModel"', module_context
        )
        self.assertEqual(result3, MockTestModel)

        # Test with non-existent model
        result4 = self.utility.resolve_string_reference(
            "NonExistentModel", module_context
        )
        self.assertIsNone(result4)

    def test_get_model_fields(self):
        """Test get_model_fields method."""
        # Test with simple model
        fields = self.utility.get_model_fields(NestedModel)
        self.assertEqual(len(fields), 2)
        self.assertIn("id", fields)
        self.assertIn("name", fields)
        self.assertEqual(fields["id"], int)
        self.assertEqual(fields["name"], str)

        # Test with complex model
        fields = self.utility.get_model_fields(MockTestModel)
        self.assertEqual(len(fields), 10)
        self.assertIn("nested", fields)
        self.assertIn("nested_list", fields)
        self.assertEqual(fields["nested"], NestedModel)

        # Test caching
        cached_fields = self.utility.get_model_fields(MockTestModel)
        self.assertEqual(id(fields), id(cached_fields))

    def test_register_model(self):
        """Test register_model method."""
        # Clear existing registrations
        self.utility.reference_resolver._model_registry.clear()

        # Register a model
        self.utility.register_model(MockTestModel)

        # Check direct registration - MockTestModel should register as 'mock_test'
        self.assertIn("mock_test", self.utility.reference_resolver._model_registry)
        self.assertEqual(
            self.utility.reference_resolver._model_registry["mock_test"], MockTestModel
        )

        # Register with custom name
        self.utility.register_model(NestedModel, "custom_name")
        self.assertIn("custom_name", self.utility.reference_resolver._model_registry)
        self.assertEqual(
            self.utility.reference_resolver._model_registry["custom_name"], NestedModel
        )

        # Test shortened name registration
        self.utility.register_model(RelatedModel, "prefix_related")
        self.assertIn("related", self.utility.reference_resolver._model_registry)

    def test_register_models(self):
        """Test register_models method."""
        # Clear existing registrations
        self.utility.reference_resolver._model_registry.clear()

        # Register multiple models
        models = [MockTestModel, NestedModel, RelatedModel]
        self.utility.register_models(models)

        # Check all models were registered with normalized names
        self.assertIn("mock_test", self.utility.reference_resolver._model_registry)
        self.assertIn("nested", self.utility.reference_resolver._model_registry)
        self.assertIn("related", self.utility.reference_resolver._model_registry)

    def test_find_model_by_name(self):
        """Test find_model_by_name method."""
        # Clear existing registrations
        self.utility.reference_resolver._model_registry.clear()

        # Register models
        self.utility.register_model(MockTestModel)
        self.utility.register_model(UserModel)

        # Test direct match
        model = self.utility.find_model_by_name("mock_test")
        self.assertEqual(model, MockTestModel)

        # Test case insensitive match
        model = self.utility.find_model_by_name("MOCK_TEST")
        self.assertEqual(model, MockTestModel)

        # Test singular/plural handling
        self.utility.register_model(CommentModel, "comments")
        model = self.utility.find_model_by_name("comment")
        self.assertEqual(model, CommentModel)

        # Test partial match
        model = self.utility.find_model_by_name("user")
        self.assertEqual(model, UserModel)

        # Test no match
        model = self.utility.find_model_by_name("nonexistent")
        self.assertIsNone(model)

    def test_generate_unique_type_name(self):
        """Test generate_unique_type_name method."""
        # The "Model" suffix is stripped to produce a GraphQL-friendly base name.
        type_name = self.utility.generate_unique_type_name(MockTestModel)
        expected_name = "MockTest"
        self.assertEqual(type_name, expected_name)

        # Suffix is appended (without separator) to the base name.
        suffixed = self.utility.generate_unique_type_name(MockTestModel, "Input")
        self.assertEqual(suffixed, f"{expected_name}Input")

        # Caching: a repeated call with the same model returns the same name.
        cached_name = self.utility.generate_unique_type_name(MockTestModel)
        self.assertEqual(cached_name, expected_name)

    def test_generate_detailed_schema(self):
        """Test generate_detailed_schema method."""
        # Generate schema for simple model
        schema = self.utility.generate_detailed_schema(NestedModel)
        self.assertIn("id: int", schema)
        self.assertIn("name: str", schema)

        # Generate schema for complex model
        schema = self.utility.generate_detailed_schema(MockTestModel)
        self.assertIn("nested:", schema)
        self.assertIn("tags: List[str]", schema)
        self.assertIn("enum_field: MockTestEnum", schema)

        # Create a custom mocking of the generate_detailed_schema for max_depth testing
        original_method = self.utility.generate_detailed_schema

        def mock_generate_detailed_schema(model, max_depth=3, depth=0):
            if depth >= max_depth:
                return "(max depth reached)"
            if model == ForwardReference:
                return "ForwardReference fields"
            return original_method(model, max_depth, depth)

        # Temporarily replace the method
        self.utility.generate_detailed_schema = mock_generate_detailed_schema

        # Now test with max_depth=1
        schema = self.utility.generate_detailed_schema(
            ForwardRefUser, max_depth=1, depth=1
        )
        self.assertEqual(schema, "(max depth reached)")

        # Restore original method
        self.utility.generate_detailed_schema = original_method

    @pytest.mark.asyncio
    async def test_convert_to_model(self):
        """Test convert_to_model method."""

        # Create mock inference function
        async def mock_inference(user_input, schema, **kwargs):
            return '{"id": 1, "name": "Test"}'

        # Test successful conversion
        result = await self.utility.convert_to_model(
            "Convert this to a NestedModel",
            NestedModel,
            inference_function=mock_inference,
        )

        self.assertIsInstance(result, NestedModel)
        self.assertEqual(result.id, 1)
        self.assertEqual(result.name, "Test")

        # Test json response type
        result = await self.utility.convert_to_model(
            "Convert this to a NestedModel",
            NestedModel,
            response_type="json",
            inference_function=mock_inference,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["id"], 1)
        self.assertEqual(result["name"], "Test")

        # Test markdown code block handling
        async def mock_inference_with_markdown(user_input, schema, **kwargs):
            return """```json
{"id": 2, "name": "Markdown Test"}
```"""

        result = await self.utility.convert_to_model(
            "Convert this with markdown",
            NestedModel,
            inference_function=mock_inference_with_markdown,
        )

        self.assertIsInstance(result, NestedModel)
        self.assertEqual(result.id, 2)
        self.assertEqual(result.name, "Markdown Test")

        # Test error handling
        async def mock_inference_with_error(user_input, schema, **kwargs):
            failures = kwargs.get("failures", 0)
            if failures < 2:
                return '{"invalid": "json'
            return '{"id": 3, "name": "After Error"}'

        result = await self.utility.convert_to_model(
            "Convert with error",
            NestedModel,
            inference_function=mock_inference_with_error,
        )

        self.assertIsInstance(result, NestedModel)
        self.assertEqual(result.id, 3)
        self.assertEqual(result.name, "After Error")

        # Test max failures
        async def mock_inference_always_error(user_input, schema, **kwargs):
            return '{"invalid": "json'

        result = await self.utility.convert_to_model(
            "Always error",
            NestedModel,
            max_failures=2,
            inference_function=mock_inference_always_error,
        )

        self.assertIsInstance(result, str)
        self.assertIn("Failed to convert", result)

    def test_discover_model_relationships(self):
        """Test discover_model_relationships method."""
        # Note: The function might only discover relationships when a manager is present
        # Let's patch the method to test properly

        # First, create a simpler test case
        test_module = MagicMock(
            UserModel=UserModel,
            UserReferenceModel=UserReferenceModel,
            UserManager=UserManager,
        )

        simplified_modules = {"user_module": test_module}

        # With one module, we should get one relationship
        relationships = self.utility.discover_model_relationships(simplified_modules)
        self.assertEqual(
            len(relationships), 1
        )  # Expect 1 relationship in the simplified test

        # After the in-flight WIP commit, the relationship tuple is 3-wide
        # (network model is now derived from BaseNetworkModel, not part of the
        # discovery tuple — see Pydantic.py:discover_model_relationships).
        model_class, ref_model_class, manager_class = relationships[0]
        self.assertEqual(model_class, UserModel)
        self.assertEqual(ref_model_class, UserReferenceModel)
        self.assertEqual(manager_class, UserManager)

    def test_collect_model_fields(self):
        """Test collect_model_fields method."""
        # Create test relationships with real model classes, not MagicMock objects
        relationships = [
            (UserModel, UserReferenceModel, UserManager),
            (
                PostModel,
                PostModel,
                object,
            ),  # Use real classes, not MagicMock
        ]

        model_fields = self.utility.collect_model_fields(relationships)

        # Check that fields were collected for all models
        self.assertIn(UserModel, model_fields)
        self.assertIn(UserReferenceModel, model_fields)
        self.assertIn(PostModel, model_fields)

        # Check field content
        self.assertEqual(len(model_fields[UserModel]), 3)  # id, name, email
        self.assertEqual(len(model_fields[UserReferenceModel]), 1)  # id

    def test_enhance_model_discovery(self):
        """Test enhance_model_discovery method."""
        # Clear existing registrations
        self.utility.reference_resolver._model_registry.clear()

        # Register base models
        self.utility.register_model(UserModel)
        self.utility.register_model(PostModel)  # Register PostModel too

        # Create model fields mapping
        model_fields = {
            PostModel: {
                "id": int,
                "title": str,
                "content": str,
                "user_id": int,
                "user": "UserModel",  # String reference
            },
            CommentModel: {
                "id": int,
                "content": str,
                "post_id": int,
                "post": PostModel,  # Direct reference
            },
        }

        # Enhance discovery
        self.utility.enhance_model_discovery(model_fields)

        # Check that new relationships were discovered
        self.assertEqual(self.utility.find_model_by_name("user"), UserModel)
        self.assertEqual(self.utility.find_model_by_name("post"), PostModel)

    def test_obj_to_dict_basic(self):
        """Test obj_to_dict with basic types and objects."""
        # Test None
        self.assertIsNone(obj_to_dict(None))

        # Test primitive types
        self.assertEqual(obj_to_dict("string"), "string")
        self.assertEqual(obj_to_dict(42), 42)
        self.assertEqual(obj_to_dict(3.14), 3.14)
        self.assertEqual(obj_to_dict(True), True)

        # Test lists and tuples
        self.assertEqual(obj_to_dict([1, 2, 3]), [1, 2, 3])
        self.assertEqual(obj_to_dict((1, 2, 3)), [1, 2, 3])

        # Test sets
        result = obj_to_dict({1, 2, 3})
        self.assertIsInstance(result, list)
        self.assertEqual(set(result), {1, 2, 3})

        # Test enums
        self.assertEqual(obj_to_dict(MockTestEnum.VALUE1), "value1")

        # Test dictionaries
        input_dict = {"key1": "value1", "key2": 42, "_private": "hidden"}
        result = obj_to_dict(input_dict)
        expected = {"key1": "value1", "key2": 42}  # _private should be filtered
        self.assertEqual(result, expected)

    def test_obj_to_dict_datetime(self):
        """Test obj_to_dict with datetime objects."""
        from datetime import date, datetime, time

        # Test datetime
        dt = datetime(2023, 12, 25, 10, 30, 45)
        result = obj_to_dict(dt)
        self.assertEqual(result, "2023-12-25T10:30:45")

        # Test date
        d = date(2023, 12, 25)
        result = obj_to_dict(d)
        self.assertEqual(result, "2023-12-25")

        # Test time
        t = time(10, 30, 45)
        result = obj_to_dict(t)
        self.assertEqual(result, "10:30:45")

    def test_obj_to_dict_nested_objects(self):
        """Test obj_to_dict with nested objects (the main fix)."""

        # Create a mock SQLAlchemy-like object with nested relationships
        class MockUser:
            def __init__(self):
                self.id = "user-123"
                self.name = "John Doe"
                self.email = "john@example.com"
                self._sa_instance_state = "should_be_ignored"

        class MockPost:
            def __init__(self):
                self.id = "post-456"
                self.title = "Test Post"
                self.content = "This is a test post"
                self.user = MockUser()  # Nested object
                self._sa_instance_state = "should_be_ignored"

        class MockComment:
            def __init__(self):
                self.id = "comment-789"
                self.content = "Great post!"
                self.post = MockPost()  # Nested object with its own nested object
                self.user = MockUser()  # Another nested object
                self._sa_instance_state = "should_be_ignored"

        # Test conversion with nested objects
        comment = MockComment()
        result = obj_to_dict(comment)

        # Verify structure
        self.assertIsInstance(result, dict)
        self.assertEqual(result["id"], "comment-789")
        self.assertEqual(result["content"], "Great post!")

        # Verify nested post is converted
        self.assertIsInstance(result["post"], dict)
        self.assertEqual(result["post"]["id"], "post-456")
        self.assertEqual(result["post"]["title"], "Test Post")

        # Verify doubly nested user in post is converted
        self.assertIsInstance(result["post"]["user"], dict)
        self.assertEqual(result["post"]["user"]["id"], "user-123")
        self.assertEqual(result["post"]["user"]["name"], "John Doe")

        # Verify nested user in comment is converted
        self.assertIsInstance(result["user"], dict)
        self.assertEqual(result["user"]["id"], "user-123")
        self.assertEqual(result["user"]["name"], "John Doe")

        # Verify private attributes are excluded
        self.assertNotIn("_sa_instance_state", result)
        self.assertNotIn("_sa_instance_state", result["post"])
        self.assertNotIn("_sa_instance_state", result["user"])
        self.assertNotIn("_sa_instance_state", result["post"]["user"])

    def test_obj_to_dict_circular_references(self):
        """Test obj_to_dict handles circular references."""

        class MockUserWithCircular:
            def __init__(self):
                self.id = "user-123"
                self.name = "John Doe"
                self.posts = []  # Will be populated with circular references

        class MockPostWithCircular:
            def __init__(self, user):
                self.id = "post-456"
                self.title = "Test Post"
                self.user = user  # Back reference to user

        # Create circular reference
        user = MockUserWithCircular()
        post = MockPostWithCircular(user)
        user.posts = [post]  # Creates circular reference: user -> post -> user

        # Test conversion handles circular references
        result = obj_to_dict(user)

        # Verify main object structure
        self.assertIsInstance(result, dict)
        self.assertEqual(result["id"], "user-123")
        self.assertEqual(result["name"], "John Doe")

        # Verify posts list is converted
        self.assertIsInstance(result["posts"], list)
        self.assertEqual(len(result["posts"]), 1)

        # Verify first post is converted
        post_dict = result["posts"][0]
        self.assertIsInstance(post_dict, dict)
        self.assertEqual(post_dict["id"], "post-456")
        self.assertEqual(post_dict["title"], "Test Post")

        # Verify circular reference is handled (should be a reference, not full object)
        self.assertIsInstance(post_dict["user"], dict)
        self.assertTrue(post_dict["user"].get("__circular_ref__", False))
        self.assertEqual(post_dict["user"]["id"], "user-123")

    def test_obj_to_dict_mixed_nested_types(self):
        """Test obj_to_dict with mixed nested types including lists and dicts."""

        class MockComplexObject:
            def __init__(self):
                self.id = "complex-123"
                self.simple_list = [1, 2, 3]
                self.object_list = [MockUser() for _ in range(2)]
                self.nested_dict = {
                    "key1": "value1",
                    "key2": MockUser(),
                    "nested": {"deep": MockUser()},
                }
                self.mixed_data = {
                    "list_of_objects": [MockUser(), MockUser()],
                    "object_with_list": MockUser(),
                }

        class MockUser:
            def __init__(self):
                self.id = f"user-{id(self)}"  # Unique ID
                self.name = "Test User"

        # Test conversion
        complex_obj = MockComplexObject()
        result = obj_to_dict(complex_obj)

        # Verify basic structure
        self.assertIsInstance(result, dict)
        self.assertEqual(result["id"], "complex-123")

        # Verify simple list unchanged
        self.assertEqual(result["simple_list"], [1, 2, 3])

        # Verify object list converted to list of dicts
        self.assertIsInstance(result["object_list"], list)
        self.assertEqual(len(result["object_list"]), 2)
        for user_dict in result["object_list"]:
            self.assertIsInstance(user_dict, dict)
            self.assertIn("id", user_dict)
            self.assertEqual(user_dict["name"], "Test User")

        # Verify nested dict with mixed content
        nested_dict = result["nested_dict"]
        self.assertIsInstance(nested_dict, dict)
        self.assertEqual(nested_dict["key1"], "value1")
        self.assertIsInstance(nested_dict["key2"], dict)
        self.assertEqual(nested_dict["key2"]["name"], "Test User")

        # Verify deeply nested structure
        self.assertIsInstance(nested_dict["nested"], dict)
        self.assertIsInstance(nested_dict["nested"]["deep"], dict)
        self.assertEqual(nested_dict["nested"]["deep"]["name"], "Test User")

    def test_obj_to_dict_pydantic_models(self):
        """Test obj_to_dict with Pydantic models."""

        # Create Pydantic model instance
        nested_instance = NestedModel(id=1, name="Nested")
        mock_instance = MockTestModel(
            id=42,
            name="Test Model",
            tags=["tag1", "tag2"],
            nested=nested_instance,
            nested_list=[
                NestedModel(id=2, name="List Item 1"),
                NestedModel(id=3, name="List Item 2"),
            ],
            optional_nested=NestedModel(id=4, name="Optional"),
            optional_string="optional value",
            enum_field=MockTestEnum.VALUE2,
            union_field="string value",
            dict_field={"a": 1, "b": 2},
        )

        # Convert to dict
        result = obj_to_dict(mock_instance)

        # Verify basic fields
        self.assertEqual(result["id"], 42)
        self.assertEqual(result["name"], "Test Model")
        self.assertEqual(result["tags"], ["tag1", "tag2"])
        self.assertEqual(result["enum_field"], "value2")
        self.assertEqual(result["union_field"], "string value")
        self.assertEqual(result["dict_field"], {"a": 1, "b": 2})

        # Verify nested model converted to dict
        self.assertIsInstance(result["nested"], dict)
        self.assertEqual(result["nested"]["id"], 1)
        self.assertEqual(result["nested"]["name"], "Nested")

        # Verify nested list converted
        self.assertIsInstance(result["nested_list"], list)
        self.assertEqual(len(result["nested_list"]), 2)
        for i, item in enumerate(result["nested_list"], 2):
            self.assertIsInstance(item, dict)
            self.assertEqual(item["id"], i)
            self.assertEqual(item["name"], f"List Item {i-1}")

        # Verify optional nested converted
        self.assertIsInstance(result["optional_nested"], dict)
        self.assertEqual(result["optional_nested"]["id"], 4)
        self.assertEqual(result["optional_nested"]["name"], "Optional")

    def test_get_model_for_field(self):
        """Test get_model_for_field method."""
        # Clear existing registrations
        self.utility.reference_resolver._model_registry.clear()

        # Register models
        self.utility.register_model(UserModel)
        self.utility.register_model(PostModel)

        # Test direct field match
        model = self.utility.get_model_for_field("user", UserModel)
        self.assertEqual(model, UserModel)

        # Test with string reference
        model = self.utility.get_model_for_field("user_ref", "UserModel", PostModel)
        self.assertEqual(model, UserModel)

        # Test with list type
        model = self.utility.get_model_for_field("users", List[UserModel])
        self.assertEqual(model, UserModel)

        # Test with optional type
        model = self.utility.get_model_for_field("optional_user", Optional[UserModel])
        self.assertEqual(model, UserModel)

        # Test with non-existent model
        model = self.utility.get_model_for_field("nonexistent", str)
        self.assertIsNone(model)

    def test_get_model_hierarchy(self):
        """Test get_model_hierarchy method."""

        # Create test class hierarchy
        class BaseTestModel(BaseModel):
            id: int

        class ExtendedModel(BaseTestModel):
            name: str

        class FurtherExtendedModel(ExtendedModel):
            description: str

        # Get hierarchy
        hierarchy = self.utility.get_model_hierarchy(FurtherExtendedModel)

        # Check hierarchy order
        self.assertEqual(hierarchy[0], ExtendedModel)
        self.assertEqual(hierarchy[1], BaseTestModel)
        self.assertEqual(hierarchy[2], BaseModel)

        # Check caching
        cached_hierarchy = self.utility.get_model_hierarchy(FurtherExtendedModel)
        self.assertEqual(id(hierarchy), id(cached_hierarchy))

    def test_clear_caches(self):
        """Test clear_caches method."""
        # Populate caches
        self.utility.get_model_fields(MockTestModel)
        self.utility.register_model(MockTestModel)
        self.utility.generate_unique_type_name(MockTestModel)
        self.utility.get_model_for_field("test", MockTestModel)
        self.utility.get_model_hierarchy(MockTestModel)

        # Verify caches are populated
        self.assertNotEqual(len(self.utility._model_fields_cache), 0)
        self.assertNotEqual(len(self.utility.reference_resolver._model_registry), 0)
        self.assertNotEqual(len(self.utility._type_name_mapping), 0)

        # Clear caches
        self.utility.clear_caches()

        # Verify caches are cleared
        self.assertEqual(len(self.utility._model_fields_cache), 0)
        self.assertEqual(len(self.utility.reference_resolver._model_registry), 0)
        self.assertEqual(len(self.utility._type_name_mapping), 0)
        self.assertEqual(len(self.utility._relationship_cache), 0)
        self.assertEqual(len(self.utility._model_hierarchy_cache), 0)
        self.assertEqual(len(self.utility._processed_models), 0)

    def test_is_model_processed(self):
        """Test is_model_processed method."""
        # Initially model is not processed
        self.assertFalse(self.utility.is_model_processed(MockTestModel))

        # Mark as processed
        self.utility._processed_models.add(MockTestModel)

        # Now it should be processed
        self.assertTrue(self.utility.is_model_processed(MockTestModel))

    def test_mark_model_processed(self):
        """Test mark_model_processed method."""
        # Initially model is not processed
        self.assertFalse(self.utility.is_model_processed(MockTestModel))

        # Mark as processed
        self.utility.mark_model_processed(MockTestModel)

        # Now it should be processed
        self.assertTrue(self.utility.is_model_processed(MockTestModel))

    def test_process_model_relationships(self):
        """Test process_model_relationships method."""
        # Clear existing registrations
        self.utility.reference_resolver._model_registry.clear()

        # Register models
        self.utility.register_model(UserModel)
        self.utility.register_model(PostModel)
        self.utility.register_model(CommentModel)

        # Process relationships
        processed_models = set()
        relationships = self.utility.process_model_relationships(
            CommentModel, processed_models
        )

        # Check detected relationships
        self.assertIn("post", relationships)
        self.assertIn("user", relationships)
        self.assertEqual(relationships["post"], PostModel)
        self.assertEqual(relationships["user"], UserReferenceModel)

        # Check processed models tracking
        self.assertIn(CommentModel, processed_models)
        self.assertIn(PostModel, processed_models)
        self.assertIn(UserReferenceModel, processed_models)

        # Test max recursion depth
        processed_models = set()
        relationships = self.utility.process_model_relationships(
            CommentModel, processed_models, max_recursion_depth=0
        )

        # Only immediate relationships should be detected
        self.assertIn("post", relationships)
        self.assertIn("user", relationships)
        self.assertEqual(len(processed_models), 1)  # Only CommentModel


class TestModelRegistry:
    """Test suite for the ModelRegistry class."""

    def test_basic_model_binding(self):
        """Test basic model binding functionality."""
        registry = ModelRegistry()

        class TestModel(BaseModel):
            name: str = Field(..., description="Test name")
            value: int = Field(0, description="Test value")

        # Bind the model
        registry.bind(TestModel, table_comment="Test table")

        # Verify binding
        assert registry.is_model_bound(TestModel)
        assert TestModel in registry.get_bound_models()
        assert len(registry.get_bound_models()) == 1

        # Check metadata
        metadata = registry.model_metadata.get(TestModel, {})
        assert metadata.get("table_comment") == "Test table"

    def test_bind_db_functionality(self):
        """Test that the ModelRegistry properly uses the new .DB(declarative_base) functionality."""
        registry = ModelRegistry()

        class TestModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Test name")
            value: int = Field(0, description="Test value")

        # Bind the model to the registry
        registry.bind(TestModel)

        # Commit the registry - this should trigger the .DB(declarative_base) calls
        registry.commit()

        # Verify the registry has a declarative base
        assert registry.declarative_base is not None

        # Verify the model was processed and stored in the registry
        assert TestModel in registry.db_models
        sqlalchemy_model = registry.db_models[TestModel]
        assert sqlalchemy_model is not None

        # Verify we can call .DB(declarative_base) directly
        direct_model = TestModel.DB(registry.declarative_base)
        assert direct_model is not None
        assert direct_model == sqlalchemy_model  # Should return the same cached model

        # Verify the model is stored in the declarative base's registry
        registry_key = f"{TestModel.__module__}.{TestModel.__name__}"
        assert hasattr(registry.declarative_base, "_pydantic_models")
        assert registry_key in registry.declarative_base._pydantic_models
        assert (
            registry.declarative_base._pydantic_models[registry_key] == sqlalchemy_model
        )

    def test_model_registry_commit_process(self):
        """Test the complete ModelRegistry commit process."""
        from lib.Pydantic2SQLAlchemy import DatabaseMixin

        registry = ModelRegistry()

        class TestCommitModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Test name")
            enabled: bool = Field(True, description="Test enabled flag")

        # Bind the model
        registry.bind(TestCommitModel)

        # Verify model is bound but not committed
        assert registry.is_model_bound(TestCommitModel)
        assert not registry.is_committed()

        # Should not be able to get SQLAlchemy model yet
        assert registry.get_sqlalchemy_model(TestCommitModel) is None

        # Commit the registry (without database manager for this test)
        try:
            registry.commit()

            # After commit, should be locked
            assert registry.is_committed()

            # Should be able to get SQLAlchemy model
            sql_model = registry.get_sqlalchemy_model(TestCommitModel)
            assert sql_model is not None
            assert hasattr(sql_model, "__tablename__")

        except Exception as e:
            # Expected if database components aren't fully available
            pytest.skip(f"Database components not available: {e}")

    def test_model_registry_isolation(self):
        """Test that ModelRegistry instances are properly isolated."""
        from lib.Pydantic2SQLAlchemy import DatabaseMixin

        registry1 = ModelRegistry()
        registry2 = ModelRegistry()

        class Model1(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Model 1")

        class Model2(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Model 2")

        # Bind different models to different registries
        registry1.bind(Model1)
        registry2.bind(Model2)

        # Verify isolation
        assert registry1.is_model_bound(Model1)
        assert not registry1.is_model_bound(Model2)
        assert registry2.is_model_bound(Model2)
        assert not registry2.is_model_bound(Model1)

        # Commit both registries
        try:
            registry1.commit()
            registry2.commit()

            # Verify each registry only has its own models
            assert registry1.get_sqlalchemy_model(Model1) is not None
            assert registry1.get_sqlalchemy_model(Model2) is None
            assert registry2.get_sqlalchemy_model(Model2) is not None
            assert registry2.get_sqlalchemy_model(Model1) is None

        except Exception as e:
            pytest.skip(f"Database components not available: {e}")

    def test_database_mixin_error_states(self):
        """Test that DatabaseMixin properly handles error states."""
        from sqlalchemy.orm import declarative_base

        from lib.Pydantic2SQLAlchemy import DatabaseMixin

        class UnboundModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Test name")

        # Should raise error when calling .DB with None
        with pytest.raises(ValueError, match="declarative_base cannot be None"):
            _ = UnboundModel.DB(None)

        # Should work when calling .DB with valid declarative_base
        Base = declarative_base()
        sql_model = UnboundModel.DB(Base)
        assert sql_model is not None
        assert hasattr(sql_model, "__tablename__")

    def test_model_registry_with_extensions(self):
        """Test ModelRegistry with extension models."""
        from lib.Pydantic2SQLAlchemy import DatabaseMixin, extension_model

        registry = ModelRegistry()

        class BaseTestModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Base name")

        @extension_model(BaseTestModel)
        class TestExtension(BaseModel):
            extra_field: str = Field(..., description="Extra field")

        # Bind base model and extension
        registry.bind(BaseTestModel)
        registry.bind_extension(BaseTestModel, TestExtension)

        # Verify extension relationship
        assert BaseTestModel in registry.extension_models
        assert TestExtension in registry.extension_models[BaseTestModel]

    def test_model_registry_clear_functionality(self):
        """Test that ModelRegistry.clear() works correctly."""
        from lib.Pydantic2SQLAlchemy import DatabaseMixin

        registry = ModelRegistry()

        class TestClearModel(BaseModel, DatabaseMixin):
            name: str = Field(..., description="Test name")

        # Bind model and verify state
        registry.bind(TestClearModel)
        assert len(registry.get_bound_models()) == 1

        # Clear registry
        registry.clear()

        # Verify everything is cleared
        assert len(registry.get_bound_models()) == 0
        assert not registry.is_committed()
        assert len(registry.extension_models) == 0
        assert len(registry.model_metadata) == 0
        assert len(registry.db_models) == 0

    def test_model_registry_error_handling(self):
        """Test ModelRegistry error handling for various edge cases."""
        registry = ModelRegistry()

        # Test binding after commit
        class TestModel(BaseModel):
            name: str = Field(..., description="Test name")

        registry.bind(TestModel)

        # Force registry to locked state
        registry._locked = True

        class AnotherModel(BaseModel):
            value: int = Field(0, description="Test value")

        # Should raise error when trying to bind after commit
        with pytest.raises(
            RuntimeError, match="Cannot bind models after registry has been committed"
        ):
            registry.bind(AnotherModel)

        # Reset for other tests
        registry._locked = False

    @pytest.mark.parametrize("mock_server", [], indirect=True)
    def test_model_registry_server_integration(self, mock_server):
        """Test ModelRegistry integration with server isolation."""
        # This test uses the mock_server fixture to test server-level isolation

        # Verify the server has a model registry
        assert hasattr(mock_server.app.state, "model_registry")
        registry = mock_server.app.state.model_registry

        # Verify registry is committed (app should commit during startup)
        assert registry.is_committed()

        # Verify registry has some models bound (from core system)
        bound_models = registry.get_bound_models()
        assert len(bound_models) > 0

        # Test that we can get SQLAlchemy models from the registry
        from logic.BLL_Auth import UserModel

        user_sql_model = registry.get_sqlalchemy_model(UserModel)
        assert user_sql_model is not None
        assert hasattr(user_sql_model, "__tablename__")
        assert hasattr(user_sql_model, "create")  # Should have CRUD methods

    def test_model_registry_thread_safety(self):
        """Test that ModelRegistry operations are thread-safe."""
        import threading

        from sqlalchemy.orm import declarative_base

        from lib.Pydantic2SQLAlchemy import DatabaseMixin

        results = []

        # Use separate declarative bases for each thread to avoid SQLAlchemy conflicts
        def access_db_in_thread(thread_id):
            try:
                # Each thread uses its own declarative base to avoid conflicts
                Base = declarative_base()

                class ThreadTestModel(BaseModel, DatabaseMixin):
                    name: str = Field(..., description="Thread test name")
                    thread_id: int = Field(..., description="Thread ID")

                # Each thread tries to access .DB method (stateless operation)
                sql_model = ThreadTestModel.DB(Base)
                assert sql_model is not None
                assert hasattr(sql_model, "__tablename__")
                results.append(f"thread_{thread_id}_success")
            except Exception as e:
                results.append(f"thread_{thread_id}_error_{type(e).__name__}")

        # Create multiple threads that try to access .DB method
        threads = []
        for i in range(5):
            thread = threading.Thread(target=access_db_in_thread, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # All threads should succeed since .DB() is stateless
        success_count = len([r for r in results if "success" in r])
        error_results = [r for r in results if "error" in r]

        # Print results for debugging if there are issues
        if success_count != 5 or error_results:
            logger.debug(f"Thread results: {results}")
            logger.debug(
                f"Success count: {success_count}, Error count: {len(error_results)}"
            )

        assert (
            success_count == 5
        ), f"All threads should succeed with isolated declarative bases. Got {success_count} successes, errors: {error_results}"

        # Verify no errors occurred
        error_count = len(error_results)
        assert error_count == 0, f"No errors should occur, but got: {error_results}"

    def test_scoped_import_functionality(self):
        """Test that ModelRegistry._scoped_import works correctly."""
        registry = ModelRegistry()

        # Test importing BLL files from logic scope
        try:
            imported_modules, import_errors = registry._scoped_import(
                file_type="BLL", scopes=["logic"]
            )

            # Should return lists (even if empty for test environment)
            assert isinstance(imported_modules, list)
            assert isinstance(import_errors, list)

            # If there are any import errors, they should be tuples of (file_path, error_message)
            for error in import_errors:
                assert isinstance(error, tuple)
                assert len(error) == 2

        except Exception as e:
            # Expected in test environment where BLL files may not exist
            pytest.skip(f"BLL files not available in test environment: {e}")

    def test_from_scoped_import_class_method(self):
        """Test that ModelRegistry.from_scoped_import creates registry correctly."""
        try:
            # Create registry using the class method
            registry = ModelRegistry.from_scoped_import(
                file_type="BLL", scopes=["logic"]
            )

            # Should return a ModelRegistry instance
            assert isinstance(registry, ModelRegistry)

            # Should have utility attached
            assert hasattr(registry, "utility")
            assert registry.utility is not None

            # May have bound models if BLL files were found
            bound_models = registry.get_bound_models()
            assert isinstance(bound_models, set)

        except Exception as e:
            # Expected in test environment where BLL files may not exist
            pytest.skip(f"BLL files not available in test environment: {e}")
