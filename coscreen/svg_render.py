"""SVG rasterization with a wheel-contained backend on Windows."""
from __future__ import annotations

import io
import sys


def svg_to_png(svg: bytes, output_width: int) -> bytes:
    if sys.platform != 'win32':
        import cairosvg
        return cairosvg.svg2png(bytestring=svg, output_width=output_width)
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM
    drawing = svg2rlg(io.BytesIO(svg))
    if drawing is None or drawing.width <= 0:
        raise ValueError('SVG has no drawable content')
    return renderPM.drawToString(drawing, fmt='PNG', dpi=72 * output_width / drawing.width,
                                 backend='_renderPM')
