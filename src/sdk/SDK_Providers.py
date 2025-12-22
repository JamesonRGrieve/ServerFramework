"""
Provider SDK module for provider and related entity management.

This module provides comprehensive access to provider-related endpoints including:
- Provider management (create, read, update, delete, search)
- Provider instance management
- Provider instance settings
- Provider extensions
- Provider extension abilities
- Rotation management
- Rotation provider instances
- Provider instance usage tracking
- Extension instance abilities

This module now uses configuration-driven resource management following
the improved abstraction patterns similar to endpoint patterns.
"""

from typing import Any, Dict, List

from sdk.AbstractSDKHandler import AbstractSDKHandler, ResourceConfig

# ===== Provider SDK =====


class ProviderSDK(AbstractSDKHandler):
    """SDK for provider management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for provider management."""
        return {
            "providers": ResourceConfig(
                name="provider",
                name_plural="providers",
                endpoint="/v1/provider",
                required_fields=["name"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
            )
        }

    def create_provider(
        self,
        name: str,
        friendly_name: str = None,
        agent_settings_json: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new provider.

        Args:
            name: Provider name
            friendly_name: Human-readable provider name
            agent_settings_json: JSON configuration for the provider
            **kwargs: Additional provider fields

        Returns:
            Provider creation response
        """
        provider_data = {"name": name}
        if friendly_name:
            provider_data["friendly_name"] = friendly_name
        if agent_settings_json:
            provider_data["agent_settings_json"] = agent_settings_json
        provider_data.update(kwargs)

        return self.providers.create(provider_data)

    def get_provider(self, provider_id: str) -> Dict[str, Any]:
        """Get a provider by ID."""
        return self.providers.get(provider_id)

    def list_providers(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List providers."""
        return self.providers.list(offset=offset, limit=limit, **filters)

    def update_provider(self, provider_id: str, **updates) -> Dict[str, Any]:
        """Update a provider."""
        return self.providers.update(provider_id, updates)

    def delete_provider(self, provider_id: str) -> None:
        """Delete a provider."""
        self.providers.delete(provider_id)

    def search_providers(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search providers."""
        return self.providers.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    def get_provider_status(self, provider_id: str) -> Dict[str, Any]:
        """Get provider status."""
        return self._request("GET", f"/v1/provider/{provider_id}/status")

    def get_provider_config(self, provider_id: str) -> Dict[str, Any]:
        """Get provider configuration."""
        return self._request("GET", f"/v1/provider/{provider_id}/config")

    def update_provider_config(
        self, provider_id: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update provider configuration."""
        return self._request("PATCH", f"/v1/provider/{provider_id}/config", data=config)

    def enable_provider(self, provider_id: str) -> Dict[str, Any]:
        """Enable a provider."""
        return self._request("POST", f"/v1/provider/{provider_id}/enable")

    def disable_provider(self, provider_id: str) -> Dict[str, Any]:
        """Disable a provider."""
        return self._request("POST", f"/v1/provider/{provider_id}/disable")

    def get_provider_metrics(
        self, provider_id: str, start_date: str = None, end_date: str = None
    ) -> Dict[str, Any]:
        """Get provider metrics."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._request(
            "GET", f"/v1/provider/{provider_id}/metrics", query_params=params
        )

    def get_provider_logs(
        self, provider_id: str, limit: int = 100, offset: int = 0
    ) -> Dict[str, Any]:
        """Get provider logs."""
        params = {"limit": limit, "offset": offset}
        return self._request(
            "GET", f"/v1/provider/{provider_id}/logs", query_params=params
        )

    def batch_update_providers(
        self, updates: Dict[str, Any], provider_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update providers."""
        return self.providers.batch_update(updates, provider_ids)

    def batch_delete_providers(self, provider_ids: List[str]) -> Dict[str, Any]:
        """Batch delete providers."""
        return self.providers.batch_delete(provider_ids)


# ===== Provider Instance SDK =====


class ProviderInstanceSDK(AbstractSDKHandler):
    """SDK for provider instance management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for provider instance management."""
        return {
            "provider_instances": ResourceConfig(
                name="provider_instance",
                name_plural="provider_instances",
                endpoint="/v1/provider/instance",
                required_fields=["name", "provider_id"],
                supports_search=True,
                supports_batch=True,
                parent_resource="provider",
            )
        }

    def create_provider_instance(
        self,
        name: str,
        provider_id: str,
        model_name: str = None,
        api_key: str = None,
        team_id: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new provider instance.

        Args:
            name: Instance name
            provider_id: ID of the parent provider
            model_name: Model name for the instance
            api_key: API key for the instance
            team_id: Team ID for the instance
            **kwargs: Additional instance fields

        Returns:
            Provider instance creation response
        """
        instance_data = {
            "name": name,
            "provider_id": provider_id,
        }
        if model_name:
            instance_data["model_name"] = model_name
        if api_key:
            instance_data["api_key"] = api_key
        if team_id:
            instance_data["team_id"] = team_id
        instance_data.update(kwargs)

        return self.provider_instances.create(instance_data)

    def get_provider_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get a provider instance by ID."""
        return self.provider_instances.get(instance_id)

    def list_provider_instances(
        self, provider_id: str = None, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List provider instances."""
        list_filters = filters.copy()
        if provider_id:
            list_filters["provider_id"] = provider_id
        return self.provider_instances.list(offset=offset, limit=limit, **list_filters)

    def update_provider_instance(self, instance_id: str, **updates) -> Dict[str, Any]:
        """Update a provider instance."""
        return self.provider_instances.update(instance_id, updates)

    def delete_provider_instance(self, instance_id: str) -> None:
        """Delete a provider instance."""
        self.provider_instances.delete(instance_id)

    def search_provider_instances(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search provider instances."""
        return self.provider_instances.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    def get_provider_instance_status(self, instance_id: str) -> Dict[str, Any]:
        """Get provider instance status."""
        return self._request("GET", f"/v1/provider/instance/{instance_id}/status")

    def get_provider_instance_config(self, instance_id: str) -> Dict[str, Any]:
        """Get provider instance configuration."""
        return self._request("GET", f"/v1/provider/instance/{instance_id}/config")

    def update_provider_instance_config(
        self, instance_id: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update provider instance configuration."""
        return self._request(
            "PATCH", f"/v1/provider/instance/{instance_id}/config", data=config
        )

    def enable_provider_instance(self, instance_id: str) -> Dict[str, Any]:
        """Enable a provider instance."""
        return self._request("POST", f"/v1/provider/instance/{instance_id}/enable")

    def disable_provider_instance(self, instance_id: str) -> Dict[str, Any]:
        """Disable a provider instance."""
        return self._request("POST", f"/v1/provider/instance/{instance_id}/disable")

    def get_provider_instance_metrics(
        self, instance_id: str, start_date: str = None, end_date: str = None
    ) -> Dict[str, Any]:
        """Get provider instance metrics."""
        params = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return self._request(
            "GET", f"/v1/provider/instance/{instance_id}/metrics", query_params=params
        )

    def get_provider_instance_logs(
        self, instance_id: str, limit: int = 100, offset: int = 0
    ) -> Dict[str, Any]:
        """Get provider instance logs."""
        params = {"limit": limit, "offset": offset}
        return self._request(
            "GET", f"/v1/provider/instance/{instance_id}/logs", query_params=params
        )

    def batch_update_provider_instances(
        self, updates: Dict[str, Any], instance_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update provider instances."""
        return self.provider_instances.batch_update(updates, instance_ids)

    def batch_delete_provider_instances(
        self, instance_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch delete provider instances."""
        return self.provider_instances.batch_delete(instance_ids)


# ===== Provider Instance Setting SDK =====


class ProviderInstanceSettingSDK(AbstractSDKHandler):
    """SDK for provider instance setting management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for provider instance setting management."""
        return {
            "provider_instance_settings": ResourceConfig(
                name="provider_instance_setting",
                name_plural="provider_instance_settings",
                endpoint="/v1/provider/instance/setting",
                required_fields=["provider_instance_id", "key", "value"],
                supports_search=False,
                supports_batch=False,
                parent_resource="provider_instance",
            )
        }

    def create_provider_instance_setting(
        self,
        provider_instance_id: str,
        key: str,
        value: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a provider instance setting.

        Args:
            provider_instance_id: ID of the provider instance
            key: Setting key
            value: Setting value
            **kwargs: Additional setting fields

        Returns:
            Provider instance setting creation response
        """
        setting_data = {
            "provider_instance_id": provider_instance_id,
            "key": key,
            "value": value,
        }
        setting_data.update(kwargs)

        return self.provider_instance_settings.create(setting_data)

    def get_provider_instance_setting(self, setting_id: str) -> Dict[str, Any]:
        """Get a provider instance setting by ID."""
        return self.provider_instance_settings.get(setting_id)

    def list_provider_instance_settings(
        self, instance_id: str = None, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List provider instance settings."""
        list_filters = filters.copy()
        if instance_id:
            list_filters["provider_instance_id"] = instance_id
        return self.provider_instance_settings.list(
            offset=offset, limit=limit, **list_filters
        )

    def update_provider_instance_setting(
        self, setting_id: str, **updates
    ) -> Dict[str, Any]:
        """Update a provider instance setting."""
        return self.provider_instance_settings.update(setting_id, updates)

    def delete_provider_instance_setting(self, setting_id: str) -> None:
        """Delete a provider instance setting."""
        self.provider_instance_settings.delete(setting_id)


# ===== Provider Extension SDK =====


class ProviderExtensionSDK(AbstractSDKHandler):
    """SDK for provider extension management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for provider extension management."""
        return {
            "provider_extensions": ResourceConfig(
                name="provider_extension",
                name_plural="provider_extensions",
                endpoint="/v1/provider/extension",
                required_fields=["provider_id", "extension_id"],
                supports_search=True,
                supports_batch=False,
                parent_resource="provider",
            )
        }

    def create_provider_extension(
        self,
        provider_id: str,
        extension_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a provider extension relationship.

        Args:
            provider_id: ID of the provider
            extension_id: ID of the extension
            **kwargs: Additional relationship fields

        Returns:
            Provider extension creation response
        """
        extension_data = {
            "provider_id": provider_id,
            "extension_id": extension_id,
        }
        extension_data.update(kwargs)

        return self.provider_extensions.create(extension_data)

    def get_provider_extension(self, extension_id: str) -> Dict[str, Any]:
        """Get a provider extension by ID."""
        return self.provider_extensions.get(extension_id)

    def list_provider_extensions(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List provider extensions."""
        return self.provider_extensions.list(offset=offset, limit=limit, **filters)

    def update_provider_extension(self, extension_id: str, **updates) -> Dict[str, Any]:
        """Update a provider extension."""
        return self.provider_extensions.update(extension_id, updates)

    def delete_provider_extension(self, extension_id: str) -> None:
        """Delete a provider extension."""
        self.provider_extensions.delete(extension_id)

    def search_provider_extensions(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search provider extensions."""
        return self.provider_extensions.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )


# ===== Provider Extension Ability SDK =====


class ProviderExtensionAbilitySDK(AbstractSDKHandler):
    """SDK for provider extension ability management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for provider extension ability management."""
        return {
            "provider_extension_abilities": ResourceConfig(
                name="provider_extension_ability",
                name_plural="provider_extension_abilities",
                endpoint="/v1/extension/ability/provider",
                required_fields=["provider_extension_id", "ability_id"],
                supports_search=False,
                supports_batch=False,
                parent_resource="provider_extension",
            )
        }

    def create_provider_extension_ability(
        self,
        provider_extension_id: str,
        ability_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a provider extension ability relationship.

        Args:
            provider_extension_id: ID of the provider extension
            ability_id: ID of the ability
            **kwargs: Additional relationship fields

        Returns:
            Provider extension ability creation response
        """
        ability_data = {
            "provider_extension_id": provider_extension_id,
            "ability_id": ability_id,
        }
        ability_data.update(kwargs)

        return self.provider_extension_abilities.create(ability_data)

    def get_provider_extension_ability(self, ability_id: str) -> Dict[str, Any]:
        """Get a provider extension ability by ID."""
        return self.provider_extension_abilities.get(ability_id)

    def list_provider_extension_abilities(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List provider extension abilities."""
        return self.provider_extension_abilities.list(
            offset=offset, limit=limit, **filters
        )

    def update_provider_extension_ability(
        self, ability_id: str, **updates
    ) -> Dict[str, Any]:
        """Update a provider extension ability."""
        return self.provider_extension_abilities.update(ability_id, updates)

    def delete_provider_extension_ability(self, ability_id: str) -> None:
        """Delete a provider extension ability."""
        self.provider_extension_abilities.delete(ability_id)


# ===== Rotation SDK =====


class RotationSDK(AbstractSDKHandler):
    """SDK for rotation management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for rotation management."""
        return {
            "rotations": ResourceConfig(
                name="rotation",
                name_plural="rotations",
                endpoint="/v1/rotation",
                required_fields=["name"],
                supports_search=True,
                supports_batch=True,
            )
        }

    def create_rotation(
        self,
        name: str,
        description: str = None,
        team_id: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new rotation.

        Args:
            name: Rotation name
            description: Rotation description
            team_id: Team ID for the rotation
            **kwargs: Additional rotation fields

        Returns:
            Rotation creation response
        """
        rotation_data = {"name": name}
        if description:
            rotation_data["description"] = description
        if team_id:
            rotation_data["team_id"] = team_id
        rotation_data.update(kwargs)

        return self.rotations.create(rotation_data)

    def get_rotation(self, rotation_id: str) -> Dict[str, Any]:
        """Get a rotation by ID."""
        return self.rotations.get(rotation_id)

    def list_rotations(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List rotations."""
        return self.rotations.list(offset=offset, limit=limit, **filters)

    def update_rotation(self, rotation_id: str, **updates) -> Dict[str, Any]:
        """Update a rotation."""
        return self.rotations.update(rotation_id, updates)

    def delete_rotation(self, rotation_id: str) -> None:
        """Delete a rotation."""
        self.rotations.delete(rotation_id)

    def search_rotations(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search rotations."""
        return self.rotations.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    def batch_update_rotations(
        self, updates: Dict[str, Any], rotation_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update rotations."""
        return self.rotations.batch_update(updates, rotation_ids)

    def batch_delete_rotations(self, rotation_ids: List[str]) -> Dict[str, Any]:
        """Batch delete rotations."""
        return self.rotations.batch_delete(rotation_ids)


# ===== Rotation Provider Instance SDK =====


class RotationProviderInstanceSDK(AbstractSDKHandler):
    """SDK for rotation provider instance management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for rotation provider instance management."""
        return {
            "rotation_provider_instances": ResourceConfig(
                name="rotation_provider_instance",
                name_plural="rotation_provider_instances",
                endpoint="/v1/rotation/provider/instance",
                required_fields=["rotation_id", "provider_instance_id"],
                supports_search=False,
                supports_batch=False,
                parent_resource="rotation",
            )
        }

    def create_rotation_provider_instance(
        self,
        rotation_id: str,
        provider_instance_id: str,
        parent_id: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a rotation provider instance relationship.

        Args:
            rotation_id: ID of the rotation
            provider_instance_id: ID of the provider instance
            parent_id: Parent rotation provider instance ID
            **kwargs: Additional relationship fields

        Returns:
            Rotation provider instance creation response
        """
        rotation_provider_data = {
            "rotation_id": rotation_id,
            "provider_instance_id": provider_instance_id,
        }
        if parent_id:
            rotation_provider_data["parent_id"] = parent_id
        rotation_provider_data.update(kwargs)

        return self.rotation_provider_instances.create(rotation_provider_data)

    def get_rotation_provider_instance(
        self, rotation_provider_id: str
    ) -> Dict[str, Any]:
        """Get a rotation provider instance by ID."""
        return self.rotation_provider_instances.get(rotation_provider_id)

    def list_rotation_provider_instances(
        self, rotation_id: str = None, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List rotation provider instances."""
        list_filters = filters.copy()
        if rotation_id:
            list_filters["rotation_id"] = rotation_id
        return self.rotation_provider_instances.list(
            offset=offset, limit=limit, **list_filters
        )

    def update_rotation_provider_instance(
        self, rotation_provider_id: str, **updates
    ) -> Dict[str, Any]:
        """Update a rotation provider instance."""
        return self.rotation_provider_instances.update(rotation_provider_id, updates)

    def delete_rotation_provider_instance(self, rotation_provider_id: str) -> None:
        """Delete a rotation provider instance."""
        self.rotation_provider_instances.delete(rotation_provider_id)


# ===== Provider Instance Usage SDK =====


class ProviderInstanceUsageSDK(AbstractSDKHandler):
    """SDK for provider instance usage tracking operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for provider instance usage management."""
        return {
            "provider_instance_usage": ResourceConfig(
                name="provider_instance_usage",
                name_plural="provider_instance_usage",
                endpoint="/v1/provider/instance/usage",
                required_fields=["provider_instance_id"],
                supports_search=True,
                supports_batch=False,
                parent_resource="provider_instance",
            )
        }

    def create_provider_instance_usage(
        self,
        provider_instance_id: str,
        input_tokens: int = None,
        output_tokens: int = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a provider instance usage record.

        Args:
            provider_instance_id: ID of the provider instance
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens used
            **kwargs: Additional usage fields

        Returns:
            Provider instance usage creation response
        """
        usage_data = {
            "provider_instance_id": provider_instance_id,
        }
        if input_tokens is not None:
            usage_data["input_tokens"] = input_tokens
        if output_tokens is not None:
            usage_data["output_tokens"] = output_tokens
        usage_data.update(kwargs)

        return self.provider_instance_usage.create(usage_data)

    def get_provider_instance_usage(self, usage_id: str) -> Dict[str, Any]:
        """Get a provider instance usage record by ID."""
        return self.provider_instance_usage.get(usage_id)

    def list_provider_instance_usage(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List provider instance usage records."""
        return self.provider_instance_usage.list(offset=offset, limit=limit, **filters)

    def update_provider_instance_usage(
        self, usage_id: str, **updates
    ) -> Dict[str, Any]:
        """Update a provider instance usage record."""
        return self.provider_instance_usage.update(usage_id, updates)

    def delete_provider_instance_usage(self, usage_id: str) -> None:
        """Delete a provider instance usage record."""
        self.provider_instance_usage.delete(usage_id)

    def search_provider_instance_usage(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search provider instance usage records."""
        return self.provider_instance_usage.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )


# ===== Extension Instance Ability SDK =====


class ExtensionInstanceAbilitySDK(AbstractSDKHandler):
    """SDK for extension instance ability management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for extension instance ability management."""
        return {
            "extension_instance_abilities": ResourceConfig(
                name="extension_instance_ability",
                name_plural="extension_instance_abilities",
                endpoint="/v1/extension/ability/provider/instance",
                required_fields=[
                    "provider_instance_id",
                    "provider_extension_ability_id",
                ],
                supports_search=True,
                supports_batch=False,
                parent_resource="provider_instance",
            )
        }

    def create_extension_instance_ability(
        self,
        provider_instance_id: str,
        provider_extension_ability_id: str,
        state: bool = True,
        forced: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create an extension instance ability.

        Args:
            provider_instance_id: ID of the provider instance
            provider_extension_ability_id: ID of the provider extension ability
            state: Whether the ability is enabled
            forced: Whether the ability is forced
            **kwargs: Additional ability fields

        Returns:
            Extension instance ability creation response
        """
        ability_data = {
            "provider_instance_id": provider_instance_id,
            "provider_extension_ability_id": provider_extension_ability_id,
            "state": state,
            "forced": forced,
        }
        ability_data.update(kwargs)

        return self.extension_instance_abilities.create(ability_data)

    def get_extension_instance_ability(self, ability_id: str) -> Dict[str, Any]:
        """Get an extension instance ability by ID."""
        return self.extension_instance_abilities.get(ability_id)

    def list_extension_instance_abilities(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List extension instance abilities."""
        return self.extension_instance_abilities.list(
            offset=offset, limit=limit, **filters
        )

    def update_extension_instance_ability(
        self, ability_id: str, **updates
    ) -> Dict[str, Any]:
        """Update an extension instance ability."""
        return self.extension_instance_abilities.update(ability_id, updates)

    def delete_extension_instance_ability(self, ability_id: str) -> None:
        """Delete an extension instance ability."""
        self.extension_instance_abilities.delete(ability_id)

    def search_extension_instance_abilities(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search extension instance abilities."""
        return self.extension_instance_abilities.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )


# ===== Main Providers SDK (Composite) =====


class ProvidersSDK(AbstractSDKHandler):
    """Composite SDK for all provider-related operations.

    This SDK combines all provider functionality into a single interface
    using configuration-driven resource management.
    """

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure all provider-related resources."""
        return {
            "providers": ResourceConfig(
                name="provider",
                name_plural="providers",
                endpoint="/v1/provider",
                required_fields=["name"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
            ),
            "provider_instances": ResourceConfig(
                name="provider_instance",
                name_plural="provider_instances",
                endpoint="/v1/provider/instance",
                required_fields=["name", "provider_id"],
                supports_search=True,
                supports_batch=True,
                parent_resource="provider",
            ),
            "provider_instance_settings": ResourceConfig(
                name="provider_instance_setting",
                name_plural="provider_instance_settings",
                endpoint="/v1/provider/instance/setting",
                required_fields=["provider_instance_id", "key", "value"],
                supports_search=False,
                supports_batch=False,
                parent_resource="provider_instance",
            ),
            "provider_extensions": ResourceConfig(
                name="provider_extension",
                name_plural="provider_extensions",
                endpoint="/v1/provider/extension",
                required_fields=["provider_id", "extension_id"],
                supports_search=True,
                supports_batch=False,
                parent_resource="provider",
            ),
            "provider_extension_abilities": ResourceConfig(
                name="provider_extension_ability",
                name_plural="provider_extension_abilities",
                endpoint="/v1/extension/ability/provider",
                required_fields=["provider_extension_id", "ability_id"],
                supports_search=False,
                supports_batch=False,
                parent_resource="provider_extension",
            ),
            "rotations": ResourceConfig(
                name="rotation",
                name_plural="rotations",
                endpoint="/v1/rotation",
                required_fields=["name"],
                supports_search=True,
                supports_batch=True,
            ),
            "rotation_provider_instances": ResourceConfig(
                name="rotation_provider_instance",
                name_plural="rotation_provider_instances",
                endpoint="/v1/rotation/provider/instance",
                required_fields=["rotation_id", "provider_instance_id"],
                supports_search=False,
                supports_batch=False,
                parent_resource="rotation",
            ),
            "provider_instance_usage": ResourceConfig(
                name="provider_instance_usage",
                name_plural="provider_instance_usage",
                endpoint="/v1/provider/instance/usage",
                required_fields=["provider_instance_id"],
                supports_search=True,
                supports_batch=False,
                parent_resource="provider_instance",
            ),
            "extension_instance_abilities": ResourceConfig(
                name="extension_instance_ability",
                name_plural="extension_instance_abilities",
                endpoint="/v1/extension/ability/provider/instance",
                required_fields=[
                    "provider_instance_id",
                    "provider_extension_ability_id",
                ],
                supports_search=True,
                supports_batch=False,
                parent_resource="provider_instance",
            ),
        }

    # Provider convenience methods
    def create_provider(
        self,
        name: str,
        friendly_name: str = None,
        agent_settings_json: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new provider."""
        provider_data = {"name": name}
        if friendly_name:
            provider_data["friendly_name"] = friendly_name
        if agent_settings_json:
            provider_data["agent_settings_json"] = agent_settings_json
        provider_data.update(kwargs)
        return self.providers.create(provider_data)

    def get_provider(self, provider_id: str) -> Dict[str, Any]:
        """Get a provider by ID."""
        return self.providers.get(provider_id)

    def list_providers(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List providers."""
        return self.providers.list(offset=offset, limit=limit, **filters)

    def update_provider(self, provider_id: str, **updates) -> Dict[str, Any]:
        """Update a provider."""
        return self.providers.update(provider_id, updates)

    def delete_provider(self, provider_id: str) -> None:
        """Delete a provider."""
        self.providers.delete(provider_id)

    def search_providers(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search providers."""
        return self.providers.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    # Provider instance convenience methods
    def create_provider_instance(
        self,
        name: str,
        provider_id: str,
        model_name: str = None,
        api_key: str = None,
        team_id: str = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new provider instance."""
        instance_data = {
            "name": name,
            "provider_id": provider_id,
        }
        if model_name:
            instance_data["model_name"] = model_name
        if api_key:
            instance_data["api_key"] = api_key
        if team_id:
            instance_data["team_id"] = team_id
        instance_data.update(kwargs)
        return self.provider_instances.create(instance_data)

    def get_provider_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get a provider instance by ID."""
        return self.provider_instances.get(instance_id)

    def list_provider_instances(
        self, provider_id: str = None, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List provider instances."""
        list_filters = filters.copy()
        if provider_id:
            list_filters["provider_id"] = provider_id
        return self.provider_instances.list(offset=offset, limit=limit, **list_filters)

    def update_provider_instance(self, instance_id: str, **updates) -> Dict[str, Any]:
        """Update a provider instance."""
        return self.provider_instances.update(instance_id, updates)

    def delete_provider_instance(self, instance_id: str) -> None:
        """Delete a provider instance."""
        self.provider_instances.delete(instance_id)

    def search_provider_instances(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: str = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search provider instances."""
        return self.provider_instances.search(
            criteria, offset=offset, limit=limit, sort_by=sort_by, sort_order=sort_order
        )

    def __getattr__(self, name):
        """Delegate attribute access to individual SDK instances for backward compatibility."""
        # This provides backward compatibility for code that expects individual SDK instances
        try:
            # Use object.__getattribute__ to avoid recursion
            sdk_attr = f"_{name}_sdk"
            return object.__getattribute__(self, sdk_attr)
        except AttributeError:
            raise AttributeError(
                f"'{self.__class__.__name__}' object has no attribute '{name}'"
            )
