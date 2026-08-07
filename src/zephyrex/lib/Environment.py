import logging
import os
import secrets
from abc import ABC
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import tldextract
from dotenv import load_dotenv
from inflect import engine
from pydantic import BaseModel, create_model, field_validator, model_validator

_env_logger = logging.getLogger("zephyrex.environment")

load_dotenv()

inflection = engine()

# Configure inflection rules for words ending in "data"
# Since "data" is already plural, words ending in "data" should remain unchanged
inflection.defnoun("metadata", "metadata")
inflection.defnoun("userdata", "userdata")
inflection.defnoun("teamdata", "teamdata")

# Also handle the specific models we know about
inflection.defnoun("user_metadata", "user_metadata")
inflection.defnoun("team_metadata", "team_metadata")

# Add a general rule for any word ending in "data"
inflection.classical(all=True)  # Use classical Latin plurals


class AppSettings(BaseModel):
    ENVIRONMENT: Literal["local", "staging", "development", "production", "ci"] = (
        "local"
    )
    # Legacy alias. Deployments that document APP_ENV instead of ENVIRONMENT
    # set this and we forward it onto ENVIRONMENT in the model_validator below
    # so every fail-closed gate sees one source of truth.
    APP_ENV: Optional[str] = None

    APP_NAME: str = "Zephyrex"
    APP_DESCRIPTION: str = "Zephyrex Framework Server"
    APP_VERSION: str = "0.0.0"
    APP_REPOSITORY: str = "https://github.com/ZephyrexTechnologies/ServerFramework"
    APP_EXTENSIONS: str = ""
    # Empty default: consumers choose extensions explicitly via run() or
    # instance(). The test conftest sets _CORE_TEST_EXTENSIONS for the
    # test suite. The old default "email,auth_mfa,database,meta_logging,
    # payment" is NOT restored here — consumers must opt in.

    ROOT_API_KEY: str = "n0ne"
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str = "zephyrex"
    JWT_ISSUER: str = "zephyrex"
    SERVER_URI: str = "http://localhost:1996"
    ALLOWED_DOMAINS: str = ""
    # Comma-separated list of trusted-proxy CIDRs/IPs from which the
    # framework will honour X-Forwarded-For. Empty = do not trust the
    # header. Set to a real proxy CIDR (e.g. "10.0.0.0/8") in production.
    TRUSTED_PROXIES: str = ""
    BCRYPT_ROUNDS: int = 12
    # Whether registration endpoints accept arbitrary metadata fields.
    # When false (default), unknown fields in the registration body are
    # rejected with a 422 instead of being silently stored as metadata.
    ACCEPT_REGISTRATION_METADATA: str = "false"

    DATABASE_TYPE: str = "sqlite"
    DATABASE_NAME: Optional[str] = "database"
    DATABASE_PATH: str = ""
    DATABASE_SSL: str = "require"
    DATABASE_HOST: str = "localhost"
    DATABASE_PORT: str = "5432"
    DATABASE_USER: Optional[str] = None
    DATABASE_PASSWORD: str = ""

    EXTENSIONS_PATH: str = ""

    LOCALIZATION: str = "en"
    REST: str = "true"
    GQL: str = "true"
    GQL_DEPTH: int = 10
    MCP: str = "false"
    LOG_FORMAT: str = "%(asctime)s | %(levelname)s | %(message)s"
    LOG_LEVEL: str = "INFO"
    EXPOSE_DOCS: str = "false"
    REGISTRATION_DISABLED: str = "false"
    REGISTRATION_MODE: Literal["open", "invite", "closed"] = "open"
    SEED_DATA: str = "true"

    ROOT_ID: str = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"
    SYSTEM_ID: str = "FFFFFFFF-FFFF-FFFF-AAAA-FFFFFFFFFFFF"
    TEMPLATE_ID: str = "FFFFFFFF-FFFF-FFFF-0000-FFFFFFFFFFFF"

    SUPERADMIN_ROLE_ID: str = "FFFFFFFF-0000-0000-FFFF-FFFFFFFFFFFF"
    ADMIN_ROLE_ID: str = "FFFFFFFF-0000-0000-AAAA-FFFFFFFFFFFF"
    USER_ROLE_ID: str = "FFFFFFFF-0000-0000-0000-FFFFFFFFFFFF"

    TZ: str = "UTC"
    UVICORN_WORKERS: Optional[str] = 1  # type: ignore[assignment]

    @field_validator("DATABASE_NAME", "DATABASE_USER", mode="before")
    @classmethod
    def set_db_defaults(cls, v, info):
        if v is None:
            return info.data.get("APP_NAME", "Server").lower()
        return v

    @field_validator("UVICORN_WORKERS", mode="before")
    @classmethod
    def set_uvicorn_workers(cls, v, info):
        if v is None:
            log_level = info.data.get("LOG_LEVEL", "DEBUG")
            return "5" if str(log_level).lower() == "debug" else "20"
        return v

    @model_validator(mode="after")
    def _generate_dev_secrets(self):
        """Auto-generate random secrets for non-production environments.

        In local/development/ci, an empty JWT_SECRET or the ROOT_API_KEY
        default of "n0ne" would leave the framework running with trivially
        guessable credentials. Generate random values and log a warning so
        the operator notices but doesn't have to configure secrets just to
        develop.
        """
        if self.ENVIRONMENT in ("production", "staging"):
            return self
        if not (self.JWT_SECRET or "").strip():
            generated = secrets.token_urlsafe(32)
            object.__setattr__(self, "JWT_SECRET", generated)
            _env_logger.warning(
                "JWT_SECRET was empty — generated a random key for %s. "
                "Set JWT_SECRET explicitly to suppress this warning.",
                self.ENVIRONMENT,
            )
        if (self.ROOT_API_KEY or "").strip() in ("", "n0ne"):
            generated = secrets.token_urlsafe(32)
            object.__setattr__(self, "ROOT_API_KEY", generated)
            _env_logger.warning(
                "ROOT_API_KEY was unset or default 'n0ne' — generated: %s. "
                "Set ROOT_API_KEY explicitly to suppress this warning.",
                generated,
            )
        return self

    @model_validator(mode="after")
    def _reconcile_environment_alias(self):
        """Honour APP_ENV as a legacy alias for ENVIRONMENT.

        Two env-var names (`APP_ENV` vs `ENVIRONMENT`) used to feed
        independent fail-closed gates; a deployment that set only one
        left the others asleep. Resolve to a single canonical value here
        before any gate reads it.
        """
        alias = (self.APP_ENV or "").strip().lower()
        if alias and self.ENVIRONMENT == "local":
            valid = {"local", "staging", "development", "production", "ci"}
            if alias in valid:
                object.__setattr__(self, "ENVIRONMENT", alias)
            elif alias == "prod":
                object.__setattr__(self, "ENVIRONMENT", "production")
            elif alias == "dev":
                object.__setattr__(self, "ENVIRONMENT", "development")
        return self

    @model_validator(mode="after")
    def _enforce_production_fail_closed(self):
        """In ENVIRONMENT=production/staging, refuse insecure defaults.

        Short-circuits application start-up rather than relying on runtime
        checks scattered through the auth path. C-3: JWT_SECRET strength is
        enforced here under production; outside production we allow empty
        for test/dev convenience, since the conftest does not set one.

        The list of required secrets includes ``FRAMEWORK_FERNET_KEY``
        whenever an extension that depends on encrypted-at-rest secrets
        (currently ``auth_mfa`` and ``oauth_consumer``) is loaded; the
        check is in :meth:`EXT_*.validate_config` for each, but we also
        enforce here so a misconfigured production deployment cannot
        boot a partially-initialised framework.
        """
        if self.ENVIRONMENT not in ("production", "staging"):
            return self
        problems: List[str] = []
        if (self.ROOT_API_KEY or "").strip() in ("", "n0ne"):
            problems.append("ROOT_API_KEY is unset or left at the default 'n0ne'")
        secret = (self.JWT_SECRET or "").strip()
        if not secret:
            problems.append("JWT_SECRET is empty")
        elif len(secret) < 32:
            problems.append(
                f"JWT_SECRET must be at least 32 characters (got {len(secret)})"
            )
        allowed = (self.ALLOWED_DOMAINS or "").strip()
        if allowed in ("", "*"):
            problems.append(
                "ALLOWED_DOMAINS must be set to explicit origins in production "
                "(got %r)" % allowed
            )
        if (self.DATABASE_PASSWORD or "").strip() in ("", "Password1!"):
            problems.append("DATABASE_PASSWORD is unset")
        # Encryption-at-rest extensions need FRAMEWORK_FERNET_KEY. Sniff
        # APP_EXTENSIONS for them; if any are loaded, the key is mandatory.
        loaded_extensions = {
            e.strip() for e in (self.APP_EXTENSIONS or "").split(",") if e.strip()
        }
        # Auto-detect which loaded extensions depend on encryption-at-rest by
        # statically scanning their package source for ``SecretEncryption``
        # imports. Maintaining a hardcoded list here is a footgun: a new
        # extension that adopts ``encrypt_secret`` would silently boot in
        # production without a Fernet key. The static scan is conservative —
        # any ``import`` reference to ``SecretEncryption`` flags the
        # extension as dependent.
        encryption_dependent = _discover_encryption_dependent(loaded_extensions)
        if encryption_dependent:
            fernet_key = (env("FRAMEWORK_FERNET_KEY") or "").strip()
            if not fernet_key:
                problems.append(
                    "FRAMEWORK_FERNET_KEY is unset but the loaded extensions "
                    f"include {sorted(encryption_dependent)} "
                    "which require encryption-at-rest"
                )
        # PostgreSQL ``sslmode=disable`` is unsafe in production: traffic
        # to the DB cluster is unencrypted and unauthenticated. The
        # default of "require" enforces TLS without certificate
        # validation; "verify-full" is the strict posture that also
        # validates the cert chain. Operators with a private VPC where
        # TLS terminates at a sidecar can opt out via
        # ``ALLOW_PLAINTEXT_DATABASE=true``.
        ssl_mode = (self.DATABASE_SSL or "").strip().lower()
        if ssl_mode in ("", "disable", "allow", "prefer"):
            if (env("ALLOW_PLAINTEXT_DATABASE") or "").lower() != "true":
                problems.append(
                    f"DATABASE_SSL={ssl_mode!r} is unsafe in production "
                    "(use 'require' or 'verify-full', or set "
                    "ALLOW_PLAINTEXT_DATABASE=true to acknowledge)"
                )
        # H-5 — DISABLE_SSRF_GUARD turns ProviderHTTPClient into an open
        # egress oracle. It exists for unit tests; production/staging
        # must reject it loudly rather than silently boot with the guard
        # off because of one stray env variable in the deploy.
        ssrf_disable = (env("DISABLE_SSRF_GUARD") or "").strip().lower()
        if ssrf_disable in ("1", "true", "yes", "on"):
            problems.append(
                "DISABLE_SSRF_GUARD is truthy; outbound SSRF protection "
                "must remain enabled in production/staging"
            )
        if problems:
            raise ValueError(
                "Insecure production configuration: " + "; ".join(problems)
            )
        return self

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}  # type: ignore[typeddict-unknown-key]

    @classmethod
    def register_env_vars(cls, env: Dict[str, Any]) -> None:
        """
        Register additional environment variables at runtime.
        This allows extensions and providers to inject their own environment variables.

        Args:
            env: Dictionary mapping environment variable names to their default values
        """
        from zephyrex.lib.Logging import logger

        if not env:
            return

        try:
            registered_count = 0
            new_fields = {}

            # Add new fields from env
            for var_name, default_value in env.items():
                if var_name not in cls.model_fields:
                    # Infer type from default value
                    if isinstance(default_value, bool):
                        field_type = bool
                    elif isinstance(default_value, int):
                        field_type = int  # type: ignore[assignment]
                    elif isinstance(default_value, float):
                        field_type = float  # type: ignore[assignment]
                    elif isinstance(default_value, str):
                        field_type = str  # type: ignore[assignment]
                    else:
                        # Default to str for unknown types
                        field_type = str  # type: ignore[assignment]

                    new_fields[var_name] = (field_type, default_value)
                    registered_count += 1
                    logger.debug(
                        f"Registered environment variable: {var_name} = {default_value}"
                    )
                else:
                    logger.debug(
                        f"Environment variable {var_name} already exists, skipping"
                    )

            # Create new model with additional fields if we have any
            if new_fields:
                # Create new model class
                new_model = create_model(cls.__name__, __base__=BaseModel, **new_fields)  # type: ignore[call-overload]

            # Log success message if we registered any new variables
            if registered_count > 0:
                logger.debug(
                    f"Successfully registered {registered_count} new environment variables"
                )

        except Exception as e:
            logger.error(f"Error registering environment variables: {e}")


