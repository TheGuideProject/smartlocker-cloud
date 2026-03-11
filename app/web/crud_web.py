"""CRUD Edit & Delete routes for SmartLocker Cloud admin portal.

Separate router to avoid conflicts with admin.py.
All routes use POST-redirect-GET pattern (status_code=303).
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.product import Product, MixingRecipe
from app.models.company import Company
from app.models.fleet import Fleet, Vessel
from app.models.device import LockerDevice
from app.models.event import DeviceEvent
from app.models.mixing import MixingSessionCloud

logger = logging.getLogger("smartlocker.crud")

router = APIRouter(prefix="/admin", tags=["crud-web"])


# =============================================================================
# PRODUCTS - Edit & Delete
# =============================================================================

@router.post("/products/{product_id}/edit")
async def product_edit(
    product_id: str,
    ppg_code: str = Form(...),
    name: str = Form(...),
    product_type: str = Form(...),
    density_g_per_ml: float = Form(1.0),
    pot_life_minutes: int | None = Form(None),
    hazard_class: str | None = Form(None),
    description: str | None = Form(None),
    sds_url: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a product's fields."""
    try:
        result = await db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            return RedirectResponse(
                url="/admin/products?error=Product+not+found", status_code=303
            )

        product.ppg_code = ppg_code.strip()
        product.name = name.strip()
        product.product_type = product_type.strip()
        product.density_g_per_ml = density_g_per_ml
        product.pot_life_minutes = pot_life_minutes
        product.hazard_class = hazard_class.strip() if hazard_class else None
        product.description = description.strip() if description else None
        product.sds_url = sds_url.strip() if sds_url else None
        product.updated_at = datetime.utcnow()

        await db.flush()
        logger.info(f"Product updated: {product_id} ({name})")

    except Exception as e:
        logger.error(f"Error updating product {product_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/products?error=Error+updating+product", status_code=303
        )

    return RedirectResponse(url="/admin/products", status_code=303)


