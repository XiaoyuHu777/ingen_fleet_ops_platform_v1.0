"""Durable byte-range download of an authenticated bundle representation."""

import hashlib
import os
import re
from pathlib import Path
from time import sleep

import httpx

from agent.artifacts import MAX_BUNDLE_BYTES, ArtifactError
from agent.storage import atomic_json, read_json, sync_dir

# The existing Registry derives its strong ETag from the entire bundle SHA-256.
STRONG_TAG = re.compile(r'"[0-9a-f]{64}"')
SPAN = re.compile(r"bytes ([0-9]{1,8})-([0-9]{1,8})/([0-9]{1,8})")
CHUNK = 16 * 1024


class DownloadError(ValueError):
    """The response could not be used to extend an on-disk representation."""


class StagedDownload:
    def __init__(self, staging: Path, digest: str) -> None:
        self.part = staging / f"{digest}.part"
        self.identity = staging / f"{digest}.json"

    def discard(self) -> None:
        self.part.unlink(missing_ok=True)
        self.identity.unlink(missing_ok=True)
        sync_dir(self.part.parent)

    def resume(self) -> tuple[int, str | None, int | None]:
        try:
            saved = read_json(self.identity)
            length = self.part.stat().st_size
            etag, total = saved["etag"], saved["length"]
            if (
                STRONG_TAG.fullmatch(etag)
                and type(total) is int
                and 0 <= length <= total <= MAX_BUNDLE_BYTES
                and total > 0
            ):
                return length, etag, total
        except (FileNotFoundError, TypeError, KeyError, ValueError):
            pass
        self.discard()
        return 0, None, None

    @staticmethod
    def headers(response: httpx.Response) -> tuple[str, int]:
        tag = response.headers.get("ETag", "")
        length = response.headers.get("Content-Length", "")
        if (
            not STRONG_TAG.fullmatch(tag)
            or not re.fullmatch(r"[0-9]{1,8}", length)
            or not 0 < int(length) <= MAX_BUNDLE_BYTES
            or response.headers.get("Content-Encoding", "identity") != "identity"
        ):
            raise DownloadError("Invalid bundle representation headers")
        return tag, int(length)

    def accept(
        self,
        response: httpx.Response,
        offset: int,
        saved_etag: str | None,
        saved_total: int | None,
        delay: float,
    ) -> str:
        if response.status_code == 416 and offset:
            self.discard()
            raise DownloadError("Range rejected; next poll will restart the bundle")
        response.raise_for_status()
        try:
            tag, length = self.headers(response)
        except DownloadError:
            if response.status_code == 206:
                self.discard()
            raise
        if response.status_code == 206:
            span = SPAN.fullmatch(response.headers.get("Content-Range", ""))
            if not span:
                self.discard()
                raise DownloadError("Invalid Content-Range")
            start, end, total = map(int, span.groups())
            if (
                not offset
                or start != offset
                or end != total - 1
                or length != total - start
                or tag != saved_etag
                or total != saved_total
            ):
                self.discard()
                raise DownloadError("Range differs from the saved representation")
        elif response.status_code == 200:
            if "Content-Range" in response.headers:
                self.discard()
                raise DownloadError("Full response contains Content-Range")
            self.discard()
            offset = 0
            total = length
        else:
            raise DownloadError("Expected a complete or partial bundle response")

        # Persist the validator before any bytes written using it can survive a crash.
        atomic_json(self.identity, {"etag": tag, "length": total})
        received = 0
        with self.part.open("ab") as output:
            sync_dir(self.part.parent)
            for block in response.iter_raw(CHUNK):
                received += len(block)
                if received > length:
                    self.discard()
                    raise DownloadError("Bundle body exceeded its declared length")
                output.write(block)
                output.flush()
                # Sync each chunk to make its offset reusable after a process restart.
                os.fsync(output.fileno())
                if delay:
                    sleep(delay)
        if received != length or self.part.stat().st_size != total:
            raise DownloadError("Bundle body ended before its declared length")
        return tag

    def complete(self, tag: str, length: int) -> bytes:
        if self.part.stat().st_size != length:
            raise DownloadError("Staged representation is incomplete")
        data = self.part.read_bytes()
        if hashlib.sha256(data).hexdigest() != tag.strip('"'):
            self.discard()
            raise ArtifactError(
                "bundle_hash_mismatch", "Bundle differs from strong ETag"
            )
        return data

    @classmethod
    def confirm_response(
        cls, response: httpx.Response, expected_etag: str, expected_total: int
    ) -> None:
        """Authenticate an approved byte read before activating cached bytes."""
        response.raise_for_status()
        tag, length = cls.headers(response)
        if tag != expected_etag:
            raise DownloadError(
                "Approved representation no longer matches the staged one"
            )
        if response.status_code == 200 and length == expected_total:
            return
        if (
            response.status_code == 206
            and length == 1
            and response.headers.get("Content-Range") == f"bytes 0-0/{expected_total}"
            and len(response.read()) == 1
        ):
            return
        raise DownloadError("Invalid range response while confirming approval")
