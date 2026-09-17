#!/usr/bin/env python3
"""ニュースを選ぶ画面(Flet)。AI が広めに集めた物から、載せる物を人が選ぶ。

    python3 tools/pick_news.py           # 窓で開く
    python3 tools/pick_news.py --web     # ブラウザで開く(8552)

Flet 1.0 以上が要る(サイトのビルド環境とは別に `pip install "flet>=1.0"` した環境で動かす)。
選んだ物は news/items.json の picked に残る。保存したら tools/build_news.py で組む。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import flet as ft

SITE = Path(__file__).resolve().parent.parent
ITEMS = SITE / "news" / "items.json"


async def main(page: ft.Page) -> None:
    page.title = "ニュースを選ぶ — デジタルドア"
    page.padding = 20
    items: list[dict] = json.loads(ITEMS.read_text(encoding="utf-8"))
    state = {"query": "", "show": "all"}  # all | unpicked | picked | ai
    listing = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
    count = ft.Text("", size=13)

    def visible() -> list[dict]:
        q = state["query"].lower()
        out = []
        for i in items:
            if state["show"] == "unpicked" and i.get("picked"):
                continue
            if state["show"] == "picked" and not i.get("picked"):
                continue
            if state["show"] == "ai" and not i.get("relevant"):
                continue
            text = " ".join(str(i.get(k) or "") for k in ("title", "title_raw", "summary", "summary_raw", "publisher", "source")).lower()
            if q and q not in text:
                continue
            out.append(i)
        return out

    def toggle(item: dict):
        async def handler(e):
            item["picked"] = bool(e.control.value)
            refresh()
        return handler

    def row(i: dict) -> ft.Control:
        title = i.get("title") or i["title_raw"]
        meta = " · ".join(x for x in ((i.get("published") or "")[:10], i.get("publisher") or i.get("source"), i.get("kind"), i.get("category"),
                                      "AI: 関係あり" if i.get("relevant") else ("AI: 薄い" if i.get("status") == "ai" else "未整理")) if x)
        return ft.Container(
            ft.Row([
                ft.Checkbox(value=bool(i.get("picked")), on_change=toggle(i)),
                ft.Column([
                    ft.Text(title, size=15, weight=ft.FontWeight.W_600),
                    ft.Text(i.get("summary") or ("" if (i.get("summary_raw") or "").startswith(i["title_raw"][:20]) else (i.get("summary_raw") or "")[:160]), size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Row([ft.Text(meta, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                            ft.TextButton("開く", url=i["link"])], spacing=8),
                ], spacing=2, expand=True, tight=True),
            ], vertical_alignment=ft.CrossAxisAlignment.START),
            padding=ft.Padding.symmetric(vertical=8), border=ft.Border.only(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))

    def refresh():
        rows = visible()
        listing.controls = [row(i) for i in rows[:300]]
        picked = sum(1 for i in items if i.get("picked"))
        count.value = f"表示 {len(rows)} 件 / 全 {len(items)} 件 / 選んだ物 {picked} 件"
        page.update()

    async def on_query(e):
        state["query"] = e.control.value or ""
        refresh()

    async def on_show(e):
        state["show"] = e.control.value
        refresh()

    async def save(e):
        ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        page.show_dialog(ft.SnackBar(ft.Text(f"保存しました: {ITEMS}")))
        page.update()

    page.add(
        ft.Row([
            ft.Text("ニュースを選ぶ", size=22, weight=ft.FontWeight.W_600),
            ft.TextField(label="絞る", width=260, on_change=on_query),
            ft.Dropdown(value="all", width=200, on_select=on_show, options=[
                ft.DropdownOption("all", "全部"), ft.DropdownOption("unpicked", "未選択"),
                ft.DropdownOption("picked", "選んだ物"), ft.DropdownOption("ai", "AIが関係ありとした物")]),
            ft.FilledButton("保存", icon=ft.Icons.SAVE, on_click=save),
            count,
        ], wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        listing,
    )
    refresh()


def run(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    web = "--web" in argv
    ft.run(main, view=ft.AppView.WEB_BROWSER if web else ft.AppView.FLET_APP, port=8552 if web else 0)
    return 0


if __name__ == "__main__":
    sys.exit(run())
