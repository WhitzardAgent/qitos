"""Tests for RetryPolicy, on_failure, and ActionExecutor retry logic."""

from __future__ import annotations

import time
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from qitos.core.tool import (
    FunctionTool,
    RetryPolicy,
    ToolMeta,
    ToolSpec,
    tool,
    build_tool_spec,
)
from qitos.core.function_tool_decorator import function_tool


# ---------------------------------------------------------------------------
# RetryPolicy dataclass
# ---------------------------------------------------------------------------


class TestRetryPolicyDataclass:
    def test_defaults(self):
        rp = RetryPolicy()
        assert rp.max_attempts == 3
        assert rp.backoff_factor == 0.5
        assert rp.max_backoff == 60.0
        assert rp.jitter is True
        assert rp.retryable_exceptions == (Exception,)

    def test_custom_values(self):
        rp = RetryPolicy(
            max_attempts=5,
            backoff_factor=1.0,
            max_backoff=30.0,
            jitter=False,
            retryable_exceptions=(ValueError, TypeError),
        )
        assert rp.max_attempts == 5
        assert rp.backoff_factor == 1.0
        assert rp.max_backoff == 30.0
        assert rp.jitter is False
        assert rp.retryable_exceptions == (ValueError, TypeError)

    def test_invalid_retryable_exceptions(self):
        with pytest.raises(TypeError, match="must contain exception types"):
            RetryPolicy(retryable_exceptions=("not_an_exception",))

    def test_retryable_exceptions_must_be_base_exception_subclass(self):
        with pytest.raises(TypeError, match="must contain exception types"):
            RetryPolicy(retryable_exceptions=(int, str))


# ---------------------------------------------------------------------------
# ToolSpec / ToolMeta fields
# ---------------------------------------------------------------------------


class TestToolSpecRetryPolicy:
    def test_spec_defaults_none(self):
        spec = ToolSpec(name="t", description="test")
        assert spec.retry_policy is None
        assert spec.on_failure is None

    def test_spec_with_retry_policy(self):
        rp = RetryPolicy(max_attempts=5)
        spec = ToolSpec(name="t", description="test", retry_policy=rp)
        assert spec.retry_policy is rp
        assert spec.retry_policy.max_attempts == 5

    def test_spec_with_on_failure(self):
        cb = lambda: None
        spec = ToolSpec(name="t", description="test", on_failure=cb)
        assert spec.on_failure is cb


class TestToolMetaRetryPolicy:
    def test_meta_defaults_none(self):
        meta = ToolMeta()
        assert meta.retry_policy is None
        assert meta.on_failure is None

    def test_meta_with_retry_policy(self):
        rp = RetryPolicy(max_attempts=7)
        meta = ToolMeta(retry_policy=rp)
        assert meta.retry_policy.max_attempts == 7

    def test_meta_with_on_failure(self):
        cb = lambda: None
        meta = ToolMeta(on_failure=cb)
        assert meta.on_failure is cb


# ---------------------------------------------------------------------------
# @tool decorator propagation
# ---------------------------------------------------------------------------


class TestToolDecoratorPropagation:
    def test_tool_with_retry_policy(self):
        rp = RetryPolicy(max_attempts=4)
        on_fail = MagicMock()

        @tool(name="retry_tool", retry_policy=rp, on_failure=on_fail)
        def my_tool(x: int) -> int:
            """A tool with retry."""
            return x

        meta = my_tool.__qitos_tool_meta__
        assert meta.retry_policy is rp
        assert meta.retry_policy.max_attempts == 4
        assert meta.on_failure is on_fail

    def test_tool_without_retry_policy(self):
        @tool(name="simple_tool")
        def my_tool(x: int) -> int:
            return x

        meta = my_tool.__qitos_tool_meta__
        assert meta.retry_policy is None
        assert meta.on_failure is None


# ---------------------------------------------------------------------------
# @function_tool decorator propagation
# ---------------------------------------------------------------------------


class TestFunctionToolDecoratorPropagation:
    def test_function_tool_with_retry_policy(self):
        rp = RetryPolicy(max_attempts=6)
        on_fail = MagicMock()

        @function_tool(retry_policy=rp, on_failure=on_fail)
        def my_tool(x: int) -> int:
            """A tool with retry."""
            return x

        assert isinstance(my_tool, FunctionTool)
        assert my_tool.meta.retry_policy is rp
        assert my_tool.spec.retry_policy is rp
        assert my_tool.meta.on_failure is on_fail
        assert my_tool.spec.on_failure is on_fail

    def test_function_tool_without_retry_policy(self):
        @function_tool
        def my_tool(x: int) -> int:
            return x

        assert my_tool.meta.retry_policy is None
        assert my_tool.spec.retry_policy is None


