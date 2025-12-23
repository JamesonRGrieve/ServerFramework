# Database System Users

## Overview
The system implements three special user types with graduated access privileges that enable secure system operations, template sharing, and administrative functions. These system users bypass standard permission checks and provide foundational security patterns.

## System User Types

### ROOT_ID (`env("ROOT_ID")`)
**Purpose**: Ultimate system administrator with unrestricted access
**Access Level**: Universal access to all resources regardless of permissions

**Capabilities:**
- Bypass all permission checks (`PermissionResult.GRANTED` always returned)
- Access to soft-deleted records (`deleted_at != NULL`)
- Mutation of all system entities
- Administrative operations on any resource
- Emergency system recovery and maintenance

**Usage Pattern:**
```python
# ROOT_ID bypasses all permission checks
if is_root_id(user_id):
    return (PermissionResult.GRANTED, None)

# Only ROOT_ID can see deleted records  
if record.deleted_at and user_id != env("ROOT_ID"):
    return (PermissionResult.NOT_FOUND, None)
```

### SYSTEM_ID (`env("SYSTEM_ID")`) 
**Purpose**: System service account for automated operations
**Access Level**: Create public/system resources, modify system entities

**Capabilities:**
- Create resources accessible by all users
- Modify system entities (tables marked with `system = True`)
- Automated seeding and system operations
- Service-to-service communications
- Background job execution

**Access Rules:**
- **SYSTEM_ID records**: Viewable by all users, modifiable by ROOT_ID/SYSTEM_ID only
- Cannot modify ROOT_ID-owned records
- Restricted from user-specific data access

**Usage Pattern:**
```python
# System entities are viewable by all, modifiable by system users only
if cls.system and required_level in [PermissionType.EDIT, PermissionType.DELETE]:
    if user_id not in [env("ROOT_ID"), env("SYSTEM_ID")]:
        return (PermissionResult.DENIED, "System entity modification requires elevated privileges")
```

### TEMPLATE_ID (`env("TEMPLATE_ID")`)
**Purpose**: Template resource creator for shareable content
**Access Level**: Create template resources with special sharing permissions

**Capabilities:**
- Create template resources shareable by all users
- Provide default configurations and examples
- Enable copy/execution permissions for all users
- Template content distribution

**Access Rules:**
- **TEMPLATE_ID records**: 
  - Viewable by all users
  - Copyable by all users (`PermissionType.COPY`)
  - Executable by all users (`PermissionType.EXECUTE`) 
  - Shareable by all users (`PermissionType.SHARE`)
  - Modifiable by ROOT_ID/SYSTEM_ID only

**Usage Pattern:**
```python
# Template records have special sharing permissions
if record.created_by_user_id == env("TEMPLATE_ID"):
    if required_level in [PermissionType.VIEW, PermissionType.COPY, 
                         PermissionType.EXECUTE, PermissionType.SHARE]:
        return (PermissionResult.GRANTED, None)
```

## Security Implementation

### Identity Validation
```python
def is_root_id(user_id: str) -> bool:
    """Check if user_id matches ROOT_ID"""
    return user_id == env("ROOT_ID")

def is_any_internal_id(user_id: str) -> bool:
    """Check if user_id matches SYSTEM_ID"""
    return user_id == env("SYSTEM_ID")

def is_template_id(user_id: str) -> bool:
    """Check if user_id matches TEMPLATE_ID"""
    return user_id == env("TEMPLATE_ID")
```

### Permission Hierarchy
1. **ROOT_ID**: Unrestricted access (highest privilege)
2. **SYSTEM_ID**: System operations and public resource creation
3. **TEMPLATE_ID**: Template resource creation with sharing
4. **Regular Users**: Standard permission-based access

### System Entity Protection
Tables marked with `system = True` receive special protection:

```python
class SystemEntity(Base, BaseMixin):
    __tablename__ = "system_entities"
    system = True  # Marks as system-protected
    
    # Only ROOT_ID and SYSTEM_ID can modify
    # All users can view (unless other restrictions apply)
```

## Access Control Patterns

### Create Permission Validation
```python
def user_can_create_referenced_entity(cls, user_id, db, **kwargs):
    """Special handling for system user creation permissions"""
    
    # ROOT_ID can create anything
    if is_root_id(user_id):
        return True
        
    # SYSTEM_ID can create system/public resources
    if is_any_internal_id(user_id) and cls.system:
        return True
        
    # TEMPLATE_ID can create template resources
    if is_template_id(user_id):
        return True
        
    # Regular permission checking for other users
    return standard_permission_check(cls, user_id, db, **kwargs)
```

### Record Ownership Patterns
```python
# System users create records with specific ownership patterns
creator_id = env("SYSTEM_ID")  # System operations
creator_id = env("TEMPLATE_ID")  # Template resources
creator_id = env("ROOT_ID")  # Administrative operations
```

### Soft Delete Handling
```python
def check_record_access(record, user_id):
    """Control access to deleted records"""
    
    # Only ROOT_ID can access deleted records
    if record.deleted_at and user_id != env("ROOT_ID"):
        return PermissionResult.NOT_FOUND
        
    return PermissionResult.GRANTED
```

## Seeding Integration

### System User Seeds
```python
class UserModel:
    seed_data = [
        {
            "id": env("ROOT_ID"),
            "email": "root@system.local",
            "system": True
        },
        {
            "id": env("SYSTEM_ID"), 
            "email": "system@system.local",
            "system": True
        },
        {
            "id": env("TEMPLATE_ID"),
            "email": "template@system.local", 
            "system": True
        }
    ]
```

### Creator Assignment
```python
# Seeding uses appropriate system user as creator
class SystemEntity:
    seed_creator_id = "SYSTEM_ID"  # Uses SYSTEM_ID as creator

class TemplateEntity:
    seed_creator_id = "TEMPLATE_ID"  # Uses TEMPLATE_ID as creator
```

## Best Practices

### Environment Configuration
```bash
# .env file configuration
ROOT_ID="00000000-0000-0000-0000-000000000001"
SYSTEM_ID="00000000-0000-0000-0000-000000000002"  
TEMPLATE_ID="00000000-0000-0000-0000-000000000003"
```

### Security Guidelines
1. **ROOT_ID Usage**: Only for emergency operations and system maintenance
2. **SYSTEM_ID Usage**: Automated processes and system entity management
3. **TEMPLATE_ID Usage**: Default content and shareable templates
4. **Access Validation**: Always validate system user identity before granting privileges
5. **Audit Logging**: Log system user operations for security monitoring

### Development Patterns
```python
# Use system users appropriately in managers
class EntityManager(AbstractBLLManager):
    def create_system_entity(self, **kwargs):
        """Create system entity using SYSTEM_ID"""
        return self.DB.create(
            requester_id=env("SYSTEM_ID"),
            db=self.db,
            **kwargs
        )
        
    def create_template(self, **kwargs):
        """Create template using TEMPLATE_ID"""
        return self.DB.create(
            requester_id=env("TEMPLATE_ID"),
            db=self.db,
            **kwargs
        )
```

## Error Handling

### System User Validation
```python
def validate_system_user(user_id: str, operation: str):
    """Validate system user for specific operations"""
    
    if not user_id:
        raise ValueError("User ID required")
        
    system_users = [env("ROOT_ID"), env("SYSTEM_ID"), env("TEMPLATE_ID")]
    if user_id in system_users:
        logger.info(f"System user {user_id} performing {operation}")
        
    return True
```

This system user architecture provides a secure foundation for system operations while maintaining clear access boundaries and audit trails.