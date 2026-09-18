# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
Report a finished workload -- how it was configured, and what it produced.

Lives here rather than in a test so that every suite running the same workload publishes the
same two tables. A telemetry test and a validation test that both drive InferenceX should be
comparable line for line; two hand-rolled tables drift apart on the first change to either.
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

    ``benchmark_serving.py`` offers two independent knobs and the distinction matters when
    reading a latency number: a fixed arrival rate measures the system under a stated offered
    load, a fixed concurrency measures it under a stated number of in-flight requests, and
    neither one set means every request is dispatched at once.
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

    The shape is part of the label because a suite commonly runs one workload under several
    of them, and two sections both called "Workload result -- Inferencex" tell a reader
    nothing about which is which.

    *shape* names it outright, for a caller that already knows -- a suite choosing between a
    ``fixed`` and an ``agentic`` option set is varying the shape of each request, which is not
    a distinction the resulting flags can be read back out of. Left None, it falls back to
    :func:`traffic_shape`.

    The two describe different axes and should not borrow each other's words. What
    :func:`traffic_shape` infers is how requests *arrive* ("fixed traffic" = a fixed arrival
    rate); what a caller passes here is usually how each request is *built*, for which
    "workload" is the honest noun.
    """
    name = getattr(workload, "workload_name", type(workload).__name__)
    options = getattr(workload, "benchmark_options", None)
    if shape:
        return f"{name}, {shape}"
    return f"{name}, {traffic_shape(options)}" if options else name


def _token_ratio(tokens_in: int | None, tokens_out: int | None) -> str:
    """
    ``8:1`` -- prompt tokens per generated token.

    The single number that says which way a run leans: a high ratio is prefill-heavy work
    (an agent with a long context and a short answer), a low one is decode-heavy. It is worth
    a row of its own because the two totals it comes from are large and hard to divide by eye.

    Rounded to a whole number, which is the granularity the figure is read at -- the exact
    totals sit in the two rows above it. Below 1:1 it keeps its decimals instead, because a
    decode-heavy run rounds to ``0:1`` and that says nothing at all.

    "-" when either total is missing, and when nothing was generated -- a ratio against zero
    is unbounded rather than large, and printing a huge number would read as a measurement.
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

    A benchmark gets its configuration as well as its numbers: a throughput figure with no
    record of the offered load that produced it cannot be compared against anything. The
    configuration goes in whatever the outcome, because a run that failed is the one whose
    settings someone needs to read -- the endpoint it aimed at and the load it asked for are
    usually the answer to why.

    *shape* is passed through to :func:`workload_label`.
    """
    label = workload_label(workload, shape)

    if options := getattr(workload, "benchmark_options", None):
        reporter.table(
            ["option", "value", "benchmark_serving.py flag"],
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
            # Straight after the table it belongs to: the table states mean and p99, the
            # figure shows the shape between and beyond them.
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
            # A workload that raised stores the command's stderr here, which is the benchmark's
            # own diagnosis -- "request failure rate 100.0% (0/256 completed)" and the like.
            # Verbatim and preformatted: it arrives as many lines, and a note would flatten it.
            reporter.output(text, title=f"Workload output -- {label} ({result.status.value})")
        case None:
            reporter.note(f"Workload result -- {label}: no result (stopped, or no parseable output)")
