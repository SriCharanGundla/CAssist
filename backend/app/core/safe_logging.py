from traceback import extract_tb


def safe_exception_context(exc: Exception) -> dict[str, object]:
    """Return useful traceback locations without logging exception messages or data."""
    frames = extract_tb(exc.__traceback__)[-12:] if exc.__traceback__ is not None else []
    return {
        "exception_type": type(exc).__name__,
        "traceback": [f"{frame.name}:{frame.lineno}" for frame in frames],
    }
