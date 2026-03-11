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
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=303,
            detail="Not authenticated",
            headers={"Location": "/admin/login"},
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(
            status_code=303,
            detail="Session expired",
            headers={"Location": "/admin/login"},
        )

    if user.role not in ("ppg_admin", "ppg_support"):
        raise HTTPException(
            status_code=303,
            detail="Admin access required",
            headers={"Location": "/admin/login"},
        )

    return user


# ============================================================
# LOGIN / LOGOUT ROUTES
# ============================================================

@router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show login form. Redirect to /admin/ if already logged in."""
    if request.session.get("user_id"):
        return RedirectResponse("/admin/", status_code=303)
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": None,
    })


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
        }, status_code=401)

    if not user.is_active:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Account is disabled",
        }, status_code=403)

    if user.role not in ("ppg_admin", "ppg_support"):
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "Admin access required",
        }, status_code=403)

    # Create session
    request.session["user_id"] = user.id
    request.session["user_email"] = user.email
    request.session["user_name"] = user.name
    request.session["user_role"] = user.role

    # Update last login
    user.last_login = datetime.utcnow()
    await db.commit()

    return RedirectResponse("/admin/", status_code=303)


@router.get("/admin/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)
