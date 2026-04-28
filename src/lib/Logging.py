import re
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from loguru import logger

from lib.Environment import env

logger.remove()

log_level = env("LOG_LEVEL")
server_timezone = env("TZ")

# Names of `extra` keys (and headers) whose values must never appear in logs.
# Match is case-insensitive and applied recursively through nested dicts/lists.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "old_password",
        "token",
        "jwt",
        "api_key",
        "apikey",
        "x-api-key",
        "secret",
        "client_secret",
        "refresh_token",
        "access_token",
        "id_token",
        "authorization",
        "auth",
        "session_key",
        "totp_secret",
        "private_key",
    }
)

_REDACTED = "[REDACTED]"

# Regex patterns for inline-secret scrubbing in message strings.
# Format: (compiled pattern, replacement). Each pattern captures the secret
# in group 'v' and rewrites it to [REDACTED].
_INLINE_PATTERNS = [
    re.compile(
        r"(?i)\b(" + "|".join(re.escape(k) for k in _SENSITIVE_KEYS) + r")"
        r"\s*[=:]\s*['\"]?(?P<v>[^\s'\"&,;}]+)['\"]?"
    ),
    re.compile(r"(?i)Bearer\s+(?P<v>[A-Za-z0-9._\-+/=]+)"),
    re.compile(r"(?i)Basic\s+(?P<v>[A-Za-z0-9+/=]+)"),
]


def _scrub_value(value):
    """Recursively redact sensitive entries in dict/list/str structures."""
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else _scrub_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def _scrub_string(text):
    """Apply inline-secret regex scrubbing to a message string."""
    if not text:
        return text
    for pattern in _INLINE_PATTERNS:
        text = pattern.sub(
            lambda m: m.group(0).replace(m.group("v"), _REDACTED), text
        )
    return text


def _redaction_patcher(record):
    """Loguru patcher: scrub sensitive keys/values before any sink sees the record."""
    extras = record.get("extra")
    if extras:
        for key in list(extras.keys()):
            if key.lower() in _SENSITIVE_KEYS:
                extras[key] = _REDACTED
            else:
                extras[key] = _scrub_value(extras[key])
    # Scrub the rendered message too — catches f-strings like
    # f"login attempt with password={canary}".
    record["message"] = _scrub_string(record.get("message", ""))


logger = logger.patch(_redaction_patcher)


def format_with_timezone(record):
    """Format log record with server timezone"""
    if server_timezone != "UTC":
        # Convert UTC time to server timezone for display
        utc_time = record["time"].replace(tzinfo=ZoneInfo("UTC"))
        local_time = utc_time.astimezone(ZoneInfo(server_timezone))
        record["time"] = local_time
    return record


LOG_LEVEL_MAP = {
    "CRITICAL": 50,
    "ERROR": 40,
    "WARNING": 30,
    "INFO": 20,
    "DEBUG": 10,
    "VERBOSE": 5,
    "SQL": 3,
    "NOTSET": 0,
}
logger.level("VERBOSE", no=5, color="<blue>")
logger.level("SQL", no=3, color="<magenta>")

logger.add(sys.stdout, level=log_level, filter=format_with_timezone)
