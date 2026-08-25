import re
import uuid
from datetime import datetime
from typing import Any, Dict, FrozenSet, Type, TypeVar

from pydantic import BaseModel
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String

from zephyrex.lib.Environment import inflection

# Type variable for generic models
T = TypeVar("T", bound=BaseModel)
SQLAlchemyModelType = TypeVar("SQLAlchemyModelType")
DatabaseManagerType = TypeVar("DatabaseManagerType")

# Using shared inflection instance from Environment
inflect_engine: Any = inflection

# Map Pydantic types to SQLAlchemy types
TYPE_MAPPING: Dict[Type[Any], Type[Any]] = {
    str: String,
    int: Integer,
    bool: Boolean,
    datetime: DateTime,
    uuid.UUID: String,
    float: Float,
    dict: JSON,
    list: JSON,
    # Add more type mappings as needed
}

# Regex to extract tablename from a class name
TABLENAME_REGEX: re.Pattern[str] = re.compile(r"(?<!^)(?=[A-Z])")

# Reserved SQLAlchemy field names that need to be renamed
RESERVED_SQLALCHEMY_NAMES: FrozenSet[str] = frozenset(
    {
        "metadata",
        "registry",
        "query",
        "session",
        "bind",
        "mapper",
        "class_",
        "table",
        "columns",
        "primary_key",
        "foreign_keys",
        "constraints",
        "indexes",
        "info",
        "schema",
        "autoload",
        "autoload_with",
    }
)
