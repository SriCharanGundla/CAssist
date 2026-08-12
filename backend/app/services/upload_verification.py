import hashlib
import zipfile
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from app.services.object_storage import ObjectStorage

_CHUNK_SIZE = 1024 * 1024
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024
_MAX_XLSX_ENTRIES = 10_000
_MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


class UploadValidationError(Exception):
    pass


@dataclass
class VerifiedUpload:
    body: BinaryIO
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
        "text/csv": (b"",),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
            b"PK\x03\x04",
        ),
    }
    return any(prefix.startswith(signature) for signature in signatures[mime_type])


def _validate_csv(body: BinaryIO) -> None:
    body.seek(0)
    sample = bytearray()
    while chunk := body.read(_CHUNK_SIZE):
        if b"\x00" in chunk:
            raise UploadValidationError("CSV contains unsupported binary content")
        if len(sample) < 64 * 1024:
            sample.extend(chunk[: 64 * 1024 - len(sample)])
    try:
        bytes(sample).decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            bytes(sample).decode("cp1252")
        except UnicodeDecodeError as exc:
            raise UploadValidationError("CSV uses an unsupported text encoding") from exc
    finally:
        body.seek(0)


def _validate_xlsx(body: BinaryIO) -> None:
    body.seek(0)
    try:
        with zipfile.ZipFile(body) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UploadValidationError("Uploaded object is not an XLSX workbook")
            if len(members) > _MAX_XLSX_ENTRIES:
                raise UploadValidationError("XLSX contains too many archive entries")
            if any(member.flag_bits & 0x1 for member in members):
                raise UploadValidationError("Encrypted XLSX workbooks are unsupported")
            if sum(member.file_size for member in members) > _MAX_XLSX_UNCOMPRESSED_BYTES:
                raise UploadValidationError("XLSX expands beyond the processing limit")
    except (OSError, zipfile.BadZipFile) as exc:
        raise UploadValidationError("Uploaded object is not a valid XLSX workbook") from exc
    finally:
        body.seek(0)


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
        if expected_mime_type == "text/csv":
            _validate_csv(temporary_body)
        elif expected_mime_type == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):
            _validate_xlsx(temporary_body)

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
