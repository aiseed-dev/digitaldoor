"""口の表（ports.toml）と `ss -ltnp` の出力を突き合わせる。K-07。ss は人が走らせてファイルで渡す。"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from kagi.inventory import Refused

OUTWARD = {"0.0.0.0", "::", "*"}
_PROC = re.compile(r'users:\(\("([^"]+)"')


@dataclass(frozen=True)
class Listen:
    bind: str
    port: int
    process: str


def load(path: Path) -> list[dict]:
    try:
        data = tomllib.loads(path.read_text("utf-8"))
    except OSError as e:
        raise Refused(f"口の表を読めない: {path} ({type(e).__name__})") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        m = re.search(r"line \d+, column \d+", str(e))
        raise Refused(
            f"口の表が TOML として読めない: {path} ({m.group(0) if m else type(e).__name__})"
        ) from None
    ports = data.get("port", [])
    if not isinstance(ports, list) or not all(isinstance(p, dict) for p in ports):
        raise Refused("口の表の port は [[port]] の並びで書く")
    for i, p in enumerate(ports, 1):
        if not isinstance(p.get("port"), int):
            raise Refused(f"口の表の {i} 件目に port（数）が無い")
    return ports


def _split_addr(local: str) -> tuple[str, int] | None:
    addr, sep, port = local.rpartition(":")
    if not sep or not port.isdigit():
        return None
    addr = addr.strip("[]")
    addr = addr.split("%", 1)[0]  # 127.0.0.53%lo
    return addr, int(port)


def parse_ss(text: str) -> list[Listen]:
    """`ss -ltnp` の LISTEN の行を読む。見出しと他の行は飛ばす。"""
    out = []
    for line in text.splitlines():
        cols = line.split()
        if not cols or cols[0] != "LISTEN" or len(cols) < 4:
            continue
        got = _split_addr(cols[3])
        if got is None:
            continue
        m = _PROC.search(line)
        out.append(Listen(got[0], got[1], m.group(1) if m else ""))
    return out


@dataclass
class Diff:
    unlisted: list[Listen]  # 表に無い口
    closed: list[dict]  # 表にあって開いていない口
    outward: list[tuple[Listen, str]]  # 外向きで表の bind と違う口（口、表の bind）

    @property
    def any(self) -> bool:
        return bool(self.unlisted or self.closed or self.outward)


def compare(table: list[dict], listens: list[Listen], host: str | None = None) -> Diff:
    if host is not None:
        table = [p for p in table if p.get("host") == host]
    table = [p for p in table if str(p.get("proto", "tcp")).lower() == "tcp"]
    by_port: dict[int, list[dict]] = {}
    for p in table:
        by_port.setdefault(p["port"], []).append(p)
    open_ports = {l.port for l in listens}
    unlisted = sorted({l for l in listens if l.port not in by_port}, key=lambda l: (l.port, l.bind))
    closed = [p for p in table if p["port"] not in open_ports]
    outward = []
    for l in sorted(set(listens), key=lambda l: (l.port, l.bind)):
        if l.bind not in OUTWARD or l.port not in by_port:
            continue
        binds = {str(p.get("bind", "")) for p in by_port[l.port]}
        # 表が外向き（0.0.0.0, ::, * のどれか）を許していれば、外向きの口は表の通り
        if not (binds & OUTWARD):
            outward.append((l, ", ".join(sorted(binds)) or "（書いていない）"))
    return Diff(unlisted, closed, outward)


def table(diff: Diff, host: str | None) -> str:
    head = f"機械 {host}" if host else "表の全部の機械"
    if not diff.any:
        return f"{head}: 表と口の一覧に差は無い。"
    lines = [f"{head}: 差がある。", "", "| 差 | host | port | bind | process |", "|---|---|---|---|---|"]
    for l in diff.unlisted:
        lines.append(f"| 表に無い口 | {host or ''} | {l.port} | {l.bind} | {l.process} |")
    for p in diff.closed:
        lines.append(
            f"| 表にあって開いていない | {p.get('host', '')} | {p['port']} | {p.get('bind', '')} | {p.get('service', '')} |"
        )
    for l, want in diff.outward:
        lines.append(
            f"| 外向きで表と違う（表は {want}） | {host or ''} | {l.port} | {l.bind} | {l.process} |"
        )
    return "\n".join(lines)
