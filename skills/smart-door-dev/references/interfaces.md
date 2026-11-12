# 疑似の扉のインターフェースと疑似の機器

扉のコントローラの疑似の扉と試験の道具は、digitaldoor のリポジトリの `controller/`(パッケージ `digitaldoor_controller`)にあり、Python で読める大きさにしてあります。Apache-2.0 なので、コピーして製品に使うこともできます。錠のドライバの登録簿、Matter の橋、事業所サーバーは digitaldoor のリポジトリの `digitalkey/` にあり、こちらは AGPL-3.0-or-later です。ここでは、自社の実装が合わせるべきインターフェースの形をまとめます。正は仕様書で、ここは要約です。

## 目次
1. 接点(錠と扉と建物)
2. コントローラ(判断)
3. HTTP のインターフェース
4. 監査記録
5. 伝票(記録を外へ出す)
6. 錠のドライバ
7. Matter の錠
8. リーダーからの判定結果
9. 疑似の機器の一覧

## 1. 接点 — `digitaldoor_controller/contacts.py`

コントローラから見える錠と扉と建物は、この入出力の束だけです。錠の銘柄はこの下に隠れます(R-CT-03、04)。

| 向き | 名前 | 意味 |
|---|---|---|
| 入力 | `fire()` | 火災信号 |
| 入力 | `mains()` | 商用電源の有無 |
| 入力 | `door_open()` | 扉の開閉 |
| 入力 | `bolt_out()` | デッドボルトの位置 |
| 出力 | `set_lock(locked)` | 施錠と解錠 |

開発中は `SimContacts`、基板では GPIO やリレーの実装に差し替えます。手動リリースと改ざん検知は、事象としてコントローラに渡します。

## 2. コントローラ — `digitaldoor_controller/controller.py`

| 呼び出し | 役 |
|---|---|
| `on_auth(subject, route, valid=, reason=, duress=)` | リーダーか上の層からの判定結果。有効性は上の層が決める |
| `on_fire(active)`、`on_mains(present)`、`on_door(open)` | 建物と扉の事象 |
| `on_manual_release(means)`、`on_tamper(what)` | 非常手段と改ざん。記録だけ |
| `lock_request(by)`、`unlock_request(by)` | 上位(Matter、画面)からの命令 |
| `tick(now)` | 時計。自動施錠を進める |
| `set_time(now, synced)` | 時刻の同期。前と新しい時刻を残し、大きなずれは異常として残す |
| `configure(by, **changes)` | 設定の変更。項目ごとに前と新しい値を残す。無い項目と許されない値は全体を拒む |
| `state()` | 状態の照会 |

設定(`Config`)の項目: `two_person`、`two_person_window_s`、`autolock_s`(0 で自動施錠しない)、`power_policy`(release / hold)、`fire_policy`、`time_jump_s`。

## 3. HTTP のインターフェース — `digitaldoor_controller/server.py`

標準ライブラリだけの小さなインターフェースです。向きは三つに分かれます(出す、受ける、引かれる)。

| 道 | 中身 |
|---|---|
| `GET /state` | 状態 |
| `POST /lock`、`POST /unlock` | 上位からの命令 `{"by": "..."}` |
| `POST /auth` | リーダーからの判定結果 `{"subject","route","valid","reason","duress"}` |
| `POST /event` | `{"fire": true}`、`{"mains": false}`、`{"door_open": true}`、`{"manual": "機械キー"}`、`{"tamper": "..."}` |
| `POST /time` | `{"now": 1700000000}` |
| `GET /audit?since=N` | 連番 N より後の記録 |
| `GET /verify` | 連鎖の検査 |

記録を消すインターフェースはありません(R-CT-14)。時計と記録の書き出しは、別々のスレッドで回し、書き出しは判断の排他を持ちません(`start_workers`)。

## 4. 監査記録 — `digitaldoor_controller/audit.py`

一件の欄: `seq`(連番)、`at`(錠の時刻)、`synced`(同期の印)、`kind`(操作/物理/認証/資格情報/設定/非常/異常/時刻)、`event`、`result`、`subject`(仮名化した識別子)、`route`(経路)、`detail`、`prev_hash`、`hash`、`signature`。

