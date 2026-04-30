# API Endpoint Schema

This document describes the current API endpoint schema as implemented in the codebase. The schema follows RESTful patterns with consistent authentication and data formats.

**IMPORTANT**: This system has migrated from manual EP_*.py files to automatic router generation from BLL managers using the `RouterMixin` pattern. The endpoints listed below are generated automatically by the model registry from managers in the `logic/` directory and extensions.

## General Patterns

- **URL Parameters**: Braces in a URL (`{param}`) indicate path parameters extracted from the URL slug
- **Non-standard implementations**: Flagged with an exclamation mark (!)
- **Standard CRUD Operations**: Most entities support POST (Create), GET (List/Read), PUT (Update), DELETE (Delete), and POST /search
- **Field Selection**: All GET endpoints support a `fields` parameter to specify which fields to include in responses
- **Batch Operations**: Support batch operations via bulk endpoints with specific request formats:
  - **POST**: `{entity_name_plural: [{}, {}]}` or `{entity_name: {}}`
  - **PUT**: `{entity_name: {update_data}, target_ids: ["id1", "id2"]}`
  - **DELETE**: `{target_ids: ["id1", "id2"]}`

## Current Implementation Status

The following endpoints are currently implemented and tested based on the BLL managers found in the codebase:

### Core Authentication Endpoints (BLL_Auth)
- **User Management**: User registration, authentication, profile management
- **Team Management**: Team creation, membership, role assignment
- **Session Management**: JWT session handling and revocation
- **Invitation System**: Team invitations and acceptance
- **Role Management**: Team-based role hierarchy

### Extensions Domain (BLL_Extensions)
- **Extension Management**: System extension registration and management
- **Ability Management**: Extension capability definitions

### Providers Domain (BLL_Providers)
- **Provider Management**: External service provider configuration
- **Provider Extensions**: Provider-specific extension mappings
- **Provider Instances**: Individual provider instances and settings
- **Rotation Management**: Provider rotation and failover

## Authentication Domain

### User Router (UserManager)

**Current Authentication**: JWT for protected operations, None/Basic for registration/login

- **Create (Register) a User** [None]
    - `POST /v1/user`
- **Login a User** [Basic]
    - `!POST /v1/user/authorize`
        - Authenticates user with credentials and returns JWT token with session creation
- **Get Current User** [JWT]
    - `!GET /v1/user`
        - Gets the requesting user (singular), not a list
- **Get a User by ID** [JWT]
    - `GET /v1/user/{id}`
- **Update Current User** [JWT]
    - `!PUT /v1/user`
        - Updates the requesting user (singular), not bulk processing
- **Delete Current User** [JWT]
    - `!DELETE /v1/user`
        - Deletes the requesting user (singular), not bulk processing
- **Change User Password** [JWT]
    - `!PATCH /v1/user`
        - Changes the current user's password
- **Verify Authorization** [JWT/API Key]
    - `!GET /v1`
        - Verifies JWT or API Key validity, returns 204 if valid, 401 if not

### User Session Management [JWT]

- **List All Sessions**
    - `GET /v1/session`
- **Get a Session**
    - `GET /v1/session/{id}`
- **Revoke Session**
    - `DELETE /v1/session/{id}`
- **List User Sessions**
    - `GET /v1/user/{user_id}/session`
- **Get User Session**
    - `GET /v1/user/{user_id}/session/{id}`
- **Revoke All User Sessions**
    - `DELETE /v1/user/{user_id}/session`

### Team Router (TeamManager) [JWT]

- **Create a Team**
    - `POST /v1/team`
- **Get a Team**
    - `GET /v1/team/{id}`
- **List Teams**
    - `GET /v1/team`
- **Update a Team**
    - `PUT /v1/team/{id}`
    - `PUT /v1/team` (batch updates)
- **Delete a Team**
    - `DELETE /v1/team/{id}`
    - `DELETE /v1/team` (batch deletes)
- **Get Team Users** [JWT]
    - `!GET /v1/team/{id}/user`
        - Retrieves users in a team with UserTeam data
- **Update User Role in Team** [JWT]
    - `!PATCH /v1/team/{team_id}/user/{user_id}`
        - Updates user role within a team
- **Search Teams**
    - `POST /v1/team/search`

### Team Metadata Router (TeamMetadataManager) [JWT]

- **Create Team Metadata**
    - `POST /v1/team/{team_id}/metadata`
- **Get Team Metadata**
    - `GET /v1/team/{team_id}/metadata/{id}`
- **List Team Metadata**
    - `GET /v1/team/{team_id}/metadata`
- **Update Team Metadata**
    - `PUT /v1/team/{team_id}/metadata/{id}`
