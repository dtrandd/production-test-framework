# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.
"""
Default power, cost and token-price inputs for inference economics, and the metrics they derive.

Values marked ``InferenceX`` are the per-chip figures https://inferencex.semianalysis.com
publishes (``/api/v1/views/options`` and its chip pages), as of October 2026: TDP, all-in
provisioned watts (GPU plus its share of host CPU, NICs and cooling), and $/chip-hour for owning
at large hyperscaler volume and for renting. Values marked ``estimate`` fill in chips InferenceX
does not cover and should be overridden when a better figure is known.
"""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "COST_BASES",
    "DEFAULT_HOST_WATTS_PER_GPU",
    "DEFAULT_PUE",
    "ECONOMICS_METRICS",
    "GPU_ECONOMICS",
    "MODEL_TOKEN_PRICES",
    "X_AXIS_METRICS",
    "EconomicsInputs",
    "GpuEconomics",
    "TokenPrice",
    "economics_metrics",
    "estimate_all_in_watts",
    "lookup_gpu",
    "resolve_economics",
]

# Calibrated so H100 reproduces InferenceX's 1.37 kW: (700 W + 354 W) x 1.3.
DEFAULT_HOST_WATTS_PER_GPU = 350.0
# power_model's air-cooled facility PUE.
DEFAULT_PUE = 1.3

#: ``owning`` is InferenceX's default basis ("H" metrics); ``rental`` is its "R" variant.
COST_BASES = ("owning", "rental")


def estimate_all_in_watts(
    tdp_w: float, host_watts_per_gpu: float = DEFAULT_HOST_WATTS_PER_GPU, pue: float = DEFAULT_PUE
) -> float:
    """All-in provisioned watts per GPU: its TDP plus its host share, times facility PUE."""
    return (tdp_w + host_watts_per_gpu) * pue


@dataclass(frozen=True)
class GpuEconomics:
    """Per-chip power and cost defaults; ``None`` where no figure is known."""

    label: str
    vendor: str
    tdp_w: float
    all_in_w: float
    owning_cost_per_hour: float | None
    rental_cost_per_hour: float | None
    power_source: str
    cost_source: str
    aliases: tuple[str, ...] = ()


ESTIMATED_POWER = "estimate: (TDP + host share) x PUE"
MARKET_RENTAL = "estimate: market rental, October 2026"


def _estimated(label: str, vendor: str, tdp_w: float, rental: float, *aliases: str) -> GpuEconomics:
    return GpuEconomics(
        label, vendor, tdp_w, estimate_all_in_watts(tdp_w), None, rental, ESTIMATED_POWER, MARKET_RENTAL, aliases
    )


#: Keyed by a normalised name; see :func:`lookup_gpu`.
GPU_ECONOMICS: dict[str, GpuEconomics] = {
    "h100": GpuEconomics(
        "H100 SXM", "NVIDIA", 700, 1370, 1.17, 2.00, "InferenceX", "InferenceX", ("h100-sxm", "h100-80gb-hbm3")
    ),
    "h200": GpuEconomics("H200 SXM", "NVIDIA", 700, 1370, 1.22, 2.90, "InferenceX", "InferenceX", ("h200-sxm",)),
    "b200": GpuEconomics("B200", "NVIDIA", 1000, 1710, 1.73, 3.70, "InferenceX", "InferenceX"),
    "b300": GpuEconomics("B300", "NVIDIA", 1200, 1900, 2.26, 4.25, "InferenceX", "InferenceX"),
    "gb200": GpuEconomics(
        "GB200 NVL72", "NVIDIA", 1200, 1870, 1.86, 4.00, "InferenceX", "InferenceX", ("gb200-nvl72",)
    ),
    "gb300": GpuEconomics(
        "GB300 NVL72", "NVIDIA", 1400, 2120, 2.31, 5.00, "InferenceX", "InferenceX", ("gb300-nvl72",)
    ),
    "vr200": GpuEconomics(
        "Vera Rubin NVL72",
        "NVIDIA",
        1800,
        round(1800 * 2120 / 1400),
        3.61,
        8.50,
        "estimate: GB300's all-in/TDP ratio",
        "InferenceX",
        ("vera-rubin-nvl72",),
    ),
    "mi300x": GpuEconomics("MI300X", "AMD", 750, 1390, 0.95, 1.30, "InferenceX", "InferenceX", ("instinct-mi300x",)),
    "mi325x": GpuEconomics("MI325X", "AMD", 1000, 1690, 1.10, 1.60, "InferenceX", "InferenceX", ("instinct-mi325x",)),
    "mi355x": GpuEconomics("MI355X", "AMD", 1400, 2090, 1.50, 2.90, "InferenceX", "InferenceX", ("instinct-mi355x",)),
    "rtx-pro-6000-blackwell": GpuEconomics(
        "RTX PRO 6000 Blackwell",
        "NVIDIA",
        600,
        estimate_all_in_watts(600),
        0.68,
        0.52,
        ESTIMATED_POWER,
        "InferenceX",
        ("rtx6000pro", "rtx-pro-6000", "rtx-pro-6000-blackwell-server-edition"),
    ),
    "a100": _estimated("A100 80GB SXM", "NVIDIA", 400, 1.38, "a100-sxm4-80gb", "a100-80gb"),
    "l40s": _estimated("L40S", "NVIDIA", 350, 0.97),
    "l4": _estimated("L4", "NVIDIA", 72, 0.49),
    "rtx-pro-2000-blackwell": _estimated("RTX PRO 2000 Blackwell", "NVIDIA", 70, 0.17, "rtx-pro-2000"),
    "mi350x": _estimated("MI350X", "AMD", 1000, 3.40, "instinct-mi350x"),
}


