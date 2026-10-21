"""Object storage behind a Protocol so boto3 never leaks into services."""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import anyio.to_thread
import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True, slots=True)
class ObjectMeta:
    size: int
    content_type: str | None


@dataclass(frozen=True, slots=True)
class PresignedUpload:
    url: str
    headers: dict[str, str]


@runtime_checkable
class ObjectStorage(Protocol):
    async def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> PresignedUpload: ...

    async def head(self, key: str) -> ObjectMeta | None: ...

    async def get(self, key: str) -> bytes: ...

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def copy(self, src: str, dst: str) -> None: ...

    async def delete(self, key: str) -> None: ...

    def public_url(self, key: str | None) -> str | None: ...


class S3ObjectStorage:
    """boto3 S3 adapter; every blocking call runs in a worker thread."""

    def __init__(self, bucket: str, public_base_url: str = "", region: str | None = None) -> None:
        self._bucket = bucket
        self._client = boto3.client("s3", region_name=region)
        self._public_base = public_base_url.rstrip("/") or f"https://{bucket}.s3.amazonaws.com"

    async def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> PresignedUpload:
        url = await anyio.to_thread.run_sync(
            lambda: self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                },
                ExpiresIn=expires_in,
            )
        )
        return PresignedUpload(
            url=str(url),
            headers={"Content-Type": content_type, "Content-Length": str(content_length)},
        )

    async def head(self, key: str) -> ObjectMeta | None:
        def _head() -> ObjectMeta | None:
            try:
                out = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            return ObjectMeta(size=int(out["ContentLength"]), content_type=out.get("ContentType"))

        return await anyio.to_thread.run_sync(_head)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            body: bytes = self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
            return body

        return await anyio.to_thread.run_sync(_get)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )
        )

    async def copy(self, src: str, dst: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.copy_object(
                Bucket=self._bucket, Key=dst, CopySource={"Bucket": self._bucket, "Key": src}
            )
        )

    async def delete(self, key: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key)
        )

    def public_url(self, key: str | None) -> str | None:
        return None if key is None else f"{self._public_base}/{key}"
