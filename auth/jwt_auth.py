"""JWT/OAuth2 authentication for the API.

Provides OAuth2 Password Flow with JWT tokens (free, open-source).
No external auth service needed — uses python-jose + passlib for hashing.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from config.settings import get_settings

logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme for swagger UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token")

router = APIRouter(prefix="/auth", tags=["Authentication"])

# User data store — in production, use a database
USERS_FILE = Path(__file__).parent.parent / ".users.json"


class User(BaseModel):
    """User in the system."""

    username: str
    email: str | None = None
    full_name: str | None = None
    disabled: bool = False
    org_id: str = "default"
    role: str = "ap_clerk"  # ap_clerk | ap_manager | ap_admin


class UserInDB(User):
    """User with hashed password."""

    hashed_password: str


class Token(BaseModel):
    """OAuth2 token response."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class TokenData(BaseModel):
    """Decoded JWT token data."""

    username: str | None = None
    scopes: list[str] = Field(default_factory=list)
    org_id: str = "default"


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: Plain text password

    Returns:
        Hashed password
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    Args:
        plain_password: Plain text password
        hashed_password: Hashed password from database

    Returns:
        True if password matches, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


def _load_users() -> dict[str, UserInDB]:
    """Load users from JSON file.

    Returns:
        Dict of username → UserInDB
    """
    if not USERS_FILE.exists():
        logger.warning(f"Users file not found at {USERS_FILE}; initialising with demo user")
        # Create demo user: admin / admin123
        demo_users = {
            "admin": {
                "username": "admin",
                "email": "admin@meridian-ap.local",
                "full_name": "Admin User",
                "disabled": False,
                "org_id": "default",
                "role": "ap_admin",
                "hashed_password": hash_password("admin123"),
            }
        }
        _save_users(demo_users)
        return {k: UserInDB(**v) for k, v in demo_users.items()}

    try:
        with open(USERS_FILE, "r") as f:
            data = json.load(f)
        return {k: UserInDB(**v) for k, v in data.items()}
    except Exception as e:
        logger.error(f"Failed to load users: {e}")
        return {}


def _save_users(users: dict) -> None:
    """Save users to JSON file.

    Args:
        users: Dict of username → user data
    """
    try:
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save users: {e}")


def authenticate_user(username: str, password: str) -> UserInDB | None:
    """Authenticate a user by username and password.

    Args:
        username: Username
        password: Plain text password

    Returns:
        UserInDB if authentication succeeds, None otherwise
    """
    users = _load_users()
    user = users.get(username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token.

    Args:
        data: Token claims
        expires_delta: Expiry timedelta (default: from config)

    Returns:
        Encoded JWT token
    """
    settings = get_settings()
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_access_token_expire_minutes
        )

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token.

    Args:
        data: Token claims

    Returns:
        Encoded JWT token
    """
    settings = get_settings()
    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    to_encode.update({"exp": expire, "type": "refresh"})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Dependency: extract and validate JWT token, return current user.

    Args:
        token: JWT token from Authorization header

    Returns:
        User if token is valid

    Raises:
        HTTPException: if token is invalid or user not found
    """
    settings = get_settings()

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    users = _load_users()
    user = users.get(username)
    if user is None:
        raise credentials_exception

    return User(**user.model_dump(exclude={"hashed_password"}))


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Dependency: require admin role.

    Args:
        current_user: Current authenticated user

    Returns:
        User if role is ap_admin

    Raises:
        HTTPException: if user is not admin
    """
    if current_user.role != "ap_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )
    return current_user


@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Token:
    """OAuth2 login endpoint.

    Issues access_token (short-lived, 30min) and refresh_token (long-lived, 7 days).

    Args:
        form_data: OAuth2 password flow request (username + password)

    Returns:
        Token with access and refresh tokens

    Raises:
        HTTPException: if credentials are invalid
    """
    user = authenticate_user(form_data.username, form_data.password)
    if not user or user.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={
            "sub": user.username,
            "org_id": user.org_id,
        }
    )
    refresh_token = create_refresh_token(
        data={
            "sub": user.username,
            "org_id": user.org_id,
        }
    )

    logger.info(f"User {user.username} logged in")

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    refresh_token: str,
) -> Token:
    """Refresh an access token using a refresh token.

    Args:
        refresh_token: Valid refresh token

    Returns:
        New access and refresh tokens

    Raises:
        HTTPException: if refresh token is invalid
    """
    settings = get_settings()

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
    )

    try:
        payload = jwt.decode(
            refresh_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != "refresh":
            raise credentials_exception

        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    users = _load_users()
    user = users.get(username)
    if user is None:
        raise credentials_exception

    access_token = create_access_token(
        data={
            "sub": user.username,
            "org_id": user.org_id,
        }
    )
    new_refresh_token = create_refresh_token(
        data={
            "sub": user.username,
            "org_id": user.org_id,
        }
    )

    return Token(
        access_token=access_token,
        refresh_token=new_refresh_token,
    )


@router.get("/me", response_model=User)
async def read_users_me(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get the current authenticated user.

    Args:
        current_user: Current user from JWT token

    Returns:
        User information
    """
    return current_user
