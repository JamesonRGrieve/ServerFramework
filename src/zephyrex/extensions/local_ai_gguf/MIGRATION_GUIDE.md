# Migration Guide: GGUFModelManager to New Model Management System

This guide explains how to migrate from the old `GGUFModelManager` to the new generic model management system.

## Overview

The new system introduces a more modular and extensible architecture with the following key components:

1. `Model` - Base interface for all model types
2. `GGUFModel` - GGUF-specific model implementation
3. `ModelManager` - Generic model manager that works with any `Model` implementation

## Key Changes

### 1. Model Management

**Old Way (GGUF-specific):**
```python
from local_ai_gguf.utils.model_manager import GGUFModelManager

# Get singleton instance
manager = GGUFModelManager()

# Mount a model
success, model_or_error = manager.mount_model(
    model_path="/path/to/model.gguf",
    model_name="my-model",
    config={
        "context_length": 2048,
        "batch_size": 512,
        "gpu_layers": 20
    }
)
```

**New Way (Generic):**
```python
from local_ai.utils.models import ModelManager
from local_ai_gguf.utils.models import GGUFModel

# Get singleton instance (now generic)
manager = ModelManager()

# Mount a GGUF model
success, model_or_error = manager.mount_model(
    model_id="my-model:model.gguf",
    model_path="/path/to/model.gguf",
    model_name="my-model",
    config={
        "context_length": 2048,
        "batch_size": 512,
        "gpu_layers": 20
    },
    model_class=GGUFModel  # Specify which model implementation to use
)
```

### 2. Getting a Model

**Old Way:**
```python
model, model_info = manager.get_model("my-model")
```

**New Way:**
```python
model = manager.get_model("my-model:model.gguf")
model_info = manager.get_model_info("my-model:model.gguf")
```

### 3. Unmounting a Model

**Old Way:**
```python
success, message = manager.unmount_model("my-model")
```

**New Way:**
```python
success, message = manager.unmount_model("my-model:model.gguf")
```

### 4. New Features

The new system includes several improvements:

1. **Generic Model Support**: Easily add support for new model types by implementing the `Model` interface.
2. **Better Resource Management**: Improved memory tracking and cleanup.
3. **Thread Safety**: Better handling of concurrent access to models.
4. **More Information**: Get detailed information about loaded models.

### 5. Utility Methods

```python
# List all loaded models
models = manager.list_models()

# Clean up unused models (older than 5 minutes by default)
results = manager.cleanup_unused_models(max_age_seconds=300)
```

## Migration Steps

1. Update imports to use the new module paths
2. Update model mounting to use the generic `ModelManager` with `GGUFModel` class
3. Update any code that interacts with the model manager to use the new API
4. Test thoroughly to ensure all functionality works as expected

## Adding New Model Types

To add support for a new model type (e.g., PyTorch):

1. Create a new class that implements the `Model` interface
2. Register it with the `ModelManager` by passing the class to `mount_model`

Example:
```python
class TorchModel(Model[torch.nn.Module]):
    # Implement Model interface
    ...

# Use the new model type
manager.mount_model(
    model_id="my-torch-model",
    model_path="/path/to/model.pt",
    model_name="my-torch-model",
    config={...},
    model_class=TorchModel
)
```
