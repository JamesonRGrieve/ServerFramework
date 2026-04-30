"""End-to-end tests for the mirror-on-create / -update / -delete lifecycle decorators."""

from __future__ import annotations

from pydantic import BaseModel

from serverframework.extensions.MirrorLifecycle import (
    MirrorEvent,
    MirrorPolicy,
    MirrorSpec,
    get_mirror_specs,
    mirror_on_create,
    mirror_on_delete,
    mirror_on_update,
)


class _LocalModel(BaseModel):
    id: str | None = None
    external_payment_id: str | None = None


class _ExternalModel(BaseModel):
    id: str | None = None


class _OtherExternalModel(BaseModel):
    id: str | None = None


class TestSpecAttachment:
    def test_create_decorator_attaches_spec(self):
        @mirror_on_create(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
        )
        class _M:
            pass

        specs = get_mirror_specs(_M)
        assert len(specs) == 1
        s = specs[0]
        assert isinstance(s, MirrorSpec)
        assert s.event == MirrorEvent.CREATE
        assert s.local is _LocalModel
        assert s.external is _ExternalModel
        assert s.link_field == "external_payment_id"
        assert s.policy == MirrorPolicy.OUTBOX

    def test_explicit_saga_policy(self):
        @mirror_on_create(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
            policy=MirrorPolicy.SAGA,
        )
        class _M:
            pass

        assert get_mirror_specs(_M)[0].policy == MirrorPolicy.SAGA

    def test_update_and_delete_decorators(self):
        @mirror_on_create(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
        )
        @mirror_on_update(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
        )
        @mirror_on_delete(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
        )
        class _M:
            pass

        specs = get_mirror_specs(_M)
        events = {s.event for s in specs}
        assert MirrorEvent.CREATE in events
        assert MirrorEvent.UPDATE in events
        assert MirrorEvent.DELETE in events

    def test_idempotency_key_fn_is_attached(self):
        def key_fn(req_id, args):
            return f"{req_id}-key"

        @mirror_on_create(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
            idempotency_key_fn=key_fn,
        )
        class _M:
            pass

        s = get_mirror_specs(_M)[0]
        assert s.idempotency_key_fn is key_fn

    def test_subclass_inherits_specs(self):
        @mirror_on_create(
            local=_LocalModel,
            external=_ExternalModel,
            link_field="external_payment_id",
        )
        class _Parent:
            pass

        @mirror_on_create(
            local=_LocalModel,
            external=_OtherExternalModel,
            link_field="external_payment_id",
        )
        class _Child(_Parent):
            pass

        # Child should have BOTH specs (parent + own).
        child_specs = get_mirror_specs(_Child)
        externals = {s.external for s in child_specs}
        assert _ExternalModel in externals
        assert _OtherExternalModel in externals

        # Parent should still have only its own spec.
        parent_specs = get_mirror_specs(_Parent)
        assert len(parent_specs) == 1
        assert parent_specs[0].external is _ExternalModel


class TestPolicyEnum:
    def test_values(self):
        assert MirrorPolicy.OUTBOX.value == "outbox"
        assert MirrorPolicy.SAGA.value == "saga"