- **Delete Team Metadata**
    - `DELETE /v1/team/{team_id}/metadata/{id}`

### User Metadata Router (UserMetadataManager) [JWT]

- **Create User Metadata**
    - `POST /v1/user/{user_id}/metadata`
- **Get User Metadata**
    - `GET /v1/user/{user_id}/metadata/{id}`
- **List User Metadata**
    - `GET /v1/user/{user_id}/metadata`
- **Update User Metadata**
    - `PUT /v1/user/{user_id}/metadata/{id}`
- **Delete User Metadata**
    - `DELETE /v1/user/{user_id}/metadata/{id}`

### User Team Router (UserTeamManager) [JWT]

- **Create User Team Association**
    - `POST /v1/user/{user_id}/user_team`
- **Get User Team Association**
    - `GET /v1/user/{user_id}/user_team/{id}`
- **List User Team Associations**
    - `GET /v1/user/{user_id}/user_team`
- **Update User Team Association**
    - `PUT /v1/user/{user_id}/user_team/{id}`
- **Delete User Team Association**
    - `DELETE /v1/user/{user_id}/user_team/{id}`

### Role Router (RoleManager) [JWT Read, API Key Write]

- **Create a Role**
    - `POST /v1/team/{team_id}/role`
- **Get a Role**
    - `GET /v1/role/{id}`
- **List Roles**
    - `GET /v1/team/{team_id}/role`
- **Update a Role**
    - `PUT /v1/role/{id}`
- **Delete a Role**
    - `DELETE /v1/role/{id}`
- **Search Roles**
    - `POST /v1/team/{team_id}/role/search`

### Invitation Router (InvitationManager) [JWT]

- **Create an Invitation**
    - `POST /v1/invitation`
    - `POST /v1/team/{team_id}/invitation`
- **Get an Invitation**
    - `GET /v1/invitation/{id}`
- **List Invitations**
    - `GET /v1/invitation`
    - `GET /v1/team/{team_id}/invitation`
- **Update an Invitation**
    - `PUT /v1/invitation/{id}`
    - `PUT /v1/invitation` (batch updates)
- **Delete an Invitation**
    - `DELETE /v1/invitation/{id}`
    - `DELETE /v1/invitation` (batch deletes)
    - `!DELETE /v1/team/{team_id}/invitation` (revokes ALL open invitations)
- **Search Invitations**
    - `POST /v1/invitation/search`
- **Accept an Invitation** [JWT]
    - `!PATCH /v1/invitation/{id}`
        - Accepts invitation with either invitation_code or invitee_id

## Extensions Domain

### Extension Router (ExtensionManager) [JWT Read, API Key Write]

- **Create an Extension**
    - `POST /v1/extension`
- **Get an Extension**
    - `GET /v1/extension/{id}`
- **List Extensions**
    - `GET /v1/extension`
- **Update an Extension**
    - `PUT /v1/extension/{id}`
    - `PUT /v1/extension` (batch updates)
- **Delete an Extension**
    - `DELETE /v1/extension/{id}`
    - `DELETE /v1/extension` (batch deletes)
- **Search Extensions**
    - `POST /v1/extension/search`

### Ability Router (AbilityManager) [JWT Read, API Key Write]

- **Create an Ability**
    - `POST /v1/ability`
- **Get an Ability**
    - `GET /v1/ability/{id}`
- **List Abilities**
    - `GET /v1/ability`
- **Update an Ability**
    - `PUT /v1/ability/{id}`
    - `PUT /v1/ability` (batch updates)
- **Delete an Ability**
    - `DELETE /v1/ability/{id}`
    - `DELETE /v1/ability` (batch deletes)
- **Search Abilities**
    - `POST /v1/ability/search`

## Providers Domain

### Provider Extension Router (ProviderExtensionManager) [JWT Read, API Key Write]

- **Create a Provider Extension**
    - `POST /v1/provider/extension`
- **Get a Provider Extension**
    - `GET /v1/provider/extension/{id}`
- **List Provider Extensions**
    - `GET /v1/provider/extension`
- **Update a Provider Extension**
    - `PUT /v1/provider/extension/{id}`
    - `PUT /v1/provider/extension` (batch updates)
- **Delete a Provider Extension**
    - `DELETE /v1/provider/extension/{id}`
    - `DELETE /v1/provider/extension` (batch deletes)
- **Search Provider Extensions**
    - `POST /v1/provider/extension/search`

### Provider Router (ProviderManager) [JWT Read, API Key Write]

- **Create a Provider**
    - `POST /v1/provider`
- **Get a Provider**
    - `GET /v1/provider/{id}`
- **List Providers**
    - `GET /v1/provider`
