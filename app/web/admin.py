"""PPG Admin Portal - Web routes with Jinja2 templates."""

import os
import re
import json
import asyncio
import logging
import urllib.request
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.product import Product, MixingRecipe
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.company import Company
from app.models.fleet import Fleet, Vessel
from app.models.pairing import PairingCode
from app.models.maintenance import MaintenanceChart
from app.models.can_tracking import CanTracking
from app.models.inventory import InventoryAdjustment
from app.api.events import _aggregate_sensor_issues

logger = logging.getLogger("smartlocker.admin")

router = APIRouter(prefix="/admin", tags=["admin-web"])
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    """Admin dashboard overview."""
    # Get counts
    products_count = (await db.execute(select(func.count(Product.id)))).scalar() or 0
    recipes_count = (await db.execute(select(func.count(MixingRecipe.id)))).scalar() or 0
    devices_count = (await db.execute(select(func.count(LockerDevice.id)))).scalar() or 0
    events_count = (await db.execute(select(func.count(DeviceEvent.id)))).scalar() or 0
    companies_count = (await db.execute(select(func.count(Company.id)))).scalar() or 0

    # Recent events
    recent_events_result = await db.execute(
        select(DeviceEvent).order_by(DeviceEvent.received_at.desc()).limit(10)
    )
    recent_events = recent_events_result.scalars().all()

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "products_count": products_count,
        "recipes_count": recipes_count,
        "devices_count": devices_count,
        "events_count": events_count,
        "companies_count": companies_count,
        "recent_events": recent_events,
    })


@router.get("/products", response_class=HTMLResponse)
async def admin_products(request: Request, db: AsyncSession = Depends(get_db)):
    """Product catalog management."""
    result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = result.scalars().all()
    return templates.TemplateResponse("admin/products.html", {
        "request": request,
        "products": products,
    })


@router.post("/products/add")
async def admin_add_product(
    request: Request,
    ppg_code: str = Form(...),
    name: str = Form(...),
    product_type: str = Form(...),
    density_g_per_ml: float = Form(1.0),
    pot_life_minutes: int = Form(None),
    hazard_class: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Add a new product via form."""
    product = Product(
        ppg_code=ppg_code,
        name=name,
        product_type=product_type,
        density_g_per_ml=density_g_per_ml,
        pot_life_minutes=pot_life_minutes if pot_life_minutes else None,
        hazard_class=hazard_class or None,
    )
    db.add(product)
    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/recipes", response_class=HTMLResponse)
async def admin_recipes(request: Request, db: AsyncSession = Depends(get_db)):
    """Recipe management."""
    result = await db.execute(
        select(MixingRecipe).where(MixingRecipe.is_active == True).order_by(MixingRecipe.name)
    )
    recipes = result.scalars().all()

    # Get products for dropdowns
    products_result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = products_result.scalars().all()

    return templates.TemplateResponse("admin/recipes.html", {
        "request": request,
        "recipes": recipes,
        "products": products,
    })


@router.post("/recipes/add")
async def admin_add_recipe(
    request: Request,
    name: str = Form(...),
    base_product_id: str = Form(...),
    hardener_product_id: str = Form(...),
    ratio_base: float = Form(3.0),
    ratio_hardener: float = Form(1.0),
    tolerance_pct: float = Form(5.0),
    thinner_pct_brush: float = Form(0),
    thinner_pct_roller: float = Form(0),
    thinner_pct_spray: float = Form(5),
    recommended_thinner_id: str = Form(None),
    pot_life_minutes: int = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Add a new recipe via form."""
    recipe = MixingRecipe(
        name=name,
        base_product_id=base_product_id,
        hardener_product_id=hardener_product_id,
        ratio_base=ratio_base,
        ratio_hardener=ratio_hardener,
        tolerance_pct=tolerance_pct,
        thinner_pct_brush=thinner_pct_brush,
        thinner_pct_roller=thinner_pct_roller,
        thinner_pct_spray=thinner_pct_spray,
        recommended_thinner_id=recommended_thinner_id if recommended_thinner_id else None,
        pot_life_minutes=pot_life_minutes if pot_life_minutes else None,
    )
    db.add(recipe)
    return RedirectResponse(url="/admin/recipes", status_code=303)


@router.get("/events", response_class=HTMLResponse)
async def admin_events(request: Request, db: AsyncSession = Depends(get_db)):
    """Event log viewer."""
    result = await db.execute(
        select(DeviceEvent).order_by(DeviceEvent.received_at.desc()).limit(100)
    )
    events = result.scalars().all()
    return templates.TemplateResponse("admin/events.html", {
        "request": request,
        "events": events,
    })


_GITHUB_API_URL = (
    "https://api.github.com/repos/"
    "TheGuideProject/smartlocker-edge/contents/config/VERSION"
)
_GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/"
    "TheGuideProject/smartlocker-edge/master/config/VERSION"
)


