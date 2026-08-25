import json
from datetime import date, datetime
from typing import Any

import strawberry

from zephyrex.lib.AbstractPydantic2 import TypeIntrospector


def enum_serializer(value: Any) -> str:
    """Serialize enum values to their string representation"""
    if hasattr(value, "name"):
        return value.name  # type: ignore[no-any-return]
    elif hasattr(value, "value"):
        return value.value  # type: ignore[no-any-return]
    return str(value)


# Configure GraphQL scalar types
@strawberry.scalar(
    description="DateTime scalar",
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: datetime.fromisoformat(v) if v else None,
)
class DateTimeScalar:
    pass


@strawberry.scalar(
    description="Date scalar",
    serialize=lambda v: v.isoformat() if v else None,
    parse_value=lambda v: date.fromisoformat(v) if v else None,
)
class DateScalar:
    pass


# Define scalar types for complex data
@strawberry.scalar(
    description="Any JSON-serializable value",
    serialize=lambda v: (
        v
        if isinstance(v, str)
        else (
            enum_serializer(v)
            if hasattr(v, "name") or hasattr(v, "value")
            else json.dumps(v) if v is not None else None
        )
    ),
    parse_value=lambda v: (
        v if isinstance(v, str) else json.loads(v) if v is not None else None
    ),
)
class AnyScalar:
    pass


ANY_SCALAR = AnyScalar


@strawberry.scalar(
    description="JSON object",
    serialize=lambda v: json.dumps(v) if v is not None else None,
    parse_value=lambda v: json.loads(v) if v is not None else None,
)
class DictScalar:
    pass


DICT_SCALAR = DictScalar


@strawberry.scalar(
    description="JSON array",
    serialize=lambda v: json.dumps(v) if v is not None else None,
    parse_value=lambda v: json.loads(v) if v is not None else None,
)
class ListScalar:
    pass


LIST_SCALAR = ListScalar

# Remove generic type - not needed

# Map Python types to GraphQL scalar types
TYPE_MAPPING = {
    str: strawberry.scalar(
        str,
        description="String value",
        serialize=lambda v: v if v is not None else None,
        parse_value=lambda v: v if v is not None else None,
    ),
    int: strawberry.scalar(int, description="Integer value"),
    float: strawberry.scalar(float, description="Float value"),
    bool: strawberry.scalar(bool, description="Boolean value"),
    datetime: DateTimeScalar,
    date: DateScalar,
    dict: DICT_SCALAR,
    list: LIST_SCALAR,
    Any: ANY_SCALAR,
}


# Create a shared type introspector instance
_type_introspector: TypeIntrospector = TypeIntrospector()
