"""Mixing Sessions web routes - View mixing session data from edge devices.

Provides read-only views of mixing sessions aggregated from edge device events.
"""

import logging

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.mixing import MixingSessionCloud
from app.models.device import LockerDevice
from app.models.product import MixingRecipe

logger = logging.getLogger("smartlocker.mixing_web")

router = APIRouter(prefix="/admin", tags=["mixing-web"])
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/mixing-sessions", response_class=HTMLResponse)
async def mixing_sessions_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all mixing sessions with device and recipe info."""
    result = await db.execute(
        select(MixingSessionCloud)
        .options(
            selectinload(MixingSessionCloud.device),
            selectinload(MixingSessionCloud.recipe),
        )
        .order_by(desc(MixingSessionCloud.started_at))
    )
    sessions = result.scalars().all()

    return templates.TemplateResponse(
        "admin/mixing_sessions.html",
        {
            "request": request,
            "sessions": sessions,
        },
    )
