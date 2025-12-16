# BLL Hook System Patterns and Guide

This document provides comprehensive guidance on using the enhanced BLL hook system, including the new class-level hook functionality, type-safe patterns, and best practices.

## Hook System Overview

The BLL hook system allows you to execute custom code before or after any method on BLL manager classes. The system supports:

- **Class-level hooks (`ClassName`)**: Apply to ALL methods of a manager class
- **Method-specific hooks (`ClassName.method_name`)**: Target individual methods  
- **Timing control**: Execute before or after method execution
- **Priority ordering**: Control execution order with numeric priorities
- **Conditional execution**: Use conditions to control when hooks run
- **Type safety**: Full TypeScript-like type annotations and IDE support

## Hook Registration Patterns

### 1. Class-Level Hooks (NEW)

Apply hooks to ALL methods of a manager class - perfect for cross-cutting concerns:

```python
from logic.AbstractLogicManager import hook_bll, HookContext, HookTiming

# Audit ALL operations on UserManager
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=1)
def audit_all_user_operations(context: HookContext) -> None:
    """Audit every user operation - create, get, list, update, delete, search."""
    logger.info(
        f"User {context.manager.requester.id} executing {context.method_name} "
        f"on UserManager with target_id: {context.manager.target_id}"
    )

# Performance monitoring for ALL operations  
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=2)
@hook_bll(UserManager, timing=HookTiming.AFTER, priority=98)
def monitor_user_operations(context: HookContext) -> None:
    """Monitor performance of all UserManager operations."""
    if context.timing == HookTiming.BEFORE:
        context.condition_data['start_time'] = time.time()
    elif context.timing == HookTiming.AFTER:
        duration = time.time() - context.condition_data.get('start_time', 0)
        logger.info(f"UserManager.{context.method_name} took {duration:.3f}s")

# Security validation for ALL operations
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=5)
def validate_user_permissions(context: HookContext) -> None:
    """Apply security checks to all UserManager operations."""
    if not context.manager.requester.has_permission('user_management'):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

# Rate limiting for ALL operations
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=3)
def rate_limit_user_operations(context: HookContext) -> None:
    """Apply rate limiting to all UserManager operations."""
    user_id = context.manager.requester.id
    if not rate_limiter.check_limit(user_id, 'user_operations', limit=100, window=3600):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
```

**Class-Level Hook Benefits:**
- **DRY Principle**: Single hook instead of multiple method-specific hooks
- **Future-Proof**: Automatically applies to new methods added to the class
- **Cross-Cutting Concerns**: Perfect for auditing, logging, security, performance monitoring
- **Reduced Maintenance**: Single point of change for behavior affecting all methods

### 2. Method-Specific Hooks

Target individual methods for specialized behavior:

```python
# Method-specific hook using method reference (type-safe)
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
def validate_user_creation(context: HookContext) -> None:
    """Validate user creation with enhanced context."""
    email = context.kwargs.get('email')
    if email and email.endswith('@blocked.com'):
        raise HTTPException(status_code=403, detail="Domain blocked")
    
    # Modify arguments before execution
    if not context.kwargs.get('timezone'):
        context.kwargs['timezone'] = 'UTC'

# Post-creation processing
@hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=20)
def post_user_creation(context: HookContext) -> None:
    """Handle post-creation tasks with enhanced context."""
    if context.result and hasattr(context.result, 'email'):
        send_welcome_email(context.result.email)
        logger.info(f"User created: {context.result.email}")

# Conditional method-specific hook
@hook_bll(
    AbilityManager.get, 
    timing=HookTiming.AFTER, 
    priority=15,
    condition=lambda ctx: ctx.result and hasattr(ctx.result, 'name')
)
def log_ability_access(context: HookContext) -> None:
    """Log ability access with conditional execution."""
    logger.debug(f"Ability '{context.result.name}' accessed by {context.manager.requester.id}")
```

### 3. Combined Class and Method-Specific Hooks

Use both class-level and method-specific hooks together for layered functionality:

