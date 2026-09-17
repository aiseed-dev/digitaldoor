# デジタルドア — 作業の約束

## サイト

- 手順は docs/ に書く(setup.md、writing.md、news.md)。README は概要と docs への案内だけにする
- 解説は `articles/guide.adoc`(番号順)、ブログは `articles/blog.adoc`(日付順)。AsciiDoc、日本語で書く。ビルドは `.venv/bin/python tools/build_article.py --all`、続けて `tools/build_feed.py` と `tools/build_news.py`。確認は `tools/serve.py`
- 報道は、会社が正式に発表したことと、報道されただけのことを分けて書く。誰が、いつ、どこに書いたかを添える
- 出典は記事の末尾に「== 出典」でまとめる。引用は短く、要約は自分の言葉で書く
- 層の名前で書く(ドアホン、電子錠、扉のコントローラ、鍵の発行、業務システムとの連携)。題は読み手に通じる言葉にする
- 組織名と個人名は、公開の報道にある物だけを書く
- README、docs、サイトの説明文は、一般的な技術用語と です・ます調で書く。記事も普通の解説とブログの文体で書く
- `html/guide/`、`html/blog/`、`html/news/`、`sitemap.xml`、`feed.xml` はビルドで作る。直すときは `articles/`、`news/`、テンプレートを直してビルドし直す
- アクセス計測を入れるときは本人が決め、プライバシーポリシーの頁と一緒に入れる
- ニュースは広めに集め、載せる物は人が選ぶ(`picked`)。AI の relevant は参考の見立てとして扱う。`news/items.json` が正で、手で直すときは title/summary/kind/category を直し、status は ai のままにする。収集元を足すときは `news/sources.json`。AI への指示文は `tools/collect_news.py` の SYSTEM(原文にあることだけ書く、発表と報道を分ける、製品名と会社名は原文のまま)

## ソフトウェア(digitalkey/)

- テストは `.venv/bin/python -m pytest -q`(entrance・door・panel・sesame の全部)。commit は全テストが通ってから、別コマンドで行う
- 設計判断は docs/SEKKEI.md(entrance)と docs/扉コントローラ仕様書.md(door)に書く。実装を変えるときは文書を先に直す
- 様式(digitalkey/entrance/forms_data/*.adoc)が正。コードに項目名を書くときは様式の名前をそのまま使う
- 伝票と監査の記録は追記だけで扱う。訂正は新しい伝票・新しい記録として書く。テストもその前提で書く
- 判断は door が持つ。panel(Matter のブリッジと画面)は翻訳と表示を担当する
- 錠の銘柄は接点の層(door)か駆動の差し替え口(entrance の LockDriver)の中に閉じる
- 署名の口(Signer)は後で STSAFE に差し替える
- 機器は買う。書くのは駆動の差し替え口まで
- Matter の機器はプロセスに一つ(UDP 5541)。画面の接続をまたいで共有する
- 起動確認の後は avahi-publish-service の子プロセスと UDP 5541 / TCP 8797 / 8798 を `fuser -k` で空ける
- テストの個人情報(画像・名簿)は、バイト列や架空の名前で代用する
- Sesame の鍵(sesame-keys.json)は手元だけに置く(.gitignore 済み)。テストと画面の確認は疑似の Sesame(fake.py、`--fake`)で行う。実機は BLE アダプタを挿してから
- 公開する物なので、文書に書く固有名は製品名と会社名(CANDY HOUSE の Sesame 等)にとどめる
- 細部は作るときに選ぶ。設計書には作った物と選んでいる物を書く。肯定形で書く

## git

- commit は本人の合図で行う
- push は本人が内容を確認してから自分で行う