- `hash` は、`hash` と `signature` を除いた欄を、名前の順に並べた JSON(UTF-8、ASCII 以外の文字はそのまま)の SHA-256 です。
- `signature` は `hash` の 16 進の文字列への署名です。署名のインターフェース(`Signer`)は差し替えられ、今はソフトの Ed25519(秘密鍵は置き場のファイル `audit.ed25519`)、後でセキュアエレメントに替えます。試験では HMAC も使います。
- `verify_chain` が、欠番、連鎖、内容、署名を順に検べます。

## 5. 伝票 — `digitaldoor_controller/slips.py`

記録を、受け手が公開鍵だけで検べられる伝票として、箱(フォルダ)へ出します(R-CT-17)。

| 項目 | 中身 |
|---|---|
| ファイル | 一件一つ。`<扉>-<連番10桁>.json`。別名で書いてから名前を変える |
| 欄 | `format`(`digitaldoor-audit-slip/1`)、`door`、`alg`(`Ed25519`)、`key_id`(公開鍵の SHA-256 の先頭 16 桁)、`entry` |
| 続きから | どこまで出したかは装置側の `export.cursor`。箱が無い間は進めない |
| 受け手 | 据付のときに台帳へ登録した公開鍵で検べる(`check_box`)。伝票の中の鍵の識別は照合にだけ使う |

命令行: `digitaldoor-controller pubkey`、`digitaldoor-controller export --box DIR`、`digitaldoor-controller serve --box DIR`、`digitaldoor-controller check-box --box DIR --door NAME --pubkey HEX`。

## 6. 錠のドライバ — `digitalkey/locks.py`

錠の指定は `<種類>:<引数>`(例 `sesame:<機器の UUID>`)。ドライバは別のパッケージにし、entry point で登録します。

```toml
[project.entry-points."digitalkey.locks"]
mylock = "mylock_driver.locks:make"
```

```python
# mylock_driver/locks.py
from dataclasses import dataclass

@dataclass
class MyLock:
    id: str
    device: str

    async def unlock(self) -> None: ...
    async def lock(self) -> None: ...

def make(lock_id: str, arg: str) -> MyLock:
    return MyLock(lock_id, arg)
```

ドライバのインターフェース(`LockDriver`)は `id` と、非同期の `unlock()`、`lock()` だけです。機器の詳しい説明はドライバのコードの docstring に書きます。テストのために、機器の側を模擬する疑似の機器(例 `drivers/digitalkey-sesame/digitalkey_sesame/fake.py`)を先に作ると、実機なしで通信の手順まで確かめられます。

## 7. Matter の錠 — `digitalkey/panel/`

扉を Matter の Door Lock(機器種別 0x000A、クラスタ 0x0101)として見せる橋です。見せるのは施解錠と扉の開閉だけで、命令は判断側(コントローラ)に渡り、拒まれたら状態を変えません(R-CT-18)。資格情報(暗証番号、カード)は扉と発行者の層が持つので、PIN の機能は出しません。

## 8. リーダーからの判定結果

コントローラが受けるのは「どの主体の資格情報が、どの経路で提示され、有効だったか」だけです(R-CT-19)。顔で解錠したときは、主体の仮名化した識別子、経路 = 顔、信頼度、生体検知の結果を記録に含め、顔の画像は入れません(R-RD-08)。リーダーは差し替えられる前提で、Aliro のリーダーでも、ドアホンでも、このインターフェースは変わりません。

## 9. 疑似の機器の一覧

| 疑似 | 置き場 | 使い道 |
|---|---|---|
| `SimContacts` | `digitaldoor_controller/contacts.py` | 錠と扉と建物の接点。火災、停電、開閉を属性で切り替える |
| `DummyLock` | `digitalkey/entrance/keyring.py` | 配線確認と試験用の錠。動作を記録するだけ |
| `FakeSesame` | `drivers/digitalkey-sesame/digitalkey_sesame/fake.py` | Sesame の機器側の手順。`--fake` で画面も実機なしで動く |
| 時計 | `Controller.set_time`、`tick` | 実時間を待たずに、自動施錠や時刻のずれを試す |
| 箱 | 一時ディレクトリ | 伝票の書き出し。消す、作らない、書けなくするで故障を注入する |
