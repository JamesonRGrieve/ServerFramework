import os
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
)

from fastapi import Depends, Header, HTTPException, Request, Security, status
from fastapi.params import Depends as DependsParam
from fastapi.security import HTTPBasic
from pydantic import BaseModel, ValidationError

from zephyrex.lib.Logging import logger

from .types import AuthType
from .query import get_request_info

if TYPE_CHECKING:
    from zephyrex.pydantic2.manager_contract import ManagerContract as ManagerContract


def get_auth_dependency(auth_type: AuthType) -> Optional[Any]:
    """Get the authentication dependency based on auth_type."""
    if auth_type == AuthType.JWT:
        from zephyrex.lib.AuthProvider import get_auth_provider

        def jwt_auth(
            request: Request,
            authorization: str = Header(None),
            x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
        ):
            model_registry = (
                getattr(request.app.state, "model_registry", None) if request else None
            )

            # Support both JWT and API key for JWT endpoints
            if x_api_key:
                return get_auth_provider().auth(
                    model_registry=model_registry,
                    authorization=f"Bearer {x_api_key}",
                    request=request,
                )
            elif authorization:
                return get_auth_provider().auth(
                    model_registry=model_registry,
                    authorization=authorization,
                    request=request,
                )

            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No authentication provided.",
            )

        return Depends(jwt_auth)

    elif auth_type == AuthType.API_KEY:

        def api_key_auth(
            request: Request,
            authorization: str = Header(None),
            x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
        ):
            from zephyrex.lib.AuthProvider import get_auth_provider

            if x_api_key:
                # API key provided - authenticate with it
                model_registry = (
                    getattr(request.app.state, "model_registry", None)
                    if request
                    else None
                )
                return get_auth_provider().auth(
                    model_registry=model_registry,
                    authorization=f"Bearer {x_api_key}",
                    request=request,
                )
            elif authorization:
                # JWT provided but API key required - forbidden
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="API key required for this operation",
                )
            else:
                # No auth provided at all - unauthorized
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="No authentication provided.",
                )

        return Depends(api_key_auth)

    elif auth_type == AuthType.BASIC:
        return Security(HTTPBasic())

    else:
        return None


