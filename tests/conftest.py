"""Pytest configuration for ensuring project packages are importable."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest


@pytest.fixture(autouse=True)
def isolate_default_run_logs(tmp_path, monkeypatch):
    """Default SDK evidence belongs to each test, never the caller's workspace."""
    monkeypatch.chdir(tmp_path)
