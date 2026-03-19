"""Tests for src/hermit/runtime/observation/logging/setup.py"""

from __future__ import annotations

import io
import logging

from hermit.runtime.observation.logging.setup import configure_logging


class TestConfigureLogging:
    def test_default_level_is_info(self) -> None:
        stream = io.StringIO()
        configure_logging(stream=stream)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_custom_level_debug(self) -> None:
        stream = io.StringIO()
        configure_logging(level="DEBUG", stream=stream)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_custom_level_warning(self) -> None:
        stream = io.StringIO()
        configure_logging(level="WARNING", stream=stream)
        root = logging.getLogger()
        assert root.level == logging.WARNING

    def test_case_insensitive_level(self) -> None:
        stream = io.StringIO()
        configure_logging(level="debug", stream=stream)
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_invalid_level_defaults_to_info(self) -> None:
        stream = io.StringIO()
        configure_logging(level="NONEXISTENT", stream=stream)
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_noisy_loggers_suppressed(self) -> None:
        stream = io.StringIO()
        configure_logging(stream=stream)
        for name in ("httpx", "httpcore", "httpcore.http11", "httpcore.connection"):
            logger = logging.getLogger(name)
            assert logger.level == logging.WARNING

    def test_custom_stream_used(self) -> None:
        stream = io.StringIO()
        configure_logging(stream=stream)
        # structlog should be configured without error
        import structlog

        log = structlog.get_logger()
        # The fact that configure_logging didn't raise is the key assertion
        assert log is not None

    def test_default_stream_no_explicit_factory(self) -> None:
        """When no stream is provided, structlog should use its default factory."""
        configure_logging()
        import structlog

        log = structlog.get_logger()
        assert log is not None
