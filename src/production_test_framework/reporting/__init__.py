# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
One result report per run, in HTML or Markdown.

A suite turns the report on from its root ``conftest.py``::

    pytest_plugins = ["production_test_framework.reporting.pytest_plugin"]

and a run writes one with ``--report-file build/report.html``. Tests take the ``reporter``
fixture and add tables to it; everything else -- the header, the environment, the per-category
results and the failure summary -- is assembled from what pytest already knows.

The submodules are layered so a caller only imports what it needs:

* :mod:`~.formatting` -- numbers, tables and the status vocabulary. Knows nothing of pytest.
* :mod:`~.report` -- the :class:`Reporter` and the pytest hooks that feed it.
* :mod:`~.environment` -- rows describing the runner, the checkout and the GPUs.
* :mod:`~.workload` -- the configuration and result tables for a finished workload.
* :mod:`~.assets` -- the header logo, embedded into the report file itself.
"""

from .assets import Logo, load_logo
from .environment import (
    benchmark_option_rows,
    ci_rows,
    command_output,
    display_path,
    git_rows,
    gpu_rows,
    runner_rows,
)
from .formatting import (
    COMPACT_DECIMALS,
    SIGNIFICANT_DECIMALS,
    CoverageStatus,
    MetricStatus,
    ReportFormat,
    Table,
    format_delta,
    format_duration,
    format_number,
    format_value,
    render_table,
    render_table_html,
    render_table_markdown,
)
from .report import DEFAULT_TITLE, Reporter, ReportPlugin
from .workload import report_workload_result, traffic_shape, workload_label

__all__ = [
    "COMPACT_DECIMALS",
    "DEFAULT_TITLE",
    "SIGNIFICANT_DECIMALS",
    "CoverageStatus",
    "Logo",
    "MetricStatus",
    "ReportFormat",
    "ReportPlugin",
    "Reporter",
    "Table",
    "benchmark_option_rows",
    "ci_rows",
    "command_output",
    "display_path",
    "format_delta",
    "format_duration",
    "format_number",
    "format_value",
    "git_rows",
    "gpu_rows",
    "load_logo",
    "render_table",
    "render_table_html",
    "render_table_markdown",
    "report_workload_result",
    "runner_rows",
    "traffic_shape",
    "workload_label",
]
