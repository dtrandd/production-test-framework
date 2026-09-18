# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
"""
Description of the machine and the checkout a run happened on, for the report's header.

Everything here is deployment-agnostic: it describes the runner, the code under test and the
GPUs of a host reached over SSH. What a run was *pointed at* -- a hardware profile, a cluster,
a set of endpoints -- differs per suite and is assembled by that suite, on top of these rows.

No function here raises. The environment table is context for a result, so a missing driver,
an absent git checkout or an unreachable host must never turn a passing run into an error --
each one degrades to a row saying what could not be collected and why.
"""

import os
import platform
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..helper import is_localhost

__all__ = [
    "benchmark_option_rows",
    "ci_rows",
    "command_output",
    "display_path",
    "git_rows",
    "gpu_rows",
    "parse_driver_versions",
    "runner_rows",
    "ssh_target",
]


def display_path(path: Path) -> str:
    """
    A path safe to put in a shared report: repo-relative, with the private prefix dropped.

    Relative to the enclosing git repository -- prefixed with the repository's own directory
    name, so the result reads the way anyone with a checkout would recognise it.
    """
    resolved = Path(path).resolve()
    for parent in (resolved, *resolved.parents):
        if (parent / ".git").exists():
            return str(Path(parent.name) / resolved.relative_to(parent))
    return f".../{resolved.parent.name}/{resolved.name}"


