# セットアップ・ビルド・公開

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
