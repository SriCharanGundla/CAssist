INCOMING_PREFIX = "incoming/"
ORIGINALS_PREFIX = "originals/"


def permanent_key_for_incoming(incoming_key: str) -> str:
    """Return the private final key paired with an opaque incoming upload key."""
    if not incoming_key.startswith(INCOMING_PREFIX):
        raise ValueError("Expected an incoming object key")
    opaque_identifier = incoming_key.removeprefix(INCOMING_PREFIX)
    if not opaque_identifier:
        raise ValueError("Incoming object key has no opaque identifier")
    return f"{ORIGINALS_PREFIX}{opaque_identifier}"