```python
# Class-level hook for all operations (runs first due to lower priority)
@hook_bll(ExtensionManager, timing=HookTiming.BEFORE, priority=5)
def audit_all_extension_operations(context: HookContext) -> None:
    """Audit all ExtensionManager operations."""
    logger.info(f"Extension operation: {context.method_name}")

# Method-specific hook for creation only (runs after class-level hook)
@hook_bll(ExtensionManager.create, timing=HookTiming.BEFORE, priority=10)
def validate_extension_creation(context: HookContext) -> None:
    """Additional validation specific to extension creation."""
    name = context.kwargs.get('name')
    if name and name.lower() == 'reserved':
        raise HTTPException(status_code=400, detail="Name 'reserved' not allowed")

# Post-creation logging
@hook_bll(ExtensionManager.create, timing=HookTiming.AFTER, priority=20)
def log_extension_creation(context: HookContext) -> None:
    """Log successful extension creation."""
    if context.result:
        logger.info(f"Extension '{context.result.name}' created successfully")

# Execution order for ExtensionManager.create():
# 1. audit_all_extension_operations (priority 5, class-level)
# 2. validate_extension_creation (priority 10, method-specific) 
# 3. [original create method executes]
# 4. log_extension_creation (priority 20, after hook)
```

## Hook Context Usage Patterns

### Accessing Context Properties

```python
def comprehensive_hook_example(context: HookContext) -> None:
    """
    Example showing all available context properties and methods.
    """
    # Access the manager instance and its properties
    manager = context.manager
    requester_id = manager.requester.id
    target_id = manager.target_id
    
    # Access method information
    method_name = context.method_name  # "create", "update", etc.
    timing = context.timing  # HookTiming.BEFORE or HookTiming.AFTER
    
    # Access method arguments (mutable)
    method_args = context.args  # list - can be modified
    method_kwargs = context.kwargs  # dict - can be modified
    
    # Access/modify result (for 'after' hooks)
    if context.timing == HookTiming.AFTER:
        result = context.result
        # Modify the result that will be returned
        context.set_result({"wrapped": result, "processed_by": "hook"})
    
    # Skip the original method execution (for 'before' hooks only)
    if some_condition and context.timing == HookTiming.BEFORE:
        context.skip_method()
        context.set_result({"skipped": True, "reason": "Condition not met"})
    
    # Modify method arguments (for 'before' hooks)
    if context.timing == HookTiming.BEFORE:
        context.kwargs['hook_processed'] = True
        context.kwargs['processed_at'] = datetime.now()
    
    # Store data for communication between before/after hooks
    if context.timing == HookTiming.BEFORE:
        context.condition_data['hook_start_time'] = time.time()
    elif context.timing == HookTiming.AFTER:
        start_time = context.condition_data.get('hook_start_time', 0)
        duration = time.time() - start_time
        logger.info(f"Hook processing took {duration:.3f}s")
```

### Timing-Aware Hooks

Single hooks that behave differently based on execution timing:

```python
@hook_bll(UserManager.update, timing=HookTiming.BEFORE, priority=15)
@hook_bll(UserManager.update, timing=HookTiming.AFTER, priority=15)
def audit_user_changes(context: HookContext) -> None:
    """Audit user changes - works for both before and after."""
    if context.timing == HookTiming.BEFORE:
        # Store original state for comparison
        if hasattr(context.manager, 'target') and context.manager.target:
            context.condition_data['original_user'] = context.manager.target
            
        # Log the update attempt
        logger.info(f"User update attempted by {context.manager.requester.id}")
        
    elif context.timing == HookTiming.AFTER:
        # Log what changed
        original = context.condition_data.get('original_user')
        if original and context.result:
            changes = {}
            for key, new_value in context.kwargs.items():
                old_value = getattr(original, key, None)
                if old_value != new_value:
                    changes[key] = {'old': old_value, 'new': new_value}
            
            if changes:
                logger.info(f"User {context.result.id} updated: {changes}")
```

### Argument Modification Patterns

```python
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
def enhance_user_creation_data(context: HookContext) -> None:
    """Enhance user creation data with additional fields."""
    # Add audit information
    context.kwargs['created_via'] = 'api'
    context.kwargs['creation_source'] = context.manager.requester.id
    
    # Set defaults
    if 'timezone' not in context.kwargs:
        context.kwargs['timezone'] = 'UTC'
    
    if 'locale' not in context.kwargs:
        context.kwargs['locale'] = 'en_US'
    
    # Normalize email
    if 'email' in context.kwargs:
        context.kwargs['email'] = context.kwargs['email'].lower().strip()
    
    # Generate username if not provided
    if 'username' not in context.kwargs and 'email' in context.kwargs:
        email_prefix = context.kwargs['email'].split('@')[0]
        context.kwargs['username'] = f"{email_prefix}_{uuid.uuid4().hex[:8]}"
```

