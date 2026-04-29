# SDK Publishing Guide

This document outlines the process for building, testing, and publishing the SDK package to PyPI.

## Prerequisites

Before publishing, ensure you have the following:

- A PyPI account (or access to your organization's account)
- The necessary permissions to publish the package
- Python 3.8 or later installed
- The following tools installed:
  - `build`: `python -m pip install build`
  - `twine`: `python -m pip install twine`

## Version Management

Version numbers follow [Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH):

1. MAJOR version for incompatible API changes
2. MINOR version for new functionality in a backward compatible manner
3. PATCH version for backward compatible bug fixes

Update the version in the following files:
- `sdk/__init__.py`: Update `__version__`
- `pyproject.toml`: Update the `version` field under `[project]`

## Building the Package

Use the provided build script to generate the distribution packages:

```bash
# Make the script executable if needed
chmod +x scripts/build.sh

# Run the build script
./scripts/build.sh
```

This script:
1. Cleans up previous build artifacts
2. Upgrades pip and build tools
3. Builds source and wheel distributions
4. Lists the created distribution files

## Pre-publishing Checklist

Before publishing, verify:

- [ ] All tests pass: `./scripts/test.sh`
- [ ] Documentation is up to date
- [ ] Version number has been updated
- [ ] CHANGELOG.md has been updated (if applicable)
- [ ] You're publishing from the correct branch (usually main/master)

## Publishing to PyPI

To publish the package to PyPI:

```bash
# For the official PyPI
python -m twine upload dist/*

# For the test PyPI (recommended for testing before official release)
python -m twine upload --repository-url https://test.pypi.org/legacy/ dist/*
```

You'll be prompted for your PyPI username and password.

## Publishing via GitHub Actions (Recommended)

For automated publishing, set up a GitHub Actions workflow:

1. Create a `.github/workflows/publish.yml` file:

```yaml
name: Publish to PyPI

on:
  release:
    types: [published]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install build twine
    - name: Build and publish
      env:
        TWINE_USERNAME: ${{ secrets.PYPI_USERNAME }}
        TWINE_PASSWORD: ${{ secrets.PYPI_PASSWORD }}
      run: |
        python -m build
        twine upload dist/*
```

2. Add PyPI credentials as GitHub repository secrets:
   - PYPI_USERNAME: Your PyPI username
   - PYPI_PASSWORD: Your PyPI password or token

3. Publishing happens automatically when you create a new release in GitHub.

## Testing the Published Package

After publishing, verify the package works correctly:

```bash
# Create a new virtual environment
python -m venv test_env
source test_env/bin/activate  # On Windows: test_env\Scripts\activate

# Install the package from PyPI
pip install api-sdk

# Run a basic test
python -c "from api_sdk import SDK; logger.debug(SDK)"
```

## Troubleshooting

If you encounter issues:

- **Invalid credentials**: Double-check your PyPI username and password
- **Version conflict**: Ensure you're not trying to upload a version that already exists
- **Missing files**: Verify the build process completed successfully
- **Package not found after publishing**: PyPI might take a few minutes to index the new version

## Publishing Documentation

If you have separate documentation (e.g., Sphinx docs):

1. Build the docs:
   ```bash
   cd docs
   make html
   ```

2. Deploy to a documentation hosting service like ReadTheDocs or GitHub Pages.

## Release Announcement

After a successful release:

1. Announce in your project's communication channels
2. Update any relevant integration guides
3. Notify users of significant changes, especially breaking changes

## Separate Package: `serverframework-sdk`

The SDK ships as a separate pip package, decoupled from the server's release cadence. The repository carries two `pyproject.toml` files (server and SDK) with independent version numbers; the SDK source still lives in this monorepo for now.

The SDK package depends on the server's introspection-friendly subset (the `RouterMixin` registry, the Pydantic model export, the OpenAPI schema generation) but not on FastAPI's runtime or SQLAlchemy. A small shared-types module under `serverframework.types` re-exports the Pydantic models that consumers need to type-hint against (`UserModel.Create`, `SessionModel`, etc.) so the type-imports do not pull from internal modules. `pip install serverframework-sdk` succeeds without installing fastapi or sqlalchemy.

Extension discovery uses the same introspection generator that drives `SDK.Patterns.md`'s handler generation. At build time, the SDK package walks the consumer's installed-extension registry and emits per-resource handler files into the package — the same registry pass that drives OpenAPI and the REST router. Entry points are not used for discovery. The build step runs in the consumer's environment after `pip install serverframework[ext_payment]` so generated handlers reflect the actually-installed extensions.

The server's package metadata excludes `sdk*` from `[tool.setuptools.packages.find]`. SDK release cadence is decoupled from server release cadence. 