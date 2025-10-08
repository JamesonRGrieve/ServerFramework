import os
from abc import ABC
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlparse

import tldextract
from dotenv import load_dotenv
from inflect import engine
from pydantic import BaseModel, create_model, field_validator

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

    APP_NAME: str = "Server"
    APP_DESCRIPTION: str = "extensible server framework"
    APP_VERSION: str = "0.0.0"
    APP_REPOSITORY: str = "https://github.com/ZephyrexTechnologies/ServerFramework"
    APP_EXTENSIONS: str = "email,auth_mfa,database,meta_logging,payment"

    ROOT_API_KEY: str = "n0ne"
    SERVER_URI: str = "http://localhost:1996"
    ALLOWED_DOMAINS: str = "*"

    DATABASE_TYPE: str = "sqlite"
    DATABASE_NAME: Optional[str] = "database"
    DATABASE_SSL: str = "disable"
    DATABASE_HOST: str = "localhost"
    DATABASE_PORT: str = "5432"
    DATABASE_USER: Optional[str] = None
    DATABASE_PASSWORD: str = "Password1!"

    LOCALIZATION: str = "en"
    REST: str = "true"
    GQL: str = "true"
    GQL_DEPTH: int = 3
    MCP: str = "false"
    LOG_FORMAT: str = "%(asctime)s | %(levelname)s | %(message)s"
    LOG_LEVEL: str = "INFO"
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
    UVICORN_WORKERS: Optional[str] = 1

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

    model_config = {"env_file": ".env", "case_sensitive": True, "extra": "ignore"}

    @classmethod
    def register_env_vars(cls, env: Dict[str, Any]) -> None:
        """
        Register additional environment variables at runtime.
        This allows extensions and providers to inject their own environment variables.

        Args:
            env: Dictionary mapping environment variable names to their default values
        """
        from lib.Logging import logger

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
                        field_type = int
                    elif isinstance(default_value, float):
                        field_type = float
                    elif isinstance(default_value, str):
                        field_type = str
                    else:
                        # Default to str for unknown types
                        field_type = str

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
                new_model = create_model(cls.__name__, __base__=BaseModel, **new_fields)

            # Log success message if we registered any new variables
            if registered_count > 0:
                logger.debug(
                    f"Successfully registered {registered_count} new environment variables"
                )

        except Exception as e:
            logger.error(f"Error registering environment variables: {e}")


settings = AppSettings.model_validate(os.environ)


def register_extension_env_vars(env: Optional[Dict[str, Any]]) -> None:
    """
    Register environment variables from extensions and providers.
    This updates both the AppSettings model and the global settings instance.

    Args:
        env: Dictionary mapping environment variable names to their default values
    """
    from lib.Logging import logger

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


def env(var: str) -> str:
    """
    Get environment variable with fallback to default values.
    For backward compatibility with existing code.
    """
    if hasattr(settings, var):
        value = getattr(settings, var)
        return str(value) if value is not None else ""
    return os.getenv(var, "")


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
