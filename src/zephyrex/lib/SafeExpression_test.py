# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safe expressions: what a chain's condition or computed value may
compute, and the code paths it must never reach (imports, attribute
escapes, dunder walks, eval-style builtins) or the sizes and work it may
never take on.

Holes the draft left, each with a test below that fails on it: ``%``
formatted text (``'%*d' % (10**9, 1)`` allocates a gigabyte before any size
check); ``round`` with any digits (``round(1, -10**12)`` never returns);
nesting with no depth limit; ``sum(lists, [])`` concatenation; a repetition
of a shared large value passing its length check; dunder names and keys
read from the context; and TypeError/OverflowError escaping as themselves
instead of ExpressionError."""

import pytest

from zephyrex.lib.SafeExpression import (
    MAX_DEPTH,
    MAX_EXPRESSION_CHARACTERS,
    MAX_RESULT_SIZE,
    ExpressionError,
    evaluate,
    check,
    parse,
    size_of,
)

CONTEXT = {
    "status": "ok",
    "items": [1, 2, 3],
    "run": {"output": {"score": 0.9, "tags": ["a", "b"]}},
    "name": "Ada",
}


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("status == 'ok'", True),
        ("len(items) > 2 and status != 'failed'", True),
        ("run.output.score >= 0.5", True),
        ("run['output']['tags'][1]", "b"),
        ("'a' in run.output.tags", True),
        ("items[0] + items[-1]", 4),
        ("sum(items) / len(items)", 2.0),
        ("'big' if max(items) > 2 else 'small'", "big"),
        ("upper(name) + '!'", "ADA!"),
        ("not (1 < 2 < 3)", False),
        ("sorted([3, 1, 2])", [1, 2, 3]),
        ("{'k': len(name)}", {"k": 3}),
        ("None is None", True),
        ("items + [4]", [1, 2, 3, 4]),
        ("10 % 4 + 7 // 2 - 1", 4),
        ("round(2.567, 2)", 2.57),
        ("0 or '' or 'last'", "last"),
        ("1 and 0 and 2", 0),
    ],
)
def test_allowed(expression, expected):
    assert evaluate(expression, CONTEXT) == expected


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "eval('1')",
        "exec('x = 1')",
        "status.__class__",
        "items.__class__.__mro__",
        "().__class__.__bases__[0].__subclasses__()",
        "(lambda: 1)()",
        "[x for x in items]",
        "status.upper()",
        "items[0:2]",
        "{**run}",
        "2 ** 100000",
        "f'{status}'",
        "unknown_name",
        "len(items, key=1)",
        "len(*items)",
        "(x := 1)",
        "{1, 2}",
        "b'bytes'",
        "1j",
    ],
)
def test_refused(expression):
    with pytest.raises(ExpressionError):
        evaluate(expression, CONTEXT)
    if expression != "unknown_name":
        # Refused when saved, too, without evaluating (whether a name is
        # defined is known only when it is evaluated).
        with pytest.raises(ExpressionError):
            check(expression)


def test_check_accepts_what_evaluates():
    for expression in (
        "status == 'ok' and len(items) > 2",
        "run.output.tags[0] + upper(name)",
        "'big' if max(items) > 2 else 'small'",
        "{'k': [1, (2, 3)]} != None",
        "name == '__not_a_key__'",
    ):
        check(expression)
    with pytest.raises(ExpressionError):
        check("run['__class__']")


def test_sizes():
    with pytest.raises(ExpressionError):
        evaluate("'x' * 10000000", {})
    with pytest.raises(ExpressionError):
        evaluate("[0] * (" + str(MAX_RESULT_SIZE + 1) + ")", {})
    with pytest.raises(ExpressionError):
        parse("1 + " * MAX_EXPRESSION_CHARACTERS + "1")
    with pytest.raises(ExpressionError):
        evaluate(" + ".join(["1"] * 400), {})
    with pytest.raises(ExpressionError):
        evaluate("name + name", {"name": "x" * MAX_RESULT_SIZE})


def test_failures_are_expression_errors():
    for expression in (
        "1 / 0",
        "'a' + 1",
        "items[10]",
        "run.missing",
        "int('x')",
        "int(float('nan'))",
        "[1] in {1: 2}",
    ):
        with pytest.raises(ExpressionError):
            evaluate(expression, CONTEXT)


def test_parse_checks_without_evaluating():
    parse("unknown_name > 1")  # names are resolved only when evaluated
    with pytest.raises(ExpressionError):
        parse("if x:")


def test_context_values_are_used_as_they_are():
    """Reading a large value is not making one: only what an expression
    builds is weighed (the caller bounds its context)."""
    big = "x" * (MAX_RESULT_SIZE * 2)
    assert evaluate("len(big) > 0 and big[0] == 'x'", {"big": big}) is True


def test_size_of_stops_counting_past_its_limit():
    huge = [[0] * 1000] * 1000
    assert MAX_RESULT_SIZE < size_of(huge) < MAX_RESULT_SIZE + 2000
    assert size_of({"ab": [1, "cd"]}) == 2 + 3 + 3 + 1 + 3


class TestHolesInTheDraft:
    def test_text_is_not_percent_formatted(self):
        with pytest.raises(ExpressionError):
            evaluate("'%s' % name", CONTEXT)

    def test_round_digits_are_bounded(self):
        with pytest.raises(ExpressionError):
            evaluate("round(1.5, 100)", {})
        with pytest.raises(ExpressionError):
            evaluate("round(1, -1000000000000)", {})

    def test_nesting_is_bounded(self):
        assert evaluate("-" * (MAX_DEPTH // 2) + "1", {}) in (1, -1)
        with pytest.raises(ExpressionError):
            evaluate("-" * 100 + "1", {})

    def test_sum_adds_numbers_only(self):
        with pytest.raises(ExpressionError):
            evaluate("sum([[1], [2]], [])", {})
        with pytest.raises(ExpressionError):
            evaluate("sum(['a', 'b'])", {})

    def test_a_repeated_large_value_is_weighed_whole(self):
        with pytest.raises(ExpressionError):
            evaluate("[name * 1000] * 1000", CONTEXT)

    def test_dunder_names_and_keys_are_refused(self):
        with pytest.raises(ExpressionError):
            evaluate("__builtins__", {"__builtins__": {"open": 1}})
        with pytest.raises(ExpressionError):
            evaluate("d.__init__", {"d": {"__init__": 1}})
        with pytest.raises(ExpressionError):
            evaluate("d['__init__']", {"d": {"__init__": 1}})

    @pytest.mark.parametrize(
        "expression",
        ["{[1]: 2}", "round(float('inf'))"],
    )
    def test_failures_never_escape_as_other_errors(self, expression):
        with pytest.raises(ExpressionError):
            evaluate(expression, {})
