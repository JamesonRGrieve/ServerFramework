"""
Extensions SDK module for extension and ability management.

This module provides comprehensive access to extension-related endpoints including:
- Extension management (create, read, update, delete)
- Ability management (create, read, update, delete)
- Extension-ability relationships

This module now uses configuration-driven resource management following
the improved abstraction patterns similar to endpoint patterns.
"""

from typing import Any, Dict, List

from zephyrex.sdk.AbstractSDKHandler import AbstractSDKHandler, ResourceConfig

# ===== Extension SDK =====


class ExtensionSDK(AbstractSDKHandler):
    """SDK for extension management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for extension management."""
        return {
            "extensions": ResourceConfig(
                name="extension",
                name_plural="extensions",
                endpoint="/v1/extension",
                required_fields=["name"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
            )
        }

    # Convenience methods that delegate to the resource manager
    def create_extension(
        self,
        name: str,
        description: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new extension.

        Args:
            name: Extension name (required)
            description: Extension description (optional)
            **kwargs: Additional extension fields

        Returns:
            Extension creation response
        """
        extension_data = {"name": name}
        if description:
            extension_data["description"] = description
        extension_data.update(kwargs)
        return self.extensions.create(extension_data)

    def get_extension(self, extension_id: str) -> Dict[str, Any]:
        """Get an extension by ID."""
        return self.extensions.get(extension_id)

    def list_extensions(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List extensions."""
        return self.extensions.list(offset=offset, limit=limit, **filters)

    def update_extension(self, extension_id: str, **updates) -> Dict[str, Any]:
        """Update an extension."""
        return self.extensions.update(extension_id, updates)

    def delete_extension(self, extension_id: str) -> None:
        """Delete an extension."""
        self.extensions.delete(extension_id)

    def search_extensions(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search extensions."""
        return self.extensions.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    def batch_update_extensions(
        self, updates: Dict[str, Any], extension_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update extensions."""
        return self.extensions.batch_update(updates, extension_ids)

    def batch_delete_extensions(self, extension_ids: List[str]) -> Dict[str, Any]:
        """Batch delete extensions."""
        return self.extensions.batch_delete(extension_ids)


# ===== Ability SDK =====


class AbilitySDK(AbstractSDKHandler):
    """SDK for ability management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for ability management."""
        return {
            "abilities": ResourceConfig(
                name="ability",
                name_plural="abilities",
                endpoint="/v1/ability",
                required_fields=["name", "extension_id"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
                parent_resource="extension",
            )
        }

    # Convenience methods that delegate to the resource manager
    def create_ability(
        self,
        name: str,
        extension_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new ability.

        Args:
            name: Ability name (required)
            extension_id: ID of the parent extension (required)
            **kwargs: Additional ability fields

        Returns:
            Ability creation response
        """
        ability_data = {
            "name": name,
            "extension_id": extension_id,
        }
        ability_data.update(kwargs)
        return self.abilities.create(ability_data)

    def get_ability(self, ability_id: str) -> Dict[str, Any]:
        """Get an ability by ID."""
        return self.abilities.get(ability_id)

    def list_abilities(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List abilities."""
        return self.abilities.list(offset=offset, limit=limit, **filters)

    def update_ability(self, ability_id: str, **updates) -> Dict[str, Any]:
        """Update an ability."""
        return self.abilities.update(ability_id, updates)

    def delete_ability(self, ability_id: str) -> None:
        """Delete an ability."""
        self.abilities.delete(ability_id)

    def search_abilities(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search abilities."""
        return self.abilities.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    def batch_update_abilities(
        self, updates: Dict[str, Any], ability_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update abilities."""
        return self.abilities.batch_update(updates, ability_ids)

    def batch_delete_abilities(self, ability_ids: List[str]) -> Dict[str, Any]:
        """Batch delete abilities."""
        return self.abilities.batch_delete(ability_ids)


# ===== Main Extensions SDK (Composite) =====


class ExtensionsSDK(AbstractSDKHandler):
    """Composite SDK for all extension-related operations.

    This SDK combines extension and ability management into a single interface
    using configuration-driven resource management.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for extensions and abilities."""
        return {
            "extensions": ResourceConfig(
                name="extension",
                name_plural="extensions",
                endpoint="/v1/extension",
                required_fields=["name"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
            ),
            "abilities": ResourceConfig(
                name="ability",
                name_plural="abilities",
                endpoint="/v1/ability",
                required_fields=["name", "extension_id"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
                parent_resource="extension",
            ),
        }

    # Extension convenience methods
    def create_extension(
        self,
        name: str,
        description: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new extension."""
        extension_data = {"name": name}
        if description:
            extension_data["description"] = description
        extension_data.update(kwargs)
        return self.extensions.create(extension_data)

    def get_extension(self, extension_id: str) -> Dict[str, Any]:
        """Get an extension by ID."""
        return self.extensions.get(extension_id)

    def list_extensions(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List extensions."""
        return self.extensions.list(offset=offset, limit=limit, **filters)

    def update_extension(self, extension_id: str, **updates) -> Dict[str, Any]:
        """Update an extension."""
        return self.extensions.update(extension_id, updates)

    def delete_extension(self, extension_id: str) -> None:
        """Delete an extension."""
        self.extensions.delete(extension_id)

    def search_extensions(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search extensions."""
        return self.extensions.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    # Ability convenience methods
    def create_ability(
        self,
        name: str,
        extension_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new ability."""
        ability_data = {
            "name": name,
            "extension_id": extension_id,
        }
        ability_data.update(kwargs)
        return self.abilities.create(ability_data)

    def get_ability(self, ability_id: str) -> Dict[str, Any]:
        """Get an ability by ID."""
        return self.abilities.get(ability_id)

    def list_abilities(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List abilities."""
        return self.abilities.list(offset=offset, limit=limit, **filters)

    def update_ability(self, ability_id: str, **updates) -> Dict[str, Any]:
        """Update an ability."""
        return self.abilities.update(ability_id, updates)

    def delete_ability(self, ability_id: str) -> None:
        """Delete an ability."""
        self.abilities.delete(ability_id)

    def search_abilities(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search abilities."""
        return self.abilities.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    # Batch operations
    def batch_update_extensions(
        self, updates: Dict[str, Any], extension_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update extensions."""
        return self.extensions.batch_update(updates, extension_ids)

    def batch_delete_extensions(self, extension_ids: List[str]) -> Dict[str, Any]:
        """Batch delete extensions."""
        return self.extensions.batch_delete(extension_ids)

    def batch_update_abilities(
        self, updates: Dict[str, Any], ability_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update abilities."""
        return self.abilities.batch_update(updates, ability_ids)

    def batch_delete_abilities(self, ability_ids: List[str]) -> Dict[str, Any]:
        """Batch delete abilities."""
        return self.abilities.batch_delete(ability_ids)
