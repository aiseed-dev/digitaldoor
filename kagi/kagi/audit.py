"""追記だけの記録（audit.log）。連番、ハッシュの連鎖、HMAC の署名。K-06。

消す・直す手は作らない。署名の鍵は読んで HMAC に使うだけで、どこにも出さない。
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import hmac
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

GENESIS = "0" * 64
KEY_NAME = "kagi-log-key"
HOWTO = (
    "署名の鍵が無い。人が作って置く（kagi は作らない。skills/kagi/02-記録.md）: "
    "head -c 32 /dev/urandom > ~/.config/kagi/log-key; chmod 600 ~/.config/kagi/log-key; "
    "export KAGI_LOG_KEY_FILE=~/.config/kagi/log-key"
)
FIELDS = ("seq", "ts", "actor", "action", "detail", "prev", "hash", "sig")


class NoKey(Exception):
    """署名の鍵が無い。終了コード 2。"""


class Key:
    """署名の鍵。中身は repr にも str にも出さない。"""

    __slots__ = ("_b", "path", "warning")

    def __init__(self, b: bytes, path: Path, warning: str | None):
        self._b = b
        self.path = path
        self.warning = warning

    def __repr__(self) -> str:
        return f"Key(path={str(self.path)!r})"

    __str__ = __repr__

    def sign(self, h: str) -> str:
        return hmac.new(self._b, h.encode("ascii"), hashlib.sha256).hexdigest()


def find_key(env: dict | None = None) -> Key:
    env = os.environ if env is None else env
    cands = []
    if env.get("CREDENTIALS_DIRECTORY"):
        cands.append(Path(env["CREDENTIALS_DIRECTORY"]) / KEY_NAME)
    if env.get("KAGI_LOG_KEY_FILE"):
        cands.append(Path(env["KAGI_LOG_KEY_FILE"]).expanduser())
    for p in cands:
        if not p.is_file():
            continue
        try:
            mode = stat.S_IMODE(p.stat().st_mode)
            b = p.read_bytes()
        except OSError as e:
            raise NoKey(f"署名の鍵を読めない: {p} ({type(e).__name__})") from None
        if not b:
            raise NoKey(f"署名の鍵が空: {p}")
        warn = None if mode == 0o600 else f"署名の鍵の許可が {mode:03o}（600 にする: chmod 600 {p}）"
        return Key(b, p, warn)
    raise NoKey(HOWTO)


def digest(seq: int, ts: str, actor: str, action: str, detail: str, prev: str) -> str:
    return hashlib.sha256(f"{seq}|{ts}|{actor}|{action}|{detail}|{prev}".encode()).hexdigest()


def _read(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l for l in path.read_text("utf-8").splitlines() if l.strip()]


def add(path: Path, key: Key, actor: str, action: str, detail: str, now: dt.datetime | None = None) -> int:
    """一行足し、その seq を返す。前の行が読めなければ足さない（ValueError）。"""
    ts = (now or dt.datetime.now().astimezone()).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        lines = [l for l in f.read().splitlines() if l.strip()]
        if lines:
            try:
                last = json.loads(lines[-1])
                seq, prev = int(last["seq"]) + 1, str(last["hash"])
            except (ValueError, KeyError, TypeError):
                raise ValueError("記録の最後の行が読めないので足さない。kagi log verify で見る") from None
        else:
            seq, prev = 1, GENESIS
        h = digest(seq, ts, actor, action, detail, prev)
        rec = {
            "seq": seq,
            "ts": ts,
            "actor": actor,
            "action": action,
            "detail": detail,
            "prev": prev,
            "hash": h,
            "sig": key.sign(h),
        }
        f.seek(0, os.SEEK_END)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return seq


@dataclass
class Failure:
    where: str  # "seq N" か "line N"
    reason: str


def verify(path: Path, key: Key) -> tuple[int, list[Failure]]:
    lines = _read(path)
    fails: list[Failure] = []
    prev_seq, prev_hash = 0, GENESIS
    for n, line in enumerate(lines, 1):
        try:
            r = json.loads(line)
            if not isinstance(r, dict) or any(k not in r for k in FIELDS):
                raise ValueError
            seq = int(r["seq"])
        except (ValueError, TypeError):
            fails.append(Failure(f"line {n}", "JSON として読めないか欄が足りない"))
            prev_seq += 1
            prev_hash = None
            continue
        where = f"seq {seq}"
        if seq != prev_seq + 1:
            fails.append(Failure(where, f"連番が飛んだ（前は {prev_seq}）"))
        if prev_hash is not None and r["prev"] != prev_hash:
            fails.append(Failure(where, "prev が前の行の hash と違う"))
        calc = digest(seq, str(r["ts"]), str(r["actor"]), str(r["action"]), str(r["detail"]), str(r["prev"]))
        if calc != r["hash"]:
            fails.append(Failure(where, "hash が中身の計算と違う"))
        if not hmac.compare_digest(key.sign(str(r["hash"])), str(r["sig"])):
            fails.append(Failure(where, "sig が鍵と合わない"))
        prev_seq, prev_hash = seq, str(r["hash"])
    return len(lines), fails