def _get_latest_version_from_github() -> dict:
    """Fetch the latest version from the GitHub repo's config/VERSION file.

    Tries GitHub API with token first (works for private repos),
    falls back to raw URL (works for public repos).
    Returns {"version": "x.y.z", "error": None} on success,
    or {"version": None, "error": "..."} on failure.
    """
    import base64

    github_token = os.environ.get("GITHUB_TOKEN", "")

    # Method 1: GitHub API (works for private repos with token)
    if github_token:
        try:
            req = urllib.request.Request(_GITHUB_API_URL)
            req.add_header("Authorization", f"token {github_token}")
            req.add_header("Accept", "application/vnd.github.v3+json")
            with urllib.request.urlopen(req, timeout=8) as resp:
                import json as _json
                data = _json.loads(resp.read().decode("utf-8"))
                content = base64.b64decode(data.get("content", ""))
                version = content.decode("utf-8").strip()
                if version:
                    return {"version": version, "error": None}
        except Exception as exc:
            logger.warning("GitHub API fetch failed: %s", exc)

    # Method 2: Raw URL (works for public repos)
    try:
        req = urllib.request.Request(_GITHUB_RAW_URL)
        if github_token:
            req.add_header("Authorization", f"token {github_token}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            version = resp.read().decode("utf-8").strip()
            if version:
                return {"version": version, "error": None}
            return {"version": None, "error": "Empty VERSION file"}
    except Exception as exc:
        logger.warning("Failed to fetch latest version from GitHub: %s", exc)
        return {"version": None, "error": str(exc)}


@router.get("/devices", response_class=HTMLResponse)
async def admin_devices(request: Request, db: AsyncSession = Depends(get_db)):
    """Device monitoring dashboard."""
    result = await db.execute(
        select(LockerDevice)
        .options(selectinload(LockerDevice.vessel))
        .order_by(LockerDevice.last_heartbeat.desc().nullslast())
    )
    devices = result.scalars().all()

    # Get vessels for dropdown (for register form)
    vessels_result = await db.execute(select(Vessel).order_by(Vessel.name))
    vessels = vessels_result.scalars().all()

    # Build enriched device list with monitoring info + aggregated health
    device_list = []
    for d in devices:
        sensor_alerts = _check_sensor_health(d.sensor_health) if d.sensor_health else []

        # Smart aggregation: get aggregated health from stored health logs
        try:
            health_summary = await _aggregate_sensor_issues(db, d.id, hours=48)
        except Exception:
            health_summary = []

        device_list.append({
            'device': d,
            'is_online': d.is_online,
            'last_seen_ago': d.last_seen_ago,
            'sensor_alerts': sensor_alerts,
            'vessel_name': d.vessel.name if d.vessel else 'Unassigned',
            'health_summary': health_summary,
            'update_status': d.update_status,
            'pending_update_version': d.pending_update_version,
            'update_error': d.update_error,
            'update_requested_at': d.update_requested_at,
        })

    online_count = sum(1 for d in device_list if d['is_online'])

    # Collect all active alerts across devices
    all_alerts = []
    for d in device_list:
        for alert in d['sensor_alerts']:
            all_alerts.append({
                'device_name': d['device'].name or d['device'].device_id,
                'level': alert['level'],
                'message': alert['message'],
                'sensor': alert['sensor'],
            })

    # Fetch latest version from GitHub (run in thread to avoid blocking)
    latest = await asyncio.to_thread(_get_latest_version_from_github)
    latest_version = latest.get("version")

    return templates.TemplateResponse("admin/devices.html", {
        "request": request,
        "devices": device_list,
        "vessels": vessels,
        "online_count": online_count,
        "total_count": len(device_list),
        "all_alerts": all_alerts,
        "latest_version": latest_version,
    })


@router.post("/devices/add")
async def admin_add_device(
    request: Request,
    device_id: str = Form(...),
    vessel_id: str = Form(...),
    name: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Register a new device from admin UI."""
    api_key = LockerDevice.generate_api_key()
    device = LockerDevice(
        device_id=device_id,
        vessel_id=vessel_id,
        name=name or None,
        api_key_hash=api_key,  # Store plaintext for MVP; hash in production
    )
    db.add(device)
    await db.flush()  # So we can redirect
    return RedirectResponse(url="/admin/devices", status_code=303)


# ---- Fleet Management (Company → Fleet → Vessel) ----

@router.get("/fleet", response_class=HTMLResponse)
async def admin_fleet(request: Request, db: AsyncSession = Depends(get_db)):
    """Fleet management: companies, fleets, vessels."""
    # Get companies with their fleets and vessels (eager load)
    companies_result = await db.execute(
        select(Company).options(
            selectinload(Company.fleets)
            .selectinload(Fleet.vessels)
            .selectinload(Vessel.devices)
        ).order_by(Company.name)
    )
    companies = companies_result.scalars().all()

    return templates.TemplateResponse("admin/fleet.html", {
        "request": request,
        "companies": companies,
    })


@router.post("/companies/add")
async def admin_add_company(
    request: Request,
    name: str = Form(...),
    contact_email: str = Form(""),
    contact_phone: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Add a new company (ship owner)."""
    company = Company(
        name=name,
        contact_email=contact_email or None,
        contact_phone=contact_phone or None,
    )
    db.add(company)
    return RedirectResponse(url="/admin/fleet", status_code=303)


@router.post("/fleets/add")
async def admin_add_fleet(
    request: Request,
    company_id: str = Form(...),
    name: str = Form(...),
    region: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Add a new fleet under a company."""
    fleet = Fleet(
        company_id=company_id,
        name=name,
        region=region or None,
    )
    db.add(fleet)
    return RedirectResponse(url="/admin/fleet", status_code=303)


@router.post("/vessels/add")
async def admin_add_vessel(
    request: Request,
    fleet_id: str = Form(...),
    name: str = Form(...),
    imo_number: str = Form(""),
    vessel_type: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Add a new vessel under a fleet."""
    vessel = Vessel(
        fleet_id=fleet_id,
        name=name,
        imo_number=imo_number or None,
        vessel_type=vessel_type or None,
    )
    db.add(vessel)
    return RedirectResponse(url="/admin/fleet", status_code=303)


# ---- Pairing Code Management ----

@router.get("/pairing", response_class=HTMLResponse)
async def admin_pairing(request: Request, db: AsyncSession = Depends(get_db)):
    """Pairing code management page."""
    # Get all pairing codes with vessel info
    codes_result = await db.execute(
        select(PairingCode)
        .options(
            selectinload(PairingCode.vessel),
            selectinload(PairingCode.device),
        )
        .order_by(PairingCode.created_at.desc())
    )
    codes = codes_result.scalars().all()

    # Get vessels for the dropdown (with fleet/company info)
    vessels_result = await db.execute(
        select(Vessel)
        .options(selectinload(Vessel.fleet).selectinload(Fleet.company))
        .order_by(Vessel.name)
    )
    vessels = vessels_result.scalars().all()

    return templates.TemplateResponse("admin/pairing.html", {
        "request": request,
        "codes": codes,
        "vessels": vessels,
    })


@router.post("/pairing/generate")
async def admin_generate_pairing_code(
    request: Request,
    vessel_id: str = Form(...),
    device_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new 6-digit pairing code for a vessel."""
    # Generate unique code (retry if collision)
    for _ in range(10):
        code = PairingCode.generate_code()
        existing = await db.execute(
            select(PairingCode).where(PairingCode.code == code)
        )
        if not existing.scalar_one_or_none():
            break
    else:
        # Extremely unlikely, but handle gracefully
        code = PairingCode.generate_code()

    pairing = PairingCode(
        code=code,
        vessel_id=vessel_id,
        device_name=device_name or None,
        expires_at=PairingCode.default_expiry(),
    )
    db.add(pairing)
    return RedirectResponse(url="/admin/pairing", status_code=303)


# ---- Maintenance Charts ----

@router.get("/charts", response_class=HTMLResponse)
async def admin_charts(request: Request, db: AsyncSession = Depends(get_db)):
    """Maintenance chart management page."""
    # Get all charts
    charts_result = await db.execute(
        select(MaintenanceChart).order_by(MaintenanceChart.created_at.desc())
    )
    charts = charts_result.scalars().all()

    # Get vessels for dropdown
    vessels_result = await db.execute(
        select(Vessel)
        .options(selectinload(Vessel.fleet).selectinload(Fleet.company))
        .order_by(Vessel.name)
    )
    vessels = vessels_result.scalars().all()

    return templates.TemplateResponse("admin/charts.html", {
        "request": request,
        "charts": charts,
        "vessels": vessels,
        "active": "charts",
    })


@router.post("/charts/upload", response_class=HTMLResponse)
async def admin_upload_chart(
    request: Request,
    vessel_id: str = Form(""),
    pdf_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a PDF maintenance chart and show parsed preview for editing."""
    from app.services.chart_parser import parse_maintenance_chart

    # Validate file type
    if not pdf_file.filename.lower().endswith(".pdf"):
        # Get vessels for re-rendering the page
        vessels_result = await db.execute(
            select(Vessel)
            .options(selectinload(Vessel.fleet).selectinload(Fleet.company))
            .order_by(Vessel.name)
        )
        vessels = vessels_result.scalars().all()
        charts_result = await db.execute(
            select(MaintenanceChart).order_by(MaintenanceChart.created_at.desc())
        )
        charts = charts_result.scalars().all()
        return templates.TemplateResponse("admin/charts.html", {
            "request": request,
            "charts": charts,
            "vessels": vessels,
            "active": "charts",
            "error": "Only PDF files are accepted.",
        })

    # Read PDF bytes
    pdf_bytes = await pdf_file.read()

    # Save PDF to disk
    os.makedirs(os.path.join(settings.UPLOAD_DIR, "charts"), exist_ok=True)
    safe_name = pdf_file.filename.replace(" ", "_")
    pdf_path = os.path.join(settings.UPLOAD_DIR, "charts", safe_name)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    # Parse the PDF
    try:
        parsed = parse_maintenance_chart(pdf_bytes)
    except Exception as e:
        logger.error(f"PDF parse error: {e}")
        vessels_result = await db.execute(
            select(Vessel)
            .options(selectinload(Vessel.fleet).selectinload(Fleet.company))
            .order_by(Vessel.name)
        )
        vessels = vessels_result.scalars().all()
        charts_result = await db.execute(
            select(MaintenanceChart).order_by(MaintenanceChart.created_at.desc())
        )
        charts = charts_result.scalars().all()
        return templates.TemplateResponse("admin/charts.html", {
            "request": request,
            "charts": charts,
            "vessels": vessels,
            "active": "charts",
            "error": f"Error parsing PDF: {str(e)}",
        })

    # Get vessels for the vessel selector
    vessels_result = await db.execute(
        select(Vessel)
        .options(selectinload(Vessel.fleet).selectinload(Fleet.company))
        .order_by(Vessel.name)
    )
    vessels = vessels_result.scalars().all()

    # Auto-match vessel by IMO number
    matched_vessel_id = vessel_id or ""
    if not matched_vessel_id and parsed.get("imo_number"):
        for v in vessels:
            if v.imo_number == parsed["imo_number"]:
                matched_vessel_id = v.id
                break

    return templates.TemplateResponse("admin/chart_preview.html", {
        "request": request,
        "parsed": parsed,
        "parsed_json": json.dumps(parsed),
        "pdf_path": pdf_path,
        "pdf_filename": pdf_file.filename,
        "vessel_id": matched_vessel_id,
        "vessels": vessels,
        "active": "charts",
    })


@router.post("/charts/confirm", response_class=HTMLResponse)
async def admin_confirm_chart(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save the edited chart data + auto-create products."""
    form = await request.form()

    vessel_id = form.get("vessel_id", "")
    pdf_path = form.get("pdf_path", "")
    pdf_filename = form.get("pdf_filename", "")
    chart_name = form.get("chart_name", pdf_filename or "Maintenance Chart")
    imo_number = form.get("imo_number", "")
    vessel_name = form.get("vessel_name", "")

    # Rebuild the parsed data from the edited form fields
    # Products
    products_data = []
    idx = 0
    while True:
        pname = form.get(f"product_{idx}_name")
        if pname is None:
            break
        products_data.append({
            "name": pname,
            "thinner": form.get(f"product_{idx}_thinner", ""),
            "components": int(form.get(f"product_{idx}_components", "1")),
            "base_ratio": int(form.get(f"product_{idx}_base_ratio", "100")),
            "hardener_ratio": int(form.get(f"product_{idx}_hardener_ratio", "0")),
            "coverage_m2_per_liter": int(form.get(f"product_{idx}_coverage", "0")),
        })
        idx += 1

    # Areas with layers
    areas_data = []
    area_idx = 0
    while True:
        area_name = form.get(f"area_{area_idx}_name")
        if area_name is None:
            break
        layers = []
        layer_idx = 0
        while True:
            lproduct = form.get(f"area_{area_idx}_layer_{layer_idx}_product")
            if lproduct is None:
                break
            layers.append({
                "layer_number": layer_idx + 1,
                "product": lproduct,
                "color": form.get(f"area_{area_idx}_layer_{layer_idx}_color", ""),
            })
            layer_idx += 1
        areas_data.append({
            "name": area_name,
            "layers": layers,
            "notes": form.get(f"area_{area_idx}_notes", ""),
        })
        area_idx += 1

    # Marking colors
    marking_data = []
    mc_idx = 0
    while True:
        mc_purpose = form.get(f"marking_{mc_idx}_purpose")
        if mc_purpose is None:
            break
        marking_data.append({
            "purpose": mc_purpose,
            "color": form.get(f"marking_{mc_idx}_color", ""),
        })
        mc_idx += 1

    # Final parsed data
    final_data = {
        "vessel_name": vessel_name,
        "imo_number": imo_number,
        "products": products_data,
        "areas": areas_data,
        "marking_colors": marking_data,
    }

    # ---- Auto-create products in the catalog ----
    created_products = 0
    for p in products_data:
        pname = p["name"].strip()
        if not pname:
            continue

        # Check if product already exists (by name)
        existing = await db.execute(
            select(Product).where(Product.name == pname)
        )
        if existing.scalar_one_or_none():
            continue  # Already in catalog

        # Determine product type based on name/components
        if p.get("components", 1) == 2:
            # It's a 2-component system — create base and hardener pair
            product_type = "base_paint"
        elif "THERM" in pname.upper():
            product_type = "base_paint"
        elif "PRIME" in pname.upper():
            product_type = "primer"
        elif "DUR" in pname.upper() or "RITE" in pname.upper():
            product_type = "base_paint"
        else:
            product_type = "base_paint"

        # Generate a PPG code from the name (e.g. "SIGMACOVER 280" → "SC-280")
        ppg_code = _generate_ppg_code(pname)

        # Check ppg_code uniqueness
        existing_code = await db.execute(
            select(Product).where(Product.ppg_code == ppg_code)
        )
        if existing_code.scalar_one_or_none():
            ppg_code = ppg_code + "-AUTO"

        new_product = Product(
            ppg_code=ppg_code,
            name=pname,
            product_type=product_type,
            density_g_per_ml=1.0,
            description=f"Auto-imported from maintenance chart: {chart_name}",
        )
        db.add(new_product)
        created_products += 1

    # ---- Save the MaintenanceChart record ----
    chart = MaintenanceChart(
        name=chart_name,
        vessel_id=vessel_id if vessel_id else None,
        imo_number=imo_number,
        pdf_file_path=pdf_path,
        parsed_data=final_data,
        description=f"Parsed from: {pdf_filename}",
    )
    db.add(chart)
    await db.flush()

    logger.info(
        f"Chart saved: {chart_name}, {created_products} new products created, "
        f"{len(areas_data)} areas"
    )

    # Redirect to charts page with success message
    return RedirectResponse(
        url=f"/admin/charts?saved=1&products={created_products}",
        status_code=303,
    )


@router.get("/charts/{chart_id}", response_class=HTMLResponse)
async def admin_chart_detail(
    chart_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """View a saved chart with its data."""
    chart = await db.get(MaintenanceChart, chart_id)
    if not chart:
        return RedirectResponse(url="/admin/charts", status_code=303)

    return templates.TemplateResponse("admin/chart_detail.html", {
        "request": request,
        "chart": chart,
        "active": "charts",
    })


@router.post("/devices/{device_id}/change-password")
async def admin_change_device_password(
    device_id: str,
    request: Request,
    new_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Set a pending admin password for a device (pushed on next config sync)."""
    result = await db.execute(
        select(LockerDevice).where(LockerDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if not device:
        return RedirectResponse(url="/admin/devices", status_code=303)

    device.pending_admin_password = new_password
    return RedirectResponse(url="/admin/devices", status_code=303)


@router.post("/devices/send-update-all")
async def admin_send_update_all(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Send OTA update to ALL devices."""
    form = await request.form()
    target_version = form.get("target_version", "").strip()
    branch = form.get("branch", "master").strip() or "master"

    if not target_version:
        return RedirectResponse(url="/admin/devices", status_code=303)

    result = await db.execute(select(LockerDevice))
    devices = result.scalars().all()

    for device in devices:
        device.pending_update_version = target_version
        device.pending_update_branch = branch
        device.update_status = "pending"
        device.update_requested_at = datetime.utcnow()
        device.update_error = None

    await db.commit()
    return RedirectResponse(url="/admin/devices", status_code=303)


@router.post("/devices/{device_id}/send-update")
async def admin_send_update(
    request: Request,
    device_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Send OTA update command to a specific device."""
    form = await request.form()
    target_version = form.get("target_version", "").strip()
    branch = form.get("branch", "master").strip() or "master"

    if not target_version:
        return RedirectResponse(url="/admin/devices", status_code=303)

    result = await db.execute(
        select(LockerDevice).where(LockerDevice.id == device_id)
    )
    device = result.scalar_one_or_none()
    if device:
        device.pending_update_version = target_version
        device.pending_update_branch = branch
        device.update_status = "pending"
        device.update_requested_at = datetime.utcnow()
        device.update_error = None
        await db.commit()

    return RedirectResponse(url="/admin/devices", status_code=303)


# ---- Inventory Monitoring ----

@router.get("/inventory", response_class=HTMLResponse)
async def admin_inventory(
    request: Request,
    device_filter: str = Query("", alias="device"),
    product_filter: str = Query("", alias="product"),
    db: AsyncSession = Depends(get_db),
):
    """Inventory monitoring dashboard."""
    # Get all devices with vessel info
    devices_result = await db.execute(
        select(LockerDevice)
        .options(selectinload(LockerDevice.vessel))
        .order_by(LockerDevice.name)
    )
    devices = devices_result.scalars().all()

    # Get all products for filter dropdown
    products_result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = products_result.scalars().all()

    # Build inventory data per device
    vessel_inventory = []
    total_cans = 0
    total_in_use = 0
    total_liters = 0.0
    low_stock_alerts = []

    for device in devices:
        # Get all can tracking records for this device
        can_query = select(CanTracking).where(
            CanTracking.device_id == device.id
        )
        if device_filter and device_filter != device.id:
            continue

        cans_result = await db.execute(can_query)
        cans = cans_result.scalars().all()

        if not cans and not device_filter:
            continue  # Skip devices with no cans unless filtered

        # Build product breakdown
        product_breakdown = {}
        device_cans_in_use = 0
        for can in cans:
            if product_filter and can.product_id != product_filter:
                continue

            product_name = "Unknown Product"
            density = 1.0
            if can.product_id:
                for p in products:
                    if p.id == can.product_id:
                        product_name = p.name
                        density = p.density_g_per_ml or 1.0
                        break

            if product_name not in product_breakdown:
                product_breakdown[product_name] = {
                    "product_id": can.product_id,
                    "can_count": 0,
                    "full_liters": 0.0,
                    "current_liters": 0.0,
                    "total_consumed_g": 0.0,
                    "cans": [],
                }

            pb = product_breakdown[product_name]
            pb["can_count"] += 1
            pb["cans"].append(can)

            if can.weight_full_g and density > 0:
                pb["full_liters"] += (can.weight_full_g / density) / 1000.0
            if can.weight_current_g and density > 0:
                pb["current_liters"] += (can.weight_current_g / density) / 1000.0
            pb["total_consumed_g"] += can.total_consumed_g or 0

            if can.status == "in_use":
                device_cans_in_use += 1

            total_cans += 1
            if can.status == "in_use":
                total_in_use += 1

        # Calculate used percentage for each product
        for pname, pb in product_breakdown.items():
            if pb["full_liters"] > 0:
                pb["used_pct"] = round(
                    ((pb["full_liters"] - pb["current_liters"]) / pb["full_liters"]) * 100,
                    1,
                )
            else:
                pb["used_pct"] = 0.0

            total_liters += pb["current_liters"]

            # Low stock alert: less than 20% remaining
            if pb["used_pct"] > 80:
                low_stock_alerts.append({
                    "vessel": device.vessel.name if device.vessel else device.device_id,
                    "product": pname,
                    "remaining_pct": round(100 - pb["used_pct"], 1),
                    "current_liters": round(pb["current_liters"], 1),
                })

        vessel_inventory.append({
            "device": device,
            "vessel_name": device.vessel.name if device.vessel else "Unassigned",
            "is_online": device.is_online,
            "last_seen_ago": device.last_seen_ago,
            "product_breakdown": product_breakdown,
            "total_cans": len(cans),
            "cans_in_use": device_cans_in_use,
            "cans": cans,
        })

    # Get recent inventory events
    event_types = [
        "can_placed", "can_removed", "can_returned",
        "can_consumed", "unauthorized_removal",
    ]
    recent_events_result = await db.execute(
        select(DeviceEvent)
        .where(DeviceEvent.event_type.in_(event_types))
        .order_by(DeviceEvent.timestamp.desc())
        .limit(20)
    )
    recent_events = recent_events_result.scalars().all()

    # Get recent adjustments
    adjustments_result = await db.execute(
        select(InventoryAdjustment)
        .order_by(InventoryAdjustment.created_at.desc())
        .limit(10)
    )
    adjustments = adjustments_result.scalars().all()

    return templates.TemplateResponse("admin/inventory.html", {
        "request": request,
        "vessel_inventory": vessel_inventory,
        "total_cans": total_cans,
        "total_in_use": total_in_use,
        "total_liters": round(total_liters, 1),
        "low_stock_alerts": low_stock_alerts,
        "recent_events": recent_events,
        "adjustments": adjustments,
        "products": products,
        "devices": devices,
        "device_filter": device_filter,
        "product_filter": product_filter,
        "active": "inventory",
    })


@router.post("/inventory/adjust")
async def admin_adjust_inventory(
    request: Request,
    product_id: str = Form(...),
    device_id: str = Form(""),
    adjustment_type: str = Form(...),
    quantity_cans: int = Form(0),
    quantity_liters: float = Form(0.0),
    lot_number: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create a manual inventory adjustment."""
    # Calculate weight from liters using product density
    weight_g = 0.0
    if quantity_liters > 0:
        product_result = await db.execute(
            select(Product).where(Product.id == product_id)
        )
        product = product_result.scalar_one_or_none()
        if product:
            weight_g = quantity_liters * 1000 * (product.density_g_per_ml or 1.0)

    adjustment = InventoryAdjustment(
        device_id=device_id if device_id else None,
        product_id=product_id,
        adjustment_type=adjustment_type,
        quantity_cans=quantity_cans,
        quantity_liters=quantity_liters,
        weight_g=weight_g,
        lot_number=lot_number or None,
        notes=notes or None,
        created_by="admin",
    )
    db.add(adjustment)
    return RedirectResponse(url="/admin/inventory", status_code=303)


@router.post("/inventory/import-pdf", response_class=HTMLResponse)
async def import_inventory_pdf(
    request: Request,
    pdf_file: UploadFile = File(...),
    device_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Parse a purchase order PDF and show preview for confirmation."""
    import fitz  # PyMuPDF

    if not pdf_file.filename.lower().endswith(".pdf"):
        return RedirectResponse(url="/admin/inventory?error=Only+PDF+files+accepted", status_code=303)

    # Read PDF
    pdf_bytes = await pdf_file.read()

    # Save PDF
    os.makedirs(os.path.join(settings.UPLOAD_DIR, "inventory"), exist_ok=True)
    safe_name = pdf_file.filename.replace(" ", "_")
    pdf_path = os.path.join(settings.UPLOAD_DIR, "inventory", safe_name)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    # Extract text from PDF
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"
        doc.close()
    except Exception as e:
        logger.error(f"PDF parse error: {e}")
        return RedirectResponse(
            url="/admin/inventory?error=Error+parsing+PDF",
            status_code=303,
        )

    # Get existing products for matching
    products_result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = products_result.scalars().all()

    # Try to match products in the PDF text
    matched_items = []
    for product in products:
        # Search for product name in text (case-insensitive)
        pattern = re.escape(product.name)
        matches = re.finditer(pattern, full_text, re.IGNORECASE)

        for match in matches:
            # Look for quantities near the match (within 200 chars after)
            context = full_text[match.start():match.start() + 200]

            # Try to extract quantity (number followed by optional unit)
            qty_patterns = [
                r'(\d+)\s*(?:x|pcs|cans|units|tins)',
                r'qty[:\s]*(\d+)',
                r'quantity[:\s]*(\d+)',
                r'(\d+)\s*(?:L|lt|liter|litre)',
                r'(\d+)',
            ]
            quantity = 0
            for qp in qty_patterns:
                qty_match = re.search(qp, context[len(product.name):], re.IGNORECASE)
                if qty_match:
                    quantity = int(qty_match.group(1))
                    if quantity > 0 and quantity < 10000:  # Sanity check
                        break
                    quantity = 0

            # Extract lot number if present
            lot_match = re.search(
                r'(?:lot|batch|lotto)[:\s#]*([A-Z0-9\-]+)',
                context,
                re.IGNORECASE,
            )
            lot_number = lot_match.group(1) if lot_match else ""

            # Only add if we haven't already matched this product
            if not any(m["product_id"] == product.id for m in matched_items):
                matched_items.append({
                    "product_id": product.id,
                    "product_name": product.name,
                    "quantity": quantity if quantity > 0 else 1,
                    "lot_number": lot_number,
                    "context": context[:100].strip(),
                })

    # Get devices for dropdown
    devices_result = await db.execute(
        select(LockerDevice)
        .options(selectinload(LockerDevice.vessel))
        .order_by(LockerDevice.name)
    )
    devices = devices_result.scalars().all()

    return templates.TemplateResponse("admin/inventory_import_preview.html", {
        "request": request,
        "matched_items": matched_items,
        "pdf_filename": pdf_file.filename,
        "pdf_path": pdf_path,
        "full_text_preview": full_text[:2000],
        "devices": devices,
        "device_id": device_id,
        "products": products,
        "active": "inventory",
    })


@router.post("/inventory/import-confirm")
async def confirm_inventory_import(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Confirm and save PDF import items as inventory adjustments."""
    form = await request.form()

    pdf_filename = form.get("pdf_filename", "")
    device_id = form.get("device_id", "")

    idx = 0
    created = 0
    while True:
        product_id = form.get(f"item_{idx}_product_id")
        if product_id is None:
            break

        include = form.get(f"item_{idx}_include")
        if include != "on":
            idx += 1
            continue

        quantity = int(form.get(f"item_{idx}_quantity", "1"))
        lot_number = form.get(f"item_{idx}_lot_number", "")

        adjustment = InventoryAdjustment(
            device_id=device_id if device_id else None,
            product_id=product_id,
            adjustment_type="pdf_import",
            quantity_cans=quantity,
            lot_number=lot_number or None,
            source_document=pdf_filename,
            notes=f"Imported from PDF: {pdf_filename}",
            created_by="admin",
        )
        db.add(adjustment)
        created += 1
        idx += 1

    return RedirectResponse(
        url=f"/admin/inventory?imported={created}",
        status_code=303,
    )


@router.get("/inventory/analytics", response_class=HTMLResponse)
async def inventory_analytics(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Consumption analytics and predictions."""
    # Get all products
    products_result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = products_result.scalars().all()

    # Get all devices with vessel info
    devices_result = await db.execute(
        select(LockerDevice)
        .options(selectinload(LockerDevice.vessel))
        .order_by(LockerDevice.name)
    )
    devices = devices_result.scalars().all()

    # 30-day cutoff for consumption analysis
    cutoff_30d = datetime.utcnow() - timedelta(days=30)

    # Get all can tracking data
    cans_result = await db.execute(
        select(CanTracking).where(CanTracking.total_consumed_g > 0)
    )
    all_cans = cans_result.scalars().all()

    # Build product-level consumption data
    product_consumption = {}
    for can in all_cans:
        product_name = "Unknown"
        density = 1.0
        for p in products:
            if p.id == can.product_id:
                product_name = p.name
                density = p.density_g_per_ml or 1.0
                break

        if product_name not in product_consumption:
            product_consumption[product_name] = {
                "product_id": can.product_id,
                "total_consumed_g": 0.0,
                "total_consumed_liters": 0.0,
                "can_count": 0,
            }

        pc = product_consumption[product_name]
        pc["total_consumed_g"] += can.total_consumed_g
        pc["total_consumed_liters"] += (can.total_consumed_g / density) / 1000.0
        pc["can_count"] += 1

    # Sort by consumption (top consumed first)
    top_consumed = sorted(
        product_consumption.items(),
        key=lambda x: x[1]["total_consumed_liters"],
        reverse=True,
    )

    # Per-device analytics (current stock + predictions)
    device_analytics = []
    reorder_suggestions = []

    for device in devices:
        device_cans_result = await db.execute(
            select(CanTracking).where(
                and_(
                    CanTracking.device_id == device.id,
                    CanTracking.status.in_(["in_stock", "in_use"]),
                )
            )
        )
        device_cans = device_cans_result.scalars().all()

        if not device_cans:
            continue

        product_stock = {}
        for can in device_cans:
            product_name = "Unknown"
            density = 1.0
            for p in products:
                if p.id == can.product_id:
                    product_name = p.name
                    density = p.density_g_per_ml or 1.0
                    break

            if product_name not in product_stock:
                product_stock[product_name] = {
                    "product_id": can.product_id,
                    "current_liters": 0.0,
                    "total_consumed_g": 0.0,
                    "total_consumed_liters": 0.0,
                    "can_count": 0,
                    "times_used_total": 0,
                    "density": density,
                }

            ps = product_stock[product_name]
            ps["can_count"] += 1
            ps["times_used_total"] += can.times_used or 0
            ps["total_consumed_g"] += can.total_consumed_g or 0
            ps["total_consumed_liters"] += (
                (can.total_consumed_g or 0) / density
            ) / 1000.0
            if can.weight_current_g and density > 0:
                ps["current_liters"] += (can.weight_current_g / density) / 1000.0

        # Calculate daily consumption rate and predictions
        for pname, ps in product_stock.items():
            # Estimate daily rate from total consumption / days active
            if ps["total_consumed_liters"] > 0 and ps["times_used_total"] > 0:
                # Rough estimate: assume consumption happened over 30 days
                daily_rate = ps["total_consumed_liters"] / 30.0
                if daily_rate > 0 and ps["current_liters"] > 0:
                    days_remaining = ps["current_liters"] / daily_rate
                    ps["daily_rate_liters"] = round(daily_rate, 2)
                    ps["days_remaining"] = round(days_remaining, 0)

                    if days_remaining < 7:
                        reorder_suggestions.append({
                            "vessel": device.vessel.name if device.vessel else device.device_id,
                            "product": pname,
                            "days_remaining": round(days_remaining, 0),
                            "current_liters": round(ps["current_liters"], 1),
                            "daily_rate": round(daily_rate, 2),
                        })
                else:
                    ps["daily_rate_liters"] = 0
                    ps["days_remaining"] = None
            else:
                ps["daily_rate_liters"] = 0
                ps["days_remaining"] = None

        device_analytics.append({
            "device": device,
            "vessel_name": device.vessel.name if device.vessel else "Unassigned",
            "product_stock": product_stock,
        })

    return templates.TemplateResponse("admin/inventory_analytics.html", {
        "request": request,
        "top_consumed": top_consumed,
        "device_analytics": device_analytics,
        "reorder_suggestions": reorder_suggestions,
        "active": "inventory",
    })


def _check_sensor_health(health_data: dict) -> list:
    """
    Analyze sensor health data and return a list of alerts.

    Each alert: {"sensor": str, "level": "ok"|"warning"|"error", "message": str}
    """
    alerts = []
    if not health_data:
        return alerts

    for sensor_name, sensor_info in health_data.items():
        if not isinstance(sensor_info, dict):
            continue

        status = sensor_info.get("status", "unknown")
        if status == "error" or status == "disconnected":
            alerts.append({
                "sensor": sensor_name,
                "level": "error",
                "message": sensor_info.get("message", f"{sensor_name} is not responding"),
            })
        elif status == "warning":
            alerts.append({
                "sensor": sensor_name,
                "level": "warning",
                "message": sensor_info.get("message", f"{sensor_name} has a warning"),
            })
        elif status == "out_of_range":
            alerts.append({
                "sensor": sensor_name,
                "level": "warning",
                "message": sensor_info.get(
                    "message",
                    f"{sensor_name} reading out of expected range"
                ),
            })
        # "ok" status generates no alert

    return alerts


def _generate_ppg_code(name: str) -> str:
    """Generate a short PPG code from a product name.

    E.g. 'SIGMACOVER 280' → 'SC-280'
         'SIGMADUR 550' → 'SD-550'
         'SIGMAPRIME 200' → 'SP-200'
    """
    import re
    name = name.strip().upper()

    # Extract the number part
    num_match = re.search(r"(\d+)", name)
    num = num_match.group(1) if num_match else "000"

    # Common abbreviations
    abbrevs = {
        "SIGMACOVER": "SC",
        "SIGMADUR": "SD",
        "SIGMAGUARD": "SG",
        "SIGMAPRIME": "SP",
        "SIGMARINE": "SM",
        "SIGMATHERM": "ST",
        "SIGMARITE": "SR",
    }

    prefix = "SX"  # Default
    for full, short in abbrevs.items():
        if full in name:
            prefix = short
            break

    return f"{prefix}-{num}"
