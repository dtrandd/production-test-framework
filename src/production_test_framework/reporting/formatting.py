# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
Sink-agnostic building blocks for a report: number formatting, tables, status vocabulary.

Nothing here knows about pytest or about a file on disk. :mod:`..reporting.report` renders
these into HTML or Markdown; a test only ever builds them.
"""

import html
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = [
    "COMPACT_DECIMALS",
    "SIGNIFICANT_DECIMALS",
    "STATUS_CLASS",
    "CoverageStatus",
    "MetricStatus",
    "ReportFormat",
    "Table",
    "format_delta",
    "format_duration",
    "format_number",
    "format_value",
    "md_escape",
    "render_table",
    "render_table_html",
    "render_table_markdown",
]


class ReportFormat(StrEnum):
    """
    Output format for the report file.

    ``StrEnum`` so the pytest option's string value converts with no lookup table, and
    :meth:`for_path` can infer from a filename when the option is left off.
    """

    HTML = "html"
    MD = "md"

    @classmethod
    def for_path(cls, path: Path) -> ReportFormat:
        """Infer a format from *path*'s suffix, defaulting to HTML for anything unfamiliar."""
        return cls.MD if path.suffix.lower() in (".md", ".markdown") else cls.HTML


class MetricStatus(StrEnum):
    """
    What happened to one expected metric across a workload.

    ``StrEnum`` so a member drops straight into a table cell and an f-string. The three cases
    need different fixes, which is the whole reason the suite distinguishes them:

    * :attr:`ROSE` -- the total increased; the profiler and the pipeline both work.
    * :attr:`FLAT` -- the series exists and is being scraped, but this workload drove no NCCL
      ops through it. The exporter is alive; either the instrumentation for this metric is not
      recording, or the workload genuinely does not exercise it.
    * :attr:`NO_SERIES` -- Prometheus has no series under this name at all, so nothing was ever
      exported. Check the profiler plugin, the OTLP endpoint and the collector.
    """

    ROSE = "rose"
    FLAT = "flat"
    NO_SERIES = "no series"

    @property
    def is_failure(self) -> bool:
        return self is not MetricStatus.ROSE

    @property
    def remedy(self) -> str:
        """One line on what to look at, shown beside a metric that did not increase."""
        match self:
            case MetricStatus.ROSE:
                return ""
            case MetricStatus.FLAT:
                return "scraped but did not move -- workload drove no ops through it"
            case MetricStatus.NO_SERIES:
                return "never exported -- check the profiler plugin, OTLP endpoint and collector"

    @classmethod
    def for_metric(cls, rose: bool, current: float | None) -> MetricStatus:
        """Classify one metric from whether it rose and whether it has a series at all."""
        if rose:
            return cls.ROSE
        return cls.NO_SERIES if current is None else cls.FLAT


class CoverageStatus(StrEnum):
    """Whether a participant count met the profile's declared coverage."""

    OK = "ok"
    SHORT = "short"

    @property
    def is_failure(self) -> bool:
        return self is CoverageStatus.SHORT

    @classmethod
    def for_counts(cls, seen: int, expected: int) -> CoverageStatus:
        return cls.OK if seen >= expected else cls.SHORT


#: Cell text -> CSS class for the HTML sink's status colouring. Covers both the metric
#: vocabulary above and pytest's own outcome names, so one table can carry either.
STATUS_CLASS = {
    MetricStatus.ROSE.value: "ok",
    MetricStatus.FLAT.value: "warn",
    MetricStatus.NO_SERIES.value: "bad",
    CoverageStatus.OK.value: "ok",
    CoverageStatus.SHORT.value: "bad",
    "passed": "ok",
    "failed": "bad",
    "error": "bad",
    "skipped": "warn",
    "xfailed": "warn",
    "xpassed": "warn",
}


SIGNIFICANT_DECIMALS = 4

COMPACT_DECIMALS = 2


def _decimal_places(value: float, significant: int | None = None) -> int:
    """
    How many decimals to show for *value*: as many as it has, capped at *significant* ones.
    """
    if significant is None:
        significant = SIGNIFICANT_DECIMALS

    text = repr(float(value))
    if "e" in text or "E" in text:
        # repr went exponential, which happens below ~1e-5. Derive the leading-zero count from
        # the exponent instead of parsing a mantissa.
        exponent = math.floor(math.log10(abs(value)))
        return max(0, -exponent - 1) + significant

    fraction = text.partition(".")[2].rstrip("0")
    if len(fraction) <= COMPACT_DECIMALS:
        return len(fraction)
    leading_zeros = len(fraction) - len(fraction.lstrip("0"))
    return min(len(fraction), leading_zeros + significant)


