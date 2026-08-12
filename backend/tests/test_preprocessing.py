import hashlib
from io import BytesIO
from typing import BinaryIO

import pypdfium2 as pdfium
import pytest
from openpyxl import Workbook
from PIL import Image

from app.services.object_storage import PresignedUpload, StoredObject
from app.services.preprocessing import PreprocessingError, preprocess_document


class MemoryObjectStorage:
    def __init__(self, content: bytes, content_type: str) -> None:
        self.content = content
        self.content_type = content_type

    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int,
    ) -> PresignedUpload:
        raise NotImplementedError

    def open_object(self, object_key: str) -> StoredObject:
        return StoredObject(
            body=BytesIO(self.content),
            content_length=len(self.content),
            content_type=self.content_type,
        )

    def put_object(
        self,
        object_key: str,
        body: BinaryIO,
        content_type: str,
        content_length: int,
    ) -> None:
        raise NotImplementedError

    def delete_object(self, object_key: str) -> None:
        raise NotImplementedError


def _png_bytes(size: tuple[int, int] = (120, 80)) -> bytes:
    output = BytesIO()
    image = Image.new("RGBA", size, (0, 80, 180, 120))
    try:
        image.save(output, format="PNG")
    finally:
        image.close()
    return output.getvalue()


def _pdf_bytes(page_count: int) -> bytes:
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(page_count):
            document.new_page(612, 792)
        output = BytesIO()
        document.save(output)
        return output.getvalue()
    finally:
        document.close()


def _xlsx_bytes() -> bytes:
    output = BytesIO()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Invoices"
    worksheet.append(["Invoice No.", "Amount", "Paid"])
    worksheet.append(["INV-1", "1180.00", True])
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_image_preprocessing_normalizes_to_rgb_png_and_cleans_temporary_files() -> None:
    content = _png_bytes()
    preprocessed = preprocess_document(
        MemoryObjectStorage(content, "image/png"),
        "originals/opaque",
        len(content),
        hashlib.sha256(content).hexdigest(),
        "image/png",
        maximum_pages=50,
        render_dpi=144,
        maximum_pixels=1_000_000,
        maximum_total_pixels=1_000_000,
    )
    page_path = preprocessed.page_paths[0]
    temporary_directory = page_path.parent

    assert preprocessed.page_count == 1
    assert preprocessed.page_text == (None,)
    assert page_path.name == "page-0001.png"
    assert sorted(path.name for path in temporary_directory.iterdir()) == ["page-0001.png"]
    with Image.open(page_path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (120, 80)

    preprocessed.close()
    assert not temporary_directory.exists()


def test_pdf_preprocessing_renders_each_page_at_configured_resolution() -> None:
    content = _pdf_bytes(2)
    with preprocess_document(
        MemoryObjectStorage(content, "application/pdf"),
        "originals/opaque",
        len(content),
        hashlib.sha256(content).hexdigest(),
        "application/pdf",
        maximum_pages=10,
        render_dpi=144,
        maximum_pixels=3_000_000,
        maximum_total_pixels=6_000_000,
    ) as preprocessed:
        assert preprocessed.page_count == 2
        assert preprocessed.page_text == (None, None)
        assert [path.name for path in preprocessed.page_paths] == [
            "page-0001.png",
            "page-0002.png",
        ]
        for page_path in preprocessed.page_paths:
            with Image.open(page_path) as image:
                assert image.mode == "RGB"
                assert image.size == (1224, 1584)


@pytest.mark.parametrize(
    ("content", "mime_type", "expected_source"),
    [
        (
            b"Invoice No.,Amount\r\nINV-1,1180.00\r\n",
            "text/csv",
            "Source: CSV",
        ),
        (
            _xlsx_bytes(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Source: Worksheet: Invoices",
        ),
    ],
)
def test_spreadsheet_preprocessing_renders_values_and_native_text(
    content: bytes,
    mime_type: str,
    expected_source: str,
) -> None:
    with preprocess_document(
        MemoryObjectStorage(content, mime_type),
        "originals/opaque",
        len(content),
        hashlib.sha256(content).hexdigest(),
        mime_type,
        maximum_pages=10,
        render_dpi=144,
        maximum_pixels=3_000_000,
        maximum_total_pixels=6_000_000,
    ) as preprocessed:
        assert preprocessed.page_count == 1
        assert expected_source in (preprocessed.page_text[0] or "")
        assert "Invoice No." in (preprocessed.page_text[0] or "")
        assert "1180.00" in (preprocessed.page_text[0] or "")
        with Image.open(preprocessed.page_paths[0]) as image:
            assert image.format == "PNG"
            assert image.mode == "RGB"


@pytest.mark.parametrize(
    ("content", "mime_type", "maximum_pages", "maximum_pixels", "error_code"),
    [
        (_pdf_bytes(2), "application/pdf", 1, 3_000_000, "PAGE_LIMIT_EXCEEDED"),
        (_png_bytes((100, 100)), "image/png", 50, 9_999, "PIXEL_LIMIT_EXCEEDED"),
        (b"not-a-png", "image/png", 50, 1_000_000, "INVALID_DOCUMENT"),
    ],
)
def test_preprocessing_rejects_unsafe_or_invalid_documents(
    content: bytes,
    mime_type: str,
    maximum_pages: int,
    maximum_pixels: int,
    error_code: str,
) -> None:
    with pytest.raises(PreprocessingError) as error:
        preprocess_document(
            MemoryObjectStorage(content, mime_type),
            "originals/opaque",
            len(content),
            hashlib.sha256(content).hexdigest(),
            mime_type,
            maximum_pages=maximum_pages,
            render_dpi=144,
            maximum_pixels=maximum_pixels,
            maximum_total_pixels=maximum_pixels * maximum_pages,
        )

    assert error.value.code == error_code
    assert "opaque" not in error.value.safe_message


def test_preprocessing_rejects_changed_source_size() -> None:
    content = _png_bytes()
    with pytest.raises(PreprocessingError, match="verified metadata") as error:
        preprocess_document(
            MemoryObjectStorage(content, "image/png"),
            "originals/opaque",
            len(content) + 1,
            hashlib.sha256(content).hexdigest(),
            "image/png",
            maximum_pages=50,
            render_dpi=144,
            maximum_pixels=1_000_000,
            maximum_total_pixels=1_000_000,
        )

    assert error.value.code == "SOURCE_SIZE_MISMATCH"


def test_preprocessing_rejects_changed_source_hash() -> None:
    content = _png_bytes()
    with pytest.raises(PreprocessingError) as error:
        preprocess_document(
            MemoryObjectStorage(content, "image/png"),
            "originals/opaque",
            len(content),
            "0" * 64,
            "image/png",
            maximum_pages=50,
            render_dpi=144,
            maximum_pixels=1_000_000,
            maximum_total_pixels=1_000_000,
        )

    assert error.value.code == "SOURCE_HASH_MISMATCH"


def test_pdf_preprocessing_enforces_aggregate_pixel_limit() -> None:
    content = _pdf_bytes(2)
    with pytest.raises(PreprocessingError) as error:
        preprocess_document(
            MemoryObjectStorage(content, "application/pdf"),
            "originals/opaque",
            len(content),
            hashlib.sha256(content).hexdigest(),
            "application/pdf",
            maximum_pages=10,
            render_dpi=144,
            maximum_pixels=3_000_000,
            maximum_total_pixels=3_000_000,
        )

    assert error.value.code == "TOTAL_PIXEL_LIMIT_EXCEEDED"
