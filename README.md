# デジタルドア — 情報サイト

電子錠、ドアホン、扉のコントローラ、鍵の発行、通信規格(Aliro、Matter)など、扉と鍵に関する技術情報を解説するサイトです。

公開先: https://door.aiseed.dev

## 各社で、AI を使ってソフトウェアを作る

このリポジトリは公開です。扉メーカー、錠メーカー、ドアホンやカメラのメーカー、設備工事会社、管理会社が、ここにある仕様と参照実装を土台にして、自分の AI と一緒に自社のソフトウェアを作れるようにしています。進め方は [SKILL.md](SKILL.md) にあります。ふだん使っている AI に読ませ、自社の範囲を伝えると、仕様、テスト、実装の順に一緒に作ります。

## 使う人と製造者をつなぐ

開発の間も、扉を使う人と扉を作る製造者の間に立ちます。使う人は困りごとを自分の言葉で Issue に書き、運営と AI がそれを要求の番号に整え、製造者は満たす番号を申告し、満たせない物と代わりの案を同じ番号の下に返します。確かめるのはテストと記録です。流れは [docs/つなぐ.md](docs/つなぐ.md) に、入り口は [Issues](https://github.com/aiseed-dev/digitaldoor/issues/new/choose) にあります。

## 扉の製造者向け

扉の製造者に「何を満たすか」を伝える要求仕様と、扉側のコントローラの仕様です。仕様を確かめる疑似の扉と試験の道具は、[controller/](controller/)(パッケージ digitaldoor-controller、Apache-2.0)にあります。認証制度ではなく実用の仕様で、料金と会費はありません。満たしているかどうかは、公開されたテストを自分で実行すれば分かります。

| 文書 | 内容 |
|---|---|
| [docs/要求仕様/README.md](docs/要求仕様/README.md) | 要求仕様の文書群。共通コア、電動ラッチ、認定の境界と標準インターフェース、読取機（ドアホンと顔認識）、用途別のプロファイル（個別住宅、集合住宅、事業所、室内、防火区画）、開発用ミニサーバー |
| [docs/扉コントローラ仕様書.md](docs/扉コントローラ仕様書.md) | 扉側コントローラの仕様（要求 R-CT-01〜25）。疑似の扉と試験の道具は `controller/`、Matter の橋は `digitalkey/panel/` |
| [docs/適合の現在.md](docs/適合の現在.md) | 要求の番号とテストの対応。`python tools/taiou.py` が作ります。番号の無い要求は未検証です |

## 受け入れをする人向け

| 文書 | 内容 |
|---|---|
| [docs/使い方.md](docs/使い方.md) | セットアップ、ビルド、公開、記事の書き方、ニュースの仕組み、ソフトウェアの設計方針 |
| [docs/manual.md](docs/manual.md) | ユーザーマニュアル。`tools/build_manual.py` がコードから自動生成します（サイトの /manual/ にも掲載） |

考え方や背景は、サイトの解説記事（/guide/）とブログ（/blog/）に書いています。

## ソフトウェア

同じリポジトリに、扉と鍵のソフトウェア(Python パッケージ `digitalkey`)を含めています。

| ディレクトリ | 内容 |
|---|---|
| `digitalkey/entrance/` | 鍵の発行と失効、本人確認、台帳、報告、バックアップ。民泊・貸家・WWOOF の受け入れ向け |
| `controller/` | 扉側のコントローラ(別パッケージ digitaldoor-controller)。仕様を確かめる疑似の扉と試験の道具(Apache-2.0)。火災・停電・避難時の判断、監査ログ、接点の抽象化、HTTP API。`digitalkey/site` はこれを pip で入れて使います |
| `digitalkey/panel/` | 扉を Matter の電子錠として見せるブリッジと、操作画面(Flet) |
| `digitalkey/site/` | 事業所サーバー。設定ファイル、扉のコントローラと鍵の台帳、HTTP API、systemd 用のファイルは `deploy/` |
| `digitalkey/mobile/` | 管理者と利用者のスマートフォンアプリ(Flet)。`flet build` で Android/iOS 向けに組めます |
| `digitalkey/locks.py` | 錠ドライバの登録簿。`dummy` は組み込み、それ以外は pip で入れたドライバのパッケージが登録します |
| `drivers/digitalkey-sesame/` | 錠ドライバの例。CANDY HOUSE 製 Sesame を BLE 直結または Web API で操作します。別パッケージとして PyPI に公開できます(機器ごとの詳しい説明はコード内) |
| `tests/` | テスト |

セットアップとテストの手順は [docs/使い方.md](docs/使い方.md) を見てください。

## ライセンス

文章は CC BY 4.0、このリポジトリのコードは AGPL-3.0-or-later です。詳しくは [LICENSE](LICENSE) を見てください。扉のコントローラ(`controller/`)は Apache-2.0 です(`controller/LICENSE`)。
