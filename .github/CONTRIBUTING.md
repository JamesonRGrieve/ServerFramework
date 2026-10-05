# How to Enhance the GitHub Templates Repository
Your willingness to help improve our project is invaluable. Whether you're spotting a bug, suggesting a new feature, or submitting code changes, your contributions play a crucial role in our community’s success. This guide explains the recommended processes and best practices for getting involved. While these suggestions are flexible, following them can lead to smoother collaboration and higher quality work. If you feel any part of these guidelines could be refined, we encourage you to propose modifications through a pull request.
---
## Ways to Participate
### Identifying and Reporting Issues
If you encounter an error or unexpected behavior, please share your findings with us. A detailed issue report makes it easier for the maintainers and other contributors to understand and replicate the problem, thereby expediting the resolution process.
- Begin by using our [Bug Report Form](./ISSUE_TEMPLATE/bug-report.yml) to open a new issue.
### Recommending Enhancements
We are always interested in hearing your thoughts on how to improve the repository—be it innovative features or subtle tweaks to existing functionalities. Your suggestions help us evolve the project and better serve the community.
- Submit your ideas through our [Feature Proposal Form](./ISSUE_TEMPLATE/feature-request.yml).
### Contributing Code Through Pull Requests
When you’ve made changes that you believe will benefit the project, we invite you to share your code modifications. Follow these steps to ensure your contribution is reviewed and integrated efficiently:
1. **Fork and Branch:** Create a personal fork of the repository and branch off from the `main` branch.
2. **Testing:** If your contribution includes new code, accompany it with tests that verify its functionality.
3. **Documentation Updates:** Update relevant documentation if your changes modify any public interfaces or usage instructions.
4. **Test Validation:** Confirm that all existing tests pass on your branch.
5. **Adhere to Style Guidelines:** Ensure your code is formatted according to the repository’s standards (for example, using Black for Python).
6. **Submit a Pull Request:** Use our [Pull Request Guide](./PULL_REQUEST_TEMPLATE/pull_request_template.yml) when opening your pull request.
---
## Best Practices for Commit Messages and Code Style
### Crafting Clear Commit Messages
Well-structured commit messages are essential for maintaining a clear project history. Please follow these practices:
- **Present Tense:** Write commit messages in the present tense (e.g., "Add support for X" instead of "Added support for X").
- **Imperative Mood:** Use command-style language (e.g., "Fix bug in Y" instead of "Fixes bug in Y").
- **Conciseness:** Keep the first line of your message brief—ideally under 72 characters.
- **Contextual Details:** After the initial line, include any necessary details, and reference related issues or pull requests as needed.
- **Documentation-Only Changes:** If your commit only modifies documentation, include `[ci skip]` in the commit title to indicate that no code tests are needed.
### Maintaining Consistent Code Formatting
Uniform code style is key to a maintainable codebase. Please observe the following:
- **Follow Established Guidelines:** Stick to the code formatting standards set for the project (e.g., Black for Python).
- **Language-Specific Conventions:** Adhere to any additional style guides relevant to the programming language or framework you are working in.
- **Review Before Submission:** Double-check your code against the repository’s style expectations to ensure consistency and readability.
---
## Releasing
Releases go to PyPI as the single `zephyrex` distribution, with an extra per bundled extension. `.github/workflows/release.yml` runs on a `v*` tag, which must equal the `version` in `pyproject.toml` (e.g. `v0.0.1a1`):

1. Build the wheel and sdist from the checkout; check the extras match the extensions' declarations, that the wheel ships no tests, and that its provenance manifest names the tagged commit built from a clean tree (`scripts/check_wheel.py`); write the SBOM, run the dependency audit (any known vulnerability in `requirements.lock` fails the release), and sign with sigstore. Tag only a clean, committed tree.
2. Publish to TestPyPI (`testpypi` environment).
3. Install `zephyrex[all]` from TestPyPI into a clean environment, resolving its dependencies from PyPI only, then run `zephyrex version` and `scripts/release_smoke.py`.
4. Publish to PyPI (`pypi` environment). The run waits here for a required reviewer to approve.
5. Attach the packages, signatures and SBOM to the GitHub release.

Publishing uses PyPI Trusted Publishing, so no token is stored. Each index needs a trusted publisher for this repository, workflow `release.yml` and environment `testpypi` or `pypi`, and the `pypi` environment needs its required reviewers set. After changing an extension's pip dependencies, run `python -m zephyrex.extensions.sync_dependencies` to update its manifest and extra; the test suite fails until you do.
---
## In Summary
Every contribution, from minor corrections to major feature additions, is appreciated. Clear communication and adherence to these guidelines help us create a more robust and user-friendly repository. Thank you for investing your time and expertise in advancing this project. We eagerly await your ideas and improvements!
Happy contributing!