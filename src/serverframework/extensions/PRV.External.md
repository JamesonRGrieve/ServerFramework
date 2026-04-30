# AbstractExternalModel System

## Overview

The AbstractExternalModel system provides a unified interface for working with external API resources using the same patterns as our internal database models. This allows external APIs to be managed through the Provider Rotation System while maintaining consistent interfaces for endpoints and GraphQL abstractions.

## Architecture

### Core Components

1. **AbstractExternalModel** - Base class for external API resource models
2. **AbstractExternalAPIClient** - Provides database-like interface for external APIs  
3. **AbstractExternalManager** - Manager class for external models (similar to AbstractBLLManager)

### Integration Points

- **Provider Rotation System** - All external API calls go through provider rotation for reliability
- **Endpoint/GraphQL Compatibility** - External models can be used with existing endpoint patterns
- **Consistent Interface** - Same CRUD operations as internal models (.create(), .get(), .list(), etc.)

## Key Classes

### AbstractExternalModel

Base class for representing external API resources. Similar to internal Pydantic models but with additional methods for API integration.

**Key Properties:**
- `external_resource` - API resource identifier (e.g., "products", "customers")
- `field_mappings` - Maps internal field names to external API field names
- `provider_methods` - Maps CRUD operations to provider method names

**Key Methods:**
- `to_external_format()` - Convert internal data to external API format
- `from_external_format()` - Convert external API data to internal format
- `to_external_query_format()` - Convert query parameters for external API
- `*_via_provider()` - Static methods called by Provider Rotation System

### AbstractExternalAPIClient

Provides database-like interface for external APIs. Acts as the "DB" property equivalent for external models.

**Key Methods:**
- `create()` - Create entity via external API
- `get()` - Get entity by ID via external API
- `list()` - List entities with filtering/pagination
- `update()` - Update entity via external API
- `delete()` - Delete entity via external API
- `exists()` - Check if entity exists

### AbstractExternalManager

Manager class for external models. **Inherits from AbstractBLLManager** to leverage all existing functionality (hooks, validation, search transformers, etc.) while overriding database operations to work with external APIs.

**Key Properties:**
- `Model` - The external model class
- `DB` - Returns the external API client (overrides the database property)
- All AbstractBLLManager functionality (hooks, validation, etc.)

## Implementation Example: Stripe Product

### 1. Define the External Model