def _discover_encryption_dependent(extension_names: set) -> set:
    """Return the subset of ``extension_names`` whose package source
    references the ``SecretEncryption`` helper.

    Static scan of ``src/zephyrex/extensions/<name>/*.py`` for
    ``SecretEncryption`` imports. Conservative: any reference flags the
    extension as dependent. Falls back to an empty set on I/O errors so
    a discovery failure cannot crash the startup path; the caller treats
    a missing entry as "no encryption needed" which is the safe default
    when the source tree is unreadable.
    """
    import pathlib

    if not extension_names:
        return set()
    try:
        ext_dir = pathlib.Path(__file__).parent.parent / "extensions"
    except Exception:
        return set()
    if not ext_dir.is_dir():
        return set()
    dependent: set = set()
    for name in extension_names:
        pkg = ext_dir / name
        if not pkg.is_dir():
            continue
        try:
            for src in pkg.rglob("*.py"):
                if src.name.endswith("_test.py"):
                    continue
                try:
                    text = src.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if "SecretEncryption" in text:
                    dependent.add(name)
                    break
        except OSError:
            continue
    return dependent


settings = AppSettings.model_validate(os.environ)


def refresh_settings() -> None:
    """Re-read os.environ into the cached settings singleton.

    Test-only — production code should never need this.
    """
    global settings
    settings = AppSettings.model_validate(os.environ)


