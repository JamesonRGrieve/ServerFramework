# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evaluate small expressions written by users (a chain's conditions and
the values its steps compute) without running code.

An expression is parsed as Python and walked node by node; only a fixed set
of nodes is allowed: literals, names from the given context, key and index
lookup on mappings and sequences, comparisons, boolean and arithmetic
operators, conditional expressions, and calls to a few pure functions.
``eval``/``exec`` are never used, nothing is imported, and no attribute of
any object is reachable: ``a.b`` reads the key ``b`` of the mapping ``a``,
and no name, key or function may be a dunder.

The work is bounded: the expression's length, node count and nesting depth;
the size of every value an operator or function makes (text length plus
element count, weighed before a repetition allocates); the bits of an
integer; and the digits ``round`` may ask for. With no loops,
comprehensions or lambdas, each node is evaluated at most once. Values read
from the context are used as they are: bounding the context is the caller's.

    evaluate("len(items) > 2 and status == 'ok'", {"items": [1, 2, 3], "status": "ok"})
"""

import ast
import operator
from collections import deque
from typing import Any, Callable, Dict, Mapping, Tuple, Type

MAX_EXPRESSION_CHARACTERS = 2_000
MAX_NODES = 300
MAX_DEPTH = 32
MAX_RESULT_SIZE = 100_000
MAX_INTEGER_BITS = 512
MAX_ROUND_DIGITS = 15

NUMBERS = (int, float)
SEQUENCES = (str, list, tuple)
# Nodes that read a value that already exists (the context's, or a literal
# within the length limit); every other node makes one, and is weighed.
_READS = (ast.Name, ast.Attribute, ast.Subscript, ast.Constant)


class ExpressionError(ValueError):
    """An expression that is not allowed, or fails to evaluate."""


def _dunder(name: Any) -> bool:
    return isinstance(name, str) and name.startswith("__")


def size_of(value: Any, limit: int = MAX_RESULT_SIZE) -> int:
    """``value``'s size (text length plus element count, nested), counted up
    to just past ``limit`` and no further, so measuring is itself bounded.
    A value shared many times (``[s] * n``) counts each time it appears."""
    total = 0
    pending: deque[Any] = deque([value])
    while pending and total <= limit:
        item = pending.popleft()
        total += (
            len(item) + 1 if isinstance(item, (str, bytes, dict, list, tuple)) else 1
        )
        if total > limit:
            break
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return total


def _bounded(value: Any) -> Any:
    """``value``, unless it is too large to keep working with."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() > MAX_INTEGER_BITS:
            raise ExpressionError(f"a number is at most {MAX_INTEGER_BITS} bits")
    elif isinstance(value, (str, bytes, list, tuple, dict)):
        if size_of(value) > MAX_RESULT_SIZE:
            raise ExpressionError(f"a value's size is at most {MAX_RESULT_SIZE}")
    return value


def _numbers(left: Any, right: Any) -> bool:
    return isinstance(left, NUMBERS) and isinstance(right, NUMBERS)


def _add(left: Any, right: Any) -> Any:
    if _numbers(left, right) or (
        type(left) is type(right) and isinstance(left, SEQUENCES)
    ):
        return left + right
    raise ExpressionError("+ adds numbers, or two texts, lists or tuples")


def _multiply(left: Any, right: Any) -> Any:
    """Numbers, or a repetition (``"ab" * n``) refused before it allocates
    more than the size limit."""
    if _numbers(left, right):
        return left * right
    for sequence, count in ((left, right), (right, left)):
        if isinstance(sequence, SEQUENCES) and isinstance(count, int):
            if size_of(sequence) * max(count, 0) > MAX_RESULT_SIZE:
                raise ExpressionError(f"a value's size is at most {MAX_RESULT_SIZE}")
            return sequence * count
    raise ExpressionError("* multiplies numbers, or repeats a text or list")


