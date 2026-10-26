"""
Synchronizes data from the FINoT API into PostgreSQL.

Readings are fetched in date-range batches to stay under the API's 10k-record
limit, with per-entity progress tracking so syncs are resumable.

Data format returned by FINoT:
{
    "index": ["2025-01-01T00:00:00.000Z", "2025-01-01T01:00:00.000Z", ...],
    "attributes": [
        {"attrName": "airTemperature", "values": [20.5, 21.0, ...]},
        {"attrName": "airRelativeHumidity", "values": [65, 66, ...]},
        ...
    ]
}
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .finot_client import client_from_profile
from ..models import Station, Reading, SyncLog, SyncProgress, SyncRun, FinotProfile
from ..config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# Known dynamic metrics (attributes with historical data)
DYNAMIC_METRICS = {
    "airTemperature", "airRelativeHumidity",
    "soilTemperature", "soilMoisture",
    "soilElectricalConductivity", "soilSalinity", "soilTDS",
    "leafWetness", "rain", "rainRate",
    "solarRadiation", "windSpeed", "gustWindSpeed", "windDirection",
    "PM10", "PM2d5", "PM1d0", "SO2",
    "vocIndex", "ambientIlluminance"
}


def parse_finot_timestamp(ts_string: str) -> Optional[datetime]:
    """
    Parse FINoT timestamp string to datetime.

    Handles formats like:
    - "2025-01-01T00:00:00.000Z"
    - "2025-01-01T00:00:00.000"
    - "2025-01-01T00:00:00Z"
    """
    if not ts_string:
        return None

    try:
        # Try with milliseconds and Z
        if ts_string.endswith("Z"):
            ts_string = ts_string[:-1] + "+00:00"

        return datetime.fromisoformat(ts_string)
    except ValueError as e:
        logger.warning(f"Could not parse timestamp '{ts_string}': {e}")
        return None


def extract_station_data(station_obj: dict) -> dict:
    """
    Extract station metadata from FINoT station object.

    Maps FINoT's nested structure to our flat schema.
    """
    # Basic fields
    data = {
        "id": station_obj.get("id", ""),
        "type": station_obj.get("type", "Unknown"),
        "attributes": station_obj,  # Store raw attributes for reference
    }

    # Extract from nested attributes with metadata
    def get_attr_value(attr_name: str) -> Optional[str]:
        attr = station_obj.get(attr_name, {})
        if isinstance(attr, dict):
            return attr.get("value")
        return None

    data["name"] = get_attr_value("name")
    data["serial_number"] = get_attr_value("serialNumber")
    data["product_id"] = get_attr_value("productId")
    data["connection_status"] = get_attr_value("connectionStatus") or "UNKNOWN"

    # Extract location
    location = station_obj.get("location", {})
    if isinstance(location, dict):
        loc_value = location.get("value", {})
        if isinstance(loc_value, dict) and loc_value.get("type") == "Point":
            coords = loc_value.get("coordinates", [])
            if len(coords) >= 2:
                data["longitude"] = coords[0]
                data["latitude"] = coords[1]

    # Last update timestamp
    time_instant = station_obj.get("TimeInstant", {})
    if isinstance(time_instant, dict):
        ts = time_instant.get("value")
        if ts:
            data["last_seen"] = parse_finot_timestamp(ts)

    return data


async def sync_stations(db: Session, profile: FinotProfile) -> int:
    """
    Sync stations from FINoT to database, tagged with the owning profile.

    Returns:
        Number of stations synced
    """
    logger.info(f"Starting stations sync for profile '{profile.name}'...")

    sync_log = SyncLog(
        sync_type="stations",
        status="running",
        started_at=datetime.now(timezone.utc)
    )
    db.add(sync_log)
    db.commit()

    try:
        client = client_from_profile(profile)
        stations_data = await client.fetch_stations()
        logger.info(f"Fetched {len(stations_data)} stations from FINoT")

        count = 0
        for station_obj in stations_data:
            station_data = extract_station_data(station_obj)
            station_data["profile_id"] = profile.id

            # Upsert station
            stmt = pg_insert(Station).values(**station_data)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "type": stmt.excluded.type,
                    "name": stmt.excluded.name,
                    "serial_number": stmt.excluded.serial_number,
                    "product_id": stmt.excluded.product_id,
                    "connection_status": stmt.excluded.connection_status,
                    "latitude": stmt.excluded.latitude,
                    "longitude": stmt.excluded.longitude,
                    "last_seen": stmt.excluded.last_seen,
                    "attributes": stmt.excluded.attributes,
                    "profile_id": stmt.excluded.profile_id,
                    "synced_at": datetime.now(timezone.utc)
                }
            )
            db.execute(stmt)

            # Initialize sync progress for this station
            progress_stmt = pg_insert(SyncProgress).values(
                entity_id=station_data["id"],
                entity_type=station_data["type"],
                is_complete=False,
                total_records=0
            )
            progress_stmt = progress_stmt.on_conflict_do_nothing(index_elements=["entity_id"])
            db.execute(progress_stmt)

            count += 1

        db.commit()

        sync_log.status = "completed"
        sync_log.records_count = count
        sync_log.completed_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(f"Synced {count} stations")
        return count

    except Exception as e:
        logger.error(f"Stations sync failed: {e}")
        sync_log.status = "failed"
        sync_log.error_message = str(e)
        sync_log.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise


async def sync_readings_batch(
    db: Session,
    profile: FinotProfile,
    force_full: bool = False,
    batch_days: int = None,
    start_date: datetime = None
) -> int:
    """
    Sync readings from FINoT using batch fetching with date ranges.

    This avoids the 10k record limit by:
    1. Fetching data in small date ranges (batch_days)
    2. Tracking progress per entity
    3. Resuming from where it left off

    Args:
        db: Database session
        profile: FinotProfile whose stations to sync readings for
        force_full: If True, re-sync from start_date regardless of progress
        batch_days: Number of days per batch (default from settings)
        start_date: Start date for sync (default from settings)

    Returns:
        Total number of readings synced
    """
    batch_days = batch_days or settings.sync_batch_days
    start_date = start_date or datetime.fromisoformat(settings.sync_start_date).replace(tzinfo=timezone.utc)

    logger.info(f"Starting batch readings sync for profile '{profile.name}' (batch_days={batch_days}, start_date={start_date})")

    # Create sync run record
    sync_run = SyncRun(status="running", started_at=datetime.now(timezone.utc))
    db.add(sync_run)
    db.commit()

    try:
        client = client_from_profile(profile)
        # Get stations belonging to this profile
        stations = db.query(Station).filter(Station.profile_id == profile.id).all()
        logger.info(f"Syncing readings for {len(stations)} stations")

        total_readings = 0
        entities_synced = 0
        failed_entities = 0

        for station in stations:
            progress = None
            try:
                # Get or create sync progress
                progress = db.query(SyncProgress).filter(
                    SyncProgress.entity_id == station.id
                ).first()

                if not progress:
                    progress = SyncProgress(
                        entity_id=station.id,
                        entity_type=station.type,
                        is_complete=False,
                        total_records=0
                    )
                    db.add(progress)
                    db.commit()

                # Determine start point. Resuming re-fetches a short overlap because
                # FINoT can ingest readings after their timestamp; inserts are idempotent.
                if force_full or not progress.last_synced_to:
                    current_start = start_date
                else:
                    current_start = progress.last_synced_to - timedelta(hours=settings.sync_overlap_hours)

                now = datetime.now(timezone.utc)
                station_readings = 0
                earliest = None
                latest = None

                # Batch through date ranges
                while current_start < now:
                    current_end = min(current_start + timedelta(days=batch_days), now)

                    logger.info(f"Fetching {station.id}: {current_start.date()} to {current_end.date()}")

                    try:
                        result = await client.fetch_historical_data(
                            entity_id=station.id,
                            from_date=current_start,
                            to_date=current_end
                        )

                        # Process data
                        index = result.get("index", [])
                        attributes = result.get("attributes", [])

                        if index:
                            batch_count = process_readings_batch(db, station.id, index, attributes)
                            station_readings += batch_count

                            # Track earliest/latest
                            for ts_str in index:
                                ts = parse_finot_timestamp(ts_str)
                                if ts:
                                    if earliest is None or ts < earliest:
                                        earliest = ts
                                    if latest is None or ts > latest:
                                        latest = ts

                    except Exception as e:
                        logger.warning(f"Batch fetch failed for {station.id} ({current_start} to {current_end}): {e}")
                        # Keep the last successful cursor so this batch is retried.
                        raise

                    # Update progress after each batch
                    progress.last_synced_to = current_end
                    progress.updated_at = datetime.now(timezone.utc)
                    db.commit()

                    current_start = current_end

                # Update final progress
                progress.total_records = (progress.total_records or 0) + station_readings
                if earliest:
                    progress.earliest_data = earliest
                if latest:
                    progress.latest_data = latest
                progress.is_complete = True
                progress.error_message = None
                db.commit()

                total_readings += station_readings
                entities_synced += 1
                logger.info(f"Synced {station_readings} readings for {station.name or station.id}")

            except Exception as e:
                logger.error(f"Failed to sync readings for station {station.id}: {e}")
                db.rollback()
                failed_entities += 1
                # Update progress with error
                if progress:
                    progress.error_message = str(e)
                    progress.is_complete = False
                    db.commit()
                continue

        # Update sync run
        sync_run.status = "failed" if failed_entities else "completed"
        sync_run.error_message = f"Failed to sync {failed_entities} station(s); retry to resume" if failed_entities else None
        sync_run.completed_at = datetime.now(timezone.utc)
        sync_run.entities_synced = entities_synced
        sync_run.records_synced = total_readings
        db.commit()

        logger.info(f"Batch sync complete: {total_readings} readings from {entities_synced} entities")
        return total_readings

    except Exception as e:
        logger.error(f"Batch readings sync failed: {e}")
        db.rollback()
        sync_run.status = "failed"
        sync_run.error_message = str(e)
        sync_run.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise


def process_readings_batch(db: Session, station_id: str, index: list, attributes: list) -> int:
    """
    Process a batch of readings and insert into database.

    Args:
        db: Database session
        station_id: Station ID
        index: List of timestamp strings
        attributes: List of attribute dicts with attrName and values

    Returns:
        Number of new readings inserted (duplicates are skipped)
    """
    readings_batch = []

    for attr in attributes:
        attr_name = attr.get("attrName")
        values = attr.get("values", [])

        # Skip if not a dynamic metric we care about
        if attr_name not in DYNAMIC_METRICS:
            continue

        # Create readings for each timestamp-value pair
        for i, timestamp_str in enumerate(index):
            if i >= len(values):
                break

            value = values[i]

            # Skip null values
            if value is None:
                continue

            timestamp = parse_finot_timestamp(timestamp_str)
            if not timestamp:
                continue

            readings_batch.append({
                "station_id": station_id,
                "metric": attr_name,
                "value": float(value),
                "recorded_at": timestamp
            })

    # Bulk insert with conflict handling
    inserted = 0
    if readings_batch:
        # Insert in chunks to avoid memory issues
        chunk_size = 1000
        for i in range(0, len(readings_batch), chunk_size):
            chunk = readings_batch[i:i + chunk_size]

            stmt = pg_insert(Reading).values(chunk)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["station_id", "recorded_at", "metric"]
            )
            inserted += db.execute(stmt).rowcount

        db.commit()

    return inserted


def recover_interrupted_syncs(db: Session) -> int:
    """
    Mark runs left as 'running' by a previous process as failed.

    Background syncs run in-process, so a redeploy or restart kills them without
    updating their rows; a stale 'running' row would otherwise block every
    future sync with "already_running". Call once at startup.
    """
    now = datetime.now(timezone.utc)
    message = "Interrupted by API restart; retry to resume"
    count = 0
    for model in (SyncRun, SyncLog):
        for row in db.query(model).filter(model.status == "running").all():
            row.status = "failed"
            row.error_message = message
            row.completed_at = now
            count += 1
    db.commit()
    if count:
        logger.warning(f"Marked {count} interrupted sync record(s) as failed")
    return count


async def full_sync(db: Session, profile: FinotProfile, force_full: bool = False) -> dict:
    """
    Run a complete sync for a single profile: stations first, then readings.

    Args:
        db: Database session
        profile: FinotProfile to sync
        force_full: If True, re-sync all data from start_date

    Returns:
        Dict with sync results
    """
    stations_count = await sync_stations(db, profile)
    readings_count = await sync_readings_batch(db, profile, force_full=force_full)

    return {
        "stations_synced": stations_count,
        "readings_synced": readings_count,
        "completed_at": datetime.now(timezone.utc).isoformat()
    }


def get_sync_status(db: Session, profile_id: Optional[int] = None) -> dict:
    """Get current sync status summary, optionally scoped to one profile."""
    from sqlalchemy import func

    station_query = db.query(Station)
    progress_query = db.query(SyncProgress).join(Station, SyncProgress.entity_id == Station.id)
    if profile_id is not None:
        station_query = station_query.filter(Station.profile_id == profile_id)
        progress_query = progress_query.filter(Station.profile_id == profile_id)

    # Count stations
    station_count = station_query.count()

    # Count readings (scoped via station join when a profile is given)
    if profile_id is not None:
        reading_count = db.query(func.count(Reading.id)).join(
            Station, Reading.station_id == Station.id
        ).filter(Station.profile_id == profile_id).scalar() or 0
    else:
        reading_count = db.query(func.count(Reading.id)).scalar() or 0

    # Count sync progress
    complete_count = progress_query.filter(SyncProgress.is_complete == True).count()
    pending_count = progress_query.filter(SyncProgress.is_complete == False).count()

    # Get latest sync run
    latest_run = db.query(SyncRun).order_by(SyncRun.started_at.desc()).first()

    return {
        "stations_in_db": station_count,
        "readings_in_db": reading_count,
        "entities_fully_synced": complete_count,
        "entities_pending": pending_count,
        "latest_sync_run": {
            "id": latest_run.id,
            "status": latest_run.status,
            "started_at": latest_run.started_at.isoformat() if latest_run.started_at else None,
            "completed_at": latest_run.completed_at.isoformat() if latest_run.completed_at else None,
            "entities_synced": latest_run.entities_synced,
            "records_synced": latest_run.records_synced,
            "error": latest_run.error_message
        } if latest_run else None
    }


def get_sync_progress(db: Session, profile_id: Optional[int] = None) -> list:
    """Get detailed sync progress for all entities, optionally scoped to one profile."""
    query = db.query(SyncProgress).join(Station, SyncProgress.entity_id == Station.id)
    if profile_id is not None:
        query = query.filter(Station.profile_id == profile_id)
    progress_list = query.order_by(SyncProgress.entity_id).all()

    return [
        {
            "entity_id": p.entity_id,
            "entity_type": p.entity_type,
            "is_complete": p.is_complete,
            "last_synced_to": p.last_synced_to.isoformat() if p.last_synced_to else None,
            "earliest_data": p.earliest_data.isoformat() if p.earliest_data else None,
            "latest_data": p.latest_data.isoformat() if p.latest_data else None,
            "total_records": p.total_records,
            "error": p.error_message
        }
        for p in progress_list
    ]
