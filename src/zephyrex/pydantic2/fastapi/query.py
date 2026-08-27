import json
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)

import stringcase
from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from zephyrex.lib.ContentNegotiation import (
    MIME_JSON,
    MIME_TOON,
    MIME_TOML,
    MIME_XML,
    MIME_YAML,
)
from zephyrex.lib.Environment import inflection
from zephyrex.pydantic2.util import (
    is_reference_field_name,
    reference_relationship_name,
)

# ---------------------------------------------------------------------------
# Multi-format OpenAPI helpers — content negotiation advertisement
# ---------------------------------------------------------------------------

# All content types supported by the content negotiation middleware.
# Responses advertise all five; POST/PUT/PATCH request bodies accept all five.
_NEGOTIABLE_CONTENT_TYPES: Tuple[str, ...] = (
    MIME_JSON,
    MIME_TOON,
    MIME_YAML,
    MIME_TOML,
    MIME_XML,
)


def _multiformat_response_content(example: Optional[Any] = None) -> Dict[str, Any]:
    """Build a ``content`` dict listing all negotiable MIME types.

    When *example* is provided it is attached to the ``application/json``
    entry; the remaining types are advertised without an inline example
    (the schema is inherited from the route's ``response_model``).
    """
    content: Dict[str, Any] = {}
    for mime in _NEGOTIABLE_CONTENT_TYPES:
        if mime == MIME_JSON and example is not None:
            content[mime] = {"example": example}
        else:
            content[mime] = {}
    return content


def _multiformat_request_body_extra() -> Dict[str, Any]:
    """Return ``openapi_extra`` that adds non-JSON content types to the
    route's request body, reflecting the middleware's ability to
    deserialize TOON, YAML, TOML, and XML in addition to JSON.
    """
    extra_content: Dict[str, Any] = {}
    for mime in _NEGOTIABLE_CONTENT_TYPES:
        if mime == MIME_JSON:
            continue  # FastAPI generates this from Body(...)
        extra_content[mime] = {}
    return {"requestBody": {"content": extra_content}}


class RequestInfo:
    def __init__(self, request_dict: Dict[str, Any]):
        self.method = request_dict.get("method")
        self.url = request_dict.get("url")
        self.base_url = request_dict.get("base_url")
        self.headers = request_dict.get("headers", {})
        self.query_params = request_dict.get("query_params", {})
        self.path_params = request_dict.get("path_params", {})
        self.cookies = request_dict.get("cookies", {})
        self.client = request_dict.get("client", {})
        self.body = request_dict.get("body")
        self.path = request_dict.get("path")
        self.scheme = request_dict.get("scheme")
        self.is_secure = request_dict.get("is_secure")


async def get_request_info(request: Request) -> Dict:
    body = None
    try:
        body_bytes = await request.body()
        if body_bytes:
            try:
                body = json.loads(body_bytes)
            except json.JSONDecodeError:
                body = body_bytes.decode("utf-8")
    except Exception:
        pass

    return {
        "method": request.method,
        "url": str(request.url),
        "base_url": str(request.base_url),
        "headers": dict(request.headers),
        "query_params": dict(request.query_params),
        "path_params": dict(request.path_params),
        "cookies": dict(request.cookies),
        "client": {
            "host": request.client.host if request.client else None,
            "port": request.client.port if request.client else None,
        },
        "body": body,
        "path": request.url.path,
        "scheme": request.url.scheme,
        "is_secure": request.url.is_secure,
    }


def _normalize_query_key(key: str) -> str:
    """Normalize query parameter key names by handling list-style suffixes."""
    return key[:-2] if key.endswith("[]") else key


def _type_accepts_list(annotation: Any) -> bool:
    """Check if a type annotation accepts list-like values."""
    if annotation is None:
        return False

    origin = get_origin(annotation)
    if origin in {list, List, set, Set, tuple, Tuple}:
        return True
    if origin is Union:
        return any(
            _type_accepts_list(arg)
            for arg in get_args(annotation)
            if arg is not type(None)
        )
    if origin is Annotated:
        args = get_args(annotation)
        return bool(args) and _type_accepts_list(args[0])
    return False


