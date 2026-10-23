"""Safe calculator tool (AST-whitelisted; never uses eval/exec)."""

from __future__ import annotations

import ast
import json
import math
import operator
import re
from typing import Any

MAX_EXPONENT = 1000
MAX_EXPR_LEN = 500
# "383,285" -> "383285", but NOT the argument comma in "f(1, 2)" or "f(1,234567)".
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def pct_change(old: float, new: float) -> float:
    """Percent change from old to new, in percent (e.g. 12.5 means +12.5%)."""
    return (new - old) / abs(old) * 100.0


def cagr(begin: float, end: float, years: float) -> float:
    """Compound annual growth rate, in percent."""
    return ((end / begin) ** (1.0 / years) - 1.0) * 100.0


_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "pct_change": pct_change,
    "cagr": cagr,
}


class CalcError(ValueError):
    pass


def safe_eval(expression: str) -> float:
    if len(expression) > MAX_EXPR_LEN:
        raise CalcError("expression too long")
    try:
        tree = ast.parse(_THOUSANDS.sub("", expression.strip()), mode="eval")
    except SyntaxError as e:
        raise CalcError(f"syntax error: {e.msg}") from e
    result = _eval(tree.body)
    if isinstance(result, bool) or not isinstance(result, int | float) or not math.isfinite(result):
        raise CalcError("result is not a finite number")
    return float(result)


def _eval(node: ast.AST) -> Any:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalcError("exponent too large")
        try:
            return _BIN[type(node.op)](left, right)
        except ZeroDivisionError:
            raise CalcError("division by zero") from None
        except OverflowError:
            raise CalcError("numeric overflow") from None
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        try:
            return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
        except (ValueError, ZeroDivisionError, OverflowError, TypeError) as e:
            raise CalcError(f"{node.func.id}: {e}") from e
    raise CalcError(f"unsupported expression element: {type(node).__name__}")


# ---------------------------------------------------------------- MCP server wiring (mcp 2.x)
def build_server():
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer("agentlens-calc")

    @mcp.tool()
    def calculate(expression: str) -> str:
        """Evaluate an arithmetic expression. Supports + - * / // % **, parentheses, abs, round,
        min, max, sqrt, log, pct_change(old, new) -> percent, cagr(begin, end, years) -> percent.
        Numbers may contain thousands separators (383,285). Put a SPACE after commas that separate
        function arguments, e.g. pct_change(100, 120). Use this for ALL arithmetic."""
        return json.dumps({"expression": expression, "result": safe_eval(expression)})

    return mcp


def main() -> None:
    from agentlens_tools._run import run_server

    run_server(build_server())


if __name__ == "__main__":
    main()
