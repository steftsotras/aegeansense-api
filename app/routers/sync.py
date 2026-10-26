"""
Sync API endpoints for triggering and monitoring data synchronization.

All endpoints act on a single FinotProfile (multi-tenant FINoT account) and
are admin-only, since syncing is now an explicit per-profile admin action
rather than an automatic background job.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from datetime import datetime, timedelta, timezone
from typing import Optional
import asyncio
import logging

from ..database import get_db
from ..dependencies import get_current_admin
from ..models import AdminUser, SyncLog, SyncRun, FinotProfile
from ..schemas import SyncResult
from ..services.sync_service import (
    full_sync, sync_stations, sync_readings_batch,
    get_sync_status, get_sync_progress
)
from ..services.finot_client import client_from_profile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])

# Keeps a reference to the running background sync task so it isn't garbage-collected.
_sync_task = None


def sync_in_progress(db: Session) -> bool:
    """True if a background sync task is alive or a run is recorded as running."""
    if _sync_task is not None and not _sync_task.done():
        return True
    return db.query(SyncRun).filter(SyncRun.status == "running").count() > 0


def start_background_sync(profile_ids: list[int], force_full: bool = False) -> None:
    """Run full_sync for each profile, one after another, in a background task."""
    global _sync_task

    async def run_sync():
        from ..database import SessionLocal
        for profile_id in profile_ids:
            session = SessionLocal()
            try:
                profile = session.get(FinotProfile, profile_id)
                if profile and profile.is_active:
                    await full_sync(session, profile, force_full=force_full)
            except Exception as e:
                # full_sync has already recorded the failure on its SyncRun/SyncLog.
                logger.error(f"Background sync failed for profile {profile_id}: {e}")
            finally:
                session.close()

    _sync_task = asyncio.create_task(run_sync())


def _get_active_profile(db: Session, profile_id: int) -> FinotProfile:
    profile = db.query(FinotProfile).filter(FinotProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="FINoT profile not found")
    if not profile.is_active:
        raise HTTPException(status_code=409, detail=f"Profile '{profile.name}' is not active")
    return profile


@router.post("/", response_model=SyncResult)
async def trigger_sync(
    profile_id: int = Query(..., description="FinotProfile to sync"),
    force_full: bool = Query(False, description="Force full re-sync from start date"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Trigger a full sync from FINoT (stations + readings) for one profile."""
    profile = _get_active_profile(db, profile_id)

    try:
        result = await full_sync(db, profile, force_full=force_full)
        return SyncResult(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start")
async def start_sync_background(
    profile_id: int = Query(..., description="FinotProfile to sync"),
    force_full: bool = Query(False, description="Force full re-sync from start date"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """
    Start a background sync job for one profile.

    This will fetch all of the profile's entities and their historical data
    from FINoT and store them in the local PostgreSQL database.

    Use force_full=true to re-sync all data from the start date.
    """
    _get_active_profile(db, profile_id)

    if sync_in_progress(db):
        return {
            "status": "already_running",
            "message": "A sync is already in progress",
            "check_status_at": "/api/sync/status"
        }

    start_background_sync([profile_id], force_full=force_full)

    return {
        "status": "started",
        "message": "Sync started in background",
        "profile_id": profile_id,
        "force_full": force_full,
        "check_status_at": "/api/sync/status"
    }


@router.post("/stations")
async def trigger_stations_sync(
    profile_id: int = Query(..., description="FinotProfile to sync"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Sync only station metadata for one profile."""
    profile = _get_active_profile(db, profile_id)

    count = await sync_stations(db, profile)
    return {"stations_synced": count, "completed_at": datetime.now(timezone.utc)}


@router.post("/readings")
async def trigger_readings_sync(
    profile_id: int = Query(..., description="FinotProfile to sync"),
    days: int = Query(30, description="Number of days of history to fetch"),
    force_full: bool = Query(False, description="Force full re-sync"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """
    Sync readings for one profile using batch fetching.

    Uses date-range batching to avoid the 10k record limit.
    """
    profile = _get_active_profile(db, profile_id)

    start_date = datetime.now(timezone.utc) - timedelta(days=days)
    count = await sync_readings_batch(db, profile, force_full=force_full, start_date=start_date)
    return {"readings_synced": count, "days": days, "completed_at": datetime.now(timezone.utc)}


@router.get("/status")
def get_status(
    profile_id: Optional[int] = Query(None, description="Scope to one FinotProfile"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Get current sync status, optionally scoped to one profile."""
    status = get_sync_status(db, profile_id=profile_id)
    status["sync_running"] = sync_in_progress(db)
    return status


@router.get("/progress")
def get_progress(
    profile_id: Optional[int] = Query(None, description="Scope to one FinotProfile"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Get detailed sync progress for all entities, optionally scoped to one profile."""
    progress = get_sync_progress(db, profile_id=profile_id)
    return {
        "count": len(progress),
        "entities": progress
    }


@router.get("/logs")
def get_sync_logs(
    limit: int = 20,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Get recent per-step sync logs (e.g. station-metadata syncs)."""
    logs = db.query(SyncLog).order_by(desc(SyncLog.started_at)).limit(limit).all()
    return [
        {
            "id": log.id,
            "type": log.sync_type,
            "status": log.status,
            "started_at": log.started_at,
            "completed_at": log.completed_at,
            "records": log.records_count,
            "error": log.error_message
        }
        for log in logs
    ]


@router.get("/runs")
def get_sync_runs(
    limit: int = 20,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Get recent sync runs (across all profiles)."""
    runs = db.query(SyncRun).order_by(desc(SyncRun.started_at)).limit(limit).all()
    return [
        {
            "id": run.id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "entities_synced": run.entities_synced,
            "records_synced": run.records_synced,
            "error": run.error_message
        }
        for run in runs
    ]


@router.post("/test")
async def test_connection(
    profile_id: int = Query(..., description="FinotProfile to test"),
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Test connection to FINoT API for one profile."""
    profile = _get_active_profile(db, profile_id)

    client = client_from_profile(profile)
    result = await client.test_connection()
    return {
        "profile_id": profile_id,
        "configured": True,
        **result
    }
