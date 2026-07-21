"""Database engine, session factory, and FastAPI dependency."""

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///expenseflow.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""


def init_db() -> None:
    """Create all tables. Imports models so they are registered on the metadata."""
    from app import models  # noqa: F401  (register mappers before create_all)

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a scoped database session and close it when the request ends."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
