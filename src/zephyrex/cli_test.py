"""Pure-utility argparse tests for ``zephyrex.cli``."""

from __future__ import annotations

import re

import pytest

from zephyrex.cli import main


def test_version_prints_and_returns_zero(capsys):
    rc = main(["version"])
    captured = capsys.readouterr()
    assert rc == 0
    combined = captured.out + captured.err
    # Either "zephyrex <X.Y.Z>" or "zephyrex version: unknown".
    assert re.search(r"(\d+\.\d+(?:\.\d+)?|unknown)", combined)


def test_run_help_returns_zero_and_mentions_extensions(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--extensions" in captured.out


def test_no_subcommand_returns_nonzero(capsys):
    rc = main([])
    assert rc != 0
    captured = capsys.readouterr()
    assert "subcommand" in captured.err.lower() or "usage" in captured.err.lower()


def test_run_does_not_start_uvicorn(monkeypatch):
    captured_kwargs = {}

    def fake_run(**kwargs):
        captured_kwargs.update(kwargs)

    import zephyrex as _sf

    monkeypatch.setattr(_sf, "run", fake_run)

    rc = main(["run"])
    assert rc == 0
    assert captured_kwargs.get("extensions") is None
    assert captured_kwargs.get("extensions_path") is None
    assert captured_kwargs.get("workers") is None
    assert captured_kwargs.get("reload") is None
    assert captured_kwargs.get("log_level") is None
    assert captured_kwargs.get("proxy_headers") is True


def test_run_forwards_extensions_and_port(monkeypatch):
    captured_kwargs = {}

    def fake_run(**kwargs):
        captured_kwargs.update(kwargs)

    import zephyrex as _sf

    monkeypatch.setattr(_sf, "run", fake_run)

    rc = main(["run", "--extensions", "payment", "--port", "9000"])
    assert rc == 0
    assert captured_kwargs["extensions"] == "payment"
    assert captured_kwargs["port"] == 9000
