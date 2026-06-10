"""Client Portal - Read-only fleet and support views.

This portal is for client roles only (ship_owner, crew). PPG staff are
redirected to /admin/ by require_client_session and preview client data
from /admin/client-preview instead. Every query here is scoped to the
authenticated user's own company; company_id query parameters are ignored.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, and_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.fleet import Fleet, Vessel
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.inventory import InventoryAdjustment
from app.models.product import Product
from app.models.support_request import SupportRequest
from app.web.auth_web import require_client_session

logger = logging.getLogger("smartlocker.dashboard")

router = APIRouter(prefix="/client", tags=["client-web"])
legacy_router = APIRouter(prefix="/dashboard", tags=["dashboard-web"])
templates = Jinja2Templates(directory="app/web/templates")


def _client_dashboard_company_scope(user, requested_company_id: str | None = None) -> str | None:
    """Return the company scope for the client portal.

    The scope is always the user's assigned company. A company_id requested
    via query string is deliberately ignored so clients can never browse
    another company's data.
    """
    del requested_company_id  # Never honoured: clients only see their company.
    return getattr(user, "company_id", None)


def _client_can_access_company(user, company_id: str | None) -> bool:
    """Return whether a client user may view data for a company."""
    return bool(company_id) and getattr(user, "company_id", None) == company_id


def _client_scope_summary() -> dict:
    """Describe the client-portal data scope for the page header."""
    return {
        "title": "Client view",
        "detail": "Showing only vessels linked to your company.",
        "badge": "client",
    }


def _client_dashboard_quick_actions(vessels: list, support_requests: list) -> list[dict]:
    """Return a compact, priority-ordered action list for the client dashboard."""
    actions: list[dict] = []

    if vessels:
        first_vessel = vessels[0]
        actions.append({
            "label": "Open first vessel",
            "href": f"/client/vessels/{first_vessel.id}",
            "detail": "Review installed SmartLocker devices and visible stock.",
            "badge": "fleet",
            "tone": "primary",
        })
    else:
        actions.append({
            "label": "No vessels yet",
            "href": "",
            "detail": "PPG configures vessels, devices, and initial stock.",
            "badge": "setup",
            "tone": "muted",
        })

    open_support_count = len(support_requests)
    actions.extend([
        {
            "label": "Support",
            "href": "/client/support",
            "detail": "Open tickets, device issues, and client requests.",
            "badge": f"{open_support_count} open" if open_support_count else "ready",
            "tone": "danger" if open_support_count else "success",
        },
        {
            "label": "Activity",
            "href": "/client/activity",
            "detail": "See scans, inventory events, and locker activity.",
            "badge": "latest",
            "tone": "info",
        },
    ])
    return actions


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


def _client_support_redirect(**params: str) -> str:
    """Build a client support redirect with optional feedback messages."""
    if not params:
        return "/client/support"
    return f"/client/support?{urlencode(params)}"


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


def _client_vessel_inventory_status(devices: list, products: list) -> dict:
    """Explain the client-visible inventory state for one vessel."""
    if not devices:
        return {
            "title": "SmartLocker not installed",
            "detail": "PPG must assign a SmartLocker before live stock can appear for this vessel.",
            "badge": "setup",
            "tone": "warning",
        }

    if products:
        product_count = len(products)
        return {
            "title": "Inventory visible",
            "detail": "Stock combines SmartLocker reports with PPG inventory adjustments.",
            "badge": f"{product_count} product" if product_count == 1 else f"{product_count} products",
            "tone": "ready",
        }

    return {
        "title": "Waiting for stock",
        "detail": "A SmartLocker is installed, but no stock is visible yet. PPG can add stock or wait for the next device sync.",
        "badge": "empty",
        "tone": "warning",
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


async def _company_vessels(db: AsyncSession, company_id: str | None) -> list:
    """Load the vessels (with fleet and devices) for one client company."""
    if not company_id:
        return []
    vessel_query = (
        select(Vessel)
        .options(
            selectinload(Vessel.fleet).selectinload(Fleet.company),
            selectinload(Vessel.devices),
        )
        .join(Fleet)
        .where(Fleet.company_id == company_id)
        .order_by(Vessel.name)
    )
    vessel_result = await db.execute(vessel_query)
    return vessel_result.scalars().unique().all()


async def _company_devices(db: AsyncSession, company_id: str | None) -> list:
    """Load the SmartLocker devices for one client company."""
    if not company_id:
        return []
    device_query = (
        select(LockerDevice)
        .options(selectinload(LockerDevice.vessel).selectinload(Vessel.fleet).selectinload(Fleet.company))
        .join(Vessel)
        .join(Fleet)
        .where(Fleet.company_id == company_id)
        .order_by(LockerDevice.device_id)
    )
    devices_result = await db.execute(device_query)
    return devices_result.scalars().unique().all()


@legacy_router.get("/", response_class=HTMLResponse)
async def legacy_dashboard_redirect():
    """Keep old dashboard links working while the client portal moves to /client."""
    return RedirectResponse("/client/", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def client_dashboard(
    request: Request,
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Client fleet overview, always scoped to the user's company."""
    scoped_company_id = _client_dashboard_company_scope(current_user)

    vessels = await _company_vessels(db, scoped_company_id)

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

    # ---- Open support requests for this company's devices ----
    support_requests = []
    if device_ids:
        edge_device_ids = [d.device_id for d in all_devices]
        support_result = await db.execute(
            select(SupportRequest)
            .where(
                SupportRequest.status.in_(["open", "in_progress"]),
                SupportRequest.device_id.in_(edge_device_ids),
            )
            .order_by(desc(SupportRequest.created_at))
            .limit(20)
        )
        support_requests = support_result.scalars().all()

    return templates.TemplateResponse("client/dashboard.html", {
        "request": request,
        "vessels": vessels,
        "total_vessels": total_vessels,
        "total_devices": total_devices,
        "online_count": online_count,
        "offline_count": offline_count,
        "recent_events": recent_events,
        "support_requests": support_requests,
        "event_count_24h": len(recent_events),
        "client_scope": _client_scope_summary(),
        "current_user": current_user,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
        "quick_actions": _client_dashboard_quick_actions(vessels, support_requests),
        "active": "client_dashboard",
    })


