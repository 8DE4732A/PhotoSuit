"""Rasterizer: converts SVG strings to PNG images via resvg."""

from affine import Affine
from resvg import render, usvg

# Initialize font database once (load system fonts for text rendering)
_fontdb = usvg.FontDatabase.default()
_fontdb.load_system_fonts()


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

    tree = usvg.Tree.from_str(svg_string, opts, _fontdb)

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
