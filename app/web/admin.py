"""PPG Admin Portal - Web routes with Jinja2 templates."""

import os
import json
import logging

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
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

    return templates.TemplateResponse("admin/devices.html", {
        "request": request,
        "devices": device_list,
        "vessels": vessels,
        "online_count": online_count,
        "total_count": len(device_list),
        "all_alerts": all_alerts,
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
