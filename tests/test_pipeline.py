"""Tests for the PhotoSuit processing pipeline."""

import json
import shutil
from pathlib import Path

import piexif
from PIL import Image

from app.exif_parser import parse_exif
from app.normalizer import (
    format_aperture,
    format_exposure_time,
    format_focal_length,
    format_iso,
    load_logo_base64,
    normalize_exif,
    normalize_make,
)
from app.pipeline import process_image
from app.renderer import list_templates, load_template_config, render_svg


TEST_DIR = Path(__file__).parent


def _create_test_image(path: Path) -> None:
    """Create a small test JPEG with EXIF data."""
    img = Image.new("RGB", (800, 600), color=(100, 150, 200))
    img.save(str(path), "JPEG", quality=90)
    exif_dict = {
        "0th": {
            piexif.ImageIFD.Make: b"Canon",
            piexif.ImageIFD.Model: b"Canon EOS R5",
        },
        "Exif": {
            piexif.ExifIFD.FocalLength: (50, 1),
            piexif.ExifIFD.FNumber: (18, 10),
            piexif.ExifIFD.ISOSpeedRatings: 100,
            piexif.ExifIFD.ExposureTime: (1, 250),
            piexif.ExifIFD.DateTimeOriginal: b"2024:06:15 14:30:00",
            piexif.ExifIFD.LensModel: b"RF 50mm F1.2L USM",
        },
    }
    piexif.insert(piexif.dump(exif_dict), str(path))


# --- Normalizer tests ---


def test_normalize_make():
    assert normalize_make("Canon Inc.") == "Canon"
    assert normalize_make("CANON INC.") == "Canon"
    assert normalize_make("NIKON CORPORATION") == "Nikon"
    assert normalize_make("SONY") == "Sony"
    assert normalize_make("Apple") == "Apple"
    assert normalize_make("FUJIFILM") == "Fujifilm"
    assert normalize_make(None) == "Unknown"
    assert normalize_make("") == "Unknown"


def test_format_focal_length():
    assert format_focal_length("50") == "50mm"
    assert format_focal_length("50.0") == "50mm"
    assert format_focal_length("85/1") == "85mm"
    assert format_focal_length("35/2") == "17.5mm"
    assert format_focal_length(None) == ""


def test_format_aperture():
    assert format_aperture("1.8") == "f/1.8"
    assert format_aperture("18/10") == "f/1.8"
    assert format_aperture("4") == "f/4"
    assert format_aperture(None) == ""


def test_format_exposure_time():
    assert format_exposure_time("1/250") == "1/250s"
    assert format_exposure_time("1/1000") == "1/1000s"
    assert format_exposure_time("2") == "2s"
    assert format_exposure_time(None) == ""


def test_format_iso():
    assert format_iso("100") == "ISO 100"
    assert format_iso("3200") == "ISO 3200"
    assert format_iso(None) == ""


def test_load_logo_base64():
    logo = load_logo_base64("Canon")
    assert logo is not None
    assert logo.startswith("data:image/svg+xml;base64,")

    auto_logo = load_logo_base64("Canon", auto=True)
    assert auto_logo is not None

    missing = load_logo_base64("NonExistentBrand")
    assert missing is None


# --- EXIF parser tests ---


def test_parse_exif(tmp_path):
    img_path = tmp_path / "test.jpg"
    _create_test_image(img_path)

    exif = parse_exif(img_path)
    assert exif["make"] == "Canon"
    assert exif["model"] == "Canon EOS R5"
    assert exif["image_width"] == 800
    assert exif["image_height"] == 600
    assert exif["iso"] == "100"


# --- Normalize EXIF tests ---


def test_normalize_exif(tmp_path):
    img_path = tmp_path / "test.jpg"
    _create_test_image(img_path)

    raw = parse_exif(img_path)
    result = normalize_exif(raw)

    assert result["exif"]["make"] == "Canon"
    assert result["exif"]["model"] == "EOS R5"  # manufacturer prefix stripped
    assert result["exif"]["focal_length"] == "50mm"
    assert result["exif"]["aperture"] == "f/1.8"
    assert result["exif"]["iso"] == "ISO 100"
    assert result["assets"]["make_logo_base64"] is not None
    assert result["layout"]["image_width"] == 800


