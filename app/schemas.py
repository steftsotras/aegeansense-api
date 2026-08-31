"""
Pydantic schemas for API request/response validation.
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional


# ===== Station Schemas =====

class StationBase(BaseModel):
    id: str
    type: str
    name: Optional[str] = None
    serial_number: Optional[str] = None
    product_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    connection_status: str = "UNKNOWN"
    last_seen: Optional[datetime] = None
    synced_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MetricReading(BaseModel):
    value: float
    recorded_at: datetime
    label: str
    unit: Optional[str] = None


class StationWithMetrics(StationBase):
    metrics: dict[str, MetricReading] = {}


# ===== Metric Schemas =====

class MetricSchema(BaseModel):
    name: str
    label: str
    unit_code: Optional[str] = None
    unit_symbol: Optional[str] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True


# ===== Reading Schemas =====

class DataPoint(BaseModel):
    recorded_at: datetime
    value: float


class MetricSeries(BaseModel):
    metric: str
    label: str
    unit: Optional[str] = None
    data: list[DataPoint]


class HistoricalData(BaseModel):
    station_id: str
    from_date: datetime
    to_date: datetime
    series: list[MetricSeries]


# ===== Sync Schemas =====

class SyncResult(BaseModel):
    stations_synced: int
    readings_synced: int
    completed_at: datetime


# ===== Admin / Auth Schemas =====

class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ===== FinotProfile Schemas =====

class FinotProfileCreate(BaseModel):
    name: str
    finot_url: str
    finot_username: str
    finot_password: str
    is_active: bool = True


class FinotProfileUpdate(BaseModel):
    name: Optional[str] = None
    finot_url: Optional[str] = None
    finot_username: Optional[str] = None
    finot_password: Optional[str] = None
    is_active: Optional[bool] = None


class FinotProfileOut(BaseModel):
    id: int
    name: str
    finot_url: str
    finot_username: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
