# SPDX-License-Identifier: AGPL-3.0-or-later
"""Symbolic mathematics on untrusted text, safely.

Text is never handed to ``sympy.sympify`` or ``parse_expr``: both
``eval`` it, so ``__import__('os')…`` would run. It is parsed with
``ast`` and only a whitelist is turned into SymPy objects: numbers,
``+ - * / % **``, the constants ``pi``, ``e``, ``I`` and ``oo``, a fixed
set of functions, and variables. A numeric power whose result would run
past ``MAX_DIGITS`` digits is refused before it is computed.

SymPy can still spend unbounded time or memory on a short, valid input
(``integrate(exp(x**x))``), so the work runs in a separate worker
process with an address-space ceiling, and a call that outlives
``SYMBOLIC_TIMEOUT_SECONDS`` kills the worker; the next call starts
another.
"""

import ast
import json
import math
import os
import re
import select
import subprocess
import sys
import threading
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Tuple

import sympy

MAX_EXPRESSION_LENGTH = 500
MAX_DIGITS = 10_000
MAX_VARIABLES = 10
DECIMAL_DIGITS = 15
SYMBOLIC_TIMEOUT_SECONDS = 10.0
WORKER_MEMORY_BYTES = 2 * 1024**3
VARIABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,15}$")

_CONSTANTS: Dict[str, Any] = {
    "pi": sympy.pi,
    "e": sympy.E,
    "E": sympy.E,
    "I": sympy.I,
    "oo": sympy.oo,
    "inf": sympy.oo,
}
# name -> (SymPy function, allowed argument counts)
_FUNCTIONS: Dict[str, Tuple[Callable[..., Any], Tuple[int, ...]]] = {
    "sin": (sympy.sin, (1,)),
    "cos": (sympy.cos, (1,)),
    "tan": (sympy.tan, (1,)),
    "asin": (sympy.asin, (1,)),
    "acos": (sympy.acos, (1,)),
    "atan": (sympy.atan, (1,)),
    "sinh": (sympy.sinh, (1,)),
    "cosh": (sympy.cosh, (1,)),
    "tanh": (sympy.tanh, (1,)),
    "exp": (sympy.exp, (1,)),
    "log": (sympy.log, (1, 2)),
    "ln": (sympy.log, (1,)),
    "sqrt": (sympy.sqrt, (1,)),
    "abs": (sympy.Abs, (1,)),
    "floor": (sympy.floor, (1,)),
    "ceil": (sympy.ceiling, (1,)),
}
_BINARY: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: sympy.Mod(a, b),
}


class RefusedInput(ValueError):
    """Text outside the whitelist, or beyond a limit."""


def _power(base: Any, exponent: Any) -> Any:
    """``base ** exponent``, refused when both are numbers and the result
    would run past ``MAX_DIGITS`` digits."""
    if base.is_Number and exponent.is_Number and abs(base) > 1:
        digits = float(abs(exponent)) * math.log10(float(abs(base)))
        if digits > MAX_DIGITS:
            raise RefusedInput(f"a power that large (~{digits:.0f} digits) is refused")
    return base**exponent


