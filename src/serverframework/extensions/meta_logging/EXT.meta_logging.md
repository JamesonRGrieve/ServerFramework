# Meta Logging Extension

This document describes the Meta Logging extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

The Meta Logging extension provides comprehensive audit logging, failed login tracking, and privacy compliance logging for AGInfrastructure using InfluxDB for time-series data storage.

## Overview

The `meta_logging` extension enables system-wide logging capabilities for audit trails, security monitoring, and compliance reporting. It integrates with InfluxDB to store time-series logging data efficiently.

## Architecture

### Extension Class Structure
```python
class EXT_Meta_Logging(AbstractStaticExtension):
    name = "meta_logging"
    version = "1.0.0"
    description = "Meta Logging extension with audit trails, failed login tracking, and privacy compliance logging"
    
    # Static abilities
    abilities: Set[str] = {
        "audit_logging",
        "failed_login_tracking",
        "system_logging",
    }
```

### Key Components

#### Business Logic Layer (`BLL_Meta_Logging.py`)
- **MetaLoggingManager**: Core manager for logging operations
- **AuditLogModel**: Audit event data model
- **FailedLoginAttemptModel**: Failed login tracking model
- **SystemLogModel**: System event logging model

### Integration with InfluxDB
The extension requires the database extension with InfluxDB support for storing time-series log data.

## Dependencies

### Extension Dependencies
- **database** - Required for InfluxDB logging backend

### PIP Dependencies
- `influxdb-client>=1.36.0` - InfluxDB 2.x Python client for time-series logging

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `INFLUXDB_HOST` | `""` | InfluxDB host address |
| `INFLUXDB_PORT` | `"8086"` | InfluxDB port |
| `INFLUXDB_DATABASE` | `""` | InfluxDB database name (v1.x) |
| `INFLUXDB_USERNAME` | `""` | InfluxDB username (v1.x) |
| `INFLUXDB_PASSWORD` | `""` | InfluxDB password (v1.x) |
| `INFLUXDB_VERSION` | `"1"` | InfluxDB version (1 or 2) |
| `INFLUXDB_ORG` | `""` | InfluxDB organization (v2.x) |
| `INFLUXDB_TOKEN` | `""` | InfluxDB auth token (v2.x) |
| `INFLUXDB_BUCKET` | `""` | InfluxDB bucket (v2.x) |

## Abilities

### Core Abilities
- **audit_logging** - Log audit events for user actions and system events
- **failed_login_tracking** - Track and analyze failed login attempts
- **system_logging** - Log system-level events and errors

### Conditional Abilities
When InfluxDB is available:
- **privacy_audit** - Privacy impact logging for compliance
- **compliance_reporting** - Generate compliance reports

## Logging Functions

### Audit Event Logging
```python
result = await extension.log_audit_event(
    user_id="user123",
    action="update_profile",
    resource_type="user",
    resource_id="user456",
    ip_address="192.168.1.1",
    user_agent="Mozilla/5.0...",
    success=True,
    error_message=None,
    additional_data={"field": "email", "old_value": "old@example.com"},
    privacy_impact=True,
    data_categories=["personal_info", "contact_info"]
)
```

### Failed Login Tracking
```python
result = await extension.log_failed_login(
    username="user@example.com",
    ip_address="192.168.1.1",
    user_agent="Mozilla/5.0...",
    failure_reason="invalid_password",
    attempt_count=3,
    is_blocked=False,
    country="US",
    risk_score=0.7
)
```

### System Event Logging
```python
result = await extension.log_system_event(
    level="ERROR",
    component="authentication",
    message="Failed to validate JWT token",
    user_id="user123",
    request_id="req-456",
    additional_data={"error_code": "JWT_EXPIRED"}
)
```

## Query Functions

### Query Audit Logs
```python
logs = await extension.query_audit_logs(
    start_time="2024-01-01T00:00:00Z",
    end_time="2024-01-31T23:59:59Z",
    user_id="user123",
    action="update_profile",
    resource_type="user",
    privacy_impact=True
)
```