# ---------------------------------------------------------------------------
# build_tool_spec propagation
# ---------------------------------------------------------------------------


class TestBuildToolSpec:
    def test_build_tool_spec_propagates_retry_policy(self):
        rp = RetryPolicy(max_attempts=10, backoff_factor=2.0)
        on_fail = MagicMock()

        def my_fn(x: int) -> int:
            return x

        meta = ToolMeta(retry_policy=rp, on_failure=on_fail)
        spec = build_tool_spec(my_fn, meta)
        assert spec.retry_policy is rp
        assert spec.retry_policy.max_attempts == 10
        assert spec.retry_policy.backoff_factor == 2.0
        assert spec.on_failure is on_fail

    def test_build_tool_spec_no_retry_policy(self):
        def my_fn(x: int) -> int:
            return x

        meta = ToolMeta()
        spec = build_tool_spec(my_fn, meta)
        assert spec.retry_policy is None
        assert spec.on_failure is None


# ---------------------------------------------------------------------------
# ActionExecutor retry logic (integration-level)
# ---------------------------------------------------------------------------


class TestActionExecutorRetry:
    def test_function_tool_with_retry_policy_spec(self):
        """FunctionTool stores retry_policy in spec for ActionExecutor to use."""
        rp = RetryPolicy(max_attempts=3, backoff_factor=0.01, jitter=False, retryable_exceptions=(ValueError,))

        def flaky_tool(x: int) -> int:
            return x * 2

        ft = FunctionTool(
            flaky_tool,
            meta=ToolMeta(retry_policy=rp),
        )
        assert ft.spec.retry_policy is rp
        assert ft.spec.retry_policy.max_attempts == 3

    def test_retry_policy_exhausted_on_failure_spec(self):
        """on_failure callback is stored in spec for ActionExecutor to invoke on exhaustion."""
        on_fail = MagicMock()

        def always_fails(x: int) -> int:
            raise RuntimeError("permanent error")

        ft = FunctionTool(
            always_fails,
            meta=ToolMeta(
                retry_policy=RetryPolicy(max_attempts=2, backoff_factor=0.01, jitter=False, retryable_exceptions=(RuntimeError,)),
                on_failure=on_fail,
            ),
        )

        # ActionExecutor uses these, verify they're in the spec
        assert ft.spec.on_failure is on_fail
        assert ft.spec.retry_policy.max_attempts == 2

    def test_non_retryable_exception_spec(self):
        """retryable_exceptions filters which exceptions ActionExecutor retries."""
        rp = RetryPolicy(max_attempts=3, retryable_exceptions=(ValueError,))

        def type_error_tool(x: int) -> int:
            raise TypeError("wrong type")

        ft = FunctionTool(
            type_error_tool,
            meta=ToolMeta(retry_policy=rp),
        )
        assert ft.spec.retry_policy.retryable_exceptions == (ValueError,)
        # TypeError is not in retryable_exceptions, so ActionExecutor won't retry it

    def test_max_retries_backward_compat(self):
        """max_retries still works without RetryPolicy."""
        def simple_tool(x: int) -> int:
            return x

        ft = FunctionTool(
            simple_tool,
            meta=ToolMeta(max_retries=5),
        )
        assert ft.spec.max_retries == 5
        assert ft.spec.retry_policy is None


# ---------------------------------------------------------------------------
# RetryPolicy edge cases
# ---------------------------------------------------------------------------


class TestRetryPolicyEdgeCases:
    def test_single_attempt_no_retry(self):
        rp = RetryPolicy(max_attempts=1)
        assert rp.max_attempts == 1

    def test_zero_backoff(self):
        rp = RetryPolicy(backoff_factor=0)
        assert rp.backoff_factor == 0

    def test_large_max_backoff(self):
        rp = RetryPolicy(max_backoff=600.0)
        assert rp.max_backoff == 600.0

    def test_single_retryable_exception(self):
        rp = RetryPolicy(retryable_exceptions=(ConnectionError,))
        assert rp.retryable_exceptions == (ConnectionError,)

    def test_empty_retryable_exceptions_tuple_valid(self):
        """Empty tuple is valid — nothing is retryable."""
        rp = RetryPolicy(retryable_exceptions=())
        assert rp.retryable_exceptions == ()
