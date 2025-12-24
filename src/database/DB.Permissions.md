# Database Permissions

## Overview
The permission system (`StaticPermissions.py`) provides enterprise-grade access control through hierarchical role-based permissions, team structures, and granular resource-level permissions. The implementation uses advanced SQL techniques including recursive CTEs, permission caching, and comprehensive security patterns to ensure both performance and security.

## Permission Architecture

### Core Components

**Permission Types (PermissionType Enum):**
- `VIEW`: Basic read access to resources
- `EXECUTE`: Run or use ability  
- `COPY`: Duplication rights
- `EDIT`: Modification rights
- `DELETE`: Removal rights
- `SHARE`: Permission management and sharing

**Permission Results (PermissionResult Enum):**
- `GRANTED`: Access is allowed
- `DENIED`: Access is explicitly denied
- `NOT_FOUND`: Target resource doesn't exist
- `ERROR`: System error during permission check

### System Users
Special user types with graduated access levels:

```python
ROOT_ID = env("ROOT_ID")      # Full system access, sees deleted records
SYSTEM_ID = env("SYSTEM_ID")  # System operations, creates public resources  
TEMPLATE_ID = env("TEMPLATE_ID")  # Template resources, shareable by all
```

**Access Rules:**
- **ROOT_ID records**: Only accessible by ROOT_ID (records with `user_id` or `created_by_user_id` equal to ROOT_ID are completely inaccessible to other users)
- **SYSTEM_ID records**: Viewable by all users when `minimum_role` is `None` or `"user"` (i.e., VIEW-level access), but only modifiable (EDIT/DELETE) by ROOT_ID/SYSTEM_ID. The `can_access_system_record()` function implements this nuanced check - regular users get read access automatically for basic permission levels.
- **TEMPLATE_ID records**: Viewable/copyable/executable/shareable by all users when `minimum_role` is `None` or `"user"`, modifiable by ROOT_ID/SYSTEM_ID only

## Permission Models

### Permission Table
Granular resource permissions stored in database.

**Fields:**
- `user_id`: Optional user assignment
- `team_id`: Optional team assignment
- `role_id`: Optional role assignment  
- `resource_type`: Target table name
- `resource_id`: Target record ID
- `expires_at`: Permission expiration
- Permission flags: `can_view`, `can_execute`, `can_copy`, `can_edit`, `can_delete`, `can_share`

**Scope Types:**
- **User Permissions**: Direct user assignments
- **Team Permissions**: Inherited through team membership
- **Role Permissions**: Inherited through role assignments

### Role Hierarchy
Hierarchical role system with inheritance.

**Built-in Roles:**
- `user`: Base user role
- `admin`: Administrative role (inherits from user)
- `superadmin`: Super administrative role (inherits from admin)

**Role Management:**
```python
def _get_role_hierarchy_map(db: Session) -> dict:
    """Build role hierarchy with caching for performance"""
    # Caches role hierarchy for 5 minutes (300 seconds TTL)
    # Maps role names to hierarchy levels
    # Testing support: When cache is valid, a "valid" key is injected
    # into the cache dict (_get_role_hierarchy_map._cache["valid"] = True)
    # to allow tests to verify cache hit behavior
```

### Team Structure
Hierarchical teams with parent-child relationships.

**Team Access:**
- Users can access resources in teams they're members of
- Includes parent team access through hierarchy traversal
- Role-based access within teams (admin roles for modification)

## Permission Checking

### Core Permission Check
Central permission validation function:

