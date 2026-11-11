"""Tool registry, schema export, and dependency-free input validation."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Mapping
from typing import Any

from aegis_ai.models import ToolSpec


_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ToolRegistrationError(ValueError):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        if not _NAME_PATTERN.fullmatch(tool.name):
            raise ToolRegistrationError(
                "tool name must be 1-64 letters, digits, underscores or hyphens"
            )
        if tool.name in self._tools:
            raise ToolRegistrationError(f"tool already registered: {tool.name}")
        if not tool.description.strip():
            raise ToolRegistrationError("tool description must not be empty")
        if tool.parameters.get("type") != "object":
            raise ToolRegistrationError("tool parameters must describe a JSON object")
        if not callable(tool.handler):
            raise ToolRegistrationError("tool handler must be callable")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                },
            }
            for tool in self._tools.values()
        ]

    async def invoke(self, tool: ToolSpec, arguments: Mapping[str, Any]) -> Any:
        result = tool.handler(arguments)
        if inspect.isawaitable(result):
            return await result
        return result


def validate_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> list[str]:
    """Validate the supported JSON Schema subset and return human-readable errors."""
    errors: list[str] = []
    if not isinstance(arguments, Mapping):
        return ["arguments must be an object"]

    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        return ["tool schema has invalid properties or required fields"]

    for key in required:
        if key not in arguments:
            errors.append(f"missing required argument: {key}")

    if schema.get("additionalProperties", False) is False:
        for key in arguments:
            if key not in properties:
                errors.append(f"unexpected argument: {key}")

    for key, value in arguments.items():
        spec = properties.get(key)
        if isinstance(spec, Mapping):
            errors.extend(_validate_value(value, spec, str(key)))
    return errors


def _validate_value(value: Any, spec: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected = spec.get("type")
    valid = True

    if expected == "string":
        valid = isinstance(value, str)
    elif expected == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "number":
        valid = (
            isinstance(value, (int, float)) and not isinstance(value, bool) and _is_finite(value)
        )
    elif expected == "boolean":
        valid = isinstance(value, bool)
    elif expected == "object":
        valid = isinstance(value, Mapping)
    elif expected == "array":
        valid = isinstance(value, list)

    if not valid:
        return [f"{path} must be {expected or 'valid'}"]

    if isinstance(value, str):
        if len(value) < spec.get("minLength", 0):
            errors.append(f"{path} is shorter than minLength")
        if "maxLength" in spec and len(value) > spec["maxLength"]:
            errors.append(f"{path} is longer than maxLength")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec and value < spec["minimum"]:
            errors.append(f"{path} is below minimum")
        if "maximum" in spec and value > spec["maximum"]:
            errors.append(f"{path} is above maximum")

    if "enum" in spec and value not in spec["enum"]:
        errors.append(f"{path} is not an allowed value")

    if expected == "array" and "items" in spec:
        item_spec = spec["items"]
        if isinstance(item_spec, Mapping):
            for index, item in enumerate(value):
                errors.extend(_validate_value(item, item_spec, f"{path}[{index}]"))

    if expected == "object" and "properties" in spec:
        errors.extend(validate_arguments(spec, value))

    return errors


def _is_finite(value: int | float) -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:
        return False
