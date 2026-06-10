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


def _support_request_stats(support_requests: list) -> dict:
    """Build compact support stats for the client portal."""
    open_count = sum(1 for request in support_requests if request.status in {"open", "in_progress"})
    return {
        "total": len(support_requests),
        "open": open_count,
        "resolved": len(support_requests) - open_count,
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

    return templates.TemplateResponse("owner/support.html", {
        "request": request,
        "current_user": current_user,
        "is_ppg_staff": is_ppg_staff,
        "active": "client_support",
        "company_id": scoped_company_id,
        "devices": devices,
        "support_requests": support_requests,
        "stats": _support_request_stats(support_requests),
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
        "vessel": vessel,
        "total_liters": inventory["total_liters"],
        "product_count": inventory["product_count"],
        "low_stock_count": inventory["low_stock_count"],
        "products": inventory["products"],
    })
