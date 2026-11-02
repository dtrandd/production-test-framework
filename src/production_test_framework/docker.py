# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.

"""
Helpers for tests that run part of their work in a container.
"""

import itertools
import os
import subprocess
import time

from .host import Host

__all__ = [
    "CONTAINER_LABEL",
    "docker_argv",
    "force_remove_container",
    "label_args",
    "list_containers_by_label",
    "remove_containers_by_label",
    "unique_container_name",
]

#: Label key applied to every container these helpers start, so orphans can be found without
#: knowing their names. The value is the container name.
CONTAINER_LABEL = "production-test-framework.container"

#: Seconds between attempts to remove a container that is still dying.
REMOVE_RETRY_INTERVAL = 2.0

#: What ``docker rm --force`` says when the container is still dying after its kill, or another
#: removal of it is under way: both clear once it has exited.
STILL_DYING = ("did not receive an exit event", "is already in progress")

# Distinguishes containers started by the same process; the pid distinguishes processes.
_sequence = itertools.count()


def docker_argv(*args: str, sudo: bool = False) -> list[str]:
    """
    Build a docker command line, optionally via ``sudo``.

    Some hosts only allow docker through ``sudo``; taking it as an argument keeps that decision
    with the caller rather than reading an environment variable here.
    """
    prefix = ["sudo", "docker"] if sudo else ["docker"]
    return [*prefix, *args]


def unique_container_name(base: str) -> str:
    """
    A container name unique to this process and run, e.g. ``inferencex-4821-0``.

    Unique so a container left behind by an earlier run cannot make the next one fail with
    "name already in use", and so two suites can run side by side on one host. Still
    predictable enough to be logged and removed.
    """
    return f"{base}-{os.getpid()}-{next(_sequence)}"


def label_args(name: str, extra: dict[str, str] | None = None) -> list[str]:
    """``docker run`` arguments tagging a container as started by this framework."""
    args = ["--label", f"{CONTAINER_LABEL}={name}"]
    for key, value in (extra or {}).items():
        args.extend(["--label", f"{key}={value}"])
    return args


def force_remove_container(name: str, *, sudo: bool = False, timeout: float = 30.0, host: Host | None = None) -> bool:
    """
    Remove *name*, killing it first if it is still running. True when it is gone. With *host*,
    the container is on that machine.

    Safe to call when the container has already exited under ``--rm``: a missing container
    counts as success, so this can run unconditionally after every command. A container that
    takes more than about 10s to die after its kill, such as a large model server releasing its
    GPUs, makes ``docker rm --force`` give up; that is retried until *timeout*.
    """
    argv = docker_argv("rm", "--force", name, sudo=sudo)
    deadline = time.monotonic() + timeout
    while True:
        try:
            completed = subprocess.run(
                host.argv(argv) if host else argv,
                capture_output=True,
                text=True,
                timeout=max(deadline - time.monotonic(), 1.0),
                check=False,
            )
        except OSError, subprocess.SubprocessError:
            return False
        # `docker rm` on an absent container is the expected case after a clean run.
        if completed.returncode == 0 or "no such container" in completed.stderr.lower():
            return True
        still_dying = any(message in completed.stderr for message in STILL_DYING)
        if not still_dying or time.monotonic() + REMOVE_RETRY_INTERVAL >= deadline:
            return False
        time.sleep(REMOVE_RETRY_INTERVAL)


def list_containers_by_label(label: str = CONTAINER_LABEL, *, sudo: bool = False, timeout: float = 30.0) -> list[str]:
    """
    Container ids carrying *label*, running or not.

    Empty when there are none, and also when docker cannot be reached -- callers are usually
    cleaning up and should not fail because of it.
    """
    try:
        completed = subprocess.run(
            docker_argv("ps", "--quiet", "--all", "--filter", f"label={label}", sudo=sudo),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return []
    if completed.returncode != 0:
        return []
    return completed.stdout.split()


def remove_containers_by_label(label: str = CONTAINER_LABEL, *, sudo: bool = False, timeout: float = 60.0) -> list[str]:
    """
    Force-remove every container carrying *label*; returns the ids it tried to remove.

    For sweeping orphans at the end of a session. ``docker container prune`` is not a
    substitute: it only removes containers that have already stopped, and the leak worth
    guarding against is one that is still running and still holding its resources.
    """
    container_ids = list_containers_by_label(label, sudo=sudo, timeout=timeout)
    if not container_ids:
        return []
    try:
        subprocess.run(
            docker_argv("rm", "--force", *container_ids, sudo=sudo),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        pass
    return container_ids
