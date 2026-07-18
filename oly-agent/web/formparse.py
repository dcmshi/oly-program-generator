# web/formparse.py
"""Form-value parsers shared by the web query modules.

Centralizes the previously copy-pasted `_float`/`_int` helpers and hardens
them: `float("nan")`/`float("inf")` pass a bare try/float and poison NUMERIC
columns (a NaN max breaks all future weight resolution), and 1e6+ overflows
NUMERIC precision into a 500 (WEB-L4).
"""

import math

# Upper bound for any weight/height/duration-ish field — anything above is a
# typo, and NUMERIC(6,2)-class columns overflow long before it.
MAX_REASONABLE_FLOAT = 10000.0


def parse_float(v):
    """Parse a form value to a finite, sane float — or None."""
    try:
        f = float(v) if v else None
    except (ValueError, TypeError):
        return None
    if f is None or not math.isfinite(f) or abs(f) >= MAX_REASONABLE_FLOAT:
        return None
    return f


# int4 tops out at ~2.1e9 — anything near it in a form field is garbage and
# would 500 at the driver (audit2-L3)
MAX_REASONABLE_INT = 2_000_000_000


def parse_int(v, lo: int | None = None, hi: int | None = None):
    """Parse a form value to an int — or None.

    Optional lo/hi mirror DB CHECK constraints so out-of-range values store
    NULL (or hit the caller's default) instead of 500ing on the constraint.
    """
    try:
        i = int(v) if v else None
    except (ValueError, TypeError):
        return None
    if i is None or abs(i) >= MAX_REASONABLE_INT:
        return None
    if lo is not None and i < lo:
        return None
    if hi is not None and i > hi:
        return None
    return i
