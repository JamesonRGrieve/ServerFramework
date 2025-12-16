# Email Extension

This document describes the Email extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

The Email extension provides comprehensive email functionality for AGInfrastructure through the Provider Rotation System, with built-in support for various email providers.

## Overview

The `email` extension enables email sending capabilities, template management, and delivery tracking. It integrates with authentication workflows and provides both meta abilities (extension-level) and provider abilities.

## Architecture

### Extension Class Structure
```python
class EXT_EMail(AbstractStaticExtension):
    name: ClassVar[str] = "email"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Email extension for interacting with various email providers"
    
    # Static abilities (meta abilities)
    _abilities: ClassVar[Set[str]] = {
        "email_status",  # Meta ability to check email service status
        "email_config",  # Meta ability to manage email configuration
    }
```

### Provider System

The Email extension uses the Provider Rotation System with abstract and concrete providers:

#### Abstract Provider
```python
class AbstractProvider_EMail(AbstractStaticProvider):
    """Abstract email provider defining required abilities."""
    extension_type: ClassVar[str] = "email"
    
    @abstractmethod
    @ability("send_email")
    async def send_email(self, recipient: str, subject: str, body: str, **kwargs) -> Dict[str, Any]:
        """Send an email - must be implemented by concrete providers."""
        pass
```

#### Concrete Providers
- **SendGrid** (`PRV_SendGrid_EMail`) - Production email service provider

### Hook-Based Integration

The extension uses hooks to automatically send emails for system events:

```python
@AbstractStaticExtension.hook("bll", "invitations", "invitation", "create", "after")
async def send_invitation_email(cls, invitation, **kwargs):
    """Hook to send invitation email after invitation is created."""
    if cls.root:
        result = await cls.root.rotate(
            cls.AbstractEmailProvider.send_email,
            recipient=invitation.email,
            subject=f"You've been invited to {invitation.team.name}",
            body=invitation_body
        )
```

## Dependencies

### PIP Dependencies
- `sendgrid>=6.10.0` - SendGrid Python library

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SENDGRID_API_KEY` | `""` | SendGrid API key |
| `SENDGRID_FROM_EMAIL` | `""` | Default sender email address |
| `EMAIL_PROVIDER` | `"sendgrid"` | Default email provider |
| `SMTP_SERVER` | `""` | SMTP server for generic email |
| `SMTP_PORT` | `"587"` | SMTP server port |
| `IMAP_SERVER` | `""` | IMAP server for email retrieval |
| `IMAP_PORT` | `"993"` | IMAP server port |

## Abilities

### Meta Abilities (Extension-Level)
- **email_status** - Get the current status of the email extension
- **email_config** - Get the current email configuration

### Provider Abilities (via Rotation System)
- **send_email** - Send individual emails
- **send_bulk_email** - Send bulk emails
- **send_template_email** - Send emails using templates
- **track_email** - Track email delivery status

## Provider Registration

The extension automatically registers email providers through the seeding system:

### BLL Hook Integration
```python
def register_email_providers_hook():
    """Hook to register email providers in the core Provider table"""
    providers_to_add = []
    
    if env("SENDGRID_API_KEY") and env("SENDGRID_FROM_EMAIL"):
        providers_to_add.append({
            "name": "SendGrid", 
            "friendly_name": "SendGrid Email Service"
        })
    
    return providers_to_add
```

### Provider Instance Registration
```python
def register_email_provider_instances_hook():
    """Hook to register email provider instances"""
    instances_to_add = []
    
    if sendgrid_key and sendgrid_email:
        instances_to_add.append({
            "name": "Root_SendGrid",
            "_provider_name": "SendGrid",
            "api_key": sendgrid_key,
            "model_name": sendgrid_email,  # from_email stored here
            "enabled": True,
        })
    
    return instances_to_add
```

## Usage Examples

### Checking Email Status
```python
status = EXT_EMail.get_extension_status()
# Returns:
# {
#     "extension": "email",
#     "version": "1.0.0",
#     "providers_available": 1,
#     "configured": true,
#     "default_provider": "sendgrid"
# }
```

### Sending Email via Rotation System
```python
# Send email using the rotation system
if EXT_EMail.root:
    result = await EXT_EMail.root.rotate(
        EXT_EMail.AbstractEmailProvider.send_email,
        recipient="user@example.com",
        subject="Welcome!",
        body="Welcome to our platform!"
    )
```

### Invitation Email Integration
The extension automatically sends invitation emails when invitations are created:

```python
# This happens automatically via hooks when creating invitations
invitation = invitation_manager.create(
    email="new_user@example.com",
    team_id=team_id
)
# Email is sent automatically via the hook
```

## External Model Integration

The extension provides external models for email tracking:

### SendGrid Models
```python
class SendGrid_EmailModel(AbstractExternalModel):
    """External model for SendGrid email tracking."""
    
    message_id: str = Field(..., description="SendGrid message ID")
    status: str = Field(..., description="Email delivery status")
    opens: int = Field(0, description="Number of opens")
    clicks: int = Field(0, description="Number of clicks")
    bounces: int = Field(0, description="Number of bounces")
```

## Configuration Validation

The extension provides configuration validation:

```python
is_configured = EXT_EMail.validate_configuration()
# Checks for required environment variables based on provider
```

## Testing

### Extension Testing
```python
class TestEmailExtension(AbstractEXTTest):
    extension_class = EXT_EMail
    
    def test_email_functionality(self, extension_server, extension_db):
        """Test email extension in isolated environment."""
        # Runs with test.email.database.db
        pass
```

### Provider Testing
```python
class TestSendGridProvider(AbstractPRVTest):
    provider_class = PRV_SendGrid_EMail
    
    def test_sendgrid_email(self, extension_server, extension_db):
        """Test SendGrid provider functionality."""
        # Inherits email extension's test environment
        pass
```

## Security Features

### API Key Management
- API keys stored securely in environment variables
- Provider instance bonding for secure credential access
- No logging of sensitive information

### Email Validation
- Recipient email validation
- SPF/DKIM configuration support
- Bounce and complaint handling

## Best Practices

### For Developers
1. Use the Provider Rotation System for all email operations
2. Implement proper error handling for email failures
3. Use templates for consistent email formatting
4. Test with multiple email providers

### For Operations
1. Configure SPF and DKIM records for deliverability
2. Monitor email bounce and complaint rates
3. Set up webhook endpoints for delivery tracking
4. Rotate API keys regularly

### Email Design
1. Use responsive HTML templates
2. Include plain text alternatives
3. Test across email clients
4. Follow anti-spam best practices

## Troubleshooting

### Common Issues
- **Missing API Key**: Ensure `SENDGRID_API_KEY` is set
- **Invalid From Email**: Verify `SENDGRID_FROM_EMAIL` is authorized
- **Delivery Failures**: Check SendGrid dashboard for bounce reasons
- **Rate Limits**: Monitor SendGrid API rate limits

### Debug Mode
Enable detailed logging with appropriate log levels for troubleshooting email issues.