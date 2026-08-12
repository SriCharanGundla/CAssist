from dataclasses import dataclass
from typing import BinaryIO, Protocol

import boto3
from botocore.exceptions import ClientError

from app.core.config import Settings


class ObjectStorageError(Exception):
    pass


class ObjectNotFoundError(ObjectStorageError):
    pass


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class StoredObject:
    body: BinaryIO
    content_length: int
    content_type: str | None


class ObjectStorage(Protocol):
    def create_upload_url(
        self,
        object_key: str,
        content_type: str,
        expires_in: int,
    ) -> PresignedUpload: ...

    def open_object(self, object_key: str) -> StoredObject: ...

    def put_object(
        self,
        object_key: str,
        body: BinaryIO,
        content_type: str,
        content_length: int,
    ) -> None: ...

    def delete_object(self, object_key: str) -> None: ...


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

    def open_object(self, object_key: str) -> StoredObject:
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=object_key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404", "NotFound"}:
                raise ObjectNotFoundError("Object does not exist") from exc
            raise ObjectStorageError("Unable to read object") from exc
        except Exception as exc:
            raise ObjectStorageError("Unable to read object") from exc

        return StoredObject(
            body=response["Body"],
            content_length=response["ContentLength"],
            content_type=response.get("ContentType"),
        )

    def put_object(
        self,
        object_key: str,
        body: BinaryIO,
        content_type: str,
        content_length: int,
    ) -> None:
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=body,
                ContentType=content_type,
                ContentLength=content_length,
            )
        except Exception as exc:
            raise ObjectStorageError("Unable to store verified object") from exc

    def delete_object(self, object_key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=object_key)
        except Exception as exc:
            raise ObjectStorageError("Unable to delete object") from exc
