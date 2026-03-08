"""
SmartLocker Cloud - FastAPI Application Entry Point

Starts the web server with:
- REST API endpoints (/api/...)
- Admin portal (/admin/...)
- Ship Owner dashboard (/dashboard/...)
- Auto-generated API docs (/docs)
"""

import os
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse

from app.config import settings
from app.database import init_db, async_session
from app.api.auth import hash_password

# Import all models so SQLAlchemy knows about them
from app.models import *  # noqa


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Create tables automatically on startup
    # (In production, switch to Alembic migrations once schema is stable)
    await init_db()
    await _seed_admin_user()

    # Create upload directory
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    yield  # App runs here

    # Shutdown (nothing to clean up for now)


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

# Static files (CSS, images)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/web/templates")


# ---- Include API Routers ----

from app.api.auth import router as auth_router
from app.api.products import router as products_router, recipe_router
from app.api.events import router as events_router
from app.api.pairing import router as pairing_router

app.include_router(auth_router)
app.include_router(products_router)
app.include_router(recipe_router)
app.include_router(events_router)
app.include_router(pairing_router)

# Include web routers
from app.web.admin import router as admin_router
from app.web.dashboard import router as dashboard_router

app.include_router(admin_router)
app.include_router(dashboard_router)


# ---- Error Handler (shows traceback in browser for debugging) ----

@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception):
    """Show detailed error info instead of generic 500."""
    tb = traceback.format_exc()
    return HTMLResponse(
        content=f"<html><body style='background:#0d1b2a;color:#e8ecf1;font-family:monospace;padding:20px;'>"
                f"<h2 style='color:#e63946;'>Error: {type(exc).__name__}</h2>"
                f"<p style='color:#f4a261;'>{exc}</p>"
                f"<pre style='background:#1b2838;padding:15px;border-radius:8px;overflow-x:auto;'>{tb}</pre>"
                f"</body></html>",
        status_code=500,
    )


# ---- Root Routes ----

@app.get("/")
async def root():
    """Redirect to admin portal."""
    return RedirectResponse(url="/admin/")


@app.get("/health")
async def health():
    """Health check for Railway."""
    return {"status": "ok", "app": settings.APP_NAME}


# ---- Seed Data ----

async def _seed_admin_user():
    """Create default admin user if none exists."""
    from app.models.user import User
    from sqlalchemy import select

    try:
        async with async_session() as session:
            result = await session.execute(
                select(User).where(User.email == settings.ADMIN_EMAIL)
            )
            if not result.scalar_one_or_none():
                admin = User(
                    email=settings.ADMIN_EMAIL,
                    password_hash=hash_password(settings.ADMIN_PASSWORD),
                    name="PPG Admin",
                    role="ppg_admin",
                )
                session.add(admin)
                await session.commit()
                print(f"  Created admin user: {settings.ADMIN_EMAIL}")
            else:
                print(f"  Admin user already exists: {settings.ADMIN_EMAIL}")
    except Exception as e:
        print(f"  Warning: Could not seed admin user: {e}")