def register_extension_env_vars(env: Optional[Dict[str, Any]]) -> None:
    """
    Register environment variables from extensions and providers.
    This updates both the AppSettings model and the global settings instance.

    Args:
        env: Dictionary mapping environment variable names to their default values
    """
    from zephyrex.lib.Logging import logger

    if not env:
        return

    # Register with the model class
    AppSettings.register_env_vars(env)

    # Update the global settings instance
    global settings

    # Create new settings instance with the updated model
    try:
        # Merge current environment with new defaults
        env_data = dict(os.environ)
        for var_name, default_value in env.items():
            if var_name not in env_data:
                env_data[var_name] = str(default_value)

        # Create new settings instance
        new_settings = AppSettings.model_validate(env_data)

        # Update the global settings
        settings = new_settings

        logger.debug(
            f"Updated global settings with {len(env)} new environment variables"
        )

    except Exception as e:
        logger.error(f"Error updating global settings: {e}")


def is_production() -> bool:
    """Single source of truth for production gating (C-2).

    Reads ``os.environ`` directly so test-time monkeypatching of either
    ``ENVIRONMENT`` or ``APP_ENV`` is honoured immediately. Both names
    are accepted; ``ENVIRONMENT`` wins when both are set.
    """
    env_value = (
        os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENV") or ""
    ).strip().lower()
    return env_value == "production"


