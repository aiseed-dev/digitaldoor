# デジタルドア — 情報サイト

2026年9月17日。尼寺康泰。

扉と鍵の情報サイト。読取機、錠、扉のコントローラ、鍵の発行、規格(Aliro、Matter)について、事実と噂を分けて書く。最初の記事はAppleの顔認証ドアホン。

## 構成

| 場所 | 中身 |
|---|---|
| `news/` | ニュース。`sources.json`(取り口)、`keywords.json`(絞る語)、`items.json`(集めた物。AIが整えた題・一行・印) |
| `articles/guide.adoc` | 解説(規格と機器。番号順、/guide/) |
| `articles/blog.adoc` | ブログ(時事のノート。日付順、/blog/)。どちらも AsciiDoc、日本語。英語は `ifdef::lang-en[]` で足せる |
| `articles/assets/blog/<記事ID>/` | 画像・PDF |
| `html/` | 公開されるHTML。`index.html`、`404.html`、`license/`、`_headers`、`css/`、`images/`(OG画像とアイコン)は手で置く。`guide/`、`blog/`、`sitemap.xml`、`feed.xml` はビルドで作られる |
| `tools/` | ビルドの仕組み(aiseed.dev のサイトと同じ物。`init_site.py` で複写)とニュースの道具 |
| `site.json` | サイトの設定(名前、URL、シリーズ) |

## 動かす

依存は `requirements.txt`(aiseed.dev のサイトと同じビルドの道具に、ニュース用の anthropic と公開用の httpx・blake3)。

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python tools/build_article.py --all     # 全記事とインデックスと sitemap
.venv/bin/python tools/build_feed.py               # RSS(feed.xml)
.venv/bin/python tools/collect_news.py             # ニュースを集めて AI で整える(要 ANTHROPIC_API_KEY)
.venv/bin/python tools/build_news.py               # /news/ とトップのニュース
.venv/bin/python tools/serve.py --port 8000       # 保存 → 自動ビルド → ブラウザ更新
```

公開先は https://door.aiseed.dev 。Cloudflare Pages の Git 連携で、GitHub の main に push すると出る(ビルドコマンド無し、出力ディレクトリ `html`、`html/` は組んだ物ごと置き場に入れてある)。カスタムドメイン door.aiseed.dev は Pages のプロジェクト door-aiseed-dev に付ける。

## 書き方

記事は `// ===== article: <記事ID> =====` で区切り、フロントマター(`slug`、`date`、`title.ja`、`subtitle.ja`、`description.ja`、`category.ja`)の後に `ifdef::lang-ja[]` 〜 `endif::[]` で本文。出典は末尾に「== 出典」として、誰がいつ何を書いたかが分かる形で並べる。

## ライセンス

文章は CC BY 4.0、コードは AGPL-3.0-or-later。[LICENSE](LICENSE)。

## ニュース(AIが広めに集め、人が選ぶ)

`tools/collect_news.py` が `news/sources.json` の取り口(Google News の検索 feed 日英 17 本、CSA、9to5Mac、MacRumors)を広めに読み、直近 30 日の新しい物を `news/items.json` に足し、Claude に一件ずつ「日本語の題、一行の要約、区分(規格・読取機・錠・扉・鍵の発行・事件・企業)、印(発表・報道・噂・解説)、関係がありそうかの見立て」を付けさせる。要約は原文にあることだけ、個人名は書かない。AI の資格情報が無いときは題のまま置き、後で `--retry` で整える。

載せる物は人が選ぶ。`tools/pick_news.py`(Flet 1.0 以上が要るので別の環境で: `python3 -m venv .venv-pick && .venv-pick/bin/pip install "flet>=1.0"`、`.venv-pick/bin/python tools/pick_news.py --web`)で一覧を見て印を付け、保存すると `picked` が items.json に残る。`tools/build_news.py` は選んだ物だけを `/news/` とトップに出す。

毎朝 06:00(JST)に GitHub Actions(`.github/workflows/news.yml`)が集めて整え、組んで、`news/items.json` と `html/` を記録して push する。push で Cloudflare Pages が出す。人が選んだ分は、選んで保存して組んで push すれば出る。リポジトリの秘密に入れるのは `ANTHROPIC_API_KEY` の一つ。
