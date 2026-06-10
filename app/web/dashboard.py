"""Ship Owner Dashboard - Read-only view of their fleet data."""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.company import Company
from app.models.fleet import Fleet, Vessel
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.support_request import SupportRequest
from app.web.auth_web import PPG_WEB_ROLES, require_client_session

logger = logging.getLogger("smartlocker.dashboard")

router = APIRouter(prefix="/client", tags=["client-web"])
legacy_router = APIRouter(prefix="/dashboard", tags=["dashboard-web"])
templates = Jinja2Templates(directory="app/web/templates")


def _client_dashboard_company_scope(user, requested_company_id: str | None) -> str | None:
    """Return the company scope for the client portal dashboard."""
    if getattr(user, "role", None) in PPG_WEB_ROLES:
        return requested_company_id
    return getattr(user, "company_id", None)


def _client_dashboard_uses_global_support_scope(
    is_ppg_staff: bool,
    scoped_company_id: str | None,
    device_ids: list[str],
) -> bool:
    """Return True only for PPG's unfiltered global client-portal preview."""
    return is_ppg_staff and not scoped_company_id and not device_ids


@legacy_router.get("/", response_class=HTMLResponse)
async def legacy_dashboard_redirect():
    """Keep old dashboard links working while the client portal moves to /client."""
    return RedirectResponse("/client/", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def owner_dashboard(
    request: Request,
    company_id: str = Query(None),
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Ship owner fleet overview with real data."""
    scoped_company_id = _client_dashboard_company_scope(current_user, company_id)
    is_ppg_staff = current_user.role in PPG_WEB_ROLES

    # ---- Query vessels (optionally filtered by company_id) ----
    vessels = []
    if is_ppg_staff or scoped_company_id:
        vessel_query = (
            select(Vessel)
            .options(
                selectinload(Vessel.fleet).selectinload(Fleet.company),
                selectinload(Vessel.devices),
            )
        )
        if scoped_company_id:
            vessel_query = vessel_query.join(Fleet).where(Fleet.company_id == scoped_company_id)

        vessel_result = await db.execute(vessel_query.order_by(Vessel.name))
        vessels = vessel_result.scalars().unique().all()

    # ---- Collect all devices from those vessels ----
    all_devices = []
    for v in vessels:
        for d in v.devices:
            all_devices.append(d)

    total_vessels = len(vessels)
    total_devices = len(all_devices)
    online_count = sum(1 for d in all_devices if d.is_online)
    offline_count = total_devices - online_count

    # ---- Recent events (last 24h) ----
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    device_ids = [str(d.id) for d in all_devices]

    recent_events = []
    if device_ids:
        events_result = await db.execute(
            select(DeviceEvent)
            .where(
                and_(
                    DeviceEvent.device_id.in_(device_ids),
                    DeviceEvent.timestamp >= cutoff_24h,
                )
            )
            .order_by(desc(DeviceEvent.timestamp))
            .limit(20)
        )
        recent_events = events_result.scalars().all()

    # ---- Open support requests ----
    support_requests = []
    show_global_support = _client_dashboard_uses_global_support_scope(
        is_ppg_staff,
        scoped_company_id,
        device_ids,
    )
    if show_global_support or device_ids:
        support_query = select(SupportRequest).where(
            SupportRequest.status.in_(["open", "in_progress"])
        )
        if device_ids:
            # Filter to devices belonging to these vessels
            edge_device_ids = [d.device_id for d in all_devices]
            support_query = support_query.where(
                SupportRequest.device_id.in_(edge_device_ids)
            )
        support_result = await db.execute(
            support_query.order_by(desc(SupportRequest.created_at)).limit(20)
        )
        support_requests = support_result.scalars().all()

    # ---- Event count for summary ----
    event_count_24h = len(recent_events)

    # ---- Build device lookup by vessel id for template ----
    # Already loaded via selectinload on vessels

    return templates.TemplateResponse("owner/dashboard.html", {
        "request": request,
        "vessels": vessels,
        "total_vessels": total_vessels,
        "total_devices": total_devices,
        "online_count": online_count,
        "offline_count": offline_count,
        "recent_events": recent_events,
        "support_requests": support_requests,
        "event_count_24h": event_count_24h,
        "company_id": scoped_company_id,
        "current_user": current_user,
        "is_ppg_staff": is_ppg_staff,
        "active": "client_dashboard",
    })
