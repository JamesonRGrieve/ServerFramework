import json
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    Union,
)

import stringcase
from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel as RouteModel
from pydantic import ValidationError, create_model

from zephyrex.lib.Environment import inflection
from zephyrex.lib.InboundSecurity import carry_rate_limit
from zephyrex.lib.Logging import logger
from zephyrex.lib.Preconditions import (
    IF_MATCH_HEADER,
    VERSION_FIELDS,
    etag_headers,
    expect_route_version,
    expect_versions,
)
from zephyrex.pydantic2.util import manager_resource_name

from .types import AuthType, CustomRouteConfig, RouteType
from .query import (
    _apply_field_projection_to_entity,
    _degradation_responses_annotation,
    _multiformat_request_body_extra,
    _multiformat_response_content,
    _normalize_projection_values,
    _normalize_query_list,
    _render_degradation_sentinel,
    _validate_includes,
    create_query_model_dependency,
    get_request_info,
)
from .examples import ExampleGenerator
from .resource import (
    _build_links,
    _populate_includes_on_serialized,
    _populate_reverse_includes,
    _resolve_has_permission,
    apply_field_acl_to_payload,
    create_manager_factory,
    extract_body_data,
    get_auth_dependency,
    handle_resource_operation_error,
    serialize_for_response,
    validate_field_acl_query,
)

if TYPE_CHECKING:
    from zephyrex.pydantic2.manager_contract import ManagerContract as ManagerContract
    from .types import NetworkModelProtocol as NetworkModelProtocol

# The If-Match request header of a single-record write: the record's ETag as
# the client read it. Missing is refused (428) unless the deployment set
# IF_MATCH_REQUIRED=false.
_IF_MATCH = Header(
    default=None,
    alias=IF_MATCH_HEADER,
    description="The record's ETag as last read; a stale one is refused (412)",
)
# A batch delete names the version of each record it deletes in one If-Match
# list (`"<v1>", "<v2>"`); a record at none of them refuses the batch.
_BATCH_IF_MATCH = Header(
    default=None,
    alias=IF_MATCH_HEADER,
    description="The ETags of the records as last read; one stale refuses all (412)",
)


class BatchTarget(RouteModel):
    """A batch update target: the record's id and, optionally, the ETag the
    client read it at."""

    id: str
    if_match: Optional[str] = None


def register_route(
    router: APIRouter,
    route_type: RouteType,
    manager_class: Type["ManagerContract"],
    model_registry: Any,
    auth_type: AuthType,
    route_auth_overrides: Dict[RouteType, AuthType],
    examples: Dict[str, Dict[str, Any]],
    child_manager_class: Optional[Type["ManagerContract"]] = None,
    parent_param_name: Optional[str] = None,
    manager_property: Optional[str] = None,
) -> None:
    """
    Register a single route type on the router.

    Args:
        router: The FastAPI router
        route_type: Type of route (get, list, create, update, delete, search, batch_update, batch_delete)
        manager_class: The manager class
        model_registry: Model registry instance
        auth_type: Default authentication type
        route_auth_overrides: Route-specific auth overrides
        examples: Example responses for documentation
        parent_param_name: Name of parent parameter for nested routes
        manager_property: Property to access for nested managers
    """
    # Check if manager_class is actually a class
    if not isinstance(manager_class, type):
        logger.error(
            f"register_route called with invalid manager_class: {manager_class} (type: {type(manager_class)}). Expected a class but got {type(manager_class).__name__}. Route type: {route_type}. This indicates a bug in the caller."
        )
        return

    base_model = getattr(manager_class, "Model", None)
    if base_model is None:
        logger.error(
            f"Manager class {manager_class.__name__} has no Model. Route type: {route_type}. Skipping route registration."
        )
        return

    bound_base_model = base_model
    if model_registry and hasattr(model_registry, "apply"):
        try:
            bound_base_model = model_registry.apply(base_model)
        except Exception as exc:
            logger.warning(
                f"Failed to apply model registry to {base_model}: {exc}. Using base model."
            )

    # Derive resource names
    if manager_property:
        resource_name_plural = manager_property
        # ``singular_noun`` returns False for a word that is already singular.
        resource_name = (
            inflection.singular_noun(resource_name_plural) or resource_name_plural
        )
        # manager_property is only ever set together with child_manager_class
        # by the nested-resource caller (see register_custom_route below).
        assert child_manager_class is not None
        child_base_model = child_manager_class.Model
        if model_registry and hasattr(model_registry, "apply"):
            try:
                child_base_model = model_registry.apply(child_base_model)
            except Exception as exc:
                logger.warning(
                    f"Failed to apply model registry to {child_manager_class}: {exc}."
                )
        if not hasattr(child_base_model, "Network"):
            logger.error(
                f"Child base model {child_base_model} does not define Network model."
            )
            return
        network_model: "NetworkModelProtocol" = child_base_model.Network
        target_model = child_base_model
    else:
        resource_name = manager_resource_name(manager_class)
        resource_name_plural = inflection.plural(resource_name)
        if not hasattr(bound_base_model, "Network"):
            logger.error(
                f"Base model {bound_base_model} does not define Network model."
            )
            return
        network_model = bound_base_model.Network
        target_model = bound_base_model

    # Generate examples if not provided
    if not examples or route_type not in examples:
        try:
            generated_examples = ExampleGenerator.generate_operation_examples(
                network_model, resource_name
            )
            # Apply any overrides from manager configuration
            if (
                hasattr(manager_class, "example_overrides")
                and manager_class.example_overrides
            ):
                for key, override in manager_class.example_overrides.items():
                    if key in generated_examples:
                        generated_examples[key] = ExampleGenerator.customize_example(
                            generated_examples[key], override
                        )
            examples = generated_examples
        except Exception as e:
            logger.warning(f"Failed to generate examples for {resource_name}: {e}")
            examples = {}

    # The route's own auth (an override, else the manager's) governs both
    # the dependency and how the manager is built.
    route_auth = route_auth_overrides.get(route_type, auth_type)
    auth_dependency = get_auth_dependency(route_auth)
    manager_factory: Callable = create_manager_factory(
        manager_class, model_registry, route_auth
    )

    # Build dependencies
    dependencies = [auth_dependency] if auth_dependency else None

    # Parent name for nested routes
    parent_name = parent_param_name.replace("_id", "") if parent_param_name else None

    # Common route handling logic
    def get_manager(manager_instance, property_path):
        """Get the appropriate manager instance based on property path."""
        if property_path:
            current = manager_instance
            for prop in property_path.split("."):
                current = getattr(current, prop)
            return current
        return manager_instance

    # Item 48 — best-effort 202/QueuedForRetry annotation for managers
    # whose underlying provider declares ``QUEUE_AND_RETRY``. Empty for
    # managers that do not opt in.
    degradation_responses = _degradation_responses_annotation(manager_class)

    _common_error_responses: Dict[Union[int, str], Dict[str, Any]] = {
        304: {"description": "Not Modified — ETag matched, no body returned"},
        401: {"description": "Unauthorized — missing or invalid authentication"},
        403: {"description": "Forbidden — insufficient permissions"},
        404: {"description": "Not Found — resource does not exist"},
        410: {"description": "Gone — resource was deleted"},
        415: {
            "description": "Unsupported Media Type — use application/json, toon, yaml, toml, or xml"
        },
        418: {
            "description": "I'm a Teapot — you hit a honeypot (scanner probe detected)"
        },
        422: {"description": "Unprocessable Entity — validation error"},
        423: {
            "description": "Locked — resource is under an advisory lock, retry later"
        },
        429: {"description": "Too Many Requests — rate limit exceeded"},
        451: {"description": "Unavailable For Legal Reasons — GDPR erasure applied"},
        500: {"description": "Internal Server Error"},
        502: {"description": "Bad Gateway — upstream provider temporarily unavailable"},
        503: {
            "description": "Service Unavailable — server is draining / shutting down"
        },
        507: {"description": "Insufficient Storage — quota exceeded"},
    }
    _mutation_responses: Dict[Union[int, str], Dict[str, Any]] = {
        **_common_error_responses,
        412: {
            "description": "Precondition Failed — If-Match ETag mismatch (entity modified since last read)"
        },
        428: {
            "description": "Precondition Required — If-Match header required for this resource"
        },
    }
    _list_responses: Dict[Union[int, str], Dict[str, Any]] = {
        **_common_error_responses,
        416: {
            "description": "Range Not Satisfiable — requested page/offset beyond available items"
        },
    }

    if route_type == RouteType.GET:
        _build_get_route(
            router=router,
            model_registry=model_registry,
            examples=examples,
            parent_param_name=parent_param_name,
            manager_property=manager_property,
            resource_name=resource_name,
            resource_name_plural=resource_name_plural,
            network_model=network_model,
            target_model=target_model,
            manager_factory=manager_factory,
            dependencies=dependencies,
            parent_name=parent_name,
            get_manager=get_manager,
            degradation_responses=degradation_responses,
            _common_error_responses=_common_error_responses,
        )
    elif route_type == RouteType.LIST:
        _build_list_route(
            router=router,
            model_registry=model_registry,
            examples=examples,
            parent_param_name=parent_param_name,
            manager_property=manager_property,
            resource_name=resource_name,
            resource_name_plural=resource_name_plural,
            network_model=network_model,
            target_model=target_model,
            manager_factory=manager_factory,
            dependencies=dependencies,
            parent_name=parent_name,
            get_manager=get_manager,
            degradation_responses=degradation_responses,
            _list_responses=_list_responses,
        )
    elif route_type == RouteType.CREATE:
        _build_create_route(
            router=router,
            examples=examples,
            parent_param_name=parent_param_name,
            manager_property=manager_property,
            resource_name=resource_name,
            resource_name_plural=resource_name_plural,
            network_model=network_model,
            manager_factory=manager_factory,
            dependencies=dependencies,
            parent_name=parent_name,
            get_manager=get_manager,
            degradation_responses=degradation_responses,
            _mutation_responses=_mutation_responses,
        )
    elif route_type == RouteType.UPDATE:
        _build_update_route(
            router=router,
            model_registry=model_registry,
            examples=examples,
            manager_property=manager_property,
            resource_name=resource_name,
            resource_name_plural=resource_name_plural,
            network_model=network_model,
            manager_factory=manager_factory,
            dependencies=dependencies,
            parent_name=parent_name,
            get_manager=get_manager,
            degradation_responses=degradation_responses,
            _mutation_responses=_mutation_responses,
            target_model=target_model,
        )
    elif route_type == RouteType.DELETE:
        _build_delete_route(
            router=router,
            manager_property=manager_property,
            resource_name=resource_name,
            manager_factory=manager_factory,
            dependencies=dependencies,
            parent_name=parent_name,
            get_manager=get_manager,
        )
    elif route_type == RouteType.SEARCH:
        _build_search_route(
            router=router,
            model_registry=model_registry,
            examples=examples,
            parent_param_name=parent_param_name,
            manager_property=manager_property,
            resource_name=resource_name,
            resource_name_plural=resource_name_plural,
            network_model=network_model,
            target_model=target_model,
            manager_factory=manager_factory,
            dependencies=dependencies,
            parent_name=parent_name,
            get_manager=get_manager,
            _list_responses=_list_responses,
        )
    elif route_type == RouteType.BATCH_UPDATE:
        _build_batch_update_route(
            router=router,
            examples=examples,
            manager_property=manager_property,
            resource_name=resource_name,
            resource_name_plural=resource_name_plural,
            network_model=network_model,
            manager_factory=manager_factory,
            dependencies=dependencies,
            get_manager=get_manager,
            _mutation_responses=_mutation_responses,
        )
    elif route_type == RouteType.BATCH_DELETE:
        _build_batch_delete_route(
            router=router,
            manager_property=manager_property,
            resource_name_plural=resource_name_plural,
            manager_factory=manager_factory,
            dependencies=dependencies,
            get_manager=get_manager,
        )


