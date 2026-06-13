"""Tests for agent/_error_handling.py — the with_logged_errors decorator."""
from __future__ import annotations

import logging

import pytest

from agent._error_handling import with_logged_errors


class TestWithLoggedErrors:
    def test_returns_default_on_exception(self, caplog):
        @with_logged_errors(default_return="fallback")
        def boom():
            raise RuntimeError("kaboom")

        with caplog.at_level(logging.ERROR):
            result = boom()

        assert result == "fallback"
        assert any("boom" in record.message for record in caplog.records)

    def test_default_return_none(self, caplog):
        @with_logged_errors(op_name="my_op")
        def boom():
            raise ValueError("oops")

        with caplog.at_level(logging.ERROR):
            result = boom()

        assert result is None
        assert any("my_op" in record.message for record in caplog.records)

    def test_passes_through_on_success(self):
        @with_logged_errors(default_return="fallback")
        def ok():
            return 42

        assert ok() == 42

    def test_propagates_args(self):
        @with_logged_errors(default_return=None)
        def add(a, b, *, multiplier=1):
            return (a + b) * multiplier

        assert add(2, 3) == 5
        assert add(2, 3, multiplier=10) == 50

    def test_logs_full_traceback(self, caplog):
        @with_logged_errors()
        def boom():
            raise RuntimeError("with-traceback")

        with caplog.at_level(logging.ERROR):
            boom()

        # logger.exception adds exc_info, so a traceback should be attached
        assert any(record.exc_info is not None for record in caplog.records)
