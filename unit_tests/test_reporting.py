# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.

"""Unit tests for the run report: grouping, failures, the header and the logo."""

import pytest

from production_test_framework.reporting.assets import Logo, load_logo
from production_test_framework.reporting.charts import MIN_BARS, latency_percentile_figure
from production_test_framework.reporting.environment import benchmark_option_rows, git_rows, runner_rows
from production_test_framework.reporting.formatting import ReportFormat
from production_test_framework.reporting.report import (
    DEFAULT_TITLE,
    UNCATEGORIZED,
    Reporter,
    failure_snippet,
    summary_line,
)
from production_test_framework.reporting.workload import (
    _token_ratio,
    report_workload_result,
    traffic_shape,
    workload_label,
)
from production_test_framework.workload.workload import WorkloadResult, WorkloadStatus

TRACEBACK = """self = <TestServices object at 0x7f3a>

    def test_grafana(self, grafana):
>       assert resp.status_code == 200
E       assert 503 == 200
E        +  where 503 = <Response [503]>.status_code

lgtm/test_services.py:88: AssertionError"""


@pytest.fixture
def report(tmp_path):
    """A reporter writing HTML to a temporary file, with three categories configured."""
    return Reporter(
        tmp_path / "report.html",
        fmt=ReportFormat.HTML,
        category_order=["lgtm", "vpc_api", "disruptive"],
    )


def rendered(reporter):
    reporter.flush()
    return reporter.path.read_text(encoding="utf-8")


class TestSummaryLine:
    """The one line of a traceback shown in the failure table."""

    def test_takes_the_first_e_line_not_the_last(self):
        """The assertion itself, not the trailing explanation of where a value came from."""
        assert summary_line(TRACEBACK) == "assert 503 == 200"

    def test_falls_back_to_the_exception_when_there_are_no_e_lines(self):
        text = 'File "x.py", line 3\n    raise RuntimeError("boom")\nRuntimeError: boom'
        assert summary_line(text) == "RuntimeError: boom"

    def test_empty_text_yields_empty_string(self):
        assert summary_line("   \n  ") == ""


class TestFailureSnippet:
    """Tracebacks are truncated from the front, keeping the error at the end."""

    def test_short_text_is_untouched(self):
        assert failure_snippet("boom", max_chars=100) == "boom"

    def test_long_text_keeps_the_tail(self):
        text = "A" * 50 + "THE ERROR"
        snippet = failure_snippet(text, max_chars=20)
        assert snippet.endswith("THE ERROR")
        assert "truncated" in snippet


class TestCategories:
    """Results are grouped into sections, ordered as the run configured them."""

    def test_sections_follow_the_configured_order_not_the_run_order(self, report):
        report.record_outcome("t.py::c", "passed", 1.0, category="disruptive")
        report.record_outcome("t.py::a", "passed", 1.0, category="lgtm")
        report.record_outcome("t.py::b", "passed", 1.0, category="vpc_api")

        html = rendered(report)
        assert html.index(">lgtm<") < html.index(">vpc_api<") < html.index(">disruptive<")

    def test_an_unconfigured_category_still_gets_a_section_after_the_configured_ones(self, report):
        report.record_outcome("t.py::a", "passed", 1.0, category="lgtm")
        report.record_outcome("t.py::b", "passed", 1.0, category="surprise")

        html = rendered(report)
        assert html.index(">lgtm<") < html.index(">surprise<")

    def test_the_catch_all_sorts_last_of_all(self, report):
        report.record_outcome("t.py::a", "passed", 1.0)
        report.record_outcome("t.py::b", "passed", 1.0, category="zzz_unconfigured")

        html = rendered(report)
        assert html.index(">zzz_unconfigured<") < html.index(f">{UNCATEGORIZED}<")

    def test_counts_are_reported_per_category(self, report):
        report.record_outcome("t.py::a", "passed", 1.0, category="lgtm")
        report.record_outcome("t.py::b", "failed", 1.0, TRACEBACK, "lgtm")

        assert "1 failed, 1 passed" in rendered(report)


