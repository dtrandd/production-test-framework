# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

"""
Whether a Hugging Face model fits a machine for vLLM: its download in the disk's free space,
and its weights plus the KV cache of one full-length request in each GPU's memory.

Both are estimates made before anything is downloaded or started, so a run that cannot work
fails at once with the reason, rather than partway through a download or after loading the
weights. The sizes come from the Hugging Face API, or from the local cache when the model is
already there and the API cannot be reached.
"""

import fnmatch
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

__all__ = [
    "HF_URL",
    "ModelFootprint",
    "disk_shortfall",
    "gpu_shortfall",
    "model_footprint",
]

HF_URL = "https://huggingface.co"
HF_TIMEOUT = 30.0

GIB = 1024**3

#: What vLLM downloads from a repo: the first weight format it has, in this order (matched
#: anywhere in the repo, so gpt-oss-120b's original/ copy of its weights comes too), and the
#: small files beside them.
WEIGHT_PATTERNS = ("*.safetensors", "*.bin", "*.pt")
OTHER_PATTERNS = ("*.json", "*.model", "*.txt", "*.jinja", "*.tiktoken")

#: Memory vLLM's own allocations take on each GPU besides weights and KV cache: activations
#: and buffers, with --enforce-eager. Measured with Qwen3-8B at TP=2 as next to nothing, so
#: this is margin.
GPU_OVERHEAD_BYTES = int(0.5 * GIB)

_DTYPE_BYTES = {"float32": 4, "float16": 2, "bfloat16": 2}


@dataclass(frozen=True)
class ModelFootprint:
    """What a model takes: on disk, in GPU memory, and in KV cache per token."""

    repo: str
    #: Every file vLLM downloads, in bytes.
    download_bytes: int
    #: Of those, the bytes already in the local cache.
    cached_bytes: int
    #: The weights vLLM loads onto the GPUs: the top-level weight files.
    weight_bytes: int
    config: dict[str, Any]

    @property
    def missing_bytes(self) -> int:
        return max(self.download_bytes - self.cached_bytes, 0)

    def kv_bytes_per_token(self, tensor_parallel: int) -> int:
        """KV cache per token on each GPU: keys and values for every layer's share of KV heads."""
        config = self.config.get("text_config") or self.config
        heads = config["num_attention_heads"]
        kv_heads = config.get("num_key_value_heads") or heads
        head_dim = config.get("head_dim") or config["hidden_size"] // heads
        dtype = str(config.get("torch_dtype") or config.get("dtype") or "bfloat16")
        # A KV head is never split: past one per GPU, they are replicated.
        per_gpu = math.ceil(kv_heads / tensor_parallel)
        return 2 * config["num_hidden_layers"] * per_gpu * head_dim * _DTYPE_BYTES.get(dtype, 2)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _vllm_files(sizes: dict[str, int]) -> tuple[dict[str, int], dict[str, int]]:
    """``(downloaded, loaded)``: the files vLLM downloads, and of those the weights it loads."""
    for pattern in WEIGHT_PATTERNS:
        weights = {name: size for name, size in sizes.items() if fnmatch.fnmatch(name, pattern)}
        if weights:
            break
    others = {name: size for name, size in sizes.items() if _matches(name, OTHER_PATTERNS)}
    loaded = {name: size for name, size in weights.items() if "/" not in name}
    return {**others, **weights}, loaded


def _snapshot(cache_dir: Path, repo: str) -> Path | None:
    """The most recent local snapshot of *repo* in the Hugging Face cache, if any."""
    snapshots = cache_dir / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots"
    found = sorted(snapshots.glob("*"), key=lambda path: path.stat().st_mtime) if snapshots.is_dir() else []
    return found[-1] if found else None


def _cached_sizes(snapshot: Path | None) -> dict[str, int]:
    """Each complete file in *snapshot*: a snapshot's files link to blobs, missing until downloaded."""
    if snapshot is None:
        return {}
    return {
        str(path.relative_to(snapshot)): path.stat().st_size
        for path in snapshot.rglob("*")
        if path.is_file()  # follows the link; False while its blob is not there
    }


