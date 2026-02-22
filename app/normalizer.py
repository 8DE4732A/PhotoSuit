"""Data normalizer: standardizes EXIF data and resolves logo assets."""

import base64
from fractions import Fraction
from pathlib import Path
from typing import Any

ASSETS_DIR = Path(__file__).parent / "assets"
LOGOS_DIR = ASSETS_DIR / "logos"

# Mapping from raw EXIF Make values to canonical brand names.
# Keys are lowercased for case-insensitive matching.
MAKE_MAP: dict[str, str] = {
    "canon inc.": "Canon",
    "canon": "Canon",
    "nikon corporation": "Nikon",
    "nikon": "Nikon",
    "sony": "Sony",
    "sony corporation": "Sony",
    "apple": "Apple",
    "fujifilm": "Fujifilm",
    "fujifilm corporation": "Fujifilm",
    "panasonic": "Lumix",
    "lumix": "Lumix",
    "olympus": "Olympus",
    "olympus corporation": "Olympus",
    "om digital solutions": "Olympus",
    "pentax": "Pentax",
    "ricoh imaging company, ltd.": "Pentax",
    "ricoh imaging": "Pentax",
    "ricoh": "Ricoh",
    "sigma": "Sigma",
    "sigma corporation": "Sigma",
    "hasselblad": "Hasselblad",
    "leica": "Leica",
    "leica camera ag": "Leica",
    "dji": "DJI",
    "gopro": "GoPro",
    "samsung": "Samsung",
    "huawei": "Huawei",
    "xiaomi": "Xiaomi",
    "oneplus": "Oneplus",
    "oppo": "OPPO",
    "vivo": "Vivo",
    "nokia": "Nokia",
    "google": "Google",
    "insta360": "Insta360",
}


def normalize_make(raw_make: str | None) -> str:
    """Map a raw EXIF Make string to a canonical brand name."""
    if not raw_make:
        return "Unknown"
    key = raw_make.strip().lower()
    return MAKE_MAP.get(key, raw_make.strip())


def load_logo_base64(brand: str, auto: bool = False) -> str | None:
    """Load brand logo SVG and return as a Base64 data URI.

    Args:
        brand: Canonical brand name (e.g. "Canon").
        auto: If True, prefer the .auto.svg variant (uses currentColor).
    """
    if auto:
        path = LOGOS_DIR / f"{brand}.auto.svg"
        if not path.exists():
            path = LOGOS_DIR / f"{brand}.svg"
    else:
        path = LOGOS_DIR / f"{brand}.svg"

    if not path.exists():
        return None

    svg_bytes = path.read_bytes()
    b64 = base64.b64encode(svg_bytes).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def format_focal_length(raw: str | None) -> str:
    """Format focal length: '50' or '50.0' → '50mm', '85/1' → '85mm'."""
    if not raw:
        return ""
    try:
        if "/" in raw:
            val = float(Fraction(raw))
        else:
            val = float(raw)
        if val == int(val):
            return f"{int(val)}mm"
        return f"{val:.1f}mm"
    except (ValueError, ZeroDivisionError):
        return raw


def format_aperture(raw: str | None) -> str:
    """Format aperture: '1.8' → 'f/1.8', '18/10' → 'f/1.8'."""
    if not raw:
        return ""
    try:
        if "/" in raw:
            val = float(Fraction(raw))
        else:
            val = float(raw)
        if val == int(val):
            return f"f/{int(val)}"
        return f"f/{val:.1f}"
    except (ValueError, ZeroDivisionError):
        return raw


def format_exposure_time(raw: str | None) -> str:
    """Format exposure time: '1/250' → '1/250s', '2' → '2s'."""
    if not raw:
        return ""
    try:
        if "/" in raw:
            num, den = raw.split("/")
            num_f, den_f = float(num), float(den)
            if den_f == 0:
                return raw
            val = num_f / den_f
            if val >= 1:
                if val == int(val):
                    return f"{int(val)}s"
                return f"{val:.1f}s"
            # Keep as fraction
            # Simplify: e.g. 10/2500 → 1/250
            frac = Fraction(int(num_f), int(den_f))
            return f"{frac.numerator}/{frac.denominator}s"
        else:
            val = float(raw)
            if val == int(val):
                return f"{int(val)}s"
            return f"{val:.1f}s"
    except (ValueError, ZeroDivisionError):
        return raw


def format_iso(raw: str | None) -> str:
    """Format ISO: '100' → 'ISO 100'."""
    if not raw:
        return ""
    return f"ISO {raw}"


def normalize_exif(raw_exif: dict[str, Any]) -> dict[str, Any]:
    """Produce a fully normalized context from raw EXIF data.

    Returns a dict suitable for Jinja2 template injection with keys:
    exif, assets, layout.
    """
    make = normalize_make(raw_exif.get("make"))
    model = raw_exif.get("model") or ""
    # Strip manufacturer prefix from model if present
    if make != "Unknown" and model.startswith(make):
        model = model[len(make):].strip()

    exif = {
        "make": make,
        "model": model,
        "focal_length": format_focal_length(raw_exif.get("focal_length")),
        "aperture": format_aperture(raw_exif.get("f_number")),
        "exposure_time": format_exposure_time(raw_exif.get("exposure_time")),
        "iso": format_iso(raw_exif.get("iso")),
        "datetime_original": raw_exif.get("datetime_original") or "",
        "lens_model": raw_exif.get("lens_model") or "",
    }

    assets = {
        "make_logo_base64": load_logo_base64(make),
        "make_logo_auto_base64": load_logo_base64(make, auto=True),
    }

    layout = {
        "image_width": raw_exif.get("image_width", 0),
        "image_height": raw_exif.get("image_height", 0),
    }

    return {"exif": exif, "assets": assets, "layout": layout}
