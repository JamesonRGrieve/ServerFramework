from typing import Literal

from pydantic import ValidationError

# Compatibility patch for Pydantic 2.x ValidationError.from_exception_data
#
# This probes the installed Pydantic's tolerance for the older/looser
# `line_errors` shape (a `msg` key instead of the newer strict `input` key)
# by deliberately calling it with data that does NOT conform to the current
# `InitErrorDetails` TypedDict -- that mismatch is the point of the probe,
# not a bug to fix. If the installed Pydantic rejects it (TypeError), a
# compat shim classmethod is monkey-patched onto the class itself. Both the
# probe and the monkey-patch are dynamic-metaprogramming patterns that
# can't be expressed in Pydantic's own (C-extension-backed) stubs.
try:
    ValidationError.from_exception_data(
        "pydantic_compat_test",
        [{"type": "value_error", "loc": ("field",), "msg": "compat"}],  # type: ignore[typeddict-item, typeddict-unknown-key]
    )
except TypeError:
    _original_from_exception_data = ValidationError.from_exception_data

    def _compat_from_exception_data(
        cls,
        title,
        line_errors,
        input_type: Literal["python", "json"] = "python",
        hide_input: bool = False,
    ):
        normalized_errors = []
        for error in line_errors:
            ctx = dict(error.get("ctx", {}))
            if "error" not in ctx:
                ctx["error"] = ValueError(error.get("msg") or title)
            normalized_errors.append({**error, "ctx": ctx})
        return _original_from_exception_data(
            title,
            normalized_errors,  # type: ignore[arg-type]
            input_type=input_type,
            hide_input=hide_input,
        )

    ValidationError.from_exception_data = classmethod(_compat_from_exception_data)  # type: ignore[method-assign, assignment]

from .types import (
    T as T,
    AuthType as AuthType,
    RouteType as RouteType,
    HTTPMethod as HTTPMethod,
    CustomRouteConfig as CustomRouteConfig,
    NestedResourceConfig as NestedResourceConfig,
    static_route as static_route,
    RouterMixin as RouterMixin,
)
from .query import (
    _NEGOTIABLE_CONTENT_TYPES as _NEGOTIABLE_CONTENT_TYPES,
    _multiformat_response_content as _multiformat_response_content,
    _multiformat_request_body_extra as _multiformat_request_body_extra,
    RequestInfo as RequestInfo,
    get_request_info as get_request_info,
    _normalize_query_key as _normalize_query_key,
    _type_accepts_list as _type_accepts_list,
    _type_accepts_str as _type_accepts_str,
    _coerce_sequence_values as _coerce_sequence_values,
    _normalize_projection_values as _normalize_projection_values,
    _extract_projection_roots as _extract_projection_roots,
    _apply_field_projection_to_entity as _apply_field_projection_to_entity,
    _get_valid_includes_for_model as _get_valid_includes_for_model,
    _validate_includes as _validate_includes,
    create_query_model_dependency as create_query_model_dependency,
    _render_degradation_sentinel as _render_degradation_sentinel,
    _degradation_responses_annotation as _degradation_responses_annotation,
    _normalize_query_list as _normalize_query_list,
)
from .examples import ExampleGenerator as ExampleGenerator
from .resource import (
    get_auth_dependency as get_auth_dependency,
    extract_body_data as extract_body_data,
    serialize_for_response as serialize_for_response,
    _resolve_has_permission as _resolve_has_permission,
    apply_field_acl_to_payload as apply_field_acl_to_payload,
    validate_field_acl_query as validate_field_acl_query,
    _populate_includes_on_serialized as _populate_includes_on_serialized,
    create_manager_factory as create_manager_factory,
    _build_links as _build_links,
    _error_envelope as _error_envelope,
    handle_resource_operation_error as handle_resource_operation_error,
)
from .routes import (
    register_route as register_route,
    register_custom_route as register_custom_route,
)
from .router import (
    create_router_from_manager as create_router_from_manager,
    generate_routers_from_model_registry as generate_routers_from_model_registry,
)