@router.post("/products/{product_id}/delete")
async def product_delete(
    product_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a product (set is_active=False).

    Products are referenced by recipes, inventory, can tracking, etc.
    so we never hard-delete -- just deactivate.
    """
    try:
        result = await db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            return RedirectResponse(
                url="/admin/products?error=Product+not+found", status_code=303
            )

        product.is_active = False
        product.updated_at = datetime.utcnow()
        await db.flush()
        logger.info(f"Product soft-deleted: {product_id} ({product.name})")

    except Exception as e:
        logger.error(f"Error deleting product {product_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/products?error=Error+deleting+product", status_code=303
        )

    return RedirectResponse(url="/admin/products", status_code=303)


# =============================================================================
# RECIPES - Edit & Delete
# =============================================================================

@router.post("/recipes/{recipe_id}/edit")
async def recipe_edit(
    recipe_id: str,
    name: str = Form(...),
    base_product_id: str = Form(...),
    hardener_product_id: str = Form(...),
    ratio_base: float = Form(...),
    ratio_hardener: float = Form(...),
    tolerance_pct: float = Form(5.0),
    thinner_pct_brush: float = Form(5.0),
    thinner_pct_roller: float = Form(5.0),
    thinner_pct_spray: float = Form(10.0),
    recommended_thinner_id: str | None = Form(None),
    pot_life_minutes: int = Form(480),
    db: AsyncSession = Depends(get_db),
):
    """Update a mixing recipe's fields."""
    try:
        result = await db.execute(
            select(MixingRecipe).where(MixingRecipe.id == recipe_id)
        )
        recipe = result.scalar_one_or_none()
        if not recipe:
            return RedirectResponse(
                url="/admin/recipes?error=Recipe+not+found", status_code=303
            )

        recipe.name = name.strip()
        recipe.base_product_id = base_product_id
        recipe.hardener_product_id = hardener_product_id
        recipe.ratio_base = ratio_base
        recipe.ratio_hardener = ratio_hardener
        recipe.tolerance_pct = tolerance_pct
        recipe.thinner_pct_brush = thinner_pct_brush
        recipe.thinner_pct_roller = thinner_pct_roller
        recipe.thinner_pct_spray = thinner_pct_spray
        recipe.recommended_thinner_id = (
            recommended_thinner_id if recommended_thinner_id else None
        )
        recipe.pot_life_minutes = pot_life_minutes

        await db.flush()
        logger.info(f"Recipe updated: {recipe_id} ({name})")

    except Exception as e:
        logger.error(f"Error updating recipe {recipe_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/recipes?error=Error+updating+recipe", status_code=303
        )

    return RedirectResponse(url="/admin/recipes", status_code=303)


@router.post("/recipes/{recipe_id}/delete")
async def recipe_delete(
    recipe_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a mixing recipe.

    Check if any mixing sessions reference this recipe first.
    If so, soft-delete (is_active=False) instead of hard delete.
    """
    try:
        result = await db.execute(
            select(MixingRecipe).where(MixingRecipe.id == recipe_id)
        )
        recipe = result.scalar_one_or_none()
        if not recipe:
            return RedirectResponse(
                url="/admin/recipes?error=Recipe+not+found", status_code=303
            )

        # Check if mixing sessions reference this recipe
        session_count = await db.execute(
            select(func.count()).select_from(MixingSessionCloud).where(
                MixingSessionCloud.recipe_id == recipe_id
            )
        )
        count = session_count.scalar() or 0

        if count > 0:
            # Soft-delete: recipe is referenced by mixing sessions
            recipe.is_active = False
            await db.flush()
            logger.info(
                f"Recipe soft-deleted (referenced by {count} sessions): "
                f"{recipe_id} ({recipe.name})"
            )
        else:
            # Hard delete: no references
            await db.delete(recipe)
            await db.flush()
            logger.info(f"Recipe hard-deleted: {recipe_id} ({recipe.name})")

    except Exception as e:
        logger.error(f"Error deleting recipe {recipe_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/recipes?error=Error+deleting+recipe", status_code=303
        )

    return RedirectResponse(url="/admin/recipes", status_code=303)


# =============================================================================
# COMPANIES - Edit & Delete
# =============================================================================

@router.post("/companies/{company_id}/edit")
async def company_edit(
    company_id: str,
    name: str = Form(...),
    contact_email: str | None = Form(None),
    contact_phone: str | None = Form(None),
    address: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a company's fields."""
    try:
        result = await db.execute(
            select(Company).where(Company.id == company_id)
        )
        company = result.scalar_one_or_none()
        if not company:
            return RedirectResponse(
                url="/admin/fleet?error=Company+not+found", status_code=303
            )

        company.name = name.strip()
        company.contact_email = contact_email.strip() if contact_email else None
        company.contact_phone = contact_phone.strip() if contact_phone else None
        company.address = address.strip() if address else None

        await db.flush()
        logger.info(f"Company updated: {company_id} ({name})")

    except Exception as e:
        logger.error(f"Error updating company {company_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/fleet?error=Error+updating+company", status_code=303
        )

    return RedirectResponse(url="/admin/fleet", status_code=303)


@router.post("/companies/{company_id}/delete")
async def company_delete(
    company_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a company only if no fleets reference it."""
    try:
        result = await db.execute(
            select(Company).where(Company.id == company_id)
        )
        company = result.scalar_one_or_none()
        if not company:
            return RedirectResponse(
                url="/admin/fleet?error=Company+not+found", status_code=303
            )

        # Check for dependent fleets
        fleet_count = await db.execute(
            select(func.count()).select_from(Fleet).where(
                Fleet.company_id == company_id
            )
        )
        count = fleet_count.scalar() or 0

        if count > 0:
            return RedirectResponse(
                url=f"/admin/fleet?error=Cannot+delete+company:+has+{count}+fleet(s).+Delete+fleets+first.",
                status_code=303,
            )

        await db.delete(company)
        await db.flush()
        logger.info(f"Company deleted: {company_id} ({company.name})")

    except Exception as e:
        logger.error(f"Error deleting company {company_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/fleet?error=Error+deleting+company", status_code=303
        )

    return RedirectResponse(url="/admin/fleet", status_code=303)


# =============================================================================
# FLEETS - Edit & Delete
# =============================================================================

@router.post("/fleets/{fleet_id}/edit")
async def fleet_edit(
    fleet_id: str,
    name: str = Form(...),
    region: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a fleet's fields."""
    try:
        result = await db.execute(select(Fleet).where(Fleet.id == fleet_id))
        fleet = result.scalar_one_or_none()
        if not fleet:
            return RedirectResponse(
                url="/admin/fleet?error=Fleet+not+found", status_code=303
            )

        fleet.name = name.strip()
        fleet.region = region.strip() if region else None

        await db.flush()
        logger.info(f"Fleet updated: {fleet_id} ({name})")

    except Exception as e:
        logger.error(f"Error updating fleet {fleet_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/fleet?error=Error+updating+fleet", status_code=303
        )

    return RedirectResponse(url="/admin/fleet", status_code=303)


@router.post("/fleets/{fleet_id}/delete")
async def fleet_delete(
    fleet_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a fleet only if no vessels reference it."""
    try:
        result = await db.execute(select(Fleet).where(Fleet.id == fleet_id))
        fleet = result.scalar_one_or_none()
        if not fleet:
            return RedirectResponse(
                url="/admin/fleet?error=Fleet+not+found", status_code=303
            )

        # Check for dependent vessels
        vessel_count = await db.execute(
            select(func.count()).select_from(Vessel).where(
                Vessel.fleet_id == fleet_id
            )
        )
        count = vessel_count.scalar() or 0

        if count > 0:
            return RedirectResponse(
                url=f"/admin/fleet?error=Cannot+delete+fleet:+has+{count}+vessel(s).+Delete+vessels+first.",
                status_code=303,
            )

        await db.delete(fleet)
        await db.flush()
        logger.info(f"Fleet deleted: {fleet_id} ({fleet.name})")

    except Exception as e:
        logger.error(f"Error deleting fleet {fleet_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/fleet?error=Error+deleting+fleet", status_code=303
        )

    return RedirectResponse(url="/admin/fleet", status_code=303)


# =============================================================================
# VESSELS - Edit & Delete
# =============================================================================

@router.post("/vessels/{vessel_id}/edit")
async def vessel_edit(
    vessel_id: str,
    name: str = Form(...),
    imo_number: str | None = Form(None),
    vessel_type: str | None = Form(None),
    flag_state: str | None = Form(None),
    class_society: str | None = Form(None),
    built_year: int | None = Form(None),
    dwt: float | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Update a vessel's fields."""
    try:
        result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        vessel = result.scalar_one_or_none()
        if not vessel:
            return RedirectResponse(
                url="/admin/fleet?error=Vessel+not+found", status_code=303
            )

        vessel.name = name.strip()
        vessel.imo_number = imo_number.strip() if imo_number else None
        vessel.vessel_type = vessel_type.strip() if vessel_type else None
        vessel.flag_state = flag_state.strip() if flag_state else None
        vessel.class_society = class_society.strip() if class_society else None
        vessel.built_year = built_year
        vessel.dwt = dwt

        await db.flush()
        logger.info(f"Vessel updated: {vessel_id} ({name})")

    except Exception as e:
        logger.error(f"Error updating vessel {vessel_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/fleet?error=Error+updating+vessel", status_code=303
        )

    return RedirectResponse(url="/admin/fleet", status_code=303)


@router.post("/vessels/{vessel_id}/delete")
async def vessel_delete(
    vessel_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a vessel only if no devices reference it."""
    try:
        result = await db.execute(select(Vessel).where(Vessel.id == vessel_id))
        vessel = result.scalar_one_or_none()
        if not vessel:
            return RedirectResponse(
                url="/admin/fleet?error=Vessel+not+found", status_code=303
            )

        # Check for dependent devices
        device_count = await db.execute(
            select(func.count()).select_from(LockerDevice).where(
                LockerDevice.vessel_id == vessel_id
            )
        )
        count = device_count.scalar() or 0

        if count > 0:
            return RedirectResponse(
                url=f"/admin/fleet?error=Cannot+delete+vessel:+has+{count}+device(s).+Remove+devices+first.",
                status_code=303,
            )

        await db.delete(vessel)
        await db.flush()
        logger.info(f"Vessel deleted: {vessel_id} ({vessel.name})")

    except Exception as e:
        logger.error(f"Error deleting vessel {vessel_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/fleet?error=Error+deleting+vessel", status_code=303
        )

    return RedirectResponse(url="/admin/fleet", status_code=303)


# =============================================================================
# DEVICES - Edit & Delete
# =============================================================================

@router.post("/devices/{device_id}/edit")
async def device_edit(
    device_id: str,
    name: str | None = Form(None),
    vessel_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Update a device's name and/or reassign to a different vessel."""
    try:
        result = await db.execute(
            select(LockerDevice).where(LockerDevice.id == device_id)
        )
        device = result.scalar_one_or_none()
        if not device:
            return RedirectResponse(
                url="/admin/devices?error=Device+not+found", status_code=303
            )

        # Validate the target vessel exists
        vessel_result = await db.execute(
            select(Vessel).where(Vessel.id == vessel_id)
        )
        vessel = vessel_result.scalar_one_or_none()
        if not vessel:
            return RedirectResponse(
                url="/admin/devices?error=Target+vessel+not+found", status_code=303
            )

        device.name = name.strip() if name else device.name
        device.vessel_id = vessel_id

        await db.flush()
        logger.info(
            f"Device updated: {device_id} -> vessel {vessel_id} ({vessel.name})"
        )

    except Exception as e:
        logger.error(f"Error updating device {device_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/devices?error=Error+updating+device", status_code=303
        )

    return RedirectResponse(url="/admin/devices", status_code=303)


@router.post("/devices/{device_id}/delete")
async def device_delete(
    device_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a device only if no events or mixing sessions reference it.

    Devices have many FK references (events, mixing sessions, can tracking,
    inventory adjustments, health logs, support requests, pairing codes).
    We check the most common references before allowing deletion.
    """
    try:
        result = await db.execute(
            select(LockerDevice).where(LockerDevice.id == device_id)
        )
        device = result.scalar_one_or_none()
        if not device:
            return RedirectResponse(
                url="/admin/devices?error=Device+not+found", status_code=303
            )

        # Check for dependent events
        event_count_result = await db.execute(
            select(func.count()).select_from(DeviceEvent).where(
                DeviceEvent.device_id == device_id
            )
        )
        event_count = event_count_result.scalar() or 0

        # Check for dependent mixing sessions
        session_count_result = await db.execute(
            select(func.count()).select_from(MixingSessionCloud).where(
                MixingSessionCloud.device_id == device_id
            )
        )
        session_count = session_count_result.scalar() or 0

        total_refs = event_count + session_count
        if total_refs > 0:
            return RedirectResponse(
                url=f"/admin/devices?error=Cannot+delete+device:+has+{event_count}+event(s)+and+{session_count}+mixing+session(s).",
                status_code=303,
            )

        await db.delete(device)
        await db.flush()
        logger.info(f"Device deleted: {device_id} ({device.device_id})")

    except Exception as e:
        logger.error(f"Error deleting device {device_id}: {e}")
        await db.rollback()
        return RedirectResponse(
            url="/admin/devices?error=Error+deleting+device", status_code=303
        )

    return RedirectResponse(url="/admin/devices", status_code=303)
