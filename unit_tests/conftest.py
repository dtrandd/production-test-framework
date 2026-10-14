# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.

"""Shared pytest fixtures for unit tests."""

import pytest

from production_test_framework.config import LGTMConfig


@pytest.fixture
def lgtm_config():
    """LGTMConfig with test values (no real SSH)."""
    return LGTMConfig(
        host="test-host.example.com",
        remote_user="testuser",
    )
