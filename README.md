# デジタルドア — 情報サイト

電子錠、ドアホン、扉のコントローラ、鍵の発行、通信規格(Aliro、Matter)など、扉と鍵に関する技術情報を解説するサイトです。

公開先: https://door.aiseed.dev

## ドキュメント

| 文書 | 内容 |
|---|---|
| [docs/setup.md](docs/setup.md) | セットアップ、ビルド、開発サーバー、公開の手順、ディレクトリの構成 |
| [docs/writing.md](docs/writing.md) | 記事の書き方 |
| [docs/news.md](docs/news.md) | ニュースの収集と掲載の仕組み |
| [docs/扉コントローラ仕様書.md](docs/扉コントローラ仕様書.md) | 扉側コントローラの仕様(要求 R-CT-01〜25) |
| [docs/要求仕様/README.md](docs/要求仕様/README.md) | ドア製造者向けの要求仕様の文書群(共通コア、電動ラッチ、玄関と室内扉のプロファイル、開発用ミニサーバー) |
| [docs/ドア製品の階層構造.md](docs/ドア製品の階層構造.md) | 扉から利用者までを層に分けた一覧表 |
| [docs/Matter.md](docs/Matter.md)、[docs/Aliro.md](docs/Aliro.md) | 二つの規格についてのメモ(解説記事の元) |
| [docs/やりたいこと.md](docs/やりたいこと.md) | 仕様と参照実装を公開する方針 |
| [docs/トヨタのデジタルキー.md](docs/トヨタのデジタルキー.md) | アプリを直せない構造についてのメモ |
| [docs/SEKKEI.md](docs/SEKKEI.md) | 台帳・鍵・本人確認(entrance)の設計判断 |

## ソフトウェア

同じリポジトリに、扉と鍵のソフトウェア(Python パッケージ `digitalkey`)を含めています。

| ディレクトリ | 内容 |
|---|---|
| `digitalkey/entrance/` | 鍵の発行と失効、本人確認、台帳、報告、バックアップ。民泊・貸家・WWOOF の受け入れ向け |
| `digitalkey/door/` | 扉側のコントローラ。火災・停電・避難時の判断、監査ログ、接点の抽象化、HTTP API。インターネットに接続しなくても動作します |
| `digitalkey/panel/` | 扉を Matter の電子錠として見せるブリッジと、操作画面(Flet) |
| `digitalkey/sesame/` | CANDY HOUSE 製 Sesame(SesameOS3)を公式アプリを使わずに BLE で直接操作するライブラリ、コマンドライン、画面 |
| `flet_ble/` | Android/iOS で BLE を使うための Flet 拡張 |
| `tests/` | テスト(61 件) |

セットアップとテストの手順は [docs/setup.md](docs/setup.md) を見てください。

## ライセンス

文章は CC BY 4.0、コードは AGPL-3.0-or-later です。詳しくは [LICENSE](LICENSE) を見てください。
