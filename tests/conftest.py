"""The audit package from this checkout's scripts, and the checkout fixture of the ratchet tests."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ratchet_fixture import Checkout  # noqa: E402


@pytest.fixture(params=["src/pkg", "src/other"])
def repo(tmp_path: Path, request) -> Checkout:
    checkout = tmp_path / "repository"
    checkout.mkdir()
    return Checkout(checkout, request.param)