def _build_get_route(
    router: Any,
    model_registry: Any,
    examples: Any,
    parent_param_name: Any,
    manager_property: Any,
    resource_name: Any,
    resource_name_plural: Any,
    network_model: Any,
    target_model: Any,
    manager_factory: Any,
    dependencies: Any,
    parent_name: Any,
    get_manager: Any,
    degradation_responses: Any,
    _common_error_responses: Any,
) -> None:
    """Register the GET route (extracted from register_route, #226)."""
    path = "/{id}" if not parent_param_name else "/{id}"
    summary = f"Get {resource_name}" + (f" for {parent_name}" if parent_name else "")

    # Prepare responses with examples — advertise all negotiable formats
    # (typed with `Union[int, str]` keys to match APIRouter's
    # `responses` parameter, which also accepts "default" etc.)
    responses: Dict[Union[int, str], Dict[str, Any]] = {
        200: {"content": _multiformat_response_content(examples.get("get"))},
        **_common_error_responses,
    }
    responses.update(degradation_responses)

    get_query_dependency = create_query_model_dependency(network_model.GET)

    @router.get(
        path,
        summary=summary,
        response_model=network_model.ResponseSingle,
        status_code=status.HTTP_200_OK,
        dependencies=dependencies,
        responses=responses,
    )
    # `network_model.GET` is a live runtime expression here (no
    # `from __future__ import annotations` in this module), evaluated
    # at def-time to the per-model class FastAPI needs for query
    # validation. mypy can't resolve a dynamic attribute access used
    # as an annotation, so this is ignored rather than restructured.
    async def get_resource(
        request: Dict = Depends(get_request_info),
        id: str = Path(..., description=f"{stringcase.titlecase(resource_name)} ID"),
        query_params: network_model.GET = Depends(get_query_dependency),
        manager=Depends(manager_factory),
    ):
        try:
            # A nested GET is scoped to the parent in its path: a child of a
            # different parent is not found here.
            parent_scope = (
                {parent_param_name: request["path_params"][parent_param_name]}
                if parent_param_name and request
                else {}
            )

            # Normalize include/fields query params to lists (accept comma-separated strings)
            include_param = _normalize_query_list(
                getattr(query_params, "include", None)
            )
            fields_param = _normalize_query_list(getattr(query_params, "fields", None))

            if fields_param:
                # Get valid field names from the target model
                valid_fields = set(target_model.model_fields.keys())

                # Check for invalid fields
                invalid_fields = [f for f in fields_param if f not in valid_fields]

                if invalid_fields:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": f"Invalid fields requested: {', '.join(invalid_fields)}",
                            "invalid_fields": invalid_fields,
                            "valid_fields": sorted(list(valid_fields)),
                        },
                    )

            # Validate includes against model relationships
            actual_manager = get_manager(manager, manager_property)
            registry = getattr(actual_manager, "model_registry", None)
            _validate_includes(include_param, target_model, resource_name, registry)

            # The record's version is its ETag (If-Match / If-None-Match); a
            # projection that leaves the timestamps out still carries it.
            version_headers: Dict[str, str] = {}
            if fields_param and not set(fields_param) & set(VERSION_FIELDS):
                version_headers = etag_headers(
                    actual_manager.get(id=id, **parent_scope)
                )

            result = actual_manager.get(
                id=id, include=include_param, fields=fields_param, **parent_scope
            )

            if (resp := _render_degradation_sentinel(result)) is not None:
                return resp

            if result is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{stringcase.titlecase(resource_name)} with ID '{id}' not found",
                )

            # Ensure the manager return value is serialized into plain data
            # so Pydantic can validate it reliably (models -> dicts)
            serialized_result = serialize_for_response(result)
            version_headers = version_headers or etag_headers(serialized_result)

            # Check if fields are specified early to avoid validation errors
            fields_selection = _normalize_projection_values(query_params.fields)
            include_selection = _normalize_projection_values(query_params.include)

            # Build the Response model first (preserves Pydantic conversions and any included relationships),
            # then serialize and attach synthesized includes (option C)
            # Skip ResponseSingle creation when fields are specified to avoid validation errors with partial data
            if not fields_selection:
                response_model_instance = network_model.ResponseSingle(
                    **{resource_name: serialized_result}
                )
                serialized_entity = serialize_for_response(
                    getattr(response_model_instance, resource_name)
                )
            else:
                # When fields are specified, work directly with serialized_result
                serialized_entity = serialized_result

            from zephyrex.lib.AuthProvider import get_auth_provider

            def _attach_user_includes_to_entity(entity: Optional[Dict[str, Any]]):
                if not entity or not include_selection:
                    return
                # Map include token -> id field (e.g., updated_by_user -> updated_by_user_id)
                user_includes = [
                    inc for inc in include_selection if inc.endswith("_user")
                ]
                if not user_includes:
                    return

                # Build a user manager to fetch user objects
                try:
                    user_mgr = get_auth_provider()(
                        requester_id=manager.requester.id,
                        model_registry=manager.model_registry,
                    )
                except Exception:
                    # Fallback: don't attach if we cannot instantiate
                    return

                for inc in user_includes:
                    id_field = f"{inc}_id"
                    # Some models keep created_by_user_id/updated_by_user_id - try these too
                    if id_field not in entity:
                        # allow include like 'created_by_user' to map to 'created_by_user_id'
                        # if not present, skip
                        continue

                    # If include already present (the database layer loaded it,
                    # as the requester), don't overwrite
                    # Truthy, not `is not None`: an unloaded relationship or an
                    # empty nested DTO field serializes to {} (falsy), which must
                    # still be resolved from the *_id -- only a genuinely-loaded
                    # object (truthy) is left untouched.
                    if inc in entity and entity.get(inc):
                        continue

                    user_id = entity.get(id_field)
                    if not user_id:
                        entity[inc] = None
                        continue

                    try:
                        user_obj = user_mgr.get(id=user_id)
                        entity[inc] = (
                            serialize_for_response(user_obj)
                            if user_obj is not None
                            else None
                        )
                    except Exception:
                        entity[inc] = None

            _attach_user_includes_to_entity(serialized_entity)  # type: ignore[arg-type]
            if include_selection and isinstance(serialized_entity, dict):
                _populate_reverse_includes(
                    [serialized_entity],
                    include_selection,
                    target_model,
                    model_registry,
                    getattr(getattr(manager, "requester", None), "id", None),
                )

            # Item 45 — apply field-level ACL after include attachment so
            # disallowed fields are stripped from the final response shape.
            serialized_entity = apply_field_acl_to_payload(
                serialized_entity, manager, target_model
            )

            # If fields projection requested, apply it now and return JSON
            if fields_selection:
                projected_entity = _apply_field_projection_to_entity(
                    serialized_entity, fields_selection, include_selection
                )
                return JSONResponse(
                    content=jsonable_encoder({resource_name: projected_entity}),
                    status_code=status.HTTP_200_OK,
                    headers=version_headers,
                )

            if include_selection:
                populated = _populate_includes_on_serialized(
                    serialized_result, include_selection, model_registry, requester_id=getattr(getattr(manager, "requester", None), "id", None), model_class=target_model  # type: ignore[arg-type]
                )
                populated = apply_field_acl_to_payload(populated, manager, target_model)
                return JSONResponse(
                    content=jsonable_encoder({resource_name: populated}),
                    status_code=status.HTTP_200_OK,
                    headers=version_headers,
                )

            # Without fields or includes, the response_model_instance built
            # above (fields_selection is empty here) is the body.
            # Item 45 — for the Pydantic-validated path, also re-render
            # with field-acl filtering applied so the contract holds
            # uniformly across all return shapes.
            _entity_id = (
                serialized_entity.get("id")
                if isinstance(serialized_entity, dict)
                else None
            )
            _links = (
                _build_links(
                    f"/v1/{resource_name}",
                    entity_id=_entity_id,
                    resource_plural=resource_name_plural,
                )
                if _entity_id
                else {}
            )

            if _resolve_has_permission(manager) is not None:
                content = {resource_name: serialized_entity}
                if _links:
                    content["_links"] = _links
                return JSONResponse(
                    content=jsonable_encoder(content),
                    status_code=status.HTTP_200_OK,
                    headers=version_headers,
                )
            content = response_model_instance.model_dump()
            if _links:
                content["_links"] = _links
            return JSONResponse(
                content=jsonable_encoder(content),
                status_code=status.HTTP_200_OK,
                headers=version_headers,
            )
        except Exception as err:
            handle_resource_operation_error(err)


