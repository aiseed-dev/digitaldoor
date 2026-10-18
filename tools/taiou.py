# SPDX-License-Identifier: AGPL-3.0-or-later
"""Map requirement IDs in the specifications to the tests that cover them.

The specifications (docs/要求仕様/*.md and docs/扉コントローラ仕様書.md) number
every requirement as R-XX-nn. Tests name the IDs they verify in their
docstrings or comments. This script lists, per document, which requirements
have at least one test and which are still unverified, and writes the result
to docs/適合の現在.md. Standard library only.

The door controller's tests (controller/tests) are counted with the rest;
--tests adds any other test directory, e.g. another implementation's.

Usage:
    python tools/taiou.py            # write docs/適合の現在.md and print a summary
    python tools/taiou.py --check    # also fail if a test names an ID no document defines
    python tools/taiou.py --tests ../other-impl/tests
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_FILES = sorted(ROOT.glob("docs/要求仕様/*.md")) + [ROOT / "docs/扉コントローラ仕様書.md"]
TEST_DIRS = [ROOT / "tests", ROOT / "controller" / "tests", *ROOT.glob("drivers/*/tests")]
OUT = ROOT / "docs/適合の現在.md"

ID_RE = re.compile(r"\bR-[A-Z]{2}-\d{2}\b")
# A requirement is *defined* where a document states it: as the first cell of a
# table row ("| R-CM-01 | ...") or in bold ("**R-CT-05**"). Mentions in prose
# ("see R-DV-30") are references, not definitions.
DEFINED_RE = re.compile(r"^\|\s*(R-[A-Z]{2}-\d{2})\s*\||\*\*(R-[A-Z]{2}-\d{2})\*\*", re.M)
DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)")


def requirements() -> dict[str, list[str]]:
    """Return {document name: [IDs in order of first appearance]}."""
    found: dict[str, list[str]] = {}
    for path in SPEC_FILES:
        ids: list[str] = []
        for m in DEFINED_RE.finditer(path.read_text(encoding="utf-8")):
            rid = m.group(1) or m.group(2)
            if rid not in ids:
                ids.append(rid)
        if ids:
            found[path.name] = ids
    return found


def _shown(path: Path) -> str:
    """Path as shown in the table: relative to this repository, or prefixed with the other repository's name."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path.relative_to(ROOT.parent))


def coverage(extra: list[Path] | None = None) -> dict[str, list[str]]:
    """Return {ID: ["controller/tests/test_controller.py::test_fire", ...]}."""
    cov: defaultdict[str, list[str]] = defaultdict(list)
    dirs = TEST_DIRS + [d.resolve() for d in (extra or [])]
    for d in dirs:
        for path in sorted(d.rglob("test_*.py")):
            func = None
            for line in path.read_text(encoding="utf-8").splitlines():
                m = DEF_RE.match(line)
                if m:
                    func = m.group(1)
                for rid in ID_RE.findall(line):
                    where = f"{_shown(path)}::{func}" if func else _shown(path)
                    if where not in cov[rid]:
                        cov[rid].append(where)
    return dict(cov)


def render(reqs: dict[str, list[str]], cov: dict[str, list[str]], today: str) -> str:
    lines = [
        "# 適合の現在 — 要求とテストの対応",
        "",
        f"`tools/taiou.py` が {today} に作った。要求仕様の ID(R-XX-nn)ごとに、"
        "その ID を名指しするテストを並べる。テストの無い要求は「未検証」で、"
        "実機の受入れ試験で確かめる物と、まだ書いていない物の両方が入る。"
        "テストが通ることは、ここに並ぶ要求を満たすことの確かめであって、"
        "並んでいない要求については何も言わない。",
        "",
        "## まとめ",
        "",
        "| 文書 | 要求 | テストあり | 未検証 |",
        "|---|---:|---:|---:|",
    ]
    total = tested = 0
    for doc, ids in reqs.items():
        n_t = sum(1 for i in ids if i in cov)
        total += len(ids)
        tested += n_t
        lines.append(f"| {doc} | {len(ids)} | {n_t} | {len(ids) - n_t} |")
    lines.append(f"| 合計 | {total} | {tested} | {total - tested} |")
    for doc, ids in reqs.items():
        lines += ["", f"## {doc}", "", "| ID | テスト |", "|---|---|"]
        for rid in ids:
            tests = cov.get(rid)
            cell = "<br>".join(f"`{t}`" for t in tests) if tests else "未検証"
            lines.append(f"| {rid} | {cell} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail when a test names an undefined ID")
    ap.add_argument("--out", type=Path, default=OUT, help="output file (default docs/適合の現在.md)")
    ap.add_argument("--tests", type=Path, action="append", default=[],
                    help="another test directory to count (repeatable)")
    args = ap.parse_args()

    reqs = requirements()
    cov = coverage(args.tests)
    defined = {i for ids in reqs.values() for i in ids}
    unknown = sorted(i for i in cov if i not in defined)

    today = dt.date.today().isoformat()
    args.out.write_text(render(reqs, cov, today), encoding="utf-8")

    total = sum(len(v) for v in reqs.values())
    tested = sum(1 for ids in reqs.values() for i in ids if i in cov)
    print(f"要求 {total}、テストあり {tested}、未検証 {total - tested} → {args.out.relative_to(ROOT)}")
    if unknown:
        print("文書に無い ID をテストが名指ししている: " + ", ".join(unknown), file=sys.stderr)
        if args.check:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
