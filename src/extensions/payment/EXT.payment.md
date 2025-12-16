# Payment Extension

This document describes the Payment extension implementation.

> **Extension Architecture**: For general extension patterns, architecture, and concepts, see [EXT.Patterns.md](../EXT.Patterns.md).

Payment extension for AGInfrastructure providing comprehensive payment functionality through the Provider Rotation System with Stripe integration and User model extensions.

## Overview

The `payment` extension integrates payment capabilities into the core system through:
- **User Model Extensions**: Adds payment fields to UserModel using `@extension_model`
- **External API Integration**: Stripe customer management through AbstractExternalModel pattern
- **Provider Rotation System**: Automated provider management and failover
- **Authentication Hooks**: Subscription validation during login flows
- **Manager Method Injection**: Extends UserManager with payment-specific methods

## Architecture

### Extension Class Structure
```python
class EXT_Payment(AbstractStaticExtension):
    """Payment extension with Provider Rotation System integration."""
    
    name: str = "payment"
    friendly_name: str = "Payment Processing"
    version: str = "1.0.0"
    description: str = "Payment extension providing comprehensive payment processing abilities via Provider Rotation System"
    
    # Provider discovery - cache for providers
    _providers: List[Type] = []
    
    # providers property inherited from AbstractStaticExtension

### User Model Extension Pattern
The extension uses `@extension_model` to inject payment fields directly into the core UserModel:

```python
# In BLL_Payment.py
from lib.Pydantic2SQLAlchemy import extension_model
from logic.BLL_Auth import UserModel

@extension_model(UserModel)
class Payment_UserModel(BaseModel):
    """
    Payment extension for User model with Stripe customer integration.
    This automatically adds:
    - external_payment_id: Optional[str] field
    - stripe_customer: Optional[Stripe_CustomerModel] navigation property
    """
    
    external_payment_id: Optional[str] = Field(
        None, description="External payment ID linking to payment provider customer"
    )
    stripe_customer: Optional[Stripe_CustomerModel] = Field(
        None, description="Stripe customer navigation property"
    )
    
    class Create(BaseModel):
        external_payment_id: Optional[str] = None
    
    class Update(BaseModel):
        external_payment_id: Optional[str] = None
    
    class Search(BaseModel):
        external_payment_id: Optional[str] = None
```

## Provider Integration

### Stripe External Model
The extension provides Stripe customer integration through AbstractExternalModel:

```python
from extensions.AbstractExternalModel import AbstractExternalModel

class Stripe_CustomerModel(AbstractExternalModel):
    """External model representing Stripe customers."""
    
    # Pydantic model fields
    id: str = Field(..., description="Stripe customer ID")
    email: str = Field(..., description="Customer email")
    name: Optional[str] = Field(None, description="Customer name")
    
    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal data format to Stripe API format."""
        return {
            "email": internal_data.get("email"),
            "name": internal_data.get("name"),
            "metadata": internal_data.get("metadata", {})
        }
    
    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Stripe API format to internal data format."""
        return {
            "id": external_data.get("id"),
            "email": external_data.get("email"), 
            "name": external_data.get("name"),
            "created_at": external_data.get("created")
        }
    
    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """Create customer via Stripe provider."""
        # Implementation calls Stripe API using provider_instance credentials
        pass