def extract_body_data(
    body: Union[Dict[str, Any], BaseModel, List[Any]],
    resource_name: str,
    resource_name_plural: str,
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Extract data from a request body object.

    Handles different body formats:
    - Pydantic models with nested attributes
    - Plain dictionaries
    - Lists of models
    """
    # Handle list of items
    if isinstance(body, list):
        return [
            extract_body_data(item, resource_name, resource_name_plural)  # type: ignore[misc]
            for item in body
        ]

    # Handle plain dictionary
    if isinstance(body, dict):
        if resource_name in body:
            data = body[resource_name]
            if isinstance(data, list):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Format mismatch: singular key '{resource_name}' cannot contain array data",
                )
            return data  # type: ignore[no-any-return]
        elif resource_name_plural in body:
            data = body[resource_name_plural]
            if not isinstance(data, list):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Format mismatch: plural key '{resource_name_plural}' must contain array data",
                )
            return data
        return body

    # Handle Pydantic model
    if hasattr(body, "__dict__"):
        if hasattr(body, resource_name):
            attr_value = getattr(body, resource_name)
            if hasattr(attr_value, "model_dump"):
                return attr_value.model_dump(exclude_unset=True)  # type: ignore[no-any-return]
            return attr_value  # type: ignore[no-any-return]

        if hasattr(body, resource_name_plural):
            attr_value = getattr(body, resource_name_plural)
            if hasattr(attr_value, "model_dump"):
                return attr_value.model_dump(exclude_unset=True)  # type: ignore[no-any-return]
            return attr_value  # type: ignore[no-any-return]

        # Extract first attribute if no specific attribute found
        attribute_names = list(vars(body).keys())
        if attribute_names:
            actual_name = attribute_names[0]
            if hasattr(body, actual_name):
                attr_value = getattr(body, actual_name)
                if hasattr(attr_value, "model_dump"):
                    return attr_value.model_dump(exclude_unset=True)  # type: ignore[no-any-return]
                return attr_value  # type: ignore[no-any-return]

    return {}


def serialize_for_response(
    data: Union[None, Dict[str, Any], BaseModel, List[Any]],
) -> Union[None, Dict[str, Any], List[Dict[str, Any]]]:
    """Serialize data for FastAPI response models."""
    if data is None:
        return None

    if isinstance(data, list):
        return [serialize_for_response(item) for item in data]  # type: ignore[misc]

    from pydantic import BaseModel

    if isinstance(data, BaseModel):
        try:
            return data.model_dump()
        except Exception as e:
            logger.error(f"Failed to serialize model {type(data).__name__}: {e}")
            if hasattr(data, "dict"):
                return data.dict()
            return str(data)  # type: ignore[return-value]

    return data


# ---------------------------------------------------------------------------
# Item 45 — REST integration helpers for the FieldACL primitive
# ---------------------------------------------------------------------------


def _resolve_has_permission(manager: Any) -> Optional[Callable[[str], bool]]:
    """Best-effort resolver for the ``has_permission`` callable on a manager.

    The framework's mainline User model doesn't currently expose
    ``has_permission`` directly; extension and future versions wire it via
    ``manager.requester.has_permission``. Return ``None`` so the caller
    no-ops the ACL when the manager has no policy to enforce — this keeps
    framework-internal callers (system-key audit jobs) functioning.
    """
    if manager is None:
        return None
    requester = getattr(manager, "requester", None)
    if requester is None:
        return None
    fn = getattr(requester, "has_permission", None)
    if not callable(fn):
        return None

    def _check(name: str) -> bool:
        try:
            return bool(fn(name))
        except Exception:  # pragma: no cover - defensive
            return False

    return _check


def apply_field_acl_to_payload(
    payload: Any,
    manager: Any,
    model_cls: Optional[Type[BaseModel]],
    *,
    sentinel_mode: Optional[str] = None,
    cache: Optional[Any] = None,
) -> Any:
    """Item 45 — REST hook that filters disallowed fields from a serialized
    payload using the manager's requester permission resolver.

    Pass through unchanged when the manager has no resolvable
    ``has_permission`` callable so framework-internal callers continue to
    function. The sentinel mode (``omit`` | ``mask``) defaults to the
    deployment-wide ``FIELD_ACL_SENTINEL`` env var, falling back to
    ``omit`` per the FieldACL contract.
    """
    from zephyrex.lib.FieldACL import (
        DEFAULT_SENTINEL_MODE,
        apply_field_acl_to_response,
    )

    has_perm = _resolve_has_permission(manager)
    if has_perm is None or model_cls is None:
        return payload

    mode = sentinel_mode or os.getenv("FIELD_ACL_SENTINEL") or DEFAULT_SENTINEL_MODE
    requester_id = None
    requester = getattr(manager, "requester", None)
    if requester is not None:
        requester_id = getattr(requester, "id", None)
        if requester_id is not None:
            requester_id = id(requester)
    return apply_field_acl_to_response(
        payload,
        model_cls,
        has_perm,
        sentinel_mode=mode,
        cache=cache,
        requester_id=requester_id,
    )


def validate_field_acl_query(
    manager: Any,
    model_cls: Optional[Type[BaseModel]],
    fields: List[str],
    context: str,
) -> None:
    """Item 45 — reject requests using restricted fields the requester
    cannot access.

    Raises HTTP 403 when the requester references restricted fields in
    sort, filter, or projection clauses. ``context`` is folded into the
    audit-log error message ("sort_by", "filter", "projection") so the
    inference-attack vector is distinguishable in operational dashboards.
    """
    if not fields or model_cls is None:
        return

    from zephyrex.lib.FieldACL import validate_query_field_access

    has_perm = _resolve_has_permission(manager)
    if has_perm is None:
        return

    disallowed = validate_query_field_access(
        model_cls, fields, has_perm, context=context
    )
    if disallowed:
        raise HTTPException(
            status_code=403,
            detail={
                "error": (
                    f"Access denied to restricted fields in {context}: "
                    f"{sorted(disallowed)}"
                ),
                "context": context,
                "fields": sorted(disallowed),
            },
        )


def _populate_user_includes(
    items: List[Dict[str, Any]],
    user_includes: List[str],
    model_registry: Any,
    requester_id: Optional[str],
) -> None:
    """Resolve ``*_user`` / ``user`` include keys in-place to the actual user via
    a single permission-filtered batch fetch (no per-row query). A value that is
    absent or empty (``{}`` from an unloaded relationship) is (re)resolved from
    the sibling ``*_id``; a genuinely-loaded nested object is left untouched. A
    user the requester cannot view resolves to ``None``."""
    if not user_includes or not items:
        return
    from zephyrex.lib.AuthProvider import get_auth_provider
    from zephyrex.lib.Environment import env

    try:
        user_mgr = get_auth_provider()(
            requester_id=requester_id or env("ROOT_ID"),
            model_registry=model_registry,
        )
    except Exception:
        return

    needed: List[Any] = []
    seen: set = set()
    for item in items:
        for inc in user_includes:
            id_field = f"{inc}_id"
            if id_field not in item or item.get(inc):
                continue
            uid = item.get(id_field)
            if uid and uid not in seen:
                seen.add(uid)
                needed.append(uid)

    user_map: Dict[str, Any] = {}
    if needed:
        try:
            users = user_mgr.list(filters=[user_mgr.DB.id.in_(needed)])
        except Exception:
            users = []
        for user in users or []:
            uid = (
                user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
            )
            if uid is not None:
                user_map[str(uid)] = serialize_for_response(user)

    for item in items:
        for inc in user_includes:
            id_field = f"{inc}_id"
            if id_field not in item or item.get(inc):
                continue
            uid = item.get(id_field)
            item[inc] = user_map.get(str(uid)) if uid else None


def _populate_includes_on_serialized(
    serialized: Union[Dict[str, Any], List[Dict[str, Any]]],
    include_selection: Optional[List[str]],
    model_registry: Any,
    requester_id: Optional[str] = None,
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Populate requested include navigation properties on already-serialized data.

    ``*_user`` / ``user`` includes are resolved to the actual user through a
    single permission-filtered batch fetch (fixing the empty-``{}`` result and
    avoiding a per-row N+1). Any other requested include key that is still
    missing gets an empty placeholder (plural -> ``[]``, singular -> ``{}``) so
    the navigation key is always present in the response.
    """
    if not include_selection or serialized is None:
        return serialized

    single = False
    items: List[Dict[str, Any]] = []
    if isinstance(serialized, dict):
        single = True
        items = [serialized]
    elif isinstance(serialized, list):
        items = serialized
    else:
        return serialized

    user_includes = [k for k in include_selection if k == "user" or k.endswith("_user")]
    _populate_user_includes(items, user_includes, model_registry, requester_id)

    for item in items:
        for include_key in include_selection:
            if include_key not in item:
                item[include_key] = [] if include_key.endswith("s") else {}

    return items[0] if single else items


def create_manager_factory(
    manager_class: Type["ManagerContract"],
    model_registry: Any,
    auth_type: AuthType = AuthType.JWT,
) -> Callable:
    """
    Create a factory function for a manager class.

    Args:
        manager_class: The manager class to create a factory for
        model_registry: Model registry instance
        auth_type: The authentication type to use

    Returns:
        Factory function that creates manager instances
    """

    def _normalize_headers(raw_headers: Any) -> Dict[str, str]:
        normalized: Dict[str, str] = {}
        if isinstance(raw_headers, dict):
            items = raw_headers.items()
        elif isinstance(raw_headers, list):
            items = raw_headers  # type: ignore[assignment]
        else:
            items = []  # type: ignore[assignment]

        for key, value in items:
            if isinstance(key, bytes):
                key = key.decode()
            if isinstance(value, (list, tuple)) and value:
                value = value[0]
            if isinstance(value, bytes):
                value = value.decode()
            normalized[str(key).lower()] = value
        return normalized

    def _prepare_request_info(request: Any) -> Optional[Dict[str, Any]]:
        if request is None:
            return None
        if isinstance(request, DependsParam):
            return None
        if isinstance(request, Request):
            return {
                "headers": dict(request.headers),
                "query_params": dict(request.query_params),
                "path_params": dict(request.path_params),
            }
        if isinstance(request, dict):
            return request
        return None

    def factory_function(request: Any = Depends(get_request_info)) -> Any:
        """Factory function to get manager instance."""
        from zephyrex.lib.AuthProvider import get_auth_provider

        request_info = _prepare_request_info(request)
        requester_id: Optional[str] = None

        if auth_type == AuthType.NONE:
            requester_id = None
        elif request_info:
            from zephyrex.lib.InboundSecurity import (
                resolve_principal_from_api_key,
            )

            headers = _normalize_headers(request_info.get("headers", {}))
            api_key = headers.get("x-api-key")

            principal = resolve_principal_from_api_key(api_key) if api_key else None
            if principal:
                requester_id = principal
            else:
                auth_header = headers.get("authorization")
                if auth_header:
                    user = get_auth_provider().auth(
                        model_registry=model_registry,
                        authorization=auth_header,
                        request=request_info,
                    )
                    if user and hasattr(user, "id"):
                        requester_id = user.id

        if auth_type != AuthType.NONE and not requester_id:
            raise HTTPException(
                status_code=401, detail="Could not determine requester."
            )

        manager_params: Dict[str, Any] = {"requester_id": requester_id}
        if model_registry is not None:
            manager_params["model_registry"] = model_registry

        try:
            return manager_class(**manager_params)
        except TypeError:
            return manager_class(requester_id=requester_id)

    factory_function.__manager_class__ = manager_class  # type: ignore[attr-defined]
    return factory_function


def _build_links(
    base_path: str,
    entity_id: Optional[str] = None,
    resource_plural: Optional[str] = None,
) -> Dict[str, Any]:
    """Build HATEOAS _links for a resource."""
    links: Dict[str, Any] = {}
    if entity_id:
        links["self"] = {"href": f"{base_path}/{entity_id}"}
        if resource_plural:
            collection_path = base_path
            links["collection"] = {"href": collection_path}
    else:
        links["self"] = {"href": base_path}
    return links


def _error_envelope(
    message: str,
    code: Optional[str] = None,
    errors: Optional[List[Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build a normalized error detail envelope.

    Every error response uses ``{message, code, errors}`` so clients
    can write a single error handler.
    """
    envelope: Dict[str, Any] = {"message": message}
    if code is not None:
        envelope["code"] = code
    if errors:
        envelope["errors"] = errors
    envelope.update(extra)
    return envelope


def handle_resource_operation_error(err: Exception) -> None:
    """Handle resource operation errors and raise appropriate HTTP exceptions."""
    if isinstance(err, ValidationError):
        try:
            details: Any = err.errors()
            for error in details if isinstance(details, list) else []:
                error.pop("input", None)
                error.pop("ctx", None)
        except TypeError:
            details = [str(err)]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error_envelope(
                "Validation error", code="validation_error", errors=details
            ),
        )
    elif isinstance(err, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error_envelope("Validation error", code="validation_error"),
        )
    elif isinstance(err, HTTPException):
        if isinstance(err.detail, str):
            err.detail = _error_envelope(err.detail)  # type: ignore[assignment]
        raise err
    elif isinstance(err, (TypeError, AttributeError, KeyError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error_envelope("Invalid request body format", code="invalid_body"),
        )

    try:
        from zephyrex.extensions.ExternalErrors import TransientExternalError

        if isinstance(err, TransientExternalError):
            raise HTTPException(
                status_code=502,
                detail=_error_envelope(
                    "Upstream service temporarily unavailable", code="bad_gateway"
                ),
            )
    except ImportError:
        pass

    else:
        logger.exception(f"Unexpected error during operation: {err}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_error_envelope(
                "An unexpected error occurred", code="internal_error"
            ),
        )