### Result Modification Patterns

```python
@hook_bll(UserManager.get, timing=HookTiming.AFTER, priority=20)
def enhance_user_response(context: HookContext) -> None:
    """Enhance user response with additional computed fields."""
    if not context.result:
        return
    
    # Convert to dict for manipulation (assuming result is a Pydantic model)
    user_data = context.result.model_dump() if hasattr(context.result, 'model_dump') else context.result
    
    # Add computed fields
    user_data['display_name'] = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
    user_data['is_admin'] = context.manager.db.query(UserRole).filter(
        UserRole.user_id == user_data['id'],
        UserRole.role_name == 'admin'
    ).first() is not None
    
    # Add last login information
    last_login = get_last_login(user_data['id'])
    user_data['last_login'] = last_login.isoformat() if last_login else None
    
    # Return enhanced data
    context.set_result(user_data)

@hook_bll(ExtensionManager.list, timing=HookTiming.AFTER, priority=25)
def add_runtime_status(context: HookContext) -> None:
    """Add runtime status to extension list results."""
    if not context.result or not isinstance(context.result, list):
        return
    
    enhanced_results = []
    for extension in context.result:
        ext_data = extension.model_dump() if hasattr(extension, 'model_dump') else extension
        
        # Add runtime status
        ext_data['is_active'] = check_extension_status(ext_data['id'])
        ext_data['last_health_check'] = get_last_health_check(ext_data['id'])
        
        enhanced_results.append(ext_data)
    
    context.set_result(enhanced_results)
```

## Priority and Execution Order Patterns

### Priority Guidelines

Use these priority ranges for consistent hook ordering:

```python
# Priority 1-10: Critical system hooks (security, validation)
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=1)
def critical_security_check(context: HookContext) -> None:
    """Critical security validation - runs first"""
    pass

@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=5)
def essential_validation(context: HookContext) -> None:
    """Essential business rule validation"""
    pass

# Priority 11-20: Business logic hooks
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=15)
def business_logic_hook(context: HookContext) -> None:
    """Business logic processing"""
    pass

# Priority 21-30: Data enrichment and transformation
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=25)
def data_transformation_hook(context: HookContext) -> None:
    """Transform and enrich data"""
    pass

# Priority 31-40: Logging and audit (non-critical)
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=35)
def audit_logging_hook(context: HookContext) -> None:
    """Audit logging - can fail without affecting operation"""
    pass

# Priority 41-50: Performance monitoring and metrics
@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=45)
def performance_monitoring_hook(context: HookContext) -> None:
    """Performance monitoring - runs last before method"""
    pass

# After hooks typically use higher priorities (90+) for cleanup/finalization
@hook_bll(UserManager, timing=HookTiming.AFTER, priority=90)
def cleanup_hook(context: HookContext) -> None:
    """Cleanup operations - runs last"""
    pass
```

### Complex Priority Orchestration

