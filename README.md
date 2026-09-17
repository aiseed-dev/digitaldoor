# デジタルドア — 情報サイト

電子錠、ドアホン、扉のコントローラ、鍵の発行、通信規格(Aliro、Matter)など、扉と鍵に関する技術情報を解説するサイトです。

公開先: https://door.aiseed.dev

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/使い方.md](docs/使い方.md) | セットアップ、ビルド、公開、記事の書き方、ニュースの仕組み、ソフトウェアの設計方針 |
| [docs/manual.md](docs/manual.md) | ユーザーマニュアル。`tools/build_manual.py` がコードから自動生成します(サイトの /manual/ にも掲載) |
| [docs/扉コントローラ仕様書.md](docs/扉コントローラ仕様書.md) | 扉側コントローラの仕様(要求 R-CT-01〜25) |
| [docs/要求仕様/README.md](docs/要求仕様/README.md) | ドア製造者向けの要求仕様の文書群 |

考え方や背景は、サイトの解説記事(/guide/)とブログ(/blog/)に書いています。

## ソフトウェア

同じリポジトリに、扉と鍵のソフトウェア(Python パッケージ `digitalkey`)を含めています。

| ディレクトリ | 内容 |
|---|---|
| `digitalkey/entrance/` | 鍵の発行と失効、本人確認、台帳、報告、バックアップ。民泊・貸家・WWOOF の受け入れ向け |
| `digitalkey/door/` | 扉側のコントローラ。火災・停電・避難時の判断、監査ログ、接点の抽象化、HTTP API。インターネットに接続しなくても動作します |
| `digitalkey/panel/` | 扉を Matter の電子錠として見せるブリッジと、操作画面(Flet) |
| `digitalkey/site/` | 事業所サーバー。設定ファイル、扉のコントローラと鍵の台帳、HTTP API、systemd 用のファイルは `deploy/` |
| `digitalkey/mobile/` | 管理者と利用者のスマートフォンアプリ(Flet)。`flet build` で Android/iOS 向けに組めます |
| `digitalkey/sesame/` | CANDY HOUSE 製 Sesame(SesameOS3)を公式アプリを使わずに BLE で直接操作するライブラリ、コマンドライン、画面。機器ごとの詳しい説明はコード内に書いています |
| `flet_ble/` | Android/iOS で BLE を使うための Flet 拡張 |
| `tests/` | テスト |

セットアップとテストの手順は [docs/setup.md](docs/setup.md) を見てください。

## ライセンス

文章は CC BY 4.0、コードは AGPL-3.0-or-later です。詳しくは [LICENSE](LICENSE) を見てください。
