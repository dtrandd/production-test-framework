# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
The pytest side of the report: its options, the ``reporter`` fixture, and the hooks that feed
pytest's own results into the :class:`~.report.Reporter`.

Enable it from a suite's root ``conftest.py``::

    pytest_plugins = ["production_test_framework.reporting.report_plugin"]

then point a run at a file with ``--report-file``.
"""

from pathlib import Path

import pytest

from .assets import Logo, load_logo
from .formatting import ReportFormat
from .report import DEFAULT_MAX_FAILURE_SNIPPETS, DEFAULT_TITLE, UNCATEGORIZED, Reporter

__all__ = ["ReportPlugin", "category_markers", "reporter"]


#: None built in: which markers name a report section is the suite's to say, with the
#: ``report_category_markers`` ini option. Without one, every test lands under one section.
DEFAULT_CATEGORY_MARKERS: tuple[str, ...] = ()


def pytest_addoption(parser):
    group = parser.getgroup("mosaic-report")
    group.addoption(
        "--report-file",
        "--report-html",
        action="store",
        default=None,
        metavar="PATH",
        dest="report_file",
        help=(
            "Write one report to PATH: the run summary followed by the result tables the "
            "tests emit. Without this the same tables print to stdout, which is what CI and "
            "a bare pytest run want. --report-html is kept as an alias."
        ),
    )
    group.addoption(
        "--report-format",
        action="store",
        default=None,
        choices=[fmt.value for fmt in ReportFormat],
        help=(
            "Format for --report-file: html (default) or md. Left off, a .md or .markdown "
            "path infers md and anything else html."
        ),
    )
    group.addoption(
        "--report-title",
        action="store",
        default=None,
        metavar="TEXT",
        help=f"Title shown in the report's header (default: {DEFAULT_TITLE!r}).",
    )
    group.addoption(
        "--report-logo",
        action="store",
        default=None,
        metavar="PATH",
        help=(
            "Image for the report header, embedded in the file itself. SVG, PNG, JPEG, GIF "
            "or WebP. Left off, the header has no logo."
        ),
    )
    group.addoption(
        "--report-max-failures",
        action="store",
        type=int,
        default=DEFAULT_MAX_FAILURE_SNIPPETS,
        metavar="N",
        help=(
            "How many stack-trace snippets the failure section shows (default: "
            f"{DEFAULT_MAX_FAILURE_SNIPPETS}). Every failure is listed in the table above "
            "them regardless."
        ),
    )
    parser.addini(
        "report_category_markers",
        type="args",
        default=list(DEFAULT_CATEGORY_MARKERS),
        help=(
            "Markers that name a report section, most specific first. A test is filed under the first one it carries."
        ),
    )


def category_markers(config) -> list[str]:
    """The configured category markers, falling back to the built-in order."""
    configured = config.getini("report_category_markers")
    return list(configured) if configured else list(DEFAULT_CATEGORY_MARKERS)


def pytest_configure(config):
    """Open the report and register the plugin that feeds it pytest's results."""

    if getattr(config, "mosaic_reporter", None) is not None:
        return

    raw_path = config.getoption("report_file")
    path = Path(raw_path).expanduser() if raw_path else None
    chosen = config.getoption("report_format")
    fmt = ReportFormat(chosen) if chosen else (ReportFormat.for_path(path) if path else ReportFormat.HTML)

    title = config.getoption("report_title") or DEFAULT_TITLE

    raw_logo = config.getoption("report_logo")
    logo = load_logo(raw_logo) if raw_logo else Logo("")

    config.mosaic_reporter = Reporter(
        path,
        fmt=fmt,
        title=title,
        logo=logo,
        category_order=category_markers(config),
        max_failure_snippets=config.getoption("report_max_failures"),
    )
    config.pluginmanager.register(ReportPlugin(config.mosaic_reporter, category_markers(config)), "mosaic-report")


@pytest.fixture
def reporter(request):
    """
    The session's :class:`~.report.Reporter`, with a detail section open for this test.
    """
    instance = request.config.mosaic_reporter
    instance.start_test(request.node.nodeid)
    return instance


class ReportPlugin:
    """
    Feeds pytest's own results into a :class:`Reporter`'s summary half.
    """

    def __init__(self, reporter: Reporter, category_markers: list[str] | None = None):
        self.reporter = reporter
        self.category_markers = list(category_markers or [])
        self._categories: dict[str, str] = {}

    def pytest_collection_modifyitems(self, items):
        """
        Resolve every collected test's category.
        """
        for item in items:
            names = {mark.name for mark in item.iter_markers()}
            self._categories[item.nodeid] = next(
                (marker for marker in self.category_markers if marker in names), UNCATEGORIZED
            )

    def pytest_runtest_logreport(self, report):
        """
        Record one finished phase per test.
        """
        if report.when == "call":
            outcome = report.outcome
        elif report.failed:
            outcome = "error"
        elif report.when == "setup" and report.skipped:
            outcome = "skipped"
        else:
            return

        self.reporter.record_outcome(
            nodeid=report.nodeid,
            outcome=outcome,
            duration=report.duration,
            failure_text=report.longreprtext if report.failed else "",
            category=self._categories.get(report.nodeid, UNCATEGORIZED),
        )

    def pytest_sessionfinish(self, session):
        """Write the file one last time and say where it went."""
        if not self.reporter.writes_file:
            return
        self.reporter.flush()
        writer = session.config.get_terminal_writer()
        writer.line(f"\n{self.reporter.format.upper()} report: {self.reporter.path}")
