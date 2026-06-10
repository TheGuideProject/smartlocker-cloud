"""Ship Owner Dashboard - Read-only view of their fleet data."""

import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, Form, Query
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
from app.models.inventory import InventoryAdjustment
from app.models.product import Product
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


def _client_can_access_company(user, company_id: str | None) -> bool:
    """Return whether a user may view client data for a company."""
    if getattr(user, "role", None) in PPG_WEB_ROLES:
        return True
    return bool(company_id) and getattr(user, "company_id", None) == company_id


def _client_dashboard_uses_global_support_scope(
    is_ppg_staff: bool,
    scoped_company_id: str | None,
    device_ids: list[str],
) -> bool:
    """Return True only for PPG's unfiltered global client-portal preview."""
    return is_ppg_staff and not scoped_company_id and not device_ids


def _client_support_uses_global_scope(is_ppg_staff: bool, scoped_company_id: str | None) -> bool:
    """Return True only for PPG's unfiltered support preview."""
    return is_ppg_staff and not scoped_company_id


def _client_activity_uses_global_scope(is_ppg_staff: bool, scoped_company_id: str | None) -> bool:
    """Return True only for PPG's unfiltered activity preview."""
    return is_ppg_staff and not scoped_company_id


def _client_company_selector_options(companies: list, scoped_company_id: str | None) -> list[dict]:
    """Build company selector options for PPG client-portal previews."""
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


def _client_scope_summary(
    is_ppg_staff: bool,
    scoped_company_id: str | None,
    selector_options: list[dict],
) -> dict:
    """Describe the active client-portal data scope for the page header."""
    if not is_ppg_staff:
        return {
            "title": "Client view",
            "detail": "Showing only vessels linked to your company.",
            "badge": "client",
        }
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


def _support_request_stats(support_requests: list) -> dict:
    """Build compact support stats for the client portal."""
    open_count = sum(1 for request in support_requests if request.status in {"open", "in_progress"})
    return {
        "total": len(support_requests),
        "open": open_count,
        "resolved": len(support_requests) - open_count,
    }


def _client_support_request_error(
    device_id: str | None,
    error_title: str | None,
    allowed_device_ids: set[str],
) -> str | None:
    """Validate a client-created support request against accessible devices."""
    clean_device_id = (device_id or "").strip()
    clean_title = (error_title or "").strip()
    if not clean_device_id:
        return "Select a SmartLocker device"
    if clean_device_id not in allowed_device_ids:
        return "Device is not available for this client"
    if not clean_title:
        return "Describe the support request"
    return None


def _client_support_request_severity(severity: str | None) -> str:
    """Normalize client support severity into the supported model values."""
    clean_severity = (severity or "").strip().lower()
    if clean_severity in {"info", "warning", "critical"}:
        return clean_severity
    return "warning"


def _client_support_redirect(company_id: str | None, **params: str) -> str:
    """Build a client support redirect while preserving optional company scope."""
    query_params = {}
    if company_id:
        query_params["company_id"] = company_id
    query_params.update(params)
    if not query_params:
        return "/client/support"
    return f"/client/support?{urlencode(query_params)}"


def _client_activity_event_stats(events: list) -> dict:
    """Build compact event stats for the client activity view."""
    device_ids = {event.device_id for event in events if getattr(event, "device_id", None)}
    event_types = {event.event_type for event in events if getattr(event, "event_type", None)}
    return {
        "total": len(events),
        "devices": len(device_ids),
        "types": len(event_types),
    }


def _inventory_delta_liters(adjustment_type: str, quantity_liters: float | None) -> float:
    liters = float(quantity_liters or 0.0)
    if adjustment_type in {"manual_add", "pdf_import"}:
        return liters
    if adjustment_type in {"manual_remove", "mixing_consumption", "auto_consumed"}:
        return -liters
    return 0.0


def _empty_inventory_row(product_id: str, product_name: str, product_type: str) -> dict:
    return {
        "product_id": product_id,
        "name": product_name,
        "product_type": product_type,
        "product_type_label": product_type.replace("_", " ").title(),
        "liters": 0.0,
        "low_stock": False,
    }


