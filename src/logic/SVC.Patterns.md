# Service Layer Patterns

This document outlines service-specific patterns and conventions.

> **Common Patterns**: For CRUD model patterns, error handling, and configuration patterns shared across all layers, see [Framework.md](../Framework.md#common-patterns-across-layers).

## Core Service Architecture

### AbstractService Base Class

The AbstractService provides a standardized foundation for background services with lifecycle management, error handling, and resource management.

```python
class MyService(AbstractService):
    def __init__(
        self,
        requester_id: str,
        db: Optional[Session] = None,
        interval_seconds: int = 60,
        max_failures: int = 3,
        retry_delay_seconds: int = 5,
        service_id: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(
            requester_id=requester_id,
            db=db,
            interval_seconds=interval_seconds,
            max_failures=max_failures,
            retry_delay_seconds=retry_delay_seconds,
            service_id=service_id,
            **kwargs,
        )

    def _configure_service(self, **kwargs) -> None:
        """Override for service-specific initialization"""
        self.custom_setting = kwargs.get("custom_setting", "default")
        self.external_client = SomeExternalClient()
        # DatabaseManager is available as self.db_manager

    async def update(self) -> None:
        """Main service logic - runs periodically"""
        # Implement service-specific logic
        pass

    def cleanup(self) -> None:
        """Override for service-specific cleanup"""
        super().cleanup()
        if hasattr(self, 'external_client'):
            self.external_client.close()
```

## Service Lifecycle Patterns

### Initialization Pattern
```python
def _configure_service(self, **kwargs) -> None:
    """Configure service-specific settings during initialization"""
    # Parse service-specific parameters
    self.batch_size = kwargs.get("batch_size", 100)
    self.external_api_key = kwargs.get("api_key")
    
    # Initialize external clients
    if self.external_api_key:
        self.api_client = ExternalAPIClient(self.external_api_key)
    
    # Setup internal state
    self.last_processed_id = None
    self.metrics = {"processed": 0, "errors": 0}
    
    logger.debug(f"Configured {self.__class__.__name__} with batch_size={self.batch_size}")
```

### Main Logic Pattern
```python
async def update(self) -> None:
    """Implement the main service logic"""
    logger.debug(f"{self.__class__.__name__} executing update...")
    
    try:
        # 1. Get work items from database
        items = await self._get_pending_items()
        
        if not items:
            logger.debug("No pending items to process")
            return
        
        # 2. Process items in batches
        for batch in self._batch_items(items, self.batch_size):
            await self._process_batch(batch)
        
        # 3. Update metrics
        self.metrics["processed"] += len(items)
        
        logger.debug(f"Processed {len(items)} items successfully")
        
    except Exception as e:
        self.metrics["errors"] += 1
        logger.error(f"Error in {self.__class__.__name__}: {str(e)}")
        raise  # Re-raise to trigger failure handling
```

### Cleanup Pattern
```python
def cleanup(self) -> None:
    """Cleanup resources when service shuts down"""
    super().cleanup()  # Always call parent cleanup
    
    logger.debug(f"Cleaning up {self.__class__.__name__}")
    
    # Close external connections
    if hasattr(self, 'api_client'):
        self.api_client.close()
    
    # Save final state if needed
    if hasattr(self, 'metrics'):
        logger.debug(f"Final metrics: {self.metrics}")
    
    # Clear references
    self.api_client = None
    self.metrics = None
```

## Database Access Patterns

### Session Management
```python
async def update(self) -> None:
    """Use the db property for database access"""
    # The db property provides an active session via self.db_manager
    items = self.db.query(MyModel).filter(
        MyModel.status == "pending"
    ).all()
    
    for item in items:
        # Process item
        await self._process_item(item)
        
        # Update status in database
        item.status = "completed"
        item.updated_at = datetime.now(timezone.utc)
    
    # Commit changes
    self.db.commit()
```

### Error Handling with Database
```python
async def update(self) -> None:
    """Handle database errors gracefully"""
    try:
        # Start transaction
        items = self.db.query(MyModel).filter(
            MyModel.status == "pending"
        ).limit(self.batch_size).all()
        
        for item in items:
            try:
                await self._process_item(item)
                item.status = "completed"
            except Exception as e:
                # Mark individual item as failed
                item.status = "failed"
                item.error_message = str(e)
                logger.error(f"Failed to process item {item.id}: {e}")
        
        # Commit batch updates
        self.db.commit()
        
    except Exception as e:
        # Rollback on database errors
        self.db.rollback()
        raise
```

## Error Handling Patterns

### Graceful Error Recovery
```python
async def update(self) -> None:
    """Implement graceful error handling"""
    try:
        # Main processing logic
        await self._process_work()
        
    except ConnectionError as e:
        # Handle recoverable errors
        logger.warning(f"Connection error in {self.__class__.__name__}: {e}")
        # Let the framework handle retry
        raise
        
    except ValueError as e:
        # Handle configuration errors (non-recoverable)
        logger.error(f"Configuration error in {self.__class__.__name__}: {e}")
        self.stop()  # Stop service for manual intervention
        
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error in {self.__class__.__name__}: {e}")
        # Re-raise to trigger failure counting
        raise
```

### Custom Failure Handling
```python
def _handle_failure(self, error: Exception) -> bool:
    """Override for custom failure handling"""
    # Call parent failure handling first
    should_retry = super()._handle_failure(error)
    
    # Custom logic based on error type
    if isinstance(error, RateLimitError):
        logger.warning("Rate limit hit, increasing retry delay")
        self.retry_delay_seconds = min(self.retry_delay_seconds * 2, 300)
        return True
    
    if isinstance(error, AuthenticationError):
        logger.error("Authentication failed, stopping service")
        return False  # Don't retry authentication errors
    
    return should_retry
```

## Service Registration Patterns

### Single Service Registration
```python
# Create and register a service
service = MyService(
    requester_id=env("SYSTEM_ID"),
    interval_seconds=30,
    max_failures=5,
    custom_setting="value"
)

ServiceRegistry.register("my_service", service)
service.start()
```

### Bulk Service Management
```python
def initialize_services():
    """Initialize all application services"""
    services = [
        ("data_processor", DataProcessingService(
            requester_id=env("SYSTEM_ID"),
            interval_seconds=60
        )),
        ("email_sender", EmailService(
            requester_id=env("SYSTEM_ID"),
            interval_seconds=30
        )),
        ("cleanup_service", CleanupService(
            requester_id=env("SYSTEM_ID"),
            interval_seconds=3600  # Run hourly
        ))
    ]
    
    for service_id, service in services:
        ServiceRegistry.register(service_id, service)
    
    # Start all services
    ServiceRegistry.start_all()

def shutdown_services():
    """Gracefully shutdown all services"""
    ServiceRegistry.stop_all()
    ServiceRegistry.cleanup_all()
```

## Async Service Loop Patterns

### Manual Service Loop
```python
async def run_service_manually():
    """Run a service loop manually for testing"""
    service = MyService(requester_id=env("SYSTEM_ID"))
    service.start()
    
    try:
        await service.run_service_loop()
    except KeyboardInterrupt:
        logger.debug("Service interrupted by user")
    finally:
        service.cleanup()
```

### Background Task Integration
```python
import asyncio

async def start_background_services():
    """Start services as background tasks"""
    services = [
        MyService(requester_id=env("SYSTEM_ID"), service_id="service_1"),
        AnotherService(requester_id=env("SYSTEM_ID"), service_id="service_2")
    ]
    
    tasks = []
    for service in services:
        service.start()
        task = asyncio.create_task(service.run_service_loop())
        tasks.append(task)
    
    try:
        # Run all services concurrently
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.error(f"Service error: {e}")
    finally:
        # Cleanup all services
        for service in services:
            service.cleanup()
```

## Configuration Patterns

### Environment-Based Configuration
```python
def _configure_service(self, **kwargs) -> None:
    """Configure service from environment variables"""
    self.api_endpoint = env("EXTERNAL_API_ENDPOINT")
    self.api_key = env("EXTERNAL_API_KEY")
    self.batch_size = int(env("PROCESSING_BATCH_SIZE", "100"))
    self.enable_notifications = env("ENABLE_NOTIFICATIONS", "false").lower() == "true"
    
    if not self.api_endpoint or not self.api_key:
        raise ValueError("Missing required API configuration")
```

### Dynamic Configuration
```python
def _configure_service(self, **kwargs) -> None:
    """Configure service with dynamic settings"""
    # Get configuration from database
    config = self.db.query(ServiceConfig).filter(
        ServiceConfig.service_name == self.__class__.__name__
    ).first()
    
    if config:
        self.interval_seconds = config.interval_seconds
        self.batch_size = config.batch_size
        self.enabled_features = config.features.split(",")
    else:
        # Use defaults
        self.batch_size = 100
        self.enabled_features = []
```

## Monitoring and Metrics Patterns

### Basic Metrics Collection
```python
def _configure_service(self, **kwargs) -> None:
    """Initialize metrics tracking"""
    super()._configure_service(**kwargs)
    self.metrics = {
        "items_processed": 0,
        "errors_count": 0,
        "last_run_duration": 0,
        "last_run_time": None
    }

async def update(self) -> None:
    """Track execution metrics"""
    start_time = time.time()
    self.metrics["last_run_time"] = datetime.now(timezone.utc)
    
    try:
        # Main processing logic
        processed_count = await self._process_items()
        self.metrics["items_processed"] += processed_count
        
    except Exception as e:
        self.metrics["errors_count"] += 1
        raise
    finally:
        self.metrics["last_run_duration"] = time.time() - start_time
```

### Health Check Pattern
```python
def get_health_status(self) -> Dict[str, Any]:
    """Return service health information"""
    return {
        "service_id": self.service_id,
        "running": self.running,
        "paused": self.paused,
        "failures": self.failures,
        "max_failures": self.max_failures,
        "last_run": self.metrics.get("last_run_time"),
        "total_processed": self.metrics.get("items_processed", 0),
        "error_count": self.metrics.get("errors_count", 0)
    }

# Global health check for all services
def get_all_service_health():
    """Get health status for all registered services"""
    health_data = {}
    for service_id in ServiceRegistry.list():
        service = ServiceRegistry.get(service_id)
        if hasattr(service, 'get_health_status'):
            health_data[service_id] = service.get_health_status()
    return health_data
```

## Testing Patterns

### Mock Service for Testing
```python
class MockMyService(MyService):
    """Mock version for testing"""
    
    def _configure_service(self, **kwargs) -> None:
        """Override to avoid external dependencies"""
        self.batch_size = kwargs.get("batch_size", 10)
        self.api_client = Mock()  # Mock external client
        self.test_data = kwargs.get("test_data", [])
    
    async def _get_pending_items(self):
        """Return test data instead of database query"""
        return self.test_data
    
    async def _process_item(self, item):
        """Mock processing"""
        await asyncio.sleep(0.01)  # Simulate work
        return f"processed_{item}"

# Test usage
async def test_service():
    service = MockMyService(
        requester_id="test_user",
        interval_seconds=0.1,
        test_data=["item1", "item2", "item3"]
    )
    
    service.start()
    
    # Run one update cycle
    await service.update()
    
    assert service.metrics["items_processed"] == 3
    service.cleanup()
```

## Best Practices

### Service Design
1. **Single Responsibility** - Each service should have one clear purpose
2. **Idempotency** - Services should be safe to run multiple times
3. **Graceful Degradation** - Handle external service failures gracefully
4. **Resource Management** - Always clean up resources in cleanup()
5. **Configuration** - Make services configurable through _configure_service()

### Error Handling
1. **Specific Exceptions** - Catch specific exception types when possible
2. **Logging** - Log errors with appropriate detail levels
3. **Recovery** - Implement appropriate retry and recovery strategies
4. **Monitoring** - Track error rates and patterns

### Performance
1. **Batch Processing** - Process items in batches when possible
2. **Rate Limiting** - Respect external API rate limits
3. **Memory Management** - Avoid memory leaks in long-running services
4. **Database Efficiency** - Use efficient queries and proper indexing

### Monitoring
1. **Metrics** - Track key performance indicators
2. **Health Checks** - Implement service health endpoints
3. **Alerting** - Set up alerts for service failures
4. **Logging** - Use structured logging for better observability 