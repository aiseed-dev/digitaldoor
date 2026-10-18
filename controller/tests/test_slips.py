import hashlib
import json
import os

import pytest

from digitaldoor_controller.audit import Audit, Ed25519Signer, Entry, HmacSigner, load_signer
from digitaldoor_controller.cli import main
from digitaldoor_controller.slips import check_box, export, key_id


def make_audit(n=3, path=None):
    a = Audit(Ed25519Signer.generate(), path)
    for i in range(n):
        a.append(at=float(i + 1), synced=True, kind="操作", event="解錠" if i % 2 == 0 else "施錠", subject="A")
    return a


def slips(box):
    return sorted(box.glob("*.json"))


def test_export_and_check_with_public_key_only(tmp_path):
    """R-CT-17: 記録を伝票にして箱へ出し、受け手は公開鍵だけで検べられる。伝票に秘密鍵は入らない。"""
    a = make_audit(3)
    box = tmp_path / "box"
    box.mkdir()
    assert export(a, box, "玄関", tmp_path / "export.cursor") == 3
    files = slips(box)
    assert [f.name for f in files] == [f"玄関-{n:010d}.json" for n in (1, 2, 3)]
    pub = a.signer.public_key()
    d = json.loads(files[0].read_text(encoding="utf-8"))
    assert d["format"] == "digitaldoor-audit-slip/1" and d["door"] == "玄関" and d["alg"] == "Ed25519"
    assert d["key_id"] == key_id(pub) == hashlib.sha256(pub).hexdigest()[:16]
    assert d["entry"]["seq"] == 1 and d["entry"]["hash"] == a.entries[0].hash
    secret = a.signer.private_bytes()
    assert all(secret.hex() not in f.read_text(encoding="utf-8") for f in files)
    assert check_box(box, "玄関", pub) == (True, 3, "")
    assert not list(box.glob(".*"))   # 書きかけの一時ファイルが残らない


def test_check_finds_tamper_gap_and_wrong_key(tmp_path):
    """R-CT-17: 箱の伝票の書き換え、一件の削除、別の鍵を、受け手の検べが指摘する。"""
    a = make_audit(3)
    pub = a.signer.public_key()
    box = tmp_path / "box"
    box.mkdir()
    export(a, box, "玄関", tmp_path / "c")
    f = slips(box)[1]
    original = f.read_text(encoding="utf-8")

    d = json.loads(original)
    d["entry"]["subject"] = "B"
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert check_box(box, "玄関", pub)[2] == "内容が変わっています"

    d["entry"]["hash"] = hashlib.sha256(Entry(**d["entry"]).body()).hexdigest()   # ハッシュも作り直す
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    ok, _, why = check_box(box, "玄関", pub)
    assert not ok and why in ("署名が合いません", "連鎖が切れています")

    f.write_text(original, encoding="utf-8")
    assert check_box(box, "玄関", pub)[0]
    f.unlink()
    ok, _, why = check_box(box, "玄関", pub)
    assert not ok and "欠番" in why

    f.write_text(original, encoding="utf-8")
    other = Ed25519Signer.generate().public_key()
    assert check_box(box, "玄関", other)[2] == "鍵が違います"


def test_box_down_then_resume(tmp_path):
    """R-CT-17: 箱が無い間は何も出さず、戻ったら続きから全部出す。同じ伝票を二度出さず、受け手が消しても装置の記録は変わらない。"""
    a = make_audit(2)
    box, cursor = tmp_path / "box", tmp_path / "export.cursor"
    assert export(a, box, "玄関", cursor) == 0 and not box.exists()
    a.append(at=3.0, synced=True, kind="操作", event="解錠")
    box.mkdir()
    assert export(a, box, "玄関", cursor) == 3
    assert export(a, box, "玄関", cursor) == 0
    for f in slips(box):          # 受け手が取り込んで消す
        f.unlink()
    a.append(at=4.0, synced=True, kind="操作", event="施錠")
    assert export(a, box, "玄関", cursor) == 1
    assert [f.name for f in slips(box)] == ["玄関-0000000004.json"]
    assert len(a.entries) == 4 and a.verify()[0]
    assert check_box(box, "玄関", a.signer.public_key()) == (True, 1, "")


def test_hmac_cannot_export(tmp_path):
    """R-CT-17: 受け手が鍵なしで検べられない署名(HMAC)では伝票を出さない。"""
    a = Audit(HmacSigner(b"k" * 32))
    a.append(at=1.0, synced=True, kind="操作", event="解錠")
    (tmp_path / "box").mkdir()
    with pytest.raises(ValueError):
        export(a, tmp_path / "box", "玄関", tmp_path / "c")


def test_load_signer_keeps_existing_hmac(tmp_path):
    """R-CT-13: 新しい置き場は Ed25519 の鍵を所有者だけが読めるファイルに作る。前からの HMAC の置き場は HMAC のまま続ける。"""
    new = tmp_path / "new"
    s = load_signer(new)
    assert isinstance(s, Ed25519Signer) and (new / "audit.ed25519").stat().st_mode & 0o777 == 0o600
    assert load_signer(new).public_key() == s.public_key()
    old = tmp_path / "old"
    old.mkdir()
    (old / "audit.key").write_bytes(os.urandom(32))
    assert isinstance(load_signer(old), HmacSigner) and not (old / "audit.ed25519").exists()


def test_cli_pubkey_export_check(tmp_path, capsys):
    """R-CT-17: 命令行で公開鍵を出し、伝票を箱へ出し、受け手が公開鍵で検べる。"""
    d, box = tmp_path / "door", tmp_path / "box"
    a = Audit(load_signer(d), d / "audit.jsonl")
    a.append(at=1.0, synced=True, kind="操作", event="解錠")
    a.append(at=2.0, synced=True, kind="操作", event="施錠")
    assert main(["--dir", str(d), "pubkey"]) == 0
    pub = capsys.readouterr().out.strip()
    assert bytes.fromhex(pub) == a.signer.public_key()
    box.mkdir()
    assert main(["--dir", str(d), "--name", "玄関", "export", "--box", str(box)]) == 0
    assert len(slips(box)) == 2
    assert main(["check-box", "--box", str(box), "--door", "玄関", "--pubkey", pub]) == 0
    slips(box)[0].write_text("{}", encoding="utf-8")
    assert main(["check-box", "--box", str(box), "--door", "玄関", "--pubkey", pub]) == 1