def _type_accepts_str(annotation: Any) -> bool:
    """Check if a type annotation accepts string values."""
    if annotation is None:
        return False

    if annotation is str:
        return True

    origin = get_origin(annotation)
    if origin is Union:
        return any(
            _type_accepts_str(arg)
            for arg in get_args(annotation)
            if arg is not type(None)
        )
    if origin is Annotated:
        args = get_args(annotation)
        return bool(args) and _type_accepts_str(args[0])
    return False


def _coerce_sequence_values(raw_values: List[str]) -> List[str]:
    """Expand CSV strings and preserve ordering for list-compatible parameters."""
    coerced: List[str] = []
    for raw_value in raw_values:
        if isinstance(raw_value, str) and "," in raw_value:
            segments = [
                segment.strip() for segment in raw_value.split(",") if segment.strip()
            ]
            if segments:
                coerced.extend(segments)
            else:
                coerced.append(raw_value)
        else:
            coerced.append(raw_value)
    return coerced


def _normalize_projection_values(value: Optional[Union[List[str], str]]) -> List[str]:
    """Normalize projection parameters (fields/includes) into a clean list of strings."""
    if not value:
        return []

    if isinstance(value, str):
        return [segment.strip() for segment in value.split(",") if segment.strip()]

    if isinstance(value, (list, tuple, set, frozenset)):
        normalized: List[str] = []
        for item in value:
            if item is None:
                continue
            item_str = str(item).strip()
            if item_str:
                normalized.append(item_str)
        return normalized

    return []


def _extract_projection_roots(values: List[str]) -> Set[str]:
    """Get root keys from dotted projection paths."""
    roots: Set[str] = set()
    for value in values:
        if not value:
            continue
        roots.add(value.split(".", 1)[0])
    return roots


def _apply_field_projection_to_entity(
    entity: Any, fields: List[str], includes: List[str]
) -> Any:
    """Apply field projection to a serialized entity while preserving included relations."""
    if not fields or entity is None:
        return entity

    if not isinstance(entity, dict):
        return entity

    allowed_keys = _extract_projection_roots(fields)
    allowed_keys.update(_extract_projection_roots(includes))

    if not allowed_keys:
        return entity

    return {key: value for key, value in entity.items() if key in allowed_keys}


def _get_valid_includes_for_model(
    model_class: Type[BaseModel], model_registry: Optional[Any] = None
) -> Set[str]:
    """
    Get the set of valid include names for a model based on its fields.

    Valid includes are derived from:
    1. Fields ending with '_id' (the relationship name without '_id')
    2. Fields ending with '_user' patterns (common relationship patterns)
    3. Plural forms of relationship names
    4. extra_includes class variable on the model (for reverse/virtual relationships)
    5. Inverse relationships discovered via model registry scanning

    Args:
        model_class: The Pydantic model class to analyze
        model_registry: Optional ModelRegistry for discovering inverse relationships

    Returns:
        Set of valid include names
    """
    valid_includes: Set[str] = set()

    if not hasattr(model_class, "model_fields"):
        return valid_includes

    for field_name in model_class.model_fields.keys():
        # Fields ending with _id indicate a relationship
        if is_reference_field_name(field_name):
            relationship_name = reference_relationship_name(field_name)
            valid_includes.add(relationship_name)
            # Also add plural form for collection relationships
            valid_includes.add(inflection.plural(relationship_name))

        # Fields ending with _user_id have special handling
        if field_name.endswith("_user_id"):
            # e.g., created_by_user_id -> created_by_user
            user_relationship = reference_relationship_name(field_name)
            valid_includes.add(user_relationship)

    # Also check for Reference class if it exists
    if hasattr(model_class, "Reference"):
        reference_class = getattr(model_class, "Reference")
        for attr_name in dir(reference_class):
            if not attr_name.startswith("_"):
                attr = getattr(reference_class, attr_name, None)
                if attr is not None and hasattr(attr, "__name__"):
                    if attr.__name__.endswith("Model"):
                        # Convert ModelName to snake_case
                        include_name = stringcase.snakecase(
                            attr.__name__.replace("Model", "")
                        )
                        valid_includes.add(include_name)
                        valid_includes.add(attr_name.lower())

    # Check for extra_includes class variable (for reverse/virtual relationships)
    if hasattr(model_class, "extra_includes"):
        extra = getattr(model_class, "extra_includes", None)
        if extra:
            if isinstance(extra, (list, tuple, set, frozenset)):
                valid_includes.update(extra)
            elif isinstance(extra, str):
                valid_includes.add(extra)

    # Magic: Discover inverse relationships via model registry scanning
    if model_registry and hasattr(model_registry, "bound_models"):
        # Get this model's name in snake_case (e.g., TeamModel -> team)
        model_name = model_class.__name__
        if model_name.endswith("Model"):
            model_name = model_name[:-5]  # Remove 'Model' suffix
        model_name_snake = stringcase.snakecase(model_name)
        fk_pattern = f"{model_name_snake}_id"

        # Scan all bound models for foreign keys pointing to this model
        for bound_model in model_registry.bound_models:
            if bound_model is model_class:
                continue  # Skip self
            if not hasattr(bound_model, "model_fields"):
                continue

            for field_name in bound_model.model_fields.keys():
                if field_name == fk_pattern:
                    # Found a model with FK to this model - add as valid include
                    other_model_name = bound_model.__name__
                    if other_model_name.endswith("Model"):
                        other_model_name = other_model_name[:-5]
                    other_name_snake = stringcase.snakecase(other_model_name)
                    # Add plural form (e.g., InviteeModel -> invitees)
                    valid_includes.add(inflection.plural(other_name_snake))
                    # Also add singular form
                    valid_includes.add(other_name_snake)

    return valid_includes


