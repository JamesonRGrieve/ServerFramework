# SPDX-License-Identifier: AGPL-3.0-or-later
"""The generated REST routes' contracts, driven through a real FastAPI app
over an in-memory manager: what a client may set, what it gets back, and
which records a route may touch."""

from typing import Any, ClassVar, Dict, List, Optional, Type

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from zephyrex.extensions.ExternalErrors import QueuedForRetry
from zephyrex.lib.FieldACL import requires
from zephyrex.logic.AbstractLogicManager import AbstractBLLManager
from zephyrex.pydantic2.fastapi import (
    AuthType,
    HTTPMethod,
    NestedResourceConfig,
    RouterMixin,
    RouteType,
    create_router_from_manager,
    register_custom_route,
    static_route,
)
from zephyrex.pydantic2.fastapi.limits import MAX_BATCH_SIZE, MAX_PAGE_SIZE

# ---------------------------------------------------------------------------
# An in-memory resource: a model, its network envelopes, a manager over a
# dict, and a registry that binds them.
# ---------------------------------------------------------------------------

ALL_ROUTES = [
    RouteType.GET,
    RouteType.LIST,
    RouteType.CREATE,
    RouteType.UPDATE,
    RouteType.DELETE,
    RouteType.SEARCH,
    RouteType.BATCH_UPDATE,
    RouteType.BATCH_DELETE,
]

_PROJECTION = {"include": Optional[List[str]], "fields": Optional[List[str]]}


def _optional_model(name, annotations):
    """A model whose every field is optional and defaults to None."""
    namespace = {"__annotations__": annotations, "__module__": __name__}
    namespace.update({field: None for field in annotations})
    return type(name, (BaseModel,), namespace)


def _network(model, single, plural):
    """The Network envelopes the registry generates for ``model``, keyed by
    ``single``/``plural``."""
    partial = _optional_model(
        f"{model.__name__}Partial",
        {name: Optional[info.annotation] for name, info in model.model_fields.items()},
    )
    envelope = {single: Optional[partial], **_PROJECTION}
    return type(
        "Network",
        (),
        {
            "POST": _optional_model("POST", dict(envelope)),
            "PUT": _optional_model("PUT", dict(envelope)),
            "SEARCH": _optional_model("SEARCH", dict(envelope)),
            "GET": _optional_model("GET", dict(_PROJECTION)),
            "LIST": _optional_model(
                "LIST",
                {
                    **_PROJECTION,
                    "offset": Optional[int],
                    "limit": Optional[int],
                    "sort_by": Optional[str],
                    "sort_order": Optional[str],
                },
            ),
            "ResponseSingle": type(
                "ResponseSingle", (BaseModel,), {"__annotations__": {single: model}}
            ),
            "ResponsePlural": type(
                "ResponsePlural",
                (BaseModel,),
                {"__annotations__": {plural: List[model]}},
            ),
        },
    )


class WidgetModel(BaseModel):
    id: Optional[str] = None
    owner_id: Optional[str] = None
    name: Optional[str] = None

    Manager: ClassVar[Optional[Type[Any]]] = None


setattr(WidgetModel, "Network", _network(WidgetModel, "widget", "widgets"))


class OwnerModel(BaseModel):
    id: Optional[str] = None

    Manager: ClassVar[Optional[Type[Any]]] = None


class _Registry:
    def __init__(self, *models):
        self._models = {model.__name__: model for model in models}

    def apply(self, model):
        return model

    def is_model_bound(self, model):
        return model in self._models.values()

    def action_managers(self):
        return []


def _matches(row, filters):
    return all(
        getattr(row, key, None) == value
        for key, value in filters.items()
        if not isinstance(value, dict) and value is not None
    )


