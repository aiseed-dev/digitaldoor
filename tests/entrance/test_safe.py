import tarfile
from datetime import datetime, timedelta

from digitalkey.entrance import ledger as D
from digitalkey.entrance import safe


def test_archive(ledger, tmp_path):
    ledger.append("駆けつけ", {"事象": "騒音", "受付時刻": "2026-10-01T23:10", "対応者": "オーナー"}, issuer="経営者")
    tar = safe.make_archive(ledger.root, tmp_path / "out", stamp="t")
    names = tarfile.open(tar).getnames()
    assert any(n.endswith(".adoc") for n in names) and any(n.endswith("ledger.sqlite") for n in names)


def test_retention_candidates(ledger, monkeypatch):
    old = datetime.now().astimezone() - timedelta(days=365 * 4)

    class Old(datetime):
        @classmethod
        def now(cls, tz=None):
            return old

    monkeypatch.setattr(D, "datetime", Old)
    ledger.append("本人確認", {"主体": "A", "判定": "承認", "判定者": "AI社員"}, issuer="AI社員")
    monkeypatch.undo()
    ledger.append("本人確認", {"主体": "B", "判定": "承認", "判定者": "AI社員"}, issuer="AI社員")
    ledger.append("駆けつけ", {"事象": "x", "受付時刻": "2020-01-01T00:00", "対応者": "オーナー"}, issuer="経営者")
    c = safe.retention_candidates(ledger)
    assert [d["主体"] for d in c] == ["A"]
