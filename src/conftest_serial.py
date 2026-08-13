"""xdist scheduler plugin: run Efficiency_test.py last on one quiet worker.

Extends LoadFileScheduling. The serial scope is placed at the END of the
workqueue so it's the last scope assigned. Because loadfile sends all
tests from one file to one worker, the efficiency benchmarks land on a
single worker after its parallel scopes complete — the other workers
are idle by then.
"""

from __future__ import annotations

import pytest
from xdist.scheduler.loadfile import LoadFileScheduling


SERIAL_SCOPE = "__serial_last__"


class SerialLastScheduler(LoadFileScheduling):

    def _split_scope(self, nodeid: str) -> str:
        if "Efficiency_test" in nodeid:
            return SERIAL_SCOPE
        return super()._split_scope(nodeid)

    def schedule(self) -> None:
        assert self.collection_is_completed

        if self.collection is not None:
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
        serial: dict[str, bool] = {}

        for nodeid in self.collection:
            scope = self._split_scope(nodeid)
            if scope == SERIAL_SCOPE:
                serial[nodeid] = False
            else:
                parallel.setdefault(scope, {})[nodeid] = False

        for scope, nodeids in sorted(parallel.items(), key=lambda x: -len(x[1])):
            self.workqueue[scope] = nodeids

        if serial:
            self.workqueue[SERIAL_SCOPE] = serial

        for node in self.nodes:
            self._reschedule(node)


@pytest.hookimpl(trylast=True)
def pytest_xdist_make_scheduler(config, log):
    if config.getvalue("dist") == "loadfile":
        return SerialLastScheduler(config, log)
    return None
