"""PPG Admin Portal - Web routes with Jinja2 templates."""

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.product import Product, MixingRecipe
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.company import Company

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
    base_product_id: int = Form(...),
    hardener_product_id: int = Form(...),
    ratio_base: float = Form(3.0),
    ratio_hardener: float = Form(1.0),
    tolerance_pct: float = Form(5.0),
    thinner_pct_brush: float = Form(0),
    thinner_pct_roller: float = Form(0),
    thinner_pct_spray: float = Form(5),
    recommended_thinner_id: int = Form(None),
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
    """Device management."""
    result = await db.execute(select(LockerDevice).order_by(LockerDevice.created_at.desc()))
    devices = result.scalars().all()
    return templates.TemplateResponse("admin/devices.html", {
        "request": request,
        "devices": devices,
    })
