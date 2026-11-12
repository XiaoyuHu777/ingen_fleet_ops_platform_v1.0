"""Verify a signed model bundle using public keys provisioned out of band."""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import StringConstraints, ValidationError

from agent.artifacts import (
    MAX_BUNDLE_BYTES,
    ArtifactError,
    Contract,
    HexDigest,
    Manifest,
    archive,
    canonical_json,
    read_archive,
    read_manifest,
    sha256,
    verify_payload,
)

SIGNING_DOMAIN = b"ingen-fleet-artifact-manifest-v1\x00"
BUNDLE_MEMBERS = {"manifest.json", "payload.tar", "signature.json"}


class Signature(Contract):
    algorithm: Literal["Ed25519"]
    key_id: HexDigest
    signature: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{128}$")]


@dataclass(frozen=True)
class VerifiedArtifact:
    digest: str
    manifest: Manifest
    key_id: str


def key_id(key: Ed25519PublicKey) -> str:
    return sha256(key.public_bytes_raw())


def load_trust(directory: Path) -> dict[str, Ed25519PublicKey]:
    trusted: dict[str, Ed25519PublicKey] = {}
    for pem in sorted(directory.glob("*.pem")):
        key = load_pem_public_key(pem.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Trust directory requires Ed25519 public keys")
        trusted[key_id(key)] = key
    return trusted


def verify_bundle(
    data: bytes,
    trusted: dict[str, Ed25519PublicKey],
    expected_digest: str | None = None,
) -> VerifiedArtifact:
    files = read_archive(data, BUNDLE_MEMBERS)
    if "signature.json" not in files:
        raise ArtifactError("unsigned", "Artifact has no detached signature")
    if files.keys() != BUNDLE_MEMBERS:
        raise ArtifactError("invalid_bundle", "Signed bundle is missing required files")

    manifest_bytes = files["manifest.json"]
    manifest = read_manifest(manifest_bytes)
    digest = sha256(manifest_bytes)
    if expected_digest is not None and digest != expected_digest:
        raise ArtifactError("digest_mismatch", "Manifest digest differs from target")
    try:
        signature_bytes = files["signature.json"]
        if len(signature_bytes) > 1024:
            raise ValueError("Signature envelope exceeds the size limit")
        signature = Signature.model_validate_json(signature_bytes)
        if canonical_json(signature) != signature_bytes:
            raise ValueError("Noncanonical signature envelope")
    except (ValidationError, ValueError) as error:
        raise ArtifactError(
            "invalid_signature", "Invalid signature envelope"
        ) from error
    public_key = trusted.get(signature.key_id)
    if public_key is None or key_id(public_key) != signature.key_id:
        raise ArtifactError("wrong_key", "Signing key is absent from the trust set")
    try:
        public_key.verify(
            bytes.fromhex(signature.signature), SIGNING_DOMAIN + bytes.fromhex(digest)
        )
    except InvalidSignature as error:
        raise ArtifactError(
            "invalid_signature", "Ed25519 signature mismatch"
        ) from error
    verify_payload(manifest, files["payload.tar"])
    if archive(files) != data:
        raise ArtifactError("invalid_bundle", "Signed bundle must use canonical USTAR")
    return VerifiedArtifact(digest, manifest, signature.key_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--trust", required=True, type=Path)
    parser.add_argument("--digest", help="Expected bare SHA-256 or sha256:<hex>")
    args = parser.parse_args()
    try:
        with args.bundle.open("rb") as bundle:
            data = bundle.read(MAX_BUNDLE_BYTES + 1)
        result = verify_bundle(
            data,
            load_trust(args.trust),
            args.digest and args.digest.removeprefix("sha256:"),
        )
    except ArtifactError as error:
        sys.exit(f"{error.code}: {error}")
    except (OSError, ValueError) as error:
        sys.exit(f"verification_error: {error}")
    print(f"verified sha256:{result.digest}")


if __name__ == "__main__":
    main()
