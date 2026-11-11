"""秘密らしい文字列の在り処を探す。出すのは path:line と種類だけ（K-13、K-14）。

一致した文字列は、戻り値にも出力にも例外のメッセージにも入れない。
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 1024 * 1024

# 型は少なく（計画書 §6 の 3）。名だけを外に出す
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github-token", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"xox[bp]-")),
]
ASSIGNMENT = (
    "assignment",
    re.compile(r"^\s*(password|passwd|secret|token|api_key)\s*[=:]\s*\S{8,}", re.IGNORECASE),
)


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    kind: str


@dataclass
class Scan:
    hits: list[Hit]
    files: int = 0
    skipped: int = 0


def _excluded(path: Path, excludes: list[str]) -> bool:
    s = path.as_posix()
    return any(fnmatch.fnmatch(s, g) or fnmatch.fnmatch(path.name, g) for g in excludes)


def _files(root: Path, excludes: list[str]):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):  # シンボリックリンクの先は辿らない
        dirnames[:] = sorted(d for d in dirnames if d != ".git" and not _excluded(Path(dirpath, d), excludes))
        for name in sorted(filenames):
            p = Path(dirpath, name)
            if p.is_symlink():
                continue
            yield p


def scan_text(text: str, path: str) -> list[Hit]:
    hits = []
    use_assign = "example" not in path.lower()  # .example も含む
    for n, line in enumerate(text.splitlines(), 1):
        kinds = [k for k, rx in PATTERNS if rx.search(line)]
        if use_assign and ASSIGNMENT[1].search(line):
            kinds.append(ASSIGNMENT[0])
        hits += [Hit(path, n, k) for k in kinds]
    return hits


def scan(paths: list[str | Path], excludes: list[str] | None = None) -> Scan:
    excludes = excludes or []
    res = Scan(hits=[])
    for root in map(Path, paths):
        if not root.exists():
            res.skipped += 1
            continue
        for p in _files(root, excludes):
            if ".git" in p.parts or _excluded(p, excludes):
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    res.skipped += 1
                    continue
                data = p.read_bytes()
            except OSError:
                res.skipped += 1
                continue
            if b"\0" in data:
                res.skipped += 1
                continue
            res.files += 1
            res.hits += scan_text(data.decode("utf-8", errors="replace"), str(p))
    return res


def counts(res: Scan) -> dict[str, int]:
    out: dict[str, int] = {}
    for h in res.hits:
        out[h.kind] = out.get(h.kind, 0) + 1
    return out
