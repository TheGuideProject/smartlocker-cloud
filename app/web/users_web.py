"""User Management - Web routes for the admin portal."""

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database import get_db
from app.models.user import User, UserRole
from app.models.company import Company
from app.api.auth import hash_password
from app.web.auth_web import require_ppg_admin_session

router = APIRouter(prefix="/admin", tags=["users-web"])
templates = Jinja2Templates(directory="app/web/templates")

PPG_USER_ROLES = {UserRole.PPG_ADMIN.value, UserRole.PPG_SUPPORT.value}
CLIENT_USER_ROLES = {UserRole.SHIP_OWNER.value, UserRole.CREW.value}
USER_ROLE_LABELS = {
    UserRole.PPG_ADMIN.value: "PPG Admin",
    UserRole.PPG_SUPPORT.value: "PPG Support",
    UserRole.SHIP_OWNER.value: "Client Admin",
    UserRole.CREW.value: "Crew",
}


def _company_assignment_for_role(role: str, company_id: Optional[str]) -> tuple[bool, Optional[str], Optional[str]]:
    """Validate and normalize company assignment for a web user role."""
    clean_role = (role or "").strip()
    clean_company_id = (company_id or "").strip() or None

    if clean_role not in PPG_USER_ROLES | CLIENT_USER_ROLES:
        return False, None, "Invalid user role"

    if clean_role in PPG_USER_ROLES:
        return True, None, None

    if not clean_company_id:
        return False, None, "Client users must be assigned to a company"

    return True, clean_company_id, None


def _user_portal_context(role: str) -> dict:
    """Return the web portal context for a user role."""
    clean_role = (role or "").strip()
    if clean_role in PPG_USER_ROLES:
        return {
            "label": "PPG Portal",
            "login_href": "/admin/login",
            "detail": "PPG operations workspace",
        }
    if clean_role in CLIENT_USER_ROLES:
        return {
            "label": "Client Portal",
            "login_href": "/client/login",
            "detail": "Customer vessel workspace",
        }
    return {
        "label": "No web portal",
        "login_href": "",
        "detail": "Role is not enabled for web access",
    }


def _user_role_options() -> list[dict]:
    """Return user roles with labels that match the PPG/client portal split."""
    return [
        {"value": role.value, "label": USER_ROLE_LABELS.get(role.value, role.value.replace("_", " ").title())}
        for role in UserRole
    ]


def _users_error_redirect(message: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/admin/users?error={message.replace(' ', '+')}",
        status_code=303,
    )


# ---- GET /users - List all users ----

@router.get("/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_ppg_admin_session),
):
    """List all users with their companies."""
    # Fetch users with company relationship
    result = await db.execute(
        select(User).options(selectinload(User.company)).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    for user in users:
        user.portal_context = _user_portal_context(user.role)

    # Fetch companies for the dropdown
    result = await db.execute(select(Company).order_by(Company.name))
    companies = result.scalars().all()

    # Available roles
    roles = _user_role_options()

    return templates.TemplateResponse("admin/users.html", {
        "request": request,
        "users": users,
        "companies": companies,
        "roles": roles,
        "current_user": current_user,
        "active": "users",
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


# ---- POST /users/add - Create new user ----

@router.post("/users/add")
async def add_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_ppg_admin_session),
    email: str = Form(...),
    password: str = Form(...),
    name: str = Form(...),
    role: str = Form(...),
    company_id: Optional[str] = Form(None),
):
    """Create a new user."""
    # Check for duplicate email
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        return RedirectResponse(
            url="/admin/users?error=Email+already+exists", status_code=303
        )

    valid_assignment, normalized_company_id, assignment_error = _company_assignment_for_role(role, company_id)
    if not valid_assignment:
        return _users_error_redirect(assignment_error or "Invalid user setup")

    user = User(
        email=email.strip(),
        password_hash=hash_password(password),
        name=name.strip(),
        role=role,
        company_id=normalized_company_id,
    )
    db.add(user)
    await db.flush()

    return RedirectResponse(
        url="/admin/users?success=User+created+successfully", status_code=303
    )


# ---- POST /users/{user_id}/edit - Edit user ----

@router.post("/users/{user_id}/edit")
async def edit_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_ppg_admin_session),
    name: str = Form(...),
    role: str = Form(...),
    company_id: Optional[str] = Form(None),
):
    """Edit user name, role, and company."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )

    valid_assignment, normalized_company_id, assignment_error = _company_assignment_for_role(role, company_id)
    if not valid_assignment:
        return _users_error_redirect(assignment_error or "Invalid user setup")

    user.name = name.strip()
    user.role = role
    user.company_id = normalized_company_id
    await db.flush()

    return RedirectResponse(
        url="/admin/users?success=User+updated+successfully", status_code=303
    )


# ---- POST /users/{user_id}/reset-password - Reset password ----

@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_ppg_admin_session),
    new_password: str = Form(...),
):
    """Reset a user's password."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )

    user.password_hash = hash_password(new_password)
    await db.flush()

    return RedirectResponse(
        url="/admin/users?success=Password+reset+successfully", status_code=303
    )


# ---- POST /users/{user_id}/toggle-active - Toggle is_active ----

@router.post("/users/{user_id}/toggle-active")
async def toggle_active(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_ppg_admin_session),
):
    """Toggle user active status. Prevents deactivating the last admin."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        return RedirectResponse(
            url="/admin/users?error=User+not+found", status_code=303
        )

    # Prevent deactivating the last active admin
    if user.is_active and user.role == UserRole.PPG_ADMIN.value:
        admin_count = await db.execute(
            select(func.count()).select_from(User).where(
                User.role == UserRole.PPG_ADMIN.value,
                User.is_active == True,
            )
        )
        count = admin_count.scalar()
        if count <= 1:
            return RedirectResponse(
                url="/admin/users?error=Cannot+deactivate+the+last+admin+user",
                status_code=303,
            )

    user.is_active = not user.is_active
    await db.flush()

    status_text = "activated" if user.is_active else "deactivated"
    return RedirectResponse(
        url=f"/admin/users?success=User+{status_text}+successfully",
        status_code=303,
    )
