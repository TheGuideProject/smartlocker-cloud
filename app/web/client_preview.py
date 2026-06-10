"""PPG Client Preview - Inspect client-portal data from inside the PPG portal.

PPG staff no longer browse /client/* directly: this admin-only page shows
what clients see, across all companies or scoped to one company via
/admin/client-preview?company_id=...
"""

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.company import Company
from app.models.fleet import Fleet, Vessel
from app.models.event import DeviceEvent
from app.models.support_request import SupportRequest
from app.web.auth_web import require_admin_session

logger = logging.getLogger("smartlocker.client_preview")

router = APIRouter(prefix="/admin/client-preview", tags=["admin-client-preview"])
templates = Jinja2Templates(directory="app/web/templates")


def _preview_company_selector_options(companies: list, scoped_company_id: str | None) -> list[dict]:
    """Build company selector options for the PPG client preview."""
    options = [{
        "id": "",
        "name": "All companies",
        "selected": not scoped_company_id,
    }]
    for company in companies:
        company_id = getattr(company, "id", "")
        options.append({
            "id": company_id,
            "name": getattr(company, "name", company_id),
            "selected": company_id == scoped_company_id,
        })
    return options


def _preview_scope_summary(scoped_company_id: str | None, selector_options: list[dict]) -> dict:
    """Describe the active preview data scope for the page header."""
    if not scoped_company_id:
        return {
            "title": "PPG preview",
            "detail": "Showing all client companies.",
            "badge": "preview",
        }

    company_name = next(
        (
            option["name"]
            for option in selector_options
            if option.get("id") == scoped_company_id
        ),
        scoped_company_id,
    )
    return {
        "title": "PPG preview",
        "detail": f"Showing {company_name} only.",
        "badge": "preview",
    }


def _preview_uses_global_support_scope(scoped_company_id: str | None, device_ids: list) -> bool:
    """Return True only for the unfiltered all-companies support preview."""
    return not scoped_company_id and not device_ids


def _preview_uses_global_scope(scoped_company_id: str | None) -> bool:
    """Return True only for the unfiltered all-companies preview."""
    return not scoped_company_id


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_client_preview(
    request: Request,
    company_id: str = Query(None),
    user = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """Preview the client-portal view of fleet, activity, and support data."""
    scoped_company_id = (company_id or "").strip() or None

    # ---- Vessels (all companies, or one company) ----
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

    all_devices = [device for vessel in vessels for device in vessel.devices]
    device_ids = [str(device.id) for device in all_devices]
    online_count = sum(1 for device in all_devices if device.is_online)

    # ---- Recent events (last 24h) ----
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    recent_events = []
    if device_ids or _preview_uses_global_scope(scoped_company_id):
        event_query = (
            select(DeviceEvent)
            .where(DeviceEvent.timestamp >= cutoff_24h)
            .order_by(desc(DeviceEvent.timestamp))
            .limit(20)
        )
        if device_ids:
            event_query = event_query.where(
                and_(DeviceEvent.device_id.in_(device_ids))
            )
        events_result = await db.execute(event_query)
        recent_events = events_result.scalars().all()

    # ---- Open support requests ----
    support_requests = []
    edge_device_ids = [device.device_id for device in all_devices]
    if edge_device_ids or _preview_uses_global_support_scope(scoped_company_id, edge_device_ids):
        support_query = select(SupportRequest).where(
            SupportRequest.status.in_(["open", "in_progress"])
        )
        if edge_device_ids:
            support_query = support_query.where(
                SupportRequest.device_id.in_(edge_device_ids)
            )
        support_result = await db.execute(
            support_query.order_by(desc(SupportRequest.created_at)).limit(20)
        )
        support_requests = support_result.scalars().all()

    companies_result = await db.execute(select(Company).order_by(Company.name))
    selector_options = _preview_company_selector_options(
        companies_result.scalars().all(),
        scoped_company_id,
    )

    return templates.TemplateResponse("admin/client_preview.html", {
        "request": request,
        "user": user,
        "active": "client_preview",
        "company_id": scoped_company_id,
        "company_selector_options": selector_options,
        "client_scope": _preview_scope_summary(scoped_company_id, selector_options),
        "vessels": vessels,
        "total_vessels": len(vessels),
        "total_devices": len(all_devices),
        "online_count": online_count,
        "offline_count": len(all_devices) - online_count,
        "recent_events": recent_events,
        "event_count_24h": len(recent_events),
        "support_requests": support_requests,
    })
