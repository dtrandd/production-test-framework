# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

"""Unit tests for the GPU economics defaults and derived metrics."""

import pytest

from production_test_framework.gpu_economics import (
    ECONOMICS_METRICS,
    GPU_ECONOMICS,
    economics_metrics,
    estimate_all_in_watts,
    lookup_gpu,
    resolve_economics,
)
from production_test_framework.reporting import agentx as report_agentx
from production_test_framework.reporting.charts import scatter_figure
from production_test_framework.reporting.formatting import ReportFormat
from production_test_framework.reporting.report import Reporter


class TestLookupGpu:
    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("NVIDIA RTX PRO 2000 Blackwell", "rtx-pro-2000-blackwell"),
            ("rtx-pro-2000-blackwell", "rtx-pro-2000-blackwell"),
            ("NVIDIA H100 80GB HBM3", "h100"),
            ("AMD Instinct MI300X", "mi300x"),
            ("GB200 NVL72", "gb200"),
        ],
    )
    def test_names_from_smi_and_profiles_resolve(self, name, key):
        assert lookup_gpu(name)[0] == key

    def test_unknown_gpu_is_none(self):
        assert lookup_gpu("Voodoo 2") is None
        assert lookup_gpu(None) is None

    def test_every_entry_has_a_positive_tdp_and_all_in_above_it(self):
        for gpu in GPU_ECONOMICS.values():
            assert 0 < gpu.tdp_w < gpu.all_in_w


class TestAllInEstimate:
    def test_default_reproduces_the_published_h100_figure(self):
        assert estimate_all_in_watts(700) == pytest.approx(GPU_ECONOMICS["h100"].all_in_w, rel=0.01)


class TestResolveEconomics:
    def test_defaults_come_from_the_dictionary(self):
        inputs = resolve_economics("B200", "Qwen/Qwen3-8B")
        assert (inputs.gpu, inputs.cost_basis) == ("b200", "owning")
        assert inputs.all_in_watts_per_gpu == 1710
        assert inputs.cost_per_gpu_hour == 1.73
        assert inputs.input_price_per_million == 0.05
        assert inputs.sources["cost_per_gpu_hour"] == "InferenceX (owning)"

    def test_rental_basis_uses_the_rental_rate(self):
        assert resolve_economics("B200", None, {"cost_basis": "rental"}).cost_per_gpu_hour == 3.70

    def test_owning_falls_back_to_rental_when_unpublished(self):
        inputs = resolve_economics("rtx-pro-2000-blackwell", None)
        assert (inputs.cost_basis, inputs.cost_per_gpu_hour) == ("rental", 0.17)

    def test_overrides_win(self):
        inputs = resolve_economics(
            "b200",
            "Qwen/Qwen3-8B",
            {"all_in_watts_per_gpu": 1500, "cost_per_gpu_hour": 2.5, "output_price_per_million": 1.0},
        )
        assert (inputs.all_in_watts_per_gpu, inputs.cost_per_gpu_hour) == (1500, 2.5)
        assert (inputs.input_price_per_million, inputs.output_price_per_million) == (0.05, 1.0)
        assert inputs.sources["all_in_watts_per_gpu"] == "override"

    def test_host_share_and_pue_re_estimate_all_in(self):
        inputs = resolve_economics("rtx-pro-2000-blackwell", None, {"host_watts_per_gpu": 200, "pue": 1.2})
        assert inputs.all_in_watts_per_gpu == pytest.approx((70 + 200) * 1.2)

    def test_measured_server_power_is_shared_over_its_gpus(self):
        inputs = resolve_economics(
            "rtx-pro-2000-blackwell", None, {"server_watts": 600, "gpus_per_server": 2, "pue": 1.2}
        )
        assert inputs.all_in_watts_per_gpu == pytest.approx(600 * 1.2 / 2)
        assert inputs.sources["all_in_watts_per_gpu"].startswith("measured")

    def test_measured_server_power_needs_a_gpu_count(self):
        with pytest.raises(ValueError, match="gpus_per_server"):
            resolve_economics("b200", None, {"server_watts": 600})

    def test_gpu_override_selects_the_defaults(self):
        assert resolve_economics("unknown card", None, {"gpu": "h200"}).gpu == "h200"

    def test_unknown_gpu_and_model_leave_inputs_unset(self):
        inputs = resolve_economics("unknown card", "some/model")
        assert inputs.all_in_watts_per_gpu is None
        assert inputs.cost_per_gpu_hour is None
        assert inputs.input_price_per_million is None

    def test_bad_cost_basis_is_rejected(self):
        with pytest.raises(ValueError, match="cost_basis"):
            resolve_economics("b200", None, {"cost_basis": "leased"})


