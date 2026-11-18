# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

"""Unit tests for silencing the unverified-HTTPS warning around switch API calls."""

import warnings

import pytest
from urllib3.exceptions import InsecureRequestWarning

from production_test_framework.switch.tls import unverified_tls_quiet


def _warns_inside(verify_tls: bool) -> bool:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with unverified_tls_quiet(verify_tls):
            warnings.warn("unverified", InsecureRequestWarning, stacklevel=1)
    return any(issubclass(w.category, InsecureRequestWarning) for w in caught)


def test_silent_when_verification_is_off():
    assert not _warns_inside(verify_tls=False)


def test_warns_when_verification_is_on():
    assert _warns_inside(verify_tls=True)


def test_warns_again_after_the_block():
    with unverified_tls_quiet(verify_tls=False):
        pass
    with pytest.warns(InsecureRequestWarning):
        warnings.warn("unverified", InsecureRequestWarning, stacklevel=1)
