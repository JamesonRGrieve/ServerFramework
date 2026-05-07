from unittest.mock import MagicMock, patch

import pytest

from extensions.meta_labels.EXT_Meta_Labels import EXT_Meta_Labels


class TestMetaLabelsExtension:
    """Test cases for Meta Labels Extension."""

    @pytest.fixture
    def extension(self):
        """Create a MetaLabelsExtension instance for testing."""
        return EXT_Meta_Labels()

    def test_extension_metadata(self, extension):
        """Test extension metadata is correctly set."""
        assert extension.name == "meta_labels"
        assert extension.version == "1.0.0"
        assert "metadata" in extension.description.lower()
        assert "labeling" in extension.description.lower()

    def test_dependencies(self, extension):
        """Test extension dependencies are properly defined."""
        # Check extension dependencies
        ext_deps = {dep.name for dep in extension.ext_dependencies}
        assert "core" in ext_deps
        assert "database" in ext_deps

        # Check pip dependencies
        pip_deps = {dep.name for dep in extension.pip_dependencies}
        assert "pydantic" in pip_deps
        assert "sqlalchemy" in pip_deps

        # Check sys dependencies
        assert isinstance(extension.sys_dependencies, list)

    def test_capabilities(self, extension):
        """Test extension capabilities are properly defined."""
        expected_capabilities = {
            "label_management",
            "metadata_storage",
            "resource_categorization",
            "tag_management",
            "hierarchical_labeling",
            "label_search",
            "metadata_validation",
        }
        assert set(extension.capabilities) == expected_capabilities

    def test_initialization(self, extension):
        """Test extension initialization."""
        assert hasattr(extension, "bll_managers")
        assert hasattr(extension, "commands")
        assert isinstance(extension.bll_managers, dict)
        assert isinstance(extension.commands, dict)

    @patch("extensions.meta_labels.EXT_Meta_Labels.logger")
    def test_on_initialize_success(self, mock_logger, extension):
        """Test successful extension initialization."""
        with patch.object(extension, "_register_managers"), patch.object(
            extension, "_register_commands"
        ), patch.object(extension, "register_capability"):
            result = extension.on_initialize()
            assert result is True
            mock_logger.debug.assert_called()

    @patch("extensions.meta_labels.EXT_Meta_Labels.logger")
    def test_on_initialize_failure(self, mock_logger, extension):
        """Test extension initialization failure."""
        with patch.object(
            extension, "_register_managers", side_effect=Exception("Test error")
        ):
            result = extension.on_initialize()
            assert result is False
            mock_logger.error.assert_called()

    def test_register_managers(self, extension):
        """Test manager registration."""
        with patch("builtins.__import__") as mock_import:
            mock_module = MagicMock()
            mock_import.return_value = mock_module

            extension._register_managers()

            assert mock_import.called

    def test_register_commands(self, extension):
        """Test command registration."""
        extension._register_commands()

        expected_commands = {
            "create_label",
            "update_label",
            "delete_label",
            "apply_label",
            "remove_label",
            "search_labels",
            "get_resource_labels",
            "create_metadata",
            "update_metadata",
            "get_metadata",
        }
        assert set(extension.commands.keys()) == expected_commands

    def test_capability_management(self, extension):
        """Test capability management methods."""
        # Test register_capability
        extension.register_capability("test_capability")
        assert "test_capability" in extension.capabilities

        # Test get_registered_capabilities
        capabilities = extension.get_registered_capabilities()
        assert isinstance(capabilities, set)
        assert "test_capability" in capabilities

        # Test get_capabilities
        capabilities = extension.get_capabilities()
        assert isinstance(capabilities, set)

        # Test has_capability
        assert extension.has_capability("test_capability") is True
        assert extension.has_capability("nonexistent_capability") is False

    @pytest.mark.asyncio
    async def test_create_label_success(self, extension):
        """Test successful label creation."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.create_label(
            name="Test Label",
            description="Test Description",
            color="#FF0000",
            metadata={"key": "value"},
        )

        assert result["success"] is True
        assert result["label"]["name"] == "Test Label"
        assert result["label"]["color"] == "#FF0000"
        assert "Test Label" in result["message"]

    @pytest.mark.asyncio
    async def test_create_label_no_manager(self, extension):
        """Test label creation without manager."""
        result = await extension.create_label(name="Test Label")

        assert result["success"] is False
        assert "LabelManager not available" in result["message"]

    @pytest.mark.asyncio
    async def test_update_label_success(self, extension):
        """Test successful label update."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.update_label(
            label_id="test_id", name="Updated Label", color="#00FF00"
        )

        assert result["success"] is True
        assert result["label_id"] == "test_id"
        assert result["updates"]["name"] == "Updated Label"
        assert result["updates"]["color"] == "#00FF00"

    @pytest.mark.asyncio
    async def test_delete_label_success(self, extension):
        """Test successful label deletion."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.delete_label(label_id="test_id")

        assert result["success"] is True
        assert result["label_id"] == "test_id"
        assert "deleted successfully" in result["message"]

    @pytest.mark.asyncio
    async def test_apply_label_success(self, extension):
        """Test successful label application."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.apply_label(
            label_id="label_1", resource_type="document", resource_id="doc_1"
        )

        assert result["success"] is True
        assert result["label_id"] == "label_1"
        assert result["resource_type"] == "document"
        assert result["resource_id"] == "doc_1"

    @pytest.mark.asyncio
    async def test_remove_label_success(self, extension):
        """Test successful label removal."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.remove_label(
            label_id="label_1", resource_type="document", resource_id="doc_1"
        )

        assert result["success"] is True
        assert result["label_id"] == "label_1"
        assert result["resource_type"] == "document"
        assert result["resource_id"] == "doc_1"

    @pytest.mark.asyncio
    async def test_search_labels_success(self, extension):
        """Test successful label search."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.search_labels(query="Important", limit=10)

        assert result["success"] is True
        assert "labels" in result
        assert "count" in result
        assert result["query"] == "Important"
        assert isinstance(result["labels"], list)

    @pytest.mark.asyncio
    async def test_get_resource_labels_success(self, extension):
        """Test successful resource labels retrieval."""
        extension.bll_managers["LabelManager"] = MagicMock()

        result = await extension.get_resource_labels(
            resource_type="document", resource_id="doc_1"
        )

        assert result["success"] is True
        assert result["resource_type"] == "document"
        assert result["resource_id"] == "doc_1"
        assert "labels" in result
        assert "count" in result

    @pytest.mark.asyncio
    async def test_create_metadata_success(self, extension):
        """Test successful metadata creation."""
        extension.bll_managers["MetadataManager"] = MagicMock()

        metadata = {"author": "John Doe", "version": "1.0"}
        result = await extension.create_metadata(
            resource_type="document", resource_id="doc_1", metadata=metadata
        )

        assert result["success"] is True
        assert result["resource_type"] == "document"
        assert result["resource_id"] == "doc_1"
        assert result["metadata"] == metadata
        assert "created successfully" in result["message"]

    @pytest.mark.asyncio
    async def test_update_metadata_success(self, extension):
        """Test successful metadata update."""
        extension.bll_managers["MetadataManager"] = MagicMock()

        metadata = {"version": "2.0"}
        result = await extension.update_metadata(
            resource_type="document", resource_id="doc_1", metadata=metadata, merge=True
        )

        assert result["success"] is True
        assert result["resource_type"] == "document"
        assert result["resource_id"] == "doc_1"
        assert result["metadata"] == metadata
        assert result["merge"] is True

    @pytest.mark.asyncio
    async def test_get_metadata_success(self, extension):
        """Test successful metadata retrieval."""
        extension.bll_managers["MetadataManager"] = MagicMock()

        result = await extension.get_metadata(
            resource_type="document", resource_id="doc_1"
        )

        assert result["success"] is True
        assert result["resource_type"] == "document"
        assert result["resource_id"] == "doc_1"
        assert "metadata" in result
        assert isinstance(result["metadata"], dict)

    @pytest.mark.asyncio
    async def test_metadata_operations_no_manager(self, extension):
        """Test metadata operations without manager."""
        result = await extension.create_metadata(
            resource_type="document", resource_id="doc_1", metadata={}
        )
        assert result["success"] is False
        assert "MetadataManager not available" in result["message"]

        result = await extension.update_metadata(
            resource_type="document", resource_id="doc_1", metadata={}
        )
        assert result["success"] is False

        result = await extension.get_metadata(
            resource_type="document", resource_id="doc_1"
        )
        assert result["success"] is False

    def test_lifecycle_methods(self, extension):
        """Test extension lifecycle methods."""
        # Test on_start
        assert extension.on_start() is True

        # Test on_stop
        assert extension.on_stop() is True

        # Test on_startup
        extension.on_startup()  # Should not raise exception

        # Test on_shutdown
        extension.on_shutdown()  # Should not raise exception

    def test_validate_config(self, extension):
        """Test configuration validation."""
        with patch("builtins.__import__") as mock_import:
            # Test successful validation
            issues = extension.validate_config()
            assert isinstance(issues, list)

            # Test missing dependencies
            mock_import.side_effect = ImportError("Module not found")
            issues = extension.validate_config()
            assert len(issues) > 0
            assert any("Pydantic" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test required permissions."""
        permissions = extension.get_required_permissions()
        assert isinstance(permissions, list)
        assert len(permissions) > 0

        expected_permissions = [
            "labels:create",
            "labels:read",
            "labels:update",
            "labels:delete",
            "metadata:create",
            "metadata:read",
            "metadata:update",
            "metadata:delete",
        ]
        assert set(permissions) == set(expected_permissions)

    @pytest.mark.asyncio
    async def test_error_handling(self, extension):
        """Test error handling in abilities."""
        extension.bll_managers["LabelManager"] = MagicMock()

        with patch.object(
            extension, "bll_managers", side_effect=Exception("Test error")
        ):
            result = await extension.create_label(name="Test")
            assert result["success"] is False
            assert "Error creating label" in result["message"]


if __name__ == "__main__":
    pytest.main([__file__])
