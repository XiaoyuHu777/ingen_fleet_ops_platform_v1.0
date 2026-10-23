"""Install checksum-verified, pinned local CI tools without changing global PATH."""

import hashlib
import io
import platform
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".tools" / "bin"


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def verify(content: bytes, manifest: bytes, filename: str) -> None:
    matches = [
        line.split()[0]
        for line in manifest.decode().splitlines()
        if line.split() and line.split()[-1].lstrip("*") == filename
    ]
    if len(matches) != 1 or hashlib.sha256(content).hexdigest() != matches[0]:
        raise RuntimeError(f"Checksum mismatch for {filename}")


def main() -> None:
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(
        platform.machine()
    )
    if not system or not arch:
        raise RuntimeError("Only macOS/Linux arm64/amd64 are supported")
    DEST.mkdir(parents=True, exist_ok=True)
    tflint_version = "0.65.0"
    filename = f"tflint_{system}_{arch}.zip"
    base = f"https://github.com/terraform-linters/tflint/releases/download/v{tflint_version}"
    archive = download(f"{base}/{filename}")
    verify(archive, download(f"{base}/checksums.txt"), filename)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        (DEST / "tflint").write_bytes(zipped.read("tflint"))

    gitleaks_version = "8.30.1"
    gitleaks_arch = "x64" if arch == "amd64" else arch
    filename = f"gitleaks_{gitleaks_version}_{system}_{gitleaks_arch}.tar.gz"
    base = f"https://github.com/gitleaks/gitleaks/releases/download/v{gitleaks_version}"
    archive = download(f"{base}/{filename}")
    verify(
        archive,
        download(f"{base}/gitleaks_{gitleaks_version}_checksums.txt"),
        filename,
    )
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.extractfile("gitleaks")
        if member is None:
            raise RuntimeError("gitleaks binary missing from official archive")
        (DEST / "gitleaks").write_bytes(member.read())
    for binary in ("tflint", "gitleaks"):
        (DEST / binary).chmod(0o755)
        print(f"Installed checksum-verified {binary} in .tools/bin")


if __name__ == "__main__":
    main()