```python
def check_permission(
    user_id,           # User requesting access
    record_cls,        # Model class to check
    record_id,         # Record ID to check
    db,                # Database session
    declarative_base,  # Required: SQLAlchemy declarative base (e.g., db_manager.Base)
    required_level=None,
    minimum_role=None,
    db_manager=None
):
    """
    Comprehensive permission check with:
    - System user handling
    - Ownership checks
    - Permission table lookups
    - Role hierarchy validation
    - Database manager integration
    """
    
    # ROOT_ID has universal access
    if is_root_id(user_id):
        return (PermissionResult.GRANTED, None)
        
    # Convert minimum_role to PermissionType if needed
    if required_level is None:
        if minimum_role == "superadmin":
            required_level = PermissionType.SHARE
        elif minimum_role == "admin":
            required_level = PermissionType.EDIT
        else:
            required_level = PermissionType.VIEW
            
    # Check record existence and deletion status
    # Validate system record access rules
    # Check direct ownership and system user patterns
    # Query permission table for explicit grants
```

### SQL-Level Permission Filtering
High-performance database-level permission filtering using advanced SQL patterns:

```python
def generate_permission_filter(
    user_id: str, resource_cls: Type[Any], db: Session, declarative_base,
    required_permission_level: PermissionType = None,
    _visited_classes: Optional[set] = None,
    minimum_role: Optional[str] = None,
    db_manager=None
) -> SQLAlchemy_Expression:
    """
    Generate optimized SQL WHERE conditions using:
    
    1. Recursive CTEs for team hierarchy traversal with depth limits
    2. Role hierarchy caching with 5-minute TTL
    3. System user privilege escalation
    4. Permission inheritance through reference chains
    5. Circular reference detection and prevention
    6. Database manager integration for proper base handling
    """
```

**Filter Components:**
- **Ownership Filter**: `resource.user_id == user_id` OR `resource.created_by_user_id == user_id`
  - **Exception**: The `invitations` and `Invitees` tables are explicitly excluded from ownership-based filtering. These tables use their own specialized permission logic (see Invitation Models section) rather than standard ownership checks, as invitation visibility is determined by team membership and invitee relationships rather than direct ownership.
- **Team Filter**: `resource.team_id IN (accessible_team_ids)`
- **Permission Filter**: `EXISTS(SELECT 1 FROM permissions WHERE ...)`
- **System Filter**: Special handling for system-flagged tables

### Team Hierarchy Resolution
Advanced recursive CTE implementation with depth limiting and optimization:

```python
def _get_admin_accessible_team_ids_cte(
    user_id: str, db: Session, max_depth: int = 5
) -> CTE:
    """
    Generate optimized recursive CTE with:
    - Depth-limited traversal (prevents infinite loops)
    - Role-aware permission inheritance
    - Performance-optimized JOIN strategies
    - Support for complex team hierarchies
    - Built-in circular reference detection
    """
```

## Permission Inheritance

### Permission Reference Chains
Resources can inherit permissions through relationships:

```python
class Document(Base, BaseMixin, UserRefMixin, TeamRefMixin.Optional):
    __tablename__ = "documents"
    
    # Define permission inheritance path
    permission_references = ["user", "team"]
    create_permission_reference = "team"  # Requires team admin to create
```

**Reference Resolution:**
```python
def get_referenced_records(record, visited=None):
    """
    Follow permission_references chains to find records holding permissions.
    Includes circular reference detection.
    """
```

### Create Permission Validation
Special handling for creation permissions:

```python
def user_can_create_referenced_entity(cls, user_id, db, **kwargs):
    """
    Check create permissions based on create_permission_reference:
    1. Find target class through reference chain
    2. Validate user has admin access to referenced entity
    3. Handle Permission table special cases
    """
```

## Access Control Methods

### Entity-Level Access Control
All entities inherit access control methods from BaseMixin:

```python
@classmethod
def user_has_read_access(cls, user_id, id, db, minimum_role=None, referred=False):
    """Standard read access check using unified permission system"""
    
@classmethod  
def user_has_admin_access(cls, user_id, id, db):
    """Admin access check (EDIT permission required)"""
    
@classmethod
def user_has_all_access(cls, user_id, id, db):
    """Full access check (SHARE permission required)"""

@classmethod
def user_can_create(cls, user_id, db, **kwargs):
    """Create permission validation with reference checking"""
```

