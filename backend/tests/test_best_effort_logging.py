from __future__ import annotations

import logging

from app.observability.best_effort import log_best_effort_failure


def test_best_effort_logging_redacts_exception_and_context(caplog):
    logger = logging.getLogger("tests.best_effort")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        log_best_effort_failure(
            logger,
            "unit.operation",
            RuntimeError("token=sk-secret1234567890"),
            run_id="run_best_effort",
            sample="Bearer secret-token-1234567890",
        )

    assert "unit.operation" in caplog.text
    assert "run_best_effort" in caplog.text
    assert "sk-secret1234567890" not in caplog.text
    assert "secret-token-1234567890" not in caplog.text


def test_best_effort_logging_redacts_traceback_exception_string(caplog):
    logger = logging.getLogger("tests.best_effort_traceback")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        try:
            raise RuntimeError("upstream Authorization: Bearer secretbearertokenvalue1234567890")
        except RuntimeError as exc:
            log_best_effort_failure(logger, "unit.traceback", exc)

    assert "unit.traceback" in caplog.text
    assert "Traceback" in caplog.text
    assert "secretbearertokenvalue" not in caplog.text
    assert "Bearer [REDACTED]" in caplog.text