class WidgetManager(AbstractBLLManager, RouterMixin):
    """Widgets in a dict shared by every instance (reset per test)."""

    _model = WidgetModel
    prefix = "/v1/widget"
    auth_type = AuthType.NONE
    routes_to_register = ALL_ROUTES
    rows: ClassVar[Dict[str, Any]] = {}
    created: ClassVar[List[Dict[str, Any]]] = []

    def create(self, **kwargs):
        type(self).created.append(dict(kwargs))
        row = self.Model(id=f"w{len(self.rows) + 1}", **kwargs)
        self.rows[row.id] = row
        return row

    def get(self, include=None, fields=None, **kwargs):
        row = self.rows.get(kwargs.pop("id"))
        return row if row is not None and _matches(row, kwargs) else None

    def list(
        self,
        include=None,
        fields=None,
        sort_by=None,
        sort_order="asc",
        filters=None,
        limit=None,
        offset=None,
        **kwargs,
    ):
        rows = [row for row in self.rows.values() if _matches(row, kwargs)]
        start = offset or 0
        return rows[start : start + (limit or 100)]

    def update(self, id, **kwargs):
        row = self.rows[id]
        for key, value in kwargs.items():
            setattr(row, key, value)
        return row

    def delete(self, id):
        del self.rows[id]

    def search(
        self,
        include=None,
        fields=None,
        offset=0,
        limit=100,
        sort_by=None,
        sort_order="asc",
        page=None,
        pageSize=None,
        **criteria,
    ):
        rows = [row for row in self.rows.values() if _matches(row, criteria)]
        return rows[offset : offset + limit]

    def batch_update(self, items):
        return [self.update(item["id"], **item["data"]) for item in items]

    def batch_delete(self, ids):
        for entity_id in ids:
            self.delete(entity_id)


WidgetModel.Manager = WidgetManager


class OwnerManager(AbstractBLLManager, RouterMixin):
    """Serves only its widgets, nested as ``/v1/owner/{owner_id}/kid``: the
    manager property ("kids") is not the widget's own envelope name."""

    _model = OwnerModel
    prefix = "/v1/owner"
    auth_type = AuthType.NONE
    routes_to_register: ClassVar[Optional[List[RouteType]]] = []
    nested_resources = {
        "kid": NestedResourceConfig(
            child_resource_name="kid",
            manager_property="kids",
            child_manager_class=WidgetManager,
            routes_to_register=ALL_ROUTES,
        )
    }

    @property
    def kids(self):
        return WidgetManager(model_registry=self.model_registry)


@pytest.fixture(autouse=True)
def _empty_store():
    WidgetManager.rows.clear()
    WidgetManager.created.clear()
    yield
    WidgetManager.rows.clear()
    WidgetManager.created.clear()


def _client(*managers):
    registry = _Registry(WidgetModel, OwnerModel)
    app = FastAPI()
    app.state.model_registry = registry
    for manager in managers:
        app.include_router(create_router_from_manager(manager, registry))
    return TestClient(app)


def _seed(**fields):
    row = WidgetModel(id=f"w{len(WidgetManager.rows) + 1}", **fields)
    WidgetManager.rows[row.id] = row
    return row


# ---------------------------------------------------------------------------
# Static custom routes: server-derived arguments win over the body (#272)
# ---------------------------------------------------------------------------


class _EchoArgsManager:
    """A static route that reports the arguments it was called with."""

    @static_route("/echo", method=HTTPMethod.POST, auth_type=AuthType.NONE)
    def echo(cls, model_registry, ip_address, authorization, req_uri, note=None):
        return {
            "cls": cls.__name__,
            "registry": model_registry,
            "ip_address": ip_address,
            "authorization": authorization,
            "req_uri": req_uri,
            "note": note,
        }


def _echo_client():
    router = APIRouter()
    register_custom_route(
        router,
        _EchoArgsManager.echo._static_route_config[0],
        lambda: None,
        _EchoArgsManager,
    )
    app = FastAPI()
    app.state.model_registry = "the-app-registry"
    app.include_router(router)
    return TestClient(app)


