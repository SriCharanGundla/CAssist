from dataclasses import dataclass
from typing import Protocol

import boto3

from app.core.config import Settings


class ObjectStorageError(Exception):
    pass


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    headers: dict[str, str]


class ObjectStorage(Protocol):
    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int,
    ) -> PresignedUpload: ...


class R2ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.r2_configured:
            raise ValueError("R2 object storage is not configured")

        self.bucket_name = settings.r2_bucket_name
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
        )

    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int,
    ) -> PresignedUpload:
        try:
            url = self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket_name,
                    "Key": object_key,
                    "ContentType": content_type,
                },
                ExpiresIn=expires_in,
            )
        except Exception as exc:
            raise ObjectStorageError("Unable to create an upload URL") from exc
        return PresignedUpload(
            url=url,
            headers={"Content-Type": content_type},
        )