class TestFailures:
    """Every failure is tabulated; only the first few carry a traceback."""

    def test_table_lists_every_failure_while_snippets_are_capped(self, tmp_path):
        reporter = Reporter(tmp_path / "r.html", max_failure_snippets=2)
        for index in range(5):
            reporter.record_outcome(f"t.py::test_{index}", "failed", 1.0, TRACEBACK, "lgtm")

        html = rendered(reporter)
        assert "Failures (5)" in html
        # Each failure contributes one table row; only two get a <pre> traceback.
        for index in range(5):
            assert f"test_{index}" in html
        assert html.count("pre class='failure'") == 2
        assert "3 further failure(s)" in html

    def test_an_error_counts_as_a_failure(self, report):
        report.record_outcome("t.py::a", "error", 1.0, TRACEBACK, "lgtm")
        assert "Failures (1)" in rendered(report)

    def test_no_failure_section_when_everything_passed(self, report):
        report.record_outcome("t.py::a", "passed", 1.0, category="lgtm")
        assert "Failures" not in rendered(report)

    def test_a_traceback_appears_once_not_in_the_details_as_well(self, report):
        report.start_test("t.py::a")
        report.table(["k", "v"], [["x", "1"]], title="Some detail")
        report.record_outcome("t.py::a", "failed", 1.0, TRACEBACK, "lgtm")

        assert rendered(report).count("pre class='failure'") == 1


class TestHeader:
    """Title and logo."""

    def test_default_title(self, tmp_path):
        assert DEFAULT_TITLE in rendered(Reporter(tmp_path / "r.html"))

    def test_title_is_configurable(self, tmp_path):
        assert "Nightly SV" in rendered(Reporter(tmp_path / "r.html", title="Nightly SV"))

    def test_the_bundled_logo_is_inlined_and_theme_aware(self, tmp_path):
        html = rendered(Reporter(tmp_path / "r.html"))
        # Inlined rather than linked, so the report survives being copied anywhere, and on
        # currentColor so the wordmark follows the theme.
        assert "<svg" in html
        assert "currentColor" in html
        assert "src=" not in html.split("</header>")[0]

    def test_an_empty_logo_renders_no_image(self, tmp_path):
        html = rendered(Reporter(tmp_path / "r.html", logo=Logo("")))
        assert "class='logo'" not in html

    def test_markdown_carries_the_title_but_no_logo(self, tmp_path):
        reporter = Reporter(tmp_path / "r.md", fmt=ReportFormat.MD, title="Nightly SV")
        text = rendered(reporter)
        assert text.startswith("# Nightly SV")
        assert "<svg" not in text


class TestLoadLogo:
    """A logo the report cannot read must never break the run."""

    def test_a_missing_file_degrades_to_no_logo(self, tmp_path):
        assert load_logo(tmp_path / "absent.svg").is_empty

    def test_an_unrecognised_type_degrades_to_no_logo(self, tmp_path):
        path = tmp_path / "logo.txt"
        path.write_text("not an image")
        assert load_logo(path).is_empty

    def test_a_raster_logo_is_embedded_as_a_data_uri(self, tmp_path):
        path = tmp_path / "logo.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        assert "data:image/png;base64," in load_logo(path).markup

    def test_an_svg_loses_its_xml_prolog_so_it_can_be_inlined(self, tmp_path):
        path = tmp_path / "logo.svg"
        path.write_text('<?xml version="1.0"?><svg viewBox="0 0 10 10"><rect/></svg>')
        markup = load_logo(path).markup
        assert markup.startswith("<svg")
        assert "<?xml" not in markup

    def test_width_and_height_give_way_to_a_viewbox(self, tmp_path):
        """Height comes from CSS; an SVG with no viewBox could not scale at all."""
        path = tmp_path / "logo.svg"
        path.write_text('<svg width="200" height="50"><rect/></svg>')
        markup = load_logo(path).markup
        assert 'viewBox="0 0 200 50"' in markup
        assert "width=" not in markup.split(">")[0]


class TestTrafficShape:
    """The label that distinguishes one InferenceX run from another."""

    def test_a_finite_request_rate_is_fixed_traffic(self):
        assert traffic_shape({"request_rate": 8}) == "fixed traffic"

    def test_an_infinite_request_rate_is_not(self):
        assert traffic_shape({"request_rate": "inf"}) == "burst traffic"

    def test_concurrency_without_a_rate_is_fixed_concurrency(self):
        assert traffic_shape({"max_concurrency": 32}) == "fixed concurrency"

    def test_neither_is_burst(self):
        assert traffic_shape({"num_prompts": 100}) == "burst traffic"


