import os
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

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


class IncidentRecord(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String, nullable=False, default="pending", index=True)  # pending|confirmed|closed
    type = Column(String, nullable=False, index=True)  # dead|injured

    centroid_lat = Column(Float, nullable=False)
    centroid_lon = Column(Float, nullable=False)

    first_report_at = Column(DateTime, nullable=False)
    last_report_at = Column(DateTime, nullable=False)

    report_count = Column(Integer, nullable=False, default=0)
    unique_device_count = Column(Integer, nullable=False, default=0)

    lat_bucket = Column(Integer, nullable=False, index=True)
    lon_bucket = Column(Integer, nullable=False, index=True)

    reports = relationship("ReportRecord", back_populates="incident")


class ReportRecord(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    timestamp = Column(DateTime)

    # --- anti-spam + grouping (new) ---
    received_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    device_hash = Column(String, nullable=True, index=True)
    ip_hash = Column(String, nullable=True)
    ua_hash = Column(String, nullable=True)

    accepted = Column(Boolean, nullable=False, default=True, index=True)
    reject_reason = Column(String, nullable=True)

    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=True, index=True)
    incident = relationship("IncidentRecord", back_populates="reports")


def _try_exec(conn, sql: str):
    # Best-effort schema patching: ignore "already exists" type errors
    try:
        conn.execute(text(sql))
    except Exception:
        pass


def ensure_schema():
    """
    create_all creates new tables, but does NOT add columns to existing ones.
    This function safely adds new columns/indexes if missing.
    """
    is_sqlite = DATABASE_URL.startswith("sqlite")
    with engine.begin() as conn:
        if is_sqlite:
            # SQLite: no IF NOT EXISTS for ADD COLUMN; rely on try/except
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN received_at TIMESTAMP")
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN device_hash VARCHAR")
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN ip_hash VARCHAR")
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN ua_hash VARCHAR")
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN accepted BOOLEAN")
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN reject_reason VARCHAR")
            _try_exec(conn, "ALTER TABLE reports ADD COLUMN incident_id INTEGER")
        else:
            # Postgres: do it properly + idempotently
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS received_at timestamptz")
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS device_hash text")
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS ip_hash text")
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS ua_hash text")
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS accepted boolean")
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS reject_reason text")
            _try_exec(conn, "ALTER TABLE public.reports ADD COLUMN IF NOT EXISTS incident_id integer")

        # Defaults / backfill (safe to run repeatedly)
        if is_sqlite:
            _try_exec(conn, "UPDATE reports SET accepted = 1 WHERE accepted IS NULL")
        else:
            _try_exec(conn, "UPDATE public.reports SET accepted = COALESCE(accepted, true)")
        _try_exec(conn, "UPDATE reports SET received_at = COALESCE(received_at, timestamp)")

        # Indexes
        _try_exec(conn, "CREATE INDEX IF NOT EXISTS idx_reports_device_hash ON reports (device_hash)")
        _try_exec(conn, "CREATE INDEX IF NOT EXISTS idx_reports_received_at ON reports (received_at)")
        _try_exec(conn, "CREATE INDEX IF NOT EXISTS idx_reports_incident_id ON reports (incident_id)")
        _try_exec(conn, "CREATE INDEX IF NOT EXISTS idx_incidents_bucket ON incidents (lat_bucket, lon_bucket, last_report_at)")
        _try_exec(conn, "CREATE INDEX IF NOT EXISTS idx_incidents_status_last ON incidents (status, last_report_at)")


def init_db():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
