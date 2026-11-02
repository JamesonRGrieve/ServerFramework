# SPDX-License-Identifier: AGPL-3.0-or-later
"""An event stream tells proxies not to compress, buffer or cache it."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from zephyrex.lib.EventStream import event_stream


def test_an_event_stream_is_marked_untransformable_and_unbuffered():
    app = FastAPI()

    @app.get("/events")
    def events():
        return event_stream(iter([": connected\n\n", "data: {}\n\n"]))

    answer = TestClient(app).get("/events", headers={"Accept-Encoding": "gzip"})
    assert answer.headers["content-type"].startswith("text/event-stream")
    assert answer.headers["cache-control"] == "no-cache, no-transform"
    assert answer.headers["x-accel-buffering"] == "no"
    assert "content-encoding" not in answer.headers
    assert answer.text == ": connected\n\ndata: {}\n\n"
