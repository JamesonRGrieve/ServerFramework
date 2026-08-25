# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the language-agnostic SDK IR (sdk/SDKModel.py, issue #217)."""

from zephyrex.pydantic2.fastapi import RouterMixin
from zephyrex.sdk.SDKModel import (
    STANDARD_OPERATIONS,
    ResourceDescriptor,
    extract_resources,
    resolve_body_key,
)


class SdkModelWidgetManager(RouterMixin):
    version = "v1"


def _resource():
    return extract_resources([SdkModelWidgetManager])[0]


def test_extract_resources_from_manager_list():
    resources = extract_resources([SdkModelWidgetManager])
    assert len(resources) == 1
    r = resources[0]
    assert isinstance(r, ResourceDescriptor)
    assert r.name == "sdk_model_widget"
    assert r.name_plural == "sdk_model_widgets"
    assert r.endpoint == "/v1/sdk_model_widget"
    assert r.version == "v1"
    # The resource carries the full standard operation set.
    assert r.operations is STANDARD_OPERATIONS


def test_standard_operations_match_rest_contract():
    by_name = {op.name: op for op in STANDARD_OPERATIONS}
    assert by_name["create"].http_method == "POST"
    assert by_name["get"].http_method == "GET" and by_name["get"].needs_id
    assert by_name["list"].returns_list and by_name["list"].path_suffix == ""
    assert by_name["update"].http_method == "PUT" and by_name["update"].needs_id
    assert by_name["delete"].http_method == "DELETE" and by_name["delete"].needs_id
    assert by_name["search"].path_suffix == "/search" and by_name["search"].has_query
    # All nine operations are present.
    assert set(by_name) == {
        "create",
        "get",
        "list",
        "update",
        "delete",
        "search",
        "batch_create",
        "batch_update",
        "batch_delete",
    }


def test_batch_body_keys_match_abstract_sdk_handler():
    """The batch request bodies must match sdk/AbstractSDKHandler byte-for-byte."""
    r = _resource()
    by_name = {op.name: op for op in STANDARD_OPERATIONS}

    # batch_create -> {"sdk_model_widgets": items}
    bc = by_name["batch_create"]
    assert [resolve_body_key(f, r) for f in bc.body] == ["sdk_model_widgets"]

    # batch_update -> {"sdk_model_widget": updates, "target_ids": ids}
    bu = by_name["batch_update"]
    assert [resolve_body_key(f, r) for f in bu.body] == [
        "sdk_model_widget",
        "target_ids",
    ]

    # batch_delete -> {"target_ids": ids}
    bd = by_name["batch_delete"]
    assert [resolve_body_key(f, r) for f in bd.body] == ["target_ids"]

    # create/update carry a raw (unwrapped) body.
    assert resolve_body_key(by_name["create"].body[0], r) is None
    assert resolve_body_key(by_name["update"].body[0], r) is None


def test_extraction_is_deterministic():
    a = extract_resources([SdkModelWidgetManager])
    b = extract_resources([SdkModelWidgetManager])
    assert a == b


def test_empty_registry_yields_no_resources():
    assert extract_resources(None) == []
    assert extract_resources([]) == []
