"""窓口の二つの道具の試しで共に使う値と下ごしらえの関数(conftest.py と試しの両方から import する)。

窓口仕様書 8、9、11.1、11.2 章と、設計書 22.7 の形だけを読んで書いた。名前と値は架空の物。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from fake_pb import FakePB

ENV_NAMES = ("MADOGUCHI_URL", "MADOGUCHI_IDENTITY", "MADOGUCHI_PASSWORD")
ISSUER = "3f2a9c0d1e4b5a67"        # 設計書 22.7 の例の発行者の識別(架空)
OTHER_ISSUER = "0123456789abcdef"  # 架空
FMT = "digitalkey-shoukai/1"
T0 = "2026-12-01T09:00"
T1 = "2027-01-01T00:00"
PID = "20261111T101500-a1b2c3"     # 設計書 22.7 の例の権限の番号(架空)
REPO = Path(__file__).resolve().parents[2]


def write_env(path: Path, values: dict[str, str], mode: int = 0o600) -> Path:
    """env のファイルを書く(一行に 名前=値)。権限は mode。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    os.chmod(path, mode)
    return path


@dataclass
class Creds:
    env: Path
    identity: str
    password: str


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return self.out.splitlines()

    @property
    def err_lines(self) -> list[str]:
        return self.err.splitlines()


def sheet(at: str, state: str = "効いている", pid: str = PID) -> dict:
    """設計書 22.7 の形の sheet(手で書いた物)。"""
    return {"形式": FMT, "時刻": at,
            "権限": [{"番号": pid, "対象の種類": "扉", "対象": "玄関", "開始": T0, "終了": T1, "状態": "発行済み"}],
            "鍵": [{"権限": pid, "経路": "門番を通す錠", "扉": ["玄関"], "機器": "", "識別": "c07b…",
                   "状態": state, "効くまで": ""}]}


def item(owner: str, at: str = "2026-12-10T12:00", state: str = "効いている", issuer: str = ISSUER) -> dict:
    """コピーの一人分(設計書 22.7 の形)。"""
    return {"owner": owner, "issuer": issuer, "made_at": at, "sheet": sheet(at, state)}


def write_items(path: Path, items) -> Path:
    path.write_text(json.dumps(items, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def no_secrets(res: Result, *values: str, pb: FakePB | None = None) -> None:
    """標準出力と標準エラーに、渡した値(識別、パスワード)と、疑似が出した札が現れない。"""
    text = res.out + res.err
    for v in values:
        assert v and v not in text, "秘密の値が出力に出た"
    if pb is not None:
        for t in pb.tokens:
            assert t not in text, "札が出力に出た"
