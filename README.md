# デジタルドア — 情報サイト

電子錠、ドアホン、扉のコントローラ、鍵の発行、通信規格(Aliro、Matter)など、扉と鍵に関する技術情報を解説するサイトです。

公開先: https://door.aiseed.dev

## 各社で、AI を使ってソフトウェアを作る

このリポジトリは公開です。扉メーカー、錠メーカー、ドアホンやカメラのメーカー、設備工事会社、管理会社が、ここにある仕様と参照実装を土台にして、自分の AI と一緒に自社のソフトウェアを作れるようにしています。進め方は [SKILL.md](SKILL.md) にあります。ふだん使っている AI に読ませ、自社の範囲を伝えると、仕様、テスト、実装の順に一緒に作ります。

## 自社のシステムを AI で確かめる

2026年10月から、守る側の会社は、申し込めば AI で自社のシステムの穴を確かめられるようになりました。鍵の管理の仕組みも、攻撃に耐えるかを試してから使う物です。どこから確かめるか、AI に何を読ませるか、業者にどう知らせるかを、解説 20「AI で自社のシステムを確かめる」(サイトの /guide/check-your-systems-with-ai/、元は [articles/guide.adoc](articles/guide.adoc))に書いています。申し込みから最初の一つまでの始め方は [docs/確かめの始め方.md](docs/確かめの始め方.md)(サイトの /check/)にあります。

ふだん使っている AI に読ませるガイドと、道具は次のとおりです。

| 置き場 | 内容 |
|---|---|
| [skills/system-check/](skills/system-check/SKILL.md) | 自社のシステムと納められたコードを読んで穴を見つけ、記録し、直してもう一度確かめるガイド |
| [skills/incident-response/](skills/incident-response/SKILL.md) | 事故(不正なアクセス、ランサムウェア、鍵やパスワードの漏れ、端末の紛失)が起きた時と、その備えのガイド。連絡先の表、記録の様式、通知の文の雛形つき |
| [skills/detection/](skills/detection/SKILL.md) | 自社のコンピューターとアカウントの監視を作るガイド。何を監視し、何を異常と見て、誰に知らせるか |
| [skills/kagi/](skills/kagi/SKILL.md) | 道具 kagi の使い方のガイド |
| [kagi/](kagi/) | 道具 kagi(別パッケージ digitaldoor-kagi、AGPL-3.0-or-later)。サーバーと業務システムの秘密の在庫表、ポートの表、追記だけの記録、Linux のサーバーの今の形を表と突き合わせます |

## 使う人と製造者をつなぐ

開発の間も、扉を使う人と扉を作る製造者の間に立ちます。使う人は困りごとを自分の言葉で Issue に書き、運営と AI がそれを要求の番号に整え、製造者は満たす番号を申告し、満たせない物と代わりの案を同じ番号の下に返します。確かめるのはテストと記録です。流れは [docs/つなぐ.md](docs/つなぐ.md) に、入り口は [Issues](https://github.com/aiseed-dev/digitaldoor/issues/new/choose) にあります。

## 扉の製造者向け

扉の製造者に「何を満たすか」を伝える要求仕様と、扉側のコントローラの仕様です。仕様を確かめる疑似の扉と試験の道具は、[controller/](controller/)(パッケージ digitaldoor-controller、Apache-2.0)にあります。認証制度ではなく実用の仕様で、料金と会費はありません。満たしているかどうかは、公開されたテストを自分で実行すれば分かります。

| 文書 | 内容 |
|---|---|
| [docs/要求仕様/README.md](docs/要求仕様/README.md) | 要求仕様の文書群。共通コア、電動ラッチ、認定の境界と標準インターフェース、リーダー（ドアホンと顔認識）、用途別のプロファイル（個別住宅、集合住宅、事業所、室内、防火区画）、開発用ミニサーバー |
| [docs/扉コントローラ仕様書.md](docs/扉コントローラ仕様書.md) | 扉側コントローラの仕様（要求 R-CT-01〜25）。疑似の扉と試験の道具は `controller/`、Matter の橋は `digitalkey/panel/` |
| [docs/適合の現在.md](docs/適合の現在.md) | 要求の番号とテストの対応。`python tools/taiou.py` が作ります。番号の無い要求は未検証です |
| [docs/権限の申請の雛形.md](docs/権限の申請の雛形.md) | 社員がどこからでも権限を申請し、情報システム部門が確かめて出し、止め、記録する流れの雛形(下書き)。各社はこれを参考に独自に開発します。部品は aiai-tools の物を使います |

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
| `digitalkey/door_serve.py` | `digitalkey door-serve`。錠のドライバ(実機の Sesame など)で動く扉の HTTP のインターフェース。パネルはこのインターフェースを叩いて本物の錠を動かします |
| `digitalkey/site/` | 事業所サーバー。設定ファイル、扉のコントローラと鍵の台帳、HTTP API、systemd 用のファイルは `deploy/` |
| `digitalkey/mobile/` | 管理者と利用者のスマートフォンアプリ(Flet)。`flet build` で Android/iOS 向けに組めます |
| `digitalkey/locks.py` | 錠ドライバの登録簿。`dummy` は組み込み、それ以外は pip で入れたドライバのパッケージが登録します |
| `drivers/digitalkey-sesame/` | 錠ドライバの例。CANDY HOUSE 製 Sesame を BLE 直結または Web API で操作します。別パッケージとして PyPI に公開できます(機器ごとの詳しい説明はコード内) |
| `kagi/` | 扉の鍵ではなく、サーバーと業務システムの秘密の在庫・ポート・記録・サーバーの形を表と突き合わせる道具(別パッケージ digitaldoor-kagi、AGPL-3.0-or-later)。使い方は `skills/kagi/` |
| `tests/` | テスト |

セットアップとテストの手順は [docs/使い方.md](docs/使い方.md) を見てください。

## ライセンス

文章は CC BY 4.0、このリポジトリのコードは AGPL-3.0-or-later です。詳しくは [LICENSE](LICENSE) を見てください。扉のコントローラ(`controller/`)は Apache-2.0 です(`controller/LICENSE`)。
