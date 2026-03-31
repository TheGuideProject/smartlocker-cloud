"""PPG Admin Portal - Web routes with Jinja2 templates."""

import os
import re
import io
import json
import base64
import asyncio
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
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
from app.models.support_request import SupportRequest
from app.api.events import _aggregate_sensor_issues
from app.web.auth_web import require_admin_session

logger = logging.getLogger("smartlocker.admin")

router = APIRouter(prefix="/admin", tags=["admin-web"])
templates = Jinja2Templates(directory="app/web/templates")


# ---- Paint Color Helpers ----

PAINT_COLOR_HEX = {
    'red': '#E64040', 'dark red': '#B32626', 'brown': '#9A6133',
    'green': '#33B359', 'dark green': '#1F7A33', 'blue': '#4080D9',
    'dark blue': '#264D99', 'white': '#EBEBEF', 'black': '#2E2E33',
    'grey': '#8D9199', 'gray': '#8D9199', 'yellow': '#F2D940',
    'orange': '#F29930', 'pink': '#E67A94', 'maroon': '#8C2630',
    'copper': '#BF7A38', 'beige': '#D9C8A3', 'cream': '#EBE3C8',
    'silver': '#B8BCC6', 'aluminum': '#ADB3BD', 'aluminium': '#ADB3BD',
    # Compound marine paint colors
    'redbrown': '#B5462A', 'red brown': '#B5462A', 'reddish brown': '#B5462A',
    'light grey': '#B0B5BD', 'light gray': '#B0B5BD',
    'dark grey': '#5A5E66', 'dark gray': '#5A5E66',
    'light blue': '#6BA3D9', 'light green': '#66CC85',
    'rust': '#C45B28', 'rust red': '#C45B28', 'oxide red': '#B5462A',
    'primer red': '#C8534D', 'signal red': '#E6333F',
    'olive': '#808C3B', 'olive green': '#808C3B',
    'navy': '#1F3366', 'navy blue': '#1F3366',
    'tan': '#D9B982', 'sand': '#D9C896', 'buff': '#D9C28C',
    'turquoise': '#40B5AD', 'teal': '#2D8C8C',
    'purple': '#8040B3', 'violet': '#6A40B3',
    'ivory': '#F5F0DC', 'off-white': '#F0EDE0', 'offwhite': '#F0EDE0',
    'charcoal': '#3D4047', 'graphite': '#4A4E56',
    'bronze': '#B08040', 'gold': '#D9A830',
}


def _color_name_to_hex(name: str) -> str:
    """Resolve paint color name to hex.

    Tries: exact match → first word → substring match → default grey.
    """
    if not name:
        return '#737880'
    key = name.strip().lower()
    # Remove trailing numbers (e.g., "Redbrown 6179" → "redbrown")
    words = key.split()
    name_only = ' '.join(w for w in words if not w.isdigit())
    if not name_only:
        name_only = key

    # 1. Exact match on full key
    if key in PAINT_COLOR_HEX:
        return PAINT_COLOR_HEX[key]
    # 2. Match without numbers
    if name_only in PAINT_COLOR_HEX:
        return PAINT_COLOR_HEX[name_only]
    # 3. First word only
    first = words[0]
    if first in PAINT_COLOR_HEX:
        return PAINT_COLOR_HEX[first]
    # 4. Substring match: check if any known color is contained in the name
    for color_key, hex_val in PAINT_COLOR_HEX.items():
        if color_key in name_only or name_only in color_key:
            return hex_val
    return '#737880'


def _extract_product_colors(parsed_data: dict) -> dict:
    """Extract product->colors mapping from maintenance chart parsed_data.
    Returns: {"SIGMAPRIME 200": ["GREY 5284"], "SIGMACOVER 280": ["GREY", "WHITE"]}
    """
    if not parsed_data:
        return {}
    colors = {}
    for area in parsed_data.get('areas', []):
        for layer in area.get('layers', []):
            product = layer.get('product', '')
            color = layer.get('color', '')
            if product and color:
                colors.setdefault(product, [])
                if color not in colors[product]:
                    colors[product].append(color)
    return colors


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Admin dashboard overview."""
    # Get counts
    products_count = (await db.execute(select(func.count(Product.id)))).scalar() or 0
    recipes_count = (await db.execute(select(func.count(MixingRecipe.id)))).scalar() or 0
    devices_count = (await db.execute(select(func.count(LockerDevice.id)))).scalar() or 0
    events_count = (await db.execute(select(func.count(DeviceEvent.id)))).scalar() or 0
    companies_count = (await db.execute(select(func.count(Company.id)))).scalar() or 0

    # Recent errors and alerts (not all events - useless with many devices)
    error_types = [
        'unauthorized_removal', 'anomaly', 'sensor_error',
        'sync_error', 'weight_anomaly', 'rfid_error'
    ]
    recent_errors_result = await db.execute(
        select(DeviceEvent)
        .where(DeviceEvent.event_type.in_(error_types))
        .order_by(DeviceEvent.received_at.desc())
        .limit(15)
    )
    recent_errors = recent_errors_result.scalars().all()

    # Offline devices (no heartbeat in last 5 minutes)
    offline_threshold = datetime.utcnow() - timedelta(minutes=5)
    offline_devices_result = await db.execute(
        select(LockerDevice)
        .options(selectinload(LockerDevice.vessel))
        .where(
            LockerDevice.last_heartbeat != None,
            LockerDevice.last_heartbeat < offline_threshold
        )
    )
    offline_devices = offline_devices_result.scalars().all()

    # Support request count (safe if table doesn't exist yet)
    try:
        support_result = await db.execute(
            select(func.count(SupportRequest.id)).where(SupportRequest.status.in_(["open", "in_progress"]))
        )
        open_support_count = support_result.scalar() or 0
    except Exception:
        open_support_count = 0

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": user,
        "products_count": products_count,
        "recipes_count": recipes_count,
        "devices_count": devices_count,
        "events_count": events_count,
        "companies_count": companies_count,
        "recent_errors": recent_errors,
        "offline_devices": offline_devices,
        "open_support_count": open_support_count,
    })


@router.get("/products", response_class=HTMLResponse)
async def admin_products(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Product catalog management."""
    result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = result.scalars().all()

    # Build product_colors: prefer product.colors_json (DB), fallback to chart colors
    charts_result = await db.execute(select(MaintenanceChart))
    charts = charts_result.scalars().all()
    product_colors = {}  # {product_name: [{"name": "GREY 5284", "hex": "#8D9199"}, ...]}
    # Fallback: maintenance chart colors
    for chart in charts:
        chart_colors = _extract_product_colors(chart.parsed_data)
        for pname, color_names in chart_colors.items():
            product_colors.setdefault(pname, [])
            for cn in color_names:
                if not any(c['name'] == cn for c in product_colors[pname]):
                    product_colors[pname].append({
                        'name': cn,
                        'hex': _color_name_to_hex(cn),
                    })
    # Override: product-level DB colors take priority
    for p in products:
        if p.colors_json:
            product_colors[p.name] = p.colors_json

    return templates.TemplateResponse("admin/products.html", {
        "request": request,
        "user": user,
        "products": products,
        "product_colors": product_colors,
    })


