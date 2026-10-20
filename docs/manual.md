# デジタルドア ユーザーマニュアル

このマニュアルは、ソフトウェアのコードから自動で生成しています。対象は、事業所のサーバーを運用する管理者、受付の担当者、鍵を使う利用者、保守の担当者です。

生成日: 2026-10-20

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

# 扉。lock は dummy(配線確認)か、入れた錠ドライバの種類。digitalkey-sesame を入れると sesameweb:<機器UUID>(Hub 3 経由)と sesame:<機器UUID>(BLE 直結)
[[door]]
id = "正面玄関"
lock = "dummy"
two_person = 1              # 2 にすると二人同時認証
autolock = 5.0              # 解錠から自動施錠までの秒

# アクセストークン。role は 管理者 | 受付 | 利用者 | 保守。利用者は subject(鍵の主体名)を書く
[[token]]
name = "管理者"
role = "管理者"
token = "8ftxnIF407TZuu8ri3Bce94UHfCtzVSW"

[[token]]
name = "受付"
role = "受付"
token = "06ZW9IfIFjmXhPRKpR6GnotKf1QaolTg"
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
  digitalkey door <命令> ...       扉のコントローラ(別パッケージ digitaldoor-controller。serve | verify | pubkey | export | check-box)
  digitalkey panel                 盤(Flet)。$DIGITALKEY_DOOR_URL の door に結び、$DIGITALKEY_PANEL_PORT(既定 8798)で開く
  digitalkey <ドライバ名> ...       pip で入れた錠ドライバのコマンド(例: digitalkey-sesame を入れると `digitalkey sesame scan`)
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
                           {forms,form,ddl,append,list,show,verify,reserve,key,report,backup,retention,serve} ...

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
usage: digitaldoor-controller [-h] [--dir DIR] [--name NAME] [--host HOST]
                              [--port PORT] [--two-person TWO_PERSON]
                              [--autolock AUTOLOCK]
                              [--power-policy {release,hold}]
                              [--admin-key HEX] [--trust FILE]
                              {serve,verify,pubkey,export,check-box,keygen,issue,trust-change} ...

扉側のコントローラ(中核)

positional arguments:
  {serve,verify,pubkey,export,check-box,keygen,issue,trust-change}
    serve               模擬の接点で core を開く
    verify              監査の連鎖を検査
    pubkey              署名の公開鍵を 16 進で表示する。据付のときに受け手の台帳へ写す
    export              まだ出していない記録を伝票にして箱へ出す(続きは置き場の export.cursor)
    check-box           受け手の側で、箱の伝票を公開鍵で検べる(置き場は使わない)
    keygen              発行者か管理者の Ed25519 の鍵の対を作る。秘密鍵は --out のファイル(権限
                        600)に書き、公開鍵と発行者の識別を JSON で出す
    issue               発行者の鍵で署名した資格情報(5.2a)を JSON で出す
    trust-change        管理者の鍵で署名した、信頼の一覧の変更の要求を JSON で出す

options:
  -h, --help            show this help message and exit
  --dir DIR             記録と鍵の置き場
  --name NAME           扉の名前。伝票のファイル名と door の欄に入る
  --host HOST
  --port PORT
  --two-person TWO_PERSON
  --autolock AUTOLOCK
  --power-policy {release,hold}
  --admin-key HEX       管理者の公開鍵(16 進)。据付のときに入れる。この鍵で署名した要求だけが信頼の一覧を変えられる
  --trust FILE          信頼する発行者の一覧(JSON。{発行者の識別: {"public_key": 16 進, "doors":
                        [扉の名前, ...]}})。管理者の変更が記録にあるときは、記録の一覧を使う

記録の署名は Ed25519 で、鍵は置き場の audit.ed25519 です(前からの置き場で audit.key がある物は HMAC
のまま)。伝票は一件ずつ <扉>-<連番>.json として箱へ出し、受け手は pubkey で写した公開鍵だけで check-box
で検べます。発行者と管理者の側では keygen で鍵を作り(秘密鍵のファイルは権限 600)、issue で資格情報を、trust-change
で信頼の一覧の変更の要求を、JSON で出します。この三つは置き場(--dir)を使わず、網も要りません。
```

### digitalkey sesame

```
usage: digitalkey sesame [-h] [-v] [--seconds SECONDS] [--tag TAG]
                         {scan,keys,register,status,lock,unlock,toggle,version} ...

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
- 自動施錠: 解錠から autolock 秒で、扉が閉じていれば施錠。0 なら自動施錠しない
- 施錠の要求: 火災中は受け付けない。停電中は解放の設定なら受け付けない
- 時刻: 同期のたびに前と新しい時刻を残す。同期済みから time_jump_s を超えて動いたら「異常/時刻のずれ」
- 設定: configure で変える。項目ごとに「設定/(項目名)」に前と新しい値を残す。無い項目と許されない値は全体を拒む
- 手動リリース・改ざん: 記録だけ
- 署名つき資格情報: present で、信頼する発行者の署名と範囲と期限を扉が確かめ、通れば上の判断(火災、二人同時、自動施錠)に渡す。
  一回限りの使用済みの一覧は、起動のときに記録(資格情報/使用)から作り直す(仕様書 5.2a)
