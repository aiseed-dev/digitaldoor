"""錠ドライバの登録簿。

錠の指定は `<種類>:<引数>` の形(例 `sesameweb:0123-...`)。`dummy` は組み込み(配線確認用)。
それ以外の種類は、pip で入れたパッケージが entry point `digitalkey.locks` で登録する。
たとえば digitalkey-sesame は `sesame` と `sesameweb` を登録する。入っていない種類を指定すると、
何を pip install すればよいかを添えて止まる。
"""
from __future__ import annotations

from importlib.metadata import entry_points

from .entrance.keyring import DummyLock, LockDriver

HINTS = {"sesame": "digitalkey-sesame", "sesameweb": "digitalkey-sesame"}


def available() -> dict[str, str]:
    """使える種類 → 提供元。"""
    out = {"dummy": "digitalkey"}
    for ep in entry_points(group="digitalkey.locks"):
        out[ep.name] = ep.dist.name if ep.dist else "?"
    return out


def make_lock(lock_id: str, spec: str) -> LockDriver:
    spec = (spec or "dummy").strip()
    if spec == "dummy":
        return DummyLock(lock_id)
    kind, _, arg = spec.partition(":")
    for ep in entry_points(group="digitalkey.locks"):
        if ep.name == kind:
            return ep.load()(lock_id, arg)
    hint = HINTS.get(kind)
    raise ValueError(f"{lock_id}: 錠の種類 '{kind}' のドライバが入っていません" + (f"(pip install {hint})" if hint else "")
                     + f"。使える種類: {', '.join(available())}")


def parse_specs(specs: list[str] | None) -> dict[str, LockDriver]:
    """`錠ID=種類:引数` の並びを、錠ID → ドライバ に。"""
    out: dict[str, LockDriver] = {}
    for s in specs or []:
        lock_id, _, spec = s.partition("=")
        out[lock_id] = make_lock(lock_id, spec)
    return out