def _validate_includes(
    include_param: Optional[List[str]],
    model_class: Type[BaseModel],
    resource_name: str,
    model_registry: Optional[Any] = None,
) -> None:
    """
    Validate that requested includes are valid relationships for the model.

    Args:
        include_param: List of include names from the request
        model_class: The target model class
        resource_name: Name of the resource for error messages
        model_registry: Optional ModelRegistry for discovering inverse relationships

    Raises:
        HTTPException: 422 if any includes are invalid
    """
    if not include_param:
        return

    valid_includes = _get_valid_includes_for_model(model_class, model_registry)

    # If we couldn't determine valid includes, skip validation (let BLL handle it)
    if not valid_includes:
        return

    invalid_includes = [inc for inc in include_param if inc not in valid_includes]

    if invalid_includes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Invalid includes requested: {', '.join(invalid_includes)}",
                "invalid_includes": invalid_includes,
                "valid_includes": sorted(list(valid_includes)),
            },
        )


def create_query_model_dependency(
    model_cls: Type[BaseModel],
) -> Callable[[Request], BaseModel]:
    """
    Build a FastAPI dependency that populates a Pydantic model from query parameters.

    Args:
        model_cls: Pydantic model class representing the query parameters.

    Returns:
        Dependency callable that instantiates the model from the request query string.
    """

    model_fields = getattr(model_cls, "model_fields", {})
    alias_map: Dict[str, str] = {}

    for field_name, field_info in model_fields.items():
        alias_map[field_name] = field_name
        alias = getattr(field_info, "alias", None)
        if not alias:
            continue
        if isinstance(alias, str):
            alias_map[alias] = field_name
        elif isinstance(alias, (list, tuple, set, frozenset)):
            for alias_option in alias:
                if isinstance(alias_option, str):
                    alias_map[alias_option] = field_name

    accepts_list_cache = {
        field_name: _type_accepts_list(field_info.annotation)
        for field_name, field_info in model_fields.items()
    }
    accepts_str_cache = {
        field_name: _type_accepts_str(field_info.annotation)
        for field_name, field_info in model_fields.items()
    }

    async def dependency(request: Request) -> BaseModel:
        if not request.query_params:
            return model_cls()

        raw_values: Dict[str, List[str]] = {}
        for raw_key, raw_value in request.query_params.multi_items():
            normalized_key = _normalize_query_key(raw_key)
            field_name = alias_map.get(normalized_key, normalized_key)
            if field_name is None:
                raise HTTPException(
                    status_code=422, detail=f"Unexpected query parameter '{raw_key}"
                )
            raw_values.setdefault(field_name, []).append(raw_value)

        parsed: Dict[str, Any] = {}
        for field_name, values in raw_values.items():
            field_info = model_fields.get(field_name)
            if not field_info:
                parsed[field_name] = values[-1]
                continue

            allows_list = accepts_list_cache.get(field_name, False)
            # For list-compatibility, always normalize to list form
            if allows_list:
                parsed[field_name] = _coerce_sequence_values(values)
            else:
                parsed[field_name] = values[-1]
        try:
            return model_cls(**parsed)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors())

    return dependency  # type: ignore[return-value]