async def _client_vessel_inventory_context(db: AsyncSession, vessel: Vessel) -> dict:
    """Build read-only vessel inventory for the client portal."""
    device_ids = [device.id for device in vessel.devices]
    product_summary: dict[str, dict] = {}

    for device in vessel.devices:
        system_info = device.system_info or {}
        vessel_stock = system_info.get("vessel_stock")
        if not isinstance(vessel_stock, list):
            continue
        for item in vessel_stock:
            product_id = item.get("product_id") or ""
            product_name = item.get("product_name") or "Unknown Product"
            product_type = item.get("product_type") or "base_paint"
            if not product_id:
                continue
            row = product_summary.setdefault(
                product_id,
                _empty_inventory_row(product_id, product_name, product_type),
            )
            row["liters"] += float(item.get("current_liters") or 0.0)

    if device_ids:
        products_result = await db.execute(
            select(Product).where(Product.is_active == True)
        )
        products_by_id = {product.id: product for product in products_result.scalars().all()}

        adjustment_result = await db.execute(
            select(InventoryAdjustment).where(
                InventoryAdjustment.device_id.in_(device_ids),
                InventoryAdjustment.adjustment_type.in_([
                    "manual_add",
                    "pdf_import",
                    "manual_remove",
                    "mixing_consumption",
                    "auto_consumed",
                ]),
            )
        )
        for adjustment in adjustment_result.scalars().all():
            product = products_by_id.get(adjustment.product_id)
            product_name = product.name if product else adjustment.product_id[:8]
            product_type = product.product_type if product else "base_paint"
            row = product_summary.setdefault(
                adjustment.product_id,
                _empty_inventory_row(adjustment.product_id, product_name, product_type),
            )
            row["liters"] = max(
                0.0,
                float(row.get("liters") or 0.0) + _inventory_delta_liters(
                    adjustment.adjustment_type,
                    adjustment.quantity_liters,
                ),
            )

    products = []
    low_stock_count = 0
    for row in product_summary.values():
        row["liters"] = round(float(row.get("liters") or 0.0), 1)
        if row["liters"] > 0:
            row["low_stock"] = row["liters"] <= 2.0
            if row["low_stock"]:
                low_stock_count += 1
            products.append(row)

    products.sort(key=lambda item: item["name"])
    return {
        "products": products,
        "total_liters": round(sum(item["liters"] for item in products), 1),
        "product_count": len(products),
        "low_stock_count": low_stock_count,
    }


async def _client_company_selector_context(
    db: AsyncSession,
    is_ppg_staff: bool,
    scoped_company_id: str | None,
) -> list[dict]:
    """Return company selector options when PPG previews the client portal."""
    if not is_ppg_staff:
        return []
    companies_result = await db.execute(select(Company).order_by(Company.name))
    return _client_company_selector_options(companies_result.scalars().all(), scoped_company_id)


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
    company_selector_options = await _client_company_selector_context(
        db,
        is_ppg_staff,
        scoped_company_id,
    )
    client_scope = _client_scope_summary(
        is_ppg_staff,
        scoped_company_id,
        company_selector_options,
    )

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
        "company_selector_options": company_selector_options,
        "client_scope": client_scope,
        "current_user": current_user,
        "is_ppg_staff": is_ppg_staff,
        "active": "client_dashboard",
    })


