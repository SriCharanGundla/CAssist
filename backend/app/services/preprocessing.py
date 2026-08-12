import hashlib
import math
import warnings
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.object_storage import ObjectStorage

_CHUNK_SIZE = 1024 * 1024
_MAX_NATIVE_TEXT_CHARACTERS_PER_PAGE = 100_000


class PreprocessingError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass
class PreprocessedDocument:
    page_paths: tuple[Path, ...]
    page_text: tuple[str | None, ...]
    page_count: int
    _temporary_directory: TemporaryDirectory[str]

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def __enter__(self) -> "PreprocessedDocument":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _download_source(
    storage: ObjectStorage,
    object_key: str,
    destination: Path,
    expected_byte_size: int,
    expected_sha256: str,
    expected_mime_type: str,
) -> None:
    stored_object = storage.open_object(object_key)
    streamed_size = 0
    digest = hashlib.sha256()
    try:
        if stored_object.content_length != expected_byte_size:
            raise PreprocessingError(
                "SOURCE_SIZE_MISMATCH",
                "Stored original size does not match verified metadata",
            )
        if stored_object.content_type != expected_mime_type:
            raise PreprocessingError(
                "SOURCE_TYPE_MISMATCH",
                "Stored original type does not match verified metadata",
            )
        with destination.open("wb") as destination_stream:
            while chunk := stored_object.body.read(_CHUNK_SIZE):
                streamed_size += len(chunk)
                if streamed_size > expected_byte_size:
                    raise PreprocessingError(
                        "SOURCE_SIZE_MISMATCH",
                        "Stored original exceeds verified metadata",
                    )
                digest.update(chunk)
                destination_stream.write(chunk)
    finally:
        stored_object.body.close()

    if streamed_size != expected_byte_size:
        raise PreprocessingError(
            "SOURCE_SIZE_MISMATCH",
            "Stored original size does not match verified metadata",
        )
    if digest.hexdigest() != expected_sha256:
        raise PreprocessingError(
            "SOURCE_HASH_MISMATCH",
            "Stored original does not match its trusted hash",
        )


def _render_pdf(
    source_path: Path,
    output_directory: Path,
    maximum_pages: int,
    render_dpi: int,
    maximum_pixels: int,
    maximum_total_pixels: int,
) -> tuple[tuple[Path, ...], tuple[str | None, ...]]:
    document = pdfium.PdfDocument(source_path)
    try:
        page_count = len(document)
        if page_count < 1:
            raise PreprocessingError("EMPTY_DOCUMENT", "PDF contains no pages")
        if page_count > maximum_pages:
            raise PreprocessingError("PAGE_LIMIT_EXCEEDED", "PDF exceeds the page limit")

        scale = render_dpi / 72
        page_paths: list[Path] = []
        page_text: list[str | None] = []
        total_pixels = 0
        for page_number in range(page_count):
            page = document.get_page(page_number)
            try:
                width, height = page.get_size()
                width_pixels = math.ceil(width * scale)
                height_pixels = math.ceil(height * scale)
                if width_pixels <= 0 or height_pixels <= 0:
                    raise PreprocessingError("INVALID_PAGE", "PDF contains an invalid page")
                if width_pixels * height_pixels > maximum_pixels:
                    raise PreprocessingError(
                        "PIXEL_LIMIT_EXCEEDED",
                        "Rendered PDF page exceeds the pixel limit",
                    )
                total_pixels += width_pixels * height_pixels
                if total_pixels > maximum_total_pixels:
                    raise PreprocessingError(
                        "TOTAL_PIXEL_LIMIT_EXCEEDED",
                        "Rendered PDF exceeds the total pixel limit",
                    )

                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    try:
                        output_path = output_directory / f"page-{page_number + 1:04d}.png"
                        rgb = image.convert("RGB")
                        try:
                            rgb.save(output_path, format="PNG")
                        finally:
                            rgb.close()
                        page_paths.append(output_path)
                    finally:
                        image.close()
                finally:
                    bitmap.close()
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_range().strip()
                    page_text.append(
                        text[:_MAX_NATIVE_TEXT_CHARACTERS_PER_PAGE] if text else None
                    )
                finally:
                    text_page.close()
            finally:
                page.close()
        return tuple(page_paths), tuple(page_text)
    finally:
        document.close()


def _normalize_image(
    source_path: Path,
    output_directory: Path,
    expected_mime_type: str,
    maximum_pixels: int,
) -> tuple[Path, ...]:
    expected_format = {"image/jpeg": "JPEG", "image/png": "PNG"}[expected_mime_type]
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source_path) as source_image:
            if source_image.format != expected_format:
                raise PreprocessingError(
                    "SOURCE_TYPE_MISMATCH",
                    "Stored original does not match verified file type",
                )
            if source_image.width * source_image.height > maximum_pixels:
                raise PreprocessingError(
                    "PIXEL_LIMIT_EXCEEDED",
                    "Image exceeds the pixel limit",
                )
            source_image.load()
            normalized = ImageOps.exif_transpose(source_image)
            try:
                output_path = output_directory / "page-0001.png"
                if "A" in normalized.getbands():
                    rgba = normalized.convert("RGBA")
                    try:
                        rgb = Image.new("RGB", rgba.size, "white")
                        try:
                            rgb.paste(rgba, mask=rgba.getchannel("A"))
                            rgb.save(output_path, format="PNG")
                        finally:
                            rgb.close()
                    finally:
                        rgba.close()
                else:
                    rgb = normalized.convert("RGB")
                    try:
                        rgb.save(output_path, format="PNG")
                    finally:
                        rgb.close()
            finally:
                if normalized is not source_image:
                    normalized.close()
    return (output_path,)


def preprocess_document(
    storage: ObjectStorage,
    object_key: str,
    expected_byte_size: int,
    expected_sha256: str,
    expected_mime_type: str,
    maximum_pages: int,
    render_dpi: int,
    maximum_pixels: int,
    maximum_total_pixels: int,
) -> PreprocessedDocument:
    temporary_directory = TemporaryDirectory(prefix="cassist-preprocess-")
    directory = Path(temporary_directory.name)
    extension = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
    }[expected_mime_type]
    source_path = directory / f"source{extension}"

    try:
        _download_source(
            storage,
            object_key,
            source_path,
            expected_byte_size,
            expected_sha256,
            expected_mime_type,
        )
        if expected_mime_type == "application/pdf":
            page_paths, page_text = _render_pdf(
                source_path,
                directory,
                maximum_pages,
                render_dpi,
                maximum_pixels,
                maximum_total_pixels,
            )
        elif expected_mime_type in {"image/jpeg", "image/png"}:
            page_paths = _normalize_image(
                source_path,
                directory,
                expected_mime_type,
                maximum_pixels,
            )
            page_text = (None,)
        else:
            raise PreprocessingError("INVALID_DOCUMENT", "Unsupported document type")
        source_path.unlink()
        return PreprocessedDocument(
            page_paths=page_paths,
            page_text=page_text,
            page_count=len(page_paths),
            _temporary_directory=temporary_directory,
        )
    except PreprocessingError:
        temporary_directory.cleanup()
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        pdfium.PdfiumError,
        OSError,
    ) as exc:
        temporary_directory.cleanup()
        raise PreprocessingError("INVALID_DOCUMENT", "Document could not be preprocessed") from exc
    except Exception:
        temporary_directory.cleanup()
        raise
