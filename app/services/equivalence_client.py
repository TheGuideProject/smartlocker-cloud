"""Bridge to the Product Equivalence platform (technical datasheets + bot).

The cloud is the only party that knows the Product Equivalence service key.
Devices call the cloud; the cloud calls Product Equivalence and caches the
result, so:
  - the key never reaches a device,
  - the m²-to-litres coverage lookup is fast (DB cache), and
  - operators keep working (stale cache) when Product Equivalence is down.
"""

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.product_spec_cache import ProductSpecCache

logger = logging.getLogger("smartlocker.equivalence")

SPECS_PATH = "/api/integrations/smartlocker/product-specs"
CHAT_PATH = "/api/integrations/smartlocker/tech-chat"


# ---- Pure helpers (unit-testable, no I/O) ----

def normalize_key(name: str) -> str:
    """Normalized cache key for a product name."""
    return " ".join((name or "").strip().lower().split())


def is_integration_configured() -> bool:
    return bool(settings.PRODUCT_EQUIVALENCE_URL and settings.SMARTLOCKER_SERVICE_KEY)


def is_cache_fresh(fetched_at: datetime, ttl_hours: int, now: datetime | None = None) -> bool:
    """True when a cache row is younger than the TTL."""
    if fetched_at is None:
        return False
    now = now or datetime.utcnow()
    return (now - fetched_at) < timedelta(hours=ttl_hours)


def cache_to_response(row: ProductSpecCache, *, stale: bool = False, cached: bool = True) -> dict:
    """Shape a cache row into the device-facing response."""
    return {
        "ok": True,
        "query": row.query_name,
        "matched_name": row.matched_name,
        "match_type": row.match_type,
        "coverage_m2_per_l": row.coverage_m2_per_l,
        "coverage_source": row.coverage_source,
        "confidence": row.confidence,
        "needs_validation": row.needs_validation,
        "specs": row.specs_json,
        "candidates": row.candidates_json or [],
        "cached": cached,
        "stale": stale,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def unavailable_response(name: str, reason: str) -> dict:
    """Graceful response when no data and no cache are available."""
    return {
        "ok": False,
        "query": name,
        "matched_name": None,
        "match_type": "none",
        "coverage_m2_per_l": None,
        "coverage_source": "none",
        "confidence": None,
        "needs_validation": True,
        "specs": None,
        "candidates": [],
        "cached": False,
        "stale": False,
        "error": reason,
    }


def _apply_remote_to_cache(row: ProductSpecCache, data: dict) -> None:
    """Copy a Product Equivalence response onto a cache row."""
    matched = data.get("matched") or {}
    coverage = data.get("coverage") or {}
    row.matched_name = matched.get("name")
    row.match_type = data.get("matchType")
    row.coverage_m2_per_l = coverage.get("m2PerL")
    row.coverage_source = coverage.get("source")
    row.confidence = data.get("confidence")
    row.needs_validation = bool(data.get("needsValidation", True))
    row.specs_json = data.get("specs")
    row.candidates_json = data.get("candidates") or []
    row.fetched_at = datetime.utcnow()


# ---- Remote calls ----

async def _post(path: str, payload: dict) -> dict | None:
    """POST to Product Equivalence with the service key. Returns JSON or None."""
    base = settings.PRODUCT_EQUIVALENCE_URL.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.SMARTLOCKER_SERVICE_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=settings.PRODUCT_EQUIVALENCE_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{base}{path}", json=payload, headers=headers)
        if resp.status_code != 200:
            logger.warning("Product Equivalence %s returned HTTP %s", path, resp.status_code)
            return None
        return resp.json()
    except Exception as exc:  # network error, timeout, bad JSON
        logger.warning("Product Equivalence %s call failed: %s", path, exc)
        return None


async def get_product_specs(db: AsyncSession, name: str) -> dict:
    """Resolve technical specs (coverage m²/L, etc.) for a product name.

    Cache-first: returns fresh cache, else fetches from Product Equivalence and
    upserts the cache, else returns stale cache, else an unavailable response.
    """
    clean = (name or "").strip()
    if not clean:
        return unavailable_response(name, "name is required")

    key = normalize_key(clean)
    result = await db.execute(
        select(ProductSpecCache).where(ProductSpecCache.query_key == key)
    )
    row = result.scalar_one_or_none()

    if row and is_cache_fresh(row.fetched_at, settings.PRODUCT_SPEC_CACHE_TTL_HOURS):
        return cache_to_response(row)

    if not is_integration_configured():
        if row:
            return cache_to_response(row, stale=True)
        return unavailable_response(clean, "Product Equivalence integration is not configured")

    data = await _post(SPECS_PATH, {"name": clean})
    if not data or not data.get("ok"):
        if row:
            return cache_to_response(row, stale=True)
        return unavailable_response(clean, "Product Equivalence is unavailable")

    if row is None:
        row = ProductSpecCache(query_key=key, query_name=clean)
        db.add(row)
    _apply_remote_to_cache(row, data)
    await db.flush()
    return cache_to_response(row, cached=False)


async def tech_chat(question: str, product_name: str | None = None) -> dict:
    """Proxy a technical question to the Product Equivalence bot (live, no cache)."""
    clean_q = (question or "").strip()
    if not clean_q:
        return {"ok": False, "error": "question is required", "answer": ""}
    if not is_integration_configured():
        return {
            "ok": False,
            "error": "Technical bot is not configured",
            "answer": "The technical assistant is not available on this deployment yet.",
        }

    payload = {"question": clean_q}
    if product_name:
        payload["productName"] = product_name.strip()
    data = await _post(CHAT_PATH, payload)
    if not data:
        return {
            "ok": False,
            "error": "Technical bot is unavailable",
            "answer": "The technical assistant could not be reached. Try again later.",
        }
    return data
