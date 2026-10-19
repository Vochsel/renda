import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "spec"


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path_factory, monkeypatch):
    # keep generated HDRIs / downloads out of the user's cache, but share across tests
    monkeypatch.setenv("RENDA_CACHE_DIR", str(tmp_path_factory.getbasetemp() / "renda-cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.getbasetemp() / "config"))
