# Dependency Management

## Overview
Comprehensive dependency management system supporting system packages, Python packages, and extension dependencies with automatic resolution, installation, and validation across multiple operating systems and package managers.

## Core Components

### Dependency Types

#### Base Dependency (`Dependency`)
Foundation class for all dependency types with common fields for name, friendly name, optional status, reason, and semantic versioning requirements.

#### System Dependencies (`SYS_Dependency`)
Cross-platform system package dependencies with package manager mappings.

**Supported Package Managers:**
- **APT**: Debian/Ubuntu package management
- **Homebrew**: macOS and Linux package management  
- **WinGet**: Windows package management
- **Chocolatey**: Windows alternative package management
- **Snap**: Universal Linux package management

**Features:**
- Multiple package manager mappings per dependency
- Automatic OS detection and package manager availability
- Batch installation support where available
- Version constraint validation

#### Python Dependencies (`PIP_Dependency`)
Python package dependencies with semantic version support.

**Features:**
- Package installation via pip or uv (if available)
- Semantic version constraint validation
- Batch installation optimization
- Integration with requirements.txt validation

#### Extension Dependencies (`EXT_Dependency`)
Framework extension dependencies with loading order resolution.

**Features:**
- Extension loading order calculation
- Optional dependency support
- Version compatibility checking
- Integration with extension registry

### Package Manager Abstraction

#### AbstractPackageManager
Unified interface for all package managers with command templates and OS support detection.

**Command Types:**
- Package availability checking
- Installation (single and batch)
- Version verification
- System update operations

#### Automatic Package Manager Detection
Dynamic detection of available package managers based on OS type and command availability with fallback support for multiple managers per platform.

### Dependency Resolution

#### Resolution Engine
Topological sorting for dependency order calculation with circular dependency detection and automatic conflict resolution.

**Features:**
- Kahn's algorithm implementation
- Circular dependency breaking
- Priority-based installation ordering
- Extension dependency validation

#### Requirements.txt Integration
Automatic validation against existing requirements.txt with conflict detection and version compatibility checking.

**Validation Types:**
- Duplicate dependency warnings
- Version constraint conflicts
- Missing version specifications
- Compatibility verification

### Installation Management

#### Unified Dependencies Class
Container class providing type-specific access and batch operations.

**Properties:**
- `.sys`: System dependencies with installation methods
- `.pip`: Python dependencies with installation methods
- `.ext`: Extension dependencies with validation methods

**Installation Flow:**
1. System dependencies (may be required by Python packages)
2. Python dependencies (with dependency resolution)
3. Extension dependency validation

#### Batch Operations
Optimized installation strategies with fallback to individual installation on batch failure.

**Features:**
- Package manager batch support detection
- Parallel installation where possible
- Individual fallback on batch failure
- Comprehensive error reporting

### Dependency Factory

#### Convenience Methods
Factory methods for creating dependencies with proper package manager mappings.

**Static Methods:**
- `for_apt()`: APT-specific dependencies
- `for_brew()`: Homebrew-specific dependencies
- `for_winget()`: WinGet-specific dependencies
- `for_all_platforms()`: Cross-platform mapping

## OS Detection & Support

### Operating System Types
Robust OS detection supporting major platforms with distribution-specific handling.

**Supported Systems:**
- **Linux**: Debian, Ubuntu, Fedora, RedHat with distro-specific detection
- **macOS**: Darwin system detection
- **Windows**: Windows system detection
- **Unknown**: Graceful fallback handling

### Cross-Platform Compatibility
Automatic package manager selection based on OS capabilities with intelligent fallback strategies.

## Advanced Features

### Semantic Versioning
Full semver support with constraint validation and compatibility checking.

**Constraint Types:**
- Exact versions (==1.0.0)
- Minimum versions (>=1.0.0)
- Range specifications (>=1.0.0,<2.0.0)
- Compatible releases (~=1.0.0)

### Extension Server Testing
Isolated test server creation with specific extension configurations.

**Features:**
- Extension-specific test environments
- FastAPI TestClient integration
- Environment isolation
- Automatic cleanup

### Conflict Resolution
Automated resolution of dependency conflicts with intelligent priority assignment.

**Strategies:**
- Version compatibility analysis
- Optional dependency handling
- Graceful degradation
- Error reporting and recovery

## Integration Patterns

### Extension Integration
Seamless integration with the extension system for dependency discovery and resolution.

### Testing Integration
Built-in support for test environments with dependency isolation and validation.

### Environment Integration
Integration with the environment management system for configuration and validation.

## Best Practices

1. **Comprehensive Dependency Declaration**: Include all required dependencies with appropriate version constraints
2. **Cross-Platform Support**: Use factory methods for multi-platform dependencies
3. **Optional Dependencies**: Mark non-critical dependencies as optional
4. **Version Constraints**: Specify semantic version requirements for stability
5. **Testing Integration**: Use dependency-specific test environments
6. **Conflict Resolution**: Monitor and resolve version conflicts proactively
7. **Installation Order**: Respect dependency resolution order for reliable installation