@router.post("/products/add")
async def admin_add_product(
    request: Request,
    user = Depends(require_admin_session),
    ppg_code: str = Form(...),
    name: str = Form(...),
    product_type: str = Form(...),
    density_g_per_ml: float = Form(1.0),
    pot_life_minutes: str = Form(""),
    hazard_class: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Add a new product via form."""
    # Parse optional int fields (HTML sends "" for empty fields)
    pot_life_int = None
    if pot_life_minutes and pot_life_minutes.strip().isdigit():
        pot_life_int = int(pot_life_minutes.strip())

    # Parse color data from dynamic form fields
    form_data = await request.form()
    colors = []
    i = 0
    while f"color_hex_{i}" in form_data:
        hex_val = form_data.get(f"color_hex_{i}", "").strip()
        name_val = form_data.get(f"color_name_{i}", "").strip()
        if hex_val:
            colors.append({"name": name_val or hex_val, "hex": hex_val})
        i += 1

    product = Product(
        ppg_code=ppg_code,
        name=name,
        product_type=product_type,
        density_g_per_ml=density_g_per_ml,
        pot_life_minutes=pot_life_int,
        hazard_class=hazard_class or None,
        colors_json=colors if colors else None,
    )
    db.add(product)
    await db.flush()
    from app.services.command_service import create_product_sync_command
    await create_product_sync_command(db)
    return RedirectResponse(url="/admin/products", status_code=303)


@router.get("/recipes", response_class=HTMLResponse)
async def admin_recipes(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
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
        "user": user,
        "recipes": recipes,
        "products": products,
    })


@router.post("/recipes/add")
async def admin_add_recipe(
    request: Request,
    user = Depends(require_admin_session),
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
    await db.flush()
    from app.services.command_service import create_recipe_sync_command
    await create_recipe_sync_command(db)
    return RedirectResponse(url="/admin/recipes", status_code=303)


@router.get("/events", response_class=HTMLResponse)
async def admin_events(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Event log viewer."""
    result = await db.execute(
        select(DeviceEvent).order_by(DeviceEvent.received_at.desc()).limit(100)
    )
    events = result.scalars().all()
    return templates.TemplateResponse("admin/events.html", {
        "request": request,
        "user": user,
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
async def admin_devices(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Device monitoring dashboard."""
    result = await db.execute(
        select(LockerDevice)
        .options(
            selectinload(LockerDevice.vessel),
            selectinload(LockerDevice.support_requests),
        )
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

        # Count open support requests (already eagerly loaded)
        open_support = [sr for sr in d.support_requests if sr.status in ('open', 'in_progress')] if d.support_requests else []

        # Set as attribute on model so template can access d.open_support_count
        d.open_support_count = len(open_support)

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
            'open_support_count': len(open_support),
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
        "user": user,
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
    user = Depends(require_admin_session),
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
async def admin_fleet(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
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
        "user": user,
        "companies": companies,
    })


@router.post("/companies/add")
async def admin_add_company(
    request: Request,
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
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
async def admin_pairing(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
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
        "user": user,
        "codes": codes,
        "vessels": vessels,
    })


@router.post("/pairing/generate")
async def admin_generate_pairing_code(
    request: Request,
    user = Depends(require_admin_session),
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
async def admin_charts(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
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
        "user": user,
        "charts": charts,
        "vessels": vessels,
        "active": "charts",
    })


@router.post("/charts/upload", response_class=HTMLResponse)
async def admin_upload_chart(
    request: Request,
    user = Depends(require_admin_session),
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
            "user": user,
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
            "user": user,
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
        "user": user,
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
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """View a saved chart with its data."""
    chart = await db.get(MaintenanceChart, chart_id)
    if not chart:
        return RedirectResponse(url="/admin/charts", status_code=303)

    return templates.TemplateResponse("admin/chart_detail.html", {
        "request": request,
        "user": user,
        "chart": chart,
        "active": "charts",
    })


@router.post("/devices/{device_id}/change-password")
async def admin_change_device_password(
    device_id: str,
    request: Request,
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """Inventory overview: Company -> Vessel navigation."""
    # Get companies with fleets -> vessels -> devices (eager load)
    companies_result = await db.execute(
        select(Company).options(
            selectinload(Company.fleets)
            .selectinload(Fleet.vessels)
            .selectinload(Vessel.devices)
        ).order_by(Company.name)
    )
    companies_raw = companies_result.scalars().all()

    # Get all products for counting
    products_result = await db.execute(
        select(Product).where(Product.is_active == True)
    )
    products = products_result.scalars().all()
    products_by_id = {p.id: p for p in products}

    # Get all can tracking data for summary stats
    cans_result = await db.execute(
        select(CanTracking).where(
            CanTracking.status.in_(["in_stock", "in_use"])
        )
    )
    all_cans = cans_result.scalars().all()

    # Build cans-by-device lookup
    cans_by_device = {}
    for can in all_cans:
        cans_by_device.setdefault(can.device_id, []).append(can)

    # Get adjustments for product counting per vessel
    adjustments_result = await db.execute(
        select(InventoryAdjustment)
    )
    all_adjustments = adjustments_result.scalars().all()
    adjustments_by_device = {}
    for adj in all_adjustments:
        adjustments_by_device.setdefault(adj.device_id, []).append(adj)

    # Build company data with vessel summaries
    total_vessels = 0
    total_liters = 0.0
    total_products_set = set()
    alerts = 0

    company_list = []
    for company in companies_raw:
        vessel_list = []
        for fleet in company.fleets:
            for vessel in fleet.vessels:
                total_vessels += 1
                device_ids = [d.id for d in vessel.devices]

                # Count liters for this vessel
                vessel_liters = 0.0
                vessel_products = set()
                vessel_low_stock = False

                for did in device_ids:
                    device_cans = cans_by_device.get(did, [])
                    for can in device_cans:
                        if can.product_id:
                            vessel_products.add(can.product_id)
                            p = products_by_id.get(can.product_id)
                            density = p.density_g_per_ml if p else 1.0
                            if can.weight_current_g and density > 0:
                                vessel_liters += (can.weight_current_g / density) / 1000.0
                            if can.weight_full_g and can.weight_current_g:
                                used_pct = ((can.weight_full_g - can.weight_current_g) / can.weight_full_g) * 100
                                if used_pct > 80:
                                    vessel_low_stock = True

                    # Also count products from adjustments
                    device_adjs = adjustments_by_device.get(did, [])
                    for adj in device_adjs:
                        if adj.product_id:
                            vessel_products.add(adj.product_id)
                        if adj.quantity_liters:
                            vessel_liters += adj.quantity_liters

                total_liters += vessel_liters
                total_products_set.update(vessel_products)
                if vessel_low_stock:
                    alerts += 1

                vessel_list.append({
                    "id": vessel.id,
                    "name": vessel.name,
                    "imo_number": vessel.imo_number,
                    "device_count": len(vessel.devices),
                    "product_count": len(vessel_products),
                    "liters": round(vessel_liters, 1),
                    "low_stock": vessel_low_stock,
                })

        if vessel_list:
            company_list.append({
                "name": company.name,
                "vessels": vessel_list,
            })

    return templates.TemplateResponse("admin/inventory.html", {
        "request": request,
        "user": user,
        "companies": company_list,
        "total_vessels": total_vessels,
        "total_liters": round(total_liters, 1),
        "total_products": len(total_products_set),
        "alerts": alerts,
    })


@router.get("/inventory/analytics", response_class=HTMLResponse)
async def inventory_analytics(
    request: Request,
    user = Depends(require_admin_session),
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
        "user": user,
        "top_consumed": top_consumed,
        "device_analytics": device_analytics,
        "reorder_suggestions": reorder_suggestions,
        "active": "inventory",
    })


@router.get("/inventory/{vessel_id}", response_class=HTMLResponse)
async def admin_inventory_vessel(
    vessel_id: str,
    request: Request,
    user = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """Detailed inventory for a specific vessel."""
    # Get vessel with fleet/company info
    vessel_result = await db.execute(
        select(Vessel)
        .options(selectinload(Vessel.fleet).selectinload(Fleet.company))
        .where(Vessel.id == vessel_id)
    )
    vessel = vessel_result.scalar_one_or_none()
    if not vessel:
        return RedirectResponse(url="/admin/inventory", status_code=303)

    # Get all devices for this vessel
    devices_result = await db.execute(
        select(LockerDevice).where(LockerDevice.vessel_id == vessel_id)
    )
    devices = devices_result.scalars().all()
    device_ids = [d.id for d in devices]

    # Get all products
    products_result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    all_products = products_result.scalars().all()
    products_by_id = {p.id: p for p in all_products}

    # Get can tracking for these devices
    cans = []
    if device_ids:
        cans_result = await db.execute(
            select(CanTracking).where(
                CanTracking.device_id.in_(device_ids),
                CanTracking.status.in_(["in_stock", "in_use"]),
            )
        )
        cans = cans_result.scalars().all()

    # Get maintenance chart for this vessel (for colors)
    chart_result = await db.execute(
        select(MaintenanceChart).where(MaintenanceChart.vessel_id == vessel_id)
    )
    charts = chart_result.scalars().all()

    # Extract product colors: prefer product.colors_json, fallback to chart colors
    vessel_product_colors = {}
    for chart in charts:
        chart_colors = _extract_product_colors(chart.parsed_data)
        for pname, color_names in chart_colors.items():
            vessel_product_colors.setdefault(pname, [])
            for cn in color_names:
                if not any(c['name'] == cn for c in vessel_product_colors[pname]):
                    vessel_product_colors[pname].append({
                        'name': cn,
                        'hex': _color_name_to_hex(cn),
                    })
    # Override: product-level DB colors take priority
    for p in all_products:
        if p.colors_json:
            vessel_product_colors[p.name] = p.colors_json

    # Build product name→id lookup
    product_name_to_id = {p.name: p.id for p in all_products}

    # Build product inventory summary
    product_summary = {}
    for can in cans:
        pname = "Unknown Product"
        density = 1.0
        product_type = "base_paint"
        hardener_name = None

        if can.product_id and can.product_id in products_by_id:
            p = products_by_id[can.product_id]
            pname = p.name
            density = p.density_g_per_ml or 1.0
            product_type = p.product_type

        if pname not in product_summary:
            product_summary[pname] = {
                "name": pname,
                "product_id": product_name_to_id.get(pname, ""),
                "product_type": product_type,
                "product_type_label": product_type.replace("_", " ").title(),
                "liters": 0.0,
                "full_liters": 0.0,
                "low_stock": False,
                "colors": vessel_product_colors.get(pname, []),
                "hardener_name": hardener_name,
                "is_hardener_pair": False,
            }

        ps = product_summary[pname]
        if can.weight_current_g and density > 0:
            ps["liters"] += (can.weight_current_g / density) / 1000.0
        if can.weight_full_g and density > 0:
            ps["full_liters"] += (can.weight_full_g / density) / 1000.0

    # Calculate low stock flags
    low_stock_count = 0
    for pname, ps in product_summary.items():
        ps["liters"] = round(ps["liters"], 1)
        if ps["full_liters"] > 0:
            used_pct = ((ps["full_liters"] - ps["liters"]) / ps["full_liters"]) * 100
            if used_pct > 80:
                ps["low_stock"] = True
                low_stock_count += 1

    # Also include products from adjustments (manual adds) that may not have cans yet
    if device_ids:
        adj_result = await db.execute(
            select(InventoryAdjustment).where(
                InventoryAdjustment.device_id.in_(device_ids),
                InventoryAdjustment.adjustment_type.in_(["manual_add", "pdf_import"]),
            )
        )
        vessel_adjustments_for_products = adj_result.scalars().all()
        for adj in vessel_adjustments_for_products:
            if adj.product_id and adj.product_id in products_by_id:
                p = products_by_id[adj.product_id]
                adj_liters = adj.quantity_liters or 0
                if p.name in product_summary:
                    # Add liters to existing product entry
                    product_summary[p.name]["liters"] += adj_liters
                else:
                    product_summary[p.name] = {
                        "name": p.name,
                        "product_id": p.id,
                        "product_type": p.product_type,
                        "product_type_label": p.product_type.replace("_", " ").title(),
                        "liters": round(adj_liters, 1),
                        "full_liters": 0.0,
                        "low_stock": False,
                        "colors": vessel_product_colors.get(p.name, []),
                        "hardener_name": None,
                        "is_hardener_pair": False,
                    }
        # Also handle manual_remove adjustments
        adj_remove_result = await db.execute(
            select(InventoryAdjustment).where(
                InventoryAdjustment.device_id.in_(device_ids),
                InventoryAdjustment.adjustment_type == "manual_remove",
            )
        )
        for adj in adj_remove_result.scalars().all():
            if adj.product_id and adj.product_id in products_by_id:
                p = products_by_id[adj.product_id]
                if p.name in product_summary:
                    product_summary[p.name]["liters"] -= (adj.quantity_liters or 0)
                    if product_summary[p.name]["liters"] < 0:
                        product_summary[p.name]["liters"] = 0

    products_list = sorted(product_summary.values(), key=lambda x: x["name"])

    # Get recent adjustments for this vessel
    adjustments = []
    if device_ids:
        adj_result = await db.execute(
            select(InventoryAdjustment)
            .where(InventoryAdjustment.device_id.in_(device_ids))
            .order_by(InventoryAdjustment.created_at.desc())
            .limit(20)
        )
        adjustments_raw = adj_result.scalars().all()
        for adj in adjustments_raw:
            p = products_by_id.get(adj.product_id)
            adjustments.append({
                "id": adj.id,
                "created_at": adj.created_at,
                "adjustment_type": adj.adjustment_type,
                "product_name": p.name if p else adj.product_id[:8],
                "quantity_cans": adj.quantity_cans,
                "quantity_liters": adj.quantity_liters,
                "source_document": adj.source_document,
                "notes": adj.notes,
            })

    total_liters = round(sum(ps["liters"] for ps in products_list), 1)

    return templates.TemplateResponse("admin/inventory_vessel.html", {
        "request": request,
        "user": user,
        "vessel": vessel,
        "total_liters": total_liters,
        "product_count": len(products_list),
        "low_stock_count": low_stock_count,
        "products": products_list,
        "adjustments": adjustments,
        "all_products": all_products,
    })


@router.post("/inventory/{vessel_id}/adjust")
async def admin_adjust_vessel_inventory(
    vessel_id: str,
    request: Request,
    user = Depends(require_admin_session),
    product_id: str = Form(...),
    adjustment_type: str = Form(...),
    quantity_liters: float = Form(0.0),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create a manual inventory adjustment for a specific vessel (liters only)."""
    # Get a device for this vessel to attach the adjustment
    device_result = await db.execute(
        select(LockerDevice).where(LockerDevice.vessel_id == vessel_id).limit(1)
    )
    device = device_result.scalar_one_or_none()
    device_id = device.id if device else None

    # Calculate weight from liters
    weight_g = 0.0
    if quantity_liters > 0:
        product_result = await db.execute(
            select(Product).where(Product.id == product_id)
        )
        product = product_result.scalar_one_or_none()
        if product:
            weight_g = quantity_liters * 1000 * (product.density_g_per_ml or 1.0)

    adjustment = InventoryAdjustment(
        device_id=device_id,
        product_id=product_id,
        adjustment_type=adjustment_type,
        quantity_cans=0,
        quantity_liters=quantity_liters,
        weight_g=weight_g,
        notes=notes or None,
        created_by="admin",
    )
    db.add(adjustment)
    return RedirectResponse(url=f"/admin/inventory/{vessel_id}", status_code=303)


@router.post("/inventory/{vessel_id}/import-pdf")
async def admin_import_vessel_pdf(
    vessel_id: str,
    request: Request,
    user = Depends(require_admin_session),
    pdf_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import a purchase order PDF for a specific vessel."""
    import fitz  # PyMuPDF

    if not pdf_file.filename.lower().endswith(".pdf"):
        return RedirectResponse(
            url=f"/admin/inventory/{vessel_id}?error=Only+PDF+files+accepted",
            status_code=303,
        )

    # Get a device for this vessel
    device_result = await db.execute(
        select(LockerDevice).where(LockerDevice.vessel_id == vessel_id).limit(1)
    )
    device = device_result.scalar_one_or_none()
    device_id = device.id if device else None

    # Read and save PDF
    pdf_bytes = await pdf_file.read()
    os.makedirs(os.path.join(settings.UPLOAD_DIR, "inventory"), exist_ok=True)
    safe_name = pdf_file.filename.replace(" ", "_")
    pdf_path = os.path.join(settings.UPLOAD_DIR, "inventory", safe_name)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    # Extract text
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n"
        doc.close()
    except Exception as e:
        logger.error(f"PDF parse error: {e}")
        return RedirectResponse(
            url=f"/admin/inventory/{vessel_id}?error=Error+parsing+PDF",
            status_code=303,
        )

    # Get products for matching
    products_result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = products_result.scalars().all()

    # Match products and create adjustments
    created = 0
    matched_product_ids = set()
    for product in products:
        pattern = re.escape(product.name)
        matches = list(re.finditer(pattern, full_text, re.IGNORECASE))
        if not matches or product.id in matched_product_ids:
            continue

        matched_product_ids.add(product.id)
        match = matches[0]
        context = full_text[match.start():match.start() + 200]

        # Extract quantity
        quantity = 1
        qty_patterns = [
            r'(\d+)\s*(?:x|pcs|cans|units|tins)',
            r'qty[:\s]*(\d+)',
            r'quantity[:\s]*(\d+)',
            r'(\d+)\s*(?:L|lt|liter|litre)',
        ]
        for qp in qty_patterns:
            qty_match = re.search(qp, context[len(product.name):], re.IGNORECASE)
            if qty_match:
                q = int(qty_match.group(1))
                if 0 < q < 10000:
                    quantity = q
                    break

        adjustment = InventoryAdjustment(
            device_id=device_id,
            product_id=product.id,
            adjustment_type="pdf_import",
            quantity_cans=quantity,
            source_document=pdf_file.filename,
            notes=f"Imported from PDF: {pdf_file.filename}",
            created_by="admin",
        )
        db.add(adjustment)
        created += 1

    return RedirectResponse(
        url=f"/admin/inventory/{vessel_id}?imported={created}",
        status_code=303,
    )


@router.post("/inventory/adjust")
async def admin_adjust_inventory(
    request: Request,
    user = Depends(require_admin_session),
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
    user = Depends(require_admin_session),
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
        "user": user,
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
    user = Depends(require_admin_session),
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


# ---- Error Code Guide ----

@router.get("/error-codes")
async def admin_error_codes(request: Request, user = Depends(require_admin_session)):
    """Error code reference guide."""
    # Define all error codes (matching edge error_codes.py)
    error_codes = [
        # Sensor Errors
        {"code": "E001", "title": "RFID Reader Disconnected", "severity": "critical", "category": "Sensor",
         "description": "RFID reader is not responding to commands. Cannot detect paint cans.",
         "resolution": "1. Check USB connection to RFID reader\n2. Try unplugging and reconnecting\n3. Restart the SmartLocker device\n4. If persistent, contact PPG support"},
        {"code": "E002", "title": "RFID Read Error", "severity": "warning", "category": "Sensor",
         "description": "RFID reader experiencing intermittent read failures.",
         "resolution": "1. Clean the RFID reader surface\n2. Check antenna connection\n3. Ensure no metal interference near reader"},
        {"code": "E003", "title": "Multiple RFID Tags Detected", "severity": "warning", "category": "Sensor",
         "description": "Multiple RFID tags detected on a single slot.",
         "resolution": "1. Remove extra paint cans from the slot\n2. Ensure only one can per slot"},
        {"code": "E004", "title": "Weight Sensor Disconnected", "severity": "critical", "category": "Sensor",
         "description": "Weight sensor (HX711/Arduino) not responding. Cannot measure paint quantities.",
         "resolution": "1. Check serial/USB connection to Arduino\n2. Verify Arduino power LED is on\n3. Restart device\n4. If persistent, check HX711 wiring"},
        {"code": "E005", "title": "Weight Out of Range", "severity": "warning", "category": "Sensor",
         "description": "Weight reading is outside the valid measurement range.",
         "resolution": "1. Recalibrate weight sensor\n2. Check load cell connections\n3. Ensure shelf is level"},
        {"code": "E006", "title": "Weight Drift", "severity": "warning", "category": "Sensor",
         "description": "Weight readings are drifting over time without physical changes.",
         "resolution": "1. Recalibrate the sensor\n2. Check for temperature effects\n3. Inspect load cell for damage"},
        {"code": "E007", "title": "Weight Overload", "severity": "warning", "category": "Sensor",
         "description": "Weight exceeds the maximum capacity of the shelf.",
         "resolution": "1. Remove excess weight from shelf\n2. Check maximum capacity rating"},
        {"code": "E008", "title": "LED Driver Error", "severity": "warning", "category": "Sensor",
         "description": "LED strip communication failure. Guide lights not working.",
         "resolution": "1. Check LED strip wiring\n2. Verify data pin connection\n3. Check power supply"},
        {"code": "E009", "title": "Buzzer Error", "severity": "info", "category": "Sensor",
         "description": "Buzzer not responding. Audio alerts disabled.",
         "resolution": "1. Check buzzer connection\n2. Verify GPIO pin assignment"},
        {"code": "E010", "title": "Sensor Init Failed", "severity": "critical", "category": "Sensor",
         "description": "One or more sensors failed to initialize at boot.",
         "resolution": "1. Restart the device\n2. Check all sensor connections\n3. Review device logs for specific failure"},
        # System Errors
        {"code": "E020", "title": "CPU Over Temperature", "severity": "critical", "category": "System",
         "description": "CPU temperature above 80C. Risk of thermal shutdown.",
         "resolution": "1. Check cooling fan is running\n2. Improve ventilation around device\n3. Move device away from heat sources\n4. Consider adding heatsink"},
        {"code": "E021", "title": "CPU High Temperature", "severity": "warning", "category": "System",
         "description": "CPU temperature above 70C. Performance may be affected.",
         "resolution": "1. Improve ventilation\n2. Check fan operation\n3. Clean dust from vents"},
        {"code": "E022", "title": "CPU Throttling", "severity": "warning", "category": "System",
         "description": "CPU frequency is being reduced due to thermal or power constraints.",
         "resolution": "1. Improve cooling\n2. Check power supply voltage\n3. Reduce ambient temperature"},
        {"code": "E023", "title": "RAM Critical", "severity": "critical", "category": "System",
         "description": "RAM usage above 90%. System may become unresponsive.",
         "resolution": "1. Restart the device immediately\n2. Check for memory leaks in logs\n3. Contact support if recurring"},
        {"code": "E024", "title": "RAM High", "severity": "warning", "category": "System",
         "description": "RAM usage above 80%. Monitor for further increase.",
         "resolution": "1. Monitor usage trend\n2. Consider restarting during next maintenance window"},
        {"code": "E025", "title": "Disk Full", "severity": "critical", "category": "System",
         "description": "SD card storage above 95%. Device cannot save data.",
         "resolution": "1. Contact support for remote log cleanup\n2. Clear old event logs\n3. Consider larger SD card"},
        {"code": "E026", "title": "Disk High Usage", "severity": "warning", "category": "System",
         "description": "SD card storage above 85%.",
         "resolution": "1. Schedule maintenance for data cleanup\n2. Enable auto-cleanup of old logs"},
        {"code": "E027", "title": "SD Card Error", "severity": "critical", "category": "System",
         "description": "SD card I/O errors detected. Risk of data loss.",
         "resolution": "1. URGENT: Replace SD card as soon as possible\n2. Back up data if accessible\n3. Contact PPG support"},
        {"code": "E028", "title": "SD Card Read-Only", "severity": "critical", "category": "System",
         "description": "SD card mounted as read-only. Cannot save any data.",
         "resolution": "1. Replace SD card immediately\n2. Power cycle the device\n3. Contact PPG support"},
        {"code": "E029", "title": "System Clock Error", "severity": "warning", "category": "System",
         "description": "System clock not synchronized. Timestamps may be inaccurate.",
         "resolution": "1. Check network connectivity for NTP sync\n2. Verify NTP server configuration"},
        {"code": "E030", "title": "Power Unstable", "severity": "warning", "category": "System",
         "description": "Voltage fluctuations detected on power supply.",
         "resolution": "1. Check power supply connection\n2. Use a regulated power supply\n3. Check for loose connections"},
        # Software Errors
        {"code": "E040", "title": "Database Error", "severity": "critical", "category": "Software",
         "description": "SQLite database corrupted or locked. Cannot save events.",
         "resolution": "1. Restart the device\n2. If persistent, database may need recovery\n3. Contact PPG support"},
        {"code": "E041", "title": "Database Full", "severity": "warning", "category": "Software",
         "description": "Database file has grown too large.",
         "resolution": "1. Trigger cloud sync to clear synced events\n2. Enable auto-cleanup\n3. Contact support"},
        {"code": "E042", "title": "Cloud Sync Failed", "severity": "warning", "category": "Software",
         "description": "Cloud synchronization failed repeatedly.",
         "resolution": "1. Check network/WiFi connection\n2. Verify cloud URL is accessible\n3. Check API key validity"},
        {"code": "E043", "title": "Sync Queue Full", "severity": "warning", "category": "Software",
         "description": "Too many events waiting to sync. Local storage filling up.",
         "resolution": "1. Check network connectivity\n2. Force sync from settings\n3. Contact support if network is OK"},
        {"code": "E044", "title": "OTA Update Failed", "severity": "warning", "category": "Software",
         "description": "Firmware update failed to install.",
         "resolution": "1. Retry update from cloud dashboard\n2. Check device has internet access\n3. Contact PPG support"},
        {"code": "E045", "title": "Config Corrupt", "severity": "critical", "category": "Software",
         "description": "Configuration file is corrupted.",
         "resolution": "1. Restart the device (auto-recovery)\n2. Re-pair device if needed\n3. Contact PPG support"},
        {"code": "E046", "title": "Memory Leak", "severity": "warning", "category": "Software",
         "description": "Process memory growing abnormally over time.",
         "resolution": "1. Restart the device\n2. Report to PPG support with timing details"},
        {"code": "E047", "title": "Watchdog Timeout", "severity": "critical", "category": "Software",
         "description": "Main process not responding. Auto-restart triggered.",
         "resolution": "1. Device should auto-restart\n2. If recurring, check system logs\n3. Contact PPG support"},
        # Inventory Errors
        {"code": "E060", "title": "Unauthorized Removal", "severity": "critical", "category": "Inventory",
         "description": "Paint can removed from locker without an active mixing session.",
         "resolution": "1. Investigate immediately - potential unauthorized access\n2. Check security camera footage\n3. Return can to locker\n4. Report incident to supervisor"},
        {"code": "E061", "title": "Wrong Slot Return", "severity": "warning", "category": "Inventory",
         "description": "Paint can returned to incorrect slot position.",
         "resolution": "1. Follow LED guide to correct slot\n2. Place can in the illuminated slot"},
        {"code": "E062", "title": "Missing Can", "severity": "warning", "category": "Inventory",
         "description": "Paint can not returned within the expected timeframe.",
         "resolution": "1. Locate the missing can\n2. Return it to the SmartLocker\n3. If consumed, log in system"},
        {"code": "E063", "title": "Unknown RFID Tag", "severity": "info", "category": "Inventory",
         "description": "Unregistered RFID tag detected on slot.",
         "resolution": "1. Register the tag in the system\n2. Assign it to the correct product"},
        {"code": "E064", "title": "Weight Mismatch", "severity": "warning", "category": "Inventory",
         "description": "Can weight doesn't match expected value for this product.",
         "resolution": "1. Verify can contents\n2. Check if correct product was placed\n3. Recalibrate if needed"},
        {"code": "E065", "title": "Stock Critical", "severity": "critical", "category": "Inventory",
         "description": "Product stock is critically low (below 10%).",
         "resolution": "1. Order replacement immediately\n2. Check pending purchase orders\n3. Notify supply chain"},
        {"code": "E066", "title": "Stock Low", "severity": "warning", "category": "Inventory",
         "description": "Product stock below reorder threshold (below 25%).",
         "resolution": "1. Plan reorder within next supply window\n2. Check consumption rate\n3. Adjust reorder points if needed"},
        # Mixing Errors
        {"code": "E080", "title": "Mix Out of Spec", "severity": "warning", "category": "Mixing",
         "description": "Mixing ratio is outside the specified tolerance range.",
         "resolution": "1. Adjust by adding more base or hardener\n2. Accept with override reason if within acceptable range\n3. Discard if too far out of spec"},
        {"code": "E081", "title": "Pot Life Expired", "severity": "critical", "category": "Mixing",
         "description": "Mixed paint has exceeded its pot life. DO NOT USE.",
         "resolution": "1. DO NOT USE this mixed paint\n2. Dispose of properly following MSDS guidelines\n3. Prepare a fresh batch"},
        {"code": "E082", "title": "Pot Life Warning", "severity": "info", "category": "Mixing",
         "description": "Mixed paint approaching pot life expiry (75%+ elapsed).",
         "resolution": "1. Use the mixed paint soon\n2. Plan application timing\n3. Do not mix more than needed"},
        {"code": "E083", "title": "Mix Aborted", "severity": "info", "category": "Mixing",
         "description": "Mixing session was manually aborted.",
         "resolution": "1. Review reason for abort\n2. Return cans to locker\n3. Start new session if needed"},
    ]

    # Group by category
    categories = {}
    for ec in error_codes:
        cat = ec["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(ec)

    return templates.TemplateResponse("admin/error_codes.html", {
        "request": request,
        "user": user,
        "error_codes": error_codes,
        "categories": categories,
        "total_codes": len(error_codes),
    })


# ---- Support Requests ----

@router.get("/support")
async def admin_support_requests(request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Support request management page."""
    # Get all support requests with device info
    result = await db.execute(
        select(SupportRequest)
        .options(selectinload(SupportRequest.device))
        .order_by(SupportRequest.created_at.desc())
        .limit(100)
    )
    requests_list = result.scalars().all()

    # Get stats
    total_result = await db.execute(select(func.count(SupportRequest.id)))
    total = total_result.scalar() or 0

    open_result = await db.execute(
        select(func.count(SupportRequest.id)).where(SupportRequest.status.in_(["open", "in_progress"]))
    )
    open_count = open_result.scalar() or 0

    return templates.TemplateResponse("admin/support_requests.html", {
        "request": request,
        "user": user,
        "support_requests": requests_list,
        "stats": {"total": total, "open": open_count, "resolved": total - open_count},
    })


@router.post("/support/{request_id}/resolve")
async def admin_resolve_support(request_id: int, request: Request, user = Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Resolve a support request."""
    result = await db.execute(select(SupportRequest).where(SupportRequest.id == request_id))
    sr = result.scalar_one_or_none()
    if not sr:
        return RedirectResponse("/admin/support?error=not_found", status_code=303)

    form = await request.form()
    sr.status = "resolved"
    sr.resolution_notes = form.get("resolution_notes", "")
    sr.resolved_by = "admin"
    sr.resolved_at = datetime.now(timezone.utc)
    await db.commit()

    return RedirectResponse("/admin/support?resolved=1", status_code=303)


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


# ---- Device Pending Items ----

@router.get("/devices/{device_id}/pending", response_class=HTMLResponse)
async def admin_device_pending(
    request: Request,
    device_id: str,
    user=Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """Show all pending/queued items for a specific device."""
    from app.models.command import DeviceCommand
    from app.models.mixing import MixingSessionCloud

    # Get device
    device = await db.get(LockerDevice, device_id)
    if not device:
        return RedirectResponse(url="/admin/devices", status_code=303)

    # 1) Pending commands (pending or delivered but not acked)
    cmd_result = await db.execute(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.status.in_(["pending", "delivered"]),
        )
        .order_by(desc(DeviceCommand.created_at))
        .limit(100)
    )
    pending_commands = cmd_result.scalars().all()

    # 2) Recent events (last 50 received)
    evt_result = await db.execute(
        select(DeviceEvent)
        .where(DeviceEvent.device_id == device_id)
        .order_by(desc(DeviceEvent.received_at))
        .limit(50)
    )
    recent_events = evt_result.scalars().all()

    # 3) Mixing sessions — especially in_progress ones
    mix_result = await db.execute(
        select(MixingSessionCloud)
        .where(MixingSessionCloud.device_id == device_id)
        .order_by(desc(MixingSessionCloud.started_at))
        .limit(50)
    )
    mixing_sessions = mix_result.scalars().all()

    # 4) Recently completed commands (for reference)
    done_cmd_result = await db.execute(
        select(DeviceCommand)
        .where(
            DeviceCommand.device_id == device_id,
            DeviceCommand.status.in_(["acked", "expired"]),
        )
        .order_by(desc(DeviceCommand.created_at))
        .limit(30)
    )
    completed_commands = done_cmd_result.scalars().all()

    # Edge-reported pending count from system_info
    edge_pending = None
    if device.system_info and "events_pending_sync" in (device.system_info or {}):
        edge_pending = device.system_info["events_pending_sync"]

    return templates.TemplateResponse("admin/device_pending.html", {
        "request": request,
        "user": user,
        "device": device,
        "pending_commands": pending_commands,
        "completed_commands": completed_commands,
        "recent_events": recent_events,
        "mixing_sessions": mixing_sessions,
        "edge_pending": edge_pending,
        "active": "devices",
    })


# ---- System Guide & Changelog ----

@router.get("/guide", response_class=HTMLResponse)
async def admin_guide(request: Request, user=Depends(require_admin_session)):
    """System Guide & Changelog."""
    return templates.TemplateResponse("admin/guide.html", {
        "request": request,
        "user": user,
        "active": "guide",
    })


# ---- Barcode Generator ----

@router.get("/barcode-generator", response_class=HTMLResponse)
async def admin_barcode_generator(request: Request, user=Depends(require_admin_session), db: AsyncSession = Depends(get_db)):
    """Barcode / QR-code label generator page."""
    result = await db.execute(
        select(Product).where(Product.is_active == True).order_by(Product.name)
    )
    products = result.scalars().all()

    # Build JSON-serializable product list for JavaScript autofill
    products_json = []
    for p in products:
        colors = []
        if p.colors_json and isinstance(p.colors_json, list):
            for c in p.colors_json:
                if isinstance(c, dict):
                    colors.append(c.get("name", ""))
                elif isinstance(c, str):
                    colors.append(c)
        products_json.append({
            "id": p.id,
            "ppg_code": p.ppg_code or "",
            "name": p.name or "",
            "product_type": p.product_type or "",
            "colors": colors,
        })

    return templates.TemplateResponse("admin/barcode_generator.html", {
        "request": request,
        "user": user,
        "active": "barcode",
        "products": products,
        "products_json": products_json,
    })


def _make_barcode_image(data: str, barcode_type: str) -> bytes:
    """Generate a barcode or QR code and return PNG bytes."""
    buf = io.BytesIO()
    if barcode_type == "qr":
        import qrcode
        img = qrcode.make(data, box_size=8, border=2)
        img.save(buf, format="PNG")
    else:
        import barcode as barcode_lib
        from barcode.writer import ImageWriter
        code128 = barcode_lib.get_barcode_class("code128")
        bc = code128(data, writer=ImageWriter())
        bc.write(buf, options={"module_width": 0.5, "module_height": 15, "font_size": 12, "text_distance": 5, "quiet_zone": 6.5})
    buf.seek(0)
    return buf.read()


@router.post("/barcode-generator/create", response_class=HTMLResponse)
async def admin_barcode_create(
    request: Request,
    user=Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
    ppg_code: str = Form(...),
    batch_start: str = Form(...),
    batch_end: str = Form(""),
    product_name: str = Form(...),
    color: str = Form(""),
    product_id: str = Form(""),
    barcode_type: str = Form("code128"),
):
    """Generate barcode preview(s), save to DB, and return HTML fragment."""
    from app.models.product_barcode import ProductBarcode

    ppg_code = ppg_code.strip().upper()
    product_name = product_name.strip().upper().replace(" ", "-")
    color = color.strip().upper().replace(" ", "") if color else ""
    barcode_type = barcode_type.strip().lower()
    product_id = product_id.strip() if product_id else ""

    # If no product_id provided, try to find by ppg_code
    if not product_id:
        result = await db.execute(
            select(Product).where(Product.ppg_code == ppg_code)
        )
        product = result.scalar_one_or_none()
        if product:
            product_id = product.id

    batches: list[str] = []
    batch_start = batch_start.strip()
    batch_end = batch_end.strip()

    if batch_end and batch_start.isdigit() and batch_end.isdigit():
        start_n = int(batch_start)
        end_n = int(batch_end)
        if end_n < start_n:
            start_n, end_n = end_n, start_n
        end_n = min(end_n, start_n + 49)
        width = max(len(batch_start), len(batch_end))
        batches = [str(n).zfill(width) for n in range(start_n, end_n + 1)]
    else:
        batches = [batch_start]

    previews: list[dict] = []
    saved_count = 0
    for batch in batches:
        # Short barcode format: SL-{PPG_CODE}-{BATCH} (scanner-friendly)
        data_string = f"SL-{ppg_code}-{batch}"
        png_bytes = _make_barcode_image(data_string, barcode_type)
        b64 = base64.b64encode(png_bytes).decode()
        previews.append({
            "data": data_string,
            "image_b64": b64,
            "batch": batch,
        })

        # Save barcode to database (skip duplicates)
        if product_id:
            existing = await db.execute(
                select(ProductBarcode).where(ProductBarcode.barcode_data == data_string)
            )
            if not existing.scalar_one_or_none():
                barcode_record = ProductBarcode(
                    barcode_data=data_string,
                    product_id=product_id,
                    ppg_code=ppg_code,
                    batch_number=batch,
                    product_name=product_name,
                    color=color,
                    barcode_type=barcode_type,
                    created_by=user.get("email", "admin") if isinstance(user, dict) else getattr(user, "email", "admin"),
                )
                db.add(barcode_record)
                saved_count += 1

    if saved_count > 0:
        try:
            await db.flush()
        except Exception as e:
            logger.error(f"Failed to save barcodes: {e}")

    return templates.TemplateResponse("admin/_barcode_previews.html", {
        "request": request,
        "previews": previews,
        "barcode_type": barcode_type,
        "ppg_code": ppg_code,
        "product_name": product_name,
        "color": color,
        "saved_count": saved_count,
        "has_product": bool(product_id),
    })


@router.post("/barcode-generator/pdf")
async def admin_barcode_pdf(
    request: Request,
    user=Depends(require_admin_session),
    ppg_code: str = Form(...),
    batch_start: str = Form(...),
    batch_end: str = Form(""),
    product_name: str = Form(...),
    color: str = Form(...),
    barcode_type: str = Form("code128"),
):
    """Generate a PDF with printable barcode labels (10cm x 5cm each)."""
    from reportlab.lib.pagesizes import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    ppg_code = ppg_code.strip().upper()
    product_name = product_name.strip().upper().replace(" ", "-")
    color = color.strip().upper().replace(" ", "")
    barcode_type = barcode_type.strip().lower()

    batch_start = batch_start.strip()
    batch_end = batch_end.strip()

    batches: list[str] = []
    if batch_end and batch_start.isdigit() and batch_end.isdigit():
        start_n = int(batch_start)
        end_n = int(batch_end)
        if end_n < start_n:
            start_n, end_n = end_n, start_n
        end_n = min(end_n, start_n + 49)
        width = max(len(batch_start), len(batch_end))
        batches = [str(n).zfill(width) for n in range(start_n, end_n + 1)]
    else:
        batches = [batch_start]

    label_w = 100 * mm
    label_h = 50 * mm

    pdf_buf = io.BytesIO()
    c = rl_canvas.Canvas(pdf_buf, pagesize=(label_w, label_h))

    for i, batch in enumerate(batches):
        if i > 0:
            c.showPage()

        # Short barcode format: SL-{PPG_CODE}-{BATCH}
        data_string = f"SL-{ppg_code}-{batch}"
        png_bytes = _make_barcode_image(data_string, barcode_type)
        img_reader = ImageReader(io.BytesIO(png_bytes))

        # Title
        c.setFont("Helvetica-Bold", 9)
        c.drawString(8 * mm, label_h - 8 * mm, "PPG SmartLocker Label")

        # Barcode image - centered
        img_w_pt = 60 * mm if barcode_type != "qr" else 28 * mm
        img_h_pt = 20 * mm if barcode_type != "qr" else 28 * mm
        img_x = (label_w - img_w_pt) / 2
        img_y = label_h - 12 * mm - img_h_pt
        c.drawImage(img_reader, img_x, img_y, width=img_w_pt, height=img_h_pt, preserveAspectRatio=True, mask='auto')

        # Short barcode ID below barcode
        c.setFont("Courier-Bold", 9)
        c.drawCentredString(label_w / 2, img_y - 4 * mm, data_string)

        # Info lines at bottom
        c.setFont("Helvetica", 7)
        bottom_y = 6 * mm
        c.drawString(8 * mm, bottom_y + 5 * mm, f"PPG Code: {ppg_code}   Batch: {batch}")
        c.drawString(8 * mm, bottom_y, f"Product: {product_name}   Color: {color}")

        # Border
        c.setStrokeColorRGB(0.6, 0.6, 0.6)
        c.setLineWidth(0.5)
        c.rect(2 * mm, 2 * mm, label_w - 4 * mm, label_h - 4 * mm)

    c.save()
    pdf_buf.seek(0)

    filename = f"labels_{ppg_code}_{batches[0]}.pdf" if len(batches) == 1 else f"labels_{ppg_code}_{batches[0]}-{batches[-1]}.pdf"

    return StreamingResponse(
        pdf_buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


# ---- Saved Barcodes List ----

@router.get("/barcodes", response_class=HTMLResponse)
async def admin_barcodes_list(
    request: Request,
    user=Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """List all saved barcodes with linked product info."""
    from app.models.product_barcode import ProductBarcode
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(ProductBarcode)
        .options(selectinload(ProductBarcode.product))
        .order_by(ProductBarcode.created_at.desc())
        .limit(500)
    )
    barcodes = result.scalars().all()

    # Group by product for summary
    product_summary = {}
    for bc in barcodes:
        pid = bc.product_id
        if pid not in product_summary:
            product_summary[pid] = {
                "name": bc.product_name,
                "ppg_code": bc.ppg_code,
                "count": 0,
                "total_scans": 0,
            }
        product_summary[pid]["count"] += 1
        product_summary[pid]["total_scans"] += bc.times_scanned or 0

    return templates.TemplateResponse("admin/barcodes_list.html", {
        "request": request,
        "user": user,
        "active": "barcode",
        "barcodes": barcodes,
        "product_summary": product_summary,
    })


@router.post("/barcodes/{barcode_id}/delete")
async def admin_barcode_delete(
    barcode_id: str,
    request: Request,
    user=Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved barcode."""
    from app.models.product_barcode import ProductBarcode

    result = await db.execute(
        select(ProductBarcode).where(ProductBarcode.id == barcode_id)
    )
    barcode = result.scalar_one_or_none()
    if barcode:
        await db.delete(barcode)
    return RedirectResponse(url="/admin/barcodes?success=Barcode+deleted", status_code=303)