@dataclass(frozen=True)
class TokenPrice:
    """List price in dollars per million tokens."""

    input: float
    output: float
    source: str


#: By served model name. InferenceX's fleet view defaults output to 4x the input price.
MODEL_TOKEN_PRICES: dict[str, TokenPrice] = {
    "Qwen/Qwen3-8B": TokenPrice(0.05, 0.20, "estimate: Qwen API list price"),
}


def _normalise(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"^(nvidia|amd)-", "", text)


def lookup_gpu(name: str | None) -> tuple[str, GpuEconomics] | None:
    """``(key, defaults)`` for a GPU named as a profile sku or as nvidia-smi/amd-smi prints it."""
    if not name:
        return None
    wanted = _normalise(name)
    for key, gpu in GPU_ECONOMICS.items():
        if wanted == key or wanted in gpu.aliases:
            return key, gpu
    return None


@dataclass(frozen=True)
class EconomicsInputs:
    """The inputs one run's metrics are computed from, each with where it came from."""

    gpu: str | None
    cost_basis: str
    all_in_watts_per_gpu: float | None
    cost_per_gpu_hour: float | None
    input_price_per_million: float | None
    output_price_per_million: float | None
    sources: Mapping[str, str]


def _number(overrides: Mapping[str, Any], key: str) -> float | None:
    value = overrides.get(key)
    return None if value is None else float(value)


def resolve_economics(
    gpu_name: str | None, model: str | None, overrides: Mapping[str, Any] | None = None
) -> EconomicsInputs:
    """
    The defaults for *gpu_name* and *model*, with *overrides* applied.

    *overrides* takes ``gpu``, ``cost_basis``, ``cost_per_gpu_hour``, ``input_price_per_million``,
    ``output_price_per_million`` and, for power, the first of: ``all_in_watts_per_gpu``;
    ``server_watts`` measured at the wall under load, spread over ``gpus_per_server`` and times
    ``pue``; ``host_watts_per_gpu`` and ``pue`` to re-estimate from TDP.
    """
    overrides = overrides or {}
    sources: dict[str, str] = {}
    found = lookup_gpu(overrides.get("gpu") or gpu_name)
    key, gpu = found if found else (None, None)

    basis = str(overrides.get("cost_basis") or "owning")
    if basis not in COST_BASES:
        raise ValueError(f"cost_basis must be one of {', '.join(COST_BASES)}, not {basis!r}")

    pue = _number(overrides, "pue")
    if (watts := _number(overrides, "all_in_watts_per_gpu")) is not None:
        sources["all_in_watts_per_gpu"] = "override"
    elif (server := _number(overrides, "server_watts")) is not None:
        gpus = _number(overrides, "gpus_per_server")
        if not gpus or gpus <= 0:
            raise ValueError("server_watts needs gpus_per_server, the GPUs that power is shared by")
        watts = server * (DEFAULT_PUE if pue is None else pue) / gpus
        sources["all_in_watts_per_gpu"] = "measured server_watts x pue / gpus_per_server"
    elif gpu is not None and ("host_watts_per_gpu" in overrides or "pue" in overrides):
        host = _number(overrides, "host_watts_per_gpu")
        watts = estimate_all_in_watts(
            gpu.tdp_w,
            DEFAULT_HOST_WATTS_PER_GPU if host is None else host,
            DEFAULT_PUE if pue is None else pue,
        )
        sources["all_in_watts_per_gpu"] = "estimated from TDP, host_watts_per_gpu and pue"
    elif gpu is not None:
        watts = gpu.all_in_w
        sources["all_in_watts_per_gpu"] = gpu.power_source
    else:
        watts = None

    if (cost := _number(overrides, "cost_per_gpu_hour")) is not None:
        sources["cost_per_gpu_hour"] = "override"
    elif gpu is not None:
        owning, rental = gpu.owning_cost_per_hour, gpu.rental_cost_per_hour
        cost = owning if basis == "owning" else rental
        if cost is None and (cost := rental if basis == "owning" else owning) is not None:
            basis = "rental" if basis == "owning" else "owning"
        if cost is not None:
            sources["cost_per_gpu_hour"] = f"{gpu.cost_source} ({basis})"

    price = MODEL_TOKEN_PRICES.get(model or "")
    prices = {}
    for side in ("input", "output"):
        field = f"{side}_price_per_million"
        if (value := _number(overrides, field)) is not None:
            sources[field] = "override"
        elif price is not None:
            value = getattr(price, side)
            sources[field] = price.source
        prices[side] = value

    return EconomicsInputs(
        gpu=key,
        cost_basis=basis,
        all_in_watts_per_gpu=watts,
        cost_per_gpu_hour=cost,
        input_price_per_million=prices["input"],
        output_price_per_million=prices["output"],
        sources=sources,
    )