```python
from serverframework.extensions.AbstractExternalModel import AbstractExternalModel
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from decimal import Decimal

class StripeProductModel(AbstractExternalModel):
    """External model for Stripe Product API resource."""
    
    # Stripe API resource identifier
    external_resource = "products"
    
    # Field mappings between internal and Stripe API
    field_mappings = {
        "display_name": "name",
        "description": "description", 
        "is_active": "active",
        "external_id": "id"
    }
    
    # Model fields (internal format)
    external_id: str = Field(..., description="Stripe product ID")
    display_name: str = Field(..., description="Product name")
    description: Optional[str] = None
    is_active: bool = Field(True, description="Whether product is active")
    created_at: Optional[int] = None  # Stripe timestamp
    
    class Create(BaseModel):
        display_name: str
        description: Optional[str] = None
        is_active: bool = True
    
    class Update(BaseModel):
        display_name: Optional[str] = None
        description: Optional[str] = None
        is_active: Optional[bool] = None
    
    class Search(BaseModel):
        display_name: Optional[str] = None
        is_active: Optional[bool] = None
    
    @classmethod
    def to_external_format(cls, internal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert internal format to Stripe API format."""
        external_data = {}
        
        for internal_field, value in internal_data.items():
            # Map field names
            external_field = cls.field_mappings.get(internal_field, internal_field)
            external_data[external_field] = value
            
        return external_data
    
    @classmethod
    def from_external_format(cls, external_data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Stripe API format to internal format."""
        internal_data = {}
        
        # Reverse field mapping
        reverse_mappings = {v: k for k, v in cls.field_mappings.items()}
        
        for external_field, value in external_data.items():
            internal_field = reverse_mappings.get(external_field, external_field)
            internal_data[internal_field] = value
            
        return internal_data
    
    @classmethod
    def to_external_query_format(
        cls, 
        query_params: Dict[str, Any],
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[List] = None
    ) -> Dict[str, Any]:
        """Convert query parameters to Stripe API format."""
        external_params = {}
        
        # Map query fields
        for internal_field, value in query_params.items():
            external_field = cls.field_mappings.get(internal_field, internal_field)
            external_params[external_field] = value
        
        # Stripe pagination
        if limit:
            external_params["limit"] = limit
        if offset:
            external_params["starting_after"] = offset  # Stripe uses cursor pagination
            
        return external_params
    
    @staticmethod
    def create_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """Create product via Stripe provider."""
        try:
            # Get Stripe client from provider
            stripe = provider_instance.get_stripe_client()
            
            # Create product
            product = stripe.Product.create(**kwargs)
            
            return {
                "success": True,
                "data": dict(product)
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    @staticmethod
    def get_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Get product via Stripe provider."""
        try:
            stripe = provider_instance.get_stripe_client()
            product = stripe.Product.retrieve(external_id)
            
            return {
                "success": True,
                "data": dict(product)
            }
            
        except stripe.error.InvalidRequestError as e:
            if "No such product" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def list_via_provider(provider_instance, **kwargs) -> Dict[str, Any]:
        """List products via Stripe provider."""
        try:
            stripe = provider_instance.get_stripe_client()
            products = stripe.Product.list(**kwargs)
            
            return {
                "success": True,
                "data": [dict(product) for product in products.data]
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def update_via_provider(provider_instance, external_id: str, **kwargs) -> Dict[str, Any]:
        """Update product via Stripe provider."""
        try:
            stripe = provider_instance.get_stripe_client()
            product = stripe.Product.modify(external_id, **kwargs)
            
            return {
                "success": True,
                "data": dict(product)
            }
            
        except stripe.error.InvalidRequestError as e:
            if "No such product" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def delete_via_provider(provider_instance, external_id: str) -> Dict[str, Any]:
        """Delete product via Stripe provider."""
        try:
            stripe = provider_instance.get_stripe_client()
            stripe.Product.delete(external_id)
            
            return {"success": True}
            
        except stripe.error.InvalidRequestError as e:
            if "No such product" in str(e):
                return {"success": False, "error": "Not found"}
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
```

### 2. Create External Manager

```python
from serverframework.extensions.AbstractExternalModel import AbstractExternalManager
from serverframework.extensions.payment.models.StripeProductModel import StripeProductModel

class StripeProductManager(AbstractExternalManager):
    """Manager for Stripe Product external API."""
    
    Model = StripeProductModel
    
    def __init__(self, requester_id: str, **kwargs):
        # Get rotation manager for payment extension
        from serverframework.extensions.payment.EXT_Payment import EXT_Payment
        
        rotation_manager = EXT_Payment.root
        if not rotation_manager:
            raise RuntimeError("Payment extension rotation not available")
            
        super().__init__(
            requester_id=requester_id,
            rotation_manager=rotation_manager,
            **kwargs
        )
    
    # All AbstractBLLManager methods work automatically!
    # Hooks, validation, search transformers, etc. all work seamlessly
    
    # Optional: Override specific validation if needed
    def create_validation(self, entity):
        """Custom validation for Stripe products."""
        super().create_validation(entity)
        # Add Stripe-specific validation here
        
    def _register_search_transformers(self):
        """Register custom search transformers for Stripe products."""
        super()._register_search_transformers()
        # Add Stripe-specific search transformers
        self.register_search_transformer('price_range', self._transform_price_range)
```

### 3. Integration in BLL

```python
from serverframework.extensions.payment.managers.StripeProductManager import StripeProductManager

class BLL_Payment(AbstractBLLManager):
    """Payment extension business logic."""
    
    @classmethod
    def get_stripe_product_manager(cls, requester_id: str, **kwargs) -> StripeProductManager:
        """Get Stripe product manager instance."""
        return StripeProductManager(requester_id=requester_id, **kwargs, db_manager=self.db_manager)
    
    @classmethod
    def create_stripe_product(cls, requester_id: str, **product_data):
        """Create a product in Stripe."""
        manager = cls.get_stripe_product_manager(requester_id)
        return manager.create(**product_data)
    
    @classmethod
    def list_stripe_products(cls, requester_id: str, **filters):
        """List products from Stripe."""
        manager = cls.get_stripe_product_manager(requester_id)
        return manager.list(**filters)
```