@router.get("/support", response_class=HTMLResponse)
async def client_support_requests(
    request: Request,
    company_id: str = Query(None),
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Read-only support ticket list for the client portal."""
    scoped_company_id = _client_dashboard_company_scope(current_user, company_id)
    is_ppg_staff = current_user.role in PPG_WEB_ROLES

    devices = []
    if is_ppg_staff or scoped_company_id:
        device_query = (
            select(LockerDevice)
            .options(selectinload(LockerDevice.vessel).selectinload(Vessel.fleet).selectinload(Fleet.company))
            .join(Vessel)
            .join(Fleet)
        )
        if scoped_company_id:
            device_query = device_query.where(Fleet.company_id == scoped_company_id)
        devices_result = await db.execute(device_query.order_by(LockerDevice.device_id))
        devices = devices_result.scalars().unique().all()

    edge_device_ids = [device.device_id for device in devices]
    support_requests = []
    show_global_support = _client_support_uses_global_scope(is_ppg_staff, scoped_company_id)
    if show_global_support or edge_device_ids:
        support_query = (
            select(SupportRequest)
            .options(selectinload(SupportRequest.device))
            .order_by(desc(SupportRequest.created_at))
            .limit(200)
        )
        if edge_device_ids:
            support_query = support_query.where(SupportRequest.device_id.in_(edge_device_ids))
        support_result = await db.execute(support_query)
        support_requests = support_result.scalars().all()
    company_selector_options = await _client_company_selector_context(
        db,
        is_ppg_staff,
        scoped_company_id,
    )
    client_scope = _client_scope_summary(
        is_ppg_staff,
        scoped_company_id,
        company_selector_options,
    )

    return templates.TemplateResponse("owner/support.html", {
        "request": request,
        "current_user": current_user,
        "is_ppg_staff": is_ppg_staff,
        "active": "client_support",
        "company_id": scoped_company_id,
        "company_selector_options": company_selector_options,
        "client_scope": client_scope,
        "devices": devices,
        "support_requests": support_requests,
        "stats": _support_request_stats(support_requests),
    })


@router.post("/support/create")
async def client_create_support_request(
    request: Request,
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
    device_id: str = Form(""),
    error_title: str = Form(""),
    severity: str = Form("warning"),
    details: str = Form(""),
    company_id: str = Form(None),
):
    """Create a client-originated support request for a device in their scope."""
    scoped_company_id = _client_dashboard_company_scope(current_user, company_id)
    is_ppg_staff = current_user.role in PPG_WEB_ROLES
    if is_ppg_staff:
        return RedirectResponse(
            _client_support_redirect(
                scoped_company_id,
                error="Use the PPG support dashboard for staff actions",
            ),
            status_code=303,
        )

    devices = []
    if scoped_company_id:
        devices_result = await db.execute(
            select(LockerDevice)
            .join(Vessel)
            .join(Fleet)
            .where(Fleet.company_id == scoped_company_id)
        )
        devices = devices_result.scalars().all()

    allowed_device_ids = {device.device_id for device in devices}
    validation_error = _client_support_request_error(
        device_id,
        error_title,
        allowed_device_ids,
    )
    if validation_error:
        return RedirectResponse(
            _client_support_redirect(scoped_company_id, error=validation_error),
            status_code=303,
        )

    support_request = SupportRequest(
        device_id=device_id.strip(),
        alarm_id="client-portal",
        error_code="CLIENT",
        error_title=error_title.strip()[:255],
        severity=_client_support_request_severity(severity),
        details=(details or "").strip() or None,
        user_name=getattr(current_user, "name", None) or getattr(current_user, "email", None) or "Client Portal",
        status="open",
    )
    db.add(support_request)
    await db.flush()

    return RedirectResponse(
        _client_support_redirect(scoped_company_id, success="Support request sent"),
        status_code=303,
    )


@router.get("/activity", response_class=HTMLResponse)
async def client_activity(
    request: Request,
    company_id: str = Query(None),
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Read-only fleet activity feed for the client portal."""
    scoped_company_id = _client_dashboard_company_scope(current_user, company_id)
    is_ppg_staff = current_user.role in PPG_WEB_ROLES

    devices = []
    if is_ppg_staff or scoped_company_id:
        device_query = (
            select(LockerDevice)
            .options(selectinload(LockerDevice.vessel).selectinload(Vessel.fleet).selectinload(Fleet.company))
            .join(Vessel)
            .join(Fleet)
        )
        if scoped_company_id:
            device_query = device_query.where(Fleet.company_id == scoped_company_id)
        devices_result = await db.execute(device_query.order_by(LockerDevice.device_id))
        devices = devices_result.scalars().unique().all()

    device_ids = [device.id for device in devices]
    events = []
    show_global_activity = _client_activity_uses_global_scope(is_ppg_staff, scoped_company_id)
    if show_global_activity or device_ids:
        event_query = (
            select(DeviceEvent)
            .options(selectinload(DeviceEvent.device))
            .order_by(desc(DeviceEvent.timestamp))
            .limit(200)
        )
        if device_ids:
            event_query = event_query.where(DeviceEvent.device_id.in_(device_ids))
        events_result = await db.execute(event_query)
        events = events_result.scalars().all()
    company_selector_options = await _client_company_selector_context(
        db,
        is_ppg_staff,
        scoped_company_id,
    )
    client_scope = _client_scope_summary(
        is_ppg_staff,
        scoped_company_id,
        company_selector_options,
    )

    return templates.TemplateResponse("owner/activity.html", {
        "request": request,
        "current_user": current_user,
        "is_ppg_staff": is_ppg_staff,
        "active": "client_activity",
        "company_id": scoped_company_id,
        "company_selector_options": company_selector_options,
        "client_scope": client_scope,
        "devices": devices,
        "events": events,
        "stats": _client_activity_event_stats(events),
    })


@router.get("/vessels/{vessel_id}", response_class=HTMLResponse)
async def client_vessel_detail(
    vessel_id: str,
    request: Request,
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Read-only vessel inventory and device status for the client portal."""
    vessel_result = await db.execute(
        select(Vessel)
        .options(
            selectinload(Vessel.fleet).selectinload(Fleet.company),
            selectinload(Vessel.devices),
        )
        .where(Vessel.id == vessel_id)
    )
    vessel = vessel_result.scalars().unique().one_or_none()
    if not vessel or not vessel.fleet:
        return RedirectResponse("/client/?error=Vessel+not+found", status_code=303)

    company_id = vessel.fleet.company_id
    if not _client_can_access_company(current_user, company_id):
        return RedirectResponse("/client/?error=Vessel+not+available", status_code=303)

    inventory = await _client_vessel_inventory_context(db, vessel)
    is_ppg_staff = current_user.role in PPG_WEB_ROLES

    return templates.TemplateResponse("owner/vessel_detail.html", {
        "request": request,
        "current_user": current_user,
        "is_ppg_staff": is_ppg_staff,
        "active": "client_dashboard",
        "company_id": company_id,
        "vessel": vessel,
        "total_liters": inventory["total_liters"],
        "product_count": inventory["product_count"],
        "low_stock_count": inventory["low_stock_count"],
        "products": inventory["products"],
    })
