# digitaldoor-controller — 疑似の扉と試験の道具

[digitaldoor](https://door.aiseed.dev) の扉コントローラ仕様書を確かめるための、扉側のコントローラです。仕様の本文は同じリポジトリの `docs/`(https://github.com/aiseed-dev/digitaldoor)にあり、この `controller/` はそれに沿って動く物の一つです。ほかのコード(AGPL-3.0-or-later)とは別のパッケージで、ライセンスは Apache-2.0 です。

## 何のための物か

- **仕様を確かめる。** 仕様書の試験の対を実際に回し、仕様に矛盾が無く作れることを確かめます。テストの docstring の先頭に、確かめる要求の番号(R-CT-nn、R-AC-nn など)を書いています。
- **疑似の扉として使う。** リーダー、鍵の発行者、管理ソフト、Matter の橋を作る人が、実機なしで相手にする扉です。接点は模擬(`SimContacts`)に差し替えて動きます。
- **後付けの錠の前で使う。** 事業所のサーバーなどが、後付けの錠の前で判断と記録を持つ部品として使えます。

**認定を受けた製品ではありません。** 扉に入れる製品では、仕様書の第2章のとおり、安全側(接点、火災と停電の一次の挙動、機械的な解放)を MCU に分け、署名の鍵をセキュアエレメントに置いてください。このコードは、その判断側と界面側を確かめるための物です。

## 持っている物

| モジュール | 中身 | 仕様書 |
|---|---|---|
| `controller.py` | 判断。火災、停電、避難、二人同時、強要、自動施錠、設定の記録、時刻のずれ | 第3章、第6章、第7章 |
| `audit.py` | 監査の記録。連番、前ハッシュの連鎖、署名(Ed25519、試験用に HMAC) | 第4章 |
| `slips.py` | 記録を署名つきの伝票として箱へ出す。受け手は公開鍵だけで検べる | 5.1 |
| `server.py` | HTTP のインターフェース(状態、命令、リーダーの判定結果、記録の差分、検査) | 5.2、5.3 |
| `contacts.py` | 接点のインターフェースと、模擬の接点 | 第2章 |
| `cli.py` | 命令行 `digitaldoor-controller` | |
| `credentials.py` | 発行者の署名つき資格情報。発行者の側で資格情報と信頼の一覧の変更の要求を作り、扉の側で署名、範囲、期限、一回限りを確かめる | 5.2a |

`Controller` は `present`(資格情報を確かめて受ける)と `change_trust`(管理者の署名つきで信頼の一覧を変える)を持ち、HTTP のインターフェースは `POST /credential`、`POST /trust`、`GET /trust` を持ちます。`/lock` と `/unlock` は同じコンピューターの中からの要求だけを受けます。

## 使い方

```bash
# digitaldoor のリポジトリの最上位で
python3 -m venv .venv
.venv/bin/pip install -e "./controller[dev]"
.venv/bin/python -m pytest -q controller
.venv/bin/digitaldoor-controller --dir vault --name 玄関 serve
.venv/bin/digitaldoor-controller --dir vault pubkey
.venv/bin/digitaldoor-controller check-box --box BOX --door 玄関 --pubkey HEX
```

## 仕様との対応

リポジトリの最上位で `python3 tools/taiou.py --check` を実行すると、要求の番号ごとに、ここのテストを並べた対応表(`docs/適合の現在.md`)ができます。

## ライセンス

Apache License 2.0 です(`LICENSE`)。製品に組み込んで、閉じたまま配ることもできます。仕様の文書(digitaldoor)は CC BY 4.0 です。
