"""Publish, resume a download, and reject one altered ONNX byte over the live API."""

import argparse
import io
import tarfile
from pathlib import Path

import httpx

from agent.artifacts import ArtifactError, sha256
from agent.verify import load_trust, verify_bundle
from services.registry.artifact_store import MEDIA_TYPE
from services.registry.config import Settings


def flip_model_byte(bundle: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as outer:
        payload = outer.getmember("payload.tar")
    start = payload.offset_data
    with tarfile.open(
        fileobj=io.BytesIO(bundle[start : start + payload.size]), mode="r:"
    ) as inner:
        graph = inner.getmember("model.onnx")
    offset = start + graph.offset_data + graph.size // 2
    altered = bytearray(bundle)
    altered[offset] ^= 1
    return bytes(altered)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    key = Settings().operator_api_key
    if key is None or not key.get_secret_value():
        parser.error("Set OPERATOR_API_KEY in .env")

    bundle = args.bundle.read_bytes()
    trust = load_trust(args.trust)
    verified = verify_bundle(bundle, trust)
    digest = verified.digest
    print(f"Verified Ed25519 signature and manifest SHA-256 {digest}", flush=True)

    with httpx.Client(
        base_url=args.base_url,
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        timeout=30,
    ) as client:
        upload = {"Content-Type": MEDIA_TYPE}
        published = client.post("/v1/artifacts", content=bundle, headers=upload)
        assert published.status_code in (200, 201), published.text
        record = published.json()
        assert record["digest"] == digest
        print(
            f"POST /v1/artifacts  →  {published.status_code} {record['state']}",
            flush=True,
        )

        replay = client.post("/v1/artifacts", content=bundle, headers=upload)
        assert replay.status_code == 200 and replay.json() == record, replay.text
        print("Repeat POST  →  200, same immutable record", flush=True)

        download = f"/v1/artifacts/{digest}/download"
        prefix = client.get(download, headers={"Range": "bytes=0-4095"})
        assert prefix.status_code == 206, prefix.text
        remainder = client.get(
            download,
            headers={"Range": "bytes=4096-", "If-Range": prefix.headers["ETag"]},
        )
        assert remainder.status_code == 206, remainder.text
        assert prefix.headers["Content-Range"] == f"bytes 0-4095/{record['size_bytes']}"
        assert remainder.headers["Content-Range"] == (
            f"bytes 4096-{record['size_bytes'] - 1}/{record['size_bytes']}"
        )
        downloaded = prefix.content + remainder.content
        assert sha256(downloaded) == record["bundle_sha256"]
        verify_bundle(downloaded, trust, digest)
        print("Two HTTP 206 responses  →  resumed bundle verified", flush=True)

        events = client.get(f"/v1/artifacts/{digest}/audit")
        events.raise_for_status()
        assert [event["to_state"] for event in events.json()["items"]] == ["published"]
        print("Audit log  →  one publication event", flush=True)

        tampered = flip_model_byte(downloaded)
        print("Flipped one byte inside model.onnx", flush=True)
        try:
            verify_bundle(tampered, trust, digest)
        except ArtifactError as error:
            assert error.code == "payload_hash_mismatch", error
            print(f"Agent rejected bundle  →  {error.code}", flush=True)
        else:
            raise AssertionError("Agent accepted altered bundle")
        rejected = client.post("/v1/artifacts", content=tampered, headers=upload)
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["error"]["code"] == "payload_hash_mismatch"
        print(
            "Registry rejected altered upload  →  422 payload_hash_mismatch", flush=True
        )


if __name__ == "__main__":
    main()
