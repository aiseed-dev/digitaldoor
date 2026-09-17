# デジタルドア — 情報サイト

扉と鍵についての情報サイトです。ドアホン、電子錠、扉のコントローラ、鍵の発行、規格(Aliro、Matter)について、事実と噂を分けて書きます。公開先は https://door.aiseed.dev です。

## ディレクトリの構成

| ディレクトリ・ファイル | 内容 |
|---|---|
| `articles/guide.adoc` | 解説記事。規格と機器の説明。番号順に並び、`/guide/` に公開されます |
| `articles/blog.adoc` | ブログ記事。時事のノート。日付順に並び、`/blog/` に公開されます |
| `articles/assets/blog/<記事ID>/` | 記事で使う画像や PDF |
| `news/` | ニュースのデータ。`sources.json`(収集元の一覧)、`keywords.json`(絞り込みのキーワード)、`items.json`(収集したニュース) |
| `html/` | 公開する HTML。`index.html`、`404.html`、`license/`、`_headers`、`css/`、`images/` は手で編集します。`guide/`、`blog/`、`news/`、`sitemap.xml`、`feed.xml` はビルドで自動生成されます |
| `tools/` | ビルドスクリプトとニュース収集のスクリプト。ビルドの仕組みは aiseed.dev のサイトと同じものです |
| `site.json` | サイトの設定(サイト名、URL、記事のシリーズ) |

記事は AsciiDoc 形式で、1 つのファイルに全記事を書きます。日本語だけで書けます。英語版を付ける場合は `ifdef::lang-en[]` のブロックを追加します。

## セットアップとビルド

Python 3.12 以上が必要です。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python tools/build_article.py --all     # 記事、一覧ページ、sitemap.xml を生成
.venv/bin/python tools/build_feed.py               # RSS フィード(feed.xml)を生成
.venv/bin/python tools/build_news.py               # ニュースページとトップページのニュース欄を生成
.venv/bin/python tools/serve.py --port 8000       # 開発サーバー。ファイルを保存すると自動でビルドしてブラウザを更新
```

## 公開

Cloudflare Pages の Git 連携を使っています。GitHub の `main` ブランチに push すると自動で公開されます。ビルドコマンドは不要で、出力ディレクトリは `html` です(ビルド済みの HTML をリポジトリに含めています)。カスタムドメイン door.aiseed.dev は Cloudflare Pages のプロジェクト `door-aiseed-dev` に設定します。

## 記事の書き方

記事は `// ===== article: <記事ID> =====` という行で区切ります。その後にフロントマター(`slug`、`date`、`title.ja`、`subtitle.ja`、`description.ja`、`category.ja`)を書き、`ifdef::lang-ja[]` から `endif::[]` の間に本文を書きます。出典は記事の末尾に「== 出典」という見出しでまとめ、誰がいつ書いた記事かが分かるようにします。

## ニュース(AI が集めて、人が選ぶ)

`tools/collect_news.py` が、`news/sources.json` に登録した収集元(Google News の検索フィード 17 本、CSA、9to5Mac、MacRumors)から直近 30 日のニュースを集め、`news/items.json` に追加します。集めた記事は Claude(Anthropic の AI)に渡し、日本語の見出し、一行の要約、分類(規格・ドアホン・錠・扉・鍵の発行・事件・企業)、種別(発表・報道・噂・解説)、関係がありそうかの判定を付けてもらいます。要約は元の記事に書いてあることだけを使い、個人名は書きません。AI の API キーが無い場合は見出しだけを保存し、後で `--retry` オプションで整えられます。

サイトに載せるニュースは人が選びます。`tools/pick_news.py` を起動すると一覧が表示されるので、載せたい記事にチェックを付けて保存します。この画面は Flet 1.0 以上が必要なので、別の仮想環境を作ります。

```bash
python3 -m venv .venv-pick
.venv-pick/bin/pip install "flet>=1.0"
.venv-pick/bin/python tools/pick_news.py --web
```

保存すると `news/items.json` の `picked` が更新されます。`tools/build_news.py` は選んだ記事だけをニュースページとトップページに載せます。

毎朝 6 時(日本時間)に GitHub Actions(`.github/workflows/news.yml`)がニュースを集めて整え、サイトをビルドし、`news/items.json` と `html/` をコミットして push します。push すると Cloudflare Pages が自動で公開します。人が選んだ記事も、保存してビルドして push すれば公開されます。GitHub のリポジトリの Secrets に `ANTHROPIC_API_KEY` を登録しておいてください。

## ライセンス

文章は CC BY 4.0、コードは AGPL-3.0-or-later です。詳しくは [LICENSE](LICENSE) を見てください。
