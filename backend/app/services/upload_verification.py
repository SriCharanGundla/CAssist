import hashlib
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import IO

from app.services.object_storage import ObjectStorage

_CHUNK_SIZE = 1024 * 1024
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024


class UploadValidationError(Exception):
    pass


@dataclass
class VerifiedUpload:
    body: IO[bytes]
    byte_size: int
    mime_type: str
    sha256: str

    def close(self) -> None:
        self.body.close()


def _matches_file_signature(mime_type: str, prefix: bytes) -> bool:
    signatures = {
        "application/pdf": (b"%PDF-",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
    }
    return any(prefix.startswith(signature) for signature in signatures[mime_type])


def verify_upload(
    storage: ObjectStorage,
    object_key: str,
    expected_byte_size: int,
    expected_mime_type: str,
    maximum_byte_size: int,
) -> VerifiedUpload:
    stored_object = storage.open_object(object_key)
    temporary_body = SpooledTemporaryFile(max_size=_SPOOL_MEMORY_LIMIT, mode="w+b")
    digest = hashlib.sha256()
    byte_size = 0
    prefix = bytearray()

    try:
        if stored_object.content_length != expected_byte_size:
            raise UploadValidationError("Uploaded object size does not match the request")
        if stored_object.content_length > maximum_byte_size:
            raise UploadValidationError("Uploaded object exceeds the size limit")
        if stored_object.content_type != expected_mime_type:
            raise UploadValidationError("Uploaded object content type does not match the request")

        while chunk := stored_object.body.read(_CHUNK_SIZE):
            byte_size += len(chunk)
            if byte_size > expected_byte_size or byte_size > maximum_byte_size:
                raise UploadValidationError("Uploaded object exceeds the expected size")
            if len(prefix) < 16:
                prefix.extend(chunk[: 16 - len(prefix)])
            digest.update(chunk)
            temporary_body.write(chunk)

        if byte_size != expected_byte_size:
            raise UploadValidationError("Uploaded object size does not match the request")
        if not _matches_file_signature(expected_mime_type, bytes(prefix)):
            raise UploadValidationError("Uploaded object content does not match its file type")

        temporary_body.seek(0)
        return VerifiedUpload(
            body=temporary_body,
            byte_size=byte_size,
            mime_type=expected_mime_type,
            sha256=digest.hexdigest(),
        )
    except Exception:
        temporary_body.close()
        raise
    finally:
        stored_object.body.close()
