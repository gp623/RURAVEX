"""MasteryFlow FastAPI application entrypoint.
Configures CORS, rate limiting, and registers all domain routers.
"""

import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Dict, List
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import Base, engine, SessionLocal
from app.models import ConceptRecord
from app.seed import seed_database

# Routers
from app.routers.auth import router as auth_router
from app.routers.concepts import router as concepts_router
from app.routers.students import router as students_router
from app.routers.diagnostic import router as diagnostic_router
from app.routers.teacher import router as teacher_router
from app.routers.simulation import router as simulation_router
from app.routers.ai import router as ai_router

# In-memory sliding window rate limiter
# client_ip -> list of timestamps
_RATE_LIMIT_STORE: Dict[str, List[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 60.0   # seconds
RATE_LIMIT_MAX_REQUESTS = 120  # requests per minute per IP


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Checks database existence and runs auto-seed on clean start."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        count = db.query(ConceptRecord).count()
        if count < 10:
            print("Curriculum not fully seeded. Running seed script...")
            seed_database()
    finally:
        db.close()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Explainable Adaptive Learning & Teacher Intervention Engine",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS restricted to configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limiting_middleware(request: Request, call_next):
    """Enforces rate limiting per IP address to protect calculation endpoints."""
    # Exclude static/health docs
    if request.url.path in ["/docs", "/openapi.json", "/health", "/"]:
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = _RATE_LIMIT_STORE[client_ip]

    # Purge timestamps outside sliding window
    _RATE_LIMIT_STORE[client_ip] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

    if len(_RATE_LIMIT_STORE[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many requests. Rate limit exceeded (120 req/min)."},
        )

    _RATE_LIMIT_STORE[client_ip].append(now)
    response = await call_next(request)
    return response


# Register Routers
app.include_router(auth_router, prefix="/api")
app.include_router(concepts_router, prefix="/api")
app.include_router(students_router, prefix="/api")
app.include_router(diagnostic_router, prefix="/api")
app.include_router(teacher_router, prefix="/api")
app.include_router(simulation_router, prefix="/api")
app.include_router(ai_router, prefix="/api")


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "MasteryFlow Engine API", "version": "1.0.0"}


@app.get("/")
def root():
    return {
        "app": "MasteryFlow",
        "description": "Explainable Adaptive Learning and Intervention Engine",
        "docs": "/docs",
        "health": "/health",
    }
