from __future__ import annotations


def suggest_box(points, frame_aspect: float = 16 / 9,
                screen_aspect: float = 16 / 9,
                pad: float = 0.12):
    """Bounding rect of the body points, padded and matched to screen aspect.

    Returns ``[x0, y0, x1, y1]`` in normalized frame coordinates, or ``None``
    when there is not enough of the body in view to guess from.
    """
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 2:
        return None

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    width = max(0.02, max(xs) - min(xs)) * (1.0 + 2.0 * pad)
    height = max(0.02, max(ys) - min(ys)) * (1.0 + 2.0 * pad)

    target = max(0.05, float(screen_aspect)) / max(0.05, float(frame_aspect))
    if width / height < target:
        width = height * target
    else:
        height = width / target

    if width > 1.0:
        width = 1.0
        height = width / target
    if height > 1.0:
        height = 1.0
        width = height * target

    x0 = min(max(0.0, cx - width / 2.0), 1.0 - width)
    y0 = min(max(0.0, cy - height / 2.0), 1.0 - height)
    return [
        round(x0, 4),
        round(y0, 4),
        round(x0 + width, 4),
        round(y0 + height, 4),
    ]
