"""Product and Recipe CRUD API endpoints."""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.product import Product, MixingRecipe
from app.api.auth import require_admin
from app.models.user import User

router = APIRouter(prefix="/api/products", tags=["products"])
recipe_router = APIRouter(prefix="/api/recipes", tags=["recipes"])


# ---- Schemas ----

class ProductCreate(BaseModel):
    ppg_code: str
    name: str
    product_type: str
    density_g_per_ml: float = 1.0
    pot_life_minutes: Optional[int] = None
    hazard_class: Optional[str] = None
    can_sizes_ml: Optional[list] = None
    can_tare_weight_g: Optional[dict] = None
    sds_url: Optional[str] = None
    description: Optional[str] = None


class ProductOut(BaseModel):
    id: str
    ppg_code: str
    name: str
    product_type: str
    density_g_per_ml: float
    pot_life_minutes: Optional[int]
    hazard_class: Optional[str]
    can_sizes_ml: Optional[list]
    can_tare_weight_g: Optional[dict]
    is_active: bool

    class Config:
        from_attributes = True


class RecipeCreate(BaseModel):
    name: str
    base_product_id: str
    hardener_product_id: str
    ratio_base: float
    ratio_hardener: float
    tolerance_pct: float = 5.0
    thinner_pct_brush: float = 5.0
    thinner_pct_roller: float = 5.0
    thinner_pct_spray: float = 10.0
    recommended_thinner_id: Optional[str] = None
    pot_life_minutes: int = 480


class RecipeOut(BaseModel):
    id: str
    name: str
    base_product_id: str
    hardener_product_id: str
    ratio_base: float
    ratio_hardener: float
    tolerance_pct: float
    pot_life_minutes: int
    is_active: bool

    class Config:
        from_attributes = True


# ---- Product Endpoints ----

@router.get("", response_model=List[ProductOut])
async def list_products(
    product_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all products, optionally filtered by type."""
    query = select(Product).where(Product.is_active == True)
    if product_type:
        query = query.where(Product.product_type == product_type)
    result = await db.execute(query.order_by(Product.name))
    return result.scalars().all()


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.post("", response_model=ProductOut)
async def create_product(
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    product = Product(**data.model_dump())
    db.add(product)
    await db.flush()
    return product


@router.put("/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: str,
    data: ProductCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    return product


@router.delete("/{product_id}")
async def delete_product(
    product_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    product.is_active = False
    return {"detail": "Product deactivated"}


# ---- Recipe Endpoints ----

@recipe_router.get("", response_model=List[RecipeOut])
async def list_recipes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MixingRecipe).where(MixingRecipe.is_active == True).order_by(MixingRecipe.name)
    )
    return result.scalars().all()


@recipe_router.post("", response_model=RecipeOut)
async def create_recipe(
    data: RecipeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    recipe = MixingRecipe(**data.model_dump())
    db.add(recipe)
    await db.flush()
    return recipe


@recipe_router.put("/{recipe_id}", response_model=RecipeOut)
async def update_recipe(
    recipe_id: str,
    data: RecipeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    result = await db.execute(select(MixingRecipe).where(MixingRecipe.id == recipe_id))
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(recipe, key, value)
    return recipe