- **Update a Provider**
    - `PUT /v1/provider/{id}`
    - `PUT /v1/provider` (batch updates)
- **Delete a Provider**
    - `DELETE /v1/provider/{id}`
    - `DELETE /v1/provider` (batch deletes)
- **Search Providers**
    - `POST /v1/provider/search`

### Provider Extension Ability Router (ProviderExtensionAbilityManager) [JWT Read, API Key Write]

- **Create Provider Extension Ability**
    - `POST /v1/extension/ability/provider`
- **Get Provider Extension Ability**
    - `GET /v1/extension/ability/provider/{id}`
- **List Provider Extension Abilities**
    - `GET /v1/extension/ability/provider`
- **Update Provider Extension Ability**
    - `PUT /v1/extension/ability/provider/{id}`
    - `PUT /v1/extension/ability/provider` (batch updates)
- **Delete Provider Extension Ability**
    - `DELETE /v1/extension/ability/provider/{id}`
    - `DELETE /v1/extension/ability/provider` (batch deletes)
  
### Provider Instance Router (ProviderInstanceManager) [JWT]

Available both as nested and standalone routes:

**Nested Routes:**
- **Create a Provider Instance**
    - `POST /v1/provider/{provider_id}/provider/instance`
- **Get a Provider Instance**
    - `GET /v1/provider/{provider_id}/provider/instance/{id}`
- **List Provider Instances**
    - `GET /v1/provider/{provider_id}/provider/instance`

**Standalone Routes:**
- **Create a Provider Instance**
    - `POST /v1/provider/instance`
- **Get a Provider Instance**
    - `GET /v1/provider/instance/{id}`
- **List Provider Instances**
    - `GET /v1/provider/instance`
- **Update a Provider Instance**
    - `PUT /v1/provider/instance/{id}`
    - `PUT /v1/provider/instance` (batch updates)
- **Delete a Provider Instance**
    - `DELETE /v1/provider/instance/{id}`
    - `DELETE /v1/provider/instance` (batch deletes)
- **Search Provider Instances**
    - `POST /v1/provider/instance/search`

### Provider Instance Settings Router (ProviderInstanceSettingManager) [JWT]

Available both as nested and standalone routes:

**Nested Routes:**
- **Create Provider Instance Setting**
    - `POST /v1/provider/instance/{instance_id}/setting`
- **Get Provider Instance Setting**
    - `GET /v1/provider/instance/{instance_id}/setting/{id}`
- **List Provider Instance Settings**
    - `GET /v1/provider/instance/{instance_id}/setting`

**Standalone Routes:**
- **Create Provider Instance Setting**
    - `POST /v1/provider/instance/setting`
- **Get Provider Instance Setting**
    - `GET /v1/provider/instance/setting/{id}`
- **List Provider Instance Settings**
    - `GET /v1/provider/instance/setting`
- **Update Provider Instance Setting**
    - `PUT /v1/provider/instance/setting/{id}`
    - `PUT /v1/provider/instance/setting` (batch updates)
- **Delete Provider Instance Setting**
    - `DELETE /v1/provider/instance/setting/{id}`
    - `DELETE /v1/provider/instance/setting` (batch deletes)
- **Search Provider Instance Settings**
    - `POST /v1/provider/instance/setting/search`

### Provider Instance Usage Router (ProviderInstanceUsageManager) [JWT Read, API Key Write]

- **Create Provider Instance Usage**
    - `POST /v1/provider/instance/usage`
- **Get Provider Instance Usage**
    - `GET /v1/provider/instance/usage/{id}`
- **List Provider Instance Usage**
    - `GET /v1/provider/instance/usage`
- **Update Provider Instance Usage**
    - `PUT /v1/provider/instance/usage/{id}`
    - `PUT /v1/provider/instance/usage` (batch updates)
- **Delete Provider Instance Usage**
    - `DELETE /v1/provider/instance/usage/{id}`
    - `DELETE /v1/provider/instance/usage` (batch deletes)

### Rotation Router (RotationManager) [JWT]

- **Create a Rotation**
    - `POST /v1/rotation`
- **Get a Rotation**
    - `GET /v1/rotation/{id}`
- **List Rotations**
    - `GET /v1/rotation`
- **Update a Rotation**
    - `PUT /v1/rotation/{id}`
    - `PUT /v1/rotation` (batch updates)
- **Delete a Rotation**
    - `DELETE /v1/rotation/{id}`
    - `DELETE /v1/rotation` (batch deletes)
- **Search Rotations**
    - `POST /v1/rotation/search`

### Rotation Provider Instance Router (RotationProviderInstanceManager) [JWT]

Available both as nested and standalone routes:

**Nested Routes:**
- **Create Rotation Provider**
    - `POST /v1/rotation/{rotation_id}/provider/instance`
