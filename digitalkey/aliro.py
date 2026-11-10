# SPDX-License-Identifier: AGPL-3.0-or-later
"""Aliro の読取機を Matter で設定するための値を作る — 実装の例(解説 02「Aliroとは何か」)。

Aliro(CSA、1.0 は 2026-02-26)は、スマートフォンと読取機の間を決める規格です。読取機に「どの読取機として名乗るか」と
「どの発行者の資格情報を信じるか」を入れる道の一つが、Matter の Door Lock クラスタ(0x0101)です。Matter 1.4 の
データモデル(Door Lock クラスタの rev 8)で、次の物が入りました。名前と番号は connectedhomeip の data_model による。

  機能のビット   ALIRO(13、AliroProvisioning)、ALBU(14、AliroBLEUWB。ALIRO が前提)
  属性           0x0080 AliroReaderVerificationKey、0x0081 AliroReaderGroupIdentifier、
                 0x0082 AliroReaderGroupSubIdentifier、0x0083 AliroExpeditedTransactionSupportedProtocolVersions、
                 0x0084 AliroGroupResolvingKey(ALBU)、0x0085 AliroSupportedBLEUWBProtocolVersions(ALBU)、
                 0x0086 AliroBLEAdvertisingVersion(ALBU)、0x0087 NumberOfAliroCredentialIssuerKeysSupported、
                 0x0088 NumberOfAliroEndpointKeysSupported。0x0080〜0x0086 は admin の権限でだけ読める
  命令           0x28 SetAliroReaderConfig(SigningKey 32 バイト、VerificationKey 65 バイト、GroupIdentifier 16 バイト、
                 GroupResolvingKey 16 バイト。最後は ALBU の錠で要る)、0x29 ClearAliroReaderConfig。どちらも admin、Timed Invoke
  資格情報の種類 SetCredential(0x22)の CredentialType 6 AliroCredentialIssuerKey、7 AliroEvictableEndpointKey、
                 8 AliroNonEvictableEndpointKey

このモジュールが作るのは二つです。

1. 読取機の設定(`ReaderConfig`)。P-256 の鍵の対(SigningKey は秘密の 32 バイト、VerificationKey は非圧縮の公開鍵 65 バイト。
   先頭が 0x04)と、読取機の組の識別(16 バイト)と、BLE と UWB のための組の鍵(16 バイト)。SetAliroReaderConfig の欄の値になる
2. 発行者の鍵(`IssuerKey`)。Aliro の資格情報(Access Document)に署名する Credential Issuer の P-256 の鍵の対。
   読取機に入れるのは公開鍵(65 バイト)だけで、SetCredential の CredentialType 6 として入れる。秘密鍵は管理者が自分の機械か、
   取り出せない所に持つ(要求仕様 R-AC-11、R-AC-20)。載せ方を担う業者に渡さない

読取機の設定の SigningKey は秘密です。repr とログに出さず、ファイルに書くときは権限 600 にします。

作らない物: スマートフォンと読取機の間の取引(NFC、BLE、UWB の上の相互認証、Access Document の検べ)。読取機は買う物で、
その中は Aliro の認証を受けた製品と、CSA の試験の道具(aliro-access-control)や各社の SDK に任せます。
Matter の側で命令を送るのは、管理者のアプリかコントローラ(connectedhomeip の chip-tool、matter.js など)です。
この扉の盤(`digitalkey.panel`)は CircuitMatter の上にあり、Timed Invoke の処理を持たないので、ALIRO の機能を出していません。

  digitalkey aliro reader-config --out reader.json [--ble-uwb]   読取機の設定を作る(権限 600)。公開の欄だけを表示する
  digitalkey aliro issuer-key --out issuer.key                    発行者の鍵の対を作る(秘密鍵は権限 600)
  digitalkey aliro matter-fields --reader reader.json [--issuer-pub HEX] [--credential-index N] [--user-index N]
                                  SetAliroReaderConfig と SetCredential の欄を JSON で出す(octet string は 16 進)
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

DOOR_LOCK_CLUSTER = 0x0101
FEATURE_ALIRO = 1 << 13
FEATURE_ALBU = 1 << 14
CMD_SET_CREDENTIAL = 0x22
CMD_SET_ALIRO_READER_CONFIG = 0x28
CMD_CLEAR_ALIRO_READER_CONFIG = 0x29
CRED_ALIRO_ISSUER_KEY = 6
CRED_ALIRO_EVICTABLE_ENDPOINT_KEY = 7
CRED_ALIRO_NON_EVICTABLE_ENDPOINT_KEY = 8
OPERATION_ADD = 0


def _uncompressed(pub: ec.EllipticCurvePublicKey) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _private_scalar(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def _from_scalar(raw: bytes) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def check_public_key(raw: bytes) -> None:
    """65 バイトの非圧縮の P-256 の公開鍵か。曲線の上の点でなければ ValueError。"""
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("公開鍵は非圧縮の 65 バイト(先頭 0x04)です")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


@dataclass(frozen=True)
class ReaderConfig:
    """SetAliroReaderConfig の欄。signing_key は秘密。"""

    signing_key: bytes          # 32 バイト(P-256 の秘密の数)
    verification_key: bytes     # 65 バイト(非圧縮の公開鍵)
    group_identifier: bytes     # 16 バイト
    group_resolving_key: bytes | None = None   # 16 バイト(ALBU の錠で要る)

    def __repr__(self) -> str:   # 秘密を出さない
        return (f"ReaderConfig(verification_key={self.verification_key.hex()[:16]}…, "
                f"group_identifier={self.group_identifier.hex()}, ble_uwb={self.group_resolving_key is not None})")

    @classmethod
    def generate(cls, *, ble_uwb: bool = False) -> "ReaderConfig":
        key = ec.generate_private_key(ec.SECP256R1())
        return cls(_private_scalar(key), _uncompressed(key.public_key()), secrets.token_bytes(16),
                   secrets.token_bytes(16) if ble_uwb else None)

    def check(self) -> None:
        """長さと、SigningKey と VerificationKey が対であることを確かめる。外れたら ValueError。"""
        if len(self.signing_key) != 32:
            raise ValueError("SigningKey は 32 バイトです")
        check_public_key(self.verification_key)
        if len(self.group_identifier) != 16:
            raise ValueError("GroupIdentifier は 16 バイトです")
        if self.group_resolving_key is not None and len(self.group_resolving_key) != 16:
            raise ValueError("GroupResolvingKey は 16 バイトです")
        if _uncompressed(_from_scalar(self.signing_key).public_key()) != self.verification_key:
            raise ValueError("SigningKey と VerificationKey が対になっていません")

    def matter_fields(self) -> dict:
        """SetAliroReaderConfig(0x28)の欄。octet string は 16 進。秘密の SigningKey を含むので、扱いに注意する。"""
        self.check()
        out = {"cluster": DOOR_LOCK_CLUSTER, "command": CMD_SET_ALIRO_READER_CONFIG, "timed": True,
               "fields": {"SigningKey": self.signing_key.hex(), "VerificationKey": self.verification_key.hex(),
                          "GroupIdentifier": self.group_identifier.hex()}}
        if self.group_resolving_key is not None:
            out["fields"]["GroupResolvingKey"] = self.group_resolving_key.hex()
        return out

    def public_view(self) -> dict:
        return {"verification_key": self.verification_key.hex(), "group_identifier": self.group_identifier.hex(),
                "ble_uwb": self.group_resolving_key is not None}

    def to_json(self) -> str:
        return json.dumps({"signing_key": self.signing_key.hex(), **self.public_view(),
                           "group_resolving_key": self.group_resolving_key.hex() if self.group_resolving_key else None})

    @classmethod
    def from_json(cls, text: str) -> "ReaderConfig":
        d = json.loads(text)
        c = cls(bytes.fromhex(d["signing_key"]), bytes.fromhex(d["verification_key"]),
                bytes.fromhex(d["group_identifier"]),
                bytes.fromhex(d["group_resolving_key"]) if d.get("group_resolving_key") else None)
        c.check()
        return c


@dataclass(frozen=True)
class IssuerKey:
    """Credential Issuer の鍵の対。読取機に入れるのは public(65 バイト)だけ。"""

    private: bytes   # 32 バイト
    public: bytes    # 65 バイト

    def __repr__(self) -> str:
        return f"IssuerKey(public={self.public.hex()[:16]}…)"

    @classmethod
    def generate(cls) -> "IssuerKey":
        key = ec.generate_private_key(ec.SECP256R1())
        return cls(_private_scalar(key), _uncompressed(key.public_key()))

    @classmethod
    def load(cls, raw: bytes) -> "IssuerKey":
        if len(raw) != 32:
            raise ValueError("発行者の秘密鍵は 32 バイトです")
        return cls(raw, _uncompressed(_from_scalar(raw).public_key()))


def issuer_key_credential(issuer_public: bytes, *, credential_index: int = 1, user_index: int | None = None) -> dict:
    """発行者の公開鍵を読取機に入れる SetCredential(0x22)の欄。CredentialType 6(AliroCredentialIssuerKey)。

    UserIndex を null にすると、錠が利用者を作る(規格の SetCredential の取り決め)。利用者の状態と種類は錠の既定に任せる。
    """
    check_public_key(issuer_public)
    if credential_index < 1:
        raise ValueError("CredentialIndex は 1 からです")
    return {"cluster": DOOR_LOCK_CLUSTER, "command": CMD_SET_CREDENTIAL, "timed": True,
            "fields": {"OperationType": OPERATION_ADD,
                       "Credential": {"CredentialType": CRED_ALIRO_ISSUER_KEY, "CredentialIndex": credential_index},
                       "CredentialData": issuer_public.hex(), "UserIndex": user_index,
                       "UserStatus": None, "UserType": None}}


def _write_secret(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalkey aliro", description="Aliro の読取機を Matter で設定する値を作る(実装の例)",
                                 epilog="秘密(読取機の SigningKey、発行者の秘密鍵)は権限 600 のファイルにだけ書き、画面には出しません。")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reader-config", help="読取機の設定を作る")
    r.add_argument("--out", required=True); r.add_argument("--ble-uwb", action="store_true", help="GroupResolvingKey も作る")
    i = sub.add_parser("issuer-key", help="発行者の鍵の対を作り、公開鍵を出す")
    i.add_argument("--out", required=True)
    m = sub.add_parser("matter-fields", help="SetAliroReaderConfig と SetCredential の欄を JSON で出す(秘密を含む)")
    m.add_argument("--reader", required=True); m.add_argument("--issuer-pub", help="発行者の公開鍵(65 バイトの 16 進)")
    m.add_argument("--credential-index", type=int, default=1); m.add_argument("--user-index", type=int)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "reader-config":
            c = ReaderConfig.generate(ble_uwb=args.ble_uwb)
            _write_secret(args.out, c.to_json().encode())
            print(json.dumps(c.public_view()))
        elif args.cmd == "issuer-key":
            k = IssuerKey.generate()
            _write_secret(args.out, k.private)
            print(json.dumps({"public_key": k.public.hex()}))
        else:
            out = [ReaderConfig.from_json(Path(args.reader).read_text()).matter_fields()]
            if args.issuer_pub:
                out.append(issuer_key_credential(bytes.fromhex(args.issuer_pub), credential_index=args.credential_index,
                                                 user_index=args.user_index))
            print(json.dumps(out, ensure_ascii=False, indent=1))
    except FileExistsError as e:
        print(f"ファイルが既にあります(上書きしません): {e.filename}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0
