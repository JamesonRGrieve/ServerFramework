# Authentication MFA Extension

This document describes the MFA extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

Multi-Factor Authentication extension for AGInfrastructure providing comprehensive MFA capabilities including TOTP, Email, and SMS-based authentication with recovery code support.

## Overview

The `auth_mfa` extension provides multi-factor authentication functionality through a layered architecture with database models, business logic managers, and API endpoints. The extension integrates with the core authentication system to add additional security layers for user accounts.

## Architecture

### Extension Class Structure
```python
class EXT_Auth_MFA(AbstractStaticExtension):
    name: ClassVar[str] = "auth_mfa"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Multi-Factor Authentication extension with TOTP, Email, and SMS support"
    
    # Static abilities provided by this extension
    _abilities: ClassVar[Set[str]] = {
        "mfa_totp",
        "mfa_email",
        "mfa_sms",
        "mfa_recovery_codes",
    }
```

### Key Components

#### Database Layer (`DB_Auth_MFA.py`)
- **DB_MultifactorMethod**: Stores user MFA method configurations
- **DB_MultifactorRecoveryCode**: Stores hashed recovery codes for account recovery

#### Business Logic Layer (`BLL_Auth_MFA.py`)
- **MultifactorMethodManager**: CRUD operations and MFA method management
- **MultifactorRecoveryCodeManager**: Recovery code generation and verification
- **TOTP Integration**: Time-based One-Time Password support via PyOTP

#### Endpoint Layer (`EP_Auth_MFA.py`)
- RESTful API endpoints for MFA method management
- Code verification endpoints for TOTP and recovery codes
- Recovery code generation endpoints

#### Extension Layer (`EXT_Auth_MFA.py`)
- Extension metadata and configuration
- Dependency management
- Static abilities declaration

## Data Models

### MultifactorMethod
Core MFA method model with support for multiple authentication types:

```python
class MultifactorMethodModel:
    method_type: MultifactorMethodType  # TOTP, EMAIL, SMS
    identifier: Optional[str]  # Phone/email for SMS/email methods
    totp_secret: Optional[str]  # Base32 encoded secret for TOTP
    totp_algorithm: str  # TOTP algorithm (SHA1, SHA256, etc.)
    totp_digits: int  # Number of digits in TOTP code (6 or 8)
    totp_period: int  # TOTP validity period in seconds
    is_enabled: bool  # Whether method is active
    is_primary: bool  # Primary method for the user
    always_ask: bool  # Whether to always prompt for this method
    verification: bool  # Whether method is verified
    last_used: Optional[datetime]  # Last usage timestamp
```

### MultifactorRecoveryCode
Recovery code model for account recovery when primary MFA is unavailable:

```python
class MultifactorRecoveryCodeModel:
    multifactormethod_id: str  # Foreign key to MFA method
    code_hash: str  # Bcrypt hashed recovery code
    code_salt: str  # Salt used for hashing
    is_used: bool  # Whether code has been consumed
    used_at: Optional[datetime]  # When code was used
    created_ip: Optional[str]  # IP address of creation
```

## Dependencies

### PIP Dependencies
- `pyotp>=2.8.0`: Required for TOTP functionality
- `qrcode[pil]>=7.4.0`: Optional QR code generation for TOTP setup

### Extension Dependencies
- Email extension (optional): For email-based MFA code delivery
- SMS provider (optional): For SMS-based MFA code delivery

## Environment Variables

| Variable              | Default              | Description                               |
| --------------------- | -------------------- | ----------------------------------------- |
| `MFA_ENABLED`         | `"true"`             | Enable/disable MFA functionality          |
| `MFA_ISSUER_NAME`     | `"AGInfrastructure"` | Issuer name displayed in authenticator apps |
| `MFA_RECOVERY_CODES_COUNT` | `"10"`          | Default number of recovery codes to generate |
| `MFA_TOTP_WINDOW`     | `"1"`                | Number of time windows to check for TOTP  |

## API Endpoints

### MFA Method Management
- `GET /v1/user/mfa` - List user's MFA methods
- `POST /v1/user/mfa` - Create new MFA method
- `GET /v1/user/mfa/{id}` - Get specific MFA method
- `PUT /v1/user/mfa/{id}` - Update MFA method
- `DELETE /v1/user/mfa/{id}` - Delete MFA method

