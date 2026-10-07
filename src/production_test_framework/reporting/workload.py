# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
Report a finished workload -- how it was configured, and what it produced.
"""

from typing import Any

from ..vllm import InferenceResult
from ..workload.inferencex_workload import InferencexBenchmarkResult
from ..workload.workload import WorkloadResult
from .charts import latency_percentile_figure
from .environment import benchmark_option_rows
from .formatting import format_duration, format_number

__all__ = ["report_workload_result", "traffic_shape", "workload_label"]


def traffic_shape(options: dict[str, Any]) -> str:
    """
    How the benchmark shaped its load, in the words the report's section headings use.
    """
    rate = options.get("request_rate")
    if rate is not None and str(rate).lower() not in ("inf", "infinity"):
        return "fixed traffic"
    if options.get("max_concurrency") is not None:
        return "fixed concurrency"
    return "burst traffic"


def workload_label(workload, shape: str | None = None) -> str:
    """
    ``Inferencex, agentic workload`` -- the workload and how it was driven.

    *shape* names the workload -- a suite choosing between a ``fixed`` and an ``agentic`` option
    set is varying the shape of each request.
    """
    name = getattr(workload, "workload_name", type(workload).__name__)
    options = getattr(workload, "benchmark_options", None)
    if shape:
        return f"{name}, {shape}"
    return f"{name}, {traffic_shape(options)}" if options else name


def _token_ratio(tokens_in: int | None, tokens_out: int | None) -> str:
    """
    Example ``8:1`` -- tokens in / tokens out ratio. A fixed workload will have a lower
    token ratio than an agentic workload.
    """
    if tokens_in is None or tokens_out is None or tokens_out == 0:
        return "-"
    ratio = tokens_in / tokens_out
    return f"{round(ratio):,}:1" if ratio >= 1 else f"{format_number(ratio)}:1"


def _benchmark_result_rows(bench: InferencexBenchmarkResult, result: WorkloadResult) -> list[list[str]]:
    """The measures worth comparing between two runs of the same benchmark."""
    latency = bench.latency_ms
    return [
        ["requests completed", format_number(bench.successful_requests, ",d"), ""],
        ["benchmark duration (timed requests)", format_duration(bench.duration_seconds), ""],
        ["tokens in (prompt)", format_number(bench.total_input_tokens, ",d"), "tokens"],
        ["tokens out (generated)", format_number(bench.total_generated_tokens, ",d"), "tokens"],
        ["tokens in/out ratio", _token_ratio(bench.total_input_tokens, bench.total_generated_tokens), "ratio"],
        ["throughput (requests)", format_number(bench.request_throughput), "req/s"],
        ["throughput (prompt+generated)", format_number(bench.total_token_throughput), "tok/s"],
        ["throughput (generated only)", format_number(bench.output_token_throughput), "tok/s"],
        ["TTFT mean", format_number(latency.get("mean_ttft")), "ms"],
        ["TTFT p99", format_number(latency.get("p99_ttft")), "ms"],
        ["TPOT mean", format_number(latency.get("mean_tpot")), "ms"],
        ["TPOT p99", format_number(latency.get("p99_tpot")), "ms"],
        ["container wall time (incl. startup/teardown)", format_duration(result.runtime), ""],
    ]


def report_workload_result(reporter, workload, result: WorkloadResult, shape: str | None = None) -> None:
    """
    Write *result* into *reporter*, in the shape that suits whatever the workload returned.

    *shape* is passed through to :func:`workload_label`.
    """
    label = workload_label(workload, shape)

    if options := getattr(workload, "benchmark_options", None):
        reporter.table(
            ["option", "value", "benchmark_serving flag"],
            benchmark_option_rows(options),
            title=f"Workload configuration -- {label}",
            left={2},
        )

    match result.result:
        case InferencexBenchmarkResult() as bench:
            reporter.table(
                ["measure", "value", "unit"],
                _benchmark_result_rows(bench, result),
                title=f"Workload result -- {label}",
                left={2},
            )

            reporter.figure(
                latency_percentile_figure(bench.latency_ms),
                title=f"Latency distribution -- {label}",
            )
        case InferenceResult() as inference:
            reporter.table(
                ["measure", "value"],
                [
                    ["characters generated", str(len(inference.text))],
                    ["usage", str(inference.usage)],
                    ["runtime", format_duration(result.runtime)],
                    ["text", inference.text],
                ],
                title=f"Workload result -- {label}",
                left={1},
            )
        case str() as text:
            reporter.output(text, title=f"Workload output -- {label} ({result.status.value})")
        case None:
            reporter.note(f"Workload result -- {label}: no result (stopped, or no parseable output)")
