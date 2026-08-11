import logging

from app.core.access_logging import (
    RedactAccessQueryStringFilter,
    configure_safe_access_logging,
)


def make_access_record(request_target: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:12345", "GET", request_target, "1.1", 303),
        exc_info=None,
    )


def test_access_log_filter_removes_the_entire_query_string() -> None:
    record = make_access_record("/api/v1/auth/callback?code=temporary-code&state=temporary-state")

    assert RedactAccessQueryStringFilter().filter(record) is True
    rendered = record.getMessage()

    assert 'GET /api/v1/auth/callback HTTP/1.1" 303' in rendered
    assert "temporary-code" not in rendered
    assert "temporary-state" not in rendered
    assert "?" not in rendered


def test_access_log_filter_leaves_paths_without_queries_unchanged() -> None:
    record = make_access_record("/api/v1/health")

    assert RedactAccessQueryStringFilter().filter(record) is True
    assert 'GET /api/v1/health HTTP/1.1" 303' in record.getMessage()


def test_access_log_configuration_is_idempotent() -> None:
    logger = logging.getLogger("uvicorn.access")
    original_filters = list(logger.filters)
    logger.filters.clear()
    try:
        configure_safe_access_logging()
        configure_safe_access_logging()
        matching_filters = [
            item for item in logger.filters if isinstance(item, RedactAccessQueryStringFilter)
        ]
        assert len(matching_filters) == 1
    finally:
        logger.filters[:] = original_filters
