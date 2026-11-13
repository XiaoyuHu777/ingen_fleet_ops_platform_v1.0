"""Store one complete signed bundle at each content address in MinIO."""

from io import BytesIO
from typing import Protocol

from minio import Minio
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from agent.artifacts import MAX_BUNDLE_BYTES

MEDIA_TYPE = "application/x-tar"


class StoreUnavailable(RuntimeError):
    pass


class ArtifactStore(Protocol):
    def existing(self, digest: str) -> bytes | None: ...
    def put(self, digest: str, data: bytes) -> None: ...
    def read(self, digest: str, offset: int, length: int) -> bytes: ...


class MinioArtifactStore:
    def __init__(self, client: Minio, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    @staticmethod
    def object_name(digest: str) -> str:
        return f"sha256/{digest}/artifact.tar"

    def existing(self, digest: str) -> bytes | None:
        try:
            stat = self.client.stat_object(self.bucket, self.object_name(digest))
        except S3Error as error:
            if error.code in {"NoSuchBucket", "NoSuchKey", "NoSuchObject"}:
                return None
            raise StoreUnavailable("Cannot inspect artifact in object store") from error
        except (MinioException, HTTPError, OSError) as error:
            raise StoreUnavailable("Cannot inspect artifact in object store") from error
        if stat.size is None or not 0 < stat.size <= MAX_BUNDLE_BYTES:
            raise StoreUnavailable("Stored artifact has invalid size")
        return self.read(digest, 0, stat.size)

    def put(self, digest: str, data: bytes) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                try:
                    self.client.make_bucket(self.bucket)
                except S3Error as error:
                    if error.code != "BucketAlreadyOwnedByYou":
                        raise
            self.client.put_object(
                self.bucket,
                self.object_name(digest),
                BytesIO(data),
                len(data),
                content_type=MEDIA_TYPE,
                part_size=MAX_BUNDLE_BYTES,
            )
        except (MinioException, HTTPError, OSError) as error:
            raise StoreUnavailable("Cannot store artifact") from error

    def read(self, digest: str, offset: int, length: int) -> bytes:
        try:
            response = self.client.get_object(
                self.bucket, self.object_name(digest), offset=offset, length=length
            )
            try:
                data = response.read(length + 1)
            finally:
                response.close()
                response.release_conn()
        except (MinioException, HTTPError, OSError) as error:
            raise StoreUnavailable(
                "Cannot download artifact from object store"
            ) from error
        if len(data) != length:
            raise StoreUnavailable(
                "Object store returned an incomplete or unexpected range"
            )
        return data
