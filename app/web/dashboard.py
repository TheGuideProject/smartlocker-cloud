"""Ship Owner Dashboard - Read-only view of their fleet data."""

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard-web"])
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/", response_class=HTMLResponse)
async def owner_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Ship owner fleet overview."""
    return templates.TemplateResponse("owner/dashboard.html", {
        "request": request,
    })
