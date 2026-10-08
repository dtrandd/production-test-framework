# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
Inline SVG figures for the report.
"""

import html

from .formatting import format_number

__all__ = ["latency_percentile_figure", "scatter_figure"]

LATENCY_FAMILIES = (
    ("ttft", "TTFT", "time to first token"),
    ("tpot", "TPOT", "time per output token"),
    ("itl", "ITL", "inter-token latency"),
    ("e2el", "E2EL", "end-to-end latency"),
)

PERCENTILES = (
    ("mean", "mean"),
    ("median", "p50"),
    ("p90", "p90"),
    ("p99", "p99"),
    ("p99_9", "p99.9"),
)

MIN_BARS = 2

_WIDTH = 360
_ROW = 20
_BAR = 13
_LABEL_GUTTER = 46
_VALUE_GUTTER = 74
_RADIUS = 4


def _bar_path(x: float, y: float, width: float, height: float, radius: float = _RADIUS) -> str:
    """
    A bar with its data-end rounded and its baseline end square.
    """
    r = min(radius, width, height / 2)
    if r <= 0:
        return f"M{x},{y} h{width} v{height} h{-width} Z"
    right = x + width
    return (
        f"M{x},{y} H{right - r} A{r},{r} 0 0 1 {right},{y + r} "
        f"V{y + height - r} A{r},{r} 0 0 1 {right - r},{y + height} H{x} Z"
    )


def _family_values(latency: dict[str, float], suffix: str) -> list[tuple[str, float]]:
    """``[(label, value)]`` for one family, in percentile order, skipping what the run omitted."""
    found = []
    for key, label in PERCENTILES:
        value = latency.get(f"{key}_{suffix}")
        if value is not None:
            found.append((label, float(value)))
    return found


def _panel(name: str, description: str, values: list[tuple[str, float]]) -> str:
    """One small multiple: a horizontal bar per percentile, on this family's own scale."""
    height = _ROW * len(values)
    largest = max(value for _, value in values) or 1.0
    span = _WIDTH - _LABEL_GUTTER - _VALUE_GUTTER

    last = len(values) - 1
    steps = [round(index * 4 / last) if last else 0 for index in range(len(values))]

    marks = []
    for index, ((label, value), step) in enumerate(zip(values, steps, strict=True)):
        y = index * _ROW + (_ROW - _BAR) / 2
        width = max(value / largest * span, 1.0)
        reading = f"{format_number(value)} ms"
        marks.append(
            f"<g><title>{html.escape(f'{name} {label}: {reading}')}</title>"
            f"<text class='cl' x='{_LABEL_GUTTER - 8}' y='{y + _BAR / 2}'>{html.escape(label)}</text>"
            f"<path class='cb{step}' d='{_bar_path(_LABEL_GUTTER, y, width, _BAR)}'/>"
            f"<text class='cv' x='{_LABEL_GUTTER + width + 6}' y='{y + _BAR / 2}'>{html.escape(reading)}</text>"
            "</g>"
        )

    return (
        "<figure class='chart'>"
        f"<svg viewBox='0 0 {_WIDTH} {height}' role='img' "
        f"aria-label='{html.escape(f'{name} latency percentiles, milliseconds')}'>"
        f"<line class='ca' x1='{_LABEL_GUTTER}' y1='0' x2='{_LABEL_GUTTER}' y2='{height}'/>"
        f"{''.join(marks)}</svg>"
        f"<figcaption><b>{html.escape(name)}</b> — {html.escape(description)} (ms)</figcaption>"
        "</figure>"
    )


def latency_percentile_figure(latency: dict[str, float]) -> str:
    """
    The latency distribution as small multiples, or "" when the run reported too little.

    One panel per family, each on **its own scale** -- TTFT and TPOT differ by two orders of
    magnitude, and a shared axis would flatten whichever is smaller into nothing. Every bar is
    labelled with its value so the panels are never compared by length alone.
    """
    panels = [
        _panel(name, description, values)
        for suffix, name, description in LATENCY_FAMILIES
        if len(values := _family_values(latency, suffix)) >= MIN_BARS
    ]
    if not panels:
        return ""
    return f"<div class='charts'>{''.join(panels)}</div>"


_SCATTER_W = 360
_SCATTER_H = 200
_SCATTER_PAD = (46, 12, 14, 30)  # left, right, top, bottom


def _scatter_panel(title: str, x_label: str, y_label: str, points: list[tuple[float, float, str, bool]]) -> str:
    left, right, top, bottom = _SCATTER_PAD
    width, height = _SCATTER_W - left - right, _SCATTER_H - top - bottom
    xs = [x for x, _, _, _ in points]
    ys = [y for _, y, _, _ in points]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(0.0, min(ys)), max(ys)
    x_span = (x_hi - x_lo) or abs(x_hi) or 1.0
    y_span = (y_hi - y_lo) or abs(y_hi) or 1.0
    x_lo, x_hi = x_lo - x_span * 0.1, x_hi + x_span * 0.1
    y_hi += y_span * 0.1

    def place(x: float, y: float) -> tuple[float, float]:
        return left + (x - x_lo) / (x_hi - x_lo) * width, top + height - (y - y_lo) / (y_hi - y_lo) * height

    marks = []
    for x, y, label, current in sorted(points, key=lambda point: point[3]):
        px, py = place(x, y)
        reading = f"{label}: {format_number(x)} {x_label}, {format_number(y)} {y_label}"
        marks.append(
            f"<g><title>{html.escape(reading)}</title>"
            f"<circle class='{'cb4' if current else 'cb1'}' cx='{px:.1f}' cy='{py:.1f}' r='{5 if current else 3.5}'/>"
            "</g>"
        )
    tick = {
        name: html.escape(format_number(value, significant=2))
        for name, value in (("x_lo", x_lo), ("x_hi", x_hi), ("y_lo", y_lo), ("y_hi", y_hi))
    }
    base, middle = top + height, left + width / 2
    axes = (
        f"<line class='ca' x1='{left}' y1='{base}' x2='{left + width}' y2='{base}'/>"
        f"<line class='ca' x1='{left}' y1='{top}' x2='{left}' y2='{base}'/>"
        f"<text class='cl' x='{left - 4}' y='{top}'>{tick['y_hi']}</text>"
        f"<text class='cl' x='{left - 4}' y='{base}'>{tick['y_lo']}</text>"
        f"<text class='cv' x='{left}' y='{base + 12}'>{tick['x_lo']}</text>"
        f"<text class='cl' x='{left + width}' y='{base + 12}'>{tick['x_hi']}</text>"
        f"<text class='cv' x='{middle}' y='{base + 24}' text-anchor='middle'>{html.escape(x_label)}</text>"
    )
    return (
        "<figure class='chart'>"
        f"<svg viewBox='0 0 {_SCATTER_W} {_SCATTER_H}' role='img' aria-label='{html.escape(title)}'>"
        f"{axes}{''.join(marks)}</svg>"
        f"<figcaption><b>{html.escape(title)}</b> ({html.escape(y_label)})</figcaption>"
        "</figure>"
    )


def scatter_figure(panels: list[tuple[str, str, str, list[tuple[float, float, str, bool]]]]) -> str:
    """
    Small-multiple scatter plots, one per ``(title, x_label, y_label, points)`` panel.

    Each point is ``(x, y, label, current)``; the current run is drawn larger and darker. Panels
    with no point are left out, and "" is returned when none remain.
    """
    drawn = [_scatter_panel(*panel) for panel in panels if panel[3]]
    return f"<div class='charts'>{''.join(drawn)}</div>" if drawn else ""
