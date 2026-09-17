"""site.toml の読み書き。"""
from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("管理者", "受付", "利用者", "保守")


@dataclass
class DoorConfig:
    id: str
    lock: str = "dummy"          # dummy | <種類>:<引数>(入れた錠ドライバによる。例 sesameweb:<uuid>)
    two_person: int = 1
    autolock_s: float = 5.0
    power_policy: str = "release"


@dataclass
class TokenConfig:
    name: str
    role: str
    token: str
    subject: str = ""            # 利用者のとき、鍵の「主体」に書かれる名前


@dataclass
class SiteConfig:
    name: str
    vault: Path
    doors: list[DoorConfig] = field(default_factory=list)
    tokens: list[TokenConfig] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8800

    def door(self, door_id: str) -> DoorConfig | None:
        return next((d for d in self.doors if d.id == door_id), None)


def load(path: Path | str) -> SiteConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    site = raw.get("site", {})
    cfg = SiteConfig(name=site.get("name", "事業所"), vault=Path(site.get("vault", "vault")),
                     host=raw.get("api", {}).get("host", "127.0.0.1"), port=int(raw.get("api", {}).get("port", 8800)))
    for d in raw.get("door", []):
        cfg.doors.append(DoorConfig(id=d["id"], lock=d.get("lock", "dummy"), two_person=int(d.get("two_person", 1)),
                                    autolock_s=float(d.get("autolock", 5.0)), power_policy=d.get("power_policy", "release")))
    for t in raw.get("token", []):
        if t.get("role") not in ROLES:
            raise ValueError(f"token {t.get('name')}: role は {ROLES} のどれか")
        cfg.tokens.append(TokenConfig(name=t["name"], role=t["role"], token=t["token"], subject=t.get("subject", "")))
    if not cfg.doors:
        raise ValueError("door が一つもありません")
    if not cfg.tokens:
        raise ValueError("token が一つもありません")
    return cfg


def sample(name: str = "本社", vault: str = "vault") -> str:
    """初期設定の雛形。管理者と受付のトークンを生成して入れる。"""
    return f'''# digitaldoor 事業所サーバーの設定

[site]
name = "{name}"
vault = "{vault}"            # 台帳・監査記録・鍵の置き場(このサーバーの中)

[api]
host = "127.0.0.1"          # 構内だけに出す。外へ出すときは前段に TLS を置く
port = 8800

# 扉。lock は dummy(配線確認)か、入れた錠ドライバの種類。digitalkey-sesame を入れると sesameweb:<機器UUID>(Hub 3 経由)と sesame:<機器UUID>(BLE 直結)
[[door]]
id = "正面玄関"
lock = "dummy"
two_person = 1              # 2 にすると二人同時認証
autolock = 5.0              # 解錠から自動施錠までの秒

# アクセストークン。role は 管理者 | 受付 | 利用者 | 保守。利用者は subject(鍵の主体名)を書く
[[token]]
name = "管理者"
role = "管理者"
token = "{secrets.token_urlsafe(24)}"

[[token]]
name = "受付"
role = "受付"
token = "{secrets.token_urlsafe(24)}"
'''
