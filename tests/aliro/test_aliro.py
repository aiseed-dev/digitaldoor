"""Aliro の読取機を Matter で設定する値(digitalkey.aliro)。"""
import json
import os

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from digitalkey import aliro as A


def test_reader_config_has_the_lengths_matter_expects_and_a_matching_pair():
    """SetAliroReaderConfig の欄は 32、65、16、16 バイトで、SigningKey と VerificationKey は対になる。"""
    c = A.ReaderConfig.generate(ble_uwb=True)
    c.check()
    f = A.ReaderConfig.from_json(c.to_json()).matter_fields()
    assert f["cluster"] == 0x0101 and f["command"] == 0x28 and f["timed"] is True
    lens = {k: len(bytes.fromhex(v)) for k, v in f["fields"].items()}
    assert lens == {"SigningKey": 32, "VerificationKey": 65, "GroupIdentifier": 16, "GroupResolvingKey": 16}
    assert f["fields"]["VerificationKey"].startswith("04")
    # 署名して公開鍵で確かめられる(対である)
    priv = ec.derive_private_key(int.from_bytes(c.signing_key, "big"), ec.SECP256R1())
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), c.verification_key)
    pub.verify(priv.sign(b"x", ec.ECDSA(hashes.SHA256())), b"x", ec.ECDSA(hashes.SHA256()))
    assert "GroupResolvingKey" not in A.ReaderConfig.generate().matter_fields()["fields"]


def test_broken_configs_are_refused():
    """長さの違う欄、対でない鍵、曲線の上に無い点は、読取機に送る前に止める。"""
    c = A.ReaderConfig.generate()
    other = A.ReaderConfig.generate()
    bad = [A.ReaderConfig(c.signing_key[:31], c.verification_key, c.group_identifier),
           A.ReaderConfig(c.signing_key, c.verification_key, c.group_identifier[:15]),
           A.ReaderConfig(c.signing_key, other.verification_key, c.group_identifier),
           A.ReaderConfig(c.signing_key, b"\x04" + b"\x01" * 64, c.group_identifier),
           A.ReaderConfig(c.signing_key, c.verification_key, c.group_identifier, b"\x00" * 8)]
    for b in bad:
        with pytest.raises(ValueError):
            b.check()


def test_secrets_stay_out_of_repr_and_the_printed_output(tmp_path, capsys):
    """R-AC-11 R-AC-20: 読取機の SigningKey と発行者の秘密鍵は、repr と画面に出さず、権限 600 のファイルにだけ書く。"""
    c = A.ReaderConfig.generate()
    assert c.signing_key.hex() not in repr(c)
    k = A.IssuerKey.generate()
    assert k.private.hex() not in repr(k)
    rf, kf = tmp_path / "reader.json", tmp_path / "issuer.key"
    assert A.main(["reader-config", "--out", str(rf)]) == 0
    assert A.main(["issuer-key", "--out", str(kf)]) == 0
    out = capsys.readouterr().out
    saved = json.loads(rf.read_text())
    assert saved["signing_key"] not in out and kf.read_bytes().hex() not in out
    assert (os.stat(rf).st_mode & 0o777) == 0o600 and (os.stat(kf).st_mode & 0o777) == 0o600
    assert A.IssuerKey.load(kf.read_bytes()).public.hex() in out
    assert A.main(["issuer-key", "--out", str(kf)]) == 1     # 上書きしない


def test_issuer_public_key_goes_in_as_credential_type_6():
    """R-AC-20: 読取機に入れるのは発行者の公開鍵だけで、SetCredential の CredentialType 6(AliroCredentialIssuerKey)。"""
    k = A.IssuerKey.generate()
    f = A.issuer_key_credential(k.public, credential_index=2)
    assert f["command"] == 0x22 and f["timed"] is True
    assert f["fields"]["Credential"] == {"CredentialType": 6, "CredentialIndex": 2}
    assert f["fields"]["CredentialData"] == k.public.hex() and k.private.hex() not in json.dumps(f)
    with pytest.raises(ValueError):
        A.issuer_key_credential(k.public[1:])
    with pytest.raises(ValueError):
        A.issuer_key_credential(k.public, credential_index=0)


def test_matter_fields_command(tmp_path, capsys):
    """命令行で、読取機の設定と発行者の鍵の二つの命令の欄を出せる。"""
    rf = tmp_path / "reader.json"
    A.main(["reader-config", "--out", str(rf), "--ble-uwb"])
    pub = A.IssuerKey.generate().public.hex()
    capsys.readouterr()
    assert A.main(["matter-fields", "--reader", str(rf), "--issuer-pub", pub]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [g["command"] for g in got] == [0x28, 0x22]
    assert A.main(["matter-fields", "--reader", str(rf), "--issuer-pub", "00"]) == 1
