"""Fixtures shared by the whole suite.

The one that matters: **no test may touch the real snapshot store.**

``convert`` snapshots its base sheet automatically (docs/06-architecture.md
6.9), so without this every converting test would write into the user's own
data directory. That is rude on its own, and it also makes tests interfere with
each other in a way that is hard to read: a snapshot left behind by one test
lets a later "there is no base sheet" test *find* one, and fail somewhere far
from the cause. Redirecting the store for every test makes that impossible
rather than merely unlikely.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_store(tmp_path_factory, monkeypatch):
    """Point the snapshot store at a fresh temporary directory, per test."""
    root = tmp_path_factory.mktemp("snapshot-store")
    monkeypatch.setenv("JSON2GCS_STORE", str(root))
    return root
