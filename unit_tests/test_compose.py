# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

import json
import subprocess
from unittest.mock import patch

import pytest

from production_test_framework.compose import ComposeStack

_CONFIG = {
    "name": "ours",
    "services": {
        "db": {"container_name": "db", "ports": [{"published": "5432", "target": 5432}]},
        "api": {"container_name": "api", "ports": [{"published": "8080", "target": 8080}]},
        "worker": {},  # no pinned name, no published port
    },
}


def _done(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


class _FakeDocker:
    """Answers the docker calls ComposeStack makes, and records them."""

    def __init__(self, ps_lines: list[str]) -> None:
        self.ps_lines = ps_lines
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        self.calls.append(args)
        if args[:2] == ["docker", "compose"] and "config" in args:
            return _done(args, stdout=json.dumps(_CONFIG))
        if args[:2] == ["docker", "ps"]:
            return _done(args, stdout="\n".join(self.ps_lines))
        return _done(args)

    def removed(self) -> list[str]:
        return [name for call in self.calls if call[:3] == ["docker", "rm", "-f"] for name in call[3:]]


_PS = [
    "db\tleftover\t0.0.0.0:5432->5432/tcp",  # our name, another project
    "cache\tleftover\t0.0.0.0:8080->6379/tcp",  # our port, another project
    "loose\t\t",  # not ours, no clash
    "api\tours\t0.0.0.0:8080->8080/tcp",  # ours: kept
    "unrelated\tother\t0.0.0.0:9000->80/tcp",
]


def _stack() -> ComposeStack:
    return ComposeStack(["stack.yml"], profiles=("extra",))


def test_conflicting_containers_lists_without_removing() -> None:
    docker = _FakeDocker(_PS)
    with patch("production_test_framework.compose._run", docker):
        assert _stack().conflicting_containers() == ["db", "cache"]
    assert docker.removed() == []
    config_call = next(c for c in docker.calls if "config" in c)
    assert config_call[:6] == ["docker", "compose", "-f", "stack.yml", "--profile", "extra"]


def test_remove_conflicting_containers() -> None:
    docker = _FakeDocker(_PS)
    with patch("production_test_framework.compose._run", docker):
        assert _stack().remove_conflicting_containers() == ["db", "cache"]
    assert docker.removed() == ["db", "cache"]


def test_stopped_container_holding_our_name_conflicts() -> None:
    docker = _FakeDocker(["db\tleftover\t"])  # stopped: no ports listed
    with patch("production_test_framework.compose._run", docker):
        assert _stack().conflicting_containers() == ["db"]


def test_nothing_to_remove_runs_no_rm() -> None:
    docker = _FakeDocker(["api\tours\t0.0.0.0:8080->8080/tcp"])
    with patch("production_test_framework.compose._run", docker):
        assert _stack().remove_conflicting_containers() == []
    assert docker.removed() == []


def test_config_failure_raises() -> None:
    def fail(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        return _done(args, returncode=1, stderr="no such file")

    with patch("production_test_framework.compose._run", fail), pytest.raises(RuntimeError, match="config"):
        _stack().conflicting_containers()


def _recording(returncode: int = 0, stderr: str = "") -> tuple[list, object]:
    calls: list[list[str]] = []

    def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        calls.append(args)
        return _done(args, returncode=returncode, stderr=stderr)

    return calls, run


def test_pull_raises_on_failure_by_default() -> None:
    calls, run = _recording(returncode=1, stderr="pull access denied for local-image")
    with patch("production_test_framework.compose._run", run), pytest.raises(RuntimeError, match="denied"):
        _stack().pull()
    assert calls[0][-1] == "pull"


def test_pull_selected_services() -> None:
    calls, run = _recording()
    with patch("production_test_framework.compose._run", run):
        assert _stack().pull("db", "api") == []
    assert calls[0][-3:] == ["pull", "db", "api"]


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected"),
    [
        (0, "Pulling db ... done\n", []),
        (
            0,
            "Error response from daemon: pull access denied for local-image\n",
            ["Error response from daemon: pull access denied for local-image"],
        ),
        (1, "network unreachable\n", ["network unreachable"]),
    ],
)
def test_pull_ignore_failures_returns_them(returncode: int, stderr: str, expected: list[str]) -> None:
    calls, run = _recording(returncode, stderr)
    with patch("production_test_framework.compose._run", run):
        assert _stack().pull(ignore_failures=True) == expected
    assert calls[0][-2:] == ["pull", "--ignore-pull-failures"]


def test_services() -> None:
    def run(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        assert args[-2:] == ["config", "--services"]
        return _done(args, stdout="db\napi\nworker\n")

    with patch("production_test_framework.compose._run", run):
        assert _stack().services() == ["db", "api", "worker"]
