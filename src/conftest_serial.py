"""xdist scheduler plugin: run Efficiency_test.py last on one quiet worker.

Extends LoadFileScheduling. Efficiency tests are held back from the
workqueue until all parallel scopes have been fully distributed and
completed. This ensures the benchmarks run on a truly quiet system,
not merely on the first worker that finishes its scopes.
"""

from __future__ import annotations

import pytest
from xdist.scheduler.loadfile import LoadFileScheduling


SERIAL_SCOPE = "__serial_last__"


class SerialLastScheduler(LoadFileScheduling):

    def __init__(self, config, log):
        super().__init__(config, log)
        self._serial_held: dict[str, bool] = {}
        self._serial_released: bool = False

    def _split_scope(self, nodeid: str) -> str:
        if "Efficiency_test" in nodeid:
            return SERIAL_SCOPE
        return super()._split_scope(nodeid)

    def schedule(self) -> None:
        assert self.collection_is_completed

        if self.collection is not None:
            self._try_release_serial()
            for node in self.nodes:
                self._reschedule(node)
            return

        if not self._check_nodes_have_same_collection():
            self.log("**Different tests collected, aborting run**")
            return

        self.collection = list(next(iter(self.registered_collections.values())))
        if not self.collection:
            return

        parallel: dict[str, dict[str, bool]] = {}

        for nodeid in self.collection:
            scope = self._split_scope(nodeid)
            if scope == SERIAL_SCOPE:
                self._serial_held[nodeid] = False
            else:
                parallel.setdefault(scope, {})[nodeid] = False

        for scope, nodeids in sorted(parallel.items(), key=lambda x: -len(x[1])):
            self.workqueue[scope] = nodeids

        if not parallel and self._serial_held:
            self.workqueue[SERIAL_SCOPE] = self._serial_held
            self._serial_released = True

        for node in self.nodes:
            self._reschedule(node)

    def _try_release_serial(self) -> None:
        if self._serial_released or not self._serial_held:
            return
        if any(k != SERIAL_SCOPE for k in self.workqueue):
            return
        if any(node.pending for node in self.nodes):
            return
        self.workqueue[SERIAL_SCOPE] = self._serial_held
        self._serial_released = True


@pytest.hookimpl(trylast=True)
def pytest_xdist_make_scheduler(config, log):
    if config.getvalue("dist") == "loadfile":
        return SerialLastScheduler(config, log)
    return None
