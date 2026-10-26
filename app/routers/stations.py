"""
Stations API endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..database import get_db
from ..models import Station, Metric
from ..schemas import StationWithMetrics, MetricReading, HistoricalData, MetricSeries, DataPoint

router = APIRouter(prefix="/api/stations", tags=["stations"])


@router.get("/", response_model=list[StationWithMetrics])
def get_stations(
    type: Optional[str] = Query(None, description="Filter by station type"),
    status: Optional[str] = Query(None, description="Filter by connection status"),
    db: Session = Depends(get_db)
):
    """Get all stations with their latest metrics."""
    query = db.query(Station)

    if type:
        query = query.filter(Station.type == type)
    if status:
        query = query.filter(Station.connection_status == status)

    stations = query.order_by(Station.name).all()

    result = []
    for station in stations:
        # Get latest reading for each metric
        latest = db.execute(text("""
            SELECT DISTINCT ON (metric)
                metric, value, recorded_at
            FROM readings
            WHERE station_id = :sid
            ORDER BY metric, recorded_at DESC
        """), {"sid": station.id}).fetchall()

        # Get metric labels
        metrics_dict = {}
        for row in latest:
            metric_info = db.query(Metric).filter(Metric.name == row.metric).first()
            metrics_dict[row.metric] = MetricReading(
                value=row.value,
                recorded_at=row.recorded_at,
                label=metric_info.label if metric_info else row.metric,
                unit=metric_info.unit_symbol if metric_info else None
            )

        result.append(StationWithMetrics(
            id=station.id,
            type=station.type,
            name=station.name,
            serial_number=station.serial_number,
            product_id=station.product_id,
            latitude=station.latitude,
            longitude=station.longitude,
            connection_status=station.connection_status,
            last_seen=station.last_seen,
            synced_at=station.synced_at,
            metrics=metrics_dict
        ))

    return result


@router.get("/{station_id}", response_model=StationWithMetrics)
def get_station(station_id: str, db: Session = Depends(get_db)):
    """Get a single station by ID."""
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # Get latest readings
    latest = db.execute(text("""
        SELECT DISTINCT ON (metric) metric, value, recorded_at
        FROM readings WHERE station_id = :sid
        ORDER BY metric, recorded_at DESC
    """), {"sid": station_id}).fetchall()

    metrics_dict = {}
    for row in latest:
        metric_info = db.query(Metric).filter(Metric.name == row.metric).first()
        metrics_dict[row.metric] = MetricReading(
            value=row.value,
            recorded_at=row.recorded_at,
            label=metric_info.label if metric_info else row.metric,
            unit=metric_info.unit_symbol if metric_info else None
        )

    return StationWithMetrics(
        id=station.id,
        type=station.type,
        name=station.name,
        serial_number=station.serial_number,
        product_id=station.product_id,
        latitude=station.latitude,
        longitude=station.longitude,
        connection_status=station.connection_status,
        last_seen=station.last_seen,
        synced_at=station.synced_at,
        metrics=metrics_dict
    )


@router.get("/{station_id}/readings", response_model=HistoricalData)
def get_readings(
    station_id: str,
    metrics: Optional[str] = Query(None, description="Comma-separated metric names"),
    from_date: Optional[datetime] = Query(None, alias="from"),
    to_date: Optional[datetime] = Query(None, alias="to"),
    agg: Optional[str] = Query(None, description="Aggregation: hour, day, month"),
    db: Session = Depends(get_db)
):
    """Get historical readings for a station."""
    # Check station exists
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    # Default date range
    if not to_date:
        to_date = datetime.now(timezone.utc)
    if not from_date:
        from_date = to_date - timedelta(days=30)

    # Build query
    metric_filter = ""
    params = {"sid": station_id, "from_date": from_date, "to_date": to_date}
    if metrics:
        metric_list = [m.strip() for m in metrics.split(",")]
        metric_filter = "AND metric = ANY(:metrics)"
        params["metrics"] = metric_list

    if agg and agg in ('hour', 'day', 'month'):
        sql = f"""
            SELECT date_trunc('{agg}', recorded_at) as ts, metric, AVG(value) as value
            FROM readings
            WHERE station_id = :sid AND recorded_at >= :from_date AND recorded_at <= :to_date
            {metric_filter}
            GROUP BY ts, metric
            ORDER BY metric, ts
        """
    else:
        sql = f"""
            SELECT recorded_at as ts, metric, value
            FROM readings
            WHERE station_id = :sid AND recorded_at >= :from_date AND recorded_at <= :to_date
            {metric_filter}
            ORDER BY metric, recorded_at
        """

    rows = db.execute(text(sql), params).fetchall()

    # Group by metric
    series_dict = {}
    for row in rows:
        if row.metric not in series_dict:
            metric_info = db.query(Metric).filter(Metric.name == row.metric).first()
            series_dict[row.metric] = {
                "metric": row.metric,
                "label": metric_info.label if metric_info else row.metric,
                "unit": metric_info.unit_symbol if metric_info else None,
                "data": []
            }
        series_dict[row.metric]["data"].append(DataPoint(recorded_at=row.ts, value=row.value))

    return HistoricalData(
        station_id=station_id,
        from_date=from_date,
        to_date=to_date,
        series=[MetricSeries(**s) for s in series_dict.values()]
    )


@router.get("/{station_id}/latest")
def get_latest(station_id: str, db: Session = Depends(get_db)):
    """Get latest reading for each metric (quick endpoint)."""
    station = db.query(Station).filter(Station.id == station_id).first()
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    latest = db.execute(text("""
        SELECT DISTINCT ON (metric) metric, value, recorded_at
        FROM readings WHERE station_id = :sid
        ORDER BY metric, recorded_at DESC
    """), {"sid": station_id}).fetchall()

    result = {}
    for row in latest:
        metric_info = db.query(Metric).filter(Metric.name == row.metric).first()
        result[row.metric] = {
            "value": row.value,
            "recorded_at": row.recorded_at.isoformat(),
            "label": metric_info.label if metric_info else row.metric,
            "unit": metric_info.unit_symbol if metric_info else None
        }

    return result
