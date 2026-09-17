# 記事の書き方


記事は `// ===== article: <記事ID> =====` という行で区切ります。その後にフロントマター(`slug`、`date`、`title.ja`、`subtitle.ja`、`description.ja`、`category.ja`)を書き、`ifdef::lang-ja[]` から `endif::[]` の間に本文を書きます。出典は記事の末尾に「== 出典」という見出しでまとめ、誰がいつ書いた記事かが分かるようにします。
