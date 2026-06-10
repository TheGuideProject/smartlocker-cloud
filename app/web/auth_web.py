"""
Web Authentication — Session-based login/logout for the admin portal.

Uses Starlette's SessionMiddleware (cookie-based sessions).
All /admin/* routes must use Depends(require_admin_session).
"""

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.database import get_db
from app.models.user import User
from app.api.auth import verify_password

router = APIRouter(tags=["auth-web"])
templates = Jinja2Templates(directory="app/web/templates")

PPG_WEB_ROLES = {"ppg_admin", "ppg_support"}
CLIENT_WEB_ROLES = {"ship_owner", "crew"}


def _can_manage_users(role: str | None) -> bool:
    """Return whether a web role may create or edit user accounts."""
    return role == "ppg_admin"


def _portal_home_for_role(role: str | None) -> str:
    """Return the correct web portal landing page for a user role."""
    if role in PPG_WEB_ROLES:
        return "/admin/"
    if role in CLIENT_WEB_ROLES:
        return "/client/"
    return "/admin/login"


def _login_path_for_request_path(path: str | None) -> str:
    """Return the correct login page for a protected web path."""
    if (path or "").startswith("/client"):
        return "/client/login"
    return "/admin/login"


def _login_context_for_path(path: str | None) -> dict:
    """Return copy and form targets for the portal-specific login page."""
    is_client = (path or "").startswith("/client")
    if is_client:
        return {
            "badge": "Client",
            "subtitle": "Fleet inventory and service overview",
            "form_action": "/client/login",
            "email_placeholder": "user@client.com",
            "footer": "SmartLocker Client Portal",
            "switch_href": "/admin/login",
            "switch_label": "PPG staff login",
        }
    return {
        "badge": "PPG",
        "subtitle": "PPG operations, devices, catalog and support",
        "form_action": "/admin/login",
        "email_placeholder": "admin@ppg.com",
        "footer": "PPG SmartLocker Platform",
        "switch_href": "/client/login",
        "switch_label": "Client portal login",
    }


async def _load_active_session_user(
    request: Request,
    db: AsyncSession,
) -> User:
    """Load the active web session user or redirect to login."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=303,
            detail="Not authenticated",
            headers={"Location": _login_path_for_request_path(request.url.path)},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(
            status_code=303,
            detail="Session expired",
            headers={"Location": _login_path_for_request_path(request.url.path)},
        )

    return user


# ============================================================
# SESSION AUTH DEPENDENCY
# ============================================================

async def require_admin_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Require an authenticated admin/support user via session cookie.
    Redirects to /admin/login if not authenticated.
    """
    user = await _load_active_session_user(request, db)

    if user.role not in PPG_WEB_ROLES:
        raise HTTPException(
            status_code=303,
            detail="Admin access required",
            headers={"Location": _portal_home_for_role(user.role)},
        )

    return user


async def require_ppg_admin_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Require a PPG Admin session for platform administration actions."""
    user = await require_admin_session(request, db)

    if not _can_manage_users(user.role):
        raise HTTPException(
            status_code=303,
            detail="PPG Admin access required",
            headers={"Location": "/admin/?error=PPG+Admin+access+required"},
        )

    return user


async def require_client_session(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Require an authenticated user allowed to view the client portal.
    PPG staff may enter for support/preview, while ship_owner and crew see
    only their assigned company data.
    """
    user = await _load_active_session_user(request, db)

    if user.role not in PPG_WEB_ROLES | CLIENT_WEB_ROLES:
        raise HTTPException(
            status_code=303,
            detail="Client portal access required",
            headers={"Location": _portal_home_for_role(user.role)},
        )

    return user


# ============================================================
# LOGIN / LOGOUT ROUTES
# ============================================================

@router.get("/login", response_class=HTMLResponse)
@router.get("/client/login", response_class=HTMLResponse)
@router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show login form. Redirect to the correct portal if already logged in."""
    if request.session.get("user_id"):
        return RedirectResponse(
            _portal_home_for_role(request.session.get("user_role")),
            status_code=303,
        )
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": None,
        **_login_context_for_path(request.url.path),
    })


@router.post("/login")
@router.post("/client/login")
@router.post("/admin/login")
async def login_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    """Validate credentials and create session."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Invalid email or password",
            **_login_context_for_path(request.url.path),
        }, status_code=401)

    if not user.is_active:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Account is disabled",
            **_login_context_for_path(request.url.path),
        }, status_code=403)

    if user.role not in PPG_WEB_ROLES | CLIENT_WEB_ROLES:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Web portal access required",
            **_login_context_for_path(request.url.path),
        }, status_code=403)

    # Create session
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_name"] = user.name
    request.session["user_role"] = user.role

    # Update last login
    user.last_login = datetime.utcnow()
    await db.commit()

    return RedirectResponse(_portal_home_for_role(user.role), status_code=303)


@router.get("/logout")
@router.get("/client/logout")
@router.get("/admin/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    login_path = _login_path_for_request_path(request.url.path)
    request.session.clear()
    return RedirectResponse(login_path, status_code=303)