```

### Abstract Provider Definition
```python
# In AbstractProvider_Payment.py
class AbstractPaymentProvider(AbstractStaticProvider):
    """Abstract base class for payment providers."""
    
    extension_type: ClassVar[str] = "payment"
    
    @classmethod
    @abstractmethod
    @ability(name="payment_create")
    async def create_payment(
        cls,
        provider_instance: ProviderInstanceModel,
        amount: Decimal,
        currency: str,
        customer_id: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a payment/charge."""
        pass
    
    @classmethod
    @abstractmethod
    @ability(name="customer_create")
    async def create_customer(
        cls,
        provider_instance: ProviderInstanceModel,
        email: str,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a customer."""
        pass
```

### Stripe Provider Implementation
```python
# In PRV_Stripe_Payment.py
class PRV_Stripe_Payment(AbstractPaymentProvider):
    """Stripe payment provider for rotation system."""
    
    name: ClassVar[str] = "stripe"
    
    @classmethod
    def bond_instance(cls, instance: ProviderInstanceModel) -> AbstractProviderInstance:
        """Bond a provider instance for API operations."""
        return StripeProviderInstance(instance)
    
    async def create_payment(
        self,
        provider_instance: ProviderInstanceModel,
        amount: Decimal,
        currency: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Create payment using Stripe API."""
        # Implementation using bonded instance
        pass

### External Manager Integration
```python
class Stripe_CustomerManager(AbstractExternalManager):
    """Manager for Stripe customer operations via Provider Rotation System."""
    
    Model = Stripe_CustomerModel
    
    def __init__(self, requester_id: str, rotation_manager=None, **kwargs):
        """Initialize with rotation manager for provider selection."""
        super().__init__(
            requester_id=requester_id,
            rotation_manager=rotation_manager,
            **kwargs
        )
    
    # Inherits all standard BLL methods (create, get, list, update, delete)
    # but routes through external APIs via Provider Rotation System
```

## UserManager Method Injection

### Payment Customer Management
The extension injects payment-specific methods directly into UserManager:

```python
def get_or_create_payment_customer(
    self,
    user_id: str,
    email: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """
    Get or create a payment customer for a user.
    This is properly injected as an instance method.
    """
    try:
        user = self.get(id=user_id)
        
        # Check if customer already exists
        if hasattr(user, "external_payment_id") and user.external_payment_id:
            return {
                "customer_id": user.external_payment_id,
                "created": False,
                "user_id": user_id,
            }
        
        # Create new Stripe customer
        stripe_customer_manager = Stripe_CustomerManager(
            requester_id=user_id,
            rotation_manager=None,  # Uses default rotation
        )
        
        customer = stripe_customer_manager.create(
            email=email or user.email,
            name=name or user.display_name,
            metadata={"user_id": user_id},
        )
        
        if customer and hasattr(customer, "id"):
            # Update user with the Stripe customer ID
            self.update(id=user_id, external_payment_id=customer.id)
            
            return {
                "customer_id": customer.id,
                "created": True,
                "user_id": user_id,
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to create payment customer"
            )
    
    except Exception as e:
        logger.error(f"Error getting or creating payment customer: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to get or create payment customer"
        )

# Properly inject as an instance method
UserManager.get_or_create_payment_customer = get_or_create_payment_customer
```

### Payment Information Access
```python
def get_user_payment_info(
    self, user_id: str, requester_id: Optional[str] = None
) -> dict:
    """Get comprehensive payment information for a user."""
    try:
        user = self.get(id=user_id)
        
        payment_info = {
            "user_id": user_id,
            "has_payment_setup": False,
            "external_payment_id": None,
            "stripe_customer": None,
        }
        
        # Check if user has payment setup
        if hasattr(user, "external_payment_id") and user.external_payment_id:
            payment_info["has_payment_setup"] = True
            payment_info["external_payment_id"] = user.external_payment_id
            
            # Get Stripe customer details
            try:
                stripe_manager = Stripe_CustomerManager(
                    requester_id=requester_id or self.requester.id,
                    db_manager=self.db_manager,
                    db=self.db,
                )
                customer = stripe_manager.get(id=user.external_payment_id)
                payment_info["stripe_customer"] = customer
            except Exception as e:
                logger.warning(f"Failed to get Stripe customer: {e}")
        
        return payment_info
    
    except Exception as e:
        logger.error(f"Error getting payment info: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to get user payment information"
        )

# Inject as instance method
UserManager.get_user_payment_info = get_user_payment_info
```

### Subscription Status Management
```python
def get_user_subscription_status(
    self, user_id: str, requester_id: Optional[str] = None
) -> dict:
    """Get subscription status for a user via Provider Rotation System."""
    try:
        payment_info = self.get_user_payment_info(
            user_id=user_id, 
            requester_id=requester_id
        )
        
        if not payment_info["has_payment_setup"]:
            return {"status": "inactive", "reason": "No payment method setup"}
        
        # TODO: Implement actual subscription checking via rotation system
        return {
            "status": "active",
            "subscription_id": "sub_mock",
            "current_period_end": "2025-12-31T23:59:59Z",
        }
    
    except Exception as e:
        logger.error(f"Error getting subscription status: {e}")
        return {"status": "unknown", "error": str(e)}

# Inject as instance method
UserManager.get_user_subscription_status = get_user_subscription_status
```

## Authentication Integration

### Login Subscription Validation Hook
The extension provides subscription validation during login:

```python
@hook_bll(UserManager.login, timing="after")
def validate_subscription_on_login(context: HookContext):
    """
    Hook that validates user subscription after successful login.
    Throws HTTP 402 Payment Required if subscription is inactive.
    """
    # Skip validation for system users
    if context.kwargs.get("login_data", {}).get("email") == os.environ.get("ROOT_EMAIL"):
        return
    
    # Skip if subscription validation is disabled
    if os.environ.get("DISABLE_SUBSCRIPTION_VALIDATION", "false").lower() == "true":
        return
    
    try:
        result = context.result
        if not result or not result.get("id"):
            return
        
        user_id = result["id"]
        
        # Create user manager with proper parameters
        user_manager = UserManager(
            requester_id=user_id, 
            db_manager=context.manager.db_manager, 
            db=context.kwargs.get("db") or context.manager.db
        )
        
        user = user_manager.get(id=user_id)
        
        # Skip validation if user doesn't have external payment ID
        if not hasattr(user, "external_payment_id") or not user.external_payment_id:
            return
        
        # Check subscription status
        subscription_status = user_manager.get_user_subscription_status(user_id=user_id)
        
        # Block login if subscription is inactive
        if subscription_status.get("status") == "inactive":
            raise HTTPException(
                status_code=402,  # Payment Required
                detail="Your subscription is inactive. Please update your payment method.",
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in subscription validation hook: {e}")
        # Don't block login for other errors
        return
```

## External Navigation Properties

### Automatic Stripe Customer Resolution
The User model extension includes navigation properties that automatically resolve Stripe customer data:

```python
# In the Payment_UserModel extension
stripe_customer: Optional[Stripe_CustomerModel] = Field(
    None, description="Stripe customer navigation property"
)

# Usage
user = user_manager.get(id=user_id)
if user.stripe_customer:  # Automatically resolves via Provider Rotation System
    logger.debug(f"Stripe customer: {user.stripe_customer.email}")
    logger.debug(f"Customer name: {user.stripe_customer.name}")
```

## Configuration

### Environment Variables
| Variable                          | Default   | Description                                     |
| --------------------------------- | --------- | ----------------------------------------------- |
| `DISABLE_SUBSCRIPTION_VALIDATION` | `"false"` | Disable subscription validation on login        |

### Provider Configuration
Payment providers are configured through the Provider Rotation System rather than environment variables:
- API keys and secrets are stored in ProviderInstance records
- Configuration is managed through the rotation system
- Providers are auto-discovered from PRV_*.py files

## Configuration Management

### Provider System Integration
Payment configuration is managed through the Provider Rotation System rather than direct environment variables:

```python
# Provider instances store Stripe configuration
{
    "name": "Root_Stripe",
    "provider_name": "Stripe",
    "api_key": stripe_api_key,
    "model_name": stripe_webhook_secret,
    "enabled": True,
}
```

### Payment Customer Creation Flow
1. **User Registration**: New user created through UserManager
2. **Payment Setup**: Call `get_or_create_payment_customer()` when needed
3. **Stripe Integration**: Creates Stripe customer via Provider Rotation System
4. **Database Update**: Updates user record with `external_payment_id`
5. **Navigation Property**: Automatic resolution of Stripe customer data

## Usage Examples

### Payment Customer Setup
```python
# Create payment customer for user
user_manager = UserManager(requester_id=user_id, db_manager=db_manager)
result = user_manager.get_or_create_payment_customer(
    user_id=user_id,
    email=user.email,
    name=user.display_name
)

if result["created"]:
    logger.debug(f"Created new Stripe customer: {result['customer_id']}")
else:
    logger.debug(f"Using existing customer: {result['customer_id']}")
```

### Payment Information Access
```python
# Get comprehensive payment information
payment_info = user_manager.get_user_payment_info(user_id=user_id)

if payment_info["has_payment_setup"]:
    stripe_customer = payment_info["stripe_customer"]
    logger.debug(f"Customer email: {stripe_customer.email}")
else:
    logger.debug("User has no payment setup")
```

### Subscription Status Check
```python
# Check subscription status
status = user_manager.get_user_subscription_status(user_id=user_id)

if status["status"] == "active":
    logger.debug("Subscription is active")
elif status["status"] == "inactive":
    logger.debug("Subscription needs renewal")
else:
    logger.debug(f"Unknown status: {status}")
```

## Testing Integration

### Extension Testing
```python
class TestPaymentExtension(AbstractEXTTest):
    extension_class = EXT_Payment
    
    def test_user_model_extension(self, extension_server, extension_db):
        """Test User model extension with payment fields."""
        # Test runs in isolated test.payment.database.db
        # User model automatically includes payment extension fields
        pass
```

### Provider Testing
```python
class TestStripeProvider(AbstractPRVTest):
    provider_class = Stripe_PaymentProvider
    
    def test_customer_creation(self, extension_server):
        """Test Stripe customer creation through provider."""
        # Test runs in payment extension's isolated environment
        pass
```

## Error Handling

### Payment Customer Creation
- Handles Stripe API failures gracefully
- Logs detailed error information
- Returns appropriate HTTP status codes
- Doesn't expose sensitive API details

### Subscription Validation
- Graceful degradation when validation fails
- Doesn't block login for system errors
- Comprehensive logging for debugging
- Configurable validation bypass

### External API Integration
- Network failure handling
- API rate limit awareness
- Provider rotation system fallback
- Error isolation from core functionality

## Security Considerations

1. **API Key Management**: Stripe keys managed through Provider system, not environment variables
2. **Customer Data**: External payment IDs only, no sensitive payment data stored
3. **Subscription Validation**: Optional validation that can be disabled
4. **Error Isolation**: Payment failures don't affect core authentication
5. **Navigation Properties**: Secure resolution through Provider Rotation System

## Best Practices

1. **Model Extension**: Use `@extension_model` for clean field injection
2. **Method Injection**: Inject methods properly into existing managers
3. **Provider Integration**: Leverage Provider Rotation System for external APIs
4. **Error Handling**: Comprehensive error handling with logging
5. **Configuration**: Use Provider system instead of direct environment variables
6. **Hook Integration**: Use hooks for automatic workflow integration
7. **Navigation Properties**: Use external navigation properties for automatic resolution
8. **Testing Isolation**: Ensure proper test isolation for payment functionality