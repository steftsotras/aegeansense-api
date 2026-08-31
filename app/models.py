"""
SQLAlchemy models for the AegeanSense environmental database (see schema.sql).
"""
from sqlalchemy import Column, String, Float, DateTime, BigInteger, Integer, Text, ForeignKey, Index, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from .database import Base


class FinotProfile(Base):
    """A FINoT tenant account (URL + credentials) whose stations sync into our DB."""
    __tablename__ = "finot_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    finot_url = Column(String, nullable=False)
    finot_username = Column(String, nullable=False)
    finot_password_encrypted = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    stations = relationship("Station", back_populates="profile")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Station(Base):
    __tablename__ = "stations"

    id = Column(String, primary_key=True)  # FINoT entity_id directly
    type = Column(String, nullable=False)
    name = Column(String)
    serial_number = Column(String)
    product_id = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    connection_status = Column(String, default="UNKNOWN")
    last_seen = Column(DateTime(timezone=True))
    attributes = Column(JSONB)  # Raw attributes from FINoT
    synced_at = Column(DateTime(timezone=True), server_default=func.now())
    profile_id = Column(Integer, ForeignKey("finot_profiles.id", ondelete="SET NULL"))

    readings = relationship("Reading", back_populates="station", cascade="all, delete-orphan")
    sync_progress = relationship("SyncProgress", back_populates="station", uselist=False, cascade="all, delete-orphan")
    profile = relationship("FinotProfile", back_populates="stations")


class Metric(Base):
    __tablename__ = "metrics"
    
    name = Column(String, primary_key=True)
    label = Column(String, nullable=False)
    unit_code = Column(String)
    unit_symbol = Column(String)
    description = Column(Text)


class Reading(Base):
    __tablename__ = "readings"
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    station_id = Column(String, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    metric = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    
    station = relationship("Station", back_populates="readings")
    
    __table_args__ = (
        Index('idx_readings_unique', 'station_id', 'recorded_at', 'metric', unique=True),
    )


class SyncProgress(Base):
    """Tracks per-entity sync state for batch syncing to avoid 10k limit."""
    __tablename__ = "sync_progress"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String, ForeignKey("stations.id", ondelete="CASCADE"), nullable=False, unique=True)
    entity_type = Column(String)
    is_complete = Column(Boolean, default=False)  # True if synced up to current date
    last_synced_to = Column(DateTime(timezone=True))  # Last date we synced data up to
    earliest_data = Column(DateTime(timezone=True))  # Earliest data point found
    latest_data = Column(DateTime(timezone=True))  # Most recent data point found
    total_records = Column(Integer, default=0)  # Count of records synced
    error_message = Column(Text)  # Last error if any
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    station = relationship("Station", back_populates="sync_progress")


class SyncRun(Base):
    """Tracks each sync job execution."""
    __tablename__ = "sync_runs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String, default="running")  # running, completed, failed
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    entities_synced = Column(Integer, default=0)
    records_synced = Column(Integer, default=0)
    error_message = Column(Text)


class SyncLog(Base):
    """Per-step sync log (e.g. station-metadata syncs), exposed via /api/sync/logs."""
    __tablename__ = "sync_log"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    status = Column(String, default="running")
    records_count = Column(Integer, default=0)
    error_message = Column(Text)