def _build_list_route(
    router: Any,
    model_registry: Any,
    examples: Any,
    parent_param_name: Any,
    manager_property: Any,
    resource_name: Any,
    resource_name_plural: Any,
    network_model: Any,
    target_model: Any,
    manager_factory: Any,
    dependencies: Any,
    parent_name: Any,
    get_manager: Any,
    degradation_responses: Any,
    _list_responses: Any,
) -> None:
    """Register the LIST route (extracted from register_route, #226)."""
    path = ""
    summary = f"List {resource_name_plural}" + (
        f" for {parent_name}" if parent_name else ""
    )

    # Prepare responses with examples — advertise all negotiable formats
    responses = {
        200: {"content": _multiformat_response_content(examples.get("list"))},
        **_list_responses,
    }
    responses.update(degradation_responses)

    list_query_dependency = create_query_model_dependency(network_model.LIST)

    @router.get(
        path,
        summary=summary,
        response_model=network_model.ResponsePlural,
        status_code=status.HTTP_200_OK,
        dependencies=dependencies,
        responses=responses,
    )
    async def list_resources(
        request: Dict = Depends(get_request_info),
        # see get_resource() above: dynamic attr-as-annotation, live at runtime
        query_params: network_model.LIST = Depends(list_query_dependency),
        manager=Depends(manager_factory),
    ):
        try:
            search_params = {}
            if parent_param_name and request:
                parent_id = request["path_params"][parent_param_name]
                search_params[parent_param_name] = parent_id

            # Reserved query parameters that are not filter fields
            reserved_params = {
                "include",
                "fields",
                "offset",
                "limit",
                "page",
                "page_size",
                "pageSize",
                "sort_by",
                "sort_order",
            }

            _FILTER_OPS = {
                "eq",
                "neq",
                "lt",
                "gt",
                "lteq",
                "gteq",
                "inc",
                "sw",
                "ew",
                "before",
                "after",
                "on",
            }
            for field_name in type(query_params).model_fields.keys():
                if field_name not in reserved_params:
                    field_value = getattr(query_params, field_name, None)
                    if field_value is not None:
                        search_params[field_name] = field_value

            raw_qp = (
                request.get("query_params", {}) if isinstance(request, dict) else {}
            )
            for raw_key, raw_val in raw_qp.items():
                if "__" not in raw_key:
                    continue
                base, op = raw_key.rsplit("__", 1)
                if op not in _FILTER_OPS:
                    continue
                if base not in search_params:
                    search_params[base] = {}
                elif not isinstance(search_params[base], dict):
                    search_params[base] = {"eq": search_params[base]}
                search_params[base][op] = raw_val

            include_param = _normalize_query_list(
                getattr(query_params, "include", None)
            )
            fields_param = _normalize_query_list(getattr(query_params, "fields", None))

            if fields_param:
                # Get valid field names from the target model
                valid_fields = set(target_model.model_fields.keys())

                # Check for invalid fields
                invalid_fields = [f for f in fields_param if f not in valid_fields]

                if invalid_fields:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": f"Invalid fields requested: {', '.join(invalid_fields)}",
                            "invalid_fields": invalid_fields,
                            "valid_fields": sorted(list(valid_fields)),
                        },
                    )

            # Validate includes against model relationships
            actual_manager = get_manager(manager, manager_property)
            registry = getattr(actual_manager, "model_registry", None)
            _validate_includes(include_param, target_model, resource_name, registry)

            # Validate sort_by field if provided
            sort_by_param = query_params.sort_by
            if sort_by_param:
                valid_fields = set(target_model.model_fields.keys())
                if sort_by_param not in valid_fields:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": f"Invalid field for sort_by: {sort_by_param}",
                            "invalid_fields": [sort_by_param],
                            "valid_fields": sorted(list(valid_fields)),
                        },
                    )

            # Validate sort_order if provided
            sort_order_param = query_params.sort_order
            if sort_order_param and sort_order_param.lower() not in ("asc", "desc"):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": f"Invalid sort_order: {sort_order_param}. Must match pattern ^(asc|desc)$",
                        "validation_error": f"sort_order must be 'asc' or 'desc', got '{sort_order_param}'",
                    },
                )

            # Item 45 — reject restricted-field references in sort_by /
            # filters / projection before SQL is generated. Inference
            # attacks via ORDER BY on a restricted column are equivalent
            # to direct read.
            if sort_by_param:
                validate_field_acl_query(
                    manager, target_model, [sort_by_param], "sort_by"
                )
            if fields_param:
                validate_field_acl_query(
                    manager, target_model, fields_param, "projection"
                )
            if search_params:
                validate_field_acl_query(
                    manager,
                    target_model,
                    list(search_params.keys()),
                    "filter",
                )

            page_param = getattr(query_params, "page", None)
            page_size_param = getattr(query_params, "page_size", None) or getattr(
                query_params, "pageSize", None
            )
            _pagination_total: Optional[int] = None

            if page_param is not None and page_size_param is not None:
                list_limit = page_size_param
                if page_param < 0:
                    _pagination_total = actual_manager.count(**search_params)
                    total_pages = max(1, -(-_pagination_total // page_size_param))
                    resolved_page = total_pages + 1 + page_param
                    list_offset = max(0, (resolved_page - 1) * page_size_param)
                else:
                    list_offset = (page_param - 1) * page_size_param
            else:
                list_limit = query_params.limit or 100
                list_offset = query_params.offset or 0

            results = actual_manager.list(
                include=include_param,
                fields=fields_param,
                offset=list_offset,
                limit=list_limit,
                sort_by=query_params.sort_by,
                sort_order=query_params.sort_order or "asc",
                **search_params,
            )

            if (resp := _render_degradation_sentinel(results)) is not None:
                return resp

            _result_count = len(results) if isinstance(results, list) else 0
            if list_offset > 0 and _result_count == 0:
                return JSONResponse(
                    status_code=416,
                    content={"detail": "Requested range contains no items"},
                    headers={"Content-Range": "items */*"},
                )

            if _pagination_total is None:
                try:
                    _pagination_total = actual_manager.count(**search_params)
                except (AttributeError, TypeError):
                    _pagination_total = _result_count
            _pagination_meta = {
                "offset": list_offset,
                "limit": list_limit,
                "total": _pagination_total,
                "has_more": (list_offset + _result_count) < _pagination_total,
            }

            # Serialize list items before constructing response model
            serialized_results = serialize_for_response(results)
            try:
                response_model_instance = network_model.ResponsePlural(
                    **{resource_name_plural: serialized_results}
                )
            except ValidationError:
                return JSONResponse(
                    content=jsonable_encoder(
                        {
                            resource_name_plural: serialized_results,
                            "pagination": _pagination_meta,
                        }
                    ),
                    status_code=status.HTTP_200_OK,
                )

            serialized_items = (
                serialize_for_response(
                    getattr(response_model_instance, resource_name_plural)
                )
                or []
            )

            include_selection = _normalize_projection_values(query_params.include)

            from zephyrex.lib.AuthProvider import get_auth_provider

            def _attach_user_includes_to_items(items: List[Dict[str, Any]]):
                if not items or not include_selection:
                    return
                user_includes = [
                    inc for inc in include_selection if inc.endswith("_user")
                ]
                if not user_includes:
                    return
                try:
                    user_mgr = get_auth_provider()(
                        requester_id=manager.requester.id,
                        model_registry=manager.model_registry,
                    )
                except Exception:
                    return

                # First pass: decide per (entity, include) what needs
                # resolving and collect every referenced user id. Entities
                # already populated, or missing a usable id, are settled now.
                pending: List[Tuple[Dict[str, Any], str, str]] = []
                needed_ids: List[Any] = []
                seen_ids: set = set()
                for entity in items:
                    for inc in user_includes:
                        id_field = f"{inc}_id"
                        if id_field not in entity:
                            continue
                        # Truthy, not `is not None`: an empty ({}) nested field
                        # from an unloaded relationship must still be resolved.
                        if inc in entity and entity.get(inc):
                            continue
                        user_id = entity.get(id_field)
                        if not user_id:
                            entity[inc] = None
                            continue
                        pending.append((entity, inc, str(user_id)))
                        if user_id not in seen_ids:
                            seen_ids.add(user_id)
                            needed_ids.append(user_id)

                if not needed_ids:
                    return

                # One permission-filtered batch fetch for every referenced
                # user, instead of a get() per entity (was O(entities x
                # user-includes) point queries). A user the requester cannot
                # view is absent from the map -> attached as None, matching
                # the old per-get semantics exactly.
                try:
                    users = user_mgr.list(filters=[user_mgr.DB.id.in_(needed_ids)])
                except Exception:
                    for entity, inc, _uid in pending:
                        entity[inc] = None
                    return

                user_map: Dict[str, Any] = {}
                for user_obj in users or []:
                    uid = (
                        user_obj.get("id")
                        if isinstance(user_obj, dict)
                        else getattr(user_obj, "id", None)
                    )
                    if uid is not None:
                        user_map[str(uid)] = serialize_for_response(user_obj)

                for entity, inc, uid in pending:
                    entity[inc] = user_map.get(uid)

            _attach_user_includes_to_items(serialized_items)  # type: ignore[arg-type]

            if include_selection and isinstance(serialized_items, list):
                _populate_reverse_includes(
                    serialized_items,
                    include_selection,
                    target_model,
                    model_registry,
                    getattr(getattr(manager, "requester", None), "id", None),
                )

            fields_selection = _normalize_projection_values(query_params.fields)

            # Item 45 — apply field-level ACL across the list response.
            # Shared cache so the per-row cost is one dictionary lookup
            # rather than re-evaluating every restricted-field permission.
            from zephyrex.lib.FieldACL import FieldACLCache

            _acl_cache = FieldACLCache()
            if isinstance(serialized_items, list):
                serialized_items = [
                    apply_field_acl_to_payload(
                        item, manager, target_model, cache=_acl_cache
                    )
                    for item in serialized_items
                ]

            if fields_selection:
                try:
                    logger.debug(
                        f"LIST projection: fields={fields_selection}, include={include_selection}, sample_keys={(list(serialized_items[0].keys()) if isinstance(serialized_items, list) and serialized_items else [])}"
                    )
                except Exception:
                    pass
                projected_items = [
                    _apply_field_projection_to_entity(
                        item, fields_selection, include_selection
                    )
                    for item in serialized_items or []
                ]
                return JSONResponse(
                    content=jsonable_encoder(
                        {
                            resource_name_plural: projected_items,
                            "pagination": _pagination_meta,
                        }
                    ),
                    status_code=status.HTTP_200_OK,
                )

            if include_selection:
                populated_items = _populate_includes_on_serialized(
                    serialized_results, include_selection, model_registry, requester_id=getattr(getattr(manager, "requester", None), "id", None), model_class=target_model  # type: ignore[arg-type]
                )
                if isinstance(populated_items, list):
                    populated_items = [
                        apply_field_acl_to_payload(
                            item, manager, target_model, cache=_acl_cache
                        )
                        for item in populated_items
                    ]
                return JSONResponse(
                    content=jsonable_encoder(
                        {
                            resource_name_plural: populated_items,
                            "pagination": _pagination_meta,
                        }
                    ),
                    status_code=status.HTTP_200_OK,
                )

            if _resolve_has_permission(manager) is not None:
                return JSONResponse(
                    content=jsonable_encoder(
                        {
                            resource_name_plural: serialized_items,
                            "pagination": _pagination_meta,
                        }
                    ),
                    status_code=status.HTTP_200_OK,
                )
            return JSONResponse(
                content=jsonable_encoder(
                    {
                        **response_model_instance.model_dump(),
                        "pagination": _pagination_meta,
                    }
                ),
                status_code=status.HTTP_200_OK,
            )
        except Exception as err:
            handle_resource_operation_error(err)


def _build_create_route(
    router: Any,
    examples: Any,
    parent_param_name: Any,
    manager_property: Any,
    resource_name: Any,
    resource_name_plural: Any,
    network_model: Any,
    manager_factory: Any,
    dependencies: Any,
    parent_name: Any,
    get_manager: Any,
    degradation_responses: Any,
    _mutation_responses: Any,
) -> None:
    """Register the CREATE route (extracted from register_route, #226)."""
    path = ""
    summary = f"Create {resource_name}" + (f" for {parent_name}" if parent_name else "")

    # Prepare responses with examples — advertise all negotiable formats
    responses = {
        201: {"content": _multiformat_response_content(examples.get("create"))},
        **_mutation_responses,
    }
    responses.update(degradation_responses)

    @router.post(
        path,
        summary=summary,
        response_model=Union[
            network_model.ResponseSingle, network_model.ResponsePlural
        ],
        status_code=status.HTTP_201_CREATED,
        dependencies=dependencies,
        responses=responses,
        openapi_extra=_multiformat_request_body_extra(),
    )
    async def create_resource(
        response: Response,
        request: Dict = Depends(get_request_info),
        body: Dict = Body(...),
        manager=Depends(manager_factory),
    ):
        try:
            # Extract the actual data from the keyed structure
            if resource_name_plural in body:
                # Handle batch creation
                items_data = body.get(resource_name_plural)
                if not isinstance(items_data, list):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"Format mismatch: plural key '{resource_name_plural}' must contain array data",
                    )
                items = []
                for item in items_data:
                    item_data = item.dict() if hasattr(item, "dict") else item
                    if parent_param_name and request:
                        item_data[parent_param_name] = request["path_params"][
                            parent_param_name
                        ]
                    actual_manager: Any = get_manager(manager, manager_property)
                    created = actual_manager.create(**item_data)
                    if (resp := _render_degradation_sentinel(created)) is not None:
                        return resp
                    items.append(created)
                return network_model.ResponsePlural(
                    **{resource_name_plural: serialize_for_response(items)}
                )
            else:
                # Handle single creation
                post_data = extract_body_data(body, resource_name, resource_name_plural)
                item_data = (
                    post_data.dict() if hasattr(post_data, "dict") else post_data
                )
                if parent_param_name and request:
                    item_data[parent_param_name] = request["path_params"][
                        parent_param_name
                    ]
                created_instance = get_manager(manager, manager_property).create(  # type: ignore[arg-type]
                    **item_data
                )
                if (resp := _render_degradation_sentinel(created_instance)) is not None:
                    return resp
                logger.debug(f"Type of created_instance: {type(created_instance)}")
                logger.debug(
                    f"Created instance dict: {created_instance.model_dump() if hasattr(created_instance, 'model_dump') else created_instance}"
                )

                # Debug the ResponseSingle structure
                logger.debug(
                    f"ResponseSingle model fields: {network_model.ResponseSingle.model_fields}"
                )
                logger.debug(f"resource_name: {resource_name}")

                # Try passing the dict instead of the instance
                created_dict = (
                    created_instance.model_dump()
                    if hasattr(created_instance, "model_dump")
                    else created_instance
                )

                # Check what fields ResponseSingle expects
                expected_fields = list(network_model.ResponseSingle.model_fields.keys())
                logger.debug(f"ResponseSingle expects fields: {expected_fields}")

                # Try to construct the payload based on expected fields
                if "base" in expected_fields:
                    payload = {"base": created_dict}
                else:
                    payload = {resource_name: created_dict}

                logger.debug(f"Payload to ResponseSingle: {payload}")
                toReturn = network_model.ResponseSingle(**payload)
                response.headers.update(etag_headers(created_dict))
                logger.debug(f"ResponseSingle type: {type(toReturn)}")
                logger.debug(
                    f"ResponseSingle dict: {toReturn.model_dump() if hasattr(toReturn, 'model_dump') else toReturn}"
                )
                return toReturn
        except Exception as err:
            handle_resource_operation_error(err)


def _build_update_route(
    router: Any,
    model_registry: Any,
    examples: Any,
    manager_property: Any,
    resource_name: Any,
    resource_name_plural: Any,
    network_model: Any,
    manager_factory: Any,
    dependencies: Any,
    parent_name: Any,
    get_manager: Any,
    degradation_responses: Any,
    _mutation_responses: Any,
    target_model: Any,
) -> None:
    """Register the UPDATE route (extracted from register_route, #226)."""
    path = "/{id}"
    summary = f"Update {resource_name}" + (f" for {parent_name}" if parent_name else "")

    # Prepare responses with examples — advertise all negotiable formats
    responses = {
        200: {"content": _multiformat_response_content(examples.get("update"))},
        **_mutation_responses,
    }
    responses.update(degradation_responses)

    @router.put(
        path,
        summary=summary,
        response_model=network_model.ResponseSingle,
        status_code=status.HTTP_200_OK,
        dependencies=dependencies,
        responses=responses,
        openapi_extra=_multiformat_request_body_extra(),
    )
    async def update_resource(
        response: Response,
        request: Dict = Depends(get_request_info),
        id: str = Path(..., description=f"{stringcase.titlecase(resource_name)} ID"),
        # see get_resource() above: dynamic attr-as-annotation, live at runtime
        body: network_model.PUT = Body(...),
        if_match: Optional[str] = _IF_MATCH,
        manager=Depends(manager_factory),
    ):
        try:
            update_data = extract_body_data(body, resource_name, resource_name_plural)

            # The update runs as the caller: a record the caller cannot see is
            # a 404 for them, never retried with elevated privileges.
            actual_manager = get_manager(manager, manager_property)
            with expect_versions(actual_manager, {id: if_match}):
                update_result = actual_manager.update(id, **update_data)  # type: ignore[arg-type]
            if (resp := _render_degradation_sentinel(update_result)) is not None:
                return resp
            serialized_update = serialize_for_response(update_result)
            version_headers = etag_headers(serialized_update)
            response.headers.update(version_headers)

            # Honor projection/includes requested in the PUT body (body may have
            # top-level 'fields' and/or 'include'). If the caller asked for
            # 'user_id' for invitations and it's missing, synthesize it from
            # created_by_user_id so consumers see an inviter value.
            body_includes = getattr(body, "include", None)
            body_fields = getattr(body, "fields", None)
            include_selection = _normalize_projection_values(body_includes)
            fields_selection = _normalize_projection_values(body_fields)

            # If include/fields requested, prefer returning the canonical post-update
            # representation from manager.get to ensure any DB hooks or joins are applied.
            if include_selection or fields_selection:
                try:
                    fresh = get_manager(manager, manager_property).get(
                        id=id, include=include_selection, fields=fields_selection
                    )
                    serialized_fresh = serialize_for_response(fresh)
                except HTTPException as he:
                    # If the manager.get unexpectedly returns 404 for the
                    # just-updated resource (permissions / visibility differences),
                    # fall back to using the serialized update result so we
                    # still return a 200 PUT response with projected fields.
                    if he.status_code == status.HTTP_404_NOT_FOUND:
                        logger.debug(
                            f"PUT projection: manager.get returned 404 for {resource_name} id={id}; falling back to serialized update"
                        )
                        serialized_fresh = (
                            serialized_update if serialized_update is not None else {}
                        )
                    else:
                        raise
                except Exception:
                    # Best-effort fallback to avoid turning a successful update
                    # into a 500 due to projection lookups.
                    serialized_fresh = (
                        serialized_update if serialized_update is not None else {}
                    )

                if fields_selection:
                    projected = _apply_field_projection_to_entity(
                        serialized_fresh, fields_selection, include_selection
                    )

                    # Generic fill: if projection returned None for requested
                    # top-level fields, re-fetch the full canonical entity and
                    # copy any non-null values for those fields back into the
                    # projected response. This covers cases like team.image_url
                    # where the manager.get called with a restricted fields set
                    # may not have provided the value.
                    try:
                        if isinstance(projected, dict):
                            missing = [
                                f for f in fields_selection if projected.get(f) is None
                            ]
                            if missing:
                                try:
                                    full = get_manager(manager, manager_property).get(
                                        id=id, include=None, fields=None
                                    )
                                    full_serialized = serialize_for_response(full)
                                    if isinstance(full_serialized, dict):
                                        for mf in missing:
                                            if (
                                                mf in full_serialized
                                                and full_serialized.get(mf) is not None
                                            ):
                                                projected[mf] = full_serialized.get(mf)
                                except Exception:
                                    # best-effort; continue to resource-specific fallbacks
                                    pass
                    except Exception:
                        pass

                    return JSONResponse(
                        content=jsonable_encoder({resource_name: projected}),
                        status_code=status.HTTP_200_OK,
                        headers=version_headers,
                    )

                if include_selection:
                    populated = _populate_includes_on_serialized(
                        serialized_fresh, include_selection, model_registry, requester_id=getattr(getattr(manager, "requester", None), "id", None), model_class=target_model  # type: ignore[arg-type]
                    )
                    return JSONResponse(
                        content=jsonable_encoder({resource_name: populated}),
                        status_code=status.HTTP_200_OK,
                        headers=version_headers,
                    )

                return network_model.ResponseSingle(**{resource_name: serialized_fresh})

            # Otherwise return the serialized update result
            return network_model.ResponseSingle(**{resource_name: serialized_update})
        except Exception as err:
            handle_resource_operation_error(err)


def _build_delete_route(
    router: Any,
    manager_property: Any,
    resource_name: Any,
    manager_factory: Any,
    dependencies: Any,
    parent_name: Any,
    get_manager: Any,
) -> None:
    """Register the DELETE route (extracted from register_route, #226)."""
    path = "/{id}"
    summary = f"Delete {resource_name}" + (f" for {parent_name}" if parent_name else "")

    @router.delete(
        path,
        summary=summary,
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=dependencies,
    )
    async def delete_resource(
        id: str = Path(..., description=f"{stringcase.titlecase(resource_name)} ID"),
        if_match: Optional[str] = _IF_MATCH,
        manager=Depends(manager_factory),
    ):
        try:
            actual_manager: Any = get_manager(manager, manager_property)
            with expect_versions(actual_manager, {id: if_match}):
                actual_manager.delete(id=id)
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except Exception as err:
            handle_resource_operation_error(err)


def _build_search_route(
    router: Any,
    model_registry: Any,
    examples: Any,
    parent_param_name: Any,
    manager_property: Any,
    resource_name: Any,
    resource_name_plural: Any,
    network_model: Any,
    target_model: Any,
    manager_factory: Any,
    dependencies: Any,
    parent_name: Any,
    get_manager: Any,
    _list_responses: Any,
) -> None:
    """Register the SEARCH route (extracted from register_route, #226)."""
    path = "/search"
    summary = f"Search {resource_name_plural}" + (
        f" for {parent_name}" if parent_name else ""
    )

    # Prepare responses with examples — advertise all negotiable formats
    responses = {
        200: {"content": _multiformat_response_content(examples.get("search"))},
        **_list_responses,
    }

    @router.post(
        path,
        summary=summary,
        response_model=network_model.ResponsePlural,
        status_code=status.HTTP_200_OK,
        dependencies=dependencies,
        responses=responses,
        openapi_extra=_multiformat_request_body_extra(),
    )
    async def search_resources(
        request: Dict = Depends(get_request_info),
        # see get_resource() above: dynamic attr-as-annotation, live at runtime
        criteria: network_model.SEARCH = Body(...),
        manager=Depends(manager_factory),
        include: Optional[Union[List[str], str]] = Query(None),
        fields: Optional[Union[List[str], str]] = Query(None),
        limit: Optional[int] = Query(None),
        offset: Optional[int] = Query(None),
        page: Optional[int] = Query(None),
        page_size: Optional[int] = Query(None, alias="pageSize"),
        sort_by: Optional[str] = Query(None),
        sort_order: Optional[str] = Query(None),
    ):
        try:
            search_data = extract_body_data(
                criteria, resource_name, resource_name_plural
            )
            if parent_param_name and request:
                search_data[parent_param_name] = request["path_params"][
                    parent_param_name
                ]

            # actual_manager: Any = get_manager(manager, manager_property)
            # results = actual_manager.search(
            #     include=getattr(criteria, "include", None),
            #     fields=getattr(criteria, "fields", None),
            #     offset=getattr(criteria, "offset", 0) or 0,
            #     limit=getattr(criteria, "limit", 100) or 100,
            #     sort_by=getattr(criteria, "sort_by", None),
            #     sort_order=getattr(criteria, "sort_order", "asc") or "asc",
            #     **search_data,
            # )

            actual_include = (
                include if include is not None else getattr(criteria, "include", None)
            )
            actual_fields = (
                fields if fields is not None else getattr(criteria, "fields", None)
            )

            # Normalize include/fields to lists if strings provided
            actual_include = _normalize_query_list(actual_include)
            actual_fields = _normalize_query_list(actual_fields)
            actual_limit = (
                limit if limit is not None else getattr(criteria, "limit", None)
            )
            if actual_limit is None:
                actual_limit = 100

            actual_offset = (
                offset if offset is not None else getattr(criteria, "offset", None)
            )
            if actual_offset is None:
                actual_offset = 0

            actual_page = page if page is not None else getattr(criteria, "page", None)
            actual_page_size = (
                page_size
                if page_size is not None
                else getattr(criteria, "page_size", getattr(criteria, "pageSize", None))
            )

            actual_sort_by = (
                sort_by if sort_by is not None else getattr(criteria, "sort_by", None)
            )
            actual_sort_order = (
                sort_order
                if sort_order is not None
                else getattr(criteria, "sort_order", None)
            )
            if not actual_sort_order:
                actual_sort_order = "asc"

            # Validate sort_by field if provided
            if actual_sort_by:
                valid_fields = set(target_model.model_fields.keys())
                if actual_sort_by not in valid_fields:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": f"Invalid field for sort_by: {actual_sort_by}",
                            "invalid_fields": [actual_sort_by],
                            "valid_fields": sorted(list(valid_fields)),
                        },
                    )

            # Validate sort_order if provided
            if actual_sort_order and actual_sort_order.lower() not in (
                "asc",
                "desc",
            ):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": f"Invalid sort_order: {actual_sort_order}. Must match pattern ^(asc|desc)$",
                        "validation_error": f"sort_order must be 'asc' or 'desc', got '{actual_sort_order}'",
                    },
                )

            # Validate includes against model relationships
            actual_manager = get_manager(manager, manager_property)
            registry = getattr(actual_manager, "model_registry", None)
            _validate_includes(actual_include, target_model, resource_name, registry)

            _search_offset = actual_offset
            _search_limit = actual_limit
            if actual_page is not None and actual_page_size is not None:
                _search_limit = actual_page_size
                _search_offset = (actual_page - 1) * actual_page_size

            search_results = actual_manager.search(
                include=actual_include,
                fields=actual_fields,
                offset=actual_offset,
                limit=actual_limit,
                sort_by=actual_sort_by,
                sort_order=actual_sort_order,
                page=actual_page,
                pageSize=actual_page_size,
                **(search_data if isinstance(search_data, dict) else {}),
            )

            _search_result_count = (
                len(search_results) if isinstance(search_results, list) else 0
            )
            try:
                _search_total = actual_manager.count(
                    **(search_data if isinstance(search_data, dict) else {})
                )
            except (AttributeError, TypeError):
                _search_total = _search_result_count
            _search_pagination_meta = {
                "offset": _search_offset,
                "limit": _search_limit,
                "total": _search_total,
                "has_more": (_search_offset + _search_result_count) < _search_total,
            }

            # Serialize search results before building response model
            serialized_search_results = serialize_for_response(search_results)
            response_model_instance = network_model.ResponsePlural(
                **{resource_name_plural: serialized_search_results}
            )

            fields_selection = _normalize_projection_values(actual_fields)
            include_selection = _normalize_projection_values(actual_include)

            if fields_selection:
                serialized_items = serialize_for_response(
                    getattr(response_model_instance, resource_name_plural)
                )
                projected_items = [
                    _apply_field_projection_to_entity(
                        item, fields_selection, include_selection
                    )
                    for item in serialized_items or []
                ]
                return JSONResponse(
                    content=jsonable_encoder(
                        {
                            resource_name_plural: projected_items,
                            "pagination": _search_pagination_meta,
                        }
                    ),
                    status_code=status.HTTP_200_OK,
                )

            if include_selection:
                populated_items = _populate_includes_on_serialized(
                    serialized_search_results, include_selection, model_registry, requester_id=getattr(getattr(manager, "requester", None), "id", None), model_class=target_model  # type: ignore[arg-type]
                )
                return JSONResponse(
                    content=jsonable_encoder(
                        {
                            resource_name_plural: populated_items,
                            "pagination": _search_pagination_meta,
                        }
                    ),
                    status_code=status.HTTP_200_OK,
                )

            return JSONResponse(
                content=jsonable_encoder(
                    {
                        **response_model_instance.model_dump(),
                        "pagination": _search_pagination_meta,
                    }
                ),
                status_code=status.HTTP_200_OK,
            )
        except Exception as err:
            handle_resource_operation_error(err)