### Model-Specific Overrides
Some models override default permission behavior:

**User Model:**
- Users can always see their own records
- Can see other users in teams they have access to
- Special handling for system user records

**Permission Model:**
- Requires SHARE permission on target resource to manage permissions
- Allows users with EDIT access to target resource to update permissions

**Invitation Models:**
- Restrictive logic: only see invitations you created or were invited to
- Team invitations require team membership

## Performance Optimizations

### Permission Caching
- **Role Hierarchy Caching**: Thread-safe LRU cache with 5-minute TTL prevents repeated role lookup queries
- **Team Hierarchy Optimization**: Recursive CTE results cached at query level
- **Permission Check Memoization**: Frequently accessed permission checks cached during request lifecycle
- **Cache Invalidation**: Automatic cache clearing on role/team modifications

### SQL Optimization
- Single-query permission checking via SQL filters
- Recursive CTEs for efficient hierarchy traversal
- EXISTS subqueries for permission lookups
- Composite indexes on permission table (`resource_type` + `resource_id`)

### Query Patterns
```python
# Efficient permission filtering in list operations
permission_filter = generate_permission_filter(user_id, cls, db, PermissionType.VIEW)
query = session.query(cls).filter(permission_filter)

# Batch permission checking
accessible_team_ids_cte = _get_admin_accessible_team_ids_cte(user_id, db)
team_filter = resource_cls.team_id.in_(select(accessible_team_ids_cte.c.id))
```

## Security Features

### System Table Protection
Tables marked with `system = True`:
- Only ROOT_ID and SYSTEM_ID can modify
- All users can view (unless other restrictions apply)
- Prevents unauthorized system configuration changes

### Soft Delete Handling
- Deleted records (`deleted_at != NULL`) only visible to ROOT_ID
- Maintains referential integrity while hiding deleted data
- Audit trail preservation

### Permission Validation
```python
def validate_columns(cls, **kwargs):
    """Prevent SQL injection through column name validation"""
    
def validate_fields(cls, fields):
    """Validate field names for DTO conversion"""
```

### Circular Reference Protection
- Permission reference chain traversal includes cycle detection
- Prevents infinite recursion in permission inheritance
- Graceful error handling for malformed permission chains

## Integration Patterns

### FastAPI Integration
Automatic permission enforcement in CRUD operations:

```python
@classmethod
@with_session
def get(cls, requester_id, db, **kwargs):
    """All database operations include automatic permission filtering"""
    
    # Apply permission filter
    perm_filter = generate_permission_filter(requester_id, cls, db, PermissionType.VIEW)
    query = build_query(db, cls, filters=[perm_filter], **kwargs)
```

### Decorator Pattern
Session management with permission context:

```python
@with_session
def crud_operation(cls, requester_id: str, db: Optional[Session], ...):
    """
    - Creates session if none provided
    - Automatic permission checking
    - Transaction management
    - Resource cleanup
    """
```

### Error Handling
Consistent error responses:
- 403 Forbidden: Permission denied
- 404 Not Found: Resource doesn't exist or no access
- 400 Bad Request: Invalid parameters
- 500 Internal Server Error: System errors

## Best Practices

### Permission Design
1. **Principle of Least Privilege**: Default to denying access
2. **Hierarchical Permissions**: Use team/role inheritance
3. **Explicit Grants**: Direct permissions for exceptions
4. **Time-Limited Access**: Use `expires_at` for temporary permissions

### Performance Guidelines
1. **Use SQL Filters**: Avoid N+1 permission queries
2. **Batch Operations**: Check permissions in bulk where possible
3. **Cache Role Hierarchy**: Leverage built-in caching
4. **Limit Recursion**: Set max_depth for hierarchy traversals

### Security Guidelines
1. **Validate Inputs**: Always validate column/field names
2. **Check System Flags**: Respect system table restrictions
3. **Handle Soft Deletes**: Filter deleted records appropriately
4. **Audit Access**: Log permission-related operations