def _node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RefusedInput(f"{value!r} is not a number")
        return sympy.Integer(value) if isinstance(value, int) else sympy.Float(value)
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        if node.id in _FUNCTIONS or not VARIABLE_NAME.match(node.id):
            raise RefusedInput(f"{node.id!r} cannot be a variable")
        return sympy.Symbol(node.id)
    if isinstance(node, ast.BinOp):
        left, right = _node(node.left), _node(node.right)
        if isinstance(node.op, ast.Pow):
            return _power(left, right)
        operation = _BINARY.get(type(node.op))
        if operation is None:
            raise RefusedInput(f"operator {type(node.op).__name__} is not allowed")
        return operation(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        raise RefusedInput(f"operator {type(node.op).__name__} is not allowed")
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name not in _FUNCTIONS:
            raise RefusedInput(f"function {name or '<expression>'!r} is not allowed")
        function, arities = _FUNCTIONS[name]
        if node.keywords or len(node.args) not in arities:
            raise RefusedInput(
                f"{name} takes {' or '.join(map(str, arities))} argument(s)"
            )
        return function(*(_node(arg) for arg in node.args))
    raise RefusedInput(f"{type(node).__name__} is not allowed in an expression")


def parse_expression(text: str) -> Any:
    """The SymPy expression ``text`` spells (``^`` is a power)."""
    if len(text) > MAX_EXPRESSION_LENGTH:
        raise RefusedInput(
            f"an expression is at most {MAX_EXPRESSION_LENGTH} characters"
        )
    try:
        tree = ast.parse(text.replace("^", "**").strip(), mode="eval")
    except SyntaxError as exc:
        raise RefusedInput(f"{text!r} is not an expression") from exc
    expression = _node(tree.body)
    if len(getattr(expression, "free_symbols", ())) > MAX_VARIABLES:
        raise RefusedInput(f"at most {MAX_VARIABLES} variables")
    return expression


def parse_equation(text: str) -> Any:
    """``lhs = rhs`` (or ``==``) as an equation; a bare expression is
    taken as equal to zero."""
    sides = text.replace("==", "=").split("=")
    if len(sides) == 1:
        return sympy.Eq(parse_expression(sides[0]), 0)
    if len(sides) == 2:
        return sympy.Eq(parse_expression(sides[0]), parse_expression(sides[1]))
    raise RefusedInput("an equation has one '='")


def variable(name: str) -> Any:
    if not VARIABLE_NAME.match(name) or name in _CONSTANTS or name in _FUNCTIONS:
        raise RefusedInput(f"{name!r} cannot be a variable")
    return sympy.Symbol(name)


def _solve_for(equation: Any, name: Optional[str]) -> Any:
    if name:
        return variable(name)
    unknowns = sorted(equation.free_symbols, key=str)
    if len(unknowns) != 1:
        raise RefusedInput("name the variable to solve for")
    return unknowns[0]


def calculate(expression: str) -> Dict[str, Any]:
    value = parse_expression(expression)
    if value.free_symbols:
        raise RefusedInput("calculate takes numbers only; solve finds unknowns")
    return {"result": str(value), "decimal": str(value.evalf(DECIMAL_DIGITS))}


def solve(equation: str, name: Optional[str]) -> Dict[str, Any]:
    parsed = parse_equation(equation)
    unknown = _solve_for(parsed, name)
    return {
        "variable": str(unknown),
        "solutions": [str(found) for found in sympy.solve(parsed, unknown)],
    }


def differentiate(expression: str, name: str, order: int) -> Dict[str, Any]:
    return {
        "result": str(sympy.diff(parse_expression(expression), variable(name), order))
    }


def integrate(
    expression: str, name: str, lower: Optional[str], upper: Optional[str]
) -> Dict[str, Any]:
    integrand, symbol = parse_expression(expression), variable(name)
    if lower is not None and upper is not None:
        result = sympy.integrate(
            integrand, (symbol, parse_expression(lower), parse_expression(upper))
        )
    else:
        result = sympy.integrate(integrand, symbol)
    # SymPy hands back the Integral itself when it finds no closed form.
    return {"result": str(result), "evaluated": not result.has(sympy.Integral)}


OPERATIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "calculate": calculate,
    "solve": solve,
    "differentiate": differentiate,
    "integrate": integrate,
}


def answer(operation: str, args: List[Any]) -> Tuple[str, Any]:
    """``("ok", result)``, ``("refused", reason)`` or ``("error",
    exception name)`` for one request."""
    if operation not in OPERATIONS:
        return ("error", f"unknown operation {operation!r}")
    try:
        return ("ok", OPERATIONS[operation](*args))
    except MemoryError:
        return ("refused", "the computation needs too much memory")
    except (ValueError, ArithmeticError, NotImplementedError, TypeError) as exc:
        # RefusedInput, and SymPy declining the input it was given.
        return ("refused", str(exc) or type(exc).__name__)
    except Exception as exc:  # The worker must outlive a SymPy defect.
        return ("error", type(exc).__name__)


def serve(requests: BinaryIO, replies: BinaryIO) -> None:
    """The worker: one JSON request per line in, one reply per line out,
    until its input closes."""
    for line in requests:
        request = json.loads(line)
        status, value = answer(request["operation"], request["args"])
        replies.write(json.dumps([status, value]).encode() + b"\n")
        replies.flush()


class SymbolicTimeout(Exception):
    """The worker did not answer in time and was killed."""


class SymbolicWorker:
    """One worker process (a fresh interpreter, so nothing of this
    process's ``__main__`` runs in it), serving one request at a time."""

    def __init__(self, timeout_seconds: float = SYMBOLIC_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen[bytes]] = None

    def _started(self) -> subprocess.Popen[bytes]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        # By file path: the worker imports only SymPy, never the framework.
        self._process = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        return self._process

    def stop(self) -> None:
        if self._process is not None:
            self._process.kill()
            self._process.wait()
            for stream in (self._process.stdin, self._process.stdout):
                if stream is not None:
                    stream.close()
        self._process = None

    def run(self, operation: str, *args: Any) -> Tuple[str, Any]:
        """The worker's reply (see ``answer``); a blocking call."""
        with self._lock:
            process = self._started()
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("the worker was started without pipes")
            request = json.dumps({"operation": operation, "args": list(args)})
            try:
                process.stdin.write(request.encode() + b"\n")
                process.stdin.flush()
                ready, _, _ = select.select(
                    [process.stdout], [], [], self.timeout_seconds
                )
                line = process.stdout.readline() if ready else None
            except (BrokenPipeError, OSError):
                line = b""
            if line is None:
                self.stop()
                raise SymbolicTimeout(operation)
            if not line:
                # The worker exited (killed for memory, or it failed to start).
                self.stop()
                return ("error", "the worker exited")
            status, value = json.loads(line)
            return str(status), value


def limit_memory() -> None:
    """An address-space ceiling for this process, so a computation that
    would exhaust the host fails inside the worker (POSIX only)."""
    try:
        import resource
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_AS, (WORKER_MEMORY_BYTES, WORKER_MEMORY_BYTES))


if __name__ == "__main__":
    limit_memory()
    serve(sys.stdin.buffer, sys.stdout.buffer)
