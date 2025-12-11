import os

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# Default: local SQLite for development
DEFAULT_SQLITE_URL = "sqlite:///./reports.db"

# If DATABASE_URL env var is set (e.g. on Render), use that instead
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_SQLITE_URL)

# For SQLite we need the special connect_args; for Postgres we don't
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class ReportRecord(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    timestamp = Column(DateTime)


def init_db():
    Base.metadata.create_all(bind=engine)