def _format_decimal(value: float, significant: int | None = None) -> str:
    """
    Grouped decimal notation, never scientific.
    """
    if not math.isfinite(value):
        return str(value)
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.{_decimal_places(value, significant)}f}"


def format_value(value: float | None, significant: int | None = None) -> str:
    """Render a metric total, distinguishing an absent series from a zero one."""
    return "absent" if value is None else _format_decimal(value, significant)


def format_number(value: float | None, spec: str | None = None, significant: int | None = None) -> str:
    """
    Render a benchmark number, or "-" when the run did not report it.
    """
    if value is None:
        return "-"
    return f"{value:{spec}}" if spec else _format_decimal(value, significant)


def format_duration(seconds: float | None) -> str:
    """
    Converts seconds as ``1 hr 12 mins 3 secs`` format, or "-" when the run did not report it.
    """
    if seconds is None:
        return "-"
    if not math.isfinite(seconds):
        return str(seconds)
    if abs(seconds) < 60:
        return f"{_format_decimal(seconds)} {'sec' if seconds == 1 else 'secs'}"

    whole = round(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    for amount, unit in ((hours, "hr"), (minutes, "min"), (secs, "sec")):
        if amount:
            parts.append(f"{amount} {unit}" + ("" if amount == 1 else "s"))
    return " ".join(parts)


def format_delta(baseline: float | None, current: float | None) -> str:
    """
    Percentage change from *baseline* to *current*.
    """
    if baseline is None or current is None:
        return "-"
    if baseline == 0:
        return "new" if current > 0 else "+0.00%"
    return f"{(current - baseline) / baseline * 100:+.2f}%"


@dataclass
class Table:
    """
    One table, sink-agnostic.

    *left* names the columns to left-align on top of column 0, which always is: right-aligned
    prose reads as ragged, while right-aligned numbers line up so a value orders of magnitude
    from its neighbours is obvious. *status_column* marks the column the HTML sink colours.
    """

    headers: list[str]
    rows: list[list[str]]
    title: str = ""
    left: set[int] = field(default_factory=set)
    status_column: int | None = None


def render_table(table: Table, indent: str = "  ") -> str:
    """Render *table* as fixed-width text, returned as one string."""
    if not table.rows:
        return f"{indent}(no rows)"

    left_aligned = {0} | table.left
    widths = [max(len(row[i]) for row in (table.headers, *table.rows)) for i in range(len(table.headers))]

    def line(cells: list[str]) -> str:
        rendered = (
            cell.ljust(widths[i]) if i in left_aligned else cell.rjust(widths[i]) for i, cell in enumerate(cells)
        )
        return (indent + "  ".join(rendered)).rstrip()

    separator = ["-" * width for width in widths]
    return "\n".join([line(table.headers), line(separator), *(line(row) for row in table.rows)])


def render_table_html(table: Table) -> str:
    """Render *table* as an HTML table, escaping every cell."""
    if not table.rows:
        return "<p class='empty'>(no rows)</p>"

    left_aligned = {0} | table.left

    def cell(tag: str, index: int, text: str) -> str:
        classes = [] if index in left_aligned else ["num"]
        if tag == "td" and index == table.status_column:
            classes.append(STATUS_CLASS.get(text, ""))
        present = [c for c in classes if c]
        attr = f" class='{' '.join(present)}'" if present else ""
        return f"<{tag}{attr}>{html.escape(text)}</{tag}>"

    head = "".join(cell("th", i, h) for i, h in enumerate(table.headers))
    body = "".join(
        "<tr>" + "".join(cell("td", i, value) for i, value in enumerate(row)) + "</tr>" for row in table.rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def md_escape(text: str) -> str:
    """Escape the one character that would break a Markdown table cell."""
    return text.replace("|", r"\|")


def render_table_markdown(table: Table) -> str:
    """
    Render *table* as a Markdown table with every column left-aligned and padded.
    """
    if not table.rows:
        return "_(no rows)_"

    cells = [[md_escape(c) for c in table.headers], *[[md_escape(c) for c in row] for row in table.rows]]
    widths = [max(len(row[i]) for row in cells) for i in range(len(table.headers))]

    def line(row: list[str]) -> str:
        return "| " + " | ".join(value.ljust(widths[i]) for i, value in enumerate(row)) + " |"

    # ":---" marks left alignment; the dashes fill the rest of the column so the rule is as
    # wide as the data above and below it.
    rule = [":" + "-" * (width - 1) if width > 1 else ":" for width in widths]
    return "\n".join([line(cells[0]), "| " + " | ".join(rule) + " |", *(line(row) for row in cells[1:])])
