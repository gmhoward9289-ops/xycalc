"""Optional renderer for a sweep-chart PNG. Export does not call this.

The landing og:image is Bill's approved still at
`static/landing-still.png`, copied by `xycalc export` when that file exists.
This module must not be used to invent a substitute hero.
"""

from __future__ import annotations

import math
from pathlib import Path

from .model import Model, ModelError, format_quantity, parse_bytes
from .pngutil import write_rgb_png

# Open Graph landscape. Twitter summary_large_image uses the same ratio.
OG_WIDTH = 1200
OG_HEIGHT = 630
SAMPLES = 96  # matches static/app.js

# README / CLI smoke-test inputs. Demonstration values, not corpus coefficients.
OG_MODEL = "mongodb.wt-cache"
OG_SWEEP_KEY = "storage_size"
OG_STORAGE = "500GB"
OG_INDEX = "40GB"
OG_AVAILABLE = "256GB"

# Light-theme calculator tokens (og:image is often shown on a white card).
BG = (251, 250, 248)
INK = (26, 26, 26)
MUTED = (107, 107, 107)
LINE = (226, 222, 216)
ACCENT = (31, 95, 79)
AMPLIFIER = (156, 66, 33)
PANEL = (255, 255, 255)

# 5×7 glyphs, bit 4 = leftmost pixel. Unknown chars are skipped.
_FONT: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    ".": (0, 0, 0, 0, 0, 0, 0x04),
    ",": (0, 0, 0, 0, 0, 0x04, 0x08),
    "-": (0, 0, 0, 0x1F, 0, 0, 0),
    "–": (0, 0, 0, 0x1F, 0, 0, 0),
    "—": (0, 0, 0, 0x1F, 0, 0, 0),
    "·": (0, 0, 0, 0x04, 0, 0, 0),
    ":": (0, 0x04, 0, 0, 0, 0x04, 0),
    "/": (0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10),
    "%": (0x19, 0x1A, 0x04, 0x04, 0x08, 0x13, 0x13),
    "×": (0, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0),
    "0": (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    "1": (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "2": (0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F),
    "3": (0x0E, 0x11, 0x01, 0x06, 0x01, 0x11, 0x0E),
    "4": (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    "5": (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    "6": (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    "7": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    "8": (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    "9": (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "A": (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "B": (0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E),
    "C": (0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E),
    "D": (0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E),
    "E": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F),
    "F": (0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10),
    "G": (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    "H": (0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    "I": (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "J": (0x01, 0x01, 0x01, 0x01, 0x11, 0x11, 0x0E),
    "K": (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    "L": (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    "M": (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    "N": (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    "O": (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "P": (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    "Q": (0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D),
    "R": (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    "S": (0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E),
    "T": (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    "U": (0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    "V": (0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "W": (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    "X": (0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11),
    "Y": (0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04),
    "Z": (0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F),
    "a": (0, 0, 0x0E, 0x01, 0x0F, 0x11, 0x0F),
    "b": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x1E),
    "c": (0, 0, 0x0E, 0x11, 0x10, 0x11, 0x0E),
    "d": (0x01, 0x01, 0x0F, 0x11, 0x11, 0x11, 0x0F),
    "e": (0, 0, 0x0E, 0x11, 0x1F, 0x10, 0x0E),
    "f": (0x06, 0x08, 0x08, 0x1C, 0x08, 0x08, 0x08),
    "g": (0, 0, 0x0F, 0x11, 0x0F, 0x01, 0x0E),
    "h": (0x10, 0x10, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "i": (0x04, 0, 0x0C, 0x04, 0x04, 0x04, 0x0E),
    "j": (0x02, 0, 0x06, 0x02, 0x02, 0x12, 0x0C),
    "k": (0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12),
    "l": (0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    "m": (0, 0, 0x1A, 0x15, 0x15, 0x15, 0x15),
    "n": (0, 0, 0x1E, 0x11, 0x11, 0x11, 0x11),
    "o": (0, 0, 0x0E, 0x11, 0x11, 0x11, 0x0E),
    "p": (0, 0, 0x1E, 0x11, 0x1E, 0x10, 0x10),
    "q": (0, 0, 0x0F, 0x11, 0x0F, 0x01, 0x01),
    "r": (0, 0, 0x16, 0x19, 0x10, 0x10, 0x10),
    "s": (0, 0, 0x0F, 0x10, 0x0E, 0x01, 0x1E),
    "t": (0x08, 0x08, 0x1C, 0x08, 0x08, 0x08, 0x06),
    "u": (0, 0, 0x11, 0x11, 0x11, 0x13, 0x0D),
    "v": (0, 0, 0x11, 0x11, 0x11, 0x0A, 0x04),
    "w": (0, 0, 0x11, 0x11, 0x15, 0x15, 0x0A),
    "x": (0, 0, 0x11, 0x0A, 0x04, 0x0A, 0x11),
    "y": (0, 0, 0x11, 0x11, 0x0F, 0x01, 0x0E),
    "z": (0, 0, 0x1F, 0x02, 0x04, 0x08, 0x1F),
}


class OgImageError(Exception):
    pass


def _put(buf: bytearray, w: int, h: int, x: int, y: int, rgb: tuple[int, int, int], a: float = 1.0) -> None:
    if x < 0 or y < 0 or x >= w or y >= h:
        return
    i = (y * w + x) * 3
    if a >= 1:
        buf[i] = rgb[0]
        buf[i + 1] = rgb[1]
        buf[i + 2] = rgb[2]
        return
    buf[i] = int(buf[i] * (1 - a) + rgb[0] * a)
    buf[i + 1] = int(buf[i + 1] * (1 - a) + rgb[1] * a)
    buf[i + 2] = int(buf[i + 2] * (1 - a) + rgb[2] * a)


def _fill(buf: bytearray, w: int, h: int, rgb: tuple[int, int, int]) -> None:
    r, g, b = rgb
    for i in range(0, w * h * 3, 3):
        buf[i] = r
        buf[i + 1] = g
        buf[i + 2] = b


def _rect(
    buf: bytearray,
    w: int,
    h: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    rgb: tuple[int, int, int],
    a: float = 1.0,
) -> None:
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    for y in range(max(0, y0), min(h, y1 + 1)):
        for x in range(max(0, x0), min(w, x1 + 1)):
            _put(buf, w, h, x, y, rgb, a)


def _dot(buf: bytearray, w: int, h: int, cx: float, cy: float, radius: float, rgb: tuple[int, int, int], a: float = 1.0) -> None:
    r = int(math.ceil(radius))
    x0, y0 = int(cx), int(cy)
    for y in range(y0 - r, y0 + r + 1):
        for x in range(x0 - r, x0 + r + 1):
            d = math.hypot(x - cx, y - cy)
            if d <= radius:
                cover = min(1.0, radius + 0.5 - d)
                _put(buf, w, h, x, y, rgb, a * cover)


def _polyline(
    buf: bytearray,
    w: int,
    h: int,
    pts: list[tuple[float, float]],
    rgb: tuple[int, int, int],
    width: float,
    dashed: bool = False,
    a: float = 1.0,
) -> None:
    dash_on, dash_off = 14.0, 10.0
    phase = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist == 0:
            continue
        steps = max(1, int(dist))
        for s in range(steps + 1):
            t = s / steps
            draw = True
            if dashed:
                cycle = (phase + t * dist) % (dash_on + dash_off)
                draw = cycle < dash_on
            if draw:
                _dot(buf, w, h, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, width / 2, rgb, a)
        phase += dist


def _fill_poly(
    buf: bytearray,
    w: int,
    h: int,
    pts: list[tuple[float, float]],
    rgb: tuple[int, int, int],
    a: float,
) -> None:
    if len(pts) < 3:
        return
    ys = [p[1] for p in pts]
    y_min = max(0, int(math.floor(min(ys))))
    y_max = min(h - 1, int(math.ceil(max(ys))))
    n = len(pts)
    for y in range(y_min, y_max + 1):
        scan = y + 0.5
        xs: list[float] = []
        for i in range(n):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % n]
            if y0 == y1:
                continue
            if (y0 <= scan < y1) or (y1 <= scan < y0):
                t = (scan - y0) / (y1 - y0)
                xs.append(x0 + t * (x1 - x0))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            xa, xb = xs[i], xs[i + 1]
            x_start = max(0, int(math.floor(xa)))
            x_end = min(w - 1, int(math.ceil(xb)))
            for x in range(x_start, x_end + 1):
                _put(buf, w, h, x, y, rgb, a)


def _text(
    buf: bytearray,
    w: int,
    h: int,
    x: int,
    y: int,
    text: str,
    rgb: tuple[int, int, int],
    scale: int = 2,
    align: str = "left",
) -> None:
    glyphs = []
    for ch in text:
        g = _FONT.get(ch) or _FONT.get(ch.upper())
        if g is None:
            continue
        glyphs.append(g)
    width = len(glyphs) * 6 * scale
    if align == "right":
        x -= width
    elif align == "center":
        x -= width // 2
    for gi, rows in enumerate(glyphs):
        for row, bits in enumerate(rows):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    px = x + (gi * 6 + col) * scale
                    py = y + row * scale
                    _rect(buf, w, h, px, py, px + scale - 1, py + scale - 1, rgb)


def _ticks(lo: float, hi: float, log: bool, count: int) -> list[float]:
    """Same 1-2-5 decades / linear steps as static/app.js ticks()."""
    out: list[float] = []
    if log and lo > 0:
        from_e = math.floor(math.log10(lo))
        to_e = math.ceil(math.log10(hi))
        e = from_e
        while e <= to_e:
            for m in (1, 2, 5):
                v = m * (10**e)
                if lo <= v <= hi:
                    out.append(v)
            e += 1
        while len(out) > count + 2:
            i = len(out) - 2
            while i > 0:
                del out[i]
                i -= 2
            if len(out) <= count + 2:
                break
        return out
    span = hi - lo
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    norm = raw / mag
    step_mul = 10 if norm > 5 else 5 if norm > 2 else 2 if norm > 1 else 1
    step = step_mul * mag
    v = math.ceil(lo / step) * step
    while v <= hi:
        out.append(v)
        v += step
    return out


def _sweep_bounds(centre: float | None, unit: str) -> tuple[float, float]:
    if centre:
        return centre / 10, centre * 10
    return (1e9, 1e13) if unit == "bytes" else (1.0, 1e4)


def _nearest_index(xs: list[float], x: float) -> int:
    best, best_d = 0, float("inf")
    for i, xi in enumerate(xs):
        d = abs(math.log(xi) - math.log(x))
        if d < best_d:
            best, best_d = i, d
    return best


def _sweep_grid(frm: float, to: float, samples: int, centre: float | None) -> list[float]:
    grid = [frm * ((to / frm) ** (i / (samples - 1))) for i in range(samples)]
    if centre:
        grid[_nearest_index(grid, centre)] = centre
    return grid


def compute_og_sweep(conn) -> dict:
    """Band envelope for the landing card. Raises OgImageError if it cannot."""
    slugs = Model.all(conn)
    if OG_MODEL not in slugs:
        raise OgImageError(f"og image needs {OG_MODEL}, which is not in this corpus")
    model = Model.load(conn, OG_MODEL)
    spec = next((i for i in model.inputs if i["key"] == OG_SWEEP_KEY), None)
    if spec is None:
        raise OgImageError(f"{OG_MODEL} has no input {OG_SWEEP_KEY}")
    values = {
        "storage_size": parse_bytes(OG_STORAGE),
        "index_size": parse_bytes(OG_INDEX),
    }
    try:
        centre_result = model.evaluate(values)
    except ModelError as e:
        raise OgImageError(str(e)) from e
    centre = values[OG_SWEEP_KEY]
    frm, to = _sweep_bounds(centre, spec["unit"])
    grid = _sweep_grid(frm, to, SAMPLES, centre)
    xs, los, modes, his = [], [], [], []
    for x in grid:
        trial = dict(values)
        trial[OG_SWEEP_KEY] = x
        try:
            r = model.evaluate(trial)
        except ModelError:
            continue
        if not math.isfinite(r.mode):
            continue
        xs.append(x)
        los.append(r.lo)
        modes.append(r.mode)
        his.append(r.hi)
    if len(xs) < 2:
        raise OgImageError("og sweep produced fewer than two evaluable points")
    return {
        "model": model,
        "spec": spec,
        "unit": centre_result.unit,
        "centre": centre,
        "available": parse_bytes(OG_AVAILABLE),
        "xs": xs,
        "los": los,
        "modes": modes,
        "his": his,
        "question": model.question,
    }


def render_og_png(conn, path: Path) -> Path:
    sweep = compute_og_sweep(conn)
    w, h = OG_WIDTH, OG_HEIGHT
    buf = bytearray(w * h * 3)
    _fill(buf, w, h, BG)
    _rect(buf, w, h, 24, 24, w - 25, h - 25, PANEL)
    _rect(buf, w, h, 24, 24, w - 25, 24, LINE)
    _rect(buf, w, h, 24, 24, 24, h - 25, LINE)
    _rect(buf, w, h, w - 25, 24, w - 25, h - 25, LINE)
    _rect(buf, w, h, 24, h - 25, w - 25, h - 25, LINE)

    _text(buf, w, h, 48, 44, "xycalc", ACCENT, scale=5)
    _text(buf, w, h, 48, 84, "the band, not a point estimate", MUTED, scale=2)
    _text(
        buf,
        w,
        h,
        w - 48,
        48,
        "what you already have",
        AMPLIFIER,
        scale=2,
        align="right",
    )

    L, R, T, B = 100, 48, 130, 88
    iw, ih = w - L - R, h - T - B
    xs, los, modes, his = sweep["xs"], sweep["los"], sweep["modes"], sweep["his"]
    avail = sweep["available"]
    y_min = min(min(los), avail)
    y_max = max(max(his), avail)
    log_y = y_min > 0
    if log_y:
        y_min = y_min / 1.15
        y_max = y_max * 1.15
    else:
        y_min = min(0.0, y_min)
        y_max = y_max * 1.05
    if y_max == y_min:
        y_max = y_min + 1
    x_lo, x_hi = xs[0], xs[-1]

    def px(x: float) -> float:
        return L + (math.log(x / x_lo) / math.log(x_hi / x_lo)) * iw

    def py(y: float) -> float:
        if log_y:
            return T + ih - (math.log(max(y, y_min) / y_min) / math.log(y_max / y_min)) * ih
        return T + ih - ((y - y_min) / (y_max - y_min)) * ih

    x_ticks = _ticks(x_lo, x_hi, True, 5)
    y_ticks = _ticks(y_min, y_max, log_y, 5)
    for t in y_ticks:
        y = int(round(py(t)))
        _polyline(buf, w, h, [(L, y), (L + iw, y)], LINE, 1.5)
        _text(buf, w, h, L - 8, y - 7, format_quantity(t, sweep["unit"]), MUTED, scale=1, align="right")
    for t in x_ticks:
        x = int(round(px(t)))
        _polyline(buf, w, h, [(x, T + ih), (x, T + ih + 6)], LINE, 1.5)
        _text(buf, w, h, x, T + ih + 12, format_quantity(t, sweep["spec"]["unit"]), MUTED, scale=1, align="center")

    hi_pts = [(px(x), py(y)) for x, y in zip(xs, his)]
    lo_pts = [(px(x), py(y)) for x, y in zip(xs, los)]
    mode_pts = [(px(x), py(y)) for x, y in zip(xs, modes)]
    band = hi_pts + list(reversed(lo_pts))
    _fill_poly(buf, w, h, band, ACCENT, 0.18)
    _polyline(buf, w, h, hi_pts, ACCENT, 2.5, dashed=True, a=0.7)
    _polyline(buf, w, h, lo_pts, ACCENT, 2.5, dashed=True, a=0.7)
    _polyline(buf, w, h, mode_pts, ACCENT, 4.0)

    if y_min <= avail <= y_max:
        y = py(avail)
        _polyline(buf, w, h, [(L, y), (L + iw, y)], AMPLIFIER, 3.0, dashed=True)
        _text(
            buf,
            w,
            h,
            L + iw,
            int(y) - 18,
            "you have " + format_quantity(avail, sweep["unit"]),
            AMPLIFIER,
            scale=2,
            align="right",
        )

    if x_lo <= sweep["centre"] <= x_hi:
        x = px(sweep["centre"])
        _polyline(buf, w, h, [(x, T), (x, T + ih)], INK, 2.0, a=0.35)

    _text(
        buf,
        w,
        h,
        L + iw // 2,
        h - 52,
        sweep["spec"]["label"],
        MUTED,
        scale=2,
        align="center",
    )
    _text(
        buf,
        w,
        h,
        48,
        h - 44,
        f"{OG_STORAGE} on disk, {OG_INDEX} indexes, {OG_AVAILABLE} already in RAM",
        MUTED,
        scale=2,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    write_rgb_png(path, w, h, bytes(buf))
    return path
