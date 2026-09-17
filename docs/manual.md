# デジタルドア ユーザーマニュアル

このマニュアルは、ソフトウェアのコードから自動で生成しています。対象は、事業所のサーバーを運用する管理者、受付の担当者、鍵を使う利用者、保守の担当者です。

生成日: 2026-09-17

## 事業所サーバー

事業所サーバー — 一つの事業所(建物)の扉と鍵を運用する常駐サービス。

構成ファイル(site.toml)を読み、扉ごとのコントローラ(door)と鍵の台帳(entrance)を一つのプロセスで動かし、
管理者・受付・利用者向けの HTTP API を出す。スマートフォンアプリ(digitalkey.mobile)と業務システムはこの API を使う。
インターネットに接続していなくても動く。記録は事業所のサーバーに置く。

### 導入

`deploy/install.sh` を root で実行すると、専用ユーザーの作成、仮想環境の作成、設定の雛形の生成、systemd への登録まで行います。設定は `/var/lib/digitaldoor/site.toml` です。

```
sh deploy/install.sh
systemctl status digitaldoor-site
```

### 設定ファイル(site.toml)

`digitalkey site init <ディレクトリ>` で次の雛形が作られます。トークンは自動生成されます。

```toml
# digitaldoor 事業所サーバーの設定

[site]
name = "本社"
vault = "/var/lib/digitaldoor/vault"            # 台帳・監査記録・鍵の置き場(このサーバーの中)

[api]
host = "127.0.0.1"          # 構内だけに出す。外へ出すときは前段に TLS を置く
port = 8800

# 扉。lock は dummy(配線確認) | sesameweb:<機器UUID>(Hub 3 経由) | sesame:<機器UUID>(BLE 直結)
[[door]]
id = "正面玄関"
lock = "dummy"
two_person = 1              # 2 にすると二人同時認証
autolock = 5.0              # 解錠から自動施錠までの秒

# アクセストークン。role は 管理者 | 受付 | 利用者 | 保守。利用者は subject(鍵の主体名)を書く
[[token]]
name = "管理者"
role = "管理者"
token = "BkfO270H4SUvlVOhx-jIBJ9xpHjaeHIV"

[[token]]
name = "受付"
role = "受付"
token = "ky6fY32NbiP3oUTqf2Xm0JOVEQHms50V"
```

### 役割

| 役割 | できること |
|---|---|
| 管理者 | サーバーの設定、鍵の発行と失効、扉の施錠、記録の閲覧と検査。 |
| 受付 | 鍵の発行、扉の施錠、操作の記録の閲覧。失効はできません。 |
| 利用者 | 自分の鍵の確認と、自分の鍵での解錠。 |
| 保守 | 扉の状態と監査記録の閲覧、記録の検査、施錠。 |

### HTTP API

アプリと業務システムはこの API を使います。認証は `Authorization: Bearer <トークン>` です。

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/health` | 誰でも |
| GET | `/state` | 管理者・受付・保守 |
| GET | `/doors` | 全員(扉の一覧) |
| POST | `/doors/{door_id}/unlock` |  |
| POST | `/doors/{door_id}/lock` |  |
| GET | `/keys` | 管理者・受付は全部、利用者は自分の分 |
| POST | `/keys` | {subject,door,start,end,route} 管理者・受付 |
| DELETE | `/keys/{key_id}` |  |
| GET | `/events/{door_id}` |  |
| GET | `/operations` | 管理者・受付(鍵の操作の記録) |
| GET | `/verify` | 管理者・保守(記録の連鎖の検査) |

## スマートフォンアプリ

スマートフォンアプリ(Flet)— 管理者と利用者が事業所サーバーを使うための画面。

利用者: 自分の鍵を見る、扉を開ける。
管理者・受付: 鍵を発行する、失効させる、扉を施錠する、操作の記録を見る。
接続先(サーバーの URL とトークン)は端末に保存する。通信は事業所サーバーの HTTP API だけ。

### 画面

```
画面:
  接続      サーバーの URL とトークンを入れて保存する。端末の保存領域に残る
  扉        扉の一覧。自分の鍵がある扉は「開ける」ボタンが出る。管理者・受付は「施錠」も
  鍵        利用者: 自分の鍵の一覧。管理者・受付: 全員の鍵と、発行(名前・扉・開始・終了)と失効
  記録      管理者・受付: 鍵の操作の記録(新しい順)

起動: digitalkey mobile [--web]。Android/iOS は `flet build` で組む(pyproject の [tool.flet])。
```

## コマンド一覧

サーバーやパソコンで使うコマンドです。`digitalkey` の後に対象を付けます。

```
digitalkey entrance <命令> ...   様式・台帳・鍵・本人確認・報告・控え(--help で一覧)
  digitalkey door <命令> ...       扉のコントローラ(serve | verify)
  digitalkey panel                 盤(Flet)。$DIGITALKEY_DOOR_URL の door に結び、$DIGITALKEY_PANEL_PORT(既定 8798)で開く
  digitalkey sesame <命令> ...     CANDY HOUSE Sesame を BLE で直接(scan | register | status | lock | unlock | toggle | version | keys)
  digitalkey sesame app [--web] [--fake]   その画面(Flet)。--fake は疑似の Sesame
  digitalkey site init <dir> | serve --config site.toml   事業所サーバー(扉のコントローラ+鍵の台帳+HTTP API)
  digitalkey mobile [--web]        管理者と利用者のスマートフォンアプリ(Flet)