### Code Verification
- `POST /v1/user/mfa/{id}/verify` - Verify MFA code
- `POST /v1/user/mfa/{id}/recovery/verify` - Verify recovery code

### Recovery Code Management
- `POST /v1/user/mfa/{id}/recovery/generate` - Generate new recovery codes

## Manager Usage Examples

### Creating an MFA Method
```python
from extensions.auth_mfa.BLL_Auth_MFA import MultifactorMethodManager, MultifactorMethodType

# Initialize manager
manager = MultifactorMethodManager(
    requester_id=user_id,
    model_registry=model_registry
)

# Create TOTP method
totp_method = manager.create(
    user_id=user_id,
    method_type=MultifactorMethodType.TOTP,
    is_primary=True
)

# Create SMS method
sms_method = manager.create(
    user_id=user_id,
    method_type=MultifactorMethodType.SMS,
    identifier="+1234567890",
    is_primary=False
)
```

### Verifying Codes
```python
# Verify TOTP code
is_valid = manager.verify_mfa_code(method_id, "123456")

# Generate and verify recovery codes
recovery_manager = manager.recovery_codes
codes = recovery_manager.generate_recovery_codes(method_id, count=10)
is_valid = recovery_manager.verify_recovery_code(method_id, "ABCD-1234")
```

## Security Features

### TOTP Implementation
- Configurable algorithms (SHA1, SHA256, SHA512)
- Configurable code length (6 or 8 digits)
- Configurable time window (15, 30, or 60 seconds)
- Clock drift tolerance (±1 time window)

### Recovery Codes
- Bcrypt hashed with unique salts
- One-time use enforcement
- IP address tracking for audit trails
- Secure random generation (XXXX-XXXX format)

### Rate Limiting
- Built-in rate limiting for verification attempts
- Configurable limits per user per time window
- Automatic lockout on excessive attempts

### Audit Logging
- Comprehensive logging of all MFA operations
- Security validation hooks
- Operation timing and success tracking
- Sensitive operation alerts

## Hook Integration

The extension provides security hooks that integrate with the authentication system:

### Security Validation Hook
Validates all MFA manager operations for proper authorization

### Audit Hook
Logs all MFA operations with timing and success metrics

### Rate Limiting Hook
Prevents brute force attacks on MFA verification

### TOTP Validation Hook
Ensures TOTP parameters meet security requirements

## Testing

The extension includes comprehensive test coverage:

- **DB Tests**: Database model validation and relationships
- **BLL Tests**: Business logic testing with various MFA scenarios
- **EP Tests**: API endpoint testing (when implemented)
- **Extension Tests**: Extension lifecycle and ability testing

### Running Tests
```bash
# Run all MFA tests
pytest src/extensions/auth_mfa/ -v

# Run specific test categories
pytest -m "auth_mfa" -v
pytest -m "extension" -v
```

## Migration Support

Database migrations are provided for:
- Initial schema creation (`e52c7c5c5717_initial_schema.py`)
- Foreign key relationships (`add_recovery_code_fk_2025062501.py`)

Migrations are automatically applied when the extension is loaded.

## Best Practices

### For Developers
1. Always use managers through the model registry pattern
2. Implement proper error handling for MFA operations
3. Use the hook system for security auditing
4. Test with multiple MFA method types

### For Users
1. Enable at least one TOTP method as primary
2. Generate and securely store recovery codes
3. Verify MFA methods after creation
4. Use different MFA methods for backup

### Security Considerations
1. Recovery codes should be displayed only once
2. Implement proper session management post-MFA
3. Consider IP-based restrictions for sensitive operations
4. Regular audit of MFA usage patterns

## Troubleshooting

### Common Issues
- **PyOTP Import Error**: Install with `pip install pyotp`
- **QR Code Generation**: Install with `pip install qrcode[pil]`
- **Clock Drift**: Ensure server time synchronization
- **Rate Limiting**: Check rate limit configuration and user behavior

### Debug Mode
Set `MFA_DEBUG=true` for additional logging during development.