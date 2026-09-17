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