```python
# Comprehensive example showing priority orchestration
class UserCreationHookSuite:
    """Organized hook suite for user creation with clear priorities"""
    
    # Phase 1: Security and Authorization (1-5)
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=1)
    def security_check(context: HookContext) -> None:
        """Phase 1: Critical security validation"""
        if not context.manager.requester.has_permission('create_user'):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=2)
    def rate_limit_check(context: HookContext) -> None:
        """Phase 1: Rate limiting"""
        user_id = context.manager.requester.id
        if not rate_limiter.check(user_id, 'user_creation', 10, 3600):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    # Phase 2: Business Logic Validation (6-15)
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=10)
    def business_validation(context: HookContext) -> None:
        """Phase 2: Business rule validation"""
        email = context.kwargs.get('email')
        if email and User.exists(email=email):
            raise HTTPException(status_code=409, detail="User already exists")
    
    # Phase 3: Data Transformation (16-25)
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=20)
    def data_transformation(context: HookContext) -> None:
        """Phase 3: Transform and enrich data"""
        context.kwargs['email'] = context.kwargs.get('email', '').lower()
        context.kwargs['created_via'] = 'api'
        context.kwargs['signup_ip'] = get_client_ip()
    
    # Phase 4: External Service Integration (26-35)
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=30)
    def external_validation(context: HookContext) -> None:
        """Phase 4: External service validation"""
        email = context.kwargs.get('email')
        if email and not email_validation_service.validate(email):
            raise HTTPException(status_code=400, detail="Invalid email domain")
    
    # Phase 5: Audit and Monitoring (36-45)
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=40)
    def audit_attempt(context: HookContext) -> None:
        """Phase 5: Audit user creation attempt"""
        logger.info(f"User creation attempted by {context.manager.requester.id}")
    
    # Post-Creation Processing (AFTER hooks)
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=10)
    def send_notifications(context: HookContext) -> None:
        """Send welcome notifications"""
        if context.result:
            send_welcome_email(context.result.email)
            notify_admin_of_new_user(context.result)
    
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=20)
    def setup_defaults(context: HookContext) -> None:
        """Set up default user resources"""
        if context.result:
            create_default_workspace(context.result.id)
            assign_default_roles(context.result.id)
    
    @staticmethod
    @hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=90)
    def final_audit(context: HookContext) -> None:
        """Final audit logging"""
        if context.result:
            logger.info(f"User {context.result.id} created successfully")
```

## Conditional Hook Patterns

### Basic Conditional Execution

```python
# Simple condition - only run if result has a name
@hook_bll(
    AbilityManager.get,
    timing=HookTiming.AFTER,
    priority=15,
    condition=lambda ctx: ctx.result and hasattr(ctx.result, 'name')
)
def log_named_ability_access(context: HookContext) -> None:
    """Log access to abilities that have names."""
    logger.debug(f"Named ability accessed: {context.result.name}")

# Complex condition - check multiple criteria
@hook_bll(
    UserManager.update,
    timing=HookTiming.BEFORE,
    priority=10,
    condition=lambda ctx: (
        'email' in ctx.kwargs and 
        ctx.manager.requester.id != ctx.kwargs.get('id') and
        not ctx.manager.requester.has_permission('admin')
    )
)
def prevent_email_change_by_non_admin(context: HookContext) -> None:
    """Prevent non-admins from changing other users' emails."""
    raise HTTPException(status_code=403, detail="Cannot change another user's email")

# Environment-based condition
@hook_bll(
    UserManager,
    timing=HookTiming.BEFORE,
    priority=5,
    condition=lambda ctx: os.getenv('ENVIRONMENT') == 'development'
)
def development_logging(context: HookContext) -> None:
    """Enhanced logging in development environment only."""
    logger.debug(f"DEV: {ctx.method_name} called with args: {ctx.args}, kwargs: {ctx.kwargs}")
```

### Dynamic Conditions with State

```python
def create_permission_condition(required_permission: str):
    """Factory function to create permission-based conditions."""
    def condition(context: HookContext) -> bool:
        return context.manager.requester.has_permission(required_permission)
    return condition

# Use the factory to create specific conditions
@hook_bll(
    UserManager.delete,
    timing=HookTiming.BEFORE,
    priority=5,
    condition=create_permission_condition('delete_user')
)
def authorized_delete_hook(context: HookContext) -> None:
    """Hook that only runs if user has delete permission."""
    logger.info(f"Authorized user {context.manager.requester.id} deleting user")

def create_data_condition(field_name: str, expected_value: Any):
    """Factory for data-based conditions."""
    def condition(context: HookContext) -> bool:
        return context.kwargs.get(field_name) == expected_value
    return condition

@hook_bll(
    UserManager.create,
    timing=HookTiming.BEFORE,
    priority=15,
    condition=create_data_condition('account_type', 'premium')
)
def premium_account_setup(context: HookContext) -> None:
    """Special setup for premium accounts only."""
    context.kwargs['premium_features_enabled'] = True
    context.kwargs['storage_limit'] = '100GB'
```

## Error Handling Patterns

### Graceful Hook Error Handling