class TestWorkloadLabel:
    """The label a report's workload sections are headed with."""

    class _Workload:
        workload_name = "Inferencex"
        benchmark_options = {"max_concurrency": 8}

    def test_an_explicit_shape_wins_over_what_the_flags_imply(self):
        """A fixed/agentic choice is not recoverable from the flags it produces."""
        assert workload_label(self._Workload(), "agentic workload") == "Inferencex, agentic workload"

    def test_without_one_the_shape_is_inferred(self):
        assert workload_label(self._Workload()) == "Inferencex, fixed concurrency"

    def test_a_workload_with_no_options_is_named_alone(self):
        class Bare:
            workload_name = "Nccl"

        assert workload_label(Bare()) == "Nccl"


class TestTokenRatio:
    """Prompt tokens per generated token, the row that says which way a run leans."""

    def test_prefill_heavy_run(self):
        assert _token_ratio(262144, 32768) == "8:1"

    def test_agentic_run(self):
        assert _token_ratio(294912, 2048) == "144:1"

    def test_it_rounds_to_a_whole_number(self):
        assert _token_ratio(1700, 200) == "8:1"  # 8.5 -> 8
        assert _token_ratio(1760, 200) == "9:1"  # 8.8 -> 9

    def test_below_one_to_one_it_keeps_its_decimals(self):
        """A decode-heavy run rounds to 0:1, which says nothing at all."""
        assert _token_ratio(1024, 4096) == "0.25:1"

    def test_large_ratios_are_grouped(self):
        assert _token_ratio(10_000_000, 1000) == "10,000:1"

    def test_nothing_generated_is_not_an_enormous_ratio(self):
        """A ratio against zero is unbounded; printing a big number would read as a measure."""
        assert _token_ratio(1024, 0) == "-"

    @pytest.mark.parametrize("pair", [(None, 100), (100, None), (None, None)])
    def test_a_missing_total_yields_a_dash(self, pair):
        assert _token_ratio(*pair) == "-"


class TestBenchmarkOptionRows:
    """Options are shown with the flag each becomes, so a run can be reproduced by hand."""

    def test_load_shaping_options_come_first(self):
        rows = benchmark_option_rows({"backend": "vllm", "num_prompts": 10})
        assert [row[0] for row in rows] == ["num_prompts", "backend"]

    def test_none_is_shown_as_an_omitted_flag(self):
        assert benchmark_option_rows({"host": None}) == [["host", "unset", "(omitted)"]]

    def test_a_true_boolean_becomes_a_bare_flag(self):
        assert benchmark_option_rows({"ignore_eos": True}) == [["ignore_eos", "true", "--ignore-eos"]]

    def test_a_false_boolean_emits_nothing(self):
        assert benchmark_option_rows({"ignore_eos": False}) == [["ignore_eos", "false", "(omitted)"]]

    def test_container_local_result_paths_are_left_out(self):
        """Fixed by the workload, identical every run, and not part of the load offered."""
        rows = benchmark_option_rows(
            {"num_prompts": 10, "result_dir": ".", "result_filename": "inferencex-result.json"}
        )
        assert [row[0] for row in rows] == ["num_prompts"]


class TestRunnerRows:
    """The machine the tests ran on."""

    def test_python_is_a_version_not_an_interpreter_path(self):
        rows = dict((label, value) for label, value in runner_rows())
        assert rows["python"].count(".") == 2, rows["python"]
        assert "/" not in rows["python"]


class TestGitRows:
    """The checkout under test, and never an exception."""

    def test_a_directory_that_is_not_a_checkout_says_so(self, tmp_path):
        ((label, value),) = git_rows(tmp_path)
        assert label == "code under test"
        assert "not collected" in value


class TestStdoutMode:
    """Without a path the tables print instead, and nothing is written."""

    def test_no_file_is_written(self, capsys):
        reporter = Reporter(None)
        assert reporter.writes_file is False
        reporter.table(["a"], [["1"]], title="A table")
        assert "A table" in capsys.readouterr().out


