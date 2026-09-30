"""Authentication and identity endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import UserLogin, Token, UserOut
from app.auth import create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(login_req: UserLogin, db: Session = Depends(get_db)):
    """Logs in or auto-registers a demo student/teacher account."""
    user = db.query(User).filter(User.id == login_req.user_id).first()
    if not user:
        role = login_req.role or ("teacher" if "teacher" in login_req.user_id else "student")
        name = login_req.user_id.replace("_", " ").title()
        user = User(id=login_req.user_id, name=name, role=role)
        db.add(user)
        db.commit()
        db.refresh(user)

    access_token = create_access_token(
        data={"sub": user.id, "role": user.role, "name": user.name}
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserOut(
            id=user.id,
            name=user.name,
            role=user.role,
            created_at=user.created_at,
        ),
    )


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    """Returns the profile of the authenticated user."""
    return UserOut(
        id=current_user.id,
        name=current_user.name,
        role=current_user.role,
        created_at=current_user.created_at,
    )
