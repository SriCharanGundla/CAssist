import pytest

from app.services.object_storage import ObjectStorageError, R2ObjectStorage


class SigningClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, dict[str, object], int]] = []

    def generate_presigned_url(
        self,
        operation: str,
        *,
        Params: dict[str, object],
        ExpiresIn: int,
    ) -> str:
        self.calls.append((operation, Params, ExpiresIn))
        if self.fail:
            raise RuntimeError("simulated SDK failure")
        return "https://download.invalid/object?signature"


def _storage(client: SigningClient) -> R2ObjectStorage:
    storage = object.__new__(R2ObjectStorage)
    storage.bucket_name = "private-originals"
    storage.client = client
    return storage


def test_download_url_signs_only_get_for_one_opaque_key() -> None:
    client = SigningClient()
    storage = _storage(client)

    signed = storage.create_download_url("originals/opaque-id", 300)

    assert signed.url == "https://download.invalid/object?signature"
    assert client.calls == [
        (
            "get_object",
            {"Bucket": "private-originals", "Key": "originals/opaque-id"},
            300,
        )
    ]


def test_download_signing_failure_uses_safe_error() -> None:
    storage = _storage(SigningClient(fail=True))

    with pytest.raises(ObjectStorageError, match="Unable to create a download URL"):
        storage.create_download_url("originals/opaque-id", 300)


def test_upload_url_signs_declared_content_length_and_type() -> None:
    client = SigningClient()
    storage = _storage(client)

    signed = storage.create_upload_url("incoming/opaque-id", "application/pdf", 1234, 300)

    assert signed.headers == {
        "Content-Type": "application/pdf",
        "Content-Length": "1234",
    }
    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "private-originals",
                "Key": "incoming/opaque-id",
                "ContentType": "application/pdf",
                "ContentLength": 1234,
            },
            300,
        )
    ]
