# SPDX-License-Identifier: AGPL-3.0-or-later
"""A local model in use is never closed under it: ``run`` pins the model
from lookup through the work, so another request that needs room, or an
unload, landing between the two neither evicts nor closes it. Exercised
with real threads and a stand-in model (no model is downloaded)."""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Optional

import pytest

from zephyrex.extensions.ExternalErrors import TransientExternalError
from zephyrex.extensions.local_ai import LocalAI
from zephyrex.extensions.local_ai.LocalAI import AbstractLocalAIProvider
from zephyrex.extensions.local_ai.LocalModels import LoadedModel, LoadedModels

WAIT_SECONDS = 5.0


class StandIn:
    """A loaded model that knows whether it was closed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = threading.Event()

    def answer(self) -> str:
        return "used after close" if self.closed.is_set() else self.name


def close(model: StandIn) -> None:
    model.closed.set()


class HeldModel(AbstractLocalAIProvider):
    """A local provider whose models are stand-ins, with a hook run in the
    window between finding a model and using it: where another request
    lands in the race."""

    name: ClassVar[str] = "held_model_test"
    window: ClassVar[Optional[Any]] = None
    timeout: ClassVar[float] = WAIT_SECONDS

    @classmethod
    def load_model(cls, instance: Any, directory: Path) -> Any:
        return StandIn(instance.name)

    @classmethod
    def timeout_seconds(cls, instance: Any) -> float:
        return cls.timeout

    @classmethod
    async def loaded(cls, instance: Any) -> LoadedModel:
        found = await super().loaded(instance)
        if cls.window is not None:
            await asyncio.to_thread(cls.window)
        return found


@pytest.fixture
def models(monkeypatch, set_env) -> LoadedModels:
    """A fresh set of loaded models, room for one, that ``run`` uses."""
    set_env("LOCAL_AI_MAX_LOADED_MODELS", "1")
    fresh = LoadedModels()
    monkeypatch.setattr(LocalAI, "LOADED_MODELS", fresh)
    monkeypatch.setattr(HeldModel, "window", None)
    return fresh


def preload(models: LoadedModels, key: str) -> StandIn:
    model = StandIn(key)
    models.load(key, key, HeldModel.name, lambda: model, close)
    return model


def in_a_thread(target: Any) -> None:
    """``target`` run on another thread, to its end."""
    thread = threading.Thread(target=target)
    thread.start()
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive()


async def test_a_request_needing_room_does_not_evict_a_model_about_to_be_used(
    models,
):
    a = preload(models, "a")
    other = StandIn("b")
    HeldModel.window = lambda: in_a_thread(
        lambda: models.load("b", "b", HeldModel.name, lambda: other, close)
    )

    answer = await HeldModel.run(SimpleNamespace(id="a", name="a"), StandIn.answer)

    assert answer == "a"
    assert not a.closed.is_set()
    assert models.get("a") is not None and models.get("b") is not None


async def test_an_unload_waits_for_the_model_about_to_be_used(models):
    a = preload(models, "a")
    HeldModel.window = lambda: in_a_thread(lambda: models.unload("a"))

    answer = await HeldModel.run(SimpleNamespace(id="a", name="a"), StandIn.answer)

    assert answer == "a"
    assert a.closed.is_set() and models.get("a") is None


async def test_a_call_past_its_deadline_holds_the_model_until_its_work_ends(
    models, monkeypatch
):
    monkeypatch.setattr(LocalAI, "DEADLINE_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(HeldModel, "timeout", 0.05)
    a = preload(models, "a")
    finish = threading.Event()
    answered: list[str] = []

    def slow(model: StandIn) -> str:
        finish.wait(WAIT_SECONDS)
        answered.append(model.answer())
        return answered[-1]

    with pytest.raises(TransientExternalError):
        await HeldModel.run(SimpleNamespace(id="a", name="a"), slow)

    # The abandoned work still runs on the model: it may not be closed
    # under it, by an unload or by room made for another.
    assert models.unload("a")
    models.load("b", "b", HeldModel.name, lambda: StandIn("b"), close)
    assert not a.closed.is_set()
    finish.set()
    assert a.closed.wait(WAIT_SECONDS)
    assert answered == ["a"]


async def test_a_new_model_is_loaded_pinned(models, monkeypatch, tmp_path):
    """A model loaded for a call is pinned as it enters memory, so room
    made for another before the call uses it passes it over."""

    async def files(instance: Any) -> Path:
        return Path(tmp_path)

    monkeypatch.setattr(HeldModel, "ensure_files", files)
    HeldModel.window = lambda: in_a_thread(
        lambda: models.load("b", "b", HeldModel.name, lambda: StandIn("b"), close)
    )

    answer = await HeldModel.run(SimpleNamespace(id="a", name="a"), StandIn.answer)

    assert answer == "a"
    found = models.get("a")
    assert found is not None and found.pins == 0
