# BLL Schema Documentation

## Overview
The Business Logic Layer (BLL) defines the application's data schema through Pydantic models that automatically generate corresponding SQLAlchemy database models. This approach provides type safety, validation, and API schema generation while maintaining clean separation of concerns.

## Architecture Pattern

### BLL-First Schema Design
The schema is defined in Business Logic Layer files using Pydantic models with special metadata that drives SQLAlchemy generation:

```python
class UserModel(
    ApplicationModel.Optional,
    UpdateMixinModel.Optional,
    ImageMixinModel.Optional,
    
    
    metaclass=ModelMeta,
):
    model_config = {"extra": "ignore", "populate_by_name": True}
    email: Optional[str] = Field(description="User's email address")
    username: Optional[str] = Field(description="User's username")
    display_name: Optional[str] = Field(description="User's display name")
    first_name: Optional[str] = Field(description="User's first name")
    last_name: Optional[str] = Field(description="User's last name")
    mfa_count: Optional[int] = Field(description="Number of MFA verifications required")
    active: Optional[bool] = Field(default=True, description="Whether the user is active")
    timezone: Optional[str] = Field(description="User's timezone")
    language: Optional[str] = Field(description="User's language")
    
    # Database metadata for SQLAlchemy generation
    table_comment: ClassVar[str] = "Core user accounts for authentication and identity management"
    seed_data: ClassVar[List[Dict[str, Any]]] = [
        {
            "id": env("ROOT_ID"),
            "email": f"root@{extract_base_domain(env('APP_URI'))}",
            "timezone": "UTC",
            "language": "en",
        },
        {
            "id": env("SYSTEM_ID"),
            "email": f"system@{extract_base_domain(env('APP_URI'))}",
            "timezone": "UTC",
            "language": "en",
        },
        {
            "id": env("TEMPLATE_ID"),
            "email": f"template@{extract_base_domain(env('APP_URI'))}",
            "timezone": "UTC",
            "language": "en",
        },
    ]
    
    # Access the generated SQLAlchemy model
    # User = UserModel.DB(model_registry.DB.manager.Base)  # Pass declarative base
```

### DatabaseMixin Pattern
All BLL models that need database representation inherit from `DatabaseMixin`, which provides:
- `.DB(declarative_base)` method that returns the generated SQLAlchemy model
- Automatic table creation from Pydantic field definitions
- Type mapping from Pydantic types to SQLAlchemy columns
- Relationship generation from reference model patterns
- Integration with DatabaseManager instances

### Schema Generation Process
1. **Pydantic Model Definition**: BLL models define fields, validation, and metadata
2. **SQLAlchemy Generation**: `Pydantic2SQLAlchemy.py` automatically creates database models
3. **Table Creation**: Generated models create database tables with proper constraints
4. **Manager Integration**: BLL managers use generated models for database operations

## Core Entity Structure

### Base Mixin Models
**ApplicationModel**: Core functionality for all entities
- `id`: Optional[str] - Primary key (UUID/String)
- `created_at`: Optional[datetime] - Creation timestamp
- `created_by_user_id`: Optional[str] - Creator reference

**UpdateMixinModel**: Extends ApplicationModel with update/delete support
- `updated_at`: Optional[datetime] - Last update timestamp  
- `updated_by_user_id`: Optional[str] - Last updater reference
- `deleted_at`: Optional[datetime] - Soft delete timestamp (NULL = active)
- `deleted_by_user_id`: Optional[str] - Deleter reference

**ParentMixinModel**: Hierarchical relationships
- `parent_id`: Optional[str] - Self-referencing foreign key

**ImageMixinModel**: Media support
- `image_url`: Optional[str] - Optional image URL

### Reference Model Pattern
Each entity defines reference patterns for type-safe relationships:

```python
class UserModel:
    class ReferenceID:
        user_id: str = Field(..., description="The ID of the related user")
        
        class Optional:
            user_id: Optional[str] = None
            
        class Search:
            user_id: Optional[StringSearchModel] = None
```

### Network Model Pattern
Each entity defines API interaction models:

```python
class UserNetworkModel:
    class POST(BaseModel):
        user: UserModel.Create
        
    class PUT(BaseModel):
        user: UserModel.Update
        
    class SEARCH(BaseModel):
        user: UserModel.Search
        
    class ResponseSingle(BaseModel):
        user: UserModel
        
    class ResponsePlural(BaseModel):
        users: List[UserModel]
```

## Authentication & Identity (BLL_Auth.py)

### UserModel
Core user accounts for authentication and identity management.

