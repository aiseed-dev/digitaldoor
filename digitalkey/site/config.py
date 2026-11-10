"""site.toml の読み書き。

トークンは SHA-256(`token_sha256`)で書き、トークンそのものは設定に置かない(使い方.md「事業所サーバーの口とトークン」)。
前からの設定の `token`(そのままの値)も読む。読んだ後は、どちらも SHA-256 だけを持つ。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("管理者", "受付", "利用者", "保守")
# 役割 → 要求仕様 共通コア 7.1 の主体(R-DV-12)
PRINCIPALS = {"管理者": "管理者", "受付": "運用者", "利用者": "利用者", "保守": "保守者"}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(24)


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
    token_sha256: str            # トークンの SHA-256(16 進)。トークンそのものは持たない
    subject: str = ""            # 利用者のとき、鍵の「主体」に書かれる名前

    @property
    def principal(self) -> str:
        return PRINCIPALS[self.role]


@dataclass
class SiteConfig:
    name: str
    vault: Path
    doors: list[DoorConfig] = field(default_factory=list)
    tokens: list[TokenConfig] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 8800
    credential_max_hours: float = 24.0   # 資格情報の最長の有効時間(R-AC-14。[credentials] max_hours)

    def door(self, door_id: str) -> DoorConfig | None:
        return next((d for d in self.doors if d.id == door_id), None)


def load(path: Path | str) -> SiteConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    site = raw.get("site", {})
    cfg = SiteConfig(name=site.get("name", "事業所"), vault=Path(site.get("vault", "vault")),
                     host=raw.get("api", {}).get("host", "127.0.0.1"), port=int(raw.get("api", {}).get("port", 8800)))
    mh = raw.get("credentials", {}).get("max_hours", cfg.credential_max_hours)
    if isinstance(mh, bool) or not isinstance(mh, (int, float)) or not 0 < mh <= 24 * 366:
        raise ValueError("[credentials] max_hours は 0 より大きい時間(数)です")
    cfg.credential_max_hours = float(mh)
    for d in raw.get("door", []):
        cfg.doors.append(DoorConfig(id=d["id"], lock=d.get("lock", "dummy"), two_person=int(d.get("two_person", 1)),
                                    autolock_s=float(d.get("autolock", 5.0)), power_policy=d.get("power_policy", "release")))
    names: set[str] = set()
    for t in raw.get("token", []):
        if t.get("role") not in ROLES:
            raise ValueError(f"token {t.get('name')}: role は {ROLES} のどれか")
        if t.get("name") in names:
            raise ValueError(f"token {t.get('name')}: 同じ名前が二つあります")
        names.add(t["name"])
        if "token_sha256" in t:
            h = str(t["token_sha256"]).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", h):
                raise ValueError(f"token {t['name']}: token_sha256 は 64 桁の 16 進です")
        elif "token" in t:
            h = token_hash(str(t["token"]))
        else:
            raise ValueError(f"token {t['name']}: token_sha256 がありません")
        cfg.tokens.append(TokenConfig(name=t["name"], role=t["role"], token_sha256=h, subject=t.get("subject", "")))
    if not cfg.doors:
        raise ValueError("door が一つもありません")
    if not cfg.tokens:
        raise ValueError("token が一つもありません")
    return cfg


def token_block(name: str, role: str, token: str, subject: str = "") -> str:
    """設定に足す [[token]] の一つ。トークンは SHA-256 だけを書く。"""
    if role not in ROLES:
        raise ValueError(f"role は {ROLES} のどれかです")
    if not name or any(ord(c) < 32 for c in name + subject):
        raise ValueError("名前と主体名は、空でなく、改行などの制御文字を含まない文字です")
    q = lambda v: '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    out = f"\n[[token]]\nname = {q(name)}\nrole = {q(role)}\ntoken_sha256 = \"{token_hash(token)}\"\n"
    return out + (f"subject = {q(subject)}\n" if subject else "")


def sample_with_tokens(name: str = "本社", vault: str = "vault") -> tuple[str, dict[str, str]]:
    """初期設定の雛形と、作ったトークン {名前: トークン}。トークンは雛形には SHA-256 だけが入る。"""
    tokens = {"管理者": new_token(), "受付": new_token()}
    text = _head(name, vault) + "".join(token_block(n, n, t) for n, t in tokens.items())
    return text, tokens


def sample(name: str = "本社", vault: str = "vault") -> str:
    """初期設定の雛形(トークンは捨てる。手引きの見本用)。"""
    return sample_with_tokens(name, vault)[0]


def _head(name: str, vault: str) -> str:
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

# 扉が自分で確かめる署名つき資格情報。有効の終わりは、鍵の終わりと「作った時刻 + max_hours」の早い方。
# 網が届かない扉で失効が効くまでの最長の時間になる(R-AC-14)。値は管理者が扉ごとの事情で決める
[credentials]
max_hours = 24

# アクセストークン。role は 管理者 | 受付 | 利用者 | 保守。利用者は subject(鍵の主体名)を書く
# トークンそのものは書かず、SHA-256 だけを書く。足すのは digitalkey site token
'''
