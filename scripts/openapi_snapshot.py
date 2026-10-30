"""Export the running app's OpenAPI contract or check the committed snapshot."""

import argparse
import json
from pathlib import Path

from pydantic import SecretStr

from services.registry.app import create_app
from services.registry.config import Settings

SNAPSHOT = Path("docs/openapi-v1.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    settings = Settings(
        _env_file=None,
        postgres_db="schema",
        postgres_user="schema",
        postgres_password=SecretStr("schema-only"),
        minio_root_user="schema",
        minio_root_password=SecretStr("schema-only"),
    )
    spec = json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not SNAPSHOT.exists() or SNAPSHOT.read_text() != spec:
            raise SystemExit(
                "OpenAPI snapshot is stale; run python -m scripts.openapi_snapshot"
            )
    else:
        SNAPSHOT.write_text(spec)


if __name__ == "__main__":
    main()