- **List Rotation Providers**
    - `GET /v1/rotation/{rotation_id}/provider/instance`

**Standalone Routes:**
- **Create Rotation Provider**
    - `POST /v1/rotation/provider/instance`
- **Get Rotation Provider**
    - `GET /v1/rotation/provider/instance/{id}`
- **List Rotation Providers**
    - `GET /v1/rotation/provider/instance`
- **Update Rotation Provider**
    - `PUT /v1/rotation/provider/instance/{id}`
    - `PUT /v1/rotation/provider/instance` (batch updates)
- **Delete Rotation Provider**
    - `DELETE /v1/rotation/provider/instance/{id}`
    - `DELETE /v1/rotation/provider/instance` (batch deletes)
    - 
### Provider Instance Extension Ability Router (ProviderInstanceExtensionAbilityManager) [JWT]

- **Create Provider Extension Ability**
    - `POST /v1/extension/ability/provider/instance`
- **Get Provider Extension Ability**
    - `GET /v1/extension/ability/provider/instance/{id}`
- **List Provider Extension Abilities**
    - `GET /v1/extension/ability/provider/instance`
- **Update Provider Extension Ability**
    - `PUT /v1/extension/ability/provider/instance/{id}`
    - `PUT /v1/extension/ability/provider/instance` (batch updates)
- **Delete Provider Extension Ability**
    - `DELETE /v1/extension/ability/provider/instance/{id}`
    - `DELETE /v1/extension/ability/provider/instance` (batch deletes)

## Authentication Types

### JWT Authentication [JWT]
- **Usage**: Standard user operations, requires valid JWT token
- **Header**: `Authorization: Bearer <token>`
- **Scope**: User and team-scoped operations

### API Key Authentication [API Key]
- **Usage**: System operations, administrative tasks
- **Header**: `Authorization: <api_key>`
- **Scope**: System entities, write operations on protected resources

### Basic Authentication [Basic]
- **Usage**: Login operations only
- **Header**: `Authorization: Basic <base64(email:password)>`
- **Scope**: User authentication

### No Authentication [None]
- **Usage**: Public endpoints (registration, health checks)
- **Header**: None required
- **Scope**: Public operations

## Request/Response Formats

### Standard Response Format

```json
{
    "resource_name": {
        "id": "uuid",
        "field1": "value1",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z"
    }
}
```

### List Response Format

```json
{
    "resource_name_plural": [
        {
            "id": "uuid1",
            "field1": "value1"
        },
        {
            "id": "uuid2", 
            "field1": "value2"
        }
    ]
}
```

### Search Request Format

```json
{
    "resource_name": {
        "field1": "search_value",
        "field2": "another_value"
    }
}
```

### Batch Create Request Format

```json
{
    "resource_name_plural": [
        {"field1": "value1"},
        {"field1": "value2"}
    ]
}
```

### Batch Update Request Format

```json
{
    "resource_name": {"field1": "new_value"},
    "target_ids": ["id1", "id2", "id3"]
}
```

### Batch Delete Request Format

```json
{
    "target_ids": ["id1", "id2", "id3"]
}
```

## Error Response Format

```json
{
    "detail": "Error message",
    "status_code": 400,
    "errors": [
        {
            "field": "field_name",
            "message": "Field error message"
        }
    ]
}
```

## Query Parameters

### Common Parameters

- **fields**: Comma-separated list of fields to include in response
- **offset**: Number of items to skip for pagination (default: 0)
- **limit**: Maximum number of items to return (default: 100, max: 1000)
- **sort_by**: Field name to sort by
- **sort_order**: Sort direction (`asc` or `desc`)

### Authentication Parameters

- **target_user_id**: For admin operations targeting specific users
- **target_team_id**: For admin operations targeting specific teams

## Current Implementation Notes

1. **RouterMixin Generation**: All endpoints are generated automatically from BLL managers using the `RouterMixin` pattern
2. **System Entity Auto-Configuration**: Resources with `BaseModel.is_system_entity=True` automatically require API key authentication for write operations
3. **Model Registry Integration**: Routers are built automatically by the model registry during application startup
4. **Authentication Detection**: Auth requirements are determined by manager ClassVars and system entity detection
5. **Batch Operation Support**: All resources support batch create, update, and delete operations
6. **Search Optimization**: Search operations automatically filter to relevant fields for better usability
7. **Example Generation**: All endpoints include automatically generated examples in OpenAPI documentation
8. **Custom Route Support**: Managers can define custom routes using `@custom_route` and `@static_route` decorators
9. **Test Coverage**: All endpoints are tested using the `AbstractEPTest` framework with manager-specific test classes