def test_static_route_body_cannot_override_server_arguments():
    """A body naming the server-injected arguments is ignored for them: the
    method sees the real peer IP and headers, not the client's claims."""
    spoofed: Dict[str, Any] = {
        "ip_address": "10.0.0.1",
        "authorization": "Bearer forged",
        "req_uri": "https://evil.example",
        "model_registry": "forged-registry",
        "cls": "Forged",
        "note": "kept",
    }
    response = _echo_client().post(
        "/echo",
        json=spoofed,
        headers={"Authorization": "Bearer real", "Referer": "https://app.example"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "cls": "_EchoArgsManager",
        "registry": "the-app-registry",
        "ip_address": "testclient",
        "authorization": "Bearer real",
        "req_uri": "https://app.example",
        # An ordinary body field still reaches the method.
        "note": "kept",
    }


# ---------------------------------------------------------------------------
# Envelopes are keyed by the network model's own name (#267)
# ---------------------------------------------------------------------------


class GadgetManager(WidgetManager):
    """Named for something other than the model it serves: its envelopes
    are still the model's (``widget``), as its generated network model
    declares them."""

    prefix = "/v1/gadget"


def test_a_manager_named_unlike_its_model_uses_the_model_envelope():
    client = _client(GadgetManager)

    created = client.post("/v1/gadget", json={"widget": {"name": "first"}})
    assert created.status_code == 201, created.text
    widget_id = created.json()["widget"]["id"]

    fetched = client.get(f"/v1/gadget/{widget_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["widget"]["name"] == "first"

    listed = client.get("/v1/gadget")
    assert listed.status_code == 200, listed.text
    assert [w["name"] for w in listed.json()["widgets"]] == ["first"]

    updated = client.put(f"/v1/gadget/{widget_id}", json={"widget": {"name": "2nd"}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["widget"]["name"] == "2nd"


def test_a_nested_resource_named_unlike_its_model_uses_the_model_envelope():
    """``/v1/owner/{owner_id}/kid`` serves widgets: the manager property
    ("kids" -> "kid") names the path, the widget model names the envelope."""
    row = _seed(owner_id="o1", name="kid-widget")
    client = _client(OwnerManager)

    fetched = client.get(f"/v1/owner/o1/kid/{row.id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["widget"]["name"] == "kid-widget"

    listed = client.get("/v1/owner/o1/kid")
    assert listed.status_code == 200, listed.text
    assert [w["id"] for w in listed.json()["widgets"]] == [row.id]


# ---------------------------------------------------------------------------
# Nested writes are scoped to the path's parent (#264)
# ---------------------------------------------------------------------------


@pytest.fixture
def two_owners():
    """One widget under owner o1 and one under owner o2."""
    return _seed(owner_id="o1", name="mine"), _seed(owner_id="o2", name="theirs")


def test_nested_update_of_another_parents_child_is_not_found(two_owners):
    _, theirs = two_owners
    response = _client(OwnerManager).put(
        f"/v1/owner/o1/kid/{theirs.id}", json={"widget": {"name": "hijacked"}}
    )

    assert response.status_code == 404, response.text
    assert WidgetManager.rows[theirs.id].name == "theirs"


def test_nested_update_cannot_move_a_child_to_another_parent(two_owners):
    mine, _ = two_owners
    client = _client(OwnerManager)

    moved = client.put(
        f"/v1/owner/o1/kid/{mine.id}", json={"widget": {"owner_id": "o2"}}
    )
    assert moved.status_code == 422, moved.text
    assert WidgetManager.rows[mine.id].owner_id == "o1"

    renamed = client.put(f"/v1/owner/o1/kid/{mine.id}", json={"widget": {"name": "ok"}})
    assert renamed.status_code == 200, renamed.text
    assert WidgetManager.rows[mine.id].name == "ok"


def test_nested_delete_of_another_parents_child_is_not_found(two_owners):
    mine, theirs = two_owners
    client = _client(OwnerManager)

    refused = client.delete(f"/v1/owner/o1/kid/{theirs.id}")
    assert refused.status_code == 404, refused.text
    assert theirs.id in WidgetManager.rows

    deleted = client.delete(f"/v1/owner/o1/kid/{mine.id}")
    assert deleted.status_code == 204, deleted.text
    assert mine.id not in WidgetManager.rows


def test_nested_batch_writes_refuse_another_parents_children(two_owners):
    mine, theirs = two_owners
    client = _client(OwnerManager)

    updated = client.put(
        "/v1/owner/o1/kid",
        json={"widget": {"name": "batched"}, "target_ids": [mine.id, theirs.id]},
    )
    assert updated.status_code == 404, updated.text
    assert theirs.id in updated.text
    assert [WidgetManager.rows[mine.id].name, WidgetManager.rows[theirs.id].name] == [
        "mine",
        "theirs",
    ]

    deleted = client.delete(f"/v1/owner/o1/kid?target_ids={mine.id},{theirs.id}")
    assert deleted.status_code == 404, deleted.text
    assert {mine.id, theirs.id} <= set(WidgetManager.rows)


def test_nested_batch_update_cannot_move_children(two_owners):
    mine, _ = two_owners
    response = _client(OwnerManager).put(
        "/v1/owner/o1/kid",
        json={"widget": {"owner_id": "o2"}, "target_ids": [mine.id]},
    )

    assert response.status_code == 422, response.text
    assert WidgetManager.rows[mine.id].owner_id == "o1"


def test_nested_list_filter_cannot_widen_the_parent_scope(two_owners):
    mine, _ = two_owners
    response = _client(OwnerManager).get("/v1/owner/o1/kid?owner_id__eq=o2")

    assert response.status_code == 200, response.text
    assert [w["id"] for w in response.json()["widgets"]] == [mine.id]


# ---------------------------------------------------------------------------
# LIST operator filters name model fields only (#266)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_filter",
    [
        "limit__eq=5",
        "offset__gt=1",
        "include__eq=x",
        "fields__eq=x",
        "sort_by__eq=name",
        "sort_order__eq=asc",
        "no_such_field__eq=1",
    ],
)
def test_list_refuses_operator_filter_on_a_non_field(raw_filter):
    """A reserved parameter or unknown name with an operator suffix is a
    422, never a clashing keyword argument (500) at the manager."""
    _seed(name="first")
    response = _client(WidgetManager).get(f"/v1/widget?{raw_filter}")

    assert response.status_code == 422, response.text
    assert raw_filter.split("=")[0] in response.text


def test_list_accepts_operator_filter_on_a_model_field():
    _seed(name="first")
    response = _client(WidgetManager).get("/v1/widget?name__eq=first")

    assert response.status_code == 200, response.text
    assert [w["name"] for w in response.json()["widgets"]] == ["first"]


# ---------------------------------------------------------------------------
# SEARCH pages and batch writes are bounded (#270)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        f"limit={MAX_PAGE_SIZE + 1}",
        "limit=0",
        "offset=-5",
        "page=0",
        f"pageSize={MAX_PAGE_SIZE + 1}",
        "pageSize=0",
    ],
)
def test_search_refuses_out_of_bounds_paging(query):
    response = _client(WidgetManager).post(f"/v1/widget/search?{query}", json={})

    assert response.status_code == 422, response.text


def test_search_accepts_the_largest_page():
    _seed(name="first")
    response = _client(WidgetManager).post(
        f"/v1/widget/search?limit={MAX_PAGE_SIZE}&offset=0", json={"widget": {}}
    )

    assert response.status_code == 200, response.text
    assert [w["name"] for w in response.json()["widgets"]] == ["first"]


def test_batch_create_refuses_an_oversized_batch_before_writing():
    items = [{"name": f"w{n}"} for n in range(MAX_BATCH_SIZE + 1)]
    response = _client(WidgetManager).post("/v1/widget", json={"widgets": items})

    assert response.status_code == 422, response.text
    assert WidgetManager.created == []


def test_batch_update_refuses_an_oversized_batch():
    row = _seed(name="kept")
    response = _client(WidgetManager).put(
        "/v1/widget",
        json={"widget": {"name": "x"}, "target_ids": [row.id] * (MAX_BATCH_SIZE + 1)},
    )

    assert response.status_code == 422, response.text
    assert WidgetManager.rows[row.id].name == "kept"


def test_batch_delete_refuses_an_oversized_batch():
    rows = [_seed(name=f"w{n}") for n in range(MAX_BATCH_SIZE + 1)]
    ids = ",".join(row.id for row in rows)
    response = _client(WidgetManager).delete(f"/v1/widget?target_ids={ids}")

    assert response.status_code == 422, response.text
    assert len(WidgetManager.rows) == MAX_BATCH_SIZE + 1


def test_batch_writes_accept_a_full_batch():
    client = _client(WidgetManager)
    items = [{"name": f"w{n}"} for n in range(MAX_BATCH_SIZE)]

    created = client.post("/v1/widget", json={"widgets": items})
    assert created.status_code == 201, created.text
    ids = [w["id"] for w in created.json()["widgets"]]

    deleted = client.delete(f"/v1/widget?target_ids={','.join(ids)}")
    assert deleted.status_code == 204, deleted.text
    assert WidgetManager.rows == {}


# ---------------------------------------------------------------------------
# Batch create reports every item (#269)
# ---------------------------------------------------------------------------


class FlakyWidgetManager(WidgetManager):
    """Queues a widget named "queue" (a QUEUE_AND_RETRY provider's answer)
    and rejects one named "bad"."""

    prefix = "/v1/flaky"

    def create(self, **kwargs):
        if kwargs.get("name") == "queue":
            type(self).created.append(dict(kwargs))
            return QueuedForRetry(tracking_id="t-1")
        if kwargs.get("name") == "bad":
            type(self).created.append(dict(kwargs))
            raise ValueError("bad widget")
        return super().create(**kwargs)


def test_batch_create_attempts_every_item_and_reports_each():
    names = ["a", "queue", "bad", "d"]
    response = _client(FlakyWidgetManager).post(
        "/v1/flaky", json={"widgets": [{"name": name} for name in names]}
    )

    assert response.status_code == 207, response.text
    body = response.json()
    # Nothing after the queued or failed item was skipped.
    assert [attempt["name"] for attempt in WidgetManager.created] == names
    assert [result["outcome"] for result in body["results"]] == [
        "created",
        "queued",
        "error",
        "created",
    ]
    assert [result["index"] for result in body["results"]] == [0, 1, 2, 3]
    assert body["results"][1]["tracking_id"] == "t-1"
    assert body["results"][2]["status_code"] == 422
    assert body["errors"] == [body["results"][2]]
    # The records created before and after are reported, not dropped.
    assert [widget["name"] for widget in body["widgets"]] == ["a", "d"]
    assert [body["results"][0]["id"], body["results"][3]["id"]] == [
        widget["id"] for widget in body["widgets"]
    ]
    assert (body["successful_creates"], body["failed_creates"]) == (2, 1)
    assert sorted(row.name for row in WidgetManager.rows.values()) == ["a", "d"]


def test_batch_create_reports_a_non_object_item():
    response = _client(WidgetManager).post(
        "/v1/widget", json={"widgets": [{"name": "a"}, "not-an-object"]}
    )

    assert response.status_code == 207, response.text
    body = response.json()
    assert [result["outcome"] for result in body["results"]] == ["created", "error"]
    assert body["results"][1]["status_code"] == 422


# ---------------------------------------------------------------------------
# Every response path applies the field ACL (#263)
# ---------------------------------------------------------------------------

SECRET = "s3cret"
READ_SECRET = "widget.read_secret"


class SecretWidgetModel(BaseModel):
    id: Optional[str] = None
    owner_id: Optional[str] = None
    name: Optional[str] = None
    secret: Optional[str] = Field(None, json_schema_extra=requires(READ_SECRET))

    Manager: ClassVar[Optional[Type[Any]]] = None


setattr(
    SecretWidgetModel,
    "Network",
    _network(SecretWidgetModel, "secret_widget", "secret_widgets"),
)


class _Requester:
    id = "requester-1"

    def __init__(self, granted):
        self.granted = granted

    def has_permission(self, name):
        return name in self.granted


class SecretWidgetManager(WidgetManager):
    """Widgets with a restricted ``secret``, served to a requester holding
    ``granted`` permissions."""

    _model = SecretWidgetModel
    prefix = "/v1/secret_widget"
    granted: ClassVar[frozenset] = frozenset()

    def __init__(self, model_registry=None, requester_id=None):
        super().__init__(model_registry=model_registry, requester_id=requester_id)
        self.requester = _Requester(type(self).granted)


class GrantedSecretWidgetManager(SecretWidgetManager):
    prefix = "/v1/granted_secret_widget"
    granted = frozenset({READ_SECRET})


def _hides_secret(records):
    assert records, "expected records to check"
    for record in records:
        assert record.get("secret") != SECRET, record


def _secret_client(manager=SecretWidgetManager):
    client = _client(manager)
    created = client.post(
        manager.prefix,
        json={"secret_widget": {"name": "first", "secret": SECRET}},
    )
    assert created.status_code == 201, created.text
    return client, created


def test_create_response_hides_a_restricted_field():
    _, created = _secret_client()

    _hides_secret([created.json()["secret_widget"]])
    # The value was stored; only the response withholds it.
    assert [row.secret for row in WidgetManager.rows.values()] == [SECRET]


def test_batch_create_response_hides_a_restricted_field():
    response = _client(SecretWidgetManager).post(
        "/v1/secret_widget",
        json={"secret_widgets": [{"name": "a", "secret": SECRET}]},
    )

    assert response.status_code == 201, response.text
    _hides_secret(response.json()["secret_widgets"])


def test_update_responses_hide_a_restricted_field():
    client, created = _secret_client()
    widget_id = created.json()["secret_widget"]["id"]

    plain = client.put(
        f"/v1/secret_widget/{widget_id}", json={"secret_widget": {"name": "b"}}
    )
    assert plain.status_code == 200, plain.text
    _hides_secret([plain.json()["secret_widget"]])

    projected = client.put(
        f"/v1/secret_widget/{widget_id}",
        json={"secret_widget": {"name": "c"}, "fields": ["name", "secret"]},
    )
    assert projected.status_code == 200, projected.text
    assert projected.json()["secret_widget"]["name"] == "c"
    _hides_secret([projected.json()["secret_widget"]])


def test_batch_update_response_hides_a_restricted_field():
    client, created = _secret_client()
    widget_id = created.json()["secret_widget"]["id"]

    response = client.put(
        "/v1/secret_widget",
        json={"secret_widget": {"name": "b"}, "target_ids": [widget_id]},
    )

    assert response.status_code == 200, response.text
    _hides_secret(response.json()["secret_widgets"])


def test_search_response_hides_a_restricted_field():
    client, _ = _secret_client()

    response = client.post("/v1/secret_widget/search", json={"secret_widget": {}})

    assert response.status_code == 200, response.text
    _hides_secret(response.json()["secret_widgets"])


@pytest.mark.parametrize(
    "query, body",
    [
        ("sort_by=secret", {}),
        ("fields=secret", {}),
        ("", {"secret_widget": {"secret": SECRET}}),
    ],
)
def test_search_refuses_a_restricted_field_in_sort_projection_or_filter(query, body):
    client, _ = _secret_client()

    response = client.post(f"/v1/secret_widget/search?{query}", json=body)

    assert response.status_code == 403, response.text


def test_a_requester_holding_the_permission_sees_the_field():
    client, created = _secret_client(GrantedSecretWidgetManager)
    assert created.json()["secret_widget"]["secret"] == SECRET

    response = client.post(
        "/v1/granted_secret_widget/search?sort_by=secret",
        json={"secret_widget": {"secret": SECRET}},
    )
    assert response.status_code == 200, response.text
    assert [w["secret"] for w in response.json()["secret_widgets"]] == [SECRET]


def test_list_fallback_for_unvalidated_rows_hides_a_restricted_field():
    """A row the plural envelope cannot validate is still filtered: the
    fallback used to return the raw rows with no ACL."""
    client, _ = _secret_client()
    # A row the envelope rejects (its name is not a string) forces the
    # fallback path.
    WidgetManager.rows["raw"] = {"id": "raw", "name": {"not": "str"}, "secret": SECRET}

    response = client.get("/v1/secret_widget")

    assert response.status_code == 200, response.text
    _hides_secret(response.json()["secret_widgets"])