def model_footprint(
    repo: str, cache_dir: Path, token: str | None = None, timeout: float = HF_TIMEOUT
) -> ModelFootprint | None:
    """
    *repo*'s footprint, from the Hugging Face API, else from the copy in *cache_dir* (the
    ``HF_HOME`` directory) when it has its config. None when neither can say.
    """
    snapshot = _snapshot(cache_dir, repo)
    cached = _cached_sizes(snapshot)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        info = requests.get(f"{HF_URL}/api/models/{repo}?blobs=true", headers=headers, timeout=timeout)
        info.raise_for_status()
        sizes = {sibling["rfilename"]: int(sibling.get("size") or 0) for sibling in info.json()["siblings"]}
        config_response = requests.get(f"{HF_URL}/{repo}/resolve/main/config.json", headers=headers, timeout=timeout)
        config_response.raise_for_status()
        config = config_response.json()
    except requests.RequestException, ValueError, KeyError:
        if "config.json" not in cached:
            return None
        sizes = cached
        config = json.loads((snapshot / "config.json").read_text(encoding="utf-8"))

    downloaded, loaded = _vllm_files(sizes)
    return ModelFootprint(
        repo=repo,
        download_bytes=sum(downloaded.values()),
        cached_bytes=sum(size for name, size in downloaded.items() if cached.get(name) == size),
        weight_bytes=sum(loaded.values()),
        config=config,
    )


def _gb(value: float) -> str:
    return f"{value / 1e9:,.1f} GB"


def _gib(value: float) -> str:
    return f"{value / GIB:,.1f} GiB"


def disk_shortfall(footprint: ModelFootprint, cache_dir: Path, free_bytes: int) -> str | None:
    """Why the model's download cannot fit in *free_bytes* at *cache_dir*, or None if it can."""
    if footprint.missing_bytes <= free_bytes:
        return None
    return (
        f"{footprint.repo} needs {_gb(footprint.missing_bytes)} more in the Hugging Face cache {cache_dir} "
        f"({_gb(footprint.download_bytes)} of files, {_gb(footprint.cached_bytes)} already there), but that "
        f"disk has {_gb(free_bytes)} free. Free up space there, set HF_HOME to a disk with room, or use a "
        "smaller model."
    )


def gpu_shortfall(
    footprint: ModelFootprint,
    *,
    gpu_bytes: int,
    tensor_parallel: int,
    memory_utilization: float,
    max_model_len: int,
    worker: str = "the server",
) -> str | None:
    """
    Why the model cannot run on GPUs of *gpu_bytes* each at *tensor_parallel*, or None if it can:
    its weights do not fit, or what they leave cannot hold the KV cache of one *max_model_len*
    request, which vLLM needs before it will start.
    """
    usable = gpu_bytes * memory_utilization
    weights = footprint.weight_bytes / tensor_parallel
    kv_room = usable - weights - GPU_OVERHEAD_BYTES
    shape = (
        f"{footprint.repo} on {worker}: each of its {tensor_parallel} GPU(s) has {_gib(gpu_bytes)}, "
        f"{memory_utilization:.0%} of it usable ({_gib(usable)})"
    )
    if weights + GPU_OVERHEAD_BYTES > usable:
        return (
            f"{shape}, but would hold {_gib(weights)} of weights ({_gb(footprint.weight_bytes)} split "
            f"{tensor_parallel} ways). The model does not fit: use more GPUs (tensor_parallel), larger "
            "ones, or a smaller model."
        )
    kv_needed = max_model_len * footprint.kv_bytes_per_token(tensor_parallel)
    if kv_needed > kv_room:
        return (
            f"{shape}. Its {_gib(weights)} of weights leave {_gib(max(kv_room, 0))} for the KV cache, but one "
            f"{max_model_len:,}-token request needs {_gib(kv_needed)}. Lower max_model_len in vllm_args, use "
            "more GPUs (tensor_parallel), or a smaller model."
        )
    return None
