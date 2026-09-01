"""Authentication endpoints.

  POST /api/auth/login     -> username+password (form) => JWT token + role
  GET  /api/auth/me        -> current logged-in user's username + role
  POST /api/auth/register  -> create a new user (ADMIN-ONLY)

To create users from Swagger: log in as an admin, click "Authorize", then call /register.
The very first admin is created by the one-time create_admin.py script.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.database import get_db
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    require_admin,
)
from app.models.database import User

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


class UserInfo(BaseModel):
    username: str
    role: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "user"  # "user" or "admin"


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Log in with username + password (form fields). Returns a signed JWT."""
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalars().first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token = create_access_token(username=user.username, role=user.role)
    return TokenResponse(access_token=token, role=user.role, username=user.username)


@router.get("/me", response_model=UserInfo)
async def me(current_user: User = Depends(get_current_user)):
    """Return the current logged-in user's username + role (frontend uses this for menus)."""
    return UserInfo(username=current_user.username, role=current_user.role)


@router.post("/register", response_model=UserInfo, status_code=status.HTTP_201_CREATED)
async def register(
    req: RegisterRequest,
    _admin: User = Depends(require_admin),   # ADMIN-ONLY guard
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account. ADMIN-ONLY — call from Swagger after authorizing as an admin."""
    if req.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="role must be 'user' or 'admin'")

    existing = (await db.execute(select(User).where(User.username == req.username))).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(
        username=req.username,
        hashed_password=hash_password(req.password),
        role=req.role,
    )
    db.add(user)
    await db.commit()
    return UserInfo(username=user.username, role=user.role)