def _build_batch_update_route(
    router: Any,
    examples: Any,
    manager_property: Any,
    resource_name: Any,
    resource_name_plural: Any,
    network_model: Any,
    manager_factory: Any,
    dependencies: Any,
    get_manager: Any,
    _mutation_responses: Any,
) -> None:
    """Register the BATCH_UPDATE route (extracted from register_route, #226)."""
    path = ""
    summary = f"Batch update {resource_name_plural}"

    # Create dynamic batch update model: each target is an id, or an id with
    # the version (ETag) the client read it at.
    BatchUpdateModel = create_model(  # type: ignore[call-overload]
        f"{stringcase.capitalcase(resource_name)}BatchUpdateModel",
        **{
            resource_name: (Dict[str, Any], ...),
            "target_ids": (List[Union[str, BatchTarget]], ...),
        },
    )

    # Prepare responses with examples — advertise all negotiable formats
    responses = {
        200: {"content": _multiformat_response_content(examples.get("batch_update"))},
        **_mutation_responses,
    }

    @router.put(
        path,
        summary=summary,
        response_model=network_model.ResponsePlural,
        status_code=status.HTTP_200_OK,
        dependencies=dependencies,
        responses=responses,
        openapi_extra=_multiformat_request_body_extra(),
    )
    async def batch_update_resources(
        body: BatchUpdateModel = Body(...),
        manager=Depends(manager_factory),
    ):
        try:
            update_data = getattr(body, resource_name)
            targets = [
                target if isinstance(target, BatchTarget) else BatchTarget(id=target)
                for target in body.target_ids  # type: ignore[attr-defined]
            ]

            items = [{"id": target.id, "data": update_data} for target in targets]

            actual_manager: Any = get_manager(manager, manager_property)
            try:
                with expect_versions(
                    actual_manager,
                    {target.id: target.if_match for target in targets},
                ):
                    updated_items = actual_manager.batch_update(items=items)
            except HTTPException as batch_err:
                if batch_err.status_code == 207:
                    return JSONResponse(
                        status_code=207,
                        content=jsonable_encoder(batch_err.detail),
                    )
                raise

            return network_model.ResponsePlural(
                **{resource_name_plural: serialize_for_response(updated_items)}
            )
        except Exception as err:
            handle_resource_operation_error(err)