## Usage Patterns

### Basic CRUD Operations

```python
# Create product manager
product_manager = BLL_Payment.get_stripe_product_manager(requester_id="user_123")

# Create - works exactly like internal models
product = product_manager.create(
    display_name="Premium Plan",
    description="Premium subscription plan",
    is_active=True
)

# Get - same interface as internal models
product = product_manager.get(id="prod_stripe_id_123")

# List with filtering - same search capabilities
products = product_manager.list(is_active=True, limit=10)

# Search with complex filters - same as internal models
products = product_manager.search(
    display_name={"inc": "Premium"},
    is_active=True,
    limit=20
)

# Update - identical to internal models
updated_product = product_manager.update(
    id="prod_stripe_id_123",
    display_name="Premium Plan v2"
)

# Batch operations - work automatically!
updated_products = product_manager.batch_update([
    {"id": "prod_1", "data": {"display_name": "New Name 1"}},
    {"id": "prod_2", "data": {"display_name": "New Name 2"}}
])

# Delete - same as internal models
product_manager.delete(id="prod_stripe_id_123")

# Hooks work automatically - no changes needed!
@hook_bll(StripeProductManager.create, timing="after")
def log_product_creation(context):
    logger.info(f"Stripe product created: {context.result.display_name}")
```

### Endpoint Integration

External models can be used with existing endpoint patterns:

```python
from serverframework.endpoints.AbstractEPRouter import AbstractEPRouter

class PaymentEPRouter(AbstractEPRouter):
    """Payment endpoints with external model support."""
    
    @app.get("/stripe/products/{product_id}")
    async def get_stripe_product(product_id: str, requester_id: str = Depends(get_requester)):
        manager = BLL_Payment.get_stripe_product_manager(requester_id)
        return manager.get(id=product_id)
    
    @app.get("/stripe/products")
    async def list_stripe_products(
        requester_id: str = Depends(get_requester),
        limit: Optional[int] = 20,
        is_active: Optional[bool] = None
    ):
        manager = BLL_Payment.get_stripe_product_manager(requester_id)
        return manager.list(limit=limit, is_active=is_active)
```

## Provider Integration

### Extending Existing Providers

Add external model support to existing providers:

```python
class StripeProvider(AbstractPaymentProvider):
    """Stripe provider with external model support."""
    
    def get_stripe_client(self):
        """Get configured Stripe client."""
        import stripe
        stripe.api_key = self.get_api_key()
        return stripe
    
    # External model methods are called via rotation system
    # Implementation is in the model's *_via_provider methods
```

## Navigation Properties

The AbstractExternalModel system supports navigation properties that work identically to internal database models. This allows seamless integration with GraphQL generation and existing endpoint patterns.

### Creating External Reference Models

Use the `create_external_reference_model` factory function to create reference models that follow the standard pattern:

```python
from serverframework.extensions.AbstractExternalModel import AbstractExternalModel, create_external_reference_model

# 1. Define your external model
class Stripe_CustomerModel(AbstractExternalModel):
    """External model for Stripe Customer."""
    
    external_resource = "customers"
    
    # Fields
    id: str = Field(..., description="Stripe customer ID")
    email: str = Field(..., description="Customer email")
    name: Optional[str] = Field(None, description="Customer name")
    
    # ... implement required abstract methods ...

# 2. Create the reference model using the factory
Stripe_CustomerReferenceModel = create_external_reference_model(
    external_model_class=Stripe_CustomerModel,
    reference_field_name="external_payment_id",  # The field name containing the foreign key
    local_field_name="external_payment_id",     # The local field to resolve from
)
```

### Using External Reference Models

Once created, external reference models work exactly like internal ones:

```python
# In your BLL_Payment.py
from serverframework.lib.Pydantic2SQLAlchemy import extension_model
from serverframework.logic.BLL_Auth import UserModel

@extension_model(UserModel)
class Payment_UserModel(Stripe_CustomerReferenceModel.Optional):
    """
    Payment extension for User model with Stripe customer navigation.
    
    This automatically adds:
    - external_payment_id: Optional[str] field
    - stripe_customer: Optional[Stripe_CustomerModel] navigation property
    """
    pass
```

