"""
Database connection and session management.
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def initialize_database():
    """Create the schema and seed reference data on a fresh database."""
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    connection = engine.raw_connection()
    cursor = connection.cursor()
    try:
        # Execute without SQLAlchemy's parameter interpolation because the seed
        # data legitimately contains percent symbols such as humidity units.
        cursor.execute(schema_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def get_db():
    """FastAPI dependency for database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