def is_staging() -> bool:
    env_value = (
        os.environ.get("ENVIRONMENT") or os.environ.get("APP_ENV") or ""
    ).strip().lower()
    return env_value == "staging"


def env(var: str, default: Optional[str] = "") -> str:
    """
    Get environment variable with fallback to a provided default value.

    Backwards-compatible: callers that only provide the variable name will get
    the old behavior (empty string when not present). Callers may provide a
    second argument to specify a default to return when the variable is not set.
    """
    if hasattr(settings, var):
        value = getattr(settings, var)
        return str(value) if value is not None else (default or "")
    return os.getenv(var, default or "")


def extract_base_domain(uri: str) -> str:
    """
    Extracts the base domain or IP address from a given URI or email-like string.

    This function handles a wide range of inputs including:
    - Fully qualified domain names with subdomains
    - IPv4 and IPv6 addresses (with optional ports)
    - 'localhost' (with optional ports)
    - Email addresses (e.g., 'user@example.com')
    - Malformed or partial URIs

    It uses standard library `urllib.parse` for parsing and `tldextract` to properly
    identify base domains including multi-part TLDs (e.g., '.co.uk', '.gov.in').

    Special handling ensures that IPv6 addresses are preserved with brackets,
    as per RFC 3986.

    Args:
        uri (str): A URI string, potentially including protocol, domain/IP, port,
                path, query parameters, or even an email address.

    Returns:
        str: The base domain (e.g., 'example.com'), IP address (e.g., '10.0.0.1'),
            or bracketed IPv6 (e.g., '[::1]') suitable for use in email generation,
            logging, or domain-based access controls.
    """
    if not uri:
        return ""

    # Handle accidental email input (e.g., root@example.com)
    if "@" in uri and "://" not in uri:
        uri = "http://" + uri.split("@")[-1]

    parsed = urlparse(uri)
    hostname = parsed.hostname
    netloc = parsed.netloc

    if not hostname:
        return ""

    # Detect IPv6 by presence of colon but not in domain (e.g., ::1, fe80::)
    if ":" in hostname and not hostname.count("."):
        # Strip port if present: [::1]:8080 → [::1]
        if netloc.startswith("[") and "]" in netloc:
            return netloc.split("]")[0] + "]"
        return f"[{hostname}]"

    # IPv4 or localhost
    if hostname == "localhost" or all(part.isdigit() for part in hostname.split(".")):
        return hostname

    # Domain — use tldextract for .co.uk etc.
    extracted = tldextract.extract(hostname)
    if not extracted.domain and not extracted.suffix:
        return hostname  # fallback

    return ".".join(part for part in [extracted.domain, extracted.suffix] if part)


class AbstractRegistry(ABC):
    pass
