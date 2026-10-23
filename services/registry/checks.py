"""Bounded, authenticated dependency probes. No fleet data is written."""

from typing import Protocol

import psycopg
import urllib3
from minio import Minio

from services.registry.config import Settings


class DependencyChecks(Protocol):
    def database(self) -> None: ...

    def object_store(self) -> None: ...


class LiveDependencyChecks:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.object_client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_root_user,
            secret_key=settings.minio_root_password.get_secret_value(),
            secure=settings.minio_secure,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(
                    connect=settings.dependency_timeout_seconds,
                    read=settings.dependency_timeout_seconds,
                ),
                retries=False,
            ),
        )

    def database(self) -> None:
        statement_timeout_ms = self.settings.dependency_timeout_seconds * 1000
        with psycopg.connect(
            host=self.settings.postgres_host,
            port=self.settings.postgres_port,
            dbname=self.settings.postgres_db,
            user=self.settings.postgres_user,
            password=self.settings.postgres_password.get_secret_value(),
            connect_timeout=self.settings.dependency_timeout_seconds,
            options=f"-c statement_timeout={statement_timeout_ms}",
        ) as connection:
            result = connection.execute("SELECT 1").fetchone()
            if result != (1,):
                raise RuntimeError("Unexpected database probe response")

    def object_store(self) -> None:
        # An authenticated S3 operation checks API availability and credentials.
        self.object_client.list_buckets()
