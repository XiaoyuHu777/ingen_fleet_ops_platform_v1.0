"""Validate a package, sign its manifest digest, and write the download bundle."""

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from agent.artifacts import (
    MAX_BUNDLE_BYTES,
    archive,
    canonical_json,
    read_manifest,
    sha256,
    verify_payload,
)
from agent.verify import SIGNING_DOMAIN, Signature, key_id


def generate_key(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    trust = directory / "public"
    trust.mkdir(exist_ok=True)
    private = Ed25519PrivateKey.generate()
    public = private.public_key()
    destination = directory / "signing.pem"
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(
                private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
            )
        with (trust / f"{key_id(public)}.pem").open("xb") as stream:
            stream.write(
                public.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            )
    except OSError:
        destination.unlink()
        raise
    print(f"Created development key {key_id(public)} in {directory}")


def sign_package(package: Path, key_path: Path, output: Path) -> str:
    manifest_bytes = (package / "manifest.json").read_bytes()
    manifest = read_manifest(manifest_bytes)
    with (package / "payload.tar").open("rb") as payload_file:
        payload = payload_file.read(MAX_BUNDLE_BYTES + 1)
    verify_payload(manifest, payload)
    key = load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Signer requires an Ed25519 private key")
    digest = sha256(manifest_bytes)
    signature = Signature(
        algorithm="Ed25519",
        key_id=key_id(key.public_key()),
        signature=key.sign(SIGNING_DOMAIN + bytes.fromhex(digest)).hex(),
    )
    bundle = archive(
        {
            "manifest.json": manifest_bytes,
            "payload.tar": payload,
            "signature.json": canonical_json(signature),
        }
    )
    if len(bundle) > MAX_BUNDLE_BYTES:
        raise ValueError("Signed bundle exceeds the registry upload limit")
    output.write_bytes(bundle)
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen")
    keygen.add_argument("directory", type=Path)
    sign = commands.add_parser("sign")
    sign.add_argument("package", type=Path)
    sign.add_argument("--key", required=True, type=Path)
    sign.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "keygen":
        generate_key(args.directory)
    else:
        print(f"signed sha256:{sign_package(args.package, args.key, args.out)}")


if __name__ == "__main__":
    main()
