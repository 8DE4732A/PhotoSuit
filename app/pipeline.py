"""Processing pipeline: orchestrates the 5-stage image processing workflow."""

from pathlib import Path
from typing import Any

from app.exif_parser import parse_exif
from app.normalizer import normalize_exif
from app.renderer import render_svg
from app.rasterizer import rasterize_svg
from app.compositor import composite


def process_image(
    input_path: str | Path,
    output_path: str | Path,
    template_id: str = "default_white",
    **user_props: Any,
) -> None:
    """Process a single image through the full pipeline.

    Stages:
        1. Parse   — extract EXIF metadata
        2. Calc    — normalize data, resolve logos, compute layout
        3. Render  — inject context into SVG template via Jinja2
        4. Raster  — convert SVG to PNG pixels (with font rendering)
        5. Compose — merge original photo with frame overlay

    Args:
        input_path: Source image file path.
        output_path: Destination for the composited JPEG.
        template_id: Which template directory to use.
        **user_props: Override template default props (e.g. bg_color="#000").
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    # Stage 1: Parse EXIF
    raw_exif = parse_exif(input_path)

    # Stage 2: Normalize & calculate
    context = normalize_exif(raw_exif)

    # Stage 3: Render SVG
    svg_string, merged_props = render_svg(template_id, context, user_props or None)

    # Stage 4: Rasterize SVG → PNG
    canvas_width = _calc_canvas_width(context, merged_props)
    frame_png = rasterize_svg(svg_string, output_width=canvas_width)

    # Stage 5: Composite
    composite(
        original_path=input_path,
        frame_png_bytes=frame_png,
        output_path=output_path,
        props=merged_props,
    )


def _calc_canvas_width(context: dict, props: dict) -> int:
    """Calculate the expected canvas width for rasterization."""
    img_w = context["layout"]["image_width"]
    padding = float(props.get("border_padding", 0.05))
    base = int(img_w * (1 + padding * 2))
    # Account for extra horizontal offsets (e.g. film strip margins)
    off_x = int(props.get("image_offset_x", 0))
    if off_x:
        base += off_x * 2  # symmetric left + right
    return base


def batch_process(
    input_dir: str | Path,
    output_dir: str | Path,
    template_id: str = "default_white",
    **user_props: Any,
) -> list[Path]:
    """Process all supported images in a directory.

    Returns list of output file paths.
    """
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    supported = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
    processed = []

    for img_path in sorted(input_dir.iterdir()):
        if img_path.suffix.lower() in supported and not img_path.name.startswith("."):
            out_path = output_dir / f"{img_path.stem}_framed.jpg"
            try:
                process_image(img_path, out_path, template_id, **user_props)
                processed.append(out_path)
            except Exception as e:
                print(f"Error processing {img_path.name}: {e}")

    return processed
