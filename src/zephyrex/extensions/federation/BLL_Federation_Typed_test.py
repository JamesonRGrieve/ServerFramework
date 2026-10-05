# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed, table-less federated models, proved against a WordPress-shaped
source (posts, a custom post type, a taxonomy, registered meta; JSON
Schemas as the WordPress REST API publishes them): the lift, the names,
and a real app serving the types on REST and GraphQL, live, with no table
made for them. ERPNext, the first real source, is proved in
``erp/EP_ERP_Typed_test.py``."""

import asyncio
from typing import Any, Dict, Iterator, List, Set

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect as sql_inspect

from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.federation import BLL_Federation_Typed as typed
from zephyrex.extensions.federation.BLL_Federation_Bootstrap import bootstrap_federation
from zephyrex.extensions.federation.BLL_Federation_Typed import (
    FederatedType,
    Operation,
    bind_federated_sources,
    federated_sources,
    lift_type,
    model_stem,
    register_federated_sources,
    type_slugs,
    unregister_federated_sources,
)
from zephyrex.extensions.federation.EXT_Federation import EXT_Federation
from zephyrex.extensions.federation.WordPressShapedSource import (
    VERSION_FIELD,
    WORDPRESS_NAMESPACE,
    WORDPRESS_TYPES,
    UnreachableSource,
    WordPressShapedSite,
    WordPressShapedSource,
)

NAMESPACE = WORDPRESS_NAMESPACE
POSTS = f"/v1/federated/{NAMESPACE}/post"
BOOKS = f"/v1/federated/{NAMESPACE}/book"
GENRES = f"/v1/federated/{NAMESPACE}/genre"
UNREACHABLE = "wordpress_unreachable"
SITE = WordPressShapedSite()
# Requesters the site refuses (its own access rule, read on every call).
REFUSED: Set[str] = set()


def _lifted(name: str) -> Any:
    federated = next(t for t in WORDPRESS_TYPES if t.name == name)
    return lift_type(NAMESPACE, name, federated)


class TestLift:
    def test_a_post_is_a_typed_record_and_a_write_payload(self):
        post = _lifted("post")
        record, write = post.record, post.write

        assert record.__name__ == "WordpressAb12cd34Post"
        assert write is not None and write.__name__ == "WordpressAb12cd34PostWrite"
        # Read-only fields are read, never written.
        for read_only in ("id", "guid", "link", "modified", VERSION_FIELD, "type"):
            assert read_only in record.model_fields
            assert read_only not in write.model_fields
        # A name no model field can hold (``_links``) is left out.
        assert "_links" not in record.model_fields
        made = record.model_validate(
            {
                "id": 7,
                "date": None,
                "status": "draft",
                "title": {"raw": "Hi", "rendered": "<p>Hi</p>"},
                "categories": [1, 2],
                "meta": {"footnotes": ""},
            }
        )
        assert made.title.rendered == "<p>Hi</p>"
        assert made.categories == [1, 2]

    def test_an_inline_object_is_a_nested_type_and_only_its_writable_part_is_written(
        self,
    ):
        write = _lifted("post").write
        assert write is not None
        title = write.model_fields["title"].annotation
        assert "WordpressAb12cd34PostTitleWrite" in str(title)
        write.model_validate({"title": {"raw": "Hello"}})
        with pytest.raises(ValidationError):
            write.model_validate({"title": {"raw": "Hello", "rendered": "<p>x</p>"}})
        # An object with nothing writable (``guid``) is not written at all.
        assert "guid" not in write.model_fields

    @pytest.mark.parametrize(
        "data",
        [
            {"id": 3},
            {"modified_gmt": "2026-01-01T00:00:00"},
            {"colour": "red"},
            {"status": "archived"},
            {"categories": ["news"]},
        ],
    )
    def test_a_write_holds_only_the_types_writable_fields_each_of_its_type(self, data):
        write = _lifted("post").write
        assert write is not None
        with pytest.raises(ValidationError):
            write.model_validate(data)

    def test_a_type_without_a_version_is_read_and_created_only(self):
        genre = _lifted("genre")
        assert genre.operations == {Operation.LIST, Operation.GET, Operation.CREATE}
        assert _lifted("post").operations == set(Operation)

    def test_definitions_are_nested_types_of_their_own(self):
        lifted = lift_type(
            NAMESPACE,
            "order",
            FederatedType(
                name="order",
                key_field="id",
                json_schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "lines": {"type": "array", "items": {"$ref": "#/$defs/Line"}},
                        "billing": {"$ref": "#/definitions/Address"},
                    },
                    "$defs": {
                        "Line": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "qty": {"type": "integer"},
                                "total": {"type": "number", "readOnly": True},
                            },
                        }
                    },
                    "definitions": {},
                },
            ),
        )
        line = lifted.record.model_validate(
            {"id": "o1", "lines": [{"sku": "a", "qty": 2}]}
        )
        assert line.lines[0].qty == 2
        assert [model.__name__ for model in lifted.nested] == [
            "WordpressAb12cd34OrderLine"
        ]
        assert lifted.write is not None
        with pytest.raises(ValidationError):
            lifted.write.model_validate({"lines": [{"sku": "a", "total": 3.0}]})

    def test_names_are_namespaced_and_distinct(self):
        assert (
            model_stem("erpnext_3f2a9c1d", "sales_invoice")
            == "Erpnext3f2a9c1dSalesInvoice"
        )
        assert type_slugs(["Sales Invoice", "sales-invoice", "2FA Device", "post"]) == [
            "sales_invoice",
            "sales_invoice_2",
            "t_2fa_device",
            "post",
        ]

    def test_a_type_must_hold_its_key_and_version(self):
        with pytest.raises(ValueError, match="key field"):
            lift_type(
                NAMESPACE,
                "x",
                FederatedType(name="x", json_schema={"properties": {}}, key_field="id"),
            )
        with pytest.raises(ValueError, match="version field"):
            lift_type(
                NAMESPACE,
                "x",
                FederatedType(
                    name="x",
                    json_schema={"properties": {"id": {"type": "integer"}}},
                    key_field="id",
                    version_field="modified",
                ),
            )


class _RecordingRegistry:
    """Records table-less bindings; loads no extension."""

    def __init__(self) -> None:
        self.bound: List[type] = []

    def bind_external(self, model: type, manager: type) -> None:
        self.bound.append(model)

    def loaded_extension_names(self) -> frozenset:
        return frozenset()


class TestBinding:
    def test_a_source_must_name_itself_as_a_namespace(self):
        binding = bind_federated_sources(
            _RecordingRegistry(),
            [WordPressShapedSource(SITE, namespace="Not A Namespace")],
        )
        assert binding.errors == {"Not A Namespace": "namespace is not [a-z][a-z0-9_]*"}
        assert binding.models == {}

    def test_a_catalogue_that_takes_too_long_is_left_out(self, monkeypatch):
        class Slow(WordPressShapedSource):
            async def catalogue(self) -> Any:
                await asyncio.sleep(5)
                return []

        monkeypatch.setattr(typed, "CATALOGUE_TIMEOUT_SECONDS", 0.05)
        registry = _RecordingRegistry()
        binding = bind_federated_sources(registry, [Slow(SITE)])
        assert "took longer" in binding.errors[NAMESPACE]
        assert registry.bound == []

    def test_an_app_without_the_federation_extension_federates_nothing(self):
        assert bootstrap_federation(model_registry=_RecordingRegistry()) is None


def _sources(_registry: Any) -> List[Any]:
    return [
        WordPressShapedSource(SITE, refuses=REFUSED),
        # A second source claiming the same namespace is not served.
        WordPressShapedSource(WordPressShapedSite()),
        UnreachableSource(WordPressShapedSite(), namespace=UNREACHABLE),
    ]


def bearer(user: Any) -> Dict[str, str]:
    return {"Authorization": f"Bearer {user.jwt}"}


GRAPHQL_POST = """
query Post($id: Long) {
  wordpressAb12cd34PostGet(input: {id: $id}) {
    id status modifiedGmt categories
    title { raw rendered }
    meta { footnotes }
  }
}
"""

GRAPHQL_CREATE = """
mutation {
  wordpressAb12cd34BookCreate(input: {data: {
    status: "draft", title: {raw: "Dune"}, genre: [4], meta: {isbn: "978-0441013593"}
  }}) {
    id title { rendered } genre meta { isbn }
  }
}
"""


class TestWordPressShapedApp(ExtensionServerMixin):
    """The federation extension's app, booted with the WordPress-shaped
    sources registered: their types are served typed, live."""

    extension_class = EXT_Federation

    @pytest.fixture(scope="module", autouse=True)
    def wordpress_sources(self) -> Iterator[None]:
        register_federated_sources("federation", _sources)
        yield
        unregister_federated_sources("federation")

    def _post(self, server: Any, user: Any, **data: Any) -> Any:
        made = server.post(f"{POSTS}/create", json={"data": data}, headers=bearer(user))
        assert made.status_code == 200, made.text
        return made

    def test_every_type_is_served_on_rest(self, server, admin_a):
        paths = set(server.app.openapi()["paths"])
        for prefix in (POSTS, BOOKS):
            for operation in ("list", "get", "create", "update", "delete"):
                assert f"{prefix}/{operation}" in paths
        assert {f"{GENRES}/{op}" for op in ("list", "get", "create")} <= paths
        assert not {f"{GENRES}/update", f"{GENRES}/delete"} & paths

    def test_a_record_is_typed_live_and_versioned(self, server, admin_a):
        made = self._post(
            server, admin_a, title={"raw": "Hello"}, status="publish", categories=[1]
        )
        body = made.json()
        assert body["title"] == {"raw": "Hello", "rendered": "<p>Hello</p>"}
        assert made.headers["etag"] == f'"{body[VERSION_FIELD]}"'
        assert SITE.records["post"][body["id"]]["title"]["raw"] == "Hello"

        read = server.post(
            f"{POSTS}/get", json={"id": body["id"]}, headers=bearer(admin_a)
        )
        assert read.status_code == 200, read.text
        assert read.headers["etag"] == made.headers["etag"]
        listed = server.post(
            f"{POSTS}/list",
            json={"filters": {"status": "publish"}},
            headers=bearer(admin_a),
        )
        assert listed.status_code == 200, listed.text
        assert body["id"] in [row["id"] for row in listed.json()["items"]]

    def test_a_write_is_checked_against_the_type(self, server, admin_a):
        for data in ({"id": 9}, {"colour": "red"}, {"title": {"rendered": "x"}}):
            refused = server.post(
                f"{POSTS}/create", json={"data": data}, headers=bearer(admin_a)
            )
            assert refused.status_code == 422, (data, refused.text)

    def test_a_save_names_its_version(self, server, admin_a):
        made = self._post(server, admin_a, title={"raw": "Draft"})
        record_id = made.json()["id"]
        change = {"id": record_id, "data": {"title": {"raw": "Second"}}}

        missing = server.post(f"{POSTS}/update", json=change, headers=bearer(admin_a))
        assert missing.status_code == 428, missing.text

        saved = server.post(
            f"{POSTS}/update",
            json=change,
            headers={**bearer(admin_a), "If-Match": made.headers["etag"]},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["title"]["raw"] == "Second"

        stale = server.post(
            f"{POSTS}/update",
            json={"id": record_id, "data": {"title": {"raw": "Third"}}},
            headers={**bearer(admin_a), "If-Match": made.headers["etag"]},
        )
        assert stale.status_code == 412, stale.text
        assert stale.json()["current"]["title"]["raw"] == "Second"
        assert stale.headers["etag"] == saved.headers["etag"]
        assert SITE.records["post"][record_id]["title"]["raw"] == "Second"

        gone = server.post(
            f"{POSTS}/delete",
            json={"id": record_id, "if_match": saved.json()[VERSION_FIELD]},
            headers=bearer(admin_a),
        )
        assert gone.status_code == 200, gone.text
        assert gone.json() == {"key": str(record_id), "deleted": True}
        assert record_id not in SITE.records["post"]

    def test_every_route_needs_a_signed_in_user(self, server):
        for path in (f"{POSTS}/list", f"{POSTS}/get", f"{GENRES}/create"):
            assert server.post(path, json={"id": 1}).status_code == 401, path

    def test_the_types_are_graphql_types(self, server, admin_a):
        made = self._post(
            server,
            admin_a,
            title={"raw": "Typed"},
            categories=[5],
            meta={"footnotes": "n"},
        ).json()
        answer = server.post(
            "/graphql",
            json={"query": GRAPHQL_POST, "variables": {"id": made["id"]}},
            headers=bearer(admin_a),
        )
        assert answer.status_code == 200, answer.text
        assert "errors" not in answer.json(), answer.text
        post = answer.json()["data"]["wordpressAb12cd34PostGet"]
        assert post["title"] == {"raw": "Typed", "rendered": "<p>Typed</p>"}
        assert post["categories"] == [5] and post["meta"] == {"footnotes": "n"}
        assert post["modifiedGmt"] == made[VERSION_FIELD]

        book = server.post(
            "/graphql", json={"query": GRAPHQL_CREATE}, headers=bearer(admin_a)
        )
        assert "errors" not in book.json(), book.text
        created = book.json()["data"]["wordpressAb12cd34BookCreate"]
        assert created["title"] == {"rendered": "<p>Dune</p>"}
        assert created["genre"] == [4] and created["meta"] == {"isbn": "978-0441013593"}

        typed = server.post(
            "/graphql",
            json={
                "query": '{ __type(name: "WordpressAb12cd34PostType") { fields { name } } }'
            },
            headers=bearer(admin_a),
        )
        fields = {f["name"] for f in typed.json()["data"]["__type"]["fields"]}
        assert {"id", "title", "modifiedGmt", "categories", "meta"} <= fields

    def test_a_graphql_input_left_out_takes_its_default(self, server, admin_a):
        self._post(server, admin_a, title={"raw": "Paged"})
        listed = server.post(
            "/graphql",
            json={
                "query": "{ wordpressAb12cd34PostList(input: {}) { start pageLength items { id } } }"
            },
            headers=bearer(admin_a),
        )
        assert "errors" not in listed.json(), listed.text
        page = listed.json()["data"]["wordpressAb12cd34PostList"]
        assert page["start"] == 0 and page["pageLength"] == 20 and page["items"]

    def test_a_record_the_upstream_answers_outside_its_schema_is_its_fault(
        self, server, admin_a
    ):
        made = self._post(server, admin_a, title={"raw": "Broken"}).json()
        SITE.records["post"][made["id"]]["author"] = "not a user id"
        try:
            broken = server.post(
                f"{POSTS}/get", json={"id": made["id"]}, headers=bearer(admin_a)
            )
            assert broken.status_code == 502, broken.text
        finally:
            del SITE.records["post"][made["id"]]

    def test_no_table_is_made_for_a_federated_type(self, server, model_registry):
        records = [model for model, _ in model_registry.external_models.values()]
        assert {model.__name__ for model in records} >= {
            "WordpressAb12cd34Post",
            "WordpressAb12cd34Book",
            "WordpressAb12cd34Genre",
        }
        assert not set(records) & set(model_registry.bound_models)
        assert not set(records) & set(model_registry.db_models)
        engine = model_registry.database_manager.get_setup_engine()
        tables = sql_inspect(engine).get_table_names()
        metadata = model_registry.database_manager.Base.metadata.tables
        assert not [t for t in [*tables, *metadata] if "wordpress" in t.lower()]

    def test_a_source_the_app_cannot_reach_is_left_out_and_the_app_boots(
        self, server, admin_a
    ):
        errors = server.app.state.federation_report.errors
        assert "502" in errors[UNREACHABLE] and "not reachable" in errors[UNREACHABLE]
        assert errors[NAMESPACE] == "namespace is already served"
        paths = set(server.app.openapi()["paths"])
        assert not [p for p in paths if UNREACHABLE in p]
        assert (
            server.post(f"{POSTS}/list", json={}, headers=bearer(admin_a)).status_code
            == 200
        )

    def test_the_sources_own_access_rule_holds_on_every_call(
        self, server, admin_a, admin_b
    ):
        made = self._post(server, admin_a, title={"raw": "Mine"}).json()
        REFUSED.add(admin_b.id)
        try:
            for path, body in (
                (f"{POSTS}/get", {"id": made["id"]}),
                (f"{POSTS}/list", {}),
                (f"{POSTS}/create", {"data": {"title": {"raw": "x"}}}),
            ):
                refused = server.post(path, json=body, headers=bearer(admin_b))
                assert refused.status_code == 404, (path, refused.text)
        finally:
            REFUSED.discard(admin_b.id)
        allowed = server.post(
            f"{POSTS}/get", json={"id": made["id"]}, headers=bearer(admin_b)
        )
        assert allowed.status_code == 200, allowed.text

    def test_the_catalogue_lists_what_the_requester_may_use(
        self, server, admin_a, admin_b
    ):
        listed = server.get("/v1/federated/catalogue", headers=bearer(admin_a))
        assert listed.status_code == 200, listed.text
        rows = {row["name"]: row for row in listed.json()["types"]}
        assert set(rows) == {"post", "book", "genre"}
        assert rows["genre"]["operations"] == ["list", "get", "create"]
        assert rows["genre"]["version_field"] is None
        post = rows["post"]
        assert post["rest"]["update"] == f"{POSTS}/update"
        assert post["graphql_fields"]["get"] == "wordpressAb12cd34PostGet"
        assert post["graphql_type"] == "WordpressAb12cd34PostType"
        assert post["graphql_write_input"] == "WordpressAb12cd34PostWriteInput"
        assert "rendered" not in str(
            post["write_schema"]["$defs"]["WordpressAb12cd34PostTitleWrite"]
        )

        REFUSED.add(admin_b.id)
        try:
            hidden = server.get("/v1/federated/catalogue", headers=bearer(admin_b))
        finally:
            REFUSED.discard(admin_b.id)
        assert hidden.status_code == 200 and hidden.json()["types"] == []

        graphed = server.post(
            "/graphql",
            json={"query": "{ federatedCatalogue { types { name graphqlType } } }"},
            headers=bearer(admin_a),
        )
        assert "errors" not in graphed.json(), graphed.text
        assert {
            "name": "post",
            "graphqlType": "WordpressAb12cd34PostType",
        } in graphed.json()["data"]["federatedCatalogue"]["types"]

    def test_only_the_apps_extensions_sources_are_asked(self, model_registry):
        register_federated_sources("an_extension_not_loaded", _sources)
        try:
            namespaces = [
                source.namespace for source in federated_sources(model_registry)
            ]
        finally:
            unregister_federated_sources("an_extension_not_loaded")
        assert namespaces == [NAMESPACE, NAMESPACE, UNREACHABLE]
