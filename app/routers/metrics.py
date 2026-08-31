"""
Metrics API endpoints - reference data for metric types.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Metric
from ..schemas import MetricSchema

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/", response_model=list[MetricSchema])
def get_metrics(db: Session = Depends(get_db)):
    """Get all metric definitions (for frontend dropdowns, labels, etc.)."""
    metrics = db.query(Metric).order_by(Metric.label).all()
    return metrics
