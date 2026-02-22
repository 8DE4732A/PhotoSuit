"""CLI interface for PhotoSuit, built with Typer."""

from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(
    name="photosuit",
    help="Image border and watermark compositing tool with EXIF-aware SVG templates.",
)


def _parse_props(props: list[str] | None) -> dict:
    """Parse --prop key=value pairs into a dictionary."""
    if not props:
        return {}
    result = {}
    for item in props:
        if "=" not in item:
            raise typer.BadParameter(f"Invalid prop format: '{item}'. Use key=value.")
        key, value = item.split("=", 1)
        # Auto-convert types
        if value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        else:
            try:
                result[key] = float(value) if "." in value else int(value)
            except ValueError:
                result[key] = value
    return result


@app.command()
def process(
    input_path: Annotated[Path, typer.Argument(help="Input image file path")],
    output: Annotated[Path, typer.Option("-o", "--output", help="Output file path")] = None,
    template: Annotated[str, typer.Option("-t", "--template", help="Template ID")] = "default_white",
    prop: Annotated[Optional[list[str]], typer.Option("--prop", help="Template prop override (key=value)")] = None,
) -> None:
    """Process a single image with a frame template."""
    from app.pipeline import process_image

    if not input_path.exists():
        typer.echo(f"Error: Input file not found: {input_path}", err=True)
        raise typer.Exit(1)

    if output is None:
        output = input_path.parent / f"{input_path.stem}_framed.jpg"

    user_props = _parse_props(prop)
    typer.echo(f"Processing {input_path.name} → {output.name} (template: {template})")
    process_image(input_path, output, template, **user_props)
    typer.echo(f"Done! Output saved to {output}")


@app.command()
def batch(
    input_dir: Annotated[Path, typer.Argument(help="Input directory containing images")],
    output_dir: Annotated[Path, typer.Option("-o", "--output", help="Output directory")] = None,
    template: Annotated[str, typer.Option("-t", "--template", help="Template ID")] = "default_white",
    prop: Annotated[Optional[list[str]], typer.Option("--prop", help="Template prop override (key=value)")] = None,
) -> None:
    """Batch process all images in a directory."""
    from app.pipeline import batch_process

    if not input_dir.is_dir():
        typer.echo(f"Error: Not a directory: {input_dir}", err=True)
        raise typer.Exit(1)

    if output_dir is None:
        output_dir = input_dir / "output"

    user_props = _parse_props(prop)
    typer.echo(f"Batch processing {input_dir} → {output_dir} (template: {template})")
    results = batch_process(input_dir, output_dir, template, **user_props)
    typer.echo(f"Done! Processed {len(results)} images.")


@app.command()
def templates() -> None:
    """List all available templates."""
    from app.renderer import list_templates

    tpl_list = list_templates()
    if not tpl_list:
        typer.echo("No templates found.")
        return

    for tpl in tpl_list:
        typer.echo(f"  {tpl['id']:20s} {tpl.get('name', '')} — {tpl.get('description', '')}")


@app.command()
def info(
    input_path: Annotated[Path, typer.Argument(help="Image file to inspect")],
) -> None:
    """Display EXIF information for an image."""
    from app.exif_parser import parse_exif
    from app.normalizer import normalize_exif

    if not input_path.exists():
        typer.echo(f"Error: File not found: {input_path}", err=True)
        raise typer.Exit(1)

    raw = parse_exif(input_path)
    normalized = normalize_exif(raw)
    exif = normalized["exif"]
    layout = normalized["layout"]

    typer.echo(f"File: {input_path.name}")
    typer.echo(f"Dimensions: {layout['image_width']}×{layout['image_height']}")
    typer.echo(f"Make: {exif['make']}")
    typer.echo(f"Model: {exif['model']}")
    typer.echo(f"Lens: {exif['lens_model']}")
    typer.echo(f"Focal Length: {exif['focal_length']}")
    typer.echo(f"Aperture: {exif['aperture']}")
    typer.echo(f"Exposure: {exif['exposure_time']}")
    typer.echo(f"ISO: {exif['iso']}")
    typer.echo(f"Date: {exif['datetime_original']}")


if __name__ == "__main__":
    app()