def _arithmetic(apply: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    """An operator for numbers only (no ``%`` formatting of text)."""

    def numeric(left: Any, right: Any) -> Any:
        if not _numbers(left, right):
            raise ExpressionError("-, /, // and % work on numbers")
        return apply(left, right)

    return numeric


_BINARY: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: _add,
    ast.Sub: _arithmetic(operator.sub),
    ast.Mult: _multiply,
    ast.Div: _arithmetic(operator.truediv),
    ast.FloorDiv: _arithmetic(operator.floordiv),
    ast.Mod: _arithmetic(operator.mod),
}
_UNARY: Dict[type, Callable[[Any], Any]] = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}
_COMPARE: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
}


def _sum(values: Any) -> Any:
    if not isinstance(values, (list, tuple)) or not all(
        isinstance(v, NUMBERS) for v in values
    ):
        raise ExpressionError("sum adds a list of numbers")
    return sum(values)


def _round(value: Any, digits: Any = None) -> Any:
    if not isinstance(value, NUMBERS):
        raise ExpressionError("round rounds a number")
    if digits is None:
        return round(value)
    if not isinstance(digits, int) or abs(digits) > MAX_ROUND_DIGITS:
        raise ExpressionError(f"round keeps at most {MAX_ROUND_DIGITS} digits")
    return round(value, digits)


FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "len": len,
    "min": min,
    "max": max,
    "sum": _sum,
    "any": any,
    "all": all,
    "abs": abs,
    "round": _round,
    "sorted": sorted,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "strip": lambda s: str(s).strip(),
}

# What a failing operation may raise; each becomes an ExpressionError.
_EVALUATION_FAILURES: Tuple[Type[BaseException], ...] = (
    TypeError,
    ValueError,
    ArithmeticError,
    LookupError,
    RecursionError,
    MemoryError,
)


class _Evaluator:
    def __init__(self, context: Mapping[str, Any]) -> None:
        self.context = context

    def visit(self, node: ast.AST) -> Any:
        handler = getattr(self, f"_{type(node).__name__}", None)
        if handler is None:
            raise ExpressionError(
                f"{type(node).__name__} is not allowed in an expression"
            )
        value = handler(node)
        return value if isinstance(node, _READS) else _bounded(value)

    def _Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def _Constant(self, node: ast.Constant) -> Any:
        if not isinstance(node.value, (str, int, float, bool, type(None))):
            raise ExpressionError("only text, number, boolean and None literals")
        return node.value

    def _Name(self, node: ast.Name) -> Any:
        if node.id in ("True", "False", "None"):
            return {"True": True, "False": False, "None": None}[node.id]
        if _dunder(node.id) or node.id not in self.context:
            raise ExpressionError(f"{node.id!r} is not defined")
        return self.context[node.id]

    def _key(self, owner: Any, key: Any) -> Any:
        if _dunder(key):
            raise ExpressionError("dunder keys are not allowed")
        if isinstance(owner, dict):
            if key not in owner:
                raise ExpressionError(f"no key {key!r}")
            return owner[key]
        if isinstance(owner, SEQUENCES) and isinstance(key, int):
            if not -len(owner) <= key < len(owner):
                raise ExpressionError(f"index {key} is out of range")
            return owner[key]
        raise ExpressionError("only mappings and sequences can be indexed")

    def _Attribute(self, node: ast.Attribute) -> Any:
        # ``run.output`` reads a mapping's key; no object attribute is reachable.
        owner = self.visit(node.value)
        if not isinstance(owner, dict):
            raise ExpressionError(f"no key {node.attr!r}: attributes are not allowed")
        return self._key(owner, node.attr)

    def _Subscript(self, node: ast.Subscript) -> Any:
        if isinstance(node.slice, ast.Slice):
            raise ExpressionError("slices are not allowed")
        return self._key(self.visit(node.value), self.visit(node.slice))

    def _List(self, node: ast.List) -> Any:
        return [self.visit(e) for e in node.elts]

    def _Tuple(self, node: ast.Tuple) -> Any:
        return tuple(self.visit(e) for e in node.elts)

    def _Dict(self, node: ast.Dict) -> Any:
        result: Dict[Any, Any] = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                raise ExpressionError("** is not allowed")
            result[self.visit(key)] = self.visit(value)
        return result

    def _BoolOp(self, node: ast.BoolOp) -> Any:
        stop_when = not isinstance(node.op, ast.And)
        result: Any = not stop_when
        for value in node.values:
            result = self.visit(value)
            if bool(result) is stop_when:
                return result
        return result

    def _UnaryOp(self, node: ast.UnaryOp) -> Any:
        op = _UNARY.get(type(node.op))
        if op is None:
            raise ExpressionError(f"{type(node.op).__name__} is not allowed")
        return op(self.visit(node.operand))

    def _BinOp(self, node: ast.BinOp) -> Any:
        op = _BINARY.get(type(node.op))
        if op is None:
            raise ExpressionError(f"{type(node.op).__name__} is not allowed")
        return op(self.visit(node.left), self.visit(node.right))

    def _Compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self.visit(comparator)
            if not _COMPARE[type(op)](left, right):
                return False
            left = right
        return True

    def _IfExp(self, node: ast.IfExp) -> Any:
        return (
            self.visit(node.body) if self.visit(node.test) else self.visit(node.orelse)
        )

    def _Call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
            raise ExpressionError(
                f"only these functions may be called: {', '.join(sorted(FUNCTIONS))}"
            )
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed")
        return FUNCTIONS[node.func.id](*[self.visit(a) for a in node.args])


