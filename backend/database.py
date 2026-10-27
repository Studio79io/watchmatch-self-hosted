import os
from contextlib import contextmanager

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker


DATABASE_URL = os.getenv("DATABASE_URL") or URL.create(
    "postgresql+psycopg",
    username=os.getenv("DB_USER", "watchmatch"),
    password=os.getenv("DB_PASSWORD", "watchmatch"),
    host=os.getenv("DB_HOST", "postgres"),
    port=int(os.getenv("DB_PORT", "5432")),
    database=os.getenv("DB_NAME", "watchmatch"),
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def initialize():
    """Verify connectivity; Alembic owns all schema creation and upgrades."""
    with engine.connect():
        pass


@contextmanager
def connect():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
