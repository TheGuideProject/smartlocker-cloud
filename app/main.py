"""
SmartLocker Cloud - FastAPI Application Entry Point

Starts the web server with:
- REST API endpoints (/api/...)
- Admin portal (/admin/...)
- Client portal (/client/... plus legacy /dashboard redirect)
- Auto-generated API docs (/docs)
"""

import os
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

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

    # Migration: add slot_count column if missing
    try:
        from app.database import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE locker_devices ADD COLUMN slot_count INTEGER DEFAULT 4"))
    except Exception:
        pass  # Column already exists

    # Migration: add colors_json column to products if missing
    try:
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE products ADD COLUMN colors_json JSON"))
    except Exception:
        pass  # Column already exists

    # Create upload directory
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)

    yield  # App runs here

    # Shutdown (nothing to clean up for now)


app = FastAPI(
    title=settings.APP_NAME,
    version="2.3.3",
    lifespan=lifespan,
)

# Session middleware (cookie-based auth for web admin)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# Static files (CSS, images)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/web/templates")


# ---- Include API Routers ----

from app.api.auth import router as auth_router
from app.api.products import router as products_router, recipe_router, barcode_router
from app.api.events import router as events_router
from app.api.pairing import router as pairing_router
from app.api.websocket import router as ws_router

app.include_router(auth_router)
app.include_router(products_router)
app.include_router(recipe_router)
app.include_router(barcode_router)
app.include_router(events_router)
app.include_router(pairing_router)
app.include_router(ws_router)

# Include web routers
from app.web.auth_web import router as auth_web_router
from app.web.admin import router as admin_router
from app.web.client_preview import router as client_preview_router
from app.web.dashboard import router as dashboard_router, legacy_router as dashboard_legacy_router
from app.web.users_web import router as users_web_router
from app.web.mixing_web import router as mixing_web_router
from app.web.crud_web import router as crud_web_router

app.include_router(auth_web_router)   # Login/logout (must be before admin)
app.include_router(admin_router)
app.include_router(client_preview_router)
app.include_router(dashboard_router)
app.include_router(dashboard_legacy_router)
app.include_router(users_web_router)
app.include_router(mixing_web_router)
app.include_router(crud_web_router)


# ---- Standalone Client Portal Redirect ----

def _external_client_portal_redirect(path: str, query: str, portal_url: str) -> str | None:
    """Return the standalone client-portal URL for a /client/* request.

    Returns None when no standalone portal is configured or the path does
    not belong to the client portal. Used to hop over to the separate
    client-portal deployment once CLIENT_PORTAL_URL is set.
    """
    clean_portal_url = (portal_url or "").strip().rstrip("/")
    if not clean_portal_url:
        return None
    if not (path.startswith("/client") or path.startswith("/dashboard")):
        return None

    target = f"{clean_portal_url}{path}"
    if query:
        target = f"{target}?{query}"
    return target


@app.middleware("http")
async def client_portal_redirect_middleware(request: Request, call_next):
    """Send /client/* traffic to the standalone client portal when configured."""
    target = _external_client_portal_redirect(
        request.url.path,
        request.url.query,
        settings.CLIENT_PORTAL_URL,
    )
    if target:
        return RedirectResponse(target, status_code=307)
    return await call_next(request)


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

def _root_portal_destination(role: str | None) -> str | None:
    """Return the portal destination for an authenticated root request."""
    if not role:
        return None

    from app.web.auth_web import _portal_home_for_role

    return _portal_home_for_role(role)


def _portal_entry_options() -> list[dict]:
    """Return the two top-level platform entry points."""
    return [
        {
            "label": "PPG Portal",
            "href": "/admin/login",
            "badge": "PPG staff",
            "detail": "Manage companies, vessels, devices, catalog, barcodes, inventory, and support.",
        },
        {
            "label": "Client Portal",
            "href": settings.CLIENT_PORTAL_URL.strip().rstrip("/") + "/client/login" if settings.CLIENT_PORTAL_URL.strip() else "/client/login",
            "badge": "Client access",
            "detail": "Review vessel stock, SmartLocker status, activity, and support requests.",
        },
    ]


@app.get("/")
async def root(request: Request):
    """Show a portal selector for guests, or redirect active sessions."""
    destination = _root_portal_destination(request.session.get("user_role"))
    if destination:
        return RedirectResponse(url=destination)

    return templates.TemplateResponse("portal_select.html", {
        "request": request,
        "portal_options": _portal_entry_options(),
    })


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
