"""Identity color for a node id — the terminal mirror of the VS Code extension's ``color.ts``.

Same golden-angle hue hash and the same OKLCH→sRGB conversion, so a feature reads as the *same*
hue in the TUI, the editor gutter, and the graph webview (on a truecolor terminal). Hue is the
identity channel everywhere; status is carried by a glyph + dim, never by hue — that consistency
is the whole point of sharing this function.
"""

from __future__ import annotations

import math

_GOLDEN = 0.618033988749895
# Dark-theme lightness/chroma (terminals are conventionally dark); matches color.ts's dark case.
_L, _C = 0.72, 0.13


def _hash_id(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _oklch_to_hex(L: float, C: float, h_deg: float) -> str:
    h = math.radians(h_deg)
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.291485548 * b
    l, m, s = l_**3, m_**3, s_**3
    lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s

    def g(x: float) -> int:
        v = 12.92 * x if x <= 0.0031308 else 1.055 * (x ** (1 / 2.4)) - 0.055
        return round(max(0.0, min(1.0, v)) * 255)

    return f"#{g(lr):02x}{g(lg):02x}{g(lb):02x}"


def color_for(node_id: str) -> str:
    """Stable ``#rrggbb`` identity color for a node id."""
    hue = ((_hash_id(node_id) * _GOLDEN) % 1) * 360
    return _oklch_to_hex(_L, _C, hue)