- 信頼の一覧: change_trust で、据付のときに入れた管理者の鍵で署名した変更の要求だけを受ける。受けた変更は記録から読み戻す

### 鍵と資格情報を作る命令

発行者と管理者の側で使う三つの命令です。置き場を使わず、網も要りません。

#### digitaldoor-controller keygen

```
usage: digitaldoor-controller keygen [-h] --out FILE

秘密鍵(32 バイト)を権限 600 で --out に書く。ある名前には書かない。公開鍵の 16 進(信頼の一覧と --admin-key
に入れる)と、発行者の識別(公開鍵の SHA-256 の先頭 16 桁。資格情報の issuer)を JSON で出す。

options:
  -h, --help  show this help message and exit
  --out FILE  秘密鍵を書くファイル。無い名前にする
```

#### digitaldoor-controller issue

```
usage: digitaldoor-controller issue [-h] --key FILE --id ID --subject SUBJECT
                                    --doors NAME[,NAME...] --from TIME
                                    --until TIME [--once]

資格情報 {"body": ..., "sig": ...} を標準出力に出す。扉の POST /credential にそのまま送れる。

options:
  -h, --help            show this help message and exit
  --key FILE            発行者の秘密鍵のファイル(keygen の --out)
  --id ID               資格情報の識別。一回限りの使用済みの印に使う
  --subject SUBJECT     仮名化した主体
  --doors NAME[,NAME...]
                        開けてよい扉の名前(コンマで区切る)
  --from TIME           有効の始まり。unix 秒か ISO
                        8601(2026-10-21T09:00:00+09:00)。時差の無い日時はこの機械の地方時
  --until TIME          有効の終わり。--from と同じ書き方
  --once                一回限りの資格情報にする
```

#### digitaldoor-controller trust-change

```
usage: digitaldoor-controller trust-change [-h] --admin-key FILE --door DOOR
                                           --action {add,remove}
                                           --issuer-key HEX
                                           [--doors NAME[,NAME...]] [--id ID]

要求 {"body": ..., "sig": ...} を標準出力に出す。扉の POST /trust
に送る。扉は据付のときに入れた管理者の公開鍵で署名を検べる。

options:
  -h, --help            show this help message and exit
  --admin-key FILE      管理者の秘密鍵のファイル。(サブコマンドの前に置く --admin-key は公開鍵の 16 進で、別の物)
  --door DOOR           この要求が効く扉の名前
  --action {add,remove}
                        発行者を足すか、外すか
  --issuer-key HEX      対象の発行者の公開鍵(16 進)
  --doors NAME[,NAME...]
                        その発行者に許す扉の名前(add のとき要る。コンマで区切る)
  --id ID               要求の識別。扉は同じ識別を二度受けない。省くと乱数で作る
```

### 錠のドライバで動く接点

```
錠のドライバで動く接点 — 扉のコントローラの接点の口を、digitalkey.locks の錠のドライバにつなぐ。

扉のコントローラ(digitaldoor_controller)が見る錠は、`fire()`、`mains()`、`door_open()`、`bolt_out()`、`set_lock(locked)` の
束だけ(扉コントローラ仕様書 R-CT-03)。この接点は `set_lock` を、非同期の錠のドライバ(`unlock()` / `lock()`。
`digitalkey.entrance.keyring.LockDriver`)に渡す。Sesame でも `dummy` でも、錠の銘柄はこの下に隠れ、判断の側は変わらない(R-CT-04)。

- 火災・電源・扉の開閉は、SimContacts と同じく属性(`_fire`、`_mains`、`_door_open`)で持つ。HTTP の `/event` が書き換え、
  基板の入力に替えるときはここだけを差し替える。
- 非同期のドライバは、この接点が持つ専用のイベントループ(別のスレッド)で動かす。扉の側の呼びは同期で、
  HTTP の口の複数のスレッドから呼ばれてよい。同じループを使うほかの非同期の処理(鍵の台帳の `Keyring.open` など)は `run()` で渡す。

錠に届かないとき(ドライバが例外を出す、`timeout_s` の内に終わらない):
- `set_lock` は例外を出さず、錠の位置の記録(`bolt_out()`)を動かさない。位置は「届いて確かめた最後の側」で、起動のときは施錠側。
  届かなかった命令で位置を動いたことにしない。
- `failures` を一つ増やし、`last_error` に短い理由を残す。`outputs` には届いた命令だけを足す(`lock` か `unlock`)。
- 時間切れのときはドライバの処理を取り消す。取り消しの前に錠へ届いていた命令は取り消せないので、位置は確かめるまで分からない。
- 扉の判断は止めない。コントローラは命令を出した側として記録を続け、錠が動いたかは `bolt_out()` と `/state` の `bolt_out` が示す。
  命令を出した時点の `failures` と比べれば、呼び手(実演の道具など)は届かなかったことを知れる。
```

