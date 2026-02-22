"""EXIF parser: extracts camera metadata from image files."""

from pathlib import Path
from typing import Any

import exifread
from PIL import Image


def parse_exif(image_path: str | Path) -> dict[str, Any]:
    """Parse EXIF data from an image file.

    Returns a structured dictionary with camera metadata and image dimensions.
    """
    image_path = Path(image_path)

    # Read EXIF tags via exifread (does not load pixel data)
    with open(image_path, "rb") as f:
        tags = exifread.process_file(f, details=False)

    # Get image dimensions via Pillow without loading full image
    with Image.open(image_path) as img:
        width, height = img.size

    exif_data: dict[str, Any] = {
        "make": _get_tag(tags, "Image Make"),
        "model": _get_tag(tags, "Image Model"),
        "focal_length": _get_tag(tags, "EXIF FocalLength"),
        "f_number": _get_tag(tags, "EXIF FNumber"),
        "iso": _get_tag(tags, "EXIF ISOSpeedRatings"),
        "exposure_time": _get_tag(tags, "EXIF ExposureTime"),
        "datetime_original": _get_tag(tags, "EXIF DateTimeOriginal"),
        "lens_model": _get_tag(tags, "EXIF LensModel"),
        "image_width": width,
        "image_height": height,
    }

    return exif_data


def _get_tag(tags: dict, key: str) -> str | None:
    """Extract a tag value as a cleaned string, or None if missing."""
    tag = tags.get(key)
    if tag is None:
        return None
    return str(tag).strip()
