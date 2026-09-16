"""The package exposes a version string sourced from its distribution metadata."""

from __future__ import annotations

import re
from importlib.metadata import version

import sartre


def test_version_is_non_empty_pep440_ish() -> None:
    assert isinstance(sartre.__version__, str)
    assert sartre.__version__  # non-empty
    # a real release starts with a number; the raw-checkout fallback is the sentinel
    assert re.match(r"^\d+\.\d+", sartre.__version__) or sartre.__version__ == "0.0.0+unknown"


def test_version_matches_distribution_when_installed() -> None:
    assert sartre.__version__ == version("sartre")