## 実演の道具

```
実演の道具 — デジタル錠の開け閉めは簡単で、鍵の管理は難しいことを、場面ごとに見せる。

    python tools/jitsuen.py --fake kantan                          # 疑似の Sesame で見せる
    python tools/jitsuen.py --lock sesame:<機器の UUID> check      # 実機の Sesame に届くか確かめる
    python tools/jitsuen.py --fake --yes all                       # Enter を待たずに全部

場面(手順は docs/使い方.md の「実演」):
  check    錠に届くことを確かめる(施錠の命令を一つ送る)
  kantan   開け閉めは部品の仕事。錠のドライバで直接、次に疑似の扉の口(POST /unlock)から開け閉めする
  sono-hi  その日の鍵。扉は網が無くても、発行者の署名と期間を自分で確かめる
  kigen    止めたつもり。網の届かない扉では、台帳で失効しても、期限が来るまで開く
  keiro    経路が散る。同じ人の鍵を一つの経路で失効しても、別の経路で開く
  more     発行者の鍵が漏れたら。信頼の一覧を変えられるのは管理者の鍵だけ
  kiroku   記録で確かめる。記録の連鎖を検べ、一字の書き換えを見つける
  all      check を除く全部を順に(kantan から kiroku まで)

しくみ: 疑似の扉(digitaldoor_controller の Controller と HTTP の口)を同じプロセスの中で開き、接点に
digitalkey.contacts.LockDriverContacts を差す。錠は --fake の疑似の Sesame か、--lock の錠のドライバ
(digitalkey.locks の登録簿。`sesame:<UUID>` は BLE の実機)。一つの場面は一つの手順ずつ進み、Enter で次へ行く
(--yes なら待たない)。場面ごとに一時の置き場(扉の記録、鍵、台帳)を作り直し、終わったら消すので、どの場面からでも
何度でも始められる。時計は扉の時計を POST /time で進める。実時間は待たない。

結果は短い日本語で出す(「開きました」「拒みました: 期限の外」)。拒む理由の言葉は扉コントローラ仕様書 5.2a の言葉そのまま。
錠に届かないときは、例外を見せずに「Sesame に届きません。電源と Bluetooth を確かめてください」と出して止まる。
実機の Sesame の鍵(sesame-keys.json)は読まない。ここで作る鍵は一時の置き場の中だけで、終われば消える。
```

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

## 錠のドライバ

site.toml の `lock` に書ける種類と提供元です。種類は pip で入れたドライバのパッケージで増えます。

| 種類 | 提供元 |
|---|---|
| dummy | digitalkey |
| sesame | digitalkey-sesame |
| sesameweb | digitalkey-sesame |

### digitalkey-sesame

CANDY HOUSE Sesame OS3 (Sesame 5 / 6 / 6 Pro) BLE protocol in pure Python. 公式アプリとクラウドを使わず、BLE で直接 登録→ログイン→施錠/解錠。

Reference: https://github.com/CANDY-HOUSE/SesameSDK_Android_with_DemoApp
           https://github.com/CANDY-HOUSE/API_document

```
digitalkey の錠ドライバ(entry point `digitalkey.locks`)。

sesame:<機器UUID>      BLE 直結。事前に `digitalkey sesame register` で鍵を登録しておく
sesameweb:<機器UUID>   CANDY HOUSE の Web API(Hub 3 経由)。API key と機器の secret key は
                       $SESAME_KEYS が指す JSON({"api_key": "...", "<uuid>": "<secret hex>"})か、
                       環境変数 SESAME_API_KEY / SESAME_SECRET_<UUIDの16進を大文字で> で渡す
```