**Fields:**
- `email`: Optional[str] - Unique email address for login
- `username`: Optional[str] - Optional unique username
- `display_name`: Optional[str] - Preferred display name
- `first_name`, `last_name`: Optional[str] - Name components
- `mfa_count`: Optional[int] - Required MFA methods (default: 1)
- `timezone`: Optional[str] - User timezone
- `active`: Optional[bool] - Account status (default: true)
- `image_url`: Optional[str] - Profile image

**Generated Relationships:**
- One-to-many with UserCredential, UserTeam, UserMetadata
- Many-to-many with Team through UserTeam

### UserCredentialModel
Password management and history.

**Fields:**
- `user_id`: str - User reference (from UserModel.ReferenceID)
- `password_hash`: Optional[str] - Bcrypt hash
- `password_changed_at`: Optional[datetime] - Change timestamp (NULL = current)

### UserRecoveryQuestionModel
Account recovery mechanism.

**Fields:**
- `user_id`: str - User reference
- `question`: str - Security question text
- `answer`: str - Hashed answer

### UserMetadataModel
Extensible user properties.

**Fields:**
- `user_id`: str - User reference
- `key`: Optional[str] - Property name
- `value`: Optional[str] - Property value

### SessionModel (Session)
JWT session management.

**Fields:**
- `user_id`: str - User reference
- `session_key`: str - Unique session identifier
- `jwt_issued_at`: datetime - JWT issue time
- `refresh_token_hash`: Optional[str] - Refresh token hash
- `device_type`, `device_name`, `browser`: Optional[str] - Device info
- `is_active`: bool - Session status
- `last_activity`: datetime - Last activity timestamp
- `expires_at`: datetime - Session expiration
- `revoked`: bool - Manual revocation flag
- `trust_score`: int - Risk assessment (0-100)
- `requires_verification`: bool - Additional verification flag

### FailedLoginAttemptModel
Security monitoring and lockout enforcement.

**Fields:**
- `user_id`: Optional[str] - User reference (from UserReferenceModel.Optional)
- `ip_address`: Optional[str] - Origin IP address

## Team Management

### TeamModel
Hierarchical team structure with encryption support.

**Fields:**
- `name`: Optional[str] - Human-readable team name (from NameMixinModel)
- `description`: Optional[str] - Team purpose description
- `encryption_key`: Optional[str] - Team resource encryption
- `token`: Optional[str] - Team token
- `training_data`: Optional[str] - Training data for team
- `parent_id`: Optional[str] - Parent team reference (from ParentMixinModel)
- `image_url`: Optional[str] - Team image (from ImageMixinModel)

### TeamMetadataModel
Extensible team properties.

**Fields:**
- `team_id`: str - Team reference
- `key`: Optional[str] - Property name
- `value`: Optional[str] - Property value

### UserTeamModel
User-team associations with roles and expiration.

**Fields:**
- `user_id`: str - User reference
- `team_id`: str - Team reference
- `role_id`: str - Role reference
- `enabled`: bool - Active status
- `expires_at`: Optional[datetime] - Membership expiration

## Role-Based Access Control

### RoleModel
Permission roles with hierarchy and security policies.

**Fields:**
- `name`: str - Role identifier/code (from NameMixinModel)
- `friendly_name`: Optional[str] - Display name
- `team_id`: Optional[str] - Team scope (from TeamReferenceModel.Optional). Required for API-created roles; system roles (seeded) may have null team_id.
- `parent_id`: str - Role hierarchy (from ParentMixinModel)
- `mfa_count`: int - Required MFA verifications
- `password_change_frequency_days`: int - Password policy
- `expires_at`: Optional[datetime] - Role expiration

**Validation:**
- API role creation requires `team_id` to be provided (returns 422 if null)

### PermissionModel
Granular resource permissions.

**Fields:**
- `resource_type`: str - Target table name
- `resource_id`: str - Target record ID
- `user_id`: Optional[str] - User assignment (from UserModel.ReferenceID.Optional)
- `team_id`: Optional[str] - Team assignment (from TeamModel.ReferenceID.Optional)
- `role_id`: Optional[str] - Role assignment (from RoleModel.ReferenceID.Optional)
- `expires_at`: Optional[datetime] - Permission expiration
- `can_view`, `can_execute`, `can_copy`, `can_edit`, `can_delete`, `can_share`: bool - Permission flags

## Invitation System

### InvitationModel
Team invitation management.

**Fields:**
- `user_id`: Optional[str] - Inviter reference (from UserReferenceModel.Optional)
- `team_id`: Optional[str] - Target team (from TeamReferenceModel.Optional)
- `role_id`: Optional[str] - Assigned role (from RoleReferenceModel.Optional)
- `code`: Optional[str] - Public invitation code
- `max_uses`: Optional[int] - Usage limit
- `expires_at`: Optional[datetime] - Invitation expiration

