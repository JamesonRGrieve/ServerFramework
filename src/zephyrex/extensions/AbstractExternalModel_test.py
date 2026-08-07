"""End-to-end tests for the new pieces added to AbstractExternalModel:
typed-error wiring, idempotency key helper, BatchResult, navigation
include plumbing, and field-mapping pipeline integration.
"""

from __future__ import annotations

import os
import warnings
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Dict, List

import pytest

from zephyrex.extensions.AbstractExternalModel import (
    AbstractExternalManager,
    AbstractExternalModel,
    BatchResult,
    BatchedNavigationResolver,
    ExternalNavigationProperty,
    _unwrap_provider_call,
    bind_resolver,
    derive_batch_idempotency_key,
    get_navigation_includes,
    idempotent,
    is_idempotent,
    reset_navigation_includes,
    reset_resolver,
    set_navigation_includes,
)
from zephyrex.extensions.ExternalErrors import (
    BaseExternalError,
    InvalidInputExternalError,
    NavigationNotIncludedError,
    PermanentExternalError,
    TransientExternalError,
)
from zephyrex.extensions.FieldMappings import CentsToDecimal, EnumRemap, Rename


# ---------------------------------------------------------------------------
# Minimal concrete model (must implement abstract *_via_provider methods)
# ---------------------------------------------------------------------------


class _Stub_Model(AbstractExternalModel):
    external_resource: ClassVar[str] = "stubs"
    field_mappings: ClassVar[List] = [
        Rename("display_name", "name"),
        CentsToDecimal("price", "amount"),
        EnumRemap("status", "state", {"active": "A", "inactive": "I"}),
    ]

    id: str | None = None
    display_name: str | None = None
    price: Decimal | None = None
    status: str | None = None

    @staticmethod
    def create_via_provider(provider_instance, **kwargs):
        return {"success": True, "data": kwargs}

    @staticmethod
    def get_via_provider(provider_instance, external_id):
        return {"success": True, "data": {"id": external_id}}

    @staticmethod
    def list_via_provider(provider_instance, **kwargs):
        return {"success": True, "data": []}

    @staticmethod
    def update_via_provider(provider_instance, external_id, **kwargs):
        return {"success": True, "data": {"id": external_id, **kwargs}}

    @staticmethod
    def delete_via_provider(provider_instance, external_id):
        return {"success": True, "data": None}


class TestFieldMappingPipelineIntegration:
    def test_to_external_format_uses_pipeline(self):
        ext = _Stub_Model.to_external_format(
            {"display_name": "Pro", "price": Decimal("9.99"), "status": "active"}
        )
        assert ext == {"name": "Pro", "amount": 999, "state": "A"}

    def test_from_external_format_uses_pipeline(self):
        internal = _Stub_Model.from_external_format(
            {"name": "Pro", "amount": 999, "state": "A"}
        )
        assert internal == {
            "display_name": "Pro",
            "price": Decimal("9.99"),
            "status": "active",
        }

    def test_legacy_dict_field_mappings_still_work(self):
        class _LegacyModel(AbstractExternalModel):
            field_mappings: ClassVar[Dict[str, str]] = {"a": "A", "b": "B"}

            @staticmethod
            def create_via_provider(provider_instance, **kwargs):
                return {"success": True, "data": kwargs}

            @staticmethod
            def get_via_provider(provider_instance, external_id):
                return {"success": True, "data": {}}

            @staticmethod
            def list_via_provider(provider_instance, **kwargs):
                return {"success": True, "data": []}

            @staticmethod
            def update_via_provider(provider_instance, external_id, **kwargs):
                return {"success": True, "data": {}}

            @staticmethod
            def delete_via_provider(provider_instance, external_id):
                return {"success": True, "data": None}

        out = _LegacyModel.to_external_format({"a": 1, "b": 2})
        assert out == {"A": 1, "B": 2}


class TestIdempotencyDecorator:
    def test_marks_method(self):
        @idempotent
        def my_method():
            pass

        assert is_idempotent(my_method) is True

    def test_unmarked_method(self):
        def plain():
            pass

        assert is_idempotent(plain) is False