def _build_batch_delete_route(
    router: Any,
    manager_property: Any,
    resource_name_plural: Any,
    manager_factory: Any,
    dependencies: Any,
    get_manager: Any,
) -> None:
    """Register the BATCH_DELETE route (extracted from register_route, #226)."""
    path = ""
    summary = f"Batch delete {resource_name_plural}"

    @router.delete(
        path,
        summary=summary,
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=dependencies,
    )
    async def batch_delete_resources(
        target_ids: str = Query(
            ..., description=f"Comma-separated list of {resource_name_plural} IDs"
        ),
        if_match: Optional[str] = _BATCH_IF_MATCH,
        manager=Depends(manager_factory),
    ):
        try:
            ids_list = [id.strip() for id in target_ids.split(",") if id.strip()]
            if not ids_list:
                raise HTTPException(
                    status_code=400,
                    detail="No valid IDs provided in target_ids parameter",
                )

            actual_manager: Any = get_manager(manager, manager_property)
            try:
                with expect_versions(
                    actual_manager,
                    {entity_id: if_match for entity_id in ids_list},
                ):
                    actual_manager.batch_delete(ids=ids_list)
            except HTTPException as batch_err:
                if batch_err.status_code == 207:
                    return JSONResponse(
                        status_code=207,
                        content=jsonable_encoder(batch_err.detail),
                    )
                raise
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except Exception as err:
            handle_resource_operation_error(err)


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
# Comma-separated query parameters forwarded to custom-route methods that
# declare them: ``?include=user,role&fields=id,name``.
_LIST_QUERY_PARAMS = ("fields", "include")


