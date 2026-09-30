# SPDX-License-Identifier: AGPL-3.0-or-later
"""A fixture wider than one test must not hold environment patches open.

The suite runs with ``--order-dependencies``, so tests of different modules
interleave on a worker. A module- or class-scoped fixture that yields inside
``pytest.MonkeyPatch.context()`` keeps its patches (DATABASE_NAME,
DATABASE_PATH, SEED_DATA=false, ...) for its whole life, and every other
module's app built meanwhile boots against its database, unseeded. The app
captures its database settings when built, so the patch is needed only for
the build: build inside the context, yield after it.
"""

import ast
from pathlib import Path
from typing import Iterator, List, Optional

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_WIDE_SCOPES = frozenset({"module", "class", "package", "session"})


def _fixture_scope(function: ast.FunctionDef) -> Optional[str]:
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            for keyword in decorator.keywords:
                if keyword.arg == "scope" and isinstance(keyword.value, ast.Constant):
                    return str(keyword.value.value)
    return None


def _is_monkeypatch_context(item: ast.withitem) -> bool:
    call = item.context_expr
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "context"
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "MonkeyPatch"
    )


def _yields_inside_patch(function: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.With)
        and any(_is_monkeypatch_context(item) for item in node.items)
        and any(isinstance(inner, ast.Yield) for inner in ast.walk(node))
        for node in ast.walk(function)
    )


def _test_sources() -> Iterator[Path]:
    yield from _SOURCE_ROOT.rglob("*_test.py")
    yield from _SOURCE_ROOT.rglob("conftest.py")


def offenders(source: str, name: str) -> List[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.FunctionDef)
            and _fixture_scope(node) in _WIDE_SCOPES
            and _yields_inside_patch(node)
        ):
            found.append(f"{name}:{node.lineno} {node.name}")
    return found


def test_no_wide_fixture_yields_inside_an_environment_patch():
    found = [
        offender
        for path in _test_sources()
        for offender in offenders(path.read_text(), str(path.relative_to(_SOURCE_ROOT)))
    ]
    assert found == []


def test_the_check_catches_the_leaking_shape():
    leaking = (
        "@pytest.fixture(scope='module')\n"
        "def app():\n"
        "    with pytest.MonkeyPatch.context() as patch:\n"
        "        patch.setenv('SEED_DATA', 'false')\n"
        "        yield build()\n"
    )
    contained = leaking.replace(
        "        yield build()", "        app = build()\n    yield app"
    )
    function_scoped = leaking.replace("(scope='module')", "")
    assert offenders(leaking, "leaking") == ["leaking:2 app"]
    assert offenders(contained, "contained") == []
    assert offenders(function_scoped, "function_scoped") == []