```

### digitalkey site

```
usage: digitalkey site [-h] {init,serve} ...

事業所サーバー

positional arguments:
  {init,serve}
    init        設定の雛形(site.toml)を書く
    serve       サーバーを起動する

options:
  -h, --help    show this help message and exit
```

### digitalkey entrance

```
usage: digitalkey entrance [-h] [--vault VAULT] [--issuer ISSUER]
                           {forms,form,ddl,append,list,show,verify,reserve,key,report,backup,retention,serve}
                           ...

entrance の命令行。経営者と AI社員 はここから台帳を扱う(画面は作らない)。

positional arguments:
  {forms,form,ddl,append,list,show,verify,reserve,key,report,backup,retention,serve}
    forms               様式の一覧
    form                記入用テキストを出す
    ddl                 様式から CREATE TABLE を出す
    append              記入済みテキストを伝票にする
    list                伝票の一覧
    show                伝票を表示
    verify              連鎖を検査
    reserve             予約を起こし、チェックインの符号を出す
    key                 鍵の発行・失効・一覧・解錠
    report              wwoof YYYY-MM | minpaku START END
    backup              金庫を固めて暗号化し、控えへ
    retention           保存年限を過ぎた伝票の候補
    serve               門(Web)を開く

options:
  -h, --help            show this help message and exit
  --vault VAULT         金庫(既定: $DIGITALKEY_VAULT か ./vault)
  --issuer ISSUER       伝票の発行者
```

### digitalkey door

```
usage: digitalkey door [-h] [--dir DIR] [--host HOST] [--port PORT]
                       [--two-person TWO_PERSON] [--autolock AUTOLOCK]
                       [--power-policy {release,hold}]
                       {serve,verify} ...

扉側のコントローラ(中核)

positional arguments:
  {serve,verify}
    serve               模擬の接点で core を開く
    verify              監査の連鎖を検査

options:
  -h, --help            show this help message and exit
  --dir DIR             記録と鍵の置き場
  --host HOST
  --port PORT
  --two-person TWO_PERSON
  --autolock AUTOLOCK
  --power-policy {release,hold}
```

### digitalkey sesame

```
usage: digitalkey sesame [-h] [-v] [--seconds SECONDS] [--tag TAG]
                         {scan,keys,register,status,lock,unlock,toggle,version}
                         ...

Command-line client (no GUI) for CANDY HOUSE Sesame 5/6/6 Pro over BLE via bleak.

  digitalkey sesame scan [--seconds 5]
  digitalkey sesame register <uuid-or-address> [--name 玄関]
  digitalkey sesame status|lock|unlock|toggle|version <uuid-or-address>
  digitalkey sesame keys
Keys are stored in ~/.config/digitalkey/sesame-keys.json (or $SESAME_KEYSTORE).

positional arguments:
  {scan,keys,register,status,lock,unlock,toggle,version}

options:
  -h, --help            show this help message and exit
  -v, --verbose
  --seconds SECONDS     scan duration
  --tag TAG             history tag written into the lock's log
