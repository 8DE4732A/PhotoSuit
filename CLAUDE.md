# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
uv sync                          # Install dependencies
uv run photosuit --help          # CLI help
uv run python -m app.cli process photo.jpg -o output.jpg -t default_white --prop border_padding=0.03
uv run python -m app.cli batch ./photos -o ./output -t film_strip
uv run python -m app.cli templates   # List available templates
uv run python -m app.cli info photo.jpg  # Show EXIF info
uv run python -m app.gui            # Launch Tkinter GUI
```

## Testing

```bash
uv run pytest tests/ -v                                        # All tests
uv run pytest tests/test_pipeline.py::test_normalize_make -v   # Single test
```

## Architecture

PhotoSuit is an EXIF-aware image frame/watermark compositing tool. It processes images through a **5-stage pipeline** defined in `app/pipeline.py`:

```
Parse → Normalize → Render → Rasterize → Composite
```

1. **Parse** (`exif_parser.py`) — Extracts EXIF metadata via `exifread` without loading pixels; gets dimensions via Pillow
2. **Normalize** (`normalizer.py`) — Canonicalizes EXIF values (e.g., "CANON INC." → "Canon"), formats display strings ("18/10" → "f/1.8"), loads brand logos as Base64 data URIs
3. **Render** (`renderer.py`) — Injects normalized context into Jinja2 SVG templates
4. **Rasterize** (`rasterizer.py`) — Converts SVG to PNG via `resvg` (Rust-based) with system font support
5. **Composite** (`compositor.py`) — Overlays rasterized frame onto original image with alpha blending via Pillow, preserves EXIF via `piexif`

Both CLI (`app/cli.py`, Typer) and GUI (`app/gui.py`, Tkinter) share the same pipeline code.

## Template System

Templates are self-contained plugin directories under `app/templates/<template_id>/`:
- `config.json` — Metadata and customizable parameter definitions (type: number/color/boolean/string)
- `template.svg` — Jinja2 + SVG template receiving context: `exif`, `assets` (Base64 logos), `layout` (dimensions), `props` (user params)

Adding a new template requires zero code changes — just create a new directory with these two files. SVG backgrounds must be transparent (compositor adds bg color). The `resvg` rasterizer does not support CSS animations, `foreignObject`, or JavaScript.

## Key Conventions

- Brand logos live in `app/assets/logos/` as SVG files; `.auto.svg` variants exist for color-sensitive contexts
- Brand name mappings in `normalizer.py:normalize_make()` handle messy EXIF make strings → canonical names
- The compositor supports asymmetric layouts via `image_offset_x`/`image_offset_y` props
- Python 3.12+ required; `uv` is the package manager