class TestReportWorkloadResult:
    """What a workload contributes to the report, on each way it can end."""

    class _Workload:
        workload_name = "Inferencex"
        benchmark_options = {"num_prompts": 256, "host": "localhost", "port": 8080}

    @staticmethod
    def _result(payload, status=WorkloadStatus.COMPLETED):
        return WorkloadResult(start_time=0.0, end_time=30.0, result=payload, status=status)

    def test_a_failed_run_still_reports_its_configuration(self, report):
        """The settings are the usual answer to why a run failed, so they must survive it."""
        report.start_test("t.py::a")
        report_workload_result(report, self._Workload(), self._result("boom", WorkloadStatus.ERROR))

        html = rendered(report)
        assert "Workload configuration -- Inferencex, burst traffic" in html
        assert "--num-prompts 256" in html

    def test_command_output_is_preformatted_not_a_paragraph(self, report):
        """stderr arrives as many lines; a note would collapse them into one."""
        report.start_test("t.py::a")
        output = "Request failed: ConnectionError\nFAIL: request failure rate 100.0%"
        report_workload_result(report, self._Workload(), self._result(output, WorkloadStatus.ERROR))

        html = rendered(report)
        assert "pre class='output'" in html
        assert "FAIL: request failure rate 100.0%" in html
        assert "Workload output -- Inferencex, burst traffic (error)" in html

    def test_long_output_keeps_its_tail(self, report):
        """The line that says what went wrong is the last one."""
        report.start_test("t.py::a")
        noise = "\n".join("Request failed" for _ in range(2000))
        report_workload_result(
            report, self._Workload(), self._result(f"{noise}\nFAIL: nothing served", WorkloadStatus.ERROR)
        )

        html = rendered(report)
        assert "FAIL: nothing served" in html
        assert "truncated" in html

    def test_a_stopped_run_with_no_output_says_so(self, report):
        report.start_test("t.py::a")
        report_workload_result(report, self._Workload(), self._result(None, WorkloadStatus.STOPPED))
        assert "no result (stopped, or no parseable output)" in rendered(report)


class TestLatencyFigure:
    """The latency small multiples: one panel per family, each on its own scale."""

    FULL = {
        f"{p}_{fam}": value
        for fam, base in (("ttft", 300.0), ("tpot", 30.0), ("itl", 31.0), ("e2el", 4300.0))
        for p, value in (
            ("mean", base),
            ("median", base),
            ("p90", base * 1.2),
            ("p99", base * 1.4),
            ("p99_9", base * 1.5),
        )
    }

    def test_one_panel_per_family(self):
        svg = latency_percentile_figure(self.FULL)
        for name in ("TTFT", "TPOT", "ITL", "E2EL"):
            assert f"<b>{name}</b>" in svg
        assert svg.count("<figure class='chart'>") == 4

    def test_a_family_with_one_value_is_not_charted(self):
        """A one-bar bar chart is a number; the result table already states it."""
        assert latency_percentile_figure({"mean_ttft": 12.0}) == ""

    def test_two_values_are_enough(self):
        svg = latency_percentile_figure({"mean_ttft": 10.0, "p99_ttft": 20.0})
        assert svg.count("<figure class='chart'>") == 1
        assert MIN_BARS == 2

    def test_nothing_to_draw_yields_nothing(self):
        assert latency_percentile_figure({}) == ""
        assert latency_percentile_figure({"request_throughput": 5.0}) == ""

    def test_std_is_not_a_percentile(self):
        """A standard deviation is not a point on the latency scale."""
        svg = latency_percentile_figure({"mean_ttft": 10.0, "p99_ttft": 20.0, "std_ttft": 3.0})
        assert ">std<" not in svg

    def test_each_family_is_scaled_independently(self):
        """TPOT and E2EL differ by two orders of magnitude; a shared axis would erase TPOT."""
        import re

        svg = latency_percentile_figure(self.FULL)
        # Each panel divides by its own maximum, so the longest bar of every panel reaches the
        # same x -- whatever that family's numbers are. (H stops a corner radius short of the
        # bar's end, which is why this is not the full span.)
        per_panel = [
            max(float(m) for m in re.findall(r"M46,[\d.]+ H([\d.]+)", panel))
            for panel in svg.split("<figure class='chart'>")[1:]
        ]
        assert len(per_panel) == 4
        assert len(set(per_panel)) == 1, per_panel

    def test_every_bar_carries_its_value_and_a_tooltip(self):
        svg = latency_percentile_figure({"mean_ttft": 10.0, "p99_ttft": 20.0})
        assert "10 ms" in svg and "20 ms" in svg
        assert "<title>TTFT mean: 10 ms</title>" in svg

    def test_the_figure_reaches_the_report_only_in_html(self, tmp_path):
        from production_test_framework.reporting.formatting import ReportFormat

        for fmt, ext, present in ((ReportFormat.HTML, "html", True), (ReportFormat.MD, "md", False)):
            reporter = Reporter(tmp_path / f"r.{ext}", fmt=fmt)
            reporter.start_test("t.py::a")
            reporter.figure(latency_percentile_figure(self.FULL), title="Latency distribution")
            body = rendered(reporter)
            assert ("<figure class='chart'>" in body) is present