```

## 扉のコントローラの動作

火災・停電・避難・二人同時・強要を、接点と記録に落とす。

判断の表(既定):
- 火災信号: 電動部を解放する(避難のため)。閉鎖は扉のクローザーの仕事。記録「非常/火災信号」
- 停電: 既定は解放(fail-safe)。設定で保持(fail-secure)にできる。復電で元に戻す
- 認証済み: 有効な資格情報が来たら解錠。二人同時が要る扉では窓の秒数内に N 人揃って解錠
- 強要: 強要の印が付いた資格情報は解錠し、記録に「強要」を残す(外への通報は上の層)
- 自動施錠: 解錠から autolock 秒で、扉が閉じていれば施錠
- 手動リリース・改ざん: 記録だけ

## 台帳の様式

台帳に記録される伝票の種類です。項目名は様式ファイル(digitalkey/entrance/forms_data/*.adoc)がそのまま定義です。

### 予約(予約)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 主体 | varchar(100), not null — 泊まる人・借りる人の氏名 |
| 層 | varchar(10), in ('WWOOF', '民泊', '貸家', '会議室'), not null |
| 到着 | datetime, not null |
| 出発 | datetime, not null |
| 錠 | varchar(40), not null — 対象の錠の識別子 |
| 連絡先 | varchar(100) |
| 受付符号 | varchar(64), not null |

### 会議室・場所貸し(利用_会議室)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 利用者 | varchar(100), not null |
| 連絡先 | varchar(100) |
| 利用日 | date, not null |
| 開始時刻 | time, not null |
| 終了時刻 | time, not null |
| 用途 | varchar(100) |
| 人数 | integer, check (人数 between 1 and 50) |
| 料金 | integer, check (料金 >= 0) — 場所の時間貸しの料金 |

### 宿泊者名簿(住宅宿泊事業)(宿泊_民泊)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 氏名 | varchar(100), not null |
| 住所 | varchar(200), not null |
| 職業 | varchar(100), not null |
| 国籍 | varchar(50), not null |
| 旅券番号 | varchar(30) — 国内に住所を持たない外国人は必須 |
| 到着日 | date, not null |
| 出発日 | date, not null |
| 予約番号 | varchar(40) |
| 本人確認番号 | varchar(40) — 本人確認伝票の番号 |

### 本人確認(本人確認)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 主体 | varchar(100), not null |
| 予約番号 | varchar(40) |
| 自撮り | varchar(64) — 画像のsha256 |
| 身分証 | varchar(64) — 画像のsha256 |
| ドアホン | varchar(64) — 画像のsha256 |
| 位置 | varchar(50) — 緯度,経度 |
| 距離m | integer — 届出住宅からの距離 |
| 時刻差s | integer — ドアホンの映像と送信の時刻差 |
| 一致度 | numeric — 顔の一致の度合い(0〜1) |
| 判定 | varchar(10), in ('承認', '保留', '拒否'), not null |
| 判定者 | varchar(50), not null |
| 理由 | varchar(200) |

### ウーファー滞在(滞在_WWOOF)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 氏名 | varchar(100), not null |
| 国籍 | varchar(50), not null |
| 在留資格 | varchar(50) — 短期滞在・ワーキングホリデー・特定活動など。日本人なら「日本」 |
| WWOOF会員番号 | varchar(30) |
| 到着日 | date, not null |
| 出発日 | date — 未定なら空欄。出発したら出発の伝票を別に起こす |
| 傷害保険 | varchar(10), in ('あり', 'なし', '未確認'), not null — ウーファー本人が用意する |
| 手伝いの内容 | text |
| 手伝い時間 | integer, check (手伝い時間 between 0 and 12) — 一日あたりの時間の目安 |
| 連絡先 | varchar(100) |
| 備考 | text |

### 定期建物賃貸借(貸家)(賃貸_定期借家)

書ける役割: 経営者

| 項目 | 型と制約 |
|---|---|
| 借主氏名 | varchar(100), not null |
| 借主住所 | varchar(200), not null |
| 連絡先 | varchar(100) |
| 開始日 | date, not null |
| 終了日 | date, not null |
| 月額賃料 | integer, not null, check (月額賃料 >= 0) |
| 敷金 | integer, check (敷金 >= 0) |
| 前家賃受領日 | date |
| 事前説明日 | date, not null — 定期借家であることの、書面による事前説明の日 |
| 中途解約 | varchar(200), not null — 例「不可(借地借家法第38条第7項による解約を除く)」 |
| 用途 | varchar(10), in ('居住', '事業'), not null |

### 鍵の失効(鍵_失効)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 鍵番号 | varchar(40), not null |
| 理由 | varchar(200), not null |

### 錠の操作(鍵_操作)

書ける役割: 経営者, AI社員, 錠

| 項目 | 型と制約 |
|---|---|
| 鍵番号 | varchar(40) |
| 錠 | varchar(40), not null |
| 操作 | varchar(10), in ('解錠', '施錠'), not null |
| 結果 | varchar(10), in ('成功', '拒否', '失敗'), not null |
| 理由 | varchar(200) |
| 主体 | varchar(100) |

### 鍵の発行(鍵_発行)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 主体 | varchar(100), not null |
| 錠 | varchar(40), not null |
| 経路 | varchar(10), in ('遠隔解錠', '暗証番号', '財布', '機械鍵'), not null |
| 開始 | datetime, not null |
| 終了 | datetime, not null |
| 予約番号 | varchar(40) |
| 根拠 | varchar(200) — 本人確認伝票などの番号 |

### 駆けつけ(駆けつけ)

書ける役割: 経営者, AI社員

| 項目 | 型と制約 |
|---|---|
| 事象 | varchar(200), not null |
| 受付時刻 | datetime, not null |
| 到着時刻 | datetime |
| 対応者 | varchar(100), not null |
| 結果 | text |

## Sesame(CANDY HOUSE)の直接操作

CANDY HOUSE Sesame OS3 (Sesame 5 / 6 / 6 Pro) BLE protocol in pure Python. 公式アプリとクラウドを使わず、BLE で直接 登録→ログイン→施錠/解錠。

Reference: https://github.com/CANDY-HOUSE/SesameSDK_Android_with_DemoApp
           https://github.com/CANDY-HOUSE/API_document

詳しい手順とプロトコルの要点は `digitalkey/sesame/` の各ファイルの先頭に書いてあります。