class _Stub_Manager(AbstractExternalManager):
    Model = _Stub_Model


class TestIdempotencyKey:
    def test_same_inputs_same_key(self):
        m = _Stub_Manager(requester_id="user_1")
        k1 = m.idempotency_key("create", "user_1", {"a": 1, "b": 2})
        k2 = m.idempotency_key("create", "user_1", {"b": 2, "a": 1})
        assert k1 == k2

    def test_different_requester_different_key(self):
        m = _Stub_Manager(requester_id="user_1")
        k1 = m.idempotency_key("create", "user_1", {"x": 1})
        k2 = m.idempotency_key("create", "user_2", {"x": 1})
        assert k1 != k2

    def test_canonicalizes_decimal(self):
        m = _Stub_Manager(requester_id="r1")
        k1 = m.idempotency_key("op", "r1", {"price": Decimal("9.99")})
        k2 = m.idempotency_key("op", "r1", {"price": "9.99"})
        # Decimal canonicalizes to its string form, so keys match.
        assert k1 == k2

    def test_canonicalizes_datetime(self):
        m = _Stub_Manager(requester_id="r1")
        dt = datetime(2026, 4, 28, 12, 0, 0)
        k1 = m.idempotency_key("op", "r1", {"when": dt})
        k2 = m.idempotency_key("op", "r1", {"when": dt.isoformat()})
        assert k1 == k2

    def test_canonicalizes_set(self):
        m = _Stub_Manager(requester_id="r1")
        k1 = m.idempotency_key("op", "r1", {"tags": {"a", "b", "c"}})
        k2 = m.idempotency_key("op", "r1", {"tags": {"c", "b", "a"}})
        assert k1 == k2


class TestBatchIdempotencyKey:
    def test_derive_is_order_independent(self):
        keys = ["key_a", "key_b", "key_c"]
        k1 = derive_batch_idempotency_key(keys)
        k2 = derive_batch_idempotency_key(list(reversed(keys)))
        assert k1 == k2


class TestBatchResult:
    def test_empty(self):
        r = BatchResult()
        assert r.all_succeeded is True
        assert r.count == 0

    def test_partial_failure(self):
        r = BatchResult()
        r.successes.append((0, {"id": "a"}))
        r.failures.append((1, PermanentExternalError("bad")))
        assert r.all_succeeded is False
        assert r.count == 2


class TestNavigationIncludes:
    def test_default_empty(self):
        # New thread = fresh contextvar.
        assert get_navigation_includes() == frozenset()

    def test_set_and_reset(self):
        token = set_navigation_includes({"customer", "subscription"})
        try:
            assert get_navigation_includes() == {"customer", "subscription"}
        finally:
            reset_navigation_includes(token)
        assert get_navigation_includes() == frozenset()

    def test_strict_mode_raises_when_not_included(self, monkeypatch):
        monkeypatch.setenv("STRICT_NAVIGATION_INCLUDE", "1")

        # Build an instance that has the navigation property and a value to resolve.
        nav = ExternalNavigationProperty(
            external_model_class=_Stub_Model,
            local_field="external_id",
        )
        nav.attr_name = "remote"

        class Holder:
            external_id = "ext_1"
            remote = nav

        token = set_navigation_includes(set())
        try:
            with pytest.raises(NavigationNotIncludedError):
                _ = Holder().remote
        finally:
            reset_navigation_includes(token)

    def test_lenient_mode_falls_back(self, monkeypatch):
        monkeypatch.delenv("STRICT_NAVIGATION_INCLUDE", raising=False)
        nav = ExternalNavigationProperty(
            external_model_class=_Stub_Model,
            local_field="external_id",
        )
        nav.attr_name = "remote"

        class Holder:
            external_id = "ext_1"
            remote = nav

        token = set_navigation_includes(set())
        try:
            # Lenient mode: we expect lazy resolution to not raise. The
            # resolver will fail and return None due to no manager, but
            # critically it does not raise NavigationNotIncludedError.
            try:
                _ = Holder().remote
            except NavigationNotIncludedError:
                pytest.fail("NavigationNotIncludedError raised in lenient mode")
        finally:
            reset_navigation_includes(token)


