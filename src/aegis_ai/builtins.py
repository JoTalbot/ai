"""Small built-in tools suitable for local demos."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping
from typing import Any

from aegis_ai.models import RiskLevel, ToolSpec


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "abs": abs,
    "ceil": math.ceil,
    "cos": math.cos,
    "exp": math.exp,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "max": max,
    "min": min,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}
_CONSTANTS = {"e": math.e, "pi": math.pi}


def calculate(expression: str) -> int | float:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    if len(expression) > 256:
        raise ValueError("expression is too long")

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("expression is not valid arithmetic") from exc

    if len(list(ast.walk(tree))) > 64:
        raise ValueError("expression is too complex")

    result = _evaluate(tree.body)
    if not isinstance(result, (int, float)) or isinstance(result, bool):
        raise ValueError("expression must produce a number")
    try:
        finite = math.isfinite(result)
    except OverflowError as exc:
        raise ValueError("result is outside the supported numeric range") from exc
    if not finite:
        raise ValueError("result must be finite")
    return result


def _evaluate(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric constants are allowed")
        try:
            finite = math.isfinite(node.value)
        except OverflowError as exc:
            raise ValueError("numeric constant is too large") from exc
        if not finite:
            raise ValueError("numeric constants must be finite")
        return node.value

    if isinstance(node, ast.Name):
        if node.id not in _CONSTANTS:
            raise ValueError(f"unknown constant: {node.id}")
        return _CONSTANTS[node.id]

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent magnitude must not exceed 100")
        return _BINARY_OPERATORS[type(node.op)](left, right)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        function = _FUNCTIONS.get(node.func.id)
        if function is None or node.keywords:
            raise ValueError("function is not allowed")
        if len(node.args) > 8:
            raise ValueError("too many function arguments")
        return function(*[_evaluate(argument) for argument in node.args])

    raise ValueError("expression contains an unsupported operation")


def calculator_tool() -> ToolSpec:
    async def handler(arguments: Mapping[str, Any]) -> dict[str, int | float]:
        return {"result": calculate(arguments["expression"])}

    return ToolSpec(
        name="calculator",
        description="Evaluate a bounded arithmetic expression using safe mathematical operations.",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "minLength": 1, "maxLength": 256}
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        risk=RiskLevel.READ_ONLY,
        handler=handler,
    )
