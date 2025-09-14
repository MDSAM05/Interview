import os
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

import asyncpg
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse
from jose import JWTError, jwt
from passlib.context import CryptContext

# ──────────────────────────────────────────────
# Configuration and Initialization
# ──────────────────────────────────────────────

load_dotenv()

# Database URL configuration
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    POSTGRES_USER = os.getenv("POSTGRES_USER", "interview_user")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "interview_password")
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_DB = os.getenv("POSTGRES_DB", "interview_db")
    DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

if DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

# JWT configuration
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM")
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "30"))

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme for authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# FastAPI app initialization
app = FastAPI()

# ──────────────────────────────────────────────
# Exception Handlers
# ──────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"type": "validation_error", "detail": exc.errors()})

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"type": "http_error", "detail": exc.detail})

# Optional observability initialization
try:
    from common.observability import init_observability
    init_observability(app, service_name="user-service")
except Exception:
    pass

# ──────────────────────────────────────────────
# Database Setup
# ──────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    """Initialize database connection pool."""
    app.state.db = await asyncpg.create_pool(DATABASE_URL)

@app.on_event("shutdown")
async def shutdown() -> None:
    """Close database connection pool."""
    await app.state.db.close()

# ──────────────────────────────────────────────
# Utility Functions
# ──────────────────────────────────────────────

def create_access_token(data: Dict[str, Any]) -> str:
    """Create a JWT token with expiry."""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

async def get_user(username: str) -> Any:
    """Fetch a user by username."""
    async with app.state.db.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM user_service.users WHERE username=$1", username)

async def authenticate_user(username: str, password: str) -> Any:
    """Verify user credentials."""
    user = await get_user(username)
    if not user or not pwd_context.verify(password, user["password"]):
        return False
    return user

async def get_current_user(token: str = Depends(oauth2_scheme)) -> Any:
    """Validate JWT and retrieve current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not JWT_SECRET or not JWT_ALGORITHM:
        raise HTTPException(status_code=500, detail="JWT configuration missing")

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await get_user(username)
    if not user:
        raise credentials_exception
    return user

# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.get("/")
def root() -> Dict[str, str]:
    """Health check endpoint."""
    return {"message": "User Service is running"}

@app.get("/debug/token")
async def debug_token(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """Debug endpoint for JWT token validation."""
    return {
        "token_received": bool(token),
        "token_length": len(token) if token else 0,
        "jwt_secret_set": bool(JWT_SECRET),
        "jwt_algorithm_set": bool(JWT_ALGORITHM)
    }

@app.post("/register")
async def register(form: OAuth2PasswordRequestForm = Depends()) -> Dict[str, str]:
    """Register a new user."""
    async with app.state.db.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM user_service.users WHERE username=$1", form.username)
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")
        hashed_password = pwd_context.hash(form.password)
        await conn.execute(
            "INSERT INTO user_service.users (username, password) VALUES ($1, $2)",
            form.username, hashed_password
        )
    return {"msg": "User registered"}

@app.post("/token")
async def login(form: OAuth2PasswordRequestForm = Depends()) -> Dict[str, str]:
    """Login and generate JWT token."""
    user = await authenticate_user(form.username, form.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/profile")
async def profile(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """Get profile information for the authenticated user."""
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "created_at": current_user["created_at"].isoformat() if current_user["created_at"] else None
    }

@app.delete("/users/{username}")
async def delete_user(username: str, current_user: dict = Depends(get_current_user)):
    """Delete a user by username."""
    async with app.state.db.acquire() as conn:
        result = await conn.execute("DELETE FROM user_service.users WHERE username=$1", username)
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="User not found")
    return {"msg": f"User '{username}' deleted"}
