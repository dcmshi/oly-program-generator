"""Two-page spread detection for vision OCR (OCR-ROLE part 2b).

A scan of an open book puts two printed pages in one landscape image. Sent
whole, the image is downscaled to the model's input size and both pages share
one transcription; Kimi K3 split Kono's spread 158–159 across two PDF pages.
`PDFExtractor` sends a detected spread as a left and a right half instead.

Pure module except `spread_gutters`, which reads a PyMuPDF document.
"""

from __future__ import annotations

from shared.constants import (
    OCR_SPREAD_ASPECT_MIN,
    OCR_SPREAD_DETECT_DPI,
    OCR_SPREAD_DOC_MIN_SHARE,
    OCR_SPREAD_GUTTER_BAND,
    OCR_SPREAD_GUTTER_MAX_INK,
    OCR_SPREAD_GUTTER_MIN_WIDTH,
)

_INK = 200               # grayscale value below which a pixel is ink (text at 40 DPI antialiases to ~150-200)
_MARGIN_ROWS = 0.08      # top / bottom share ignored (gutter shadow ticks, scanner edges)
_MIN_SIDE_INK = 0.002    # each half must hold this share of dark pixels — not a blank page


def find_gutter(samples: bytes, width: int, height: int, stride: int | None = None) -> float | None:
    """The gutter of a grayscale page render as a fraction of its width, or None.

    The gutter is the near-empty column closest to the middle of the page
    that lies in a run of near-empty columns at least
    OCR_SPREAD_GUTTER_MIN_WIDTH wide inside OCR_SPREAD_GUTTER_BAND, with ink
    on both sides. Closest to the middle, not the widest run: a half-empty
    page (a chapter end) joins the gutter to its own blank area, and the
    middle of that run would cut through its text. A single page centred on
    a landscape canvas, a diagram or a table running across the middle has no
    such run.
    """
    stride = stride or width
    if width < 20 or height < 20:
        return None
    top, bottom = int(height * _MARGIN_ROWS), int(height * (1 - _MARGIN_ROWS))
    rows = range(top, bottom)
    n_rows = max(1, bottom - top)
    lo, hi = int(width * OCR_SPREAD_GUTTER_BAND[0]), int(width * OCR_SPREAD_GUTTER_BAND[1])

    col_dark = [sum(1 for y in rows if samples[y * stride + x] < _INK) for x in range(width)]
    empty = [col_dark[x] / n_rows <= OCR_SPREAD_GUTTER_MAX_INK for x in range(lo, hi)]

    runs, run_start = [], None
    for k, is_empty in enumerate(empty + [False]):
        if is_empty and run_start is None:
            run_start = k
        elif not is_empty and run_start is not None:
            if k - run_start >= max(1, OCR_SPREAD_GUTTER_MIN_WIDTH * width):
                runs.append((lo + run_start, lo + k))          # [start, end) in page columns
            run_start = None
    if not runs:
        return None
    middle = width // 2
    split = min((min(max(middle, a), b - 1) for a, b in runs), key=lambda x: abs(x - middle))

    def ink_share(x0: int, x1: int) -> float:
        return sum(col_dark[x0:x1]) / max(1, (x1 - x0) * n_rows)

    if ink_share(0, split) < _MIN_SIDE_INK or ink_share(split, width) < _MIN_SIDE_INK:
        return None
    return split / width


def is_landscape(width: float, height: float) -> bool:
    return height > 0 and width / height >= OCR_SPREAD_ASPECT_MIN


def spread_gutters(doc, n_pages: int) -> dict[int, float]:
    """{page index: gutter fraction} for the pages to send as two halves.

    Empty unless at least OCR_SPREAD_DOC_MIN_SHARE of the scan is landscape,
    so the odd sideways page in a portrait book is never split.
    """
    import fitz

    landscape = [i for i in range(n_pages) if is_landscape(doc[i].rect.width, doc[i].rect.height)]
    if not n_pages or len(landscape) / n_pages < OCR_SPREAD_DOC_MIN_SHARE:
        return {}
    zoom = fitz.Matrix(OCR_SPREAD_DETECT_DPI / 72, OCR_SPREAD_DETECT_DPI / 72)
    gutters = {}
    for i in landscape:
        pix = doc[i].get_pixmap(matrix=zoom, colorspace=fitz.csGRAY)
        gutter = find_gutter(pix.samples, pix.width, pix.height, pix.stride)
        if gutter is not None:
            gutters[i] = gutter
    return gutters
