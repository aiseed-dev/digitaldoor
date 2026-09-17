import pytest

from digitalkey.entrance.ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "vault", roles={"オーナー": {"経営者"}})