@router.get("/support", response_class=HTMLResponse)
async def client_support_requests(
    request: Request,
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Support ticket list for the client's own devices."""
    scoped_company_id = _client_dashboard_company_scope(current_user)

    devices = await _company_devices(db, scoped_company_id)

    edge_device_ids = [device.device_id for device in devices]
    support_requests = []
    if edge_device_ids:
        support_result = await db.execute(
            select(SupportRequest)
            .options(selectinload(SupportRequest.device))
            .where(SupportRequest.device_id.in_(edge_device_ids))
            .order_by(desc(SupportRequest.created_at))
            .limit(200)
        )
        support_requests = support_result.scalars().all()

    return templates.TemplateResponse("client/support.html", {
        "request": request,
        "current_user": current_user,
        "active": "client_support",
        "client_scope": _client_scope_summary(),
        "devices": devices,
        "support_requests": support_requests,
        "stats": _support_request_stats(support_requests),
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
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
):
    """Create a client-originated support request for a device in their scope."""
    scoped_company_id = _client_dashboard_company_scope(current_user)

    devices = await _company_devices(db, scoped_company_id)
    allowed_device_ids = {device.device_id for device in devices}
    validation_error = _client_support_request_error(
        device_id,
        error_title,
        allowed_device_ids,
    )
    if validation_error:
        return RedirectResponse(
            _client_support_redirect(error=validation_error),
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
        _client_support_redirect(success="Support request sent"),
        status_code=303,
    )


@router.get("/activity", response_class=HTMLResponse)
async def client_activity(
    request: Request,
    current_user = Depends(require_client_session),
    db: AsyncSession = Depends(get_db),
):
    """Read-only fleet activity feed for the client's own devices."""
    scoped_company_id = _client_dashboard_company_scope(current_user)

    devices = await _company_devices(db, scoped_company_id)

    device_ids = [device.id for device in devices]
    events = []
    if device_ids:
        events_result = await db.execute(
            select(DeviceEvent)
            .options(selectinload(DeviceEvent.device))
            .where(DeviceEvent.device_id.in_(device_ids))
            .order_by(desc(DeviceEvent.timestamp))
            .limit(200)
        )
        events = events_result.scalars().all()

    return templates.TemplateResponse("client/activity.html", {
        "request": request,
        "current_user": current_user,
        "active": "client_activity",
        "client_scope": _client_scope_summary(),
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

    if not _client_can_access_company(current_user, vessel.fleet.company_id):
        return RedirectResponse("/client/?error=Vessel+not+available", status_code=303)

    inventory = await _client_vessel_inventory_context(db, vessel)

    return templates.TemplateResponse("client/vessel_detail.html", {
        "request": request,
        "current_user": current_user,
        "active": "client_dashboard",
        "vessel": vessel,
        "total_liters": inventory["total_liters"],
        "product_count": inventory["product_count"],
        "low_stock_count": inventory["low_stock_count"],
        "inventory_status": _client_vessel_inventory_status(
            vessel.devices,
            inventory["products"],
        ),
        "products": inventory["products"],
    })
