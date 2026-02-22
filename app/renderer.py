"""Template renderer: loads SVG templates and injects context via Jinja2."""

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).parent / "templates"


def list_templates() -> list[dict[str, Any]]:
    """List all available templates with their metadata."""
    templates = []
    if not TEMPLATES_DIR.exists():
        return templates
    for d in sorted(TEMPLATES_DIR.iterdir()):
        config_path = d / "config.json"
        if d.is_dir() and config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            templates.append(config)
    return templates


def load_template_config(template_id: str) -> dict[str, Any]:
    """Load and return the config.json for a given template."""
    config_path = TEMPLATES_DIR / template_id / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Template '{template_id}' not found at {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def get_default_props(config: dict[str, Any]) -> dict[str, Any]:
    """Extract default prop values from a template config."""
    defaults = {}
    for prop in config.get("props", []):
        defaults[prop["key"]] = prop["default"]
    return defaults


def render_svg(
    template_id: str,
    context: dict[str, Any],
    user_props: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Render a template SVG with the given context.

    Args:
        template_id: Template directory name (e.g. "default_white").
        context: Dict with keys 'exif', 'assets', 'layout' from normalizer.
        user_props: Optional user-supplied property overrides.

    Returns:
        Tuple of (rendered SVG string, merged props dict).
    """
    config = load_template_config(template_id)
    props = get_default_props(config)
    if user_props:
        props.update(user_props)

    template_dir = TEMPLATES_DIR / template_id
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
    )
    template = env.get_template("template.svg")

    full_context = {
        **context,
        "props": props,
    }

    return template.render(**full_context), props
