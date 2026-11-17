# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2026 Delos Data, Inc.

import warnings
from collections.abc import Iterator
from contextlib import contextmanager

from urllib3.exceptions import InsecureRequestWarning


@contextmanager
def unverified_tls_quiet(verify_tls: bool) -> Iterator[None]:
    """Silence the unverified-HTTPS warning inside the block when certificate checks are turned off."""
    with warnings.catch_warnings():
        if not verify_tls:
            warnings.simplefilter("ignore", InsecureRequestWarning)
        yield