def _render_degradation_sentinel(result: Any) -> Optional[Response]:
    """Item 48 — convert rotation degradation sentinels into HTTP responses.

    Returns ``None`` when ``result`` is not a degradation sentinel so the
    caller can fall through to the regular response path.

    The sentinel field set is owned by
    :func:`zephyrex.extensions.ExternalErrors.extract_degradation_sentinel`
    (the single source of truth); this function only maps the normalized
    projection onto the HTTP transport — selecting the status code (202 for
    queued, 200 for silent-dropped) and shaping the JSON body.
    """
    try:
        from zephyrex.extensions.ExternalErrors import extract_degradation_sentinel
    except Exception:
        return None
    sentinel = extract_degradation_sentinel(result)
    if sentinel is None:
        return None
    if sentinel.kind == "queued":
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"status": sentinel.status, "tracking_id": sentinel.tracking_id},
        )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": sentinel.status,
            "provider": sentinel.provider,
            "ability": sentinel.ability,
        },
    )


def _degradation_responses_annotation(
    manager_class: Any,
) -> Dict[Union[int, str], Dict[str, Any]]:
    """Item 48 — build OpenAPI ``responses`` entries for managers whose
    underlying provider declares ``degradation_policy = QUEUE_AND_RETRY``.

    Best-effort: returns an empty dict if the manager does not expose any
    introspectable degradation policy. The 202 entry is intentionally
    additive — callers merge the result into their existing ``responses``
    dict so the existing 200/201 entries are preserved.
    """
    try:
        from zephyrex.extensions.ExternalErrors import (
            DegradationMode,
            QueuedForRetryModel,
        )
    except Exception:
        return {}

    # Look for a ``degradation_policy`` attribute on the manager itself or
    # any of its declared providers / provider chain. The probe is
    # deliberately permissive — anything raising during introspection is
    # treated as "no annotation" so OpenAPI generation is never broken.
    candidates: List[Any] = []
    if manager_class is not None:
        candidates.append(manager_class)
        for attr in ("provider", "providers", "_provider", "_providers"):
            value = getattr(manager_class, attr, None)
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                candidates.extend(value)
            else:
                candidates.append(value)

    for cand in candidates:
        try:
            policy = getattr(cand, "degradation_policy", None)
            if policy is None:
                continue
            mode = getattr(policy, "mode", None)
            mode_value = getattr(mode, "value", mode)
            if mode_value == getattr(
                DegradationMode.QUEUE_AND_RETRY, "value", "queue_and_retry"
            ):
                return {
                    202: {
                        "model": QueuedForRetryModel,
                        "description": (
                            "Operation queued for retry; poll tracking_id "
                            "for completion"
                        ),
                    }
                }
        except Exception:
            continue
    return {}


def _normalize_query_list(value: Any) -> Optional[List[str]]:
    """Normalize query param that may be None, a string, or a list/tuple into a list of strings.

    - If value is None -> None
    - If value is a string -> split on commas, strip whitespace, dedupe preserving order
    - If value is a list/tuple -> split each item on commas, strip, dedupe
    Returns None if result is empty.
    """
    if value is None:
        return None
    # If FastAPI already parsed a list/tuple
    if isinstance(value, (list, tuple)):
        seen: list[str] = []
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            # Split each item on commas in case it's "provider,team" format
            item_str = str(item)
            parts = [p.strip() for p in item_str.split(",") if p.strip()]
            for s in parts:
                if s not in seen:
                    seen.append(s)
                    out.append(s)
        return out if out else None
    # If it's a string, split on commas
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
        return parts if parts else None
    # Fallback: coerce to string
    s = str(value).strip()
    return [s] if s else None