# --- Renderer tests ---


def test_list_templates():
    tpls = list_templates()
    assert len(tpls) >= 1
    assert tpls[0]["id"] == "default_white"


def test_render_svg(tmp_path):
    img_path = tmp_path / "test.jpg"
    _create_test_image(img_path)

    raw = parse_exif(img_path)
    context = normalize_exif(raw)
    svg, props = render_svg("default_white", context)

    assert "<svg" in svg
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert isinstance(props, dict)
    assert "border_padding" in props


# --- Full pipeline test ---


def test_process_image(tmp_path):
    img_path = tmp_path / "test.jpg"
    out_path = tmp_path / "output.jpg"
    _create_test_image(img_path)

    process_image(img_path, out_path, "default_white")

    assert out_path.exists()
    output = Image.open(out_path)
    # Output should be larger than input due to border
    assert output.size[0] > 800
    assert output.size[1] > 600
    assert output.format == "JPEG"

    # EXIF should be preserved
    exif_dict = piexif.load(str(out_path))
    make = exif_dict["0th"].get(piexif.ImageIFD.Make, b"")
    assert b"Canon" in make


# --- External templates directory tests ---


def _setup_external_template(tmp_path: Path) -> tuple[Path, str]:
    """Create an external templates directory with a minimal template."""
    ext_dir = tmp_path / "ext_templates"
    tpl_id = "test_external"
    tpl_dir = ext_dir / tpl_id
    tpl_dir.mkdir(parents=True)

    config = {
        "id": tpl_id,
        "name": "External Test Template",
        "description": "A test template in an external directory",
        "props": [
            {"key": "border_padding", "label": "Border", "type": "number", "default": 0.05},
            {"key": "bg_color", "label": "BG Color", "type": "color", "default": "#FFFFFF"},
        ],
    }
    (tpl_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    svg = """\
{% set pad = props.border_padding | default(0.05) %}
{% set img_w = layout.image_width %}
{% set img_h = layout.image_height %}
{% set pad_x = (img_w * pad) | int %}
{% set pad_y = (img_h * pad) | int %}
{% set canvas_w = img_w + pad_x * 2 %}
{% set canvas_h = img_h + pad_y * 2 %}
<svg xmlns="http://www.w3.org/2000/svg"
     width="{{ canvas_w }}" height="{{ canvas_h }}"
     viewBox="0 0 {{ canvas_w }} {{ canvas_h }}">
    <rect width="100%" height="100%" fill="transparent" />
</svg>
"""
    (tpl_dir / "template.svg").write_text(svg, encoding="utf-8")

    return ext_dir, tpl_id


def test_list_templates_external(tmp_path):
    ext_dir, tpl_id = _setup_external_template(tmp_path)

    tpls = list_templates(templates_dir=ext_dir)
    assert len(tpls) == 1
    assert tpls[0]["id"] == tpl_id


def test_list_templates_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    tpls = list_templates(templates_dir=empty)
    assert tpls == []


def test_load_template_config_external(tmp_path):
    ext_dir, tpl_id = _setup_external_template(tmp_path)

    config = load_template_config(tpl_id, templates_dir=ext_dir)
    assert config["id"] == tpl_id
    assert config["name"] == "External Test Template"
    assert len(config["props"]) == 2


def test_render_svg_external(tmp_path):
    ext_dir, tpl_id = _setup_external_template(tmp_path)

    img_path = tmp_path / "test.jpg"
    _create_test_image(img_path)

    raw = parse_exif(img_path)
    context = normalize_exif(raw)
    svg, props = render_svg(tpl_id, context, templates_dir=ext_dir)

    assert "<svg" in svg
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "border_padding" in props


def test_process_image_external(tmp_path):
    ext_dir, tpl_id = _setup_external_template(tmp_path)

    img_path = tmp_path / "test.jpg"
    out_path = tmp_path / "output.jpg"
    _create_test_image(img_path)

    process_image(img_path, out_path, tpl_id, templates_dir=ext_dir)

    assert out_path.exists()
    output = Image.open(out_path)
    assert output.size[0] > 800
    assert output.size[1] > 600