class TestBatchedNavigationResolver:
    def test_warm_and_get(self):
        resolver = BatchedNavigationResolver()
        resolver.warm(_Stub_Model, {"a": {"id": "a", "name": "A"}})
        assert resolver.has(_Stub_Model, "a") is True
        assert resolver.get(_Stub_Model, "a") == {"id": "a", "name": "A"}
        assert resolver.has(_Stub_Model, "b") is False

    def test_bind_and_reset(self):
        resolver = BatchedNavigationResolver()
        token = bind_resolver(resolver)
        try:
            from zephyrex.extensions.AbstractExternalModel import get_resolver
            assert get_resolver() is resolver
        finally:
            reset_resolver(token)


class TestBatchResultDeriveKey:
    def test_works_with_empty(self):
        # Sanity: an empty batch has a deterministic key too.
        k = derive_batch_idempotency_key([])
        assert isinstance(k, str)
        assert len(k) == 64


class TestUnwrapProviderCall:
    """Item 1: AbstractExternalAPIClient translates dict envelopes to typed
    exceptions, while accepting raise-style callees as a back-compat shim."""

    def test_legacy_envelope_success_is_unwrapped(self):
        def fake_provider(provider_instance, **kwargs):
            return {"success": True, "data": {"x": 1}, "error": None}

        out = _unwrap_provider_call(_Stub_Model, "create", fake_provider, None, x=1)
        assert out == {"x": 1}

    def test_legacy_envelope_no_error_key_is_unwrapped(self):
        def fake_provider(provider_instance, **kwargs):
            return {"success": True, "data": {"x": 1}}

        out = _unwrap_provider_call(_Stub_Model, "create", fake_provider, None, x=1)
        assert out == {"x": 1}

    def test_legacy_envelope_not_found_translates_to_invalid_input(self):
        def fake_provider(provider_instance, external_id):
            return {"success": False, "error": "Not found"}

        with pytest.raises(InvalidInputExternalError) as ei:
            _unwrap_provider_call(_Stub_Model, "get", fake_provider, None, "x")
        assert ei.value.upstream_status == 404

    def test_legacy_envelope_other_error_becomes_permanent(self):
        def fake_provider(provider_instance, **kwargs):
            return {"success": False, "error": "boom"}

        with pytest.raises(PermanentExternalError):
            _unwrap_provider_call(_Stub_Model, "create", fake_provider, None)

    def test_typed_raise_passes_through(self):
        def fake_provider(provider_instance, **kwargs):
            raise TransientExternalError("upstream 503", upstream_status=503)

        with pytest.raises(TransientExternalError):
            _unwrap_provider_call(_Stub_Model, "create", fake_provider, None)

    def test_unknown_exception_wrapped_when_opted_in(self):
        class _Opt(AbstractExternalModel):
            raises_typed_errors: ClassVar[bool] = True

            @staticmethod
            def create_via_provider(p, **kw): return None

            @staticmethod
            def get_via_provider(p, eid): return None

            @staticmethod
            def list_via_provider(p, **kw): return []

            @staticmethod
            def update_via_provider(p, eid, **kw): return None

            @staticmethod
            def delete_via_provider(p, eid): return None

        def fake_provider(provider_instance, **kwargs):
            raise RuntimeError("kaboom")

        with pytest.raises(PermanentExternalError):
            _unwrap_provider_call(_Opt, "create", fake_provider, None)

    def test_unknown_exception_not_wrapped_when_not_opted_in(self):
        def fake_provider(provider_instance, **kwargs):
            raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError):
            _unwrap_provider_call(_Stub_Model, "create", fake_provider, None)

    def test_payload_passthrough_when_not_an_envelope(self):
        def fake_provider(provider_instance, **kwargs):
            return {"id": "abc", "name": "X"}

        out = _unwrap_provider_call(_Stub_Model, "create", fake_provider, None)
        assert out == {"id": "abc", "name": "X"}


