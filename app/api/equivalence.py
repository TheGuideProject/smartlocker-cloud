"""Device-facing endpoints for the Product Equivalence integration.

Paired SmartLocker devices call these (with their X-API-Key) to get technical
product data (coverage m²/L for the Paint Now m²→litres math) and to ask the
technical bot. The cloud bridges to Product Equivalence and caches results.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import LockerDevice
from app.api.events import verify_device_api_key
from app.services import equivalence_client

router = APIRouter(prefix="/api/devices", tags=["devices-equivalence"])


@router.get("/product-specs")
async def device_product_specs(
    name: str = Query(..., min_length=1),
    device: LockerDevice = Depends(verify_device_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Technical specs (coverage m²/L, volume solids, density, ...) by product name."""
    return await equivalence_client.get_product_specs(db, name)


class TechChatRequest(BaseModel):
    question: str
    product_name: str | None = None


@router.post("/tech-chat")
async def device_tech_chat(
    payload: TechChatRequest,
    device: LockerDevice = Depends(verify_device_api_key),
):
    """Grounded technical Q&A bot for paint operators."""
    return await equivalence_client.tech_chat(payload.question, payload.product_name)
