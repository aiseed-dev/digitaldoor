# デジタルドア(情報サイト) — 作業の約束

- 手順は docs/(setup.md、writing.md、news.md)。README は短く保ち、手順は docs/ に書く
- 解説は `articles/guide.adoc`(番号順)、ブログは `articles/blog.adoc`(日付順)。AsciiDoc、日本語。ビルドは `.venv/bin/python tools/build_article.py --all`(requirements.txt の環境)、確認は `tools/serve.py`
- 報道は事実と噂を分けて書く。誰が、いつ、どこに書いたかを添える。会社が発表したことと、報道されただけのことを混ぜない
- 出典は記事の末尾に「== 出典」で並べる。引用は短く、要約は自分の言葉で
- 層の名前で書く(読取機、錠、扉のコントローラ、鍵の発行、機器と業務のつなぎ)。番号や作った呼び名を題に置かない
- 組織名と個人名は、公開の報道にある物だけ。取引先や個人の特定につながる情報は書かない
- 見出しの言葉は読み手に通じる言葉で。IT の隠語は使わない。README や docs は普通の技術用語と です・ます で書く
- `html/guide/`、`html/blog/`、`sitemap.xml`、`feed.xml` はビルドで作られる。手で直さない。直すのは `articles/` とテンプレート。ビルドの後に `tools/build_feed.py` も走らせる
- 計測(Google Analytics 等)は入れていない。入れるなら本人の判断で、プライバシーの頁と一緒に
- commit は本人の合図で。push は勝手にしない
- ニュースは広めに集めて、載せる物は人が選ぶ(`picked`)。AI の relevant は見立てで、絞りに使わない。`news/items.json` が正。手で直すなら title/summary/kind/category を直し、status は ai のまま。取り口を足すのは `news/sources.json`。AI の指示文は `tools/collect_news.py` の SYSTEM(事実と噂を分ける、原文に無いことを足さない、個人名を書かない)
- **git push は Claude はしない。** 尼寺さんが内容を確認してから自分で push する。commit までは指示があればよい

## ソフトウェア(digitalkey/)の約束
- 試験: `.venv/bin/python -m pytest -q`(entrance・door・panel の全部)。commit は全試験の緑を確認してから、別コマンドで。push は勝手にしない(ユーザーが押す)
- 設計判断は docs/SEKKEI.md(entrance)と docs/扉コントローラ仕様書.md(door)。決めたことと違う実装をしない。変えるなら文書を先に直す
- 様式(digitalkey/entrance/forms_data/*.adoc)が正。コードに項目名を埋め込むときは様式の名前をそのまま使う
- 伝票も監査の記録も変えない。訂正は新しい伝票・新しい記録。試験もそれを前提に書く
- 判断は door が持つ。panel(Matter の橋と盤)は翻訳と表示だけ。判断を panel に書かない
- 接点の下に錠の銘柄を隠す。錠固有のコードは接点の層(door)か駆動の差し替え口(entrance の LockDriver)として書く
- 署名の口(Signer)は後で STSAFE に差し替える
- 機器は買う。読取機・機器のコードは書かない(駆動の差し替え口だけ)
- Matter の機器はプロセスに一つ(UDP 5541)。画面の接続ごとに作らない
- 起動確認の後は avahi-publish-service の子プロセスと UDP 5541 / TCP 8797 / 8798 を空ける(`fuser -k`)。pkill -f は自分の殻を殺すので使わない
- 個人情報(画像・名簿)を試験で本物にしない。バイト列の代用でよい
- Sesame の鍵(sesame-keys.json)は置き場に入れない。試験と画面の確認は疑似の Sesame(fake.py、`--fake`)で。実機は BLE アダプタを挿してから
- 公開する物なので、組織名と個人名を文書に入れない。製品名(CANDY HOUSE の Sesame 等)はよい
- 細部は作るときに選ぶ。設計書には作った物と選んでいる物を書く。肯定形で書く
