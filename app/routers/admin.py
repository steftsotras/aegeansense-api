"""
Admin login and FINoT profile management endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_admin
from ..models import AdminUser, FinotProfile
from ..schemas import (
    AdminLoginRequest, AdminLoginResponse,
    FinotProfileCreate, FinotProfileUpdate, FinotProfileOut
)
from ..services.auth import verify_password, create_access_token
from ..services.crypto import encrypt_password

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/login", response_model=AdminLoginResponse)
def login(payload: AdminLoginRequest, db: Session = Depends(get_db)):
    """Authenticate an admin and return a bearer token."""
    admin = db.query(AdminUser).filter(AdminUser.email == payload.email).first()
    if not admin or not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(admin.id, admin.email)
    return AdminLoginResponse(access_token=token)


@router.get("/profiles", response_model=list[FinotProfileOut])
def list_profiles(
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """List all FINoT profiles."""
    return db.query(FinotProfile).order_by(FinotProfile.name).all()


@router.post("/profiles", response_model=FinotProfileOut)
def create_profile(
    payload: FinotProfileCreate,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Create a new FINoT profile."""
    if db.query(FinotProfile).filter(FinotProfile.name == payload.name).first():
        raise HTTPException(status_code=409, detail=f"Profile '{payload.name}' already exists")

    profile = FinotProfile(
        name=payload.name,
        finot_url=payload.finot_url,
        finot_username=payload.finot_username,
        finot_password_encrypted=encrypt_password(payload.finot_password),
        is_active=payload.is_active
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/profiles/{profile_id}", response_model=FinotProfileOut)
def get_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Get a single FINoT profile."""
    profile = db.query(FinotProfile).filter(FinotProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


@router.patch("/profiles/{profile_id}", response_model=FinotProfileOut)
def update_profile(
    profile_id: int,
    payload: FinotProfileUpdate,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Update a FINoT profile. Only provided fields are changed."""
    profile = db.query(FinotProfile).filter(FinotProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    updates = payload.model_dump(exclude_unset=True)
    if "finot_password" in updates:
        password = updates.pop("finot_password")
        profile.finot_password_encrypted = encrypt_password(password)
    for field, value in updates.items():
        setattr(profile, field, value)

    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/profiles/{profile_id}")
def delete_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _admin: AdminUser = Depends(get_current_admin)
):
    """Delete a FINoT profile. Its stations are kept, with profile_id set to NULL."""
    profile = db.query(FinotProfile).filter(FinotProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db.delete(profile)
    db.commit()
    return {"deleted": profile_id}