```python
@hook_bll(UserManager, timing=HookTiming.AFTER, priority=30)
def external_service_notification(context: HookContext) -> None:
    """Send notification to external service - with error handling."""
    try:
        if context.result and context.method_name == 'create':
            external_api.notify_user_created(context.result.id)
    except Exception as e:
        # Log error but don't fail the main operation
        logger.error(f"Failed to notify external service: {e}")
        # Optionally store for retry
        retry_queue.add('user_creation_notification', {
            'user_id': context.result.id,
            'retry_count': 0
        })

@hook_bll(UserManager.update, timing=HookTiming.BEFORE, priority=10)
def validate_with_fallback(context: HookContext) -> None:
    """Validation with fallback behavior."""
    try:
        # Primary validation
        primary_validator.validate(context.kwargs)
    except ValidationServiceUnavailable:
        # Fallback to simple validation
        logger.warning("Primary validator unavailable, using fallback")
        if not context.kwargs.get('email', '').contains('@'):
            raise HTTPException(status_code=400, detail="Invalid email format")
    except ValidationError as e:
        # Re-raise validation errors
        raise HTTPException(status_code=400, detail=str(e))
```

### Critical vs Non-Critical Hook Patterns

```python
# Critical hook - failure should stop operation
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=5)
def critical_security_validation(context: HookContext) -> None:
    """Critical security check - failure stops operation."""
    email = context.kwargs.get('email')
    if email and email in BLOCKED_EMAILS:
        raise HTTPException(status_code=403, detail="Email domain blocked")

    # This error SHOULD propagate and stop user creation

# Non-critical hook - failure should be logged but not stop operation
@hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=40)
def non_critical_analytics(context: HookContext) -> None:
    """Analytics tracking - failure should not affect operation."""
    try:
        if context.result:
            analytics_service.track_user_created(
                user_id=context.result.id,
                created_by=context.manager.requester.id
            )
    except Exception as e:
        # Log but don't re-raise
        logger.error(f"Analytics tracking failed: {e}")
        # Operation continues successfully

# Wrapper for making hooks non-critical
def non_critical_hook(hook_func):
    """Decorator to make any hook non-critical."""
    def wrapper(context: HookContext) -> None:
        try:
            hook_func(context)
        except Exception as e:
            logger.error(f"Non-critical hook {hook_func.__name__} failed: {e}")
    return wrapper

@hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=35)
@non_critical_hook
def optional_third_party_sync(context: HookContext) -> None:
    """Third-party sync - wrapped to be non-critical."""
    if context.result:
        third_party_api.sync_user(context.result)
    # Any exception here will be caught and logged by wrapper
```

### Exception Propagation

**Current Behavior:**
Hook exception → propagates → operation fails → endpoint returns error (applies to BEFORE and AFTER hooks)

**Patterns:**
```python
# Critical: let exception propagate
@hook_bll(UserManager.create, timing=HookTiming.BEFORE, priority=5)
def validate_required_fields(context: HookContext) -> None:
    if not context.kwargs.get('email'):
        raise HTTPException(status_code=400, detail="Email required")

# Non-critical: catch exception
@hook_bll(UserManager.create, timing=HookTiming.AFTER, priority=30)
def send_notification(context: HookContext) -> None:
    try:
        notification_service.send(context.result.email)
    except Exception as e:
        logger.error(f"Notification failed: {e}")
```

**Planned: Blocking Boolean Parameter**
```python
# Proposed syntax (not yet implemented)
@hook_bll(UserManager.create, timing=HookTiming.AFTER, blocking=False)
def optional_sync(context: HookContext) -> None:
    external_api.sync_user(context.result)  # Exception won't fail operation

@hook_bll(UserManager.create, timing=HookTiming.BEFORE, blocking=True)  # default
def required_validation(context: HookContext) -> None:
    validate_business_rules(context.kwargs)  # Exception fails operation
```

**Semantics:**
- `blocking=True` (default): Exception propagates, fails operation
- `blocking=False`: Exception logged, operation continues
- Use `blocking=False` carefully in BEFORE hooks (can cause inconsistent state)

**Current workaround:** Use try/except blocks or `@non_critical_hook` wrapper.

## Target ID and Caching Patterns

### Using Target ID with Hooks

