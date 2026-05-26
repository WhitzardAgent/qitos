"""E2E test utility tools — @function_tool decorated for real tool calling."""
from __future__ import annotations

from typing import Any, Dict, List

from qitos.core.function_tool_decorator import function_tool


# ---------------------------------------------------------------------------
# Calculator tools
# ---------------------------------------------------------------------------


class CalculatorToolSet:
    """Simple calculator tools for E2E testing."""

    name = "calculator"
    version = "1"

    def setup(self, context: Dict[str, Any]) -> None:
        pass

    def teardown(self, context: Dict[str, Any]) -> None:
        pass

    @function_tool(
        name="add",
        description="Add two numbers together",
        read_only=True,
    )
    def add(self, a: float, b: float) -> Dict[str, Any]:
        return {"result": a + b, "operation": "add", "inputs": [a, b]}

    @function_tool(
        name="multiply",
        description="Multiply two numbers together",
        read_only=True,
    )
    def multiply(self, a: float, b: float) -> Dict[str, Any]:
        return {"result": a * b, "operation": "multiply", "inputs": [a, b]}

    @function_tool(
        name="subtract",
        description="Subtract b from a",
        read_only=True,
    )
    def subtract(self, a: float, b: float) -> Dict[str, Any]:
        return {"result": a - b, "operation": "subtract", "inputs": [a, b]}

    @function_tool(
        name="dangerous_divide",
        description="Divide a by b (requires approval because division can cause errors)",
        needs_approval=True,
    )
    def divide(self, a: float, b: float) -> Dict[str, Any]:
        if b == 0:
            return {"result": "error: division by zero", "operation": "divide"}
        return {"result": a / b, "operation": "divide", "inputs": [a, b]}

    def tools(self) -> List[Any]:
        return [self.add, self.multiply, self.subtract, self.divide]


# ---------------------------------------------------------------------------
# String tools
# ---------------------------------------------------------------------------


class StringToolSet:
    """String operation tools for E2E testing."""

    name = "string_utils"
    version = "1"

    def setup(self, context: Dict[str, Any]) -> None:
        pass

    def teardown(self, context: Dict[str, Any]) -> None:
        pass

    @function_tool(
        name="count_chars",
        description="Count the number of characters in a string",
        read_only=True,
    )
    def count_chars(self, text: str) -> Dict[str, Any]:
        return {"result": len(text), "operation": "count_chars", "input_length": len(text)}

    @function_tool(
        name="reverse_string",
        description="Reverse a string",
        read_only=True,
    )
    def reverse_string(self, text: str) -> Dict[str, Any]:
        return {"result": text[::-1], "operation": "reverse_string"}

    @function_tool(
        name="uppercase",
        description="Convert string to uppercase",
        read_only=True,
    )
    def uppercase(self, text: str) -> Dict[str, Any]:
        return {"result": text.upper(), "operation": "uppercase"}

    def tools(self) -> List[Any]:
        return [self.count_chars, self.reverse_string, self.uppercase]


# ---------------------------------------------------------------------------
# Weather tools
# ---------------------------------------------------------------------------


class WeatherToolSet:
    """Weather lookup tools for E2E testing — returns deterministic fixed data."""

    name = "weather"
    version = "1"

    def setup(self, context: Dict[str, Any]) -> None:
        pass

    def teardown(self, context: Dict[str, Any]) -> None:
        pass

    @function_tool(
        name="get_temperature",
        description="Get the current temperature for a city",
        read_only=True,
    )
    def get_temperature(self, city: str) -> Dict[str, Any]:
        return {"result": 22, "city": city, "unit": "celsius"}

    @function_tool(
        name="get_forecast",
        description="Get the weather forecast for a city for a given number of days",
        read_only=True,
    )
    def get_forecast(self, city: str, days: int = 3) -> Dict[str, Any]:
        return {"result": "sunny", "city": city, "days": days}

    def tools(self) -> List[Any]:
        return [self.get_temperature, self.get_forecast]


# ---------------------------------------------------------------------------
# Flaky tools (for recovery / retry testing)
# ---------------------------------------------------------------------------


class FlakyToolSet:
    """Tools that fail on the first call then succeed — for recovery/retry E2E tests."""

    name = "flaky"
    version = "1"
    _call_count: int = 0

    def setup(self, context: Dict[str, Any]) -> None:
        self._call_count = 0

    def teardown(self, context: Dict[str, Any]) -> None:
        pass

    @function_tool(
        name="flaky_add",
        description="Add two numbers. May fail transiently on the first call.",
        read_only=True,
    )
    def flaky_add(self, a: float, b: float) -> Dict[str, Any]:
        self._call_count += 1
        if self._call_count <= 1:
            raise RuntimeError("transient failure — try again")
        return {"result": a + b, "operation": "flaky_add", "inputs": [a, b]}

    def tools(self) -> List[Any]:
        return [self.flaky_add]
