# ニュースの仕組み(AI が集めて、人が選ぶ)


`tools/collect_news.py` が、`news/sources.json` に登録した収集元(Google News の検索フィード 17 本、CSA、9to5Mac、MacRumors)から直近 30 日のニュースを集め、`news/items.json` に追加します。集めた記事は Claude(Anthropic の AI)に渡し、日本語の見出し、一行の要約、分類(規格・ドアホン・錠・扉・鍵の発行・事件・企業)、種別(発表・報道・噂・解説)、関係がありそうかの判定を付けてもらいます。要約は元の記事に書いてあることだけを使い、個人名は書きません。AI の API キーが無い場合は見出しだけを保存し、後で `--retry` オプションで整えられます。

サイトに載せるニュースは人が選びます。`tools/pick_news.py` を起動すると一覧が表示されるので、載せたい記事にチェックを付けて保存します。この画面は Flet 1.0 以上が必要なので、別の仮想環境を作ります。

```bash
python3 -m venv .venv-pick
.venv-pick/bin/pip install "flet>=1.0"
.venv-pick/bin/python tools/pick_news.py --web
```

保存すると `news/items.json` の `picked` が更新されます。`tools/build_news.py` は選んだ記事だけをニュースページとトップページに載せます。

毎朝 6 時(日本時間)に GitHub Actions(`.github/workflows/news.yml`)がニュースを集めて整え、サイトをビルドし、`news/items.json` と `html/` をコミットして push します。push すると Cloudflare Pages が自動で公開します。人が選んだ記事も、保存してビルドして push すれば公開されます。GitHub のリポジトリの Secrets に `ANTHROPIC_API_KEY` を登録しておいてください。
