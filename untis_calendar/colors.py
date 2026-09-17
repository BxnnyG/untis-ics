"""Convert the subject colours WebUntis reports into what ICS accepts.

Untis returns colours as hex ("#80ffff"). RFC 7986 only allows CSS3 colour
names for the COLOR property, not hex values, so the nearest named colour is
used instead.
"""

from __future__ import annotations

import re

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

# A spread of CSS3 colour names, wide enough that any Untis colour maps to
# something recognisable.
CSS3_COLORS: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "gray": (128, 128, 128),
    "silver": (192, 192, 192),
    "white": (255, 255, 255),
    "maroon": (128, 0, 0),
    "red": (255, 0, 0),
    "tomato": (255, 99, 71),
    "lightcoral": (240, 128, 128),
    "salmon": (250, 128, 114),
    "orange": (255, 165, 0),
    "gold": (255, 215, 0),
    "yellow": (255, 255, 0),
    "olive": (128, 128, 0),
    "chartreuse": (127, 255, 0),
    "lime": (0, 255, 0),
    "green": (0, 128, 0),
    "lightgreen": (144, 238, 144),
    "teal": (0, 128, 128),
    "aqua": (0, 255, 255),
    "paleturquoise": (175, 238, 238),
    "lightblue": (173, 216, 230),
    "blue": (0, 0, 255),
    "navy": (0, 0, 128),
    "purple": (128, 0, 128),
    "magenta": (255, 0, 255),
    "pink": (255, 192, 203),
    "lightpink": (255, 182, 193),
    "rosybrown": (188, 143, 143),
    "brown": (165, 42, 42),
    "tan": (210, 180, 140),
}


def parse_hex(value: str | None) -> tuple[int, int, int] | None:
    """'#80ffff' -> (128, 255, 255). Returns None for invalid input."""
    if not value:
        return None
    m = HEX_RE.match(str(value).strip())
    if not m:
        return None
    raw = m.group(1)
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def nearest_css_name(value: str | None) -> str | None:
    """Nearest CSS3 colour name for a hex value.

    Weighted distance in RGB space, roughly following perceived brightness so
    that a light green does not end up as 'navy'.
    """
    rgb = parse_hex(value)
    if rgb is None:
        return None
    r, g, b = rgb

    def distance(other: tuple[int, int, int]) -> float:
        dr, dg, db = r - other[0], g - other[1], b - other[2]
        return 2 * dr * dr + 4 * dg * dg + 3 * db * db

    return min(CSS3_COLORS, key=lambda name: distance(CSS3_COLORS[name]))


def normalise_hex(value: str | None) -> str | None:
    """Normalise to lowercase '#rrggbb'."""
    rgb = parse_hex(value)
    if rgb is None:
        return None
    return "#{:02x}{:02x}{:02x}".format(*rgb)
