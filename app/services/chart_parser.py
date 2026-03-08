"""
Maintenance Chart PDF Parser

Extracts structured data from PPG SIGMACARE maintenance chart PDFs:
- Vessel name and IMO number
- Vessel areas with coating layers (products, colors, overcoat times)
- Product info table (thinner, mixing ratio, coverage)

Returns a structured dict that can be saved to the database.
"""

import re
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("smartlocker.chart_parser")


def parse_maintenance_chart(pdf_bytes: bytes) -> Dict[str, Any]:
    """
    Parse a PPG SIGMACARE maintenance chart PDF.

    Args:
        pdf_bytes: Raw PDF file content

    Returns:
        Structured dict with vessel info, areas, products
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Combine all text from all pages
    full_text = ""
    for page in doc:
        full_text += page.get_text() + "\n"
    doc.close()

    result = {
        "vessel_name": "",
        "imo_number": "",
        "creation_date": "",
        "issue": "",
        "areas": [],
        "products": [],
        "marking_colors": [],
    }

    lines = full_text.split("\n")
    lines = [l.strip() for l in lines if l.strip()]

    # ---- Extract vessel info ----
    result["vessel_name"] = _extract_vessel_name(lines)
    result["imo_number"] = _extract_imo(lines)
    result["creation_date"] = _extract_field(lines, "Creation Date:")
    result["issue"] = _extract_field(lines, "Issue:")

    # ---- Extract product info table ----
    result["products"] = _extract_products(lines)

    # ---- Extract areas with coating layers ----
    result["areas"] = _extract_areas(lines)

    # ---- Extract marking colors ----
    result["marking_colors"] = _extract_marking_colors(lines)

    logger.info(
        f"Parsed chart: {result['vessel_name']} (IMO {result['imo_number']}), "
        f"{len(result['areas'])} areas, {len(result['products'])} products"
    )

    return result


# ============================================================
# EXTRACTION HELPERS
# ============================================================

# Known PPG product names (used for area parsing)
KNOWN_PRODUCTS = {
    "SIGMACOVER 280", "SIGMACOVER 350", "SIGMACOVER 456",
    "SIGMADUR 550", "SIGMAGUARD CSF 585",
    "SIGMAPRIME 200", "SIGMAPRIME 700",
    "SIGMARINE 28", "SIGMARINE ONE 648",
    "SIGMATHERM 175", "SIGMATHERM 540",
    "SIGMARITE 750",
}

# Known area names (section headers in the PDF)
KNOWN_AREAS = {
    "TOPSIDE", "WEATHER EXPOSED DECKS", "HANDRAILS ON UPPER DECK",
    "OUTSIDE SUPERSTRUCTURE", "DECK CRANE", "RADAR MAST & ANTENNA POST",
    "LIFE BOAT DAVITS & PROVISION CRANE",
    "HATCHCOVERS OUTSIDE & HATCH COAMING OUTSIDE + ALL PIPES",
    "CARGO HOLDS", "FUNNEL EXTERNAL", "WINDLASS / MOORING / GRABS",
    "ENGINE ROOM FLOORS", "ENGINE ROOM MAIN ENGINE", "ENGINE ROOM WALLS",
    "ENGINE ROOM HEAT RESISTANC UP TO 150",  # Typo in original PDF
    "WATER BALLAST TANK", "FRESH AND DRINKING WATER TANKS",
    "MARKING COLORS", "PRODUCT INFO",
}


def _extract_vessel_name(lines: List[str]) -> str:
    """Extract vessel name (line after 'Maintenance Chart')."""
    for i, line in enumerate(lines):
        if "Maintenance Chart" in line and i + 1 < len(lines):
            name = lines[i + 1].strip()
            if name and name != "IMO Number:" and not name.startswith("9"):
                return name
    return ""


def _extract_imo(lines: List[str]) -> str:
    """Extract IMO number."""
    for i, line in enumerate(lines):
        if "IMO Number:" in line:
            # IMO could be on same line or next line
            match = re.search(r"(\d{7})", line)
            if match:
                return match.group(1)
            if i + 1 < len(lines):
                match = re.search(r"(\d{7})", lines[i + 1])
                if match:
                    return match.group(1)
    return ""


def _extract_field(lines: List[str], field_name: str) -> str:
    """Extract a named field value."""
    for i, line in enumerate(lines):
        if field_name in line:
            # Value might be on same line after colon
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()
            # Or on the next line
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return ""


def _is_area_header(line: str) -> bool:
    """Check if a line is an area header (all caps, known or matches pattern)."""
    clean = line.strip()
    if not clean:
        return False

    # Skip known non-area headers
    skip = {"Maintenance Chart", "IMO Number:", "Creation Date:", "Issue:",
            "Min overcoat time", "Max overcoat time", "Coat Product",
            "Color", "OR", "PRODUCT INFO", "MARKING COLORS", "STEP"}
    if clean in skip or any(clean.startswith(s) for s in ["5°", "5�", "SIGMA", "Powered"]):
        return False

    # Check if it's a known area
    if clean in KNOWN_AREAS:
        return True

    # Heuristic: mostly uppercase, at least 3 chars, not a product name,
    # not a color value, not a time value
    if (len(clean) >= 5 and
        clean == clean.upper() and
        not any(clean.startswith(p) for p in KNOWN_PRODUCTS) and
        not re.match(r"^\d", clean) and
        "hrs" not in clean.lower() and
        "µ" not in clean and
        "�" not in clean and
        "RECOMMENDED" not in clean):
        return True

    return False


def _extract_products(lines: List[str]) -> List[Dict[str, Any]]:
    """Extract the PRODUCT INFO table."""
    products = []
    in_product_info = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line == "PRODUCT INFO":
            in_product_info = True
            # Skip header row (Product, Thinner, Mixing ratio, m² per Liter)
            i += 1
            # Skip "Product", "Thinner", "Mixing ratio BASE : HRD", "m² per Liter"
            while i < len(lines) and lines[i].strip() in (
                "Product", "Thinner", "Mixing ratio BASE : HRD",
                "m² per Liter", "m\u00b2 per Liter"
            ):
                i += 1
            continue

        if in_product_info and line:
            # Check if this is a product name (starts with SIGMA)
            if line.startswith("SIGMA"):
                product_name = line.strip()
                thinner = ""
                mixing_ratio = ""
                components = 1
                base_ratio = 100
                hardener_ratio = 0
                coverage_m2 = 0

                # Next lines: thinner, mixing ratio, coverage
                if i + 1 < len(lines):
                    thinner = lines[i + 1].strip()

                if i + 2 < len(lines):
                    ratio_line = lines[i + 2].strip()
                    if "component" in ratio_line:
                        if "2 component" in ratio_line:
                            components = 2
                            # Extract ratio like "80:20" or "88:12"
                            match = re.search(r"(\d+):(\d+)", ratio_line)
                            if match:
                                base_ratio = int(match.group(1))
                                hardener_ratio = int(match.group(2))
                        else:
                            components = 1

                if i + 3 < len(lines):
                    try:
                        coverage_m2 = int(lines[i + 3].strip())
                    except ValueError:
                        pass

                products.append({
                    "name": product_name,
                    "thinner": thinner if thinner != "No thinner" else None,
                    "components": components,
                    "base_ratio": base_ratio,
                    "hardener_ratio": hardener_ratio,
                    "coverage_m2_per_liter": coverage_m2,
                })
                i += 4
                continue

            # If we hit another section, stop
            if _is_area_header(line) or line == "MARKING COLORS":
                in_product_info = False

        i += 1

    return products


def _extract_areas(lines: List[str]) -> List[Dict[str, Any]]:
    """Extract vessel areas with their coating layers."""
    areas = []
    current_area = None
    i = 0

    # Stop sections
    stop_sections = {"MARKING COLORS", "PRODUCT INFO", "STEP 1"}

    while i < len(lines):
        line = lines[i].strip()

        # Stop at non-area sections
        if line in stop_sections or line.startswith("STEP "):
            break

        # Check for area header
        if _is_area_header(line):
            if current_area and current_area["layers"]:
                areas.append(current_area)

            current_area = {
                "name": line,
                "layers": [],
                "notes": "",
            }
            i += 1
            # Skip header rows
            while i < len(lines) and lines[i].strip() in (
                "Min overcoat time", "Max overcoat time",
                "Coat Product", "Color",
            ):
                i += 1
            # Skip temperature headers
            while i < len(lines) and ("°C" in lines[i] or "�C" in lines[i]):
                i += 1
            continue

        # If we're inside an area, look for coating layers
        if current_area is not None:
            # Layer number (1, 2, etc.)
            if re.match(r"^\d$", line):
                layer_num = int(line)
                product_name = ""
                color = ""

                # Product name on next line
                if i + 1 < len(lines):
                    product_name = lines[i + 1].strip()

                # Color on next line (or skip if it's a time)
                if i + 2 < len(lines):
                    next_val = lines[i + 2].strip()
                    if "hrs" not in next_val and "°C" not in next_val and "�C" not in next_val:
                        color = next_val
                        i += 3
                    else:
                        i += 2
                else:
                    i += 2

                # Skip overcoat time lines
                while i < len(lines) and (
                    "hrs" in lines[i] or
                    re.match(r"^\d+[MDY]\s*-", lines[i].strip()) or
                    "Unlimited" in lines[i]
                ):
                    i += 1

                current_area["layers"].append({
                    "layer_number": layer_num,
                    "product": product_name,
                    "color": color,
                })
                continue

            # DFT notes
            if "Recommended dry film thickness" in line or "RECOMMENDED" in line.upper():
                current_area["notes"] = line
                i += 1
                continue

            # "OR" alternative product
            if line == "OR":
                i += 1
                continue

        i += 1

    # Don't forget the last area
    if current_area and current_area["layers"]:
        areas.append(current_area)

    return areas


def _extract_marking_colors(lines: List[str]) -> List[Dict[str, str]]:
    """Extract marking color definitions."""
    colors = []
    in_marking = False

    for i, line in enumerate(lines):
        if line.strip() == "MARKING COLORS":
            in_marking = True
            continue

        if in_marking:
            if line.strip() == "PRODUCT INFO":
                break

            # Skip header
            if line.strip() in ("Product", "Color"):
                continue

            # Marking entries are like "FIRE FIGHTING" followed by "RED 6188"
            # This is complex due to the two-column layout - simplified extraction
            clean = line.strip()
            if clean and clean == clean.upper() and len(clean) > 3:
                # Check if next line is a color
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and (
                        any(c in next_line.upper() for c in
                            ["RED", "BLUE", "GREEN", "YELLOW", "BLACK", "WHITE",
                             "BROWN", "GREY", "ORANGE", "VIOLET", "ALUMINIUM"])
                    ):
                        colors.append({
                            "purpose": clean,
                            "color": next_line,
                        })

    return colors
