import logging
from collections.abc import Sequence
from typing import Any


class RedactAccessQueryStringFilter(logging.Filter):
    """Remove query strings from Uvicorn access-log request targets."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, Sequence) or isinstance(args, (str, bytes)) or len(args) < 3:
            return True

        request_target = args[2]
        if not isinstance(request_target, str) or "?" not in request_target:
            return True

        sanitized_args: list[Any] = list(args)
        sanitized_args[2] = request_target.partition("?")[0]
        record.args = tuple(sanitized_args)
        return True


def configure_safe_access_logging() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, RedactAccessQueryStringFilter) for item in access_logger.filters):
        access_logger.addFilter(RedactAccessQueryStringFilter())
