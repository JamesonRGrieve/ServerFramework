"""
Authentication SDK module for user authentication and management.

This module provides comprehensive access to authentication-related endpoints including:
- User authentication (login, logout, token verification)
- User registration and profile management
- Team management and user-team relationships
- Role and permission management
- Invitation system
- Session management
- API key management
- Notification management
- User and team metadata management
- User credentials and recovery questions
- Failed login tracking

This module now uses configuration-driven resource management following
the improved abstraction patterns similar to endpoint patterns.
"""

import base64
from logging import getLogger
from typing import Any, Dict, List, Optional, cast

from zephyrex.sdk.AbstractSDKHandler import (
    AbstractSDKHandler,
    AuthenticationError,
    ResourceConfig,
    ResourceManager,
    SDKException,
)

logger = getLogger(__name__)

_API_KEYS_ENDPOINT = "/v1/auth/api-keys"

# ===== User SDK =====


class UserSDK(AbstractSDKHandler):
    """SDK for user management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    users: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for user management."""
        return {
            "users": ResourceConfig(
                name="user",
                name_plural="users",
                endpoint="/v1/user",
                required_fields=["email", "first_name", "last_name"],
                unique_fields=["email"],
                supports_search=True,
                supports_batch=True,
            )
        }

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """Login with email and password credentials."""
        auth_string = f"{email}:{password}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        headers = {"Authorization": f"Basic {encoded_auth}"}

        try:
            response = self._request(
                "POST",
                "/v1/user/authorize",
                headers=headers,
            )
            if "token" in response:
                self.token = response["token"]
            return cast(Dict[str, Any], response)
        except Exception as e:
            raise AuthenticationError(f"Login failed: {str(e)}")

    def logout(self) -> None:
        """Logout the current user and invalidate the token."""
        try:
            self._request("POST", "/v1/user/logout")
            self.token = None
        except Exception as e:
            self.token = None
            raise AuthenticationError(f"Logout failed: {str(e)}")

    def verify_token(self) -> bool:
        """Verify if the current token is valid."""
        try:
            self._request("GET", "/v1")
            return True
        except SDKException as e:
            logger.warning(f"Token verification failed: {e}", exc_info=True)
            return False

    def register(
        self,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        display_name: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Register a new user account."""
        user_data = {
            "email": email,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        }
        if display_name:
            user_data["display_name"] = display_name
        user_data.update(kwargs)
        return cast(Dict[str, Any], self.users.create(user_data))

    def change_password(
        self, current_password: str, new_password: str
    ) -> Dict[str, Any]:
        """Change the current user's password."""
        data = {
            "current_password": current_password,
            "new_password": new_password,
        }
        return cast(Dict[str, Any], self._request("PATCH", "/v1/user", data=data))

    def get_current_user(self) -> Dict[str, Any]:
        """Get the current authenticated user's information."""
        return cast(Dict[str, Any], self._request("GET", "/v1/user"))

    def update_current_user(self, **updates) -> Dict[str, Any]:
        """Update the current user's profile."""
        data = {"user": updates}
        return cast(Dict[str, Any], self._request("PUT", "/v1/user", data=data))

    def list_users(
        self, team_id: str, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List users in a team."""
        params = {"offset": offset, "limit": limit}
        params.update(filters)
        return cast(
            Dict[str, Any],
            self._request("GET", f"/v1/team/{team_id}/user", query_params=params),
        )

    def get_user(self, user_id: str) -> Dict[str, Any]:
        """Get a user by ID."""
        return cast(Dict[str, Any], self.users.get(user_id))

    def update_user(self, user_id: str, **updates) -> Dict[str, Any]:
        """Update a user."""
        return cast(Dict[str, Any], self.users.update(user_id, updates))

    def delete_user(self, user_id: str) -> None:
        """Delete a user."""
        self.users.delete(user_id)

    def search_users(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search users."""
        return cast(
            Dict[str, Any],
            self.users.search(
                criteria,
                offset=offset,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            ),
        )

    def batch_update_users(
        self, updates: Dict[str, Any], user_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update users."""
        return cast(Dict[str, Any], self.users.batch_update(updates, user_ids))

    def batch_delete_users(self, user_ids: List[str]) -> Dict[str, Any]:
        """Batch delete users."""
        return cast(Dict[str, Any], self.users.batch_delete(user_ids))


# ===== Team SDK =====


class TeamSDK(AbstractSDKHandler):
    """SDK for team management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    teams: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for team management."""
        return {
            "teams": ResourceConfig(
                name="team",
                name_plural="teams",
                endpoint="/v1/team",
                required_fields=["name"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
            )
        }

    def create_team(
        self,
        name: str,
        description: Optional[str] = None,
        image_url: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new team."""
        team_data = {"name": name}
        if description:
            team_data["description"] = description
        if image_url:
            team_data["image_url"] = image_url
        if parent_id:
            team_data["parent_id"] = parent_id
        team_data.update(kwargs)
        return cast(Dict[str, Any], self.teams.create(team_data))

    def get_team(self, team_id: str) -> Dict[str, Any]:
        """Get a team by ID."""
        return cast(Dict[str, Any], self.teams.get(team_id))

    def list_teams(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List teams."""
        return cast(
            Dict[str, Any], self.teams.list(offset=offset, limit=limit, **filters)
        )

    def update_team(self, team_id: str, **updates) -> Dict[str, Any]:
        """Update a team."""
        return cast(Dict[str, Any], self.teams.update(team_id, updates))

    def delete_team(self, team_id: str) -> None:
        """Delete a team."""
        self.teams.delete(team_id)

    def get_team_users(
        self, team_id: str, include: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """The team's memberships, each with its user and role; ``include``
        may add ``team``. Members join through invitations."""
        params = {"include": ",".join(include)} if include else None
        return cast(
            Dict[str, Any],
            self._request("GET", f"/v1/team/{team_id}/user", query_params=params),
        )

    def update_user_role_in_team(
        self, team_id: str, user_id: str, role_id: str
    ) -> Dict[str, Any]:
        """Update a user's role in a team."""
        data = {"user_team": {"role_id": role_id}}
        return cast(
            Dict[str, Any],
            self._request("PATCH", f"/v1/team/{team_id}/user/{user_id}", data=data),
        )

    def search_teams(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search teams."""
        return cast(
            Dict[str, Any],
            self.teams.search(
                criteria,
                offset=offset,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            ),
        )

    def batch_update_teams(
        self, updates: Dict[str, Any], team_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update teams."""
        return cast(Dict[str, Any], self.teams.batch_update(updates, team_ids))

    def batch_delete_teams(self, team_ids: List[str]) -> Dict[str, Any]:
        """Batch delete teams."""
        return cast(Dict[str, Any], self.teams.batch_delete(team_ids))


# ===== Role SDK =====


class RoleSDK(AbstractSDKHandler):
    """SDK for role management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    roles: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for role management."""
        return {
            "roles": ResourceConfig(
                name="role",
                name_plural="roles",
                endpoint="/v1/role",
                required_fields=["name", "team_id"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=True,
                parent_resource="team",
            )
        }

    def create_role(
        self,
        name: str,
        team_id: str,
        friendly_name: Optional[str] = None,
        parent_id: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new role."""
        role_data = {
            "name": name,
            "team_id": team_id,
        }
        if friendly_name:
            role_data["friendly_name"] = friendly_name
        if parent_id:
            role_data["parent_id"] = parent_id
        role_data.update(kwargs)
        return cast(Dict[str, Any], self.roles.create(role_data))

    def get_role(self, role_id: str) -> Dict[str, Any]:
        """Get a role by ID."""
        return cast(Dict[str, Any], self.roles.get(role_id))

    def list_roles(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List roles."""
        return cast(
            Dict[str, Any], self.roles.list(offset=offset, limit=limit, **filters)
        )

    def list_team_roles(
        self, team_id: str, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List roles for a specific team."""
        params = {"offset": offset, "limit": limit, "team_id": team_id}
        params.update(filters)
        return cast(
            Dict[str, Any], self._request("GET", "/v1/role", query_params=params)
        )

    def update_role(self, role_id: str, **updates) -> Dict[str, Any]:
        """Update a role."""
        return cast(Dict[str, Any], self.roles.update(role_id, updates))

    def delete_role(self, role_id: str) -> None:
        """Delete a role."""
        self.roles.delete(role_id)

    def search_roles(
        self,
        criteria: Dict[str, Any],
        team_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search roles."""
        search_criteria = criteria.copy()
        if team_id:
            search_criteria["team_id"] = team_id
        return cast(
            Dict[str, Any],
            self.roles.search(
                search_criteria,
                offset=offset,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            ),
        )

    def batch_update_roles(
        self, updates: Dict[str, Any], role_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update roles."""
        return cast(Dict[str, Any], self.roles.batch_update(updates, role_ids))

    def batch_delete_roles(self, role_ids: List[str]) -> Dict[str, Any]:
        """Batch delete roles."""
        return cast(Dict[str, Any], self.roles.batch_delete(role_ids))


# ===== User Team SDK =====


class UserTeamSDK(AbstractSDKHandler):
    """SDK for user-team relationship management.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    user_teams: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for user-team management."""
        return {
            "user_teams": ResourceConfig(
                name="user_team",
                name_plural="user_teams",
                endpoint="/v1/user_team",
                required_fields=["user_id", "team_id", "role_id"],
                supports_search=True,
                supports_batch=True,
            )
        }

    def create_user_team(
        self, user_id: str, team_id: str, role_id: str, **kwargs
    ) -> Dict[str, Any]:
        """Create a user-team relationship."""
        user_team_data = {
            "user_id": user_id,
            "team_id": team_id,
            "role_id": role_id,
        }
        user_team_data.update(kwargs)
        return cast(Dict[str, Any], self.user_teams.create(user_team_data))

    def get_user_team(self, user_team_id: str) -> Dict[str, Any]:
        """Get a user-team relationship by ID."""
        return cast(Dict[str, Any], self.user_teams.get(user_team_id))

    def list_user_teams(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List user-team relationships."""
        return cast(
            Dict[str, Any], self.user_teams.list(offset=offset, limit=limit, **filters)
        )

    def update_user_team(self, user_team_id: str, **updates) -> Dict[str, Any]:
        """Update a user-team relationship."""
        return cast(Dict[str, Any], self.user_teams.update(user_team_id, updates))

    def delete_user_team(self, user_team_id: str) -> None:
        """Delete a user-team relationship."""
        self.user_teams.delete(user_team_id)


# ===== Invitation SDK =====


class InvitationSDK(AbstractSDKHandler):
    """SDK for invitation management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    invitations: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for invitation management."""
        return {
            "invitations": ResourceConfig(
                name="invitation",
                name_plural="invitations",
                endpoint="/v1/invitation",
                required_fields=["team_id", "role_id"],
                supports_search=True,
                supports_batch=True,
                parent_resource="team",
            )
        }

    def create_invitation(
        self,
        team_id: str,
        role_id: str,
        email: Optional[str] = None,
        max_uses: int = 1,
        **kwargs,
    ) -> Dict[str, Any]:
        """Create a new invitation."""
        invitation_data = {
            "team_id": team_id,
            "role_id": role_id,
            "max_uses": max_uses,
        }
        if email:
            invitation_data["email"] = email
        invitation_data.update(kwargs)
        return cast(Dict[str, Any], self.invitations.create(invitation_data))

    def get_invitation(self, invitation_id: str) -> Dict[str, Any]:
        """Get an invitation by ID."""
        return cast(Dict[str, Any], self.invitations.get(invitation_id))

    def list_invitations(
        self,
        team_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        **filters,
    ) -> Dict[str, Any]:
        """List invitations."""
        list_filters = filters.copy()
        if team_id:
            list_filters["team_id"] = team_id
        return cast(
            Dict[str, Any],
            self.invitations.list(offset=offset, limit=limit, **list_filters),
        )

    def update_invitation(self, invitation_id: str, **updates) -> Dict[str, Any]:
        """Update an invitation."""
        return cast(Dict[str, Any], self.invitations.update(invitation_id, updates))

    def delete_invitation(self, invitation_id: str) -> None:
        """Delete an invitation."""
        self.invitations.delete(invitation_id)

    def revoke_all_invitations(self, team_id: str) -> None:
        """Revoke all invitations for a team."""
        self._request("DELETE", f"/v1/team/{team_id}/invitation")

    def accept_invitation(
        self,
        invitation_id: str,
        invitation_code: Optional[str] = None,
        invitee_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Accept an invitation with its code or the caller's invitee id."""
        return self._answer_invitation(
            invitation_id, invitation_code, invitee_id, action="accept"
        )

    def decline_invitation(
        self,
        invitation_id: str,
        invitation_code: Optional[str] = None,
        invitee_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Decline an invitation with its code or the caller's invitee id."""
        return self._answer_invitation(
            invitation_id, invitation_code, invitee_id, action="decline"
        )

    def _answer_invitation(
        self,
        invitation_id: str,
        invitation_code: Optional[str],
        invitee_id: Optional[str],
        action: str,
    ) -> Dict[str, Any]:
        answer: Dict[str, str] = {"action": action}
        if invitation_code:
            answer["invitation_code"] = invitation_code
        if invitee_id:
            answer["invitee_id"] = invitee_id
        return cast(
            Dict[str, Any],
            self._request(
                "PATCH",
                f"/v1/invitation/{invitation_id}",
                data={"invitation": answer},
            ),
        )

    def batch_update_invitations(
        self, updates: Dict[str, Any], invitation_ids: List[str]
    ) -> Dict[str, Any]:
        """Batch update invitations."""
        return cast(
            Dict[str, Any], self.invitations.batch_update(updates, invitation_ids)
        )

    def batch_delete_invitations(self, invitation_ids: List[str]) -> Dict[str, Any]:
        """Batch delete invitations."""
        return cast(Dict[str, Any], self.invitations.batch_delete(invitation_ids))


# ===== Session SDK =====


class SessionSDK(AbstractSDKHandler):
    """SDK for session management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    sessions: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for session management."""
        return {
            "sessions": ResourceConfig(
                name="session",
                name_plural="sessions",
                endpoint="/v1/session",
                supports_search=False,
                supports_batch=False,
            )
        }

    def get_sessions(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """Get current user's sessions."""
        return cast(
            Dict[str, Any], self.sessions.list(offset=offset, limit=limit, **filters)
        )

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get a session by ID."""
        return cast(Dict[str, Any], self.sessions.get(session_id))

    def delete_session(self, session_id: str) -> None:
        """Delete a session."""
        self.sessions.delete(session_id)

    def list_user_sessions(
        self, user_id: str, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List sessions for a specific user."""
        params = {"offset": offset, "limit": limit}
        params.update(filters)
        return cast(
            Dict[str, Any],
            self._request("GET", f"/v1/user/{user_id}/session", query_params=params),
        )

    def get_user_session(self, user_id: str, session_id: str) -> Dict[str, Any]:
        """Get a specific user's session."""
        return cast(
            Dict[str, Any],
            self._request("GET", f"/v1/user/{user_id}/session/{session_id}"),
        )

    def revoke_all_user_sessions(self, user_id: str) -> None:
        """Revoke all sessions for a specific user."""
        self._request("DELETE", f"/v1/user/{user_id}/session")


# ===== Notification SDK =====


class NotificationSDK(AbstractSDKHandler):
    """SDK for notification management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    notifications: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for notification management."""
        return {
            "notifications": ResourceConfig(
                name="notification",
                name_plural="notifications",
                endpoint="/v1/notifications",
                supports_search=False,
                supports_batch=False,
            )
        }

    def get_notifications(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """Get current user's notifications."""
        return cast(
            Dict[str, Any],
            self.notifications.list(offset=offset, limit=limit, **filters),
        )

    def get_notification(self, notification_id: str) -> Dict[str, Any]:
        """Get a notification by ID."""
        return cast(Dict[str, Any], self.notifications.get(notification_id))

    def mark_notification_read(self, user_notification_id: str) -> Dict[str, Any]:
        """Mark one of the caller's notifications read (the id is the
        user-notification's, the per-recipient row)."""
        return cast(
            Dict[str, Any],
            self._request(
                "PATCH", f"/v1/user-notifications/{user_notification_id}/read"
            ),
        )

    def delete_notification(self, notification_id: str) -> None:
        """Delete a notification."""
        self.notifications.delete(notification_id)


# ===== API Key SDK =====


class ApiKeySDK(AbstractSDKHandler):
    """SDK for API key management operations.

    Keys are issued and rotated, never created or edited directly: the raw
    key is returned exactly once, by ``issue_api_key`` or ``rotate_api_key``.
    Deleting a key revokes it.
    """

    api_keys: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for API key management."""
        return {
            "api_keys": ResourceConfig(
                name="api_key",
                name_plural="api_keys",
                endpoint=_API_KEYS_ENDPOINT,
                supports_search=True,
                supports_batch=False,
            )
        }

    def issue_api_key(
        self,
        name: str,
        team_id: Optional[str] = None,
        role_id: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Issue a key for the caller (or a team they administer); the
        response's ``key`` is the only time the raw value is returned."""
        request: Dict[str, Any] = {"name": name}
        for field, value in (
            ("team_id", team_id),
            ("role_id", role_id),
            ("expires_at", expires_at),
        ):
            if value is not None:
                request[field] = value
        return cast(
            Dict[str, Any],
            self._request("POST", f"{_API_KEYS_ENDPOINT}/issue", data=request),
        )

    def get_api_key(self, api_key_id: str) -> Dict[str, Any]:
        """Get an API key by ID."""
        return cast(Dict[str, Any], self.api_keys.get(api_key_id))

    def list_api_keys(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List API keys."""
        return cast(
            Dict[str, Any], self.api_keys.list(offset=offset, limit=limit, **filters)
        )

    def delete_api_key(self, api_key_id: str) -> None:
        """Revoke an API key."""
        self.api_keys.delete(api_key_id)

    def rotate_api_key(self, api_key_id: str) -> Dict[str, Any]:
        """Issue a replacement key and revoke this one; the response's
        ``key`` is the replacement's raw value, returned only here."""
        return cast(
            Dict[str, Any],
            self._request(
                "POST", f"{_API_KEYS_ENDPOINT}/rotate", data={"key_id": api_key_id}
            ),
        )


# ===== User Metadata SDK =====


class UserMetadataSDK(AbstractSDKHandler):
    """SDK for user metadata management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    user_metadata: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for user metadata management."""
        return {
            "user_metadata": ResourceConfig(
                name="metadata",
                name_plural="metadata",
                endpoint="/v1/user/{user_id}/metadata",
                required_fields=["key", "value"],
                supports_search=False,
                supports_batch=False,
                parent_resource="user",
            )
        }

    def create_user_metadata(
        self, user_id: str, key: str, value: str, **kwargs
    ) -> Dict[str, Any]:
        """Create user metadata."""
        metadata_data = {
            "user_id": user_id,
            "key": key,
            "value": value,
        }
        metadata_data.update(kwargs)
        return cast(Dict[str, Any], self.user_metadata.create(metadata_data))

    def get_user_metadata(self, metadata_id: str) -> Dict[str, Any]:
        """Get user metadata by ID."""
        return cast(Dict[str, Any], self.user_metadata.get(metadata_id))

    def list_user_metadata(
        self,
        user_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        **filters,
    ) -> Dict[str, Any]:
        """List user metadata."""
        list_filters = filters.copy()
        if user_id:
            list_filters["user_id"] = user_id
        return cast(
            Dict[str, Any],
            self.user_metadata.list(offset=offset, limit=limit, **list_filters),
        )

    def update_user_metadata(self, metadata_id: str, **updates) -> Dict[str, Any]:
        """Update user metadata."""
        return cast(Dict[str, Any], self.user_metadata.update(metadata_id, updates))

    def delete_user_metadata(self, metadata_id: str) -> None:
        """Delete user metadata."""
        self.user_metadata.delete(metadata_id)


# ===== Team Metadata SDK =====


class TeamMetadataSDK(AbstractSDKHandler):
    """SDK for team metadata management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    team_metadata: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for team metadata management."""
        return {
            "team_metadata": ResourceConfig(
                name="metadata",
                name_plural="metadata",
                endpoint="/v1/team/{team_id}/metadata",
                required_fields=["key", "value"],
                supports_search=False,
                supports_batch=False,
                parent_resource="team",
            )
        }

    def create_team_metadata(
        self, team_id: str, key: str, value: str, **kwargs
    ) -> Dict[str, Any]:
        """Create team metadata."""
        metadata_data = {
            "team_id": team_id,
            "key": key,
            "value": value,
        }
        metadata_data.update(kwargs)
        return cast(Dict[str, Any], self.team_metadata.create(metadata_data))

    def get_team_metadata(self, metadata_id: str) -> Dict[str, Any]:
        """Get team metadata by ID."""
        return cast(Dict[str, Any], self.team_metadata.get(metadata_id))

    def list_team_metadata(
        self,
        team_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        **filters,
    ) -> Dict[str, Any]:
        """List team metadata."""
        list_filters = filters.copy()
        if team_id:
            list_filters["team_id"] = team_id
        return cast(
            Dict[str, Any],
            self.team_metadata.list(offset=offset, limit=limit, **list_filters),
        )

    def update_team_metadata(self, metadata_id: str, **updates) -> Dict[str, Any]:
        """Update team metadata."""
        return cast(Dict[str, Any], self.team_metadata.update(metadata_id, updates))

    def delete_team_metadata(self, metadata_id: str) -> None:
        """Delete team metadata."""
        self.team_metadata.delete(metadata_id)


# ===== User Credential SDK =====


class UserCredentialSDK(AbstractSDKHandler):
    """SDK for user credential management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    user_credentials: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for user credential management."""
        return {
            "user_credentials": ResourceConfig(
                name="user_credential",
                name_plural="user_credentials",
                endpoint="/v1/user_credential",
                required_fields=["user_id", "credential_type", "credential_data"],
                supports_search=False,
                supports_batch=False,
                parent_resource="user",
            )
        }

    def create_user_credential(
        self, user_id: str, credential_type: str, credential_data: str, **kwargs
    ) -> Dict[str, Any]:
        """Create user credential."""
        credential_data_dict = {
            "user_id": user_id,
            "credential_type": credential_type,
            "credential_data": credential_data,
        }
        credential_data_dict.update(kwargs)
        return cast(Dict[str, Any], self.user_credentials.create(credential_data_dict))

    def get_user_credential(self, credential_id: str) -> Dict[str, Any]:
        """Get user credential by ID."""
        return cast(Dict[str, Any], self.user_credentials.get(credential_id))

    def list_user_credentials(
        self,
        user_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        **filters,
    ) -> Dict[str, Any]:
        """List user credentials."""
        list_filters = filters.copy()
        if user_id:
            list_filters["user_id"] = user_id
        return cast(
            Dict[str, Any],
            self.user_credentials.list(offset=offset, limit=limit, **list_filters),
        )

    def update_user_credential(self, credential_id: str, **updates) -> Dict[str, Any]:
        """Update user credential."""
        return cast(
            Dict[str, Any], self.user_credentials.update(credential_id, updates)
        )

    def delete_user_credential(self, credential_id: str) -> None:
        """Delete user credential."""
        self.user_credentials.delete(credential_id)


# ===== Recovery Question SDK =====


class RecoveryQuestionSDK(AbstractSDKHandler):
    """SDK for recovery question management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    recovery_questions: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for recovery question management."""
        return {
            "recovery_questions": ResourceConfig(
                name="user_recovery_question",
                name_plural="user_recovery_questions",
                endpoint="/v1/user/recovery-questions",
                required_fields=["question", "answer"],
                supports_search=False,
                supports_batch=False,
                parent_resource="user",
            )
        }

    def create_recovery_question(
        self, user_id: str, question: str, answer_hash: str, **kwargs
    ) -> Dict[str, Any]:
        """Create recovery question."""
        question_data = {
            "user_id": user_id,
            "question": question,
            "answer_hash": answer_hash,
        }
        question_data.update(kwargs)
        return cast(Dict[str, Any], self.recovery_questions.create(question_data))

    def get_recovery_question(self, question_id: str) -> Dict[str, Any]:
        """Get recovery question by ID."""
        return cast(Dict[str, Any], self.recovery_questions.get(question_id))

    def update_recovery_question(self, question_id: str, **updates) -> Dict[str, Any]:
        """Update recovery question."""
        return cast(
            Dict[str, Any], self.recovery_questions.update(question_id, updates)
        )

    def delete_recovery_question(self, question_id: str) -> None:
        """Delete recovery question."""
        self.recovery_questions.delete(question_id)


# ===== Failed Login SDK =====


class FailedLoginSDK(AbstractSDKHandler):
    """SDK for failed login tracking operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    failed_logins: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for failed login management."""
        return {
            "failed_logins": ResourceConfig(
                name="failed_login_attempt",
                name_plural="failed_login_attempts",
                endpoint="/v1/admin/failed-logins",
                required_fields=["user_id"],
                supports_search=True,
                supports_batch=False,
                parent_resource="user",
            )
        }

    def create_failed_login(
        self, user_id: str, ip_address: str, user_agent: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """Create failed login record."""
        failed_login_data = {
            "user_id": user_id,
            "ip_address": ip_address,
        }
        if user_agent:
            failed_login_data["user_agent"] = user_agent
        failed_login_data.update(kwargs)
        return cast(Dict[str, Any], self.failed_logins.create(failed_login_data))

    def get_failed_login(self, failed_login_id: str) -> Dict[str, Any]:
        """Get failed login by ID."""
        return cast(Dict[str, Any], self.failed_logins.get(failed_login_id))

    def list_failed_logins(
        self,
        user_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 100,
        **filters,
    ) -> Dict[str, Any]:
        """List failed logins."""
        list_filters = filters.copy()
        if user_id:
            list_filters["user_id"] = user_id
        return cast(
            Dict[str, Any],
            self.failed_logins.list(offset=offset, limit=limit, **list_filters),
        )

    def delete_failed_login(self, failed_login_id: str) -> None:
        """Delete failed login record."""
        self.failed_logins.delete(failed_login_id)


# ===== Permission SDK =====


class PermissionSDK(AbstractSDKHandler):
    """SDK for permission management operations.

    Uses configuration-driven resource management for standardized CRUD operations.
    """

    permissions: ResourceManager

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Configure resources for permission management."""
        return {
            "permissions": ResourceConfig(
                name="permission",
                name_plural="permissions",
                endpoint="/v1/permission",
                required_fields=["name"],
                unique_fields=["name"],
                supports_search=True,
                supports_batch=False,
            )
        }

    def create_permission(
        self, name: str, description: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """Create a new permission."""
        permission_data = {"name": name}
        if description:
            permission_data["description"] = description
        permission_data.update(kwargs)
        return cast(Dict[str, Any], self.permissions.create(permission_data))

    def get_permission(self, permission_id: str) -> Dict[str, Any]:
        """Get a permission by ID."""
        return cast(Dict[str, Any], self.permissions.get(permission_id))

    def list_permissions(
        self, offset: int = 0, limit: int = 100, **filters
    ) -> Dict[str, Any]:
        """List permissions."""
        return cast(
            Dict[str, Any], self.permissions.list(offset=offset, limit=limit, **filters)
        )

    def update_permission(self, permission_id: str, **updates) -> Dict[str, Any]:
        """Update a permission."""
        return cast(Dict[str, Any], self.permissions.update(permission_id, updates))

    def delete_permission(self, permission_id: str) -> None:
        """Delete a permission."""
        self.permissions.delete(permission_id)

    def search_permissions(
        self,
        criteria: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        """Search permissions."""
        return cast(
            Dict[str, Any],
            self.permissions.search(
                criteria,
                offset=offset,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
            ),
        )


# ===== Main Auth SDK (Composite) =====


class AuthSDK(AbstractSDKHandler):
    """Composite SDK for all authentication-related operations.

    This SDK combines all authentication functionality into a single interface
    using configuration-driven resource management.
    """

    users: ResourceManager
    teams: ResourceManager
    roles: ResourceManager
    user_teams: ResourceManager
    invitations: ResourceManager
    sessions: ResourceManager
    notifications: ResourceManager
    api_keys: ResourceManager
    user_metadata: ResourceManager
    team_metadata: ResourceManager
    user_credentials: ResourceManager
    recovery_questions: ResourceManager
    failed_logins: ResourceManager
    permissions: ResourceManager

    # The resource SDKs this composite exposes; each owns its resource config.
    _PARTS: tuple = (
        UserSDK,
        TeamSDK,
        RoleSDK,
        UserTeamSDK,
        InvitationSDK,
        SessionSDK,
        NotificationSDK,
        ApiKeySDK,
        UserMetadataSDK,
        TeamMetadataSDK,
        UserCredentialSDK,
        RecoveryQuestionSDK,
        FailedLoginSDK,
        PermissionSDK,
    )

    def _configure_resources(self) -> Dict[str, ResourceConfig]:
        """Every authentication resource, as its own SDK configures it."""
        configs: Dict[str, ResourceConfig] = {}
        for part in self._PARTS:
            configs.update(part._configure_resources(self))
        return configs

    def login(self, email: str, password: str) -> Dict[str, Any]:
        """Login with email and password credentials."""
        auth_string = f"{email}:{password}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        headers = {"Authorization": f"Basic {encoded_auth}"}

        try:
            response = self._request(
                "POST",
                "/v1/user/authorize",
                headers=headers,
            )
            if "token" in response:
                self.token = response["token"]
            return cast(Dict[str, Any], response)
        except Exception as e:
            raise AuthenticationError(f"Login failed: {str(e)}")

    def logout(self) -> None:
        """Logout the current user and invalidate the token."""
        try:
            self._request("POST", "/v1/user/logout")
            self.token = None
        except Exception as e:
            self.token = None
            raise AuthenticationError(f"Logout failed: {str(e)}")

    def verify_token(self) -> bool:
        """Verify if the current token is valid."""
        try:
            self._request("GET", "/v1")
            return True
        except SDKException as e:
            logger.warning(f"Token verification failed: {e}", exc_info=True)
            return False

    def register(
        self,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        display_name: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Register a new user account."""
        user_data = {
            "email": email,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        }
        if display_name:
            user_data["display_name"] = display_name
        user_data.update(kwargs)
        return cast(Dict[str, Any], self.users.create(user_data))

    def change_password(
        self, current_password: str, new_password: str
    ) -> Dict[str, Any]:
        """Change the current user's password."""
        data = {
            "current_password": current_password,
            "new_password": new_password,
        }
        return cast(Dict[str, Any], self._request("PATCH", "/v1/user", data=data))

    def get_current_user(self) -> Dict[str, Any]:
        """Get the current authenticated user's information."""
        return cast(Dict[str, Any], self._request("GET", "/v1/user"))

    def update_current_user(self, **updates) -> Dict[str, Any]:
        """Update the current user's profile."""
        data = {"user": updates}
        return cast(Dict[str, Any], self._request("PUT", "/v1/user", data=data))

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
