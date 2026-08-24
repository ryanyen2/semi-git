"""Hand-drawn SVG. No charting library, this is one page of bars."""


def bar_chart(pairs, width=760, height=220, label=str):
    """A bar per pair, tallest bar full height. `pairs` is [(label_value, count)]."""
    if not pairs:
        return "<p>nothing to draw</p>"
    top = max(count for _, count in pairs) or 1
    step = width / len(pairs)
    bars = []
    for i, (key, count) in enumerate(pairs):
        h = (count / top) * (height - 30)
        bars.append(
            f'<rect x="{i * step:.1f}" y="{height - 20 - h:.1f}" width="{step * 0.8:.1f}" '
            f'height="{h:.1f}" fill="#3b6ea5"><title>{label(key)}: {count:,}</title></rect>'
        )
    return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">{"".join(bars)}</svg>'
