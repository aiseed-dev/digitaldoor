"""係の机の画面 `kagi app`（Flet の PC 版。設計書 §3.3）。

`build_cards` は Flet を使わない純粋な関数で、在庫・口・走査・記録の結果を札の文字と表に変える。
`flet` は `run` と `make_main` の中で遅く import する（kagi の本体は標準ライブラリだけのまま）。
鍵の値は読まない。札に出るのは在庫の id と欄の名、口の番号、path:line と種類、記録の連番と理由だけ。
四つ目の画面「人の出入り」は seed のサーバーの管理の口を使う（`kagi.admin`）。管理者の token は画面に出さない。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
from pathlib import Path

from kagi import admin, audit, inventory, machine, ports, report, scan, tokens

SEED_COLOR = "#5B7A3A"  # seed の theme と同じ種の色
FONT = "Roboto"  # Flet 1.1 の既定の字体は日本語の字形を持たない（seed の theme.py と同じ理由）
MAX_WIDTH = 900
WEB_PORT_ENV = "KAGI_APP_WEB_PORT"  # 確かめる時だけ。あればブラウザのタブで開く（127.0.0.1 だけ）


def _inventory_card(d: Path, days: int, today: dt.date) -> dict:
    card = {"title": "在庫", "summary": "", "error": None, "columns": ["id", "何", "中身"], "rows": []}
    try:
        res = inventory.check(inventory.load(d / "inventory.toml"), days, today)
    except inventory.Refused as e:
        card["error"] = f"読まなかった: {e}"
        return card
    card["summary"] = (
        f"件数 {res.count}、期限切れ {len(res.by('expired'))}、{days} 日以内 {len(res.by('soon'))}、"
        f"抜け {len(res.by('missing'))}、形の誤り {len(res.by('invalid'))}"
    )
    for p in ("expired", "missing", "invalid", "soon"):
        for f in res.by(p):
            card["rows"].append([f.id, inventory.LABEL[p], f.detail])
    return card


def _ports_card(d: Path, ss_file: Path | None, host: str | None) -> dict:
    card = {
        "title": "口",
        "summary": "",
        "error": None,
        "columns": ["差", "host", "port", "bind", "process"],
        "rows": [],
    }
    if ss_file is None:
        card["summary"] = "突き合わせていない（ss -ltnp の写しのファイルを選ぶ）"
        return card
    try:
        table = ports.load(d / "ports.toml")
        text = Path(ss_file).read_text("utf-8")
    except inventory.Refused as e:
        card["error"] = f"読まなかった: {e}"
        return card
    except (OSError, UnicodeDecodeError) as e:
        card["error"] = f"ss の出力を読めない: {ss_file} ({type(e).__name__})"
        return card
    diff = ports.compare(table, ports.parse_ss(text), host)
    head = f"機械 {host}" if host else "表の全部の機械"
    card["summary"] = (
        f"{head}（{Path(ss_file).name}）: 表に無い口 {len(diff.unlisted)}、"
        f"表にあって開いていない口 {len(diff.closed)}、外向きで表と違う口 {len(diff.outward)}"
    )
    for l in diff.unlisted:
        card["rows"].append(["表に無い口", host or "", str(l.port), l.bind, l.process])
    for p in diff.closed:
        card["rows"].append(
            [
                "表にあって開いていない",
                str(p.get("host", "")),
                str(p["port"]),
                str(p.get("bind", "")),
                str(p.get("service", "")),
            ]
        )
    for l, want in diff.outward:
        card["rows"].append([f"外向きで表と違う（表は {want}）", host or "", str(l.port), l.bind, l.process])
    return card


def _machines_card(d: Path, now: dt.datetime | None = None) -> dict:
    """機械の札。機械ごとの一行と、直す物の表。値は無い（指紋とアカウントの名と口だけ）。"""
    card = {
        "title": "機械",
        "summary": "",
        "error": None,
        "columns": ["host", "役", "写し", "直す", "直す物"],
        "rows": [],
        "detail_columns": ["host", "何", "見た物"],
        "detail": [],
    }
    try:
        rows = report.machine_rows(d, now)
    except inventory.Refused as e:
        card["summary"] = "機械の表が無い（machines.toml。kagi init で雛形）"
        card["error"] = str(e) if (d / machine.FILENAME).exists() else None
        return card
    n = sum(1 for r in rows if r["fixes"])
    none = sum(1 for r in rows if r["facts"] is None)
    card["summary"] = f"機械 {len(rows)} 台、直す物のある機械 {n}、写しの無い機械 {none}"
    for r in rows:
        card["rows"].append(
            [
                r["host"],
                r["role"],
                r["facts"] or "無い",
                "" if r["fixes"] is None else str(r["fixes"]),
                "、".join(r["what"]),
            ]
        )
        card["detail"] += [[r["host"], f.what, f.result] for f in r["findings"] if f.fix]
    return card


def collect_machine(d: Path, host: str, local: bool = False, env: dict | None = None, runner=None) -> Path:
    """写しを取って置く。ssh を使った時は記録に一行（署名の鍵があれば）。"""
    m = machine.load(Path(d) / machine.FILENAME).machine(host)
    local = local or m.get("local") is True
    alias = None if local else str(m.get("ssh", host))
    text = machine.collect(alias, runner, paths=m.get("data"))
    path = machine.save_facts(d, host, text)
    if not local:
        try:
            key = audit.find_key(env)
            audit.add(
                Path(d) / "audit.log", key, (env or os.environ).get("USER", "?"), "machine.collect", host
            )
        except (audit.NoKey, ValueError, OSError):
            pass
    return path


def fix_text(d: Path, host: str) -> str:
    """直す手の文（走らせない）。"""
    for r in report.machine_rows(d):
        if r["host"] == host and r["facts"]:
            facts = machine.load_facts(Path(d) / machine.FACTS_DIR / r["facts"])
            return machine.fix_script(host, facts, r["findings"])
    raise inventory.Refused(f"{host} の写しが無い")


def _scan_card(scan_paths: list[Path], excludes: list[str] | None) -> dict:
    card = {"title": "走査", "summary": "", "error": None, "lines": []}
    if not scan_paths:
        card["summary"] = "走らせていない（場所を選ぶ）"
        return card
    r = scan.scan(list(scan_paths), excludes)
    card["summary"] = f"読んだファイル {r.files}、飛ばした {r.skipped}、見つかった {len(r.hits)}"
    card["lines"] = [f"{h.path}:{h.line}  {h.kind}" for h in r.hits]  # 値は Hit に無い
    return card


def _audit_card(d: Path, env: dict | None) -> dict:
    card = {"title": "記録", "summary": "", "error": None, "key": False, "warning": None, "failures": []}
    try:
        key = audit.find_key(env)
    except audit.NoKey as e:
        card["summary"] = "検証していない（署名の鍵が無い。skills/kagi/02-記録.md）"
        card["error"] = str(e)
        return card
    card["key"] = True
    card["warning"] = key.warning
    try:
        n, fails = audit.verify(d / "audit.log", key)
    except (OSError, UnicodeDecodeError) as e:
        card["error"] = f"記録を読めない ({type(e).__name__})"
        return card
    if fails:
        card["summary"] = f"検証が落ちた（{n} 行のうち {len(fails)} 件）"
        card["failures"] = [f"{f.where}: {f.reason}" for f in fails]
    else:
        card["summary"] = f"ok {n} lines"
    return card


def build_cards(
    d: Path,
    ss_file: Path | None = None,
    scan_paths: list[Path] | None = None,
    *,
    host: str | None = None,
    excludes: list[str] | None = None,
    days: int = 30,
    today: dt.date | None = None,
    env: dict | None = None,
) -> dict:
    """五つの札の中身（文字と表）。Flet を使わない。値は入れない。"""
    d = Path(d)
    today = today or dt.date.today()
    return {
        "dir": str(d),
        "inventory": _inventory_card(d, days, today),
        "ports": _ports_card(d, ss_file, host),
        "machines": _machines_card(d),
        "scan": _scan_card(list(scan_paths or []), excludes),
        "audit": _audit_card(d, env),
    }


def write_report(
    d: Path,
    ss_file: Path | None = None,
    scan_paths: list[Path] | None = None,
    *,
    host: str | None = None,
    days: int = 30,
    today: dt.date | None = None,
) -> tuple[Path, str]:
    """`report` の Markdown を作り、作業の場所の `report-<日>.md` に書く。道と文を返す。"""
    today = today or dt.date.today()
    text = report.build(
        Path(d), days, ss_file, host, [str(p) for p in scan_paths] if scan_paths else None, today=today
    )
    out = Path(d) / f"report-{today.isoformat()}.md"
    out.write_text(text, "utf-8")
    return out, text


def machine_view(d: Path, host: str, now: dt.datetime | None = None) -> dict:
    """一台の機械の画面の中身: 頭の一行、直す物（手付き）、見るだけ。Flet を使わない。"""
    d = Path(d)
    v = {
        "host": host,
        "role": "",
        "facts": None,
        "head": "",
        "fixes": [],
        "notes": [],
        "error": None,
        "changes": [],
    }
    try:
        row = next((r for r in report.machine_rows(d, now) if r["host"] == host), None)
    except inventory.Refused as e:
        v["error"] = str(e)
        return v
    if row is None:
        v["error"] = f"機械の表に {host} が無い"
        return v
    v["role"], v["facts"] = row["role"], row["facts"]
    if row["facts"] is None:
        v["head"] = f"機械 {host}: 写しが無い。「写しを取る」から"
        v["error"] = "、".join(row["what"]) or None
        return v
    facts = machine.load_facts(d / machine.FACTS_DIR / row["facts"])
    v["head"] = machine.head(host, facts, row["findings"])
    v["fixes"] = [{"what": f.what, "result": f.result, "how": f.all_how()} for f in row["findings"] if f.fix]
    v["notes"] = [[f.what, f.result] for f in row["findings"] if not f.fix]
    v["changes"] = [ln.strip().removeprefix("- ") for ln in report.change_lines(row)]
    return v


def keys_view(d: Path) -> dict:
    """鍵の画面の中身。指紋は公開の物。秘密鍵は読まない。"""
    v = {
        "columns": ["id", "持つ人", "指紋", "入れてよい機械", "在庫表"],
        "rows": [],
        "problems": [],
        "error": None,
    }
    try:
        table = machine.load(Path(d) / machine.FILENAME)
    except inventory.Refused as e:
        v["error"] = str(e)
        return v
    try:
        inv = inventory.load(Path(d) / "inventory.toml")
    except inventory.Refused:
        inv = None
    v["rows"] = machine.key_rows(table, inv)
    v["problems"] = machine.key_fingerprints(table)[1]
    return v


def tokens_view(path: Path | None = None, days: int = 30, today: dt.date | None = None) -> dict:
    """相手に出した token の台帳の中身（誰に、アプリ、範囲、今、期限）。ハッシュは出さない。"""
    path = Path(path or tokens.DEFAULT).expanduser()
    v = {
        "path": str(path),
        "columns": ["誰に", "アプリ", "範囲", "今", "期限"],
        "rows": [],
        "fixes": 0,
        "error": None,
    }
    try:
        rs = tokens.rows(tokens.load(path), days, today)
    except inventory.Refused as e:
        v["error"] = str(e)
        return v
    v["rows"] = [[r["name"], r["app"], r["scope"], tokens.LABEL[r["state"]], r["expires"]] for r in rs]
    v["fixes"] = tokens.fixes(rs)
    return v


def _list(text: str) -> list[str]:
    return [x for x in (t.strip() for t in (text or "").replace("、", ",").split(",")) if x]


def add_machine(
    d: Path,
    host: str,
    ssh: str = "",
    role: str = "",
    users: str = "",
    admins: str = "",
    keys: str = "",
    services: str = "",
    local: bool = False,
) -> None:
    """機械の表に一台足す（画面の欄から。名の並びは , で区切る）。"""
    row = {
        "host": host.strip(),
        "ssh": ssh.strip(),
        "role": role.strip(),
        "os": "debian-13",
        "users": _list(users),
        "admins": _list(admins),
        "keys": _list(keys),
        "services": _list(services),
        "local": bool(local),
    }
    machine.append_row(Path(d) / machine.FILENAME, "machine", row)


def add_key(d: Path, kid: str, holder: str, pub: str) -> str:
    """鍵の表に一件足す。公開鍵のファイル（.pub）を読んで指紋を返す。"""
    infos = machine.read_pub(Path(pub.strip()))
    machine.append_row(
        Path(d) / machine.FILENAME, "key", {"id": kid.strip(), "holder": holder.strip(), "pub": pub.strip()}
    )
    return infos[0][1].fingerprint


def install_files(d: Path, host: str) -> list[Path]:
    from kagi import install

    return install.write(Path(d), machine.load(Path(d) / machine.FILENAME), host)


def make_main(d: Path):
    import flet as ft

    d = Path(d)

    async def main(page: ft.Page) -> None:
        page.title = "kagi"
        page.theme = ft.Theme(color_scheme_seed=SEED_COLOR, font_family=FONT)
        page.dark_theme = ft.Theme(color_scheme_seed=SEED_COLOR, font_family=FONT)
        page.theme_mode = ft.ThemeMode.SYSTEM
        picker = ft.FilePicker()  # 参照を持っておく（無いと外される）
        st: dict = {
            "view": "machines",
            "host": None,
            "adding": False,
            "busy": None,
            "ss": None,
            "scan": [],
            "report": None,
        }

        def field(label: str, width: int = 220, **kw) -> ft.TextField:
            return ft.TextField(label=label, dense=True, width=width, **kw)

        ss_field = field("ss -ltnp の写しのファイル", 420)
        host_field = field("host（表に機械が二つ以上なら）")
        scan_field = field("走査する場所", 420)
        actor, action, detail = field("actor", 160), field("action", 200), field("detail", 320)
        m_host, m_ssh = field("機械の名（host）", 180), field("ssh の名か番地（空なら host）", 240)
        m_role = field("役", 300)
        m_users, m_admins = field("入れてよい人（, で区切る）", 240), field("sudo を持ってよい人", 200)
        m_keys, m_services = field("入れてよい鍵の id", 200), field("動いているべきサービス", 260)
        m_local = ft.Checkbox(label="この PC 自身")
        k_id, k_holder = field("鍵の id（在庫表と同じ名）", 220), field("持つ人", 140)
        k_pub = field("公開鍵のファイル（.pub）", 320, value="~/.ssh/id_ed25519.pub")
        body = ft.Column(spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)

        def muted(text: str) -> ft.Text:
            return ft.Text(text, size=13, color=ft.Colors.ON_SURFACE_VARIANT, selectable=True)

        def err(text: str) -> ft.Text:
            return ft.Text(text, color=ft.Colors.ERROR, selectable=True)

        def mono(text: str) -> ft.Container:
            return ft.Container(
                ft.Text(text, font_family="monospace", size=13, selectable=True),
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                padding=10,
                border_radius=8,
            )

        def table(columns: list[str], rows: list[list[str]]) -> ft.Control:
            return ft.Row(
                [
                    ft.DataTable(
                        columns=[ft.DataColumn(ft.Text(c, weight=ft.FontWeight.W_600)) for c in columns],
                        rows=[
                            ft.DataRow(cells=[ft.DataCell(ft.Text(v, selectable=True)) for v in r])
                            for r in rows
                        ],
                    )
                ],
                scroll=ft.ScrollMode.AUTO,
            )

        def lines(rows: list[list[str]], first: int = 110) -> ft.Control:
            """二列の行（長い文が折り返す。DataTable は切れるので使わない）。"""
            return ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(ft.Text(a, weight=ft.FontWeight.W_600), width=first),
                            ft.Text(b, selectable=True, expand=True),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    )
                    for a, b in rows
                ],
                spacing=6,
            )

        def card(title: str, controls: list[ft.Control]) -> ft.Card:
            head = ft.Text(title, theme_style=ft.TextThemeStyle.TITLE_MEDIUM, weight=ft.FontWeight.W_600)
            return ft.Card(content=ft.Container(ft.Column([head, *controls], spacing=8), padding=16))

        def toast(text: str) -> None:
            page.show_dialog(ft.SnackBar(ft.Text(text)))

        def copier(text: str):
            async def h(e) -> None:
                await page.clipboard.set(text)
                toast("写した（機械の端末に貼る）")

            return h

        def switch(view: str):
            async def h(e) -> None:
                if st["view"] != view:
                    g["new"], g["confirm"] = None, None  # 出した token は一度だけ。画面を移ったら消す
                    if view == "gate":
                        g["loaded"] = False  # 開くたびに読み直す
                st["view"] = view
                render()

            return h

        def choose(h: str):
            async def f(e) -> None:
                st["host"] = h
                render()

            return f

        def host() -> str | None:
            return (host_field.value or "").strip() or None

        # ---- 機械 ----

        def machines_view() -> list[ft.Control]:
            try:
                rows = report.machine_rows(d)
            except inventory.Refused as e:
                rows = []
                top: list[ft.Control] = [
                    muted("機械の表がまだ無い（machines.toml）。下の「機械を足す」から一台目を書く。"),
                ]
                if (d / machine.FILENAME).exists():
                    top.append(err(str(e)))
            else:
                top = []
            hosts = [r["host"] for r in rows]
            if st["host"] not in hosts:
                st["host"] = hosts[0] if hosts else None
            chips: list[ft.Control] = []
            for r in rows:
                state = "写し無し" if r["facts"] is None else (f"直す {r['fixes']}" if r["fixes"] else "よし")
                label = f"{r['host']}  {state}"
                btn = ft.FilledButton if r["host"] == st["host"] else ft.OutlinedButton
                chips.append(btn(label, on_click=choose(r["host"])))
            chips.append(ft.TextButton("機械を足す", icon=ft.Icons.ADD, on_click=toggle_add))
            out: list[ft.Control] = [*top, ft.Row(chips, wrap=True)]
            if st["adding"] or not rows:
                out.append(
                    card(
                        "機械を足す",
                        [
                            muted(
                                "書くのは名と役と、入れてよい人と鍵。パスワードと秘密鍵は書かない。"
                                "足した後は machines.toml をエディタで直せる。"
                            ),
                            ft.Row([m_host, m_ssh, m_local], wrap=True),
                            ft.Row([m_role], wrap=True),
                            ft.Row([m_users, m_admins, m_keys], wrap=True),
                            ft.Row([m_services], wrap=True),
                            ft.FilledButton("足す", icon=ft.Icons.ADD, on_click=do_add_machine),
                        ],
                    )
                )
            if st["host"]:
                v = machine_view(d, st["host"])
                script = "\n".join(ln for f in v["fixes"] for ln in f["how"])
                ctl: list[ft.Control] = [
                    ft.Text(v["head"], weight=ft.FontWeight.W_600, selectable=True),
                    muted(f"役: {v['role'] or '（書いていない）'}　写し: {v['facts'] or '無い'}"),
                    ft.Row(
                        [
                            ft.FilledButton("写しを取る", icon=ft.Icons.SYNC, on_click=collect),
                            ft.OutlinedButton(
                                "直す手を全部写す",
                                icon=ft.Icons.CONTENT_COPY,
                                on_click=copier(script),
                                disabled=not script,
                            ),
                            ft.OutlinedButton(
                                "入れる時のファイルを作る", icon=ft.Icons.INSTALL_DESKTOP, on_click=do_install
                            ),
                        ],
                        wrap=True,
                    ),
                ]
                if st["busy"]:
                    ctl.append(ft.Row([ft.ProgressRing(width=16, height=16), muted(st["busy"])]))
                if v["error"]:
                    ctl.append(err(v["error"]))
                ctl += [muted(c) for c in v["changes"]]
                out.append(card(f"{st['host']}", ctl))
                if v["fixes"]:
                    fx: list[ft.Control] = [
                        muted(
                            "kagi は機械を変えない。手を読んで、機械の端末で一つずつ。終わったら「写しを取る」。"
                        )
                    ]
                    for f in v["fixes"]:
                        row: list[ft.Control] = [
                            ft.Container(ft.Text(f["what"], weight=ft.FontWeight.W_600), width=110),
                            ft.Column(
                                [
                                    ft.Text(f["result"], selectable=True),
                                    *([mono("\n".join(f["how"]))] if f["how"] else []),
                                ],
                                spacing=6,
                                expand=True,
                            ),
                        ]
                        if f["how"]:
                            row.append(
                                ft.IconButton(
                                    ft.Icons.CONTENT_COPY,
                                    tooltip="この手を写す",
                                    on_click=copier("\n".join(f["how"])),
                                )
                            )
                        fx.append(ft.Row(row, vertical_alignment=ft.CrossAxisAlignment.START))
                    out.append(card(f"直す物 {len(v['fixes'])}", fx))
                if v["notes"]:
                    out.append(card("見るだけ", [lines(v["notes"])]))
            return out

        async def toggle_add(e) -> None:
            st["adding"] = not st["adding"]
            render()

        async def do_add_machine(e) -> None:
            try:
                add_machine(
                    d,
                    m_host.value or "",
                    m_ssh.value or "",
                    m_role.value or "",
                    m_users.value or "",
                    m_admins.value or "",
                    m_keys.value or "",
                    m_services.value or "",
                    bool(m_local.value),
                )
            except inventory.Refused as ex:
                toast(str(ex))
                return
            st["host"], st["adding"] = (m_host.value or "").strip(), False
            for f in (m_host, m_ssh, m_role, m_users, m_admins, m_keys, m_services):
                f.value = ""
            render()

        async def collect(e) -> None:
            h = st["host"]
            st["busy"] = f"{h} の写しを取っている（読むだけ）"
            render()
            try:
                await asyncio.to_thread(collect_machine, d, h)
            except inventory.Refused as ex:
                toast(str(ex))
            st["busy"] = None
            render()

        async def do_install(e) -> None:
            try:
                paths = install_files(d, st["host"])
            except inventory.Refused as ex:
                toast(str(ex))
                return
            toast(f"書いた: {paths[0].parent}（次は 手順.md。パスワードと盤は入っていない）")

        # ---- 鍵 ----

        def keys_view_controls() -> list[ft.Control]:
            v = keys_view(d)
            c: list[ft.Control] = [
                muted(
                    "誰が入れるか（機械の画面）と、どの鍵で入るか（ここ）はひと組。出るのは公開鍵の指紋だけ。"
                )
            ]
            if v["error"]:
                c.append(muted("機械の表がまだ無い（machines.toml）。"))
            if v["rows"]:
                c.append(table(v["columns"], v["rows"]))
            c += [err(f"直す: {p}") for p in v["problems"]]
            add = card(
                "鍵を足す",
                [
                    muted(
                        "鍵は人が作る: ssh-keygen -t ed25519 -a 64（合言葉を付ける）。ここに渡すのは .pub だけ。"
                        "足したら、機械の keys に id を書き、在庫表に同じ id で一件。"
                    ),
                    ft.Row([k_id, k_holder, k_pub], wrap=True),
                    ft.FilledButton("足す", icon=ft.Icons.ADD, on_click=do_add_key),
                ],
            )
            tv = tokens_view()
            tc: list[ft.Control] = [
                muted(
                    "アプリ（seed など）の門に入る token を、誰に、どの範囲で、いつまで出したか。"
                    f"台帳に在るのはハッシュだけ（{tv['path']}）。出す・取り消すはそのアプリの命令で（seed token）。"
                )
            ]
            if tv["error"]:
                tc.append(muted(tv["error"]))
            if tv["rows"]:
                tc.append(table(tv["columns"], tv["rows"]))
            if tv["fixes"]:
                tc.append(err(f"期限切れのまま取り消していない物が {tv['fixes']} 件"))
            return [card("鍵（機械に入る）", c), add, card("出した token（アプリの門に入る）", tc)]

        async def do_add_key(e) -> None:
            try:
                fp = add_key(d, k_id.value or "", k_holder.value or "", k_pub.value or "")
            except inventory.Refused as ex:
                toast(str(ex))
                return
            k_id.value = k_holder.value = ""
            toast(f"足した: {fp}")
            render()

        # ---- 人の出入り（seed のサーバーの門） ----

        g: dict = {
            "data": None,
            "error": None,
            "loaded": False,
            "new": None,
            "confirm": None,
            "conns": {},
            "held": {},  # 読んでいる載せる前の記録: id → {名: JPEG}
            "reasons": {},  # 戻す理由の欄: id → TextField
        }
        g["replacing"], g["touched"], g["scope"] = False, set(), "member"
        g_url = field("サーバーの URL（https://…）", 320)
        g_token = field("管理者の token", 320, password=True)
        t_name = field("渡す相手の名前（必須）", 220)
        t_scope = ft.Dropdown(
            label="できる事",
            value="member",
            options=[ft.DropdownOption(key=s, text=admin.SCOPE_LABEL[s]) for s in admin.SCOPES],
            dense=True,
            width=260,
        )
        t_days = field("期限（日。1〜365）", 150, value=str(admin.default_days("member")))
        t_note = field("覚え書き", 260)
        s_fields = {k: field(label, 220) for k, label in admin.SECRET_FIELDS}
        s_fields["expires"].label = "期限（2027-11-06 の形）"
        for k in ("where", "role", "lost", "leaked"):
            s_fields[k].width = 420

        def conn() -> admin.Conn | None:
            try:
                return admin.load_conn(d)
            except inventory.Refused:
                return None

        def client() -> admin.Client:
            c = admin.load_conn(d)
            if c is None:
                raise inventory.Refused("繋ぐ先が入っていない（上の「繋ぐ先」）")
            return admin.Client(c)

        def overview() -> dict | None:
            return (g["data"] or {}).get("overview")

        def form_check() -> None:
            errs, ok = admin.check_token_form(t_name.value or "", t_scope.value or "", t_days.value or "")
            t_name.error = errs.get("name") if "name" in g["touched"] else None
            t_days.error = errs.get("days")
            issue_btn.disabled = ok is None

        async def form_changed(e) -> None:
            g["touched"].add("name" if e.control is t_name else "days")
            form_check()
            page.update()

        async def scope_changed(e) -> None:
            new = t_scope.value or "member"
            if (t_days.value or "").strip() == str(admin.default_days(g["scope"])):
                t_days.value = str(
                    admin.default_days(new)
                )  # 書き換えていなければ既定を合わせる（管理者は短く）
            g["scope"] = new
            form_check()
            page.update()

        t_name.on_change = form_changed
        t_days.on_change = form_changed
        t_scope.on_select = scope_changed

        async def gate_load() -> None:
            try:
                g["data"] = await asyncio.to_thread(admin.load_all, client())
                g["error"] = None
            except (admin.AdminError, inventory.Refused) as ex:
                g["error"] = str(ex)
            g["loaded"] = True
            render()

        async def gate_reload(e=None) -> None:
            g["loaded"], g["new"], g["conns"], g["held"] = False, None, {}, {}
            render()

        def asking(key) -> bool:
            return g["confirm"] == key

        def twice(key):
            """二度押しの釦の手。一度目は印だけ、二度目で走らせる。"""

            def wrap(fn):
                async def h(e) -> None:
                    if not admin.second_press(g, key):
                        render()
                        return
                    try:
                        msg = await fn()
                    except (admin.AdminError, inventory.Refused) as ex:
                        toast(str(ex))
                        render()
                        return
                    if msg:
                        toast(msg)
                    await gate_reload()

                return h

            return wrap

        def sure_button(label: str, sure: str, key, fn, icon=None) -> ft.Control:
            b = ft.FilledButton if asking(key) else ft.TextButton
            return b(sure if asking(key) else label, icon=icon, on_click=twice(key)(fn))

        async def save_conn(e) -> None:
            try:
                admin.save_conn(d, g_url.value or "", g_token.value or "")
            except inventory.Refused as ex:
                toast(str(ex))
                return
            g_token.value = ""  # 欄に残さない
            g["replacing"] = False
            toast("保存した（持ち主だけが読める形）")
            await gate_reload()

        async def replace_conn(e) -> None:
            g["replacing"] = not g["replacing"]
            render()

        async def forget() -> None:
            admin.forget_conn(d)
            g["data"] = None
            return "繋ぐ先を外した"

        def to_inventory(prefill: dict):
            async def h(e) -> None:
                for k, f in s_fields.items():
                    f.value, f.error = prefill.get(k, ""), None
                st["view"] = "other"
                render()

            return h

        async def do_issue(e) -> None:
            try:
                code, msg = await asyncio.to_thread(
                    admin.issue_token,
                    d,
                    client(),
                    t_name.value or "",
                    t_scope.value or "",
                    t_days.value or "",
                    t_note.value or "",
                )
            except (admin.AdminError, inventory.Refused) as ex:
                toast(str(ex))
                return
            name = (t_name.value or "").strip()
            t_name.value = t_note.value = ""
            g["touched"] = set()
            await gate_reload()
            g["new"] = {"name": name, "code": code}  # 一度だけ見せる（読み直すか画面を移ると消える）
            if msg:
                toast(msg)
            render()

        issue_btn = ft.FilledButton("出す", icon=ft.Icons.KEY_OUTLINED, on_click=do_issue, disabled=True)

        async def copy_new(e) -> None:
            if g["new"]:
                await page.clipboard.set(g["new"]["code"])
                toast("写した。その人にだけ渡す")

        async def close_new(e) -> None:
            g["new"] = None
            render()

        def opener(url: str):
            async def h(e) -> None:
                await ft.UrlLauncher().launch_url(url)

            return h

        def shower(rid: str):
            async def h(e) -> None:
                if rid in g["conns"]:
                    g["conns"].pop(rid)
                    render()
                    return
                try:
                    g["conns"][rid] = await asyncio.to_thread(lambda: client().connections(rid))
                except (admin.AdminError, inventory.Refused) as ex:
                    toast(str(ex))
                    return
                render()

            return h

        def reader(rid: str, names: list[str]):
            """載せる前の記録を読む・閉じる。開く時に写真をサーバーから読む。"""

            async def h(e) -> None:
                if rid in g["held"]:
                    g["held"].pop(rid)
                    render()
                    return
                try:
                    c = client()
                    g["held"][rid] = await asyncio.to_thread(
                        lambda: {n: c.pending_photo(rid, n) for n in names}
                    )
                except (admin.AdminError, inventory.Refused) as ex:
                    toast(str(ex))
                    return
                render()

            return h

        def paused_banner() -> list[ft.Control]:
            o = overview()
            if not (o and o.get("paused")):
                return []
            c = conn()
            text = f"{c.host if c else 'seed のサーバー'} は受け付けを止めています。「人の出入り」の「受け付けを再開する」で戻す。"
            return [
                ft.Container(
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.PAUSE, color=ft.Colors.ON_ERROR_CONTAINER),
                            ft.Text(
                                text,
                                color=ft.Colors.ON_ERROR_CONTAINER,
                                weight=ft.FontWeight.W_600,
                                expand=True,
                            ),
                        ]
                    ),
                    bgcolor=ft.Colors.ERROR_CONTAINER,
                    padding=12,
                    border_radius=8,
                )
            ]

        def gate_view() -> list[ft.Control]:
            c = None
            problem = None
            try:
                c = admin.load_conn(d)
            except inventory.Refused as ex:
                problem = str(ex)
            cc: list[ft.Control] = [
                muted(
                    "seed のサーバーの管理の口に、管理者の token で入る。token は作業の場所の "
                    f"{admin.CONN_FILE} に、持ち主だけが読める形（600）で置く。画面には出さない。"
                    "通すのは https だけ。"
                )
            ]
            if problem:
                cc.append(err(problem))
            if c:
                cc.append(ft.Text(f"サーバー: {c.url}　管理者の token: 入っています", selectable=True))
                if c.warning:
                    cc.append(err(f"注意: {c.warning}"))
                cc.append(
                    ft.Row(
                        [
                            ft.OutlinedButton(
                                "入れ替える", icon=ft.Icons.EDIT_OUTLINED, on_click=replace_conn
                            ),
                            sure_button(
                                "外す", "本当に外す", ("forget", ""), forget, ft.Icons.DELETE_OUTLINE
                            ),
                        ],
                        wrap=True,
                    )
                )
            if not c or g["replacing"]:
                if c and not g_url.value:
                    g_url.value = c.url
                cc += [
                    ft.Row([g_url, g_token], wrap=True),
                    ft.FilledButton("保存", icon=ft.Icons.SAVE, on_click=save_conn),
                ]
            if c and not admin.has_admin_token_row(d):
                cc += [
                    err(
                        "在庫表に一件足してください（名、置き場、持つ人、役、期限、なくした時、漏れた時）。"
                        "この token も、持つ秘密の一つ（K-14）。"
                    ),
                    ft.OutlinedButton(
                        "在庫表に一件足す",
                        icon=ft.Icons.ADD,
                        on_click=to_inventory(admin.admin_token_prefill(d, c)),
                    ),
                ]
            out: list[ft.Control] = [card("繋ぐ先", cc)]
            if not c:
                return out
            if not g["loaded"]:
                out.append(ft.Row([ft.ProgressRing(width=16, height=16), muted("サーバーに尋ねている…")]))
                page.run_task(gate_load)
                return out
            if g["error"]:
                out += [
                    err(g["error"]),
                    ft.OutlinedButton("読み直す", icon=ft.Icons.REFRESH, on_click=gate_reload),
                ]
                return out
            data = g["data"] or {}
            o, au = data.get("overview") or {}, data.get("audit") or {}

            out.append(card("今の状態", [lines(admin.state_rows(o), 140)]))

            async def pause() -> str | None:
                _now, msg = await asyncio.to_thread(admin.set_paused, d, client(), not o.get("paused"))
                return msg

            at: list[ft.Control] = []
            for text, cmds in admin.attention(o, au):
                at.append(err(text))
                if cmds:
                    cmd = "\n".join(cmds)
                    at.append(
                        ft.Row(
                            [
                                ft.Container(mono(cmd), expand=True),
                                ft.IconButton(ft.Icons.CONTENT_COPY, tooltip="写す", on_click=copier(cmd)),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        )
                    )
            if not at:
                at.append(ft.Text("ありません。"))
            if o.get("paused"):
                at.append(
                    sure_button(
                        "受け付けを再開する", "本当に再開する", ("pause", ""), pause, ft.Icons.PLAY_ARROW
                    )
                )
            else:
                at.append(
                    sure_button("受け付けを止める", "本当に止める", ("pause", ""), pause, ft.Icons.PAUSE)
                )
            out.append(card("気を付ける事", at))

            tk: list[ft.Control] = []
            if g["new"]:
                tk.append(
                    ft.Container(
                        ft.Column(
                            [
                                ft.Text(
                                    f"{g['new']['name']} の token。出るのはこの一度だけ。その人にだけ渡す。"
                                    "どこにも書き写さない。"
                                ),
                                ft.Row(
                                    [
                                        ft.Text(
                                            g["new"]["code"],
                                            font_family="monospace",
                                            size=16,
                                            selectable=True,
                                        ),
                                        ft.IconButton(
                                            ft.Icons.CONTENT_COPY, tooltip="写す", on_click=copy_new
                                        ),
                                        ft.TextButton("渡した（消す）", on_click=close_new),
                                    ],
                                    wrap=True,
                                ),
                            ],
                            spacing=6,
                        ),
                        bgcolor=ft.Colors.SECONDARY_CONTAINER,
                        padding=12,
                        border_radius=8,
                    )
                )
            form_check()
            tk += [
                muted("期限は 1〜365 日。管理者に出す物は短く（既定 30 日）。"),
                ft.Row([t_name, t_scope, t_days, t_note], wrap=True),
                issue_btn,
            ]
            rows = admin.token_rows(data.get("tokens") or [])
            for r in rows:
                head = f"{r['name']}　{r['scope']}　{r['state']}"
                line: list[ft.Control] = [
                    ft.Column(
                        [
                            ft.Text(head, selectable=True, color=ft.Colors.ERROR if r["soon"] else None),
                            muted("　".join(x for x in (r["period"], r["note"]) if x)),
                        ],
                        spacing=2,
                        expand=True,
                    )
                ]
                if r["live"]:

                    async def rv(name=r["name"]) -> str | None:
                        _n, msg = await asyncio.to_thread(admin.revoke_token, d, client(), name)
                        return msg

                    line.append(sure_button("取り消す", "本当に取り消す", ("revoke", r["name"]), rv))
                tk.append(ft.Row(line, vertical_alignment=ft.CrossAxisAlignment.START))
            if not rows:
                tk.append(muted("出した token はまだ無い。"))

            async def rv_all() -> str | None:
                n, msg = await asyncio.to_thread(admin.revoke_all_tokens, d, client())
                return "　".join(x for x in (f"{n} 件を取り消した", msg) if x)

            tk.append(
                sure_button(
                    "token を全部取り消す", "本当に全部取り消す（自分の分は残す）", ("revoke_all", ""), rv_all
                )
            )
            out.append(card("人に渡す token", tk))

            held = data.get("pending")
            if held is not None:  # 預かりの無い古いサーバーなら出さない
                hc: list[ft.Control] = [
                    muted(
                        "送られた記録は、ここで読んで「載せる」まで頁に出ません。人の名前や連絡先が無いかを見ます。"
                    )
                ]
                for r in admin.pending_rows(held):
                    rid = r["id"]
                    reason = g["reasons"].setdefault(
                        rid, ft.TextField(label="戻す理由（送った人に届く。200 字まで）", max_length=200)
                    )

                    async def ok(rid=rid) -> str | None:
                        return await asyncio.to_thread(admin.approve_record, d, client(), rid)

                    async def back(rid=rid, reason=reason) -> str | None:
                        msg = await asyncio.to_thread(
                            admin.return_record, d, client(), rid, reason.value or ""
                        )
                        g["reasons"].pop(rid, None)
                        return msg

                    opened = rid in g["held"]
                    one = [
                        ft.Text(rid, selectable=True),
                        muted(r["meta"]),
                        ft.Row(
                            [
                                ft.TextButton(
                                    "閉じる" if opened else "読む", on_click=reader(rid, r["photos"])
                                ),
                                sure_button("載せる", "本当に載せる", ("approve", rid), ok),
                            ],
                            wrap=True,
                            spacing=4,
                        ),
                    ]
                    if opened:
                        shots = g["held"][rid]
                        if shots:
                            one.append(
                                ft.Row(
                                    [
                                        ft.Image(src=b, width=240, height=180, fit=ft.BoxFit.CONTAIN)
                                        for b in shots.values()
                                    ],
                                    wrap=True,
                                )
                            )
                        one += [
                            mono(r["markdown"]),
                            reason,
                            sure_button("戻す", "本当に戻す", ("return", rid), back),
                        ]
                    hc.append(ft.Column(one, spacing=4))
                if not admin.pending_rows(held):
                    hc.append(muted("載せる前の記録はありません。"))
                out.append(card("載せる前の記録", hc))

            rec: list[ft.Control] = []
            for r in admin.record_rows(data.get("records") or []):
                rid = r["id"]

                async def wd(rid=rid) -> str | None:
                    return await asyncio.to_thread(admin.withdraw_record, d, client(), rid)

                btns: list[ft.Control] = []
                if r["url"]:
                    btns.append(
                        ft.TextButton("頁を開く", icon=ft.Icons.OPEN_IN_NEW, on_click=opener(r["url"]))
                    )
                btns += [
                    ft.TextButton(
                        "寄せられた一言を閉じる" if rid in g["conns"] else "寄せられた一言を見る",
                        on_click=shower(rid),
                    ),
                    sure_button("取り下げる", "本当に取り下げる", ("withdraw", rid), wd),
                ]
                one: list[ft.Control] = [
                    ft.Text(r["head"] or rid, selectable=True),
                    muted(r["meta"]),
                    ft.Row(btns, wrap=True, spacing=4),
                ]
                if rid in g["conns"]:
                    cs = admin.connection_rows(g["conns"][rid])
                    for cn in cs:

                        async def rm(rid=rid, name=cn["name"]) -> str | None:
                            return await asyncio.to_thread(admin.remove_connection, d, client(), rid, name)

                        one.append(
                            ft.Row(
                                [
                                    ft.Column(
                                        [muted(cn["head"]), ft.Text(cn["word"], selectable=True)],
                                        spacing=0,
                                        expand=True,
                                    ),
                                    sure_button(
                                        "消す", "本当に消す", ("connection", f"{rid}/{cn['name']}"), rm
                                    ),
                                ],
                                vertical_alignment=ft.CrossAxisAlignment.START,
                            )
                        )
                    if not cs:
                        one.append(muted("寄せられた一言はありません。"))
                rec.append(ft.Column(one, spacing=4))
            out.append(card("公開された記録", rec or [muted("まだありません。")]))

            av = admin.audit_view(au)
            ac: list[ft.Control] = [err(av["head"]) if av["tampered"] else muted(av["head"])]
            ac += [
                ft.Column(
                    [
                        ft.Text(f"{ts}　{who}　{what}", selectable=True, size=14),
                        *([muted(det)] if det else []),
                    ],
                    spacing=0,
                )
                for ts, who, what, det in av["rows"]
            ] or [muted("まだありません。")]
            out.append(card("操作の記録（サーバー）", ac))
            out.append(
                muted(
                    "ここでした操作は、kagi の記録（audit.log）にも一行ずつ残る（誰に何を、まで。値は書かない）。"
                )
            )
            out.append(ft.OutlinedButton("読み直す", icon=ft.Icons.REFRESH, on_click=gate_reload))
            return out

        # ---- 在庫・口・走査・記録・報告 ----

        async def do_add_secret(e) -> None:
            vals = {k: f.value or "" for k, f in s_fields.items()}
            try:
                existing = inventory.load(d / "inventory.toml") if (d / "inventory.toml").exists() else []
            except inventory.Refused as ex:
                toast(str(ex))
                return
            errs = admin.check_secret_form(vals, existing)
            for k, f in s_fields.items():
                f.error = errs.get(k)
            if errs:
                page.update()
                return
            try:
                sid = admin.add_secret(d, vals)
            except inventory.Refused as ex:
                toast(str(ex))
                return
            for f in s_fields.values():
                f.value = ""
            toast(f"在庫表に足した: {sid}")
            render()

        def other_view() -> list[ft.Control]:
            c = build_cards(d, st["ss"], st["scan"], host=host())
            inv, prt, scn, aud = c["inventory"], c["ports"], c["scan"], c["audit"]

            inv_c: list[ft.Control] = [err(inv["error"])] if inv["error"] else [ft.Text(inv["summary"])]
            if inv["rows"]:
                inv_c.append(table(inv["columns"], inv["rows"]))

            sk = admin.server_keys_view(d, overview())
            sk_c: list[ft.Control] = [
                muted(
                    "seed のサーバーが外のサービスを呼ぶ鍵（Claude、Pl@ntNet）。サーバーには在るか無いかだけを尋ねる。"
                    "在庫表の行は、名か置き場に anthropic・claude、plantnet の語を含む物を、その鍵の一件と見る。"
                )
            ]
            if not sk["seen"]:
                sk_c.append(
                    muted("サーバーの在るか無いかは、まだ尋ねていない（「人の出入り」を開くと読む）。")
                )
            sk_c.append(table(sk["columns"], sk["rows"]))
            sk_c += [err(f"直す: {x}") for x in sk["fixes"]]
            sk_c += [muted(f"見るだけ: {x}") for x in sk["notes"]]
            add_c: list[ft.Control] = [
                muted("値の欄は無い。値らしい物（長い乱数、sk- で始まる物など）を貼ると断る。"),
                ft.Row([s_fields["id"], s_fields["holder"], s_fields["expires"]], wrap=True),
                *[ft.Row([s_fields[k]], wrap=True) for k in ("where", "role", "lost", "leaked")],
                ft.FilledButton("足す", icon=ft.Icons.ADD, on_click=do_add_secret),
            ]

            prt_c: list[ft.Control] = [
                muted("機械の画面の「写しを取る」でも口は突き合わせる。ここは ss の写しを手で渡す時。"),
                ft.Row(
                    [
                        ss_field,
                        host_field,
                        ft.OutlinedButton("選ぶ", icon=ft.Icons.FOLDER_OPEN, on_click=pick_ss),
                        ft.FilledButton("突き合わせる", on_click=use_ss),
                    ],
                    wrap=True,
                ),
                err(prt["error"]) if prt["error"] else ft.Text(prt["summary"]),
            ]
            if prt["rows"]:
                prt_c.append(table(prt["columns"], prt["rows"]))

            scn_c: list[ft.Control] = [
                ft.Row(
                    [
                        scan_field,
                        ft.OutlinedButton("選ぶ", icon=ft.Icons.FOLDER_OPEN, on_click=pick_scan),
                        ft.FilledButton("走らせる", on_click=use_scan),
                    ],
                    wrap=True,
                ),
                ft.Text(scn["summary"]),
            ]
            if scn["lines"]:
                scn_c.append(mono("\n".join(scn["lines"])))

            aud_c: list[ft.Control] = [ft.Text(aud["summary"])]
            if aud["warning"]:
                aud_c.append(err(f"注意: {aud['warning']}"))
            aud_c += [err(f) for f in aud["failures"]]
            if aud["key"]:
                if aud["error"]:
                    aud_c.append(err(aud["error"]))
                aud_c.append(
                    ft.Row(
                        [actor, action, detail, ft.FilledButton("一行足す", icon=ft.Icons.ADD, on_click=add)],
                        wrap=True,
                    )
                )
            else:
                aud_c.append(muted(aud["error"] or ""))

            rep_c: list[ft.Control] = [
                ft.FilledButton("報告を作る", icon=ft.Icons.DESCRIPTION_OUTLINED, on_click=make_report)
            ]
            if st["report"]:
                path, text = st["report"]
                rep_c += [muted(f"書いた: {path}"), mono(text)]
            return [
                card("在庫", inv_c),
                card("サーバーの外の鍵（在庫表と突き合わせ）", sk_c),
                card("在庫表に一件足す", add_c),
                card("口", prt_c),
                card("走査", scn_c),
                card("記録", aud_c),
                card("報告", rep_c),
            ]

        def render() -> None:
            tabs = []
            for key, label, icon in (
                ("machines", "機械", ft.Icons.DNS_OUTLINED),
                ("keys", "鍵", ft.Icons.KEY_OUTLINED),
                ("other", "在庫・口・記録", ft.Icons.FACT_CHECK_OUTLINED),
                ("gate", "人の出入り", ft.Icons.PEOPLE_OUTLINE),
            ):
                btn = ft.FilledTonalButton if st["view"] == key else ft.TextButton
                tabs.append(btn(label, icon=icon, on_click=switch(key)))
            views = {
                "machines": machines_view,
                "keys": keys_view_controls,
                "other": other_view,
                "gate": gate_view,
            }
            view = views[st["view"]]()
            body.controls = [
                ft.Text(
                    "kagi — 係の机", theme_style=ft.TextThemeStyle.HEADLINE_SMALL, weight=ft.FontWeight.W_600
                ),
                muted(f"作業の場所: {d}"),
                *paused_banner(),
                ft.Row(tabs, wrap=True),
                *view,
                muted("鍵の値はこの画面に出ない（K-13）。kagi は機械を変えない。"),
            ]
            page.update()

        def path_of(f: ft.TextField) -> Path | None:
            v = (f.value or "").strip()
            return Path(v).expanduser() if v else None

        async def pick_ss(e) -> None:
            files = await picker.pick_files(dialog_title="ss -ltnp の写しを選ぶ")
            if files and files[0].path:
                ss_field.value = files[0].path
                await use_ss(e)
            elif files:
                toast("道が取れない（ブラウザで開いた時）。道を書いて「突き合わせる」")

        async def use_ss(e) -> None:
            st["ss"] = path_of(ss_field)
            render()

        async def pick_scan(e) -> None:
            path = await picker.get_directory_path(dialog_title="走査する場所を選ぶ")
            if path:
                scan_field.value = path
                await use_scan(e)

        async def use_scan(e) -> None:
            p = path_of(scan_field)
            st["scan"] = [p] if p else []
            render()

        async def add(e) -> None:
            vals = [(f.value or "").strip() for f in (actor, action, detail)]
            if not all(vals):
                toast("actor、action、detail の三つを書く")
                return
            try:
                key = audit.find_key()
                seq = audit.add(d / "audit.log", key, *vals)
            except audit.NoKey:
                toast("署名の鍵が無い（skills/kagi/02-記録.md）")
                return
            except (ValueError, OSError) as ex:
                toast(str(ex) if isinstance(ex, ValueError) else f"記録に書けない ({type(ex).__name__})")
                return
            action.value = detail.value = ""
            toast(f"足した: seq {seq}")
            render()

        async def make_report(e) -> None:
            try:
                st["report"] = await asyncio.to_thread(write_report, d, st["ss"], st["scan"], host=host())
            except OSError as ex:
                toast(f"報告を書けない ({type(ex).__name__})")
                return
            render()

        frame = ft.Container(body, padding=16, expand=True)

        def fit(e=None) -> None:
            # 窓が狭い時は窓の幅に合わせる（決めた幅のままだと右が切れる）
            w = page.width or 0
            frame.width = min(MAX_WIDTH, w) if w else MAX_WIDTH
            if e is not None:
                page.update()

        page.on_resize = fit
        fit()
        page.add(frame)
        page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
        render()

    return main


def run(d: Path) -> None:
    """PC の窓で開く。`KAGI_APP_WEB_PORT` がある時だけ、確かめるためにブラウザのタブで開く。"""
    import flet as ft

    port = os.environ.get(WEB_PORT_ENV, "").strip()
    if port.isdigit():
        ft.run(make_main(d), view=ft.AppView.WEB_BROWSER, host="127.0.0.1", port=int(port))
    else:
        ft.run(make_main(d))