class TestBatchOperations:
    """Item 12: AbstractExternalManager.batch_* delegates when the model
    overrides batch_*_via_provider, else loops over per-item create/update/
    delete."""

    def test_supports_bulk_returns_false_by_default(self):
        assert _Stub_Model.supports_bulk("create") is False
        assert _Stub_Model.supports_bulk("update") is False
        assert _Stub_Model.supports_bulk("delete") is False

    def test_supports_bulk_returns_true_when_overridden(self):
        class _BulkModel(AbstractExternalModel):
            @staticmethod
            def create_via_provider(p, **kw): return {}

            @staticmethod
            def get_via_provider(p, eid): return {}

            @staticmethod
            def list_via_provider(p, **kw): return []

            @staticmethod
            def update_via_provider(p, eid, **kw): return {}

            @staticmethod
            def delete_via_provider(p, eid): return None

            @staticmethod
            def batch_create_via_provider(p, items):
                return [{"id": f"x{idx}", **it} for idx, it in enumerate(items)]

        assert _BulkModel.supports_bulk("create") is True
        assert _BulkModel.supports_bulk("update") is False

    def test_batch_create_uses_bulk_slot_when_available(self):
        class _BulkModel(AbstractExternalModel):
            @staticmethod
            def create_via_provider(p, **kw): return {"success": True, "data": kw}

            @staticmethod
            def get_via_provider(p, eid): return {"success": True, "data": {}}

            @staticmethod
            def list_via_provider(p, **kw): return {"success": True, "data": []}

            @staticmethod
            def update_via_provider(p, eid, **kw): return {"success": True, "data": {}}

            @staticmethod
            def delete_via_provider(p, eid): return {"success": True, "data": None}

            @staticmethod
            def batch_create_via_provider(p, items):
                return [{"id": f"x{idx}", **it} for idx, it in enumerate(items)]

        class _FakeRotation:
            def rotate(self, fn, **kwargs):
                return fn(None, **kwargs)

        class _BulkManager(AbstractExternalManager):
            Model = _BulkModel

        m = _BulkManager(requester_id="u1", rotation_manager=_FakeRotation())
        result = m.batch_create([{"a": 1}, {"a": 2}])
        assert result.all_succeeded is True
        assert result.count == 2
        assert result.successes[0][0] == 0
        assert result.successes[1][0] == 1

    def test_batch_create_per_item_failure_in_bulk_mode(self):
        class _BulkModel(AbstractExternalModel):
            @staticmethod
            def create_via_provider(p, **kw): return {"success": True, "data": kw}

            @staticmethod
            def get_via_provider(p, eid): return {"success": True, "data": {}}

            @staticmethod
            def list_via_provider(p, **kw): return {"success": True, "data": []}

            @staticmethod
            def update_via_provider(p, eid, **kw): return {"success": True, "data": {}}

            @staticmethod
            def delete_via_provider(p, eid): return {"success": True, "data": None}

            @staticmethod
            def batch_create_via_provider(p, items):
                return [
                    {"id": "x0", **items[0]},
                    InvalidInputExternalError("bad", upstream_status=400),
                    {"id": "x2", **items[2]},
                ]

        class _FakeRotation:
            def rotate(self, fn, **kwargs):
                return fn(None, **kwargs)

        class _BulkManager(AbstractExternalManager):
            Model = _BulkModel

        m = _BulkManager(requester_id="u1", rotation_manager=_FakeRotation())
        result = m.batch_create([{"a": 1}, {"a": 2}, {"a": 3}])
        assert result.all_succeeded is False
        assert len(result.successes) == 2
        assert len(result.failures) == 1
        assert result.failures[0][0] == 1
        assert isinstance(result.failures[0][1], InvalidInputExternalError)

    def test_batch_create_whole_batch_failure_in_bulk_mode(self):
        class _BulkModel(AbstractExternalModel):
            @staticmethod
            def create_via_provider(p, **kw): return {"success": True, "data": kw}

            @staticmethod
            def get_via_provider(p, eid): return {"success": True, "data": {}}

            @staticmethod
            def list_via_provider(p, **kw): return {"success": True, "data": []}

            @staticmethod
            def update_via_provider(p, eid, **kw): return {"success": True, "data": {}}

            @staticmethod
            def delete_via_provider(p, eid): return {"success": True, "data": None}

            @staticmethod
            def batch_create_via_provider(p, items):
                raise TransientExternalError("upstream down")

        class _FakeRotation:
            def rotate(self, fn, **kwargs):
                return fn(None, **kwargs)

        class _BulkManager(AbstractExternalManager):
            Model = _BulkModel

        m = _BulkManager(requester_id="u1", rotation_manager=_FakeRotation())
        result = m.batch_create([{"a": 1}, {"a": 2}])
        assert result.all_succeeded is False
        assert len(result.failures) == 2
        assert all(isinstance(f[1], TransientExternalError) for f in result.failures)

    def test_default_batch_slots_raise_not_implemented(self):
        with pytest.raises(NotImplementedError):
            AbstractExternalModel.batch_create_via_provider(None, [])
        with pytest.raises(NotImplementedError):
            AbstractExternalModel.batch_update_via_provider(None, [])
        with pytest.raises(NotImplementedError):
            AbstractExternalModel.batch_delete_via_provider(None, [])