#: (key, label, unit) for every y-axis metric, in InferenceX's naming.
ECONOMICS_METRICS = (
    ("tpPerGpu", "Token Throughput per Chip", "tok/s/chip"),
    ("inputTputPerGpu", "Input Token Throughput per Chip", "tok/s/chip"),
    ("outputTputPerGpu", "Output Token Throughput per Chip", "tok/s/chip"),
    ("tpPerMw", "Token Throughput per All in Utility MW", "tok/s/MW"),
    ("inputTputPerMw", "Input Token Throughput per All in Utility MW", "tok/s/MW"),
    ("outputTputPerMw", "Output Token Throughput per All in Utility MW", "tok/s/MW"),
    ("tokenRevenuePerGpuHour", "Token Revenue per GPU Hour", "$/GPU/hr"),
    ("tokensPerDollar", "Total Tokens per $1 TCO", "tok/$"),
    ("outputTokensPerDollar", "Output Tokens per $1 TCO", "tok/$"),
    ("inputTokensPerDollar", "Input Tokens per $1 TCO", "tok/$"),
    ("cost", "Cost per Million Total Tokens", "$"),
    ("costOutput", "Cost per Million Output Tokens", "$"),
    ("costInput", "Cost per Million Input Tokens", "$"),
    ("jTotal", "All-in Provisioned Joules per Total Token", "J/tok"),
    ("jOutput", "All-in Provisioned Joules per Output Token", "J/tok"),
    ("jInput", "All-in Provisioned Joules per Input Token", "J/tok"),
)

#: (key, label, unit, path into an AgentX scored aggregate's request_metrics.latency) per x-axis.
X_AXIS_METRICS = (
    ("e2e-normalized-interactivity", "E2E Normalized Interactivity", "tok/s/user", "e2e_norm_intvty"),
    ("interactivity", "Interactivity", "tok/s/user", "intvty"),
    ("e2e", "E2E Latency", "s", "e2el"),
    ("ttft", "TTFT", "s", "ttft"),
)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0 or not math.isfinite(denominator):
        return None
    return numerator / denominator


def economics_metrics(
    total_tps_per_gpu: float | None,
    input_tps_per_gpu: float | None,
    output_tps_per_gpu: float | None,
    inputs: EconomicsInputs,
) -> dict[str, float | None]:
    """Every :data:`ECONOMICS_METRICS` value, ``None`` where an input is missing."""
    throughput = {"total": total_tps_per_gpu, "input": input_tps_per_gpu, "output": output_tps_per_gpu}
    megawatts = None if inputs.all_in_watts_per_gpu is None else inputs.all_in_watts_per_gpu / 1e6
    cost = inputs.cost_per_gpu_hour
    per_hour = {side: None if tps is None else tps * 3600 for side, tps in throughput.items()}

    revenue = None
    if None not in (
        input_tps_per_gpu,
        output_tps_per_gpu,
        inputs.input_price_per_million,
        inputs.output_price_per_million,
    ):
        revenue = (
            (input_tps_per_gpu * inputs.input_price_per_million + output_tps_per_gpu * inputs.output_price_per_million)
            * 3600
            / 1e6
        )

    def cost_per_million(side: str) -> float | None:
        tokens = _ratio(per_hour[side], 1e6)
        return _ratio(cost, tokens)

    return {
        "tpPerGpu": total_tps_per_gpu,
        "inputTputPerGpu": input_tps_per_gpu,
        "outputTputPerGpu": output_tps_per_gpu,
        "tpPerMw": _ratio(total_tps_per_gpu, megawatts),
        "inputTputPerMw": _ratio(input_tps_per_gpu, megawatts),
        "outputTputPerMw": _ratio(output_tps_per_gpu, megawatts),
        "tokenRevenuePerGpuHour": revenue,
        "tokensPerDollar": _ratio(per_hour["total"], cost),
        "outputTokensPerDollar": _ratio(per_hour["output"], cost),
        "inputTokensPerDollar": _ratio(per_hour["input"], cost),
        "cost": cost_per_million("total"),
        "costOutput": cost_per_million("output"),
        "costInput": cost_per_million("input"),
        "jTotal": _ratio(inputs.all_in_watts_per_gpu, total_tps_per_gpu),
        "jOutput": _ratio(inputs.all_in_watts_per_gpu, output_tps_per_gpu),
        "jInput": _ratio(inputs.all_in_watts_per_gpu, input_tps_per_gpu),
    }
