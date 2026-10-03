"""Tests for the access-controlled ability invoker.

Pure tests cover schema introspection, the access gate, and argument coercion.
Integration tests resolve and invoke *real* seeded abilities against a live
extension registry (no mocks): ``text_generation`` (a provider ability) for
resolution/schema, and ``email_status`` (a local meta ability that reads env)
for a full gated invocation with no external call.
"""

import os

import pytest

from zephyrex.extensions.ai_agents.AbilityInvoker import (
    AbilityAccessDenied,
    AbilityInvoker,
    NEVER_AGENT_INVOCABLE,
    ToolInvocationError,
    ability_to_tool_schema,
)
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin


class TestAbilityToolSchema:
    """Pure introspection of an ability callable into a tool schema."""

    def test_schema_from_provider_ability(self):
        from zephyrex.extensions.ai.PRV_Bifrost_AI import BifrostProvider

        schema = ability_to_tool_schema(
            "text_generation", BifrostProvider.generate_text
        )
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "text_generation"
        props = fn["parameters"]["properties"]
        # The calling-convention param and **kwargs are excluded...
        assert "bonded_instance" not in props
        assert "kwargs" not in props
        # ...the real model-facing params are present and typed...
        assert props["prompt"] == {"type": "string"}
        assert props["max_tokens"] == {"type": "integer"}
        assert props["temperature"] == {"type": "number"}
        # ...only the no-default, non-optional param is required.
        assert fn["parameters"]["required"] == ["prompt"]

    def test_schema_description_from_docstring(self):
        def sample(cls, target: str):
            """Do a sample thing.

            Longer detail that should not be in the description.
            """

        schema = ability_to_tool_schema("sample", sample)
        assert schema["function"]["description"] == "Do a sample thing."
        assert schema["function"]["parameters"]["required"] == ["target"]

    def test_schema_optional_param_not_required(self):
        from typing import Optional

        def sample(self, needed: str, maybe: Optional[int] = None):
            """x"""

        schema = ability_to_tool_schema("sample", sample)
        assert schema["function"]["parameters"]["required"] == ["needed"]
        assert schema["function"]["parameters"]["properties"]["maybe"] == {
            "type": "integer"
        }


class TestAccessGate:
    """The default-deny gate, tested in isolation (no registry needed)."""

    def _invoker(self):
        return AbilityInvoker(model_registry=None, requester_id="req")

    def test_denied_when_not_in_allowlist(self):
        assert self._invoker().is_allowed("web_search", set()) is False

    def test_allowed_when_in_allowlist(self):
        assert self._invoker().is_allowed("web_search", {"web_search"}) is True

    def test_global_denylist_overrides_allowlist(self):
        # An ability on the never-grantable set is refused even if granted.
        name = next(iter(NEVER_AGENT_INVOCABLE))
        assert self._invoker().is_allowed(name, {name}) is False

    @pytest.mark.asyncio
    async def test_invoke_denied_without_grant_before_resolution(self):
        # The gate fires before any resolution/execution: even with no registry,
        # a non-allowed ability raises AbilityAccessDenied, not a resolve error.
        invoker = self._invoker()
        with pytest.raises(AbilityAccessDenied):
            await invoker.invoke("web_search", {}, allowed=set())

    @pytest.mark.asyncio
    async def test_invoke_denied_for_denylisted_even_if_granted(self):
        invoker = self._invoker()
        name = next(iter(NEVER_AGENT_INVOCABLE))
        with pytest.raises(AbilityAccessDenied):
            await invoker.invoke(name, {}, allowed={name})


class TestArgumentCoercion:
    def test_none_arguments(self):
        assert AbilityInvoker._coerce_arguments(None) == {}

    def test_empty_string(self):
        assert AbilityInvoker._coerce_arguments("   ") == {}

    def test_json_string(self):
        assert AbilityInvoker._coerce_arguments('{"q": "x", "n": 3}') == {
            "q": "x",
            "n": 3,
        }

    def test_dict_passthrough(self):
        assert AbilityInvoker._coerce_arguments({"a": 1}) == {"a": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(ToolInvocationError):
            AbilityInvoker._coerce_arguments("{not json}")

    def test_non_object_json_raises(self):
        with pytest.raises(ToolInvocationError):
            AbilityInvoker._coerce_arguments("[1, 2, 3]")


class TestAbilityInvokerIntegration(ExtensionServerMixin):
    """Resolve and invoke real seeded abilities against a live registry."""

    @pytest.fixture(scope="module")
    def server(self):
        from fastapi.testclient import TestClient

        from conftest import CORE_COMPANION_EXTENSIONS
        from zephyrex.app import instance
        from zephyrex.pydantic2.sqlalchemy import prepare_test_registry

        prepare_test_registry()
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        prefix = (
            f"test.ability_invoker.{worker_id}" if worker_id else "test.ability_invoker"
        )
        wanted = (
            "ai_agents",
            "ai",
            "email",
            "conversations",
            "ai_prompts",
            "ai_memories",
        )
        names = list(wanted) + [c for c in CORE_COMPANION_EXTENSIONS if c not in wanted]
        app = instance(db_prefix=prefix, extensions=",".join(names))
        yield TestClient(app)

    def test_registry_has_abilities(self, model_registry):
        registry = getattr(model_registry, "extension_registry", None)
        assert registry is not None, "model_registry must expose extension_registry"

    def test_resolve_provider_ability(self, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        resolved = invoker.resolve("text_generation")
        assert resolved is not None
        assert resolved.kind == "provider"
        assert callable(resolved.method)

    def test_resolve_unknown_ability_returns_none(self, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        assert invoker.resolve("no_such_ability_xyz") is None

    def test_build_tools_only_includes_allowed_resolvable(self, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        tools = invoker.build_tools({"text_generation", "no_such_ability_xyz"})
        names = {t["function"]["name"] for t in tools}
        assert "text_generation" in names
        assert "no_such_ability_xyz" not in names  # unresolvable → omitted

    def test_build_tools_excludes_denylisted(self, model_registry):
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        denied = next(iter(NEVER_AGENT_INVOCABLE))
        tools = invoker.build_tools({denied, "text_generation"})
        names = {t["function"]["name"] for t in tools}
        assert denied not in names

    @pytest.mark.asyncio
    async def test_invoke_meta_ability_when_allowed(self, model_registry):
        # email_status is a real, local meta ability (reads env, no network).
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        result = await invoker.invoke("email_status", {}, allowed={"email_status"})
        assert result["success"] is True
        assert isinstance(result["content"], dict)
        assert result["content"]["extension"] == "email"

    @pytest.mark.asyncio
    async def test_invoke_provider_ability_without_resolver_fails_safely(
        self, model_registry
    ):
        # A permitted provider ability with no instance resolver must fail as a
        # captured tool error (never silently reach for an arbitrary instance),
        # so the turn can feed the failure back to the model instead of crashing.
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        result = await invoker.invoke(
            "text_generation",
            {"prompt": "hi"},
            allowed={"text_generation"},
        )
        assert result["success"] is False
        assert "resolver" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_invoke_unresolvable_allowed_ability_fails_safely(
        self, model_registry
    ):
        # Granted but unresolvable → captured failure, not a raised error.
        invoker = AbilityInvoker(model_registry=model_registry, requester_id="req")
        result = await invoker.invoke(
            "no_such_ability_xyz", {}, allowed={"no_such_ability_xyz"}
        )
        assert result["success"] is False