class TestBatchedNavigationResolveMany:
    def test_resolves_via_manager_factory(self):
        class _FakeManager:
            list_calls: list = []

            def list(self, ids):
                _FakeManager.list_calls.append(list(ids))
                return [{"id": i, "name": f"name_{i}"} for i in ids]

        resolver = BatchedNavigationResolver()
        result = resolver.resolve_many(
            _Stub_Model,
            ["a", "b", "c"],
            manager_factory=_FakeManager,
        )
        assert set(result.keys()) == {"a", "b", "c"}
        assert result["a"]["name"] == "name_a"
        # Subsequent calls hit the cache, not the manager.
        _FakeManager.list_calls.clear()
        again = resolver.resolve_many(
            _Stub_Model,
            ["a", "b"],
            manager_factory=_FakeManager,
        )
        assert again["a"]["name"] == "name_a"
        assert _FakeManager.list_calls == []  # cache hit


class TestInitSubclassWarning:
    def test_warns_on_dict_return_when_opted_in(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _Suspect(AbstractExternalModel):
                raises_typed_errors: ClassVar[bool] = True

                @staticmethod
                def create_via_provider(provider_instance, **kwargs) -> dict:
                    return {}

                @staticmethod
                def get_via_provider(provider_instance, external_id):
                    return {}

                @staticmethod
                def list_via_provider(provider_instance, **kwargs):
                    return []

                @staticmethod
                def update_via_provider(provider_instance, external_id, **kwargs):
                    return {}

                @staticmethod
                def delete_via_provider(provider_instance, external_id):
                    return None

            suspect_warns = [
                w for w in caught if "raises_typed_errors" not in str(w.message) and issubclass(w.category, UserWarning)
            ]
            # At least one warning should have fired about `dict` return.
            assert any("dict" in str(w.message) for w in suspect_warns)

    def test_does_not_warn_when_not_opted_in(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")

            class _LegacyOK(AbstractExternalModel):
                @staticmethod
                def create_via_provider(provider_instance, **kwargs) -> dict:
                    return {"success": True, "data": {}}

                @staticmethod
                def get_via_provider(provider_instance, external_id):
                    return {"success": True, "data": {}}

                @staticmethod
                def list_via_provider(provider_instance, **kwargs):
                    return {"success": True, "data": []}

                @staticmethod
                def update_via_provider(provider_instance, external_id, **kwargs):
                    return {"success": True, "data": {}}

                @staticmethod
                def delete_via_provider(provider_instance, external_id):
                    return {"success": True, "data": None}

            assert not any(
                "dict" in str(w.message) and issubclass(w.category, UserWarning)
                for w in caught
            )
