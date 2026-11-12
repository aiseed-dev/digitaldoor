"""一枚の報告（Markdown）。在庫の数と期限、ポートの差と Samba、走査の数、記録の検証。値は入れない。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from kagi import audit, inventory, machine, ports, scan, smb, tokens


def build(
    d: Path,
    days: int = 30,
    ss: Path | None = None,
    host: str | None = None,
    scan_paths: list[str] | None = None,
    excludes: list[str] | None = None,
    today: dt.date | None = None,
    smb_conf: Path | None = None,
    tokens_file: Path | None = None,
) -> str:
    today = today or dt.date.today()
    out = [f"# kagi の報告（{today.isoformat()}）", "", f"作業の場所: `{d}`", ""]

    out += ["## 在庫", ""]
    try:
        res = inventory.check(inventory.load(d / "inventory.toml"), days, today)
        out.append(
            f"- 在庫 {res.count} 件、期限切れ {len(res.by('expired'))}、"
            f"{days} 日以内 {len(res.by('soon'))}、抜け {len(res.by('missing'))}、形の誤り {len(res.by('invalid'))}"
        )
        if res.findings:
            out += ["", inventory.table(res, days)]
    except inventory.Refused as e:
        out.append(f"- 読まなかった: {e}")
    out.append("")

    out += ["## ポート", ""]
    if ss is None:
        out.append("- 突き合わせていない（`--ss FILE` で `ss -ltnp` の出力を渡す）")
    else:
        try:
            diff = ports.compare(ports.load(d / "ports.toml"), ports.parse_ss(ss.read_text("utf-8")), host)
            out.append(
                f"- 表に無いポート {len(diff.unlisted)}、表にあって開いていないポート {len(diff.closed)}、"
                f"外向きで表と違うポート {len(diff.outward)}"
            )
            if diff.any:
                out += ["", ports.table(diff, host)]
        except inventory.Refused as e:
            out.append(f"- 読まなかった: {e}")
        except OSError as e:
            out.append(f"- ss の出力を読めない: {ss} ({type(e).__name__})")
    if smb_conf is None:
        out.append("- Samba は見ていない（`--smb FILE` で smb.conf を渡す）")
    else:
        try:
            findings = smb.check(smb.load(smb_conf))
            out += [f"- Samba（smb.conf）の直す物 {smb.fixes(findings)}", "", smb.table(findings, smb_conf)]
        except inventory.Refused as e:
            out.append(f"- Samba: 読まなかった: {e}")
    out.append("")

    out += ["## サーバー", ""]
    out += machine_lines(d)
    out.append("")

    out += ["## 出した token", ""]
    out += token_lines(tokens_file, days, today)
    out.append("")

    out += ["## 走査", ""]
    if not scan_paths:
        out.append("- 走らせていない（`--scan PATH` で場所を渡す）")
    else:
        r = scan.scan(scan_paths, excludes)
        c = scan.counts(r)
        out.append(f"- 読んだファイル {r.files}、飛ばした {r.skipped}、見つかった {len(r.hits)}")
        for k in sorted(c):
            out.append(f"  - {k}: {c[k]}")
        if r.hits:
            out.append("- 場所は `kagi scan` で見る（path:line と種類だけが出る）")
    out.append("")

    out += ["## 記録", ""]
    try:
        key = audit.find_key()
        n, fails = audit.verify(d / "audit.log", key)
        if key.warning:
            out.append(f"- 注意: {key.warning}")
        if fails:
            out.append(f"- 検証が落ちた（{n} 行のうち {len(fails)} 件）")
            out += [f"  - {f.where}: {f.reason}" for f in fails]
        else:
            out.append(f"- ok {n} lines")
    except audit.NoKey:
        out.append("- 検証していない（署名の鍵が無い。skills/kagi/02-記録.md）")
    except (OSError, UnicodeDecodeError) as e:
        out.append(f"- 記録を読めない ({type(e).__name__})")
    out.append("")
    return "\n".join(out)


def machine_rows(d: Path, now: dt.datetime | None = None) -> list[dict]:
    """サーバーごとの一行（host、役、コピーの名、直す数、直す物の頭）。画面と報告が同じ物を使う。"""
    table = machine.load(Path(d) / machine.FILENAME)
    try:
        ptable = ports.load(Path(d) / "ports.toml")
    except inventory.Refused:
        ptable = None
    try:
        ids = {str(s.get("id")) for s in inventory.load(Path(d) / "inventory.toml")}
    except inventory.Refused:
        ids = None
    rows = []
    for m in table.machines:
        host = m["host"]
        row = {
            "host": host,
            "role": str(m.get("role", "")),
            "facts": None,
            "fixes": None,
            "what": [],
            "findings": [],
            "prev": None,  # 前のコピーの名
            "new": [],  # 前のコピーから増えた直す物
            "gone": [],  # 消えた直す物
        }
        path = machine.latest_facts(d, host)
        if path is not None:
            try:
                findings = machine.check(table, host, machine.load_facts(path), ptable, ids, now)
            except inventory.Refused as e:
                row["what"] = [str(e)]
            else:
                row["facts"] = path.name
                row["fixes"] = machine.fixes(findings)
                row["what"] = list(dict.fromkeys(f.what for f in findings if f.fix))
                row["findings"] = findings
                prev = machine.previous_facts(d, host)
                if prev is not None:
                    try:
                        before = machine.check(table, host, machine.load_facts(prev), ptable, ids, now)
                    except inventory.Refused:
                        before = None
                    if before is not None:
                        row["prev"] = prev.name
                        row["new"], row["gone"] = machine.changes(before, findings)
        rows.append(row)
    return rows


def machine_lines(d: Path) -> list[str]:
    try:
        rows = machine_rows(d)
    except inventory.Refused:
        return [f"- サーバーの表が無い（`{machine.FILENAME}`。`kagi init` で雛形）"]
    if not rows:
        return ["- サーバーの表が空"]
    out = []
    for r in rows:
        if r["facts"] is None:
            out.append(f"- {r['host']}: コピーが無い（`kagi machine collect --host {r['host']}`）")
        elif r["fixes"]:
            out.append(f"- {r['host']}: 直す物 {r['fixes']}（{'、'.join(r['what'])}）。コピー {r['facts']}")
        else:
            out.append(f"- {r['host']}: 直す物は無い。コピー {r['facts']}")
        out += change_lines(r, "  ")
    return out


def change_lines(r: dict, indent: str = "") -> list[str]:
    """前のコピーからの変わり目（連載 第 11 章の「先週の出力と比べる」）。"""
    if r.get("prev") is None:
        return []
    if not r["new"] and not r["gone"]:
        return [f"{indent}- 前のコピー（{r['prev']}）から、直す物は変わっていない"]
    out = [f"{indent}- 前のコピー（{r['prev']}）から: 増えた {len(r['new'])}、消えた {len(r['gone'])}"]
    out += [f"{indent}  - 増えた: {f.what}: {f.result}" for f in r["new"]]
    out += [f"{indent}  - 消えた: {f.what}: {f.result}" for f in r["gone"]]
    return out


def token_lines(path: Path | None = None, days: int = 30, today: dt.date | None = None) -> list[str]:
    path = Path(path or tokens.DEFAULT).expanduser()
    try:
        rs = tokens.rows(tokens.load(path), days, today)
    except inventory.Refused as e:
        return [f"- 見ていない: {e}"]
    live = sum(1 for r in rs if r["state"] in ("live", "soon"))
    soon = sum(1 for r in rs if r["state"] == "soon")
    out = [
        (
            f"- 出した token {len(rs)} 件、生きている {live}、{days} 日以内に切れる {soon}、"
            f"期限切れのまま {tokens.fixes(rs)}（{path}）"
        )
    ]
    out += [
        f"  - {tokens.LABEL[r['state']]}: {r['name']}（{r['app']} {r['scope']}、{r['expires']}）"
        for r in rs
        if r["state"] in ("expired", "soon")
    ]
    return out
