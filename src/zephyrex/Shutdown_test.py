# SPDX-License-Identifier: AGPL-3.0-or-later
"""SIGTERM stops the server.

Under ``zephyrex.run`` uvicorn builds the app inside ``serve``, after it has
installed its own SIGTERM handler, and the app's handler replaced it: SIGTERM
only marked the app draining, and the server ran on until SIGKILL (found by
the client's smoke test against 0.0.1a2).
"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import FrameType
from typing import List, Optional

import httpx
import pytest
from fastapi import FastAPI

pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")

BOOT_TIMEOUT_SECONDS = 90
EXIT_TIMEOUT_SECONDS = 15
POLL_SECONDS = 0.5

_SERVER = """
import sys
from zephyrex import run
run(extensions="", host="127.0.0.1", port=int(sys.argv[1]), workers=1, reload=False)
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_app_hands_sigterm_on_to_the_handler_it_replaced():
    from zephyrex.app import install_sighup_handler

    received: List[int] = []

    def earlier(signum: int, frame: Optional[FrameType]) -> None:
        received.append(signum)

    original = signal.signal(signal.SIGTERM, earlier)
    try:
        app = FastAPI()
        install_sighup_handler(app)
        os.kill(os.getpid(), signal.SIGTERM)
        assert received == [signal.SIGTERM]
        assert app.state.draining is True
    finally:
        signal.signal(signal.SIGTERM, original)


def test_a_running_server_exits_on_sigterm(tmp_path: Path):
    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTEST") and key != "DATABASE_PATH"
    }
    env.update(
        {
            "DATABASE_TYPE": "sqlite",
            "DATABASE_PATH": str(tmp_path),
            "DATABASE_NAME": "shutdown",
            "JWT_SECRET": "shutdown-test-secret-32-bytes-or-more-aa",
        }
    )
    server = subprocess.Popen(
        [sys.executable, "-c", _SERVER, str(port)],
        cwd=tempfile.gettempdir(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            assert server.poll() is None, "the server exited before answering"
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(POLL_SECONDS)
        else:
            pytest.fail("the server never answered /health")

        server.send_signal(signal.SIGTERM)
        server.wait(timeout=EXIT_TIMEOUT_SECONDS)
    finally:
        if server.poll() is None:
            server.kill()
            server.wait()
