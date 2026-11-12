# デジタルドア — 作業の約束

## サイト

- 手順は docs/使い方.md に書く。README は概要と docs への案内だけにする。考え方や背景は文書にせず、サイトの記事(articles/)にする
- ユーザーマニュアルは `tools/build_manual.py` がコードから作る。説明はコードの docstring と --help に書く。機器(Sesame 等)の詳しい説明もコードの側に書く
- 解説は `articles/guide.adoc`(番号順)、ブログは `articles/blog.adoc`(日付順)。AsciiDoc、日本語で書く。ビルドは `.venv/bin/python tools/build_article.py --all`、続けて `tools/build_feed.py`、`tools/build_news.py`、`tools/build_manual.py`。確認は `tools/serve.py`
- 報道は、会社が正式に発表したことと、報道されただけのことを分けて書く。誰が、いつ、どこに書いたかを添える
- 出典は記事の末尾に「== 出典」でまとめる。引用は短く、要約は自分の言葉で書く
- 層の名前で書く(ドアホン、電子錠、扉のコントローラ、鍵の発行、業務システムとの連携)。題は読み手に通じる言葉にする
- 組織名と個人名は、公開の報道にある物だけを書く
- README、docs、サイトの説明文は、一般的な技術用語と です・ます調で書く。記事も普通の解説とブログの文体で書く
- `html/guide/`、`html/blog/`、`html/news/`、`sitemap.xml`、`feed.xml` はビルドで作る。直すときは `articles/`、`news/`、テンプレートを直してビルドし直す
- アクセス計測を入れるときは本人が決め、プライバシーポリシーの頁と一緒に入れる
- ニュースは aiai と同じ作り方にする。開発機の AI の CLI が `news/SKILL.md`(ガイド)と `news/好み.adoc`(分野、まず見る所、件数)に沿って、元の記事を開いて確かめ、`news/YYYY-MM-DD.adoc` を 1 日 1 つ書く(`news/tsukuru.py`)。人が読んで直し、commit した物だけが載る。サーバーと GitHub Actions では作らず、API キーも置かない。分野や件数を変えるときは `好み.adoc`、書く決まり(発表と報道を分ける、製品名と会社名は原文のまま、確かめた日つきの出典)は `SKILL.md` を直す

- 要求を足すときは、理由に出どころ(Issue の番号か、使う人の困りごとの文)を書く。番号は変えない。製造者の制約と代わりの案は、同じ番号の下に残す(docs/つなぐ.md)
- SKILL.md は、各社の AI に読ませる入口。文書やコードの置き場や進め方を変えたら SKILL.md も直す

## ソフトウェア(digitalkey/)

- テストは `.venv/bin/python -m pytest -q`(entrance・panel・site・mobile)と `pytest -q controller`(扉のコントローラ)と `pytest -q drivers/digitalkey-sesame`(Sesame ドライバ)。commit は全テストが通ってから、別コマンドで行う
- 設計判断は docs/使い方.md の「設計方針」(entrance)と docs/扉コントローラ仕様書.md(door)に書く。実装を変えるときは文書を先に直す
- 様式(digitalkey/entrance/forms_data/*.adoc)が正。コードに項目名を書くときは様式の名前をそのまま使う
- 伝票と監査の記録は追記だけで扱う。訂正は新しい伝票・新しい記録として書く。テストもその前提で書く
- 扉のコントローラは `controller/`(別パッケージ `digitaldoor_controller`、Apache-2.0。`controller/LICENSE`)。仕様は docs/ が正で、実装を変えるときは仕様書を先に直す。`digitalkey/site` は pip で入れて使う。テストは `.venv/bin/python -m pytest -q controller`。AGPL のコードを `controller/` に写さない
- 判断は扉のコントローラが持つ。panel(Matter のブリッジと画面)は翻訳と表示を担当する
- 錠の銘柄は接点の層(door)か錠ドライバ(digitalkey.locks の登録簿。機器ごとのドライバは drivers/ の別パッケージで、entry point で登録する)の中に閉じる
- 署名のインターフェース(Signer)は後で STSAFE に差し替える
- 機器は買う。書くのは駆動の差し替え用のインターフェースまで
- Matter の機器はプロセスに一つ(UDP 5541)。画面の接続をまたいで共有する
- 起動確認の後は avahi-publish-service の子プロセスと UDP 5541 / TCP 8797 / 8798 を `fuser -k` で空ける
- テストの個人情報(画像・名簿)は、バイト列や架空の名前で代用する
- Sesame の鍵(sesame-keys.json)は手元だけに置く(.gitignore 済み)。テストと画面の確認は疑似の Sesame(fake.py、`--fake`)で行う。実機は BLE アダプタを挿してから
- 公開する物なので、文書に書く固有名は製品名と会社名(CANDY HOUSE の Sesame 等)にとどめる。提案先の会社の名前は、文書にも記事にもコミットメッセージにも書かない(2026-10-17)
- テストは、確かめる要求の番号(R-XX-nn)を docstring の先頭の行に書く。`python3 tools/taiou.py --check` で docs/適合の現在.md を作り直し、番号の無い要求は未検証として残す。要求仕様を直したらこれも作り直す
- 細部は作るときに選ぶ。設計書には作った物と選んでいる物を書く。肯定形で書く

## git

- commit は本人の合図で行う
- push は本人が内容を確認してから自分で行う
