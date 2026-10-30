"""Build scoped SQLAlchemy sessions without placing credentials in URLs or logs."""

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.registry.config import Settings


def database_url(settings: Settings) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


def make_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = create_engine(
        database_url(settings),
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.dependency_timeout_seconds},
    )
    return sessionmaker(bind=engine, expire_on_commit=False)
