async def process_next_document() -> None:
    """Claim and process one queued document.

    Job leasing, preprocessing, Strands extraction, deterministic validation,
    and temporary-file cleanup will be implemented in the first vertical slice.
    """