def command_output(argv: list[str], timeout: float = 5.0, cwd: Path | None = None) -> tuple[str | None, str]:
    """
    ``(stdout, reason)`` for *argv* -- stdout is None on any failure, and *reason* says why.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd)
    except FileNotFoundError:
        return None, f"{argv[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:g}s"
    except OSError as exc:
        return None, str(exc)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return None, detail[0] if detail else f"exit status {proc.returncode}"
    output = proc.stdout.strip()
    return (output, "") if output else (None, "command produced no output")


# =============================================================================
# The runner, the checkout and the CI job
# =============================================================================


def runner_rows() -> list[list[str]]:
    """The machine the tests ran on, and the interpreter they ran under."""
    return [
        ["test runner", f"{platform.node()}  ({platform.system()} {platform.release()})"],
        ["python", platform.python_version()],
    ]


def git_rows(path: Path | None = None, label: str = "code under test") -> list[list[str]]:
    """
    Which commit of *path*'s checkout the run used, and whether it was modified.

    A nightly result is only reproducible if the report says what was run, so a dirty tree is
    recorded as such rather than quietly reported as its last commit.
    """
    root = Path(path) if path is not None else Path.cwd()
    describe, reason = command_output(["git", "-C", str(root), "rev-parse", "--short", "HEAD"])
    if describe is None:
        return [[label, f"not collected -- {reason}"]]

    branch, _ = command_output(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"])
    status, _ = command_output(["git", "-C", str(root), "status", "--porcelain"], timeout=15.0)
    # command_output reports "no output" as a failure, which for `status --porcelain` is
    # exactly the clean case -- so a None status here means clean, not unknown.
    state = "" if status is None else f", {len(status.splitlines())} file(s) modified"
    detail = f"{describe}" + (f" on {branch}" if branch and branch != "HEAD" else "") + state
    return [[label, detail]]


#: GitLab CI's predefined variables, which is what the nightly runs under. Each row is
#: omitted when its variable is unset, so a laptop run simply has no CI rows.
_CI_VARIABLES = (
    ("CI pipeline", "CI_PIPELINE_URL"),
    ("CI job", "CI_JOB_URL"),
    ("CI commit branch", "CI_COMMIT_REF_NAME"),
    ("CI runner", "CI_RUNNER_DESCRIPTION"),
)


def ci_rows() -> list[list[str]]:
    """Links back to the CI pipeline that produced this report, when there was one."""
    return [[label, os.environ[name]] for label, name in _CI_VARIABLES if os.environ.get(name)]


# =============================================================================
# GPU, CUDA and kernel-module versions, collected over SSH
# =============================================================================


def parse_driver_versions(version_text: str) -> dict[str, str]:
    """
    Pull the version fields out of ``nvidia-smi --version`` or a plain ``nvidia-smi`` header.

    Three generations of output are handled, because which one a node prints depends on its
    driver and all three are still in the field: 610+, 5xx, and older builds with no --version.
    """
    raw: dict[str, str] = {}
    for key, pattern in (
        ("nvidia-smi", r"NVIDIA-SMI version\s*:\s*(\S+)"),
        ("NVML", r"NVML version\s*:\s*(\S+)"),
        ("KMD", r"KMD version\s*:\s*(\S+)"),
        ("cuda_umd", r"CUDA UMD version\s*:\s*(\S+)"),
        ("driver_field", r"DRIVER version\s*:\s*(\S+)"),
        ("cuda_field", r"^\s*CUDA version\s*:\s*(\S+)"),
        ("driver_header", r"Driver Version:\s*(\S+)"),
        ("cuda_header", r"CUDA Version:\s*(\S+)"),
    ):
        if match := re.search(pattern, version_text, re.IGNORECASE | re.MULTILINE):
            value = match.group(1)

            if not value.lower().startswith("deprecated"):
                raw[key] = value

    found = {key: raw[key] for key in ("nvidia-smi", "NVML", "KMD") if key in raw}

    if driver := raw.get("driver_field") or raw.get("driver_header") or raw.get("KMD"):
        found["driver"] = driver
    if cuda := raw.get("cuda_umd") or raw.get("cuda_field") or raw.get("cuda_header"):
        found["CUDA"] = cuda
    return found


_GPU_INFO_SCRIPT = (
    "nvidia-smi --version 2>/dev/null || nvidia-smi 2>/dev/null; "
    "echo '===KMD==='; cat /proc/driver/nvidia/version 2>/dev/null; "
    "echo '===GPUS==='; nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader 2>/dev/null"
)


def ssh_target(host: str) -> str:
    """
    ``user@host`` for the probe, or a bare host so ``~/.ssh/config`` decides.
    """
    user = os.getenv("GPU_INFO_SSH_USER") or os.getenv("SSH_USER")
    return f"{user}@{host}" if user else host


def _remote_output(host: str, script: str, timeout: float = 25.0) -> tuple[str | None, str]:
    """
    Run *script* on *host* over SSH, returning ``(stdout, reason)``.
    """
    return command_output(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=accept-new",
            ssh_target(host),
            script,
        ],
        timeout=timeout,
    )


def gpu_rows(host: str | None) -> list[list[str]]:
    """
    GPU, CUDA and kernel-module versions from *host*.

    Run straight through a shell when *host* is this machine, and over SSH otherwise. A
    single-box deployment names its endpoint ``localhost``, and asking sshd for what a
    subprocess can answer directly needs a key and a running sshd that such a box may not have.

    ``GPU_INFO_HOST`` overrides *host*, so one run can be pointed at a different node without
    editing the caller.
    """
    host = os.getenv("GPU_INFO_HOST") or host
    if not host:
        return [["GPU driver", "not collected -- no head node host to query (set GPU_INFO_HOST)"]]

    local = is_localhost(host)
    label = f"this host {platform.node()}" if local else f"head node {host}"
    if local:
        output, reason = command_output(["sh", "-c", _GPU_INFO_SCRIPT], timeout=25.0)
    else:
        output, reason = _remote_output(host, _GPU_INFO_SCRIPT)
    if output is None:
        detail = reason.rstrip(". ")
        if local:
            return [["GPU driver", f"not collected -- {detail}"]]
        auth_failure = any(word in reason.lower() for word in ("denied", "authentication"))
        hint = (
            " Set GPU_INFO_SSH_USER if the cluster login differs."
            if auth_failure and not os.getenv("GPU_INFO_SSH_USER")
            else ""
        )
        return [["GPU driver", f"not collected -- ssh {ssh_target(host)}: {detail}.{hint}"]]

    version_text, _, rest = output.partition("===KMD===")
    kmd_text, _, inventory = rest.partition("===GPUS===")

    versions = parse_driver_versions(version_text)

    if "KMD" not in versions and (match := re.search(r"NVRM version:.*?(\d+\.\d+(?:\.\d+)?)", kmd_text)):
        versions["KMD"] = match.group(1)

    rows: list[list[str]] = []
    for name in ("driver", "CUDA", "KMD", "nvidia-smi", "NVML"):
        if name in versions:
            rows.append([f"{name} version ({label})", versions[name]])
    if "open kernel module" in kmd_text.lower():
        rows.append([f"KMD variant ({label})", "open kernel modules"])

    gpu_lines = [line for line in inventory.splitlines() if "," in line]
    if gpu_lines:
        names = sorted({line.split(",", 2)[1].strip() for line in gpu_lines})
        rows.append([f"GPUs ({label})", f"{len(gpu_lines)}x {', '.join(names)}"])

    if not rows:
        rows.append(["GPU driver", f"not collected -- {label} answered but reported no NVIDIA driver"])
    return rows


# =============================================================================
# Benchmark options
# =============================================================================

#: Where the benchmark writes its own result file inside its container. Fixed by the workload
#: rather than chosen per run, and nothing about the load offered, so the configuration table
#: leaves them out -- the point of that table is what someone would need to reproduce the run.
_OMITTED_BENCHMARK_OPTIONS = frozenset({"result_dir", "result_filename"})

_BENCHMARK_OPTION_ORDER = (
    "num_prompts",
    "max_concurrency",
    "request_rate",
    "random_input_len",
    "random_output_len",
    "num_warmups",
    "dataset_name",
    "model",
    "backend",
    "ignore_eos",
)


def benchmark_option_rows(options: Mapping[str, Any]) -> list[list[str]]:
    """
    Benchmark options as (option, value, flag) rows, load-shaping options first.

    The flag column is not decoration: these keys are converted to ``benchmark_serving.py``
    arguments generically, so showing the conversion is what lets a reader reproduce the run
    by hand.
    """

    def rank(item: tuple[int, str]) -> tuple[int, int]:
        position, key = item
        listed = _BENCHMARK_OPTION_ORDER.index(key) if key in _BENCHMARK_OPTION_ORDER else len(_BENCHMARK_OPTION_ORDER)
        return listed, position

    rows: list[list[str]] = []
    for _, key in sorted(enumerate(options), key=rank):
        if key in _OMITTED_BENCHMARK_OPTIONS:
            continue
        value = options[key]
        flag = "--" + key.replace("_", "-")
        if value is None:
            rows.append([key, "unset", "(omitted)"])
        elif isinstance(value, bool):
            rows.append([key, "true" if value else "false", flag if value else "(omitted)"])
        else:
            rows.append([key, str(value), f"{flag} {value}"])
    return rows
