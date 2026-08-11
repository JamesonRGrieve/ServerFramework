# Security Policy

This policy applies to Zephyrex Framework Server and the tooling shipped under the
`zephyrex` package on PyPI.

## Supported Versions

Security fixes ship to:

| Version Track | Status              | Notes                                       |
|---------------|---------------------|---------------------------------------------|
| Latest minor  | Fully supported     | All severities receive backports.           |
| Previous minor| Critical-only       | Only Critical/High severities are backported.|
| Older minors  | Unsupported         | No backports; upgrade to the latest minor. |

The version string is the one published on PyPI and is set via
`setuptools-scm` from git tags — there is no separate sibling `version`
file to drift out of sync.

## Reporting a Vulnerability

Email the details to **security@zephyrex.example** (the alias is
monitored by the maintainers and forwarded to a private security tracker).
Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce, ideally with a minimal proof-of-concept.
- The affected version (output of `pip show zephyrex`).
- Any suggested mitigations or patches.

If you are reporting a vulnerability in a third-party dependency, please
include the upstream advisory ID (CVE/GHSA) so we can correlate against
our `pip-audit` baseline.

## Response Time Commitment

We commit to the following timeline upon receipt of a report at the
disclosure address:

- **72 hours**: triage acknowledgment with a tracking ID.
- **7 days**: remediation released for **Critical** severity.
- **30 days**: remediation released for **High** severity.
- **90 days**: remediation released for **Medium/Low** severity.

If a vulnerability cannot be remediated within these windows we publish a
mitigation advisory describing the workaround and the planned fix date.

## Verifying Releases

Every wheel published to PyPI is signed with **sigstore** as part of the
release pipeline. The signature lives next to the wheel as a
`.sigstore` attestation in the corresponding GitHub release.

To verify a downloaded wheel:

```bash
# Install the sigstore CLI
python -m pip install sigstore

# Verify the attestation. The expected identity is the GitHub Actions
# OIDC token issued for the tagged release; the issuer is GitHub.
sigstore verify identity \
    --bundle zephyrex-<version>.whl.sigstore \
    --cert-identity "https://github.com/JamesonRGrieve/ServerFramework/.github/workflows/release.yml@refs/tags/v<version>" \
    --cert-oidc-issuer "https://token.actions.githubusercontent.com" \
    zephyrex-<version>.whl
```

If the verification command exits non-zero, do not install the wheel —
report the discrepancy to the disclosure address above.

## SBOM and Dependency Hashes

Each release ships a CycloneDX-format SBOM (`bom.json`) as a release
asset alongside the wheel. The SBOM enumerates every direct and transitive
dependency with version and hash.

The `requirements.lock` file in the published wheel pins each transitive
dependency to a specific version+hash. Consumers using the lock get
reproducible installs; consumers who install via the version-range
declarations in `pyproject.toml` get the latest compatible release.

## Vulnerability Scanning

CI runs `pip-audit` against `requirements.lock` on every PR and on a
nightly cron against the latest release. The severity gate is **HIGH** —
findings at or above this tier fail the build and open an issue. The
configurable knobs live in `pyproject.toml` under
`[tool.zephyrex.supply_chain]`.

## Best Practices for Contributors

1. **Code review**: All changes require at least one maintainer review
   before merging.
2. **Secrets management**: Never commit credentials, API keys, or tokens.
   Use sandbox credentials for tests (see `extensions/EXT.Test.External.md`)
   and scope them to test-only operations.
3. **Static analysis**: Run `pip-audit`, `bandit`, and the project's
   linter suite before opening a pull request.
4. **Dependency hygiene**: Add new dependencies via `pyproject.toml` and
   regenerate `requirements.lock`. Document the rationale in the PR.

## Policy Updates

This policy is reviewed at least annually and on every Critical-severity
incident. Last reviewed: 2026-04.