def _depth(tree: ast.AST) -> int:
    deepest = 0
    pending: deque[Tuple[ast.AST, int]] = deque([(tree, 1)])
    while pending:
        node, depth = pending.popleft()
        deepest = max(deepest, depth)
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return deepest


def parse(expression: str) -> ast.Expression:
    """``expression`` parsed and checked against the limits on its length,
    parts and nesting (no evaluation): for checking an expression when it
    is saved."""
    if not isinstance(expression, str) or not expression.strip():
        raise ExpressionError("an expression is required")
    if len(expression) > MAX_EXPRESSION_CHARACTERS:
        raise ExpressionError(
            f"an expression is at most {MAX_EXPRESSION_CHARACTERS} characters"
        )
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"not a valid expression: {exc.msg}") from None
    except (RecursionError, MemoryError):
        raise ExpressionError("an expression is nested too deeply") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise ExpressionError(f"an expression has at most {MAX_NODES} parts")
    if _depth(tree) > MAX_DEPTH:
        raise ExpressionError(f"an expression nests at most {MAX_DEPTH} deep")
    return tree


# Nodes an expression may hold (operators and contexts included): what
# ``check`` accepts without evaluating.
_ALLOWED_NODES: Tuple[type, ...] = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Attribute,
    ast.Subscript,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.IfExp,
    ast.Call,
    *_UNARY,
    *_BINARY,
    *_COMPARE,
)


def check(expression: str) -> ast.Expression:
    """``expression`` parsed and found to use only what an expression may
    (allowed parts, the listed functions, no dunder names or keys), without
    evaluating it: for refusing a bad expression when it is saved. Whether
    its names are defined is known only when it is evaluated."""
    tree = parse(expression)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Dict) and None in node.keys:
            raise ExpressionError("** is not allowed")
        if isinstance(node, ast.Name) and _dunder(node.id):
            raise ExpressionError(f"{node.id!r} is not allowed")
        if isinstance(node, ast.Attribute) and _dunder(node.attr):
            raise ExpressionError(f"{node.attr!r} is not allowed")
        if isinstance(node, ast.Constant) and not isinstance(
            node.value, (str, int, float, bool, type(None))
        ):
            raise ExpressionError("only text, number, boolean and None literals")
        if isinstance(node, ast.Subscript) and (
            isinstance(node.slice, ast.Slice)
            or isinstance(node.slice, ast.Constant)
            and _dunder(node.slice.value)
        ):
            raise ExpressionError("slices and dunder keys are not allowed")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name)
            or node.func.id not in FUNCTIONS
            or node.keywords
        ):
            raise ExpressionError(
                f"only these functions may be called: {', '.join(sorted(FUNCTIONS))}"
            )
    return tree


def evaluate(expression: str, context: Mapping[str, Any]) -> Any:
    """The value of ``expression`` with ``context``'s names defined; an
    ExpressionError when it is not allowed or fails."""
    tree = parse(expression)
    try:
        return _Evaluator(context).visit(tree)
    except ExpressionError:
        raise
    except _EVALUATION_FAILURES as exc:
        raise ExpressionError(f"{type(exc).__name__}: {exc}") from None