async def _json_body(request: Request) -> Any:
    """The decoded JSON body. An absent body is an empty object: the method
    decides whether it needs fields (registration can use Basic auth)."""
    raw_body = await request.body()
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")


def _list_query_args(request: Request, signature: Any) -> Dict[str, List[str]]:
    args: Dict[str, List[str]] = {}
    for name in _LIST_QUERY_PARAMS:
        if name not in signature.parameters:
            continue
        values = [
            value.strip()
            for value in (request.query_params.get(name) or "").split(",")
            if value.strip()
        ]
        if values:
            args[name] = values
    return args


def _declared_request_args(
    request: Request, response: Response, signature: Any
) -> Dict[str, Any]:
    """What a custom-route method may ask for by parameter name besides its
    path and body: list query params, and the ``response`` it can set
    cookies or headers on."""
    args: Dict[str, Any] = dict(_list_query_args(request, signature))
    if "response" in signature.parameters:
        args["response"] = response
    return args


def register_custom_route(
    router: APIRouter,
    custom_route: CustomRouteConfig,
    manager_factory: Callable,
    manager_class: Type["ManagerContract"],
) -> None:
    """Register a custom route on the router."""
    import inspect

    # Get method from manager class
    method: Optional[Callable] = getattr(manager_class, custom_route.function, None)
    if not method:
        logger.warning(
            f"Custom route method {custom_route.function} not found on {manager_class.__name__}"
        )
        return

    # Determine if this is a static method
    is_static: bool = custom_route.is_static

    # Get auth dependency
    auth_dependency: Optional[Any] = None
    if not is_static and custom_route.auth_type:
        auth_dependency = get_auth_dependency(custom_route.auth_type)
    elif not is_static:
        # Use default auth from router if not specified
        auth_dependency = get_auth_dependency(AuthType.JWT)

    dependencies: Optional[List[Any]] = [auth_dependency] if auth_dependency else None

    # Create endpoint function
    if is_static:

        async def endpoint(request: Request, response: Response):
            model_registry = getattr(request.app.state, "model_registry", None)
            if not model_registry:
                raise HTTPException(
                    status_code=500, detail="Model registry not available"
                )

            # Build method arguments
            sig = inspect.signature(method)
            method_args = {}

            if "model_registry" in sig.parameters:
                method_args["model_registry"] = model_registry

            if "authorization" in sig.parameters:
                method_args["authorization"] = request.headers.get(
                    "authorization"
                ) or request.headers.get("Authorization")

            if "ip_address" in sig.parameters:
                # Honour X-Forwarded-For ONLY when the upstream peer is a
                # configured trusted proxy. Without this guard a remote
                # client can spoof their IP for audit-log and rate-limit
                # purposes simply by setting the header.
                from zephyrex.lib.Environment import env

                trusted = [
                    p.strip()
                    for p in (env("TRUSTED_PROXIES") or "").split(",")
                    if p.strip()
                ]
                peer_host = request.client.host if request.client else None
                if trusted and peer_host in trusted:
                    forwarded = request.headers.get("X-Forwarded-For")
                    method_args["ip_address"] = (
                        forwarded.split(",")[0].strip() if forwarded else peer_host
                    )
                else:
                    method_args["ip_address"] = peer_host

            if "req_uri" in sig.parameters:
                method_args["req_uri"] = request.headers.get("Referer")

            if "cls" in sig.parameters and "cls" not in method_args:
                method_args["cls"] = manager_class

            method_args.update(_declared_request_args(request, response, sig))

            # Handle request body for POST/PUT/PATCH
            if request.method in _BODY_METHODS:
                body = await _json_body(request)
                if not isinstance(body, dict):
                    raise HTTPException(
                        status_code=422,
                        detail="Request body must be a JSON object",
                    )

                # Map body to expected parameters
                if "registration_data" in sig.parameters:
                    method_args["registration_data"] = body.get("user", body)
                elif "login_data" in sig.parameters:
                    method_args["login_data"] = body.get("auth", body)
                elif "body" in sig.parameters:
                    method_args["body"] = body
                else:
                    method_args.update(body)

            # Add path parameters
            method_args.update(dict(request.path_params))

            # Call the static method
            result = method(**method_args)

            if (resp := _render_degradation_sentinel(result)) is not None:
                return resp

            # Wrap result if needed
            if custom_route.response_model and isinstance(
                custom_route.response_model, str
            ):
                if "ResponseSingle" in custom_route.response_model:
                    return {manager_resource_name(manager_class): result}

            return result

    else:

        async def endpoint(request: Request, response: Response):
            request_info = await get_request_info(request)
            manager = manager_factory(request=request_info)
            method_func: Callable = getattr(manager, custom_route.function)

            method_args = {
                **request.path_params,
                **_declared_request_args(
                    request, response, inspect.signature(method_func)
                ),
            }

            if request.method in _BODY_METHODS:
                method_args["body"] = await _json_body(request)
            with expect_route_version(
                manager,
                request.method,
                request.path_params,
                request.headers.get(IF_MATCH_HEADER),
            ):
                result = method_func(**method_args)

            if (resp := _render_degradation_sentinel(result)) is not None:
                return resp

            # Wrap result if needed (same logic as static routes)
            if custom_route.response_model and isinstance(
                custom_route.response_model, str
            ):
                if "ResponseSingle" in custom_route.response_model:
                    # One record, as GET /v1/user answers: its version is
                    # the ETag, as a generic single-record answer's is.
                    response.headers.update(etag_headers(result))
                    return {manager_resource_name(manager_class): result}

            return result

    # The endpoint serves the manager method; its @rate_limit policy must
    # be visible on the endpoint for route discovery to enforce it.
    carry_rate_limit(method, endpoint)

    # Register the route
    method_value = custom_route.method.value
    route_method: Callable = getattr(router, method_value.lower())
    route_method(
        custom_route.path,
        summary=custom_route.summary or f"Custom {method_value} route",
        description=custom_route.description or "",
        status_code=custom_route.status_code,
        dependencies=dependencies,
    )(endpoint)