class TestEconomicsMetrics:
    def test_matches_inferencex_published_b200_cost(self):
        """InferenceX: B200 at 6,505 tok/s/GPU costs $0.074/M total tokens at owning pricing."""
        metrics = economics_metrics(6505, None, None, resolve_economics("b200", None))
        assert metrics["cost"] == pytest.approx(0.074, abs=0.0005)

    def test_every_metric_from_known_inputs(self):
        inputs = resolve_economics(
            None,
            None,
            {
                "all_in_watts_per_gpu": 1000,
                "cost_per_gpu_hour": 2.0,
                "input_price_per_million": 1.0,
                "output_price_per_million": 4.0,
            },
        )
        metrics = economics_metrics(1000, 900, 100, inputs)

        assert set(metrics) == {key for key, _, _ in ECONOMICS_METRICS}
        assert metrics["tpPerMw"] == pytest.approx(1e6)
        assert metrics["tokenRevenuePerGpuHour"] == pytest.approx((900 * 1 + 100 * 4) * 3600 / 1e6)
        assert metrics["tokensPerDollar"] == pytest.approx(1000 * 3600 / 2)
        assert metrics["costOutput"] == pytest.approx(2 / (100 * 3600) * 1e6)
        assert metrics["jInput"] == pytest.approx(1000 / 900)

    def test_missing_inputs_give_none(self):
        metrics = economics_metrics(1000, 900, 100, resolve_economics(None, None))
        assert metrics["tpPerGpu"] == 1000
        assert metrics["cost"] is None
        assert metrics["jTotal"] is None
        assert metrics["tokenRevenuePerGpuHour"] is None


SCORED = {
    "request_metrics": {
        "throughput": {"per_gpu": {"total_tput_tps": 330.0, "input_tput_tps": 317.0, "output_tput_tps": 13.0}},
        "latency": {
            "ttft": {"p90": 3.3},
            "e2el": {"p90": 40.9},
            "intvty": {"p90": 21.0},
            "e2e_norm_intvty": {"p90": 18.0},
        },
    }
}


class TestAgentxEconomicsReport:
    def test_x_values_use_the_percentile(self):
        _, x = report_agentx.agentx_economics(SCORED, resolve_economics(None, None))
        assert x == {"e2e-normalized-interactivity": 18.0, "interactivity": 21.0, "e2e": 40.9, "ttft": 3.3}

    def test_input_rows_say_what_is_not_configured(self):
        rows = report_agentx.agentx_economics_input_rows(resolve_economics("unknown", None))
        assert ["TCO", "not configured", "$/GPU/hr", ""] in rows

    def test_report_has_a_chart_per_metric(self, tmp_path):
        reporter = Reporter(tmp_path / "r.html", fmt=ReportFormat.HTML)
        reporter.start_test("t.py::a")
        inputs = resolve_economics("rtx-pro-2000-blackwell", "Qwen/Qwen3-8B")
        report_agentx._report_agentx_economics_tables(reporter, SCORED, inputs)
        report_agentx._report_agentx_economics_figures(reporter, SCORED, inputs, [("earlier", SCORED)])
        reporter.flush()
        html = reporter.path.read_text()

        assert "AgentX economics inputs" in html
        for _, label, _ in ECONOMICS_METRICS:
            assert f"{label} -- AgentX" in html
        assert "Cost per Million Total Tokens vs TTFT" in html


class TestScatterFigure:
    def test_empty_panels_are_dropped(self):
        assert scatter_figure([("a", "s", "tok", [])]) == ""

    def test_current_run_is_emphasised(self):
        figure = scatter_figure([("a", "s", "tok", [(1.0, 2.0, "old", False), (2.0, 3.0, "this run", True)])])
        assert figure.count("<circle") == 2
        assert "cb4" in figure