### Automatic Navigation

The navigation property resolves automatically:

```python
# Get a user with payment extension
user_manager = BLL_Payment.get_user_manager(requester_id="user_123")
user = user_manager.get(id="user_456")

# Access the Stripe customer directly via navigation property
if user.external_payment_id:
    customer = user.stripe_customer  # Automatically resolves from Stripe API
    logger.debug(f"Customer email: {customer.email}")
    logger.debug(f"Customer name: {customer.name}")
```

### GraphQL Integration

Navigation properties work seamlessly with GraphQL generation - no changes required:

```graphql
query GetUserWithCustomer {
  user(id: "user_456") {
    id
    email
    external_payment_id
    stripe_customer {
      id
      email  
      name
    }
  }
}
```

### Multiple Navigation Properties

You can define multiple external navigation properties:

```python
# Define multiple external models
Stripe_SubscriptionModel = AbstractExternalModel(...)  
Stripe_PaymentMethodModel = AbstractExternalModel(...)

# Create reference models
Stripe_CustomerReferenceModel = create_external_reference_model(
    Stripe_CustomerModel, "external_payment_id", "external_payment_id"
)

Stripe_SubscriptionReferenceModel = create_external_reference_model(
    Stripe_SubscriptionModel, "external_subscription_id", "external_subscription_id"  
)

# Use multiple references
@extension_model(UserModel)
class Payment_UserModel(
    Stripe_CustomerReferenceModel.Optional,
    Stripe_SubscriptionReferenceModel.Optional
):
    """User with multiple Stripe navigation properties."""
    pass
```

### Complex Navigation Examples

```python
# User with customer and subscriptions
user = user_manager.get(id="user_123")

# Direct customer access
customer = user.stripe_customer

# Get customer's subscriptions via another navigation
if customer:
    # This would require additional setup, but demonstrates the pattern
    subscriptions = customer.subscriptions  # List navigation property
    
    for subscription in subscriptions:
        logger.debug(f"Subscription: {subscription.id} - Status: {subscription.status}")
```

### Best Practices for Navigation Properties

1. **Lazy Loading** - Navigation properties are resolved on first access and cached
2. **Error Handling** - Failed navigation returns None (single) or empty list (collection)
3. **Performance** - Consider the impact of external API calls in navigation chains
4. **Caching** - Results are cached per instance to avoid duplicate API calls
5. **Context** - Navigation properties use system context by default (SYSTEM_ID)

## Benefits

1. **Full AbstractBLLManager Integration** - Inherits ALL functionality including hooks, validation, search transformers, batch operations, etc.
2. **Unified Interface** - External APIs use identical patterns to internal models
3. **Provider Rotation** - Automatic failover and load balancing for external APIs
4. **Endpoint Compatibility** - External models work seamlessly with existing endpoint abstractions
5. **GraphQL Support** - External models can be exposed via GraphQL without changes
6. **Hook System** - Full hook support for before/after operations, auditing, etc.
7. **Consistent Error Handling** - Same error patterns as internal operations
8. **Type Safety** - Full Pydantic validation and type hints
9. **Field Mapping** - Automatic conversion between internal and external formats
10. **Search Transformers** - Custom search logic works the same way
11. **Validation Framework** - Same validation patterns for create/update operations

## Best Practices