### Query Failed Logins
```python
failed_attempts = await extension.query_failed_logins(
    start_time="2024-01-01T00:00:00Z",
    end_time="2024-01-31T23:59:59Z",
    username="user@example.com",
    ip_address="192.168.1.1",
    is_blocked=True,
    min_risk_score=0.5
)
```

## Privacy and Compliance

### Privacy Report Generation
```python
report = await extension.generate_privacy_report(days=30)
# Returns:
# {
#     "success": true,
#     "report": {
#         "period_days": 30,
#         "privacy_events": 145,
#         "data_categories_accessed": ["personal_info", "financial_data"],
#         "users_affected": 89,
#         "high_risk_operations": 12
#     }
# }
```

## Audit Hook System

The extension registers comprehensive audit hooks for BLL operations:

### Hook Registration
```python
def _register_audit_hooks(self):
    """Register hooks for comprehensive audit logging throughout the system."""
    # Hooks are registered for:
    # - User operations (create, update, delete)
    # - Team operations
    # - Authentication events
    # - Authorization changes
    # - Data access patterns
```

## Data Models

### AuditLogModel
```python
class AuditLogModel:
    user_id: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    ip_address: Optional[str]
    user_agent: Optional[str]
    success: bool
    error_message: Optional[str]
    additional_data: Optional[Dict[str, Any]]
    privacy_impact: bool
    data_categories: List[str]
    timestamp: datetime
```

### FailedLoginAttemptModel
```python
class FailedLoginAttemptModel:
    username: str
    ip_address: str
    user_agent: Optional[str]
    failure_reason: str
    attempt_count: int
    is_blocked: bool
    country: Optional[str]
    risk_score: float
    timestamp: datetime
```

### SystemLogModel
```python
class SystemLogModel:
    level: str  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    component: str
    message: str
    user_id: Optional[str]
    request_id: Optional[str]
    additional_data: Optional[Dict[str, Any]]
    timestamp: datetime
```

## Security Features

### Required Permissions
- `logging:read` - Read log data
- `logging:write` - Write log data
- `logging:manage` - Manage logging configuration

### Security Monitoring
- Failed login attempt tracking with risk scoring
- IP-based blocking detection
- User agent analysis
- Geographic location tracking

## Health Monitoring

### Audit Summary
```python
summary = extension.get_audit_summary(hours=24)
# Returns:
# {
#     "hours": 24,
#     "audit_events": 1234,
#     "failed_logins": 45,
#     "privacy_events": 67,
#     "status": "InfluxDB available"
# }
```

### Configuration Validation
```python
issues = extension.validate_config()
# Returns list of configuration issues if any
```

## Testing

### Extension Testing
```python
class TestMetaLoggingExtension(AbstractEXTTest):
    extension_class = EXT_Meta_Logging
    
    def test_audit_logging(self, extension_server, extension_db):
        """Test audit logging functionality."""
        # Test runs with InfluxDB integration
        pass
```

## Best Practices

### For Developers
1. Log all security-sensitive operations
2. Include relevant context in audit logs
3. Use appropriate log levels for system events
4. Tag privacy-impacting operations

### For Operations
1. Configure InfluxDB retention policies
2. Monitor failed login patterns
3. Set up alerts for suspicious activities
4. Regular privacy compliance reports

### For Security
1. Track all authentication attempts
2. Monitor data access patterns
3. Implement risk-based blocking
4. Maintain audit trail integrity

## Troubleshooting

### Common Issues
- **InfluxDB Connection**: Ensure InfluxDB is running and accessible
- **Missing Database Extension**: Meta logging requires database extension
- **Permission Errors**: Verify logging permissions are granted
- **Time Series Queries**: Check time zone handling in queries

### Debug Mode
Enable detailed logging to troubleshoot meta logging issues.