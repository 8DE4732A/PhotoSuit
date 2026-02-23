"""Rasterizer: converts SVG strings to PNG images via resvg."""

import threading

from affine import Affine
from resvg import render, usvg

# resvg FontDatabase is not Send-safe — use thread-local storage so each
# thread (main thread + any background worker) gets its own instance.
_local = threading.local()


def _get_fontdb() -> usvg.FontDatabase:
    """Return a per-thread FontDatabase, creating it on first access."""
    db = getattr(_local, "fontdb", None)
    if db is None:
        db = usvg.FontDatabase.default()
        db.load_system_fonts()
        _local.fontdb = db
    return db


def rasterize_svg(svg_string: str, output_width: int | None = None) -> bytes:
    """Convert an SVG string to PNG bytes.

    Args:
        svg_string: The SVG markup to rasterize.
        output_width: Optional target width in pixels. If provided, an affine
            scale transform is applied to fit the output.

    Returns:
        PNG image data as bytes.
    """
    opts = usvg.Options.default()
    opts.font_family = "Arial"

    tree = usvg.Tree.from_str(svg_string, opts, _get_fontdb())

    if output_width is not None:
        tree_w, _ = tree.int_size()
        if tree_w > 0:
            scale = output_width / tree_w
            tr = Affine.scale(scale)
        else:
            tr = Affine.identity()
    else:
        tr = Affine.identity()

    return bytes(render(tree, tr[0:6]))
