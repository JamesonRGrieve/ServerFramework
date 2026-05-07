from typing import Any, Dict, List, Optional, Set

from extensions.AbstractExtension import AbstractExtension
from lib.Dependencies import EXT_Dependency, PIP_Dependency
from lib.Logging import logger


class MetaLabelsExtension(AbstractExtension):
    """
    Meta Labels extension for AGInfrastructure.
    Provides metadata and labeling functionality for organizing and categorizing
    various resources, entities, and content across the system.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "meta_labels"
    version = "1.0.0"
    description = (
        "Metadata and labeling system for organizing and categorizing resources"
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base metadata functionality",
        ),
        EXT_Dependency(
            name="database",
            friendly_name="Database Extension",
            optional=False,
            reason="Required for storing labels and metadata",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Data Validation",
            optional=False,
            reason="Required for metadata validation and serialization",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="sqlalchemy",
            friendly_name="SQLAlchemy ORM",
            optional=False,
            reason="Required for database operations",
            semver=">=2.0.0",
        ),
    ]

    sys_dependencies = []

    # Define database tables (loaded via import system)
    db_tables = []

    # Define what capabilities this extension provides
    capabilities = [
        "label_management",
        "metadata_storage",
        "resource_categorization",
        "tag_management",
        "hierarchical_labeling",
        "label_search",
        "metadata_validation",
    ]

    def __init__(self, **kwargs):
        """
        Initialize the Meta Labels extension.
        """
        super().__init__(**kwargs)
        self.bll_managers = {}
        self.commands = {}

    def on_initialize(self) -> bool:
        """Initialize the Meta Labels extension."""
        logger.debug("Initializing Meta Labels Extension...")

        try:
            self._register_managers()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("Meta Labels extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Meta Labels extension: {str(e)}")
            return False

    def _register_managers(self) -> None:
        """Register business logic managers."""
        try:
            manager_mapping = {
                "LabelManager": (
                    "extensions.meta_labels.BLL_Meta_Labels",
                    "LabelManager",
                ),
                "MetadataManager": (
                    "extensions.meta_labels.BLL_Meta_Labels",
                    "MetadataManager",
                ),
                "TagManager": ("extensions.meta_labels.BLL_Meta_Labels", "TagManager"),
                "CategoryManager": (
                    "extensions.meta_labels.BLL_Meta_Labels",
                    "CategoryManager",
                ),
            }

            self.bll_managers = {}
            for manager_name, (module_name, class_name) in manager_mapping.items():
                try:
                    module = __import__(module_name, fromlist=[class_name])
                    manager_class = getattr(module, class_name)
                    self.bll_managers[manager_name] = manager_class
                    logger.debug(f"Registered BLL manager: {manager_name}")
                except ImportError as e:
                    logger.warning(f"Could not import BLL manager {manager_name}: {e}")
                except Exception as e:
                    logger.error(f"Error registering BLL manager {manager_name}: {e}")

            logger.debug(f"Registered {len(self.bll_managers)} BLL managers")

        except Exception as e:
            logger.error(f"Error registering managers: {e}")
            self.bll_managers = {}

    def _register_commands(self) -> None:
        """Register commands for label operations."""
        self.commands = {
            "create_label": self.create_label,
            "update_label": self.update_label,
            "delete_label": self.delete_label,
            "apply_label": self.apply_label,
            "remove_label": self.remove_label,
            "search_labels": self.search_labels,
            "get_resource_labels": self.get_resource_labels,
            "create_metadata": self.create_metadata,
            "update_metadata": self.update_metadata,
            "get_metadata": self.get_metadata,
        }

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @AbstractExtension.ability("create_label")
    async def create_label(
        self,
        name: str,
        description: str = "",
        color: str = "#000000",
        category_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new label."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            # In a real implementation, this would use the actual manager
            label_data = {
                "name": name,
                "description": description,
                "color": color,
                "category_id": category_id,
                "metadata": metadata or {},
            }

            return {
                "success": True,
                "label": label_data,
                "message": f"Label '{name}' created successfully",
            }

        except Exception as e:
            logger.error(f"Error creating label: {e}")
            return {"success": False, "message": f"Error creating label: {str(e)}"}

    @AbstractExtension.ability("update_label")
    async def update_label(
        self,
        label_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        color: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update an existing label."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            update_data = {}
            if name is not None:
                update_data["name"] = name
            if description is not None:
                update_data["description"] = description
            if color is not None:
                update_data["color"] = color
            if metadata is not None:
                update_data["metadata"] = metadata

            return {
                "success": True,
                "label_id": label_id,
                "updates": update_data,
                "message": "Label updated successfully",
            }

        except Exception as e:
            logger.error(f"Error updating label: {e}")
            return {"success": False, "message": f"Error updating label: {str(e)}"}

    @AbstractExtension.ability("delete_label")
    async def delete_label(self, label_id: str) -> Dict[str, Any]:
        """Delete a label."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            return {
                "success": True,
                "label_id": label_id,
                "message": "Label deleted successfully",
            }

        except Exception as e:
            logger.error(f"Error deleting label: {e}")
            return {"success": False, "message": f"Error deleting label: {str(e)}"}

    @AbstractExtension.ability("apply_label")
    async def apply_label(
        self, label_id: str, resource_type: str, resource_id: str
    ) -> Dict[str, Any]:
        """Apply a label to a resource."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            return {
                "success": True,
                "label_id": label_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "message": "Label applied successfully",
            }

        except Exception as e:
            logger.error(f"Error applying label: {e}")
            return {"success": False, "message": f"Error applying label: {str(e)}"}

    @AbstractExtension.ability("remove_label")
    async def remove_label(
        self, label_id: str, resource_type: str, resource_id: str
    ) -> Dict[str, Any]:
        """Remove a label from a resource."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            return {
                "success": True,
                "label_id": label_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "message": "Label removed successfully",
            }

        except Exception as e:
            logger.error(f"Error removing label: {e}")
            return {"success": False, "message": f"Error removing label: {str(e)}"}

    @AbstractExtension.ability("search_labels")
    async def search_labels(
        self,
        query: str = "",
        category_id: Optional[str] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Search for labels."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            # In a real implementation, this would query the database
            mock_labels = [
                {
                    "id": "label_1",
                    "name": "Important",
                    "description": "High priority items",
                    "color": "#ff0000",
                },
                {
                    "id": "label_2",
                    "name": "Draft",
                    "description": "Work in progress",
                    "color": "#ffaa00",
                },
            ]

            filtered_labels = [
                label for label in mock_labels if query.lower() in label["name"].lower()
            ]

            return {
                "success": True,
                "labels": filtered_labels[:limit],
                "count": len(filtered_labels),
                "query": query,
            }

        except Exception as e:
            logger.error(f"Error searching labels: {e}")
            return {"success": False, "message": f"Error searching labels: {str(e)}"}

    @AbstractExtension.ability("get_resource_labels")
    async def get_resource_labels(
        self, resource_type: str, resource_id: str
    ) -> Dict[str, Any]:
        """Get all labels applied to a resource."""
        try:
            if "LabelManager" not in self.bll_managers:
                return {"success": False, "message": "LabelManager not available"}

            # In a real implementation, this would query the database
            mock_labels = [
                {
                    "id": "label_1",
                    "name": "Important",
                    "color": "#ff0000",
                    "applied_at": "2024-01-01T00:00:00Z",
                }
            ]

            return {
                "success": True,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "labels": mock_labels,
                "count": len(mock_labels),
            }

        except Exception as e:
            logger.error(f"Error getting resource labels: {e}")
            return {
                "success": False,
                "message": f"Error getting resource labels: {str(e)}",
            }

    @AbstractExtension.ability("create_metadata")
    async def create_metadata(
        self,
        resource_type: str,
        resource_id: str,
        metadata: Dict[str, Any],
        schema_version: str = "1.0",
    ) -> Dict[str, Any]:
        """Create metadata for a resource."""
        try:
            if "MetadataManager" not in self.bll_managers:
                return {"success": False, "message": "MetadataManager not available"}

            return {
                "success": True,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": metadata,
                "schema_version": schema_version,
                "message": "Metadata created successfully",
            }

        except Exception as e:
            logger.error(f"Error creating metadata: {e}")
            return {"success": False, "message": f"Error creating metadata: {str(e)}"}

    @AbstractExtension.ability("update_metadata")
    async def update_metadata(
        self,
        resource_type: str,
        resource_id: str,
        metadata: Dict[str, Any],
        merge: bool = True,
    ) -> Dict[str, Any]:
        """Update metadata for a resource."""
        try:
            if "MetadataManager" not in self.bll_managers:
                return {"success": False, "message": "MetadataManager not available"}

            return {
                "success": True,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": metadata,
                "merge": merge,
                "message": "Metadata updated successfully",
            }

        except Exception as e:
            logger.error(f"Error updating metadata: {e}")
            return {"success": False, "message": f"Error updating metadata: {str(e)}"}

    @AbstractExtension.ability("get_metadata")
    async def get_metadata(
        self, resource_type: str, resource_id: str
    ) -> Dict[str, Any]:
        """Get metadata for a resource."""
        try:
            if "MetadataManager" not in self.bll_managers:
                return {"success": False, "message": "MetadataManager not available"}

            # In a real implementation, this would query the database
            mock_metadata = {
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
                "version": "1.0",
                "custom_fields": {},
            }

            return {
                "success": True,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "metadata": mock_metadata,
            }

        except Exception as e:
            logger.error(f"Error getting metadata: {e}")
            return {"success": False, "message": f"Error getting metadata: {str(e)}"}

    def on_start(self) -> bool:
        """Start the Meta Labels extension."""
        try:
            logger.debug("Meta Labels extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start Meta Labels extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the Meta Labels extension."""
        try:
            logger.debug("Meta Labels extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping Meta Labels extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import pydantic
        except ImportError:
            issues.append(
                "Pydantic library not installed - data validation will not work"
            )

        try:
            import sqlalchemy
        except ImportError:
            issues.append(
                "SQLAlchemy library not installed - database operations will not work"
            )

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "labels:create",
            "labels:read",
            "labels:update",
            "labels:delete",
            "metadata:create",
            "metadata:read",
            "metadata:update",
            "metadata:delete",
        ]

    def on_startup(self):
        """Called during application startup."""
        logger.debug("Meta Labels extension startup hook called")

    def on_shutdown(self):
        """Called during application shutdown."""
        logger.debug("Meta Labels extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if the extension has a specific capability."""
        return capability in self.capabilities


# Alias for backward compatibility
EXT_Meta_Labels = MetaLabelsExtension
