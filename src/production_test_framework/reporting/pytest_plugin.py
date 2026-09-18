# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
The pytest side of the report: options, and the reporter every suite shares.

Enable it from a suite's root ``conftest.py``::

    pytest_plugins = ["production_test_framework.reporting.pytest_plugin"]

then point a run at a file with ``--report-file``. Without that option the tests print their
tables to stdout instead, which is what CI logs and a bare ``pytest`` run want.

The reporter is published as ``config.mosaic_reporter`` and as the ``reporter`` fixture.
"""

from pathlib import Path

import pytest

from .assets import Logo, load_logo
from .formatting import ReportFormat
from .report import DEFAULT_MAX_FAILURE_SNIPPETS, DEFAULT_TITLE, Reporter, ReportPlugin

__all__ = ["category_markers", "reporter"]

#: Markers that name a suite, most specific first. A test's report section is the first of
#: these it carries, so one marked both ``lgtm`` and ``disruptive`` files under ``lgtm``:
#: suite-identity markers come before the ones that merely describe how a test behaves.
#:
#: A suite overrides the list with the ``report_category_markers`` ini option; anything it
#: leaves out still gets a section, ordered after these.
DEFAULT_CATEGORY_MARKERS = (
    "delos_mosaic_ui",
    "delos_mosaic_sso",
    "delos_mosaic_virtual_redfish",
    "mission_control",
    "vpc_api",
    "grafana_ui",
    "lgtm",
    "k3s",
    "topos",
    "xpt",
    "gnmi",
    "fpga",
    "metrics",
    "profiler_otel",
    "dashboards",
    # Behavioural markers last: they qualify a test rather than say which suite it belongs to,
    # so they only ever become a section for a test that carries nothing more specific.
    "performance",
    "disruptive",
    "teardown",
)


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
            "or WebP. Defaults to the bundled Delos Data lockup; pass an empty string for no "
            "logo at all."
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
    # A suite that enables this plugin more than once -- its own conftest plus a -p flag --
    # would otherwise get two reporters, the second overwriting the first's file.
    if getattr(config, "mosaic_reporter", None) is not None:
        return

    raw_path = config.getoption("report_file")
    path = Path(raw_path).expanduser() if raw_path else None
    chosen = config.getoption("report_format")
    fmt = ReportFormat(chosen) if chosen else (ReportFormat.for_path(path) if path else ReportFormat.HTML)

    title = config.getoption("report_title") or DEFAULT_TITLE

    # Unset means the bundled lockup; a path means that file; an explicitly empty string
    # means no logo at all, which Logo("") expresses and load_logo() cannot.
    raw_logo = config.getoption("report_logo")
    if raw_logo is None:
        logo = load_logo()
    elif raw_logo:
        logo = load_logo(raw_logo)
    else:
        logo = Logo("")

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