1. **Field Mappings** — declare them as a typed `field_mappings: List[FieldMapping]` rather than a `Dict[str, str]` rename map. Built-in transformers: `Rename`, `Compose`, `Decompose`, `DotPath`, `UnitConvert` (with helpers like `CentsToDecimal`), `EnumRemap`, `TimestampConvert`, plus a `Custom(fn_to, fn_from)` escape hatch. The framework derives `to_external_format` / `from_external_format` mechanically; the mapping list must be reversible so round-trip integrity is preserved, and round-trip tests run automatically as part of the provider's class-level test suite.
2. **Error Handling** — raise typed `BaseExternalError` subclasses; never return success/error dicts from `*_via_provider`. The rotation policy interprets typed errors per failure class.
3. **Pagination** — declare `paginator: ClassVar[Type[AbstractPaginator]]` — `OffsetPaginator`, `CursorPaginator`, `PageTokenPaginator`, `LinkHeaderPaginator`. Internal endpoints continue to expose offset/limit; the paginator round-trips opaque cursor state in `next_token` (a base64-encoded JSON envelope `{provider_cursor, page_size, query_hash}`). A `query_hash` mismatch is `400 invalid_pagination`. The typed `Pagination` model exposes `supports_random_access: bool`; cursor-only providers cannot jump arbitrarily to offset.
4. **Search** — declare `query_translator: ClassVar[Type[AbstractQueryDSLTranslator]]`. Built-ins: `StripeSearchTranslator`, `SOQLTranslator`, `GraphQLFilterTranslator`, `MongoStyleTranslator`, `KeyValueTranslator`. The translator consumes the typed search models (`StringSearchModel`, `NumericalSearchModel`, `DateSearchModel`, `BooleanSearchModel`) and emits the upstream's filter format. A translator may declare `supported_operators` as a class set; unsupported operators surface as a typed error naming the operator and the provider.
5. **Rate Limiting** — declare `rate_limit: ClassVar[Optional[RateLimit]]`. The framework's per-provider token bucket (backed by `DistributedCounter`) acquires before the call; 429 signals pause that provider rather than rotating.
6. **Bulk endpoints** — implement optional `batch_create_via_provider` / `batch_update_via_provider` / `batch_delete_via_provider` when the upstream supports them (Stripe batch API, SendGrid bulk send, Salesforce Composite). When present, `AbstractExternalManager.batch_*` delegates; when absent, it falls back to a loop over single-resource calls. Per-item rejections surface as individual typed errors; batch-level idempotency keys derive from per-item keys.
7. **N+1 prevention** — external navigation honors the `include` query parameter. With `include=stripe_customer`, the framework collects external IDs across the result set and issues one batched upstream call (`list_via_provider(ids=[...])`) instead of N individual calls; without `include`, navigation returns `None` (lenient mode, recommended for production) or raises `NavigationNotIncludedError` (strict mode, recommended for development). For providers whose upstream does not support list-by-id, the resolver falls back to bounded-concurrency individual calls subject to the provider's rate limit.
8. **Caching** — per-request cache keyed by `(external_model, set_of_ids)` for batched resolution; persistent caching is opt-in per type via the `@cache(ttl=...)` directive on the merged GraphQL type.
9. **Validation** — validate at the typed external-DTO boundary before the call, and again on response.
10. **Idempotency** — decorate mutating `*_via_provider` methods with `@idempotent`. The framework guarantees the rotation system's retries carry the same key as the original attempt; the canonical store is the outbox row when the operation enrolls.
11. **Mirror-on-create** — use the `@mirror_on_create(local=UserModel, external=Stripe_CustomerModel, link_field="external_payment_id")` decorator for the local-create + upstream-create + ID-write-back lifecycle. The outbox pattern is the default; the roll-forward saga is retained only for the narrow case where the upstream cannot survive at-least-once delivery and the local rollback is cheaper than reconciliation. Symmetric `@mirror_on_update` and `@mirror_on_delete` cover the corresponding events. The link-field write-back uses `AdvisoryLock` for serialization, not an ad-hoc row-level lock.

## Testing

External models are tested against real sandbox/test-mode credentials, not mocks. This is the no-mock pillar from `AGENTS.md` applied to external APIs.

```python
@pytest.mark.external_api(provider="stripe")
def test_stripe_product_creation(model_registry):
    """Test Stripe product creation against real sandbox credentials.

    Automatically xfailed when STRIPE_API_KEY_TEST is absent, with the
    reason naming the exact env var that would unblock the test.
    """
    manager = StripeProductManager(model_registry, requester_id="test_user")
    product = manager.create(display_name="Test Product")
    assert product.display_name == "Test Product"
    assert product.external_id.startswith("prod_")
```

Two markers govern external-API tests:

- `@pytest.mark.external_api(provider="...")` — requires real (sandbox) credentials; auto-xfailed when missing with a clear skip reason naming the missing env vars; runs end-to-end against the sandbox when present.
- `@pytest.mark.external_smoke` — deliberately runs without credentials and asserts that the framework's configuration-failure paths surface correctly.