**Validation:**
- `team_id` and `role_id` must both be provided together, or both omitted for app-level invitations
- Explicitly setting both to null is rejected (returns 422); omit both fields for app-level invitations

### InviteeModel
Specific invitation recipients.

**Fields:**
- `invitation_id`: str - Invitation reference
- `user_id`: Optional[str] - Invitee (if registered)
- `email`: str - Invitee email address
- `accepted_at`: Optional[datetime] - Acceptance timestamp
- `declined_at`: Optional[datetime] - Decline timestamp

## Provider System (BLL_Providers.py)

### ProviderModel
External service provider definitions.

**Fields:**
- `name`: str - Provider identifier (from NameMixinModel)
- `friendly_name`: Optional[str] - Display name
- `agent_settings_json`: Optional[str] - Configuration settings
- `system`: bool - System provider flag

### ProviderInstanceModel
User/team-specific provider configurations.

**Fields:**
- `name`: str - Instance name (from NameMixinModel)
- `provider_id`: str - Provider reference
- `user_id`: Optional[str] - User ownership (from UserModel.ReferenceID.Optional)
- `team_id`: Optional[str] - Team ownership (from TeamModel.ReferenceID.Optional)
- `model_name`: Optional[str] - Specific model
- `api_key`: Optional[str] - Authentication key

### ProviderExtensionModel
Provider-extension ability mapping.

**Fields:**
- `provider_id`: str - Provider reference
- `extension_id`: str - Extension reference

### ProviderInstanceUsageModel
Usage tracking and billing.

**Fields:**
- `provider_instance_id`: str - Instance reference
- `user_id`: Optional[str] - User (from UserModel.ReferenceID.Optional)
- `team_id`: Optional[str] - Team (from TeamModel.ReferenceID.Optional)
- `input_tokens`: Optional[int] - Input token count
- `output_tokens`: Optional[int] - Output token count

### RotationModel
Provider load balancing and failover.

**Fields:**
- `name`: str - Rotation name (from NameMixinModel)
- `description`: Optional[str] - Purpose description
- `user_id`: Optional[str] - User ownership (from UserModel.ReferenceID.Optional)
- `team_id`: Optional[str] - Team ownership (from TeamModel.ReferenceID.Optional)
- `extension_id`: Optional[str] - Extension scope (from ExtensionModel.ReferenceID.Optional)

## Extension System (BLL_Extensions.py)

### ExtensionModel
Third-party integration definitions.

**Fields:**
- `name`: str - Extension identifier (from NameMixinModel)
- `description`: Optional[str] - Extension description

### AbilityModel
Extension ability definitions.

**Fields:**
- `name`: str - Ability identifier (from NameMixinModel)
- `extension_id`: str - Extension reference

## Database Generation Features

### Automatic Type Mapping
Pydantic types automatically map to SQLAlchemy columns:
- `str` → `String`
- `int` → `Integer`
- `bool` → `Boolean`
- `datetime` → `DateTime`
- `Optional[T]` → `nullable=True`

### Relationship Generation
Reference model patterns automatically create foreign keys and relationships:
```python
# From UserModel.ReferenceID generates:
user_id = Column(String, ForeignKey("users.id"), nullable=False)
user = relationship("User", backref="related_records")
```

### Validation Integration
Pydantic validators automatically translate to SQLAlchemy validation:
```python
@model_validator(mode="after")
def validate_email(self):
    if "@" not in self.email:
        raise ValueError("Invalid email format")
    return self
# Becomes SQLAlchemy @validates decorator
```

### Seed Data Management
ClassVar seed_data automatically populates during database initialization:
```python
seed_data: ClassVar[List[Dict[str, Any]]] = [
    {"id": "{{ROOT_ID}}", "email": "root@{{BASE_DOMAIN}}"},
]
# Processed with environment variable substitution
```

## Benefits of BLL-First Approach

1. **Single Source of Truth**: Schema defined once in BLL models
2. **Type Safety**: Full typing throughout the application stack
3. **API Generation**: OpenAPI schemas generated from Pydantic models
4. **Validation Consistency**: Same validation rules for API and database
5. **Development Efficiency**: Automatic SQLAlchemy model generation
6. **Maintainability**: Changes in one place propagate through the system
7. **Testing**: Comprehensive test generation from model definitions

## Migration Path

The BLL-first approach replaces the traditional pattern where:
- **Old**: Separate DB_*.py files with manual SQLAlchemy definitions
- **New**: BLL_*.py files with Pydantic models that auto-generate SQLAlchemy

This eliminates the need for:
- Manual SQLAlchemy model maintenance
- Duplicate field definitions
- Separate validation logic
- Manual relationship configuration