```python
@hook_bll(UserManager.get, timing=HookTiming.AFTER, priority=20)
def cache_retrieved_user(context: HookContext) -> None:
    """Cache user after successful retrieval."""
    if context.result and context.manager.target_id:
        # The target is automatically cached in manager.target
        logger.debug(f"User {context.manager.target_id} cached in manager")
        
        # Add to external cache if needed
        redis_cache.set(f"user:{context.manager.target_id}", context.result, ttl=300)

@hook_bll(UserManager.update, timing=HookTiming.BEFORE, priority=10)
def validate_target_permissions(context: HookContext) -> None:
    """Validate permissions using cached target."""
    if context.manager.target_id:
        # Use cached target if available
        target_user = context.manager.target
        if target_user and target_user.is_system_user:
            raise HTTPException(status_code=403, detail="Cannot modify system user")

@hook_bll(UserManager, timing=HookTiming.BEFORE, priority=15)
def auto_populate_target_id(context: HookContext) -> None:
    """Auto-populate target_id for get/update/delete operations."""
    if context.method_name in ['get', 'update', 'delete']:
        if not context.manager.target_id and 'id' in context.kwargs:
            context.manager.target_id = context.kwargs['id']
```

## Multi-Manager Hook Patterns

### Cross-Manager Hook Registration

```python
# Apply the same hook to multiple managers
def register_audit_hooks():
    """Register audit hooks across multiple managers."""
    
    managers_to_audit = [UserManager, TeamManager, ExtensionManager, AbilityManager]
    
    for manager_class in managers_to_audit:
        @hook_bll(manager_class, timing=HookTiming.BEFORE, priority=1)
        def audit_operation(context: HookContext) -> None:
            """Audit all operations across multiple managers."""
            logger.info(
                f"Operation: {context.manager.__class__.__name__}.{context.method_name} "
                f"by user: {context.manager.requester.id}"
            )

# Alternative approach using a decorator factory
def create_audit_hook(manager_classes: List[Type]):
    """Factory to create audit hooks for multiple managers."""
    def audit_hook(context: HookContext) -> None:
        logger.info(f"{context.manager.__class__.__name__}.{context.method_name}")
    
    # Register for all specified managers
    for manager_class in manager_classes:
        hook_bll(manager_class, timing=HookTiming.BEFORE, priority=1)(audit_hook)

# Usage
create_audit_hook([UserManager, TeamManager, ExtensionManager])
```

## Best Practices and Guidelines

### Hook Organization

1. **File Organization**: Keep hooks in separate files organized by concern:
   ```
   hooks/
   ├── audit_hooks.py      # Audit and logging hooks
   ├── security_hooks.py   # Security and validation hooks
   ├── performance_hooks.py # Performance monitoring hooks
   └── integration_hooks.py # External service integration hooks
   ```

2. **Naming Conventions**: Use descriptive names that indicate:
   - What the hook does
   - When it runs (before/after)
   - What triggers it (method/class)

3. **Documentation**: Always include docstrings explaining:
   - Purpose of the hook
   - When it executes
   - What conditions affect it
   - Any side effects

### Performance Considerations

1. **Priority Assignment**: Use lower priorities for critical operations, higher for optional ones
2. **Conditional Execution**: Use conditions to avoid unnecessary hook execution
3. **Error Handling**: Wrap non-critical hooks to prevent operation failure
4. **Async Operations**: For I/O operations in hooks, consider async patterns (future enhancement)

### Testing Patterns

```python
# Testing hooks in isolation
def test_user_validation_hook():
    """Test user validation hook behavior."""
    # Create mock context
    mock_manager = Mock(spec=UserManager)
    mock_manager.requester.id = "test-user-id"
    
    context = HookContext(
        manager=mock_manager,
        method_name="create",
        args=(),
        kwargs={'email': 'test@blocked.com'},
        timing=HookTiming.BEFORE
    )
    
    # Test hook execution
    with pytest.raises(HTTPException) as exc_info:
        validate_user_creation(context)
    
    assert exc_info.value.status_code == 403
    assert "Domain blocked" in str(exc_info.value.detail)

# Testing hook integration
def test_user_creation_with_hooks(user_manager):
    """Test that hooks execute correctly during user creation."""
    # This would test the full flow including hook execution
    # Verify audit logs, data transformations, etc.
    pass
```

This comprehensive guide covers all aspects of the enhanced BLL hook system. Use these patterns to implement robust, maintainable, and performant hook-based functionality in your BLL managers.