`PRV_Fake_*` providers are an opt-in for offline CI but the recommended path is sandbox credentials. No mocks of `RotationManager`, `*_via_provider` methods, or extension functionality.

## Inbound Eventing

Federations are bidirectional. External upstreams push events back to us; the framework provides typed primitives for both shapes.

### Webhook handlers

A canonical mount at `/webhook/{extension}/{provider}` and `/webhook/{extension}/{provider}/{event}` is registered automatically when an extension or provider declares an inbound handler. The decorator `@webhook_handler(EXT_Payment, provider="stripe", event="customer.updated")` registers static methods into a typed registry. Providers declaring webhook handlers must implement `verify_signature(headers, body) -> bool` on `AbstractStaticProvider`; signature verification is mandatory and the request is rejected before dispatch on failure. Inbound events fan into the same hook bus that internal mutations fire — an external `customer.updated` triggers the AFTER-update hook chain on `Stripe_CustomerManager` exactly as if the change had originated locally. Replay protection (timestamp window, nonce cache) is per-provider in `verify_signature`. Handlers receive a `WebhookContext` with the parsed payload, originating provider instance, and requester resolution chain. Unrecognized events log a warning and return 200 (rejecting with non-2xx tells the upstream to retry, which is unwanted for events we deliberately ignore).

### Streaming services

Real-time integrations (Stripe events firehose, Slack RTM, Kafka consumers, websocket-driven chat) do not fit request/response. They are long-lived, asynchronous, stateful. `StreamingService` is one of the four service flavors and has two sub-flavors: `ConsumerService` covers long-lived inbound connections (websocket subscribers, SSE listeners, Kafka consumers); its lifecycle is `connect → on_message(event) → disconnect` with automatic exponential-backoff reconnection capped at a configurable maximum. `ProducerService` covers long-lived outbound streams that we write to. Both fan into the same hook bus that internal mutations and webhooks use; per-service state (last-seen-event cursors, subscription tokens) lives in a small per-service state table or in the provider's external state store, not in process memory. Long-lived services participate in graceful shutdown: the service receives a stop signal, drains in-flight events with a deadline, and disconnects cleanly.

## Schema drift detection

External APIs change beneath us. Each provider targeting an upstream with a published OpenAPI spec declares `openapi_url: ClassVar[Optional[str]]`. A canonical snapshot lives at `src/extensions/{name}/contracts/{provider}.openapi.json`. CI fetches the live spec on a per-provider cadence (high-velocity upstreams daily, slow-moving ones weekly), runs a structural diff (`oasdiff`) against the snapshot, and fails on breaking changes (removed fields, narrowed types, removed enum values). Non-breaking diffs produce a warning and a PR that updates the snapshot. For upstreams without machine-readable specs, the snapshot is generated from real recorded responses normalized to remove instance-specific identifiers. The CI failure includes a clear diff summary; the snapshot files are reviewable artifacts in pull requests.

## GraphQL upstreams

When an upstream API is itself GraphQL, providers federate the schema rather than wrapping it as RPC. `AbstractGraphQLProvider(AbstractStaticProvider)` declares `upstream_url`, `upstream_auth_strategy`, `federation_style: Literal["apollo_v2", "stitching", "namespaced"]`, and `type_namespace: Optional[str]`. A startup pipeline introspects the upstream, runs a `SchemaTransformer` pipeline (rename, prefix, hide-fields, mask-arguments, override-resolvers), registers the transformed types into the local Strawberry schema via `MergedSchemaRegistry`, and generates resolvers that reconstruct the upstream selection set from `info.selected_fields`, build a real GraphQL document with the original variables, forward to the upstream, and return the parsed result. Selection-set push-down is the entire point — without it the framework has rebuilt RPC inside a GraphQL costume. Cross-subgraph joins use a `BatchedFieldResolver` that respects the `include` mechanism. Apollo Federation v2 honors `@key`, `@external`, `@requires`, `@provides`. Errors and partial data follow real GraphQL semantics: upstream `errors` arrays propagate through, attached to the affected fields.
