"""
database.py
-----------
Configuration de la connexion PostgreSQL via SQLAlchemy 2.0.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Crée les tables en base si elles n'existent pas encore."""
    from models import (  # noqa: F401
        Adresse,
        Candidate,
        CentreInteret,
        Certification,
        Competence,
        Experience,
        Formation,
        Langue,
        Mission,
        MissionMatch,
        Pays,
        Ville,
    )
    Base.metadata.create_all(bind=engine)