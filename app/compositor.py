"""Compositor: assembles final image from original photo and SVG frame overlay."""

import io
from pathlib import Path
from typing import Any

import piexif
from PIL import Image


def composite(
    original_path: str | Path,
    frame_png_bytes: bytes,
    output_path: str | Path,
    props: dict[str, Any],
    quality: int = 95,
) -> None:
    """Compose the original image with the rendered frame overlay.

    Args:
        original_path: Path to the original photo.
        frame_png_bytes: PNG bytes of the rendered frame layer (logos, text, decorations).
        output_path: Where to write the final JPEG.
        props: Merged template props.
        quality: JPEG output quality (1-100).
    """
    original_path = Path(original_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    border_padding = float(props.get("border_padding", 0.05))
    bg_color = str(props.get("bg_color", "#FFFFFF"))

    # Load original image
    original = Image.open(original_path).convert("RGB")
    orig_w, orig_h = original.size

    # Load frame overlay
    frame = Image.open(io.BytesIO(frame_png_bytes)).convert("RGBA")
    canvas_w, canvas_h = frame.size

    # Create background canvas
    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)

    # Paste original image — use explicit offsets if provided, else padding
    if "image_offset_x" in props or "image_offset_y" in props:
        off_x = int(props.get("image_offset_x", 0))
        off_y = int(props.get("image_offset_y", 0))
        pad_x = int(orig_w * border_padding)
        pad_y = int(orig_h * border_padding)
        canvas.paste(original, (off_x + pad_x, off_y + pad_y))
    else:
        pad_x = int(orig_w * border_padding)
        pad_y = int(orig_h * border_padding)
        canvas.paste(original, (pad_x, pad_y))

    # Overlay frame (logos, text, dividers) with alpha compositing
    canvas.paste(frame, (0, 0), mask=frame.split()[3])

    # Copy EXIF from original to output
    exif_bytes = _read_exif_bytes(original_path)

    if exif_bytes:
        canvas.save(str(output_path), "JPEG", quality=quality, exif=exif_bytes)
    else:
        canvas.save(str(output_path), "JPEG", quality=quality)


def _read_exif_bytes(image_path: Path) -> bytes | None:
    """Read EXIF data from an image and return as bytes for piexif."""
    try:
        exif_dict = piexif.load(str(image_path))
        return piexif.dump(exif_dict)
    except Exception:
